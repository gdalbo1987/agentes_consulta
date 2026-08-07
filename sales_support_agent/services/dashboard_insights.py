"""Agregações sobre os leads (`ProspectCompany`) de um tenant — cross-run,
puras funções de leitura (sem mutação de estado).

Reaproveitado por DOIS consumidores: o Dashboard (`/dashboard`, KPIs e
gráficos) e o agente de Insights IA (`services/insights_agent.py`, como
`tools`) — mesma fonte de verdade para os dois, sem duplicar a lógica de
agregação nem correr o risco de os números do dashboard divergirem dos que o
chat responde.

Todas as funções operam sobre TODAS as pesquisas do tenant (cross search_run),
já que `ensure_companies_materialized` agora deduplica por CNPJ/domínio/nome
entre pesquisas — uma empresa conta uma vez só, mesmo se encontrada em mais de
uma pesquisa.
"""

import json
from typing import Any, Dict, List, Optional

import reflex as rx

from prospect_agent.models import CompanyContact, ProspectCompany
from prospect_agent.services.enrichment_rules import (
    STATUS_CONSIDERADOS_ENRIQUECIDOS,
    bucket_faturamento,
)


def _nomes_produtos_por_search_run(tenant_id: int) -> Dict[int, set]:
    """search_run_id -> nomes dos produtos (snapshot de `entrada.produtos`)
    pesquisados naquela execução. Necessário para filtrar por produto porque
    `ProspectCompany` não carrega produto_id — uma pesquisa cobre vários
    produtos ao mesmo tempo, então o produto "dono" de uma empresa é o(s)
    da pesquisa que a encontrou por último (seu `search_run_id` atual — mesma
    convenção já usada para notícias, ver `services/enrichment.
    noticias_por_empresa`). Lido do JSON, não da tabela `Product` ao vivo, para
    continuar funcionando mesmo se o produto tiver sido excluído depois."""
    from prospect_agent.models import SearchRun

    with rx.session() as session:
        runs = session.query(SearchRun).filter(SearchRun.tenant_id == tenant_id).all()

    mapa: Dict[int, set] = {}
    for run in runs:
        try:
            dados = json.loads(run.result_json or "{}")
        except (ValueError, TypeError):
            dados = {}
        nomes = {
            p.get("nome") for p in (dados.get("entrada") or {}).get("produtos") or []
            if p.get("nome")
        }
        mapa[run.id] = nomes
    return mapa


def produtos_pesquisados(tenant_id: int) -> List[str]:
    """Nomes distintos de produtos já pesquisados pelo tenant, para popular o
    filtro por produto (Dashboard e /leads)."""
    mapa = _nomes_produtos_por_search_run(tenant_id)
    todos = {nome for nomes in mapa.values() for nome in nomes}
    return sorted(todos)


def listar_empresas(
    tenant_id: int,
    produto: Optional[str] = None,
    user_email: Optional[str] = None,
) -> List[ProspectCompany]:
    """Wrapper público de `_carregar_empresas` — usado por consumidores fora
    deste módulo (ex.: `LeadsState._carregar_leads`) que precisam da mesma
    lista/filtros já usados no Top leads do Dashboard, sem duplicar a lógica.

    `user_email` filtra pelo usuário que COLETOU a empresa. É opcional para o
    Dashboard continuar chamando sem ele e ver a base inteira da organização.
    """
    return _carregar_empresas(tenant_id, produto=produto, user_email=user_email)


def _carregar_empresas(
    tenant_id: int,
    produto: Optional[str] = None,
    user_email: Optional[str] = None,
) -> List[ProspectCompany]:
    with rx.session() as session:
        q = session.query(ProspectCompany).filter(ProspectCompany.tenant_id == tenant_id)
        # Filtro por coletor vai no SQL (a coluna é indexada); o de produto não
        # dá, porque o vínculo empresa→produto mora no JSON da pesquisa.
        if user_email:
            q = q.filter(ProspectCompany.user_email == user_email)
        empresas = q.all()
    if not produto:
        return empresas
    mapa = _nomes_produtos_por_search_run(tenant_id)
    return [e for e in empresas if produto in mapa.get(e.search_run_id, set())]


