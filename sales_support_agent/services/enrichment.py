"""Orquestração da fase de Enriquecimento (KipFlow).

Duas responsabilidades:

1. **Materialização** — a fase de pesquisa grava as empresas apenas dentro de
   `SearchRun.result_json` (um blob de texto). `ensure_companies_materialized`
   transforma esse JSON em linhas de `ProspectCompany`, de forma idempotente.
   É chamada no on_load da página, o que cobre tanto as pesquisas antigas
   quanto as novas sem precisar mexer em `SearchState.start_search`.

2. **Enriquecimento** — `stream_enrichment` é um async generator no mesmo
   contrato de eventos do `services/prospect_agent.py`, para a UI acompanhar
   o progresso:
       ("progress", processadas, total, mensagem)
       ("done", resumo)
       ("error", mensagem_pt_BR)

ECONOMIA DE CRÉDITO (regra de negócio central desta fase):
- empresa com `enrichment_status == "completed"` é pulada SEM nenhuma requisição;
- empresa `partial`/`failed` reprocessa apenas o que falta (dados cadastrais e
  contatos são decididos separadamente) — não se paga duas vezes pelo mesmo dado;
- o `company_public_id` do LinkedIn sai do `linkedin_url` que já vem no dataset
  `online_presence`, evitando a chamada paga de resolução;
- o endpoint de telefones só é chamado quando os datasets não trouxeram nenhum;
- a busca de e-mail (etapa 3, Hunter.io) marca `CompanyContact.email_buscado_em`
  na TENTATIVA, não no sucesso, e respeita uma cota de créditos por ciclo
  (`services/hunter_client.creditos_restantes`) verificada antes de cada
  chamada — é a única etapa cujo orçamento é mensal, não por rodada.
"""

import json
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import reflex as rx

from prospect_agent.models import (
    CompanyContact,
    KipflowUsage,
    ProspectCompany,
    SearchRun,
    brt_now,
)
from prospect_agent.services import kipflow_client as kip
from prospect_agent.services import receita_client as receita
from prospect_agent.services.enrichment_rules import (
    AREAS_ALVO,
    DATASETS_EMPRESA,
    NIVEIS_SENIORIDADE,
    calcular_idade_empresa,
    calcular_percentual,
    definir_status,
    detectar_alerta_situacao,
    escolher_telefone,
    escolher_website,
    extrair_linkedin_public_id,
    formatar_telefone,
    mapear_porte,
    mapear_status,
    normalizar_uf,
    ordenar_socios,
    sigla_uf_de_localizacao,
    socio_para_contato,
)
from prospect_agent.services.normalizers import (
    normalizar_cnpj,
    normalizar_dominio,
    normalizar_nome,
)

# Status que ainda são elegíveis a (re)processamento. "completed" fica de fora
# de propósito — é a trava que impede pagar de novo pelo mesmo dado.
# "in_progress" entra porque uma execução interrompida não pode deixar a
# empresa presa para sempre.
STATUS_ELEGIVEIS = ("pending", "in_progress", "partial", "failed")

# Similaridade mínima aceita no fallback de busca por nome. Abaixo disso o
# risco de casar com a empresa errada é maior que o ganho.
SIMILARIDADE_MINIMA = 0.75

# Fallback pago (R$ 0,49) para resolver o company_public_id quando não há
# linkedin_url. Desligado por padrão: custa mais do que o contato vale na
# maioria dos casos. Mude aqui se preferir cobertura a custo.
USAR_FALLBACK_LINKEDIN_PAGO = False

# TRAVA DE CUSTO: exigir que a fonte gratuita da Receita esteja no ar.
# Sem isto, uma queda da BrasilAPI/Minha Receita faria TODAS as empresas caírem
# no caminho pago da KipFlow (personas a R$ 0,49/pessoa + telefones): numa
# rodada de 34 empresas, ~R$ 97 em vez de ~R$ 5. É preferível falhar e avisar a
# gastar 20x sem o usuário saber. Coloque False para permitir o modo caro.
EXIGIR_RECEITA_DISPONIVEL = True

# Quantas indisponibilidades seguidas da Receita abortam a execução. Não é 1
# para tolerar um soluço isolado de um serviço comunitário sem SLA.
MAX_FALHAS_SEGUIDAS_RECEITA = 3

_MSG_RECEITA_FORA = (
    "A consulta gratuita da Receita (BrasilAPI/Minha Receita) está indisponível. "
    "O enriquecimento foi interrompido de propósito: sem ela, cada empresa seria "
    "consultada na KipFlow por um custo muito maior. Tente novamente mais tarde."
)

# ESTRATÉGIA HÍBRIDA (é aqui que se controla o custo dos contatos):
# o QSA da Receita é gratuito e traz o decisor de fato (sócio/diretor), então
# a busca paga no LinkedIn (R$ 0,49 por pessoa) só acontece quando o QSA não
# devolveu ninguém — empresa sem sócio pessoa física no quadro, tipicamente
# subsidiária de holding. Coloque False para nunca pagar por contatos.
USAR_LINKEDIN_SE_QSA_VAZIO = True


# ---------------------------------------------------------------------------
# Materialização (backfill)
# ---------------------------------------------------------------------------