def _distribuicao(
    empresas: List[ProspectCompany], valor_fn, *, top_n: Optional[int] = None,
    rotulo_vazio: str = "Não informado",
) -> List[Dict[str, Any]]:
    """Agrupa `empresas` por `valor_fn(empresa)`, contando ocorrências e
    calculando o percentual sobre o total. Se `top_n` for informado, mantém só
    os N maiores grupos e soma o resto num grupo "Outros" (evita um donut com
    dezenas de fatias minúsculas para campos de texto livre, como segmento).
    """
    total = len(empresas)
    if total == 0:
        return []

    contagem: Dict[str, int] = {}
    for e in empresas:
        valor = valor_fn(e)
        chave = str(valor).strip() if valor else rotulo_vazio
        contagem[chave] = contagem.get(chave, 0) + 1

    itens = sorted(contagem.items(), key=lambda kv: kv[1], reverse=True)
    if top_n is not None and len(itens) > top_n:
        principais = itens[:top_n]
        resto = sum(qtd for _, qtd in itens[top_n:])
        itens = principais + [("Outros", resto)]

    return [
        {"nome": nome, "quantidade": qtd, "percentual": round(qtd / total * 100)}
        for nome, qtd in itens
    ]


def carregar_kpis(tenant_id: int) -> Dict[str, Any]:
    """KPIs gerais: total encontrado, total enriquecido, scores médios."""
    empresas = _carregar_empresas(tenant_id)
    total = len(empresas)
    if total == 0:
        return {
            "leads_encontrados": 0,
            "leads_enriquecidos": 0,
            "score_medio_icp": 0,
            "score_medio_enriquecimento": 0,
        }

    enriquecidos = [e for e in empresas if e.enrichment_status in STATUS_CONSIDERADOS_ENRIQUECIDOS]
    score_icp = sum(e.icp_score or 0 for e in empresas) / total
    score_enriq = sum(e.enrichment_percentage or 0 for e in empresas) / total

    return {
        "leads_encontrados": total,
        "leads_enriquecidos": len(enriquecidos),
        "score_medio_icp": round(score_icp),
        "score_medio_enriquecimento": round(score_enriq),
    }


def distribuicao_por_segmento(tenant_id: int, top_n: int = 6) -> List[Dict[str, Any]]:
    empresas = _carregar_empresas(tenant_id)
    return _distribuicao(
        empresas,
        lambda e: e.segmento or e.segmento_identificado,
        top_n=top_n,
        rotulo_vazio="Não identificado",
    )


def distribuicao_por_faturamento(tenant_id: int) -> List[Dict[str, Any]]:
    empresas = _carregar_empresas(tenant_id)
    return _distribuicao(empresas, lambda e: bucket_faturamento(e.faturamento_estimado))


def distribuicao_por_porte(tenant_id: int) -> List[Dict[str, Any]]:
    empresas = _carregar_empresas(tenant_id)
    return _distribuicao(empresas, lambda e: e.porte)


def distribuicao_por_situacao_cadastral(tenant_id: int) -> List[Dict[str, Any]]:
    empresas = _carregar_empresas(tenant_id)
    return _distribuicao(empresas, lambda e: e.status_cadastral)


def distribuicao_por_prioridade(tenant_id: int) -> List[Dict[str, Any]]:
    empresas = _carregar_empresas(tenant_id)
    return _distribuicao(empresas, lambda e: e.priorizacao_classe, rotulo_vazio="Não priorizado")


def distribuicao_por_cargo_contato(tenant_id: int, top_n: int = 6) -> List[Dict[str, Any]]:
    """Quantidade de contatos decisores por cargo (Diretor, Sócio-Administrador,
    ...), cross-empresa. `cargo` é texto livre (vem da qualificação do QSA ou
    do current_job_title do LinkedIn), por isso usa o mesmo agrupamento
    top-N + "Outros" já usado para segmento (campo de texto livre), em vez de
    uma lista fixa de cargos."""
    with rx.session() as session:
        contatos = (
            session.query(CompanyContact)
            .filter(CompanyContact.tenant_id == tenant_id)
            .all()
        )
    total = len(contatos)
    if total == 0:
        return []

    contagem: Dict[str, int] = {}
    for c in contatos:
        chave = (c.cargo or c.senioridade or "").strip() or "Não informado"
        contagem[chave] = contagem.get(chave, 0) + 1

    itens = sorted(contagem.items(), key=lambda kv: kv[1], reverse=True)
    if len(itens) > top_n:
        principais = itens[:top_n]
        resto = sum(qtd for _, qtd in itens[top_n:])
        itens = principais + [("Outros", resto)]

    return [
        {"nome": nome, "quantidade": qtd, "percentual": round(qtd / total * 100)}
        for nome, qtd in itens
    ]


def leads_por_estado(tenant_id: int) -> Dict[str, int]:
    """UF (sigla) -> contagem de leads. Só entra quem tem `estado` preenchido
    (dado do enriquecimento) — usado pelo mapa do Brasil."""
    empresas = _carregar_empresas(tenant_id)
    contagem: Dict[str, int] = {}
    for e in empresas:
        if e.estado:
            contagem[e.estado] = contagem.get(e.estado, 0) + 1
    return contagem


_COR_SEM_LEAD = "#e5e7eb"
_COR_FRACA = (207, 224, 245)  # "#cfe0f5" claro (poucos leads)
_COR_FORTE = (18, 35, 63)  # "#12233f" escuro (mesma família do BTN_GRADIENT)


def cor_por_intensidade(valor: int, maximo: int) -> str:
    """Azul claro -> escuro conforme a concentração de leads no estado —
    mesma família de cor do BTN_GRADIENT do app (não introduz paleta nova).
    Estado sem nenhum lead fica cinza neutro (dado ausente, não zero fraco)."""
    if valor <= 0 or maximo <= 0:
        return _COR_SEM_LEAD
    t = min(1.0, valor / maximo)
    rgb = tuple(round(_COR_FRACA[i] + (_COR_FORTE[i] - _COR_FRACA[i]) * t) for i in range(3))
    return "#%02x%02x%02x" % rgb


def cor_gauge(score: int) -> str:
    """Vermelho -> amarelo -> verde conforme o score (0-100), para o gauge de
    enriquecimento. Interpolação HSL simples: hue 0 (vermelho) a 120 (verde)."""
    import colorsys

    score = max(0, min(100, score))
    hue = (score / 100) * 120 / 360
    r, g, b = colorsys.hls_to_rgb(hue, 0.45, 0.75)
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))


# ---------------------------------------------------------------------------
# Recortes por produto, por coletor e por lead — usados pelas tools do chat de
# Insights (`services/insights_agent.py`). Ficam aqui, e não lá, pelo mesmo
# motivo das agregações acima: o chat e o dashboard leem da mesma fonte.
# ---------------------------------------------------------------------------
SEM_PRODUTO = "(sem produto identificado)"


def _contagem_de_contatos(tenant_id: int) -> Dict[int, int]:
    """company_id -> quantos contatos decisores. Uma query só, para não fazer
    N+1 ao montar um resumo que percorre a base inteira."""
    with rx.session() as session:
        contatos = (
            session.query(CompanyContact)
            .filter(CompanyContact.tenant_id == tenant_id)
            .all()
        )
    contagem: Dict[int, int] = {}
    for c in contatos:
        contagem[c.company_id] = contagem.get(c.company_id, 0) + 1
    return contagem


def _metricas(empresas: List[ProspectCompany], contatos_por_empresa: Dict[int, int]) -> Dict[str, Any]:
    """Bloco de métricas de um conjunto de leads.

    Os dois scores são reportados SEPARADAMENTE de propósito: `icp_score` mede
    fit com o ICP e existe para todo lead; `priorizacao_score_final` só existe
    depois da priorização e pondera outros critérios. Uma média única
    misturaria duas réguas diferentes e daria um número sem significado.
    """
    icp = [e.icp_score or 0 for e in empresas]
    prio = [e.priorizacao_score_final for e in empresas if e.priorizacao_score_final is not None]
    enriquecidos = [e for e in empresas if e.enrichment_status in STATUS_CONSIDERADOS_ENRIQUECIDOS]
    return {
        "leads": len(empresas),
        "leads_enriquecidos": len(enriquecidos),
        "leads_priorizados": len(prio),
        "contatos": sum(contatos_por_empresa.get(e.id, 0) for e in empresas),
        "score_icp_medio": round(sum(icp) / len(icp)) if icp else 0,
        "score_icp_maximo": max(icp) if icp else 0,
        "score_priorizacao_medio": round(sum(prio) / len(prio)) if prio else 0,
        "score_priorizacao_maximo": max(prio) if prio else 0,
    }