def ensure_companies_materialized(tenant_id: int, search_run_id: int) -> int:
    """Cria as linhas de ProspectCompany a partir do result_json da pesquisa.

    Idempotente por `search_run_id`: se já existe qualquer linha para esta
    pesquisa, não faz nada e devolve a contagem existente — isso também
    garante que a busca de duplicata entre pesquisas (abaixo) só roda UMA vez
    por pesquisa, não a cada carregamento de página.

    DEDUPLICAÇÃO ENTRE PESQUISAS (tenant-wide, não só dentro desta pesquisa):
    antes de criar uma linha nova, procura uma empresa já conhecida do tenant
    (de QUALQUER pesquisa anterior) por CNPJ -> domínio -> nome normalizado —
    mesma prioridade de chave usada em `prospect_agent._dedupe_empresas`. Se
    achar, ATUALIZA a existente (campos de origem + passa a apontar para esta
    pesquisa, a mais recente que a encontrou) em vez de duplicar; dados já
    enriquecidos/priorizados são preservados. Sem isso, encontrar a mesma
    empresa numa 2ª pesquisa criava uma segunda linha inteiramente
    desconectada da primeira (contagens do dashboard e Top 10 duplicavam).
    """
    with rx.session() as session:
        existentes = (
            session.query(ProspectCompany)
            .filter(
                ProspectCompany.tenant_id == tenant_id,
                ProspectCompany.search_run_id == search_run_id,
            )
            .count()
        )
        if existentes:
            return existentes

        run = session.get(SearchRun, search_run_id)
        if not run or run.tenant_id != tenant_id or not run.result_json:
            return 0

        try:
            dados = json.loads(run.result_json)
        except (ValueError, TypeError):
            return 0

        empresas = dados.get("empresas") or []

        # Índice de todas as empresas já conhecidas do tenant, de qualquer
        # pesquisa — construído uma vez e atualizado em memória a cada item
        # processado, para também não duplicar dentro do próprio lote.
        conhecidas = (
            session.query(ProspectCompany)
            .filter(ProspectCompany.tenant_id == tenant_id)
            .all()
        )
        por_cnpj = {c.cnpj: c for c in conhecidas if c.cnpj}
        por_dominio = {c.dominio: c for c in conhecidas if c.dominio}
        por_nome = {normalizar_nome(c.nome): c for c in conhecidas}

        criadas = 0
        for e in empresas:
            if not isinstance(e, dict) or not e.get("nome"):
                continue
            nome = str(e.get("nome")).strip()
            website = e.get("website")
            cnpj = normalizar_cnpj(e.get("cnpj"))
            dominio = normalizar_dominio(website)
            nome_norm = normalizar_nome(nome)

            existente = (
                (cnpj and por_cnpj.get(cnpj))
                or (dominio and por_dominio.get(dominio))
                or por_nome.get(nome_norm)
            )
            if existente:
                # A empresa passa a pertencer a quem a reencontrou: o "coletor"
                # exibido no filtro de /leads acompanha a pesquisa mais recente
                # que a trouxe, igual a `search_run_id` logo acima.
                existente.search_run_id = search_run_id
                existente.user_email = run.user_email or existente.user_email
                existente.website = website or existente.website
                existente.localizacao = str(e.get("localizacao") or existente.localizacao)
                existente.segmento_identificado = str(
                    e.get("segmento_identificado") or existente.segmento_identificado
                )
                existente.icp_score = int(e.get("icp_score") or existente.icp_score)
                existente.justificativa_match = str(
                    e.get("justificativa_match") or existente.justificativa_match
                )
                if cnpj and not existente.cnpj:
                    existente.cnpj = cnpj
                if dominio and not existente.dominio:
                    existente.dominio = dominio
                continue

            nova = ProspectCompany(
                tenant_id=tenant_id,
                search_run_id=search_run_id,
                # Quem coletou — copiado da execução de pesquisa de origem.
                user_email=run.user_email,
                nome=nome,
                website=website,
                localizacao=str(e.get("localizacao") or ""),
                segmento_identificado=str(e.get("segmento_identificado") or ""),
                icp_score=int(e.get("icp_score") or 0),
                justificativa_match=str(e.get("justificativa_match") or ""),
                cnpj=cnpj,
                dominio=dominio,
            )
            session.add(nova)
            criadas += 1
            if cnpj:
                por_cnpj[cnpj] = nova
            if dominio:
                por_dominio[dominio] = nova
            por_nome[nome_norm] = nova

        session.commit()
        return criadas


def noticias_por_empresa(
    tenant_id: int, search_run_id: int, empresas: List[Any]
) -> Dict[int, List[dict]]:
    """Mapa `ProspectCompany.id -> lista de notícias` da pesquisa de origem.

    As notícias são produto da FASE 1 e vivem só dentro de `SearchRun.result_json`
    — a materialização não as copiou para colunas. Em vez de duplicá-las no
    banco (e ter duas versões da mesma verdade), lê-se o JSON aqui e casa-se
    pelo nome normalizado, mesma chave que `prospect_agent` usa para aplicar as
    notícias aos lotes.
    """
    mapa: Dict[int, List[dict]] = {}
    with rx.session() as session:
        run = session.get(SearchRun, search_run_id)
        if not run or run.tenant_id != tenant_id or not run.result_json:
            return mapa
        try:
            dados = json.loads(run.result_json)
        except (ValueError, TypeError):
            return mapa

    por_nome: Dict[str, List[dict]] = {}
    for e in dados.get("empresas") or []:
        if not isinstance(e, dict):
            continue
        chave = normalizar_nome(str(e.get("nome") or ""))
        noticias = [n for n in (e.get("noticias") or []) if isinstance(n, dict)]
        if chave and noticias:
            por_nome[chave] = noticias

    for c in empresas:
        achadas = por_nome.get(normalizar_nome(c.nome))
        if achadas:
            mapa[c.id] = achadas
    return mapa


# ---------------------------------------------------------------------------
# Helpers de persistência
# ---------------------------------------------------------------------------

def _registrar_custo(tenant_id: int, endpoint: str, payload: Any) -> float:
    """Grava o custo da chamada em KipflowUsage (fonte do indicador do God Mode)."""
    custo = kip.extrair_custo(payload)
    if custo <= 0:
        return 0.0
    with rx.session() as session:
        session.add(KipflowUsage(tenant_id=tenant_id, endpoint=endpoint, cost=custo))
        session.commit()
    return custo


def _lotes(itens: List[Any], tamanho: int) -> List[List[Any]]:
    return [itens[i:i + tamanho] for i in range(0, len(itens), tamanho)]


def _precisa_dados_cadastrais(c: ProspectCompany) -> bool:
    """Falta o básico da Receita (fonte gratuita)."""
    return not (c.razao_social and c.cidade and c.status_cadastral)