def resumo_por_produto(tenant_id: int) -> List[Dict[str, Any]]:
    """Métricas agregadas por produto pesquisado, do maior para o menor número
    de leads.

    ATENÇÃO ao interpretar: uma pesquisa pode cobrir vários produtos ao mesmo
    tempo, e o lead encontrado por ela conta para TODOS eles. Logo a soma dos
    `leads` por produto pode ser maior que o total de leads da base — não é
    erro de contagem, é sobreposição.
    """
    empresas = _carregar_empresas(tenant_id)
    mapa = _nomes_produtos_por_search_run(tenant_id)
    contatos = _contagem_de_contatos(tenant_id)

    por_produto: Dict[str, List[ProspectCompany]] = {}
    for e in empresas:
        nomes = mapa.get(e.search_run_id) or {SEM_PRODUTO}
        for nome in nomes:
            por_produto.setdefault(nome, []).append(e)

    linhas = [
        {"produto": nome, **_metricas(lista, contatos)}
        for nome, lista in por_produto.items()
    ]
    return sorted(linhas, key=lambda l: l["leads"], reverse=True)


def resumo_por_usuario(tenant_id: int) -> List[Dict[str, Any]]:
    """Métricas agregadas por usuário que COLETOU o lead (`user_email`), do
    maior para o menor número de leads. O nome vem de `User`; leads antigos,
    anteriores à coluna `user_email`, aparecem como "(não atribuído)"."""
    from prospect_agent.models import User

    empresas = _carregar_empresas(tenant_id)
    contatos = _contagem_de_contatos(tenant_id)
    with rx.session() as session:
        nomes = {u.email: u.name for u in session.query(User).all()}

    por_usuario: Dict[str, List[ProspectCompany]] = {}
    for e in empresas:
        por_usuario.setdefault(e.user_email or "", []).append(e)

    linhas = [
        {
            "usuario": nomes.get(email) or (email or "(não atribuído)"),
            "email": email,
            **_metricas(lista, contatos),
        }
        for email, lista in por_usuario.items()
    ]
    return sorted(linhas, key=lambda l: l["leads"], reverse=True)


def encontrar_empresa(tenant_id: int, nome_empresa: str) -> Optional[ProspectCompany]:
    """Acha UM lead pelo nome (fantasia ou razão social), tolerante a
    variações: tenta match exato do nome normalizado e, se não achar, um match
    parcial (o texto digitado contido no nome). Também aceita CNPJ, com ou sem
    pontuação — é como o usuário costuma identificar a empresa quando o nome
    tem grafias concorrentes.
    """
    from prospect_agent.services.normalizers import apenas_digitos_cnpj, normalizar_nome

    if not (nome_empresa or "").strip():
        return None

    with rx.session() as session:
        empresas = (
            session.query(ProspectCompany)
            .filter(ProspectCompany.tenant_id == tenant_id)
            .all()
        )

    digitos = apenas_digitos_cnpj(nome_empresa)
    if digitos and len(digitos) >= 8:
        achada = next((e for e in empresas if (e.cnpj or "").endswith(digitos) or digitos in (e.cnpj or "")), None)
        if achada:
            return achada

    alvo = normalizar_nome(nome_empresa)
    if not alvo:
        return None
    exata = next(
        (e for e in empresas
         if normalizar_nome(e.nome) == alvo or normalizar_nome(e.razao_social or "") == alvo),
        None,
    )
    if exata:
        return exata
    return next(
        (e for e in empresas
         if alvo in normalizar_nome(e.nome) or alvo in normalizar_nome(e.razao_social or "")),
        None,
    )


def _produtos_da_empresa(tenant_id: int, empresa: ProspectCompany) -> List[str]:
    return sorted(_nomes_produtos_por_search_run(tenant_id).get(empresa.search_run_id, set()))


def ficha_do_lead(tenant_id: int, empresa: ProspectCompany) -> Dict[str, Any]:
    """TUDO o que a base sabe sobre um lead, em um dicionário só: cadastro,
    canais de contato, situação no funil, priorização com os critérios, dicas
    de abordagem e contatos decisores.

    Os campos JSON (`priorizacao_criterios`, `approach_dicas`) são desserializados
    aqui — devolver a string crua obrigaria o modelo a fazer o parse, que é
    exatamente o tipo de tarefa em que ele erra em silêncio.
    """
    from prospect_agent.models import User

    with rx.session() as session:
        contatos = (
            session.query(CompanyContact)
            .filter(CompanyContact.company_id == empresa.id)
            .all()
        )
        coletor = (
            session.query(User).filter(User.email == empresa.user_email).first()
            if empresa.user_email else None
        )

    def _json(texto: str, padrao):
        try:
            return json.loads(texto) if texto else padrao
        except (ValueError, TypeError):
            return padrao

    return {
        "identificacao": {
            "nome": empresa.nome,
            "razao_social": empresa.razao_social,
            "cnpj": empresa.cnpj,
            "cidade": empresa.cidade,
            "estado": empresa.estado,
            "segmento": empresa.segmento or empresa.segmento_identificado,
            "porte": empresa.porte,
            "faturamento_estimado": empresa.faturamento_estimado,
            "faixa_faturamento": bucket_faturamento(empresa.faturamento_estimado),
            "idade_empresa_anos": empresa.idade_empresa_anos,
            "situacao_cadastral": empresa.status_cadastral,
            # Não é redundante com a situação cadastral: a Receita mantém como
            # "ATIVA" uma empresa em recuperação judicial.
            "alerta_situacao": empresa.alerta_situacao,
            "alerta_situacao_desde": empresa.alerta_situacao_desde,
        },
        "canais": {
            "website": empresa.website_principal or empresa.website,
            "telefone": empresa.telefone,
            # Nome longo de propósito: o campo é booleano ("aquele telefone
            # também atende no WhatsApp"), não um segundo número. Com o nome
            # curto anterior o modelo tinha como ler `true` como se fosse um
            # canal separado e prometer um WhatsApp que não existe.
            "telefone_tambem_e_whatsapp": empresa.telefone_whatsapp,
            "linkedin": empresa.linkedin_url,
        },
        "origem": {
            "produtos_da_pesquisa": _produtos_da_empresa(tenant_id, empresa),
            "coletado_por": (coletor.name if coletor else empresa.user_email) or "(não atribuído)",
            "encontrado_em": empresa.created_at.strftime("%d/%m/%Y") if empresa.created_at else None,
        },
        "match_icp": {
            "score": empresa.icp_score,
            "justificativa": empresa.justificativa_match,
        },
        "enriquecimento": {
            "status": empresa.enrichment_status,
            "percentual": empresa.enrichment_percentage,
            "concluido_em": empresa.enriched_at.strftime("%d/%m/%Y") if empresa.enriched_at else None,
        },
        "priorizacao": {
            "status": empresa.priorizacao_status,
            "score_final": empresa.priorizacao_score_final,
            "classe": empresa.priorizacao_classe,
            "criterios": _json(empresa.priorizacao_criterios, []),
        },
        "recomendacoes_de_abordagem": {
            "status": empresa.approach_status,
            "dicas": _json(empresa.approach_dicas, []),
        },
        "contatos_decisores": [
            {
                "nome": c.nome,
                "cargo": c.cargo,
                "senioridade": c.senioridade,
                "area": c.area,
                "origem": "Quadro societário" if c.origem == "qsa" else "LinkedIn",
                "perfil_url": c.perfil_url,
                "email": c.email,
                # 0-100. Vem junto do e-mail porque a Hunter devolve tanto
                # endereço confirmado quanto palpite de padrão do domínio, e
                # sem o score os dois são indistinguíveis.
                "email_confianca": c.email_confianca,
            }
            for c in contatos
        ],
    }