def _precisa_kipflow(c: ProspectCompany) -> bool:
    """Falta o que SÓ a KipFlow tem (faturamento, segmento, site, LinkedIn).

    O marcador é o payload bruto já guardado: se existe, a empresa já foi
    consultada lá e não se paga de novo — inclusive quando a resposta veio
    incompleta (nem toda empresa tem site ou LinkedIn, e insistir custaria
    dinheiro sem trazer nada).
    """
    return not (c.kipflow_raw_response or "").strip()


def _aplicar_dados_receita(c: ProspectCompany, dados: Dict[str, Any]) -> None:
    """Mapeia a resposta gratuita da Receita para os campos da empresa.

    Não sobrescreve nada que já esteja preenchido — a KipFlow pode ter rodado
    antes numa execução anterior e ter dado melhor (ex.: telefone com score).
    """
    c.razao_social = c.razao_social or dados.get("razao_social")

    cnpj_api = normalizar_cnpj(dados.get("cnpj"))
    if cnpj_api and not c.cnpj:
        c.cnpj = cnpj_api

    c.cidade = c.cidade or dados.get("municipio")
    c.estado = c.estado or normalizar_uf(dados.get("uf"))
    c.bairro = c.bairro or dados.get("bairro")
    cep = dados.get("cep")
    if cep and not c.cep:
        c.cep = str(cep).replace("-", "").replace(".", "").zfill(8)
    if not c.endereco:
        partes = [
            str(dados.get("logradouro") or "").strip(),
            str(dados.get("numero") or "").strip(),
            str(dados.get("bairro") or "").strip(),
        ]
        endereco = " - ".join(p for p in partes if p)
        c.endereco = endereco or None

    c.data_inicio_atividade = c.data_inicio_atividade or dados.get("data_inicio_atividade")
    if c.data_inicio_atividade and c.idade_empresa_anos is None:
        c.idade_empresa_anos = calcular_idade_empresa(c.data_inicio_atividade)

    situacao = dados.get("descricao_situacao_cadastral") or dados.get("situacao_cadastral")
    c.status_cadastral_original = c.status_cadastral_original or situacao
    c.status_cadastral = c.status_cadastral or mapear_status(situacao)

    # Fonte preferencial do alerta: campo estruturado da Receita.
    alerta = detectar_alerta_situacao(
        dados.get("situacao_especial"), dados.get("razao_social")
    )
    if alerta:
        c.alerta_situacao = alerta
        c.alerta_situacao_desde = c.alerta_situacao_desde or dados.get("data_situacao_especial")

    c.porte_original = c.porte_original or dados.get("porte")
    # A Receita não publica faixa de faturamento/funcionários, então o porte
    # normalmente fica None aqui e só é resolvido com o dado da KipFlow.
    c.porte = c.porte or mapear_porte(dados.get("porte"), None, None)

    # CNAE como segmento provisório: é a atividade oficial declarada. Se a
    # KipFlow rodar depois, o `segmento` dela sobrescreve (é mais comercial).
    c.segmento = c.segmento or dados.get("cnae_fiscal_descricao")

    if not c.telefone:
        telefone = formatar_telefone(receita.extrair_telefone(dados))
        if telefone:
            c.telefone = telefone
            c.telefone_whatsapp = False  # a Receita não informa WhatsApp


def _salvar_contatos_qsa(
    tenant_id: int, company_id: int, dados: Dict[str, Any], limite: int,
) -> int:
    """Grava os sócios/administradores do QSA como contatos decisores (grátis).

    Ordenados por poder de decisão (Presidente > Diretor > Sócio) e cortados no
    limite do plano. Deduplica por nome, já que o QSA não tem id de perfil.
    """
    socios = ordenar_socios(receita.extrair_qsa(dados))
    if not socios:
        return 0

    gravados = 0
    with rx.session() as session:
        existentes = {
            (ct.nome or "").strip().upper()
            for ct in session.query(CompanyContact)
            .filter(CompanyContact.company_id == company_id)
            .all()
        }
        vagas = limite - len(existentes)
        for socio in socios:
            if vagas <= 0:
                break
            contato = socio_para_contato(socio)
            if contato["nome"].upper() in existentes:
                continue
            session.add(CompanyContact(tenant_id=tenant_id, company_id=company_id, **contato))
            existentes.add(contato["nome"].upper())
            vagas -= 1
            gravados += 1
        session.commit()
    return gravados


def _aplicar_dados_empresa(c: ProspectCompany, data: Dict[str, Any]) -> None:
    """Mapeia o CompanyDto da KipFlow para os campos normalizados da empresa."""
    c.razao_social = data.get("razao_social") or c.razao_social

    cnpj_api = normalizar_cnpj(data.get("cnpj"))
    if cnpj_api:
        c.cnpj = cnpj_api

    c.cidade = data.get("municipio") or c.cidade
    # A API devolve `uf` por extenso ("RIO DE JANEIRO"); guardamos a sigla para
    # a tela não exibir "RIO DE JANEIRO / RIO DE JANEIRO".
    c.estado = normalizar_uf(data.get("uf")) or c.estado
    c.endereco = data.get("endereco") or c.endereco
    c.bairro = data.get("bairro") or c.bairro
    cep = data.get("cep")
    if cep is not None:
        # `cep` vem como número na API — zeros à esquerda se perdem sem o zfill.
        c.cep = str(cep).zfill(8)
    if isinstance(data.get("lat"), (int, float)):
        c.lat = float(data["lat"])
    if isinstance(data.get("lon"), (int, float)):
        c.lon = float(data["lon"])

    c.data_inicio_atividade = data.get("data_inicio_atividade") or c.data_inicio_atividade
    c.idade_empresa_anos = calcular_idade_empresa(c.data_inicio_atividade)

    c.porte_original = data.get("porte") or c.porte_original
    c.porte = mapear_porte(
        data.get("porte"),
        data.get("faixa_funcionarios_grupo"),
        data.get("faixa_faturamento_grupo"),
    ) or c.porte

    c.segmento = data.get("segmento") or c.segmento
    c.faturamento_estimado = (
        data.get("faixa_faturamento_grupo")
        or _valor_como_texto(data.get("faturamento_grupo"))
        or _valor_como_texto(data.get("faturamento"))
        or c.faturamento_estimado
    )

    c.status_cadastral_original = data.get("situacao_cadastral") or c.status_cadastral_original
    c.status_cadastral = mapear_status(data.get("situacao_cadastral")) or c.status_cadastral

    # A KipFlow não expõe `situacao_especial`, mas a razão social costuma
    # carregar o sufixo ("... EM RECUPERACAO JUDICIAL"). Só complementa: se a
    # Receita já sinalizou, o valor dela (que tem data) prevalece.
    c.alerta_situacao = c.alerta_situacao or detectar_alerta_situacao(
        None, data.get("razao_social")
    )

    site = escolher_website(data.get("sites"))
    if site:
        c.website_principal = site
        c.dominio = normalizar_dominio(site) or c.dominio

    telefone, is_whats = escolher_telefone(data.get("telefones"))
    telefone = formatar_telefone(telefone)
    if telefone:
        c.telefone = telefone
        c.telefone_whatsapp = is_whats

    c.linkedin_url = data.get("linkedin_url") or c.linkedin_url