def buscar_empresas(
    tenant_id: int,
    produto: Optional[str] = None,
    email_usuario: Optional[str] = None,
    segmento: Optional[str] = None,
    estado: Optional[str] = None,
    porte: Optional[str] = None,
    classe_prioridade: Optional[str] = None,
    faixa_faturamento: Optional[str] = None,
    apenas_enriquecidos: bool = False,
    apenas_com_contato: bool = False,
) -> List[ProspectCompany]:
    """Leads que atendem a TODOS os filtros informados (os vazios são ignorados),
    já ordenados por potencial. Comparações de texto são frouxas de propósito
    (case-insensitive, por conteúdo): o usuário digita "metalurgia" para um
    segmento gravado como "Metalurgia e siderurgia"."""
    empresas = top_leads_por_potencial(tenant_id, limite=None, produto=produto)

    if email_usuario:
        empresas = [e for e in empresas if (e.user_email or "").lower() == email_usuario.lower()]
    if segmento:
        alvo = segmento.strip().lower()
        empresas = [e for e in empresas if alvo in ((e.segmento or e.segmento_identificado or "").lower())]
    if estado:
        alvo = estado.strip().upper()
        empresas = [e for e in empresas if (e.estado or "").upper() == alvo]
    if porte:
        alvo = porte.strip().lower()
        empresas = [e for e in empresas if (e.porte or "").lower() == alvo]
    if classe_prioridade:
        alvo = classe_prioridade.strip().lower()
        empresas = [e for e in empresas if (e.priorizacao_classe or "").lower() == alvo]
    if faixa_faturamento:
        alvo = faixa_faturamento.strip().lower()
        empresas = [e for e in empresas if alvo in bucket_faturamento(e.faturamento_estimado).lower()]
    if apenas_enriquecidos:
        empresas = [e for e in empresas if e.enrichment_status in STATUS_CONSIDERADOS_ENRIQUECIDOS]
    if apenas_com_contato:
        contatos = _contagem_de_contatos(tenant_id)
        empresas = [e for e in empresas if contatos.get(e.id, 0) > 0]
    return empresas


def resumo_de_contatos(
    tenant_id: int, produto: Optional[str] = None, email_usuario: Optional[str] = None,
) -> Dict[str, Any]:
    """Contagem de contatos decisores no recorte pedido, com a quebra por origem
    e por cargo.

    A quebra por origem não é detalhe técnico: contato de quadro societário é o
    decisor de fato, mas sem canal direto (aborda-se pelo telefone da empresa);
    contato de LinkedIn tem canal direto e costuma ser de senioridade menor.
    """
    empresas = buscar_empresas(tenant_id, produto=produto, email_usuario=email_usuario)
    ids = {e.id for e in empresas}
    with rx.session() as session:
        contatos = [
            c for c in session.query(CompanyContact)
            .filter(CompanyContact.tenant_id == tenant_id).all()
            if c.company_id in ids
        ]

    por_cargo: Dict[str, int] = {}
    for c in contatos:
        chave = (c.cargo or c.senioridade or "").strip() or "Não informado"
        por_cargo[chave] = por_cargo.get(chave, 0) + 1

    empresas_com_contato = len({c.company_id for c in contatos})
    return {
        "total_contatos": len(contatos),
        "leads_no_recorte": len(empresas),
        "leads_com_ao_menos_um_contato": empresas_com_contato,
        "leads_sem_nenhum_contato": len(empresas) - empresas_com_contato,
        "media_contatos_por_lead": round(len(contatos) / len(empresas), 1) if empresas else 0,
        # Cobertura de e-mail: separa "ainda não foi buscado" (cota do ciclo da
        # Hunter) de "buscado e não existe". Sem essa distinção, uma cobertura
        # baixa parece falha da plataforma quando na verdade é fila.
        "contatos_com_email": sum(1 for c in contatos if c.email),
        "contatos_sem_email_ja_buscados": sum(
            1 for c in contatos if not c.email and c.email_buscado_em is not None
        ),
        "contatos_com_email_ainda_nao_buscado": sum(
            1 for c in contatos if not c.email and c.email_buscado_em is None
        ),
        "por_origem": {
            "quadro_societario": sum(1 for c in contatos if c.origem == "qsa"),
            "linkedin": sum(1 for c in contatos if c.origem != "qsa"),
        },
        "por_cargo": sorted(
            [{"cargo": k, "quantidade": v} for k, v in por_cargo.items()],
            key=lambda d: d["quantidade"], reverse=True,
        )[:10],
    }


def canais_de_contato(
    tenant_id: int,
    produto: Optional[str] = None,
    email_usuario: Optional[str] = None,
    apenas_com_site: bool = False,
    apenas_com_linkedin: bool = False,
) -> List[Dict[str, Any]]:
    """Site, LinkedIn, telefone e WhatsApp de cada lead do recorte, já ordenados
    por potencial, mais o LinkedIn e o e-mail dos decisores da empresa.

    Existe separada de `ficha_do_lead` porque a pergunta "me passa o site e o
    LinkedIn desses leads" é sobre MUITAS empresas de uma vez: montar isso a
    partir da ficha exigiria uma chamada por empresa.

    `website_principal` é o site confirmado no enriquecimento; `website` é o
    que o agente de pesquisa achou. Devolvemos o confirmado quando existe, e
    ambos ficam disponíveis para o caso de divergirem.

    `telefone_tambem_e_whatsapp` é booleano: diz se o telefone acima atende no
    WhatsApp, não é um segundo número.
    """
    empresas = buscar_empresas(tenant_id, produto=produto, email_usuario=email_usuario)

    ids = {e.id for e in empresas}
    with rx.session() as session:
        contatos = [
            c for c in session.query(CompanyContact)
            .filter(CompanyContact.tenant_id == tenant_id).all()
            if c.company_id in ids
        ]
    decisores: Dict[int, List[Dict[str, Any]]] = {}
    for c in contatos:
        if not (c.perfil_url or c.email):
            continue
        decisores.setdefault(c.company_id, []).append({
            "nome": c.nome,
            "cargo": c.cargo,
            "perfil_url": c.perfil_url,
            "email": c.email,
            "email_confianca": c.email_confianca,
        })

    linhas = []
    for e in empresas:
        site = e.website_principal or e.website
        if apenas_com_site and not site:
            continue
        if apenas_com_linkedin and not e.linkedin_url:
            continue
        linhas.append({
            "empresa": e.razao_social or e.nome,
            "cidade_uf": f"{e.cidade or '?'}/{e.estado or '?'}",
            "website": site,
            "website_da_pesquisa": e.website,
            "linkedin_da_empresa": e.linkedin_url,
            "telefone": e.telefone,
            "telefone_tambem_e_whatsapp": e.telefone_whatsapp,
            "contatos_dos_decisores": decisores.get(e.id, []),
        })
    return linhas


def funil(tenant_id: int, produto: Optional[str] = None, email_usuario: Optional[str] = None) -> Dict[str, Any]:
    """Onde os leads do recorte estão no funil — quantos já foram enriquecidos,
    priorizados e receberam recomendação de abordagem, e quantos faltam em cada
    etapa. Responde "o que ainda falta processar?" sem o usuário abrir 3 telas."""
    empresas = buscar_empresas(tenant_id, produto=produto, email_usuario=email_usuario)
    total = len(empresas)
    enriquecidos = [e for e in empresas if e.enrichment_status in STATUS_CONSIDERADOS_ENRIQUECIDOS]
    priorizados = [e for e in empresas if e.priorizacao_status == "done"]
    com_approach = [e for e in empresas if e.approach_status == "done"]
    return {
        "total_de_leads": total,
        "enriquecidos": len(enriquecidos),
        "aguardando_enriquecimento": total - len(enriquecidos),
        "priorizados": len(priorizados),
        "aguardando_priorizacao": total - len(priorizados),
        "com_recomendacao_de_abordagem": len(com_approach),
        "falhas_de_enriquecimento": sum(1 for e in empresas if e.enrichment_status == "failed"),
        "falhas_de_priorizacao": sum(1 for e in empresas if e.priorizacao_status == "failed"),
    }


def top_leads_por_potencial(
    tenant_id: int, limite: Optional[int] = 10, produto: Optional[str] = None,
) -> List[ProspectCompany]:
    """Ordena por `priorizacao_score_final` (score de Prioridade, real e
    calculado) — a métrica correta de ranking, já que ela pondera fit com
    ICP, potencial financeiro, facilidade de contato etc., não só o "fit com
    ICP" que `icp_score` mede sozinho. Leads priorizados vêm sempre antes dos
    ainda não priorizados (nunca comparados na mesma régua — um `icp_score`
    alto não deve furar a fila de um lead com priorização real), e só dentro
    de cada grupo a ordenação é pelo respectivo score, do maior para o menor.
    `limite=None` retorna todos os leads. `produto` filtra só os leads da(s)
    pesquisa(s) que incluíram aquele produto (ver `_nomes_produtos_por_search_run`)."""
    empresas = _carregar_empresas(tenant_id, produto=produto)

    def chave_ordenacao(e: ProspectCompany):
        priorizado = e.priorizacao_score_final is not None
        score = e.priorizacao_score_final if priorizado else (e.icp_score or 0)
        return (priorizado, score)

    ordenados = sorted(empresas, key=chave_ordenacao, reverse=True)
    return ordenados if limite is None else ordenados[:limite]