def _valor_como_texto(valor: Any) -> Optional[str]:
    """Faturamento numérico vira texto de faixa legível (é sempre estimativa)."""
    if not isinstance(valor, (int, float)) or valor <= 0:
        return None
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _anexar_erro(c: ProspectCompany, mensagem: str) -> None:
    try:
        atuais = json.loads(c.enrichment_errors or "[]")
        if not isinstance(atuais, list):
            atuais = []
    except (ValueError, TypeError):
        atuais = []
    if mensagem not in atuais:
        atuais.append(mensagem)
    c.enrichment_errors = json.dumps(atuais, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Enriquecimento
# ---------------------------------------------------------------------------

async def stream_enrichment(
    tenant_id: int,
    search_run_id: int,
    limite_contatos: int,
) -> AsyncIterator[Tuple]:
    """Executa o enriquecimento das empresas de uma pesquisa.

    Emite ("progress", processadas, total, msg) / ("done", resumo) / ("error", msg).
    """
    if not kip.api_key_configurada():
        yield ("error", "Chave da KipFlow não configurada. Configure-a em Integrações (God Mode).")
        return

    avisos: List[str] = []
    custo_total = 0.0
    puladas = 0

    # --- seleção das empresas (a trava de economia mora aqui) ---
    with rx.session() as session:
        todas = (
            session.query(ProspectCompany)
            .filter(
                ProspectCompany.tenant_id == tenant_id,
                ProspectCompany.search_run_id == search_run_id,
            )
            .all()
        )
        total_geral = len(todas)
        elegiveis_ids = [c.id for c in todas if c.enrichment_status in STATUS_ELEGIVEIS]
        puladas = total_geral - len(elegiveis_ids)

    if not elegiveis_ids:
        if puladas:
            avisos.append(f"{puladas} empresa(s) já estavam enriquecidas e foram puladas.")
        yield ("done", {
            "processadas": 0, "puladas": puladas, "falhas": 0,
            "custo_total": 0.0, "avisos": avisos,
        })
        return

    total = len(elegiveis_ids)
    if puladas:
        avisos.append(f"{puladas} empresa(s) já estavam enriquecidas e foram puladas.")

    # Sonda de saúde da fonte gratuita ANTES de qualquer chamada paga (e só
    # depois de confirmar que há trabalho a fazer). Custa R$ 0,00 e evita
    # começar uma rodada que sairia ~20x mais cara. Ver EXIGIR_RECEITA_DISPONIVEL.
    if EXIGIR_RECEITA_DISPONIVEL and not await receita.verificar_disponibilidade():
        yield ("error", _MSG_RECEITA_FORA)
        return

    with rx.session() as session:
        for cid in elegiveis_ids:
            c = session.get(ProspectCompany, cid)
            if c:
                c.enrichment_status = "in_progress"
        session.commit()

    yield ("progress", 0, total, "Preparando lotes de consulta...")

    # =======================================================================
    # ETAPA 1 — dados cadastrais (em lote, para gastar 1 chamada e não 30)
    # =======================================================================
    try:
        custo_etapa, avisos_etapa = await _enriquecer_dados_cadastrais(
            tenant_id, elegiveis_ids, limite_contatos
        )
        custo_total += custo_etapa
        avisos.extend(avisos_etapa)
    except receita.ReceitaIndisponivelError:
        # A fonte gratuita caiu no meio da rodada. Aborta: o que já foi obtido
        # fica salvo, e não se gasta com o caminho caro sem o usuário saber.
        await _finalizar_status(elegiveis_ids)
        yield ("error", _MSG_RECEITA_FORA)
        return
    except kip.KipflowError as exc:
        if exc.fatal:
            await _finalizar_status(elegiveis_ids)
            yield ("error", exc.mensagem_pt)
            return
        avisos.append(exc.mensagem_pt)

    yield ("progress", 0, total, "Dados cadastrais concluídos. Buscando decisores...")

    # =======================================================================
    # ETAPA 2 — contatos decisores (1 chamada por empresa; é o custo principal)
    # =======================================================================
    processadas = 0
    falhas = 0
    for cid in elegiveis_ids:
        with rx.session() as session:
            c = session.get(ProspectCompany, cid)
            if not c:
                continue
            nome = c.nome
            linkedin_url = c.linkedin_url
            cnpj = c.cnpj
            precisa_telefone = not c.telefone
            ja_tem_contatos = (
                session.query(CompanyContact)
                .filter(CompanyContact.company_id == cid)
                .count()
            )

        # --- telefone: só se os datasets não trouxeram nenhum ---
        if precisa_telefone and cnpj:
            try:
                payload = await kip.buscar_telefones(cnpj, limite=1)
                custo_total += _registrar_custo(tenant_id, "contacts/phones", payload)
                telefone, is_whats = escolher_telefone(_lista_telefones(payload))
                telefone = formatar_telefone(telefone)
                if telefone:
                    with rx.session() as session:
                        c = session.get(ProspectCompany, cid)
                        if c:
                            c.telefone = telefone
                            c.telefone_whatsapp = is_whats
                            session.commit()
            except kip.KipflowError as exc:
                if exc.fatal:
                    await _finalizar_status(elegiveis_ids)
                    yield ("error", exc.mensagem_pt)
                    return

        # --- decisores no LinkedIn: FALLBACK do QSA ---
        # O QSA gratuito já rodou na etapa anterior. Só se ele não trouxe
        # ninguém é que vale pagar pelo LinkedIn — é a troca que define a
        # estratégia híbrida (ver USAR_LINKEDIN_SE_QSA_VAZIO).
        if not ja_tem_contatos and USAR_LINKEDIN_SE_QSA_VAZIO:
            public_id = extrair_linkedin_public_id(linkedin_url)
            if not public_id and USAR_FALLBACK_LINKEDIN_PAGO:
                public_id = None  # ponto de extensão: resolver via endpoint pago
            if public_id:
                try:
                    custo_total += await _buscar_contatos_em_niveis(
                        tenant_id, cid, public_id, limite_contatos
                    )
                except kip.KipflowError as exc:
                    if exc.fatal:
                        await _finalizar_status(elegiveis_ids)
                        yield ("error", exc.mensagem_pt)
                        return
                    with rx.session() as session:
                        c = session.get(ProspectCompany, cid)
                        if c:
                            _anexar_erro(c, exc.mensagem_pt)
                            session.commit()
            else:
                with rx.session() as session:
                    c = session.get(ProspectCompany, cid)
                    if c:
                        _anexar_erro(c, "Sem LinkedIn da empresa: não foi possível buscar decisores.")
                        session.commit()

        # --- fecha a empresa: percentual + status ---
        # Ainda sem e-mail (a etapa 3 vem depois): o percentual é recalculado
        # lá no fim, senão quem receber e-mail ficaria com o número defasado.
        with rx.session() as session:
            c = session.get(ProspectCompany, cid)
            if c:
                _recalcular_percentual(session, c)
                c.enriched_at = brt_now()
                if c.enrichment_status == "failed":
                    falhas += 1
                session.commit()

        processadas += 1
        yield ("progress", processadas, total, f"Enriquecendo empresas - {processadas}/{total} ({nome[:40]})")

    # =======================================================================
    # ETAPA 3 — e-mail dos contatos (Hunter.io), a partir do nome + domínio
    # =======================================================================
    async for evento in _buscar_emails_dos_contatos(tenant_id, elegiveis_ids, avisos, total):
        yield evento

    # O e-mail é o 12º campo do percentual, e ele só existe agora — sem este
    # recálculo, quem acabou de receber e-mail continuaria marcado com o
    # percentual (e o status) de antes da etapa 3.
    with rx.session() as session:
        for cid in elegiveis_ids:
            c = session.get(ProspectCompany, cid)
            if c:
                _recalcular_percentual(session, c)
        session.commit()

    sem_contato = _contar_sem_contato(tenant_id, search_run_id)
    if sem_contato:
        avisos.append(
            f"{sem_contato} empresa(s) ficaram sem contato decisor "
            "(Sem perfil que atenda aos filtros)."
        )

    yield ("done", {
        "processadas": processadas,
        "puladas": puladas,
        "falhas": falhas,
        "custo_total": round(custo_total, 4),
        "avisos": avisos,
    })


# ---------------------------------------------------------------------------
# Etapa 3: e-mail dos contatos decisores (Hunter.io)
# ---------------------------------------------------------------------------
# Erros que são do PAR (contato, empresa) e não vão mudar numa nova execução:
# insistir neles em toda rodada só gastaria tempo. Só estes marcam o contato
# como "já tentado"; um erro de rede ou de indisponibilidade não marca, para o
# contato voltar à fila na próxima vez.
_ERROS_PERMANENTES_HUNTER = (
    "invalid_domain", "invalid_first_name", "invalid_last_name",
    "invalid_full_name", "claimed_email",
)


def _dominio_da_empresa(c: ProspectCompany) -> str:
    """Domínio para consultar o Hunter, na ordem em que a confiança cai:
    o `dominio` normalizado, depois o site confirmado no enriquecimento, e por
    último o que o agente de pesquisa achou."""
    from prospect_agent.services import hunter_client as hunter

    for candidato in (c.dominio, c.website_principal, c.website):
        dominio = hunter.normalizar_dominio(candidato or "")
        if dominio:
            return dominio
    return ""


def _contatos_sem_email(ids_empresas: List[int]) -> List[Tuple[int, int, str, str]]:
    """(contact_id, company_id, nome do contato, domínio) dos contatos que ainda
    valem uma busca: sem e-mail, nunca tentados antes e com domínio conhecido.

    `email_buscado_em` é a trava de custo desta etapa — sem ela, reprocessar uma
    pesquisa gastaria a cota do ciclo inteira de novo nos mesmos contatos.
    """
    pendentes: List[Tuple[int, int, str, str]] = []
    with rx.session() as session:
        for cid in ids_empresas:
            empresa = session.get(ProspectCompany, cid)
            if not empresa:
                continue
            dominio = _dominio_da_empresa(empresa)
            if not dominio:
                continue
            contatos = (
                session.query(CompanyContact)
                .filter(CompanyContact.company_id == cid)
                .all()
            )
            for ct in contatos:
                if ct.email or ct.email_buscado_em is not None:
                    continue
                pendentes.append((ct.id, cid, ct.nome, dominio))
    return pendentes


def _gravar_email(contact_id: int, email: str, confianca: Optional[int]) -> None:
    with rx.session() as session:
        ct = session.get(CompanyContact, contact_id)
        if not ct:
            return
        ct.email = email
        ct.email_confianca = confianca
        ct.email_buscado_em = brt_now()
        session.commit()


def _marcar_tentativa(contact_id: int) -> None:
    """Registra que já se tentou, sem gravar e-mail (não achou, ou erro que não
    vai mudar). Impede uma segunda cobrança pelo mesmo contato."""
    with rx.session() as session:
        ct = session.get(CompanyContact, contact_id)
        if ct:
            ct.email_buscado_em = brt_now()
            session.commit()


async def _buscar_emails_dos_contatos(
    tenant_id: int, ids_empresas: List[int], avisos: List[str], total_empresas: int,
) -> AsyncIterator[Tuple]:
    """Busca o e-mail profissional de cada contato decisor já coletado.

    Etapa BEST-EFFORT: nada aqui pode derrubar o enriquecimento, que a esta
    altura já concluiu e salvou tudo. Qualquer problema (chave ausente, cota
    esgotada, API fora) vira aviso na tela e a rodada termina normalmente —
    mesma política que o projeto já adota para falha de envio de e-mail.

    A cota é verificada ANTES de cada chamada, e não só uma vez no início: um
    número de créditos menor que o de contatos pendentes é o caso normal com o
    plano gratuito (50 créditos/mês contra dezenas de contatos por rodada).

    `total_empresas` existe por causa do contrato de progresso: o consumidor
    grava `(atual, total)` direto em `EnrichmentRun.processadas/total_empresas`,
    e o evento "done" só restaura `processadas`. Emitir aqui o total de
    CONTATOS deixaria a barra andando para trás no meio da rodada e gravaria
    um total errado no histórico da execução. Por isso a barra fica cheia
    (todas as empresas já foram enriquecidas, esta etapa é posterior) e o
    andamento por contato vai na MENSAGEM.
    """
    from prospect_agent.services import hunter_client as hunter

    if not hunter.api_key_configurada():
        avisos.append(
            "Busca de e-mails não executada: nenhuma conta da Hunter está "
            "configurada em Integrações no painel do super admin."
        )
        return

    pendentes = _contatos_sem_email(ids_empresas)
    if not pendentes:
        return

    # O balanceador é criado uma vez por rodada e carrega, dentro dela, quais
    # contas já saíram da roda (cota esgotada ou chave inválida).
    balanceador = hunter.Balanceador(tenant_id)
    if balanceador.creditos_restantes() <= 0:
        avisos.append(_aviso_de_cota(tenant_id, len(pendentes)))
        return

    total = len(pendentes)
    encontrados = 0
    sem_resultado = 0
    nao_buscados = 0

    yield ("progress", total_empresas, total_empresas, "Buscando e-mails dos contatos...")

    for indice, (contact_id, _company_id, nome, dominio) in enumerate(pendentes, start=1):
        if balanceador.creditos_restantes() <= 0:
            nao_buscados = total - (indice - 1)
            avisos.append(_aviso_de_cota(tenant_id, nao_buscados))
            break

        try:
            resultado = await balanceador.buscar_email(
                contact_id=contact_id, dominio=dominio, nome_completo=nome,
            )
        except hunter.CotaHunterEsgotada:
            # Acabou o crédito em TODAS as contas: o balanceador já tentou as
            # outras antes de chegar aqui.
            nao_buscados = total - (indice - 1)
            avisos.append(_aviso_de_cota(tenant_id, nao_buscados))
            break
        except hunter.HunterError as exc:
            if exc.fatal:
                nao_buscados = total - (indice - 1)
                avisos.append(f"{exc.mensagem_pt} {nao_buscados} contato(s) ficaram sem e-mail.")
                break
            if exc.code in _ERROS_PERMANENTES_HUNTER:
                _marcar_tentativa(contact_id)
            sem_resultado += 1
            continue

        if resultado:
            _gravar_email(contact_id, resultado["email"], resultado["confianca"])
            encontrados += 1
        else:
            _marcar_tentativa(contact_id)
            sem_resultado += 1

        yield (
            "progress", total_empresas, total_empresas,
            f"Buscando e-mails - {indice}/{total} ({nome[:40]})",
        )

    if encontrados:
        avisos.append(f"{encontrados} e-mail(s) de contato encontrados na Hunter.")
    if sem_resultado:
        avisos.append(
            f"{sem_resultado} contato(s) sem e-mail localizável no domínio da empresa."
        )
    # Uma chave errada não interrompe a etapa (as outras contas seguem
    # trabalhando), mas precisa chegar a quem consegue corrigir.
    if balanceador.slots_com_chave_invalida:
        contas = ", ".join(str(s) for s in balanceador.slots_com_chave_invalida)
        avisos.append(
            f"Conta(s) {contas} da Hunter com chave inválida: as buscas foram "
            "redirecionadas para as demais. Revise a chave em Integrações no "
            "painel do super admin."
        )


def _aviso_de_cota(tenant_id: int, nao_buscados: int) -> str:
    """Mensagem de cota esgotada. Diz o limite e a DATA em que ele renova.

    A data é calculada, não escrita como "no próximo mês": o Hunter renova no
    aniversário da assinatura, e uma data concreta é o que permite ao usuário
    decidir se espera ou se aumenta o plano.
    """
    from prospect_agent.services import hunter_client as hunter
    from prospect_agent.services.settings import (
        get_hunter_creditos_totais, get_hunter_dia_renovacao, slots_hunter_configurados,
    )

    limite = get_hunter_creditos_totais()
    contas = len(slots_hunter_configurados())
    renova_em = hunter.proxima_renovacao(get_hunter_dia_renovacao())
    return (
        f"E-mails não puderam ser buscados por limite de uso: a cota da Hunter "
        f"({limite} crédito(s) por ciclo em {contas} conta(s)) já foi consumida. "
        f"{nao_buscados} contato(s) ficaram sem e-mail e serão tentados de novo a "
        f"partir de {renova_em:%d/%m/%Y}, quando a cota renova. O limite, o número "
        "de contas e a data de renovação são ajustáveis no painel do super admin."
    )


async def _enriquecer_dados_cadastrais(
    tenant_id: int, ids: List[int], limite_contatos: int,
) -> Tuple[float, List[str]]:
    """Preenche os dados cadastrais na ordem GRATUITO -> PAGO.

    1. Resolve o CNPJ por nome na KipFlow (pago; nenhuma fonte gratuita busca
       empresa por nome, então este passo não tem substituto).
    2. Consulta a Receita de graça (BrasilAPI/Minha Receita): razão social,
       cidade/UF, início de atividade, situação, telefone **e o QSA**, que já
       vira contato decisor sem custo.
    3. Só então chama a KipFlow em lote, para o que as fontes públicas não têm
       (faixa de faturamento/funcionários, segmento, website, LinkedIn).
    """
    custo = 0.0
    avisos: List[str] = []

    with rx.session() as session:
        pendentes = [
            c for c in (session.get(ProspectCompany, i) for i in ids)
            if c and (_precisa_dados_cadastrais(c) or _precisa_kipflow(c))
        ]
        por_cnpj = {c.cnpj: c.id for c in pendentes if c.cnpj}
        por_dominio = {c.dominio: c.id for c in pendentes if not c.cnpj and c.dominio}
        sem_chave = [(c.id, c.nome, c.localizacao) for c in pendentes if not c.cnpj and not c.dominio]

    # --- fallback: resolve CNPJ por nome antes dos lotes ---
    for cid, nome, localizacao in sem_chave:
        try:
            payload = await kip.casar_empresa_por_nome(nome, sigla_uf_de_localizacao(localizacao))
            custo += _registrar_custo(tenant_id, "intelligence/company-match", payload)
            matches = ((payload.get("data") or {}).get("matches") or [])
            melhor = next(
                (m for m in matches if (m.get("similarity") or 0) >= SIMILARIDADE_MINIMA),
                None,
            )
            if melhor:
                cnpj = normalizar_cnpj(melhor.get("cnpj"))
                if cnpj:
                    por_cnpj[cnpj] = cid
                    with rx.session() as session:
                        c = session.get(ProspectCompany, cid)
                        if c:
                            c.cnpj = cnpj
                            session.commit()
                    continue
            avisos.append(f"'{nome}': não foi possível identificar o CNPJ com confiança.")
        except kip.KipflowError as exc:
            if exc.fatal:
                raise
            avisos.append(f"'{nome}': {exc.mensagem_pt}")

    # --- FONTE GRATUITA: Receita Federal (dados cadastrais + QSA) ---
    # Roda antes da KipFlow de propósito: o que vier de graça aqui não precisa
    # ser comprado depois (telefone e contatos decisores, principalmente).
    falhas_seguidas = 0
    for cnpj, cid in list(por_cnpj.items()):
        try:
            dados = await receita.consultar_cnpj(cnpj)
            falhas_seguidas = 0
        except receita.ReceitaIndisponivelError:
            # Fonte fora do ar (≠ CNPJ inexistente). Algumas falhas seguidas
            # significam queda do serviço: abortar antes que o custo exploda.
            falhas_seguidas += 1
            if falhas_seguidas >= MAX_FALHAS_SEGUIDAS_RECEITA:
                raise
            continue
        if not dados:
            avisos.append(f"CNPJ {cnpj}: não encontrado nas bases da Receita.")
            continue
        with rx.session() as session:
            c = session.get(ProspectCompany, cid)
            if c:
                _aplicar_dados_receita(c, dados)
                session.commit()
        _salvar_contatos_qsa(tenant_id, cid, dados, limite_contatos)

    # --- lotes por CNPJ (pago, só para quem ainda precisa) ---
    with rx.session() as session:
        por_cnpj = {
            cnpj: cid for cnpj, cid in por_cnpj.items()
            if (c := session.get(ProspectCompany, cid)) and _precisa_kipflow(c)
        }

    for lote in _lotes(list(por_cnpj.keys()), kip.MAX_ITENS_POR_LOTE):
        payload = await kip.buscar_empresas_por_cnpj_em_lote(lote, DATASETS_EMPRESA)
        custo += _registrar_custo(tenant_id, "companies/batch/cnpj", payload)
        _aplicar_resultados_lote(payload, por_cnpj, normalizar_cnpj)

    # --- lotes por domínio ---
    for lote in _lotes(list(por_dominio.keys()), kip.MAX_ITENS_POR_LOTE):
        payload = await kip.buscar_empresas_por_dominio_em_lote(lote, DATASETS_EMPRESA)
        custo += _registrar_custo(tenant_id, "companies/batch/domain", payload)
        _aplicar_resultados_lote(payload, por_dominio, normalizar_dominio)

    # --- 2ª passada gratuita ---
    # Empresas que entraram pelo domínio só ganharam CNPJ agora, no lote pago,
    # e por isso ficaram de fora da consulta gratuita lá em cima. Sem esta
    # passada elas nunca receberiam o QSA — e QSA é a fonte de contato de graça.
    for cid in por_dominio.values():
        with rx.session() as session:
            c = session.get(ProspectCompany, cid)
            cnpj = c.cnpj if c else None
        if not cnpj:
            continue
        try:
            dados = await receita.consultar_cnpj(cnpj)
        except receita.ReceitaIndisponivelError:
            # Já pagamos o lote destas empresas; não vale abortar aqui, mas
            # registramos para o usuário saber que ficaram sem QSA.
            avisos.append(
                "API ao buscar os sócios de parte das "
                "empresas (elas podem ter ficado sem contato)."
            )
            break
        if not dados:
            continue
        with rx.session() as session:
            c = session.get(ProspectCompany, cid)
            if c:
                _aplicar_dados_receita(c, dados)
                session.commit()
        _salvar_contatos_qsa(tenant_id, cid, dados, limite_contatos)

    return custo, avisos


def _aplicar_resultados_lote(payload: Dict[str, Any], mapa_id: Dict, normalizador) -> None:
    """Casa cada item do lote com a empresa correspondente e persiste."""
    resultados = payload.get("results") or []
    with rx.session() as session:
        for item in resultados:
            if not isinstance(item, dict):
                continue
            chave = normalizador(item.get("identifier"))
            cid = mapa_id.get(chave)
            if cid is None:
                continue
            c = session.get(ProspectCompany, cid)
            if not c:
                continue
            if not item.get("success") or not item.get("data"):
                erro = item.get("error") or {}
                _anexar_erro(c, str(erro.get("message") or "Empresa não encontrada na KipFlow."))
                continue
            data = item["data"]
            _aplicar_dados_empresa(c, data)
            c.kipflow_raw_response = json.dumps(data, ensure_ascii=False)
        session.commit()


def _lista_telefones(payload: Dict[str, Any]) -> List[dict]:
    """Extrai a lista de telefones do endpoint dedicado (formato mais solto)."""
    dados = payload.get("data")
    if isinstance(dados, list):
        return [d for d in dados if isinstance(d, dict)]
    if isinstance(dados, dict):
        telefones = dados.get("telefones") or dados.get("phones")
        if isinstance(telefones, list):
            return [d for d in telefones if isinstance(d, dict)]
    return []


async def _buscar_contatos_em_niveis(
    tenant_id: int, company_id: int, public_id: str, limite: int,
) -> float:
    """Busca decisores por nível de senioridade, do mais sênior para o menos.

    Por que em níveis e não numa chamada só: a KipFlow cobra por PESSOA
    retornada e não garante ordenação por senioridade. Numa consulta única, as
    poucas vagas do plano (2 no Smart, 4 no Smart Plus) poderiam ser gastas com
    supervisores enquanto havia um diretor disponível. Aqui o nível sênior é
    consultado primeiro e só as vagas que sobram descem para a liderança
    operacional.

    Os níveis são disjuntos (senioridades diferentes), então ninguém é cobrado
    duas vezes. Consulta que não retorna ninguém custa R$ 0,00 — verificado em
    chamada real —, então o nível extra é praticamente de graça.

    Devolve o custo acumulado.
    """
    custo = 0.0
    restantes = limite

    for senioridades in NIVEIS_SENIORIDADE:
        if restantes <= 0:
            break
        payload = await kip.buscar_decisores(
            public_id,
            senioridades=senioridades,
            areas=AREAS_ALVO,
            quantidade=restantes,
        )
        custo += _registrar_custo(tenant_id, "social/personas", payload)
        salvos = _salvar_contatos(tenant_id, company_id, payload)
        restantes -= salvos

    return custo


def _salvar_contatos(tenant_id: int, company_id: int, payload: Dict[str, Any]) -> int:
    """Persiste os decisores retornados, deduplicando por perfil.

    Devolve quantos foram efetivamente gravados (o chamador usa isso para saber
    quantas vagas do plano ainda sobram para o próximo nível de senioridade).
    """
    pessoas = payload.get("data")
    if not isinstance(pessoas, list):
        return 0
    gravados = 0
    with rx.session() as session:
        existentes = {
            ct.perfil_public_id
            for ct in session.query(CompanyContact)
            .filter(CompanyContact.company_id == company_id)
            .all()
            if ct.perfil_public_id
        }
        for p in pessoas:
            if not isinstance(p, dict) or not p.get("full_name"):
                continue
            public_id = p.get("profile_public_id")
            if public_id and public_id in existentes:
                continue
            session.add(
                CompanyContact(
                    tenant_id=tenant_id,
                    company_id=company_id,
                    nome=str(p.get("full_name")),
                    cargo=str(p.get("current_job_title") or ""),
                    senioridade=str(p.get("seniority") or ""),
                    area=str(p.get("area") or ""),
                    perfil_url=str(p.get("profile_url") or ""),
                    perfil_public_id=public_id,
                    origem="linkedin",
                )
            )
            gravados += 1
            if public_id:
                existentes.add(public_id)
        session.commit()
    return gravados


def _recalcular_percentual(session, c: ProspectCompany) -> None:
    """Aplica a fórmula do percentual a uma empresa e atualiza o status.

    Existe como função porque os dois insumos que a fórmula recebe de fora
    (quantos contatos, quantos com e-mail) precisam ser contados do mesmo jeito
    nos TRÊS momentos em que o percentual muda: ao fechar a empresa na etapa 2,
    depois da busca de e-mails na etapa 3, e ao abortar. Contar diferente em um
    deles daria percentuais que não batem entre si.

    Não faz commit: quem chama decide o escopo da transação.
    """
    contatos = (
        session.query(CompanyContact)
        .filter(CompanyContact.company_id == c.id)
        .all()
    )
    c.enrichment_percentage = calcular_percentual(
        c,
        qtd_contatos=len(contatos),
        qtd_emails=sum(1 for ct in contatos if ct.email),
    )
    c.enrichment_status = definir_status(c.enrichment_percentage)


async def _finalizar_status(ids: List[int]) -> None:
    """Ao abortar, recalcula o status de quem ficou em `in_progress`.

    O que já foi obtido continua valendo — nenhum crédito é jogado fora.
    """
    with rx.session() as session:
        for cid in ids:
            c = session.get(ProspectCompany, cid)
            if not c or c.enrichment_status != "in_progress":
                continue
            _recalcular_percentual(session, c)
        session.commit()


def _contar_sem_contato(tenant_id: int, search_run_id: int) -> int:
    with rx.session() as session:
        empresas = (
            session.query(ProspectCompany)
            .filter(
                ProspectCompany.tenant_id == tenant_id,
                ProspectCompany.search_run_id == search_run_id,
            )
            .all()
        )
        sem = 0
        for c in empresas:
            qtd = (
                session.query(CompanyContact)
                .filter(CompanyContact.company_id == c.id)
                .count()
            )
            if qtd == 0:
                sem += 1
        return sem
