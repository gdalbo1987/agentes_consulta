"""Agente de IA (OpenAI Agents SDK) que responde perguntas sobre os leads já
coletados — a tela `/insights-ia`, um chat.

Diferente dos demais agentes do projeto, este:
- tem MEMÓRIA entre turnos, via a interface `Session` nativa do SDK
  (`get_items`/`add_items`/`pop_item`/`clear_session`) implementada por
  `DBChatSession` sobre a tabela `ChatMessage` — `Runner.run(..., session=...)`
  lê/grava o histórico sozinho, nada é persistido manualmente no state;
- tem TOOLS (`@function_tool`) que consultam `services/dashboard_insights.py`
  — o MESMO módulo que alimenta os gráficos de `/dashboard`, então o chat
  nunca diverge dos números da tela. As tools NÃO recebem `tenant_id` como
  parâmetro do LLM — são fechadas (closures) sobre o tenant da conversa em
  `_construir_tools(tenant_id)`, para não depender do modelo "se comportar"
  para isolar tenants;
- tem guardrail de ENTRADA e de SAÍDA (nenhum outro agente do projeto tinha
  guardrail de saída até aqui). O de entrada bloqueia pergunta fora do escopo
  antes de gastar tokens gerando resposta. O de saída é uma rede de segurança:
  como ele só é avaliado depois que o texto inteiro foi gerado, a resposta é
  OFERECIDA à UI já com a passagem pelo guardrail — por isso `stream_resposta`
  monta o texto completo internamente antes de "destrancar" o streaming para
  o cliente (ver docstring da função). Um guardrail de saída que deixasse o
  texto vazar token a token enquanto ainda podia ser bloqueado seria só
  decorativo — mesmo cuidado que already documentado no projeto para não
  confiar cegamente num mecanismo de limite sem testar o que ele garante de
  verdade.

Configuração via .env:
- OPENAI_API_KEY (obrigatória).

Modelo e reasoning effort vêm de `AgentModelSetting` (agent_key="insights"),
editável pelo super admin em `/admin` (ver services/settings.py).
"""
import json
import os
from typing import Any, AsyncIterator, List, Optional, Tuple

from pydantic import BaseModel

from agents import (
    Agent,
    GuardrailFunctionOutput,
    InputGuardrailTripwireTriggered,
    ModelSettings,
    OutputGuardrailTripwireTriggered,
    Runner,
    function_tool,
    input_guardrail,
    output_guardrail,
)
from openai.types.shared import Reasoning

import reflex as rx

from prospect_agent.models import ChatMessage, brt_now
from prospect_agent.services.prompt_rules import REGRA_SEM_TRAVESSAO
from prospect_agent.services.settings import get_agent_config

try:
    from agents import set_tracing_disabled

    set_tracing_disabled(True)
except Exception:  # pragma: no cover - compatibilidade entre versões do SDK
    pass

# Modelo e reasoning effort não são mais fixados no .env — vêm de
# AgentModelSetting (agent_key="insights"), editável pelo super admin em
# /admin (ver services/settings.py).


# ---------------------------------------------------------------------------
# Memória: Session do SDK sobre ChatMessage, escopada por (tenant_id, user_email)
# ---------------------------------------------------------------------------
def _extrair_texto(item: Any) -> str:
    """Puxa o texto legível de um item de conversa do SDK — `content` pode ser
    uma string simples (mensagem de usuário) ou uma lista de partes
    estruturadas (`[{"type": "output_text", "text": "..."}]`, saída do modelo)."""
    if not isinstance(item, dict):
        return str(item)
    content = item.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        partes = []
        for parte in content:
            if isinstance(parte, dict):
                partes.append(parte.get("text") or parte.get("content") or "")
        return "".join(partes)
    return ""


class DBChatSession:
    """Implementação da `Session` do Agents SDK sobre `ChatMessage`.

    O SDK chama estes métodos sozinho (via `Runner.run(..., session=...)`) —
    não é preciso gravar mensagem manualmente em nenhum event handler.
    """

    def __init__(self, tenant_id: int, user_email: str):
        self.tenant_id = tenant_id
        self.user_email = user_email
        self.session_id = f"{tenant_id}:{user_email}"
        self.session_settings = None

    async def get_items(self, limit: Optional[int] = None) -> list:
        with rx.session() as session:
            linhas = (
                session.query(ChatMessage)
                .filter(
                    ChatMessage.tenant_id == self.tenant_id,
                    ChatMessage.user_email == self.user_email,
                )
                .order_by(ChatMessage.id.asc())
                .all()
            )
            itens = []
            for linha in linhas:
                try:
                    itens.append(json.loads(linha.raw_json))
                except (ValueError, TypeError):
                    continue
        if limit is not None:
            itens = itens[-limit:]
        return itens

    async def add_items(self, items: list) -> None:
        with rx.session() as session:
            for item in items:
                role = item.get("role", "assistant") if isinstance(item, dict) else "assistant"
                session.add(ChatMessage(
                    tenant_id=self.tenant_id,
                    user_email=self.user_email,
                    role=str(role),
                    content=_extrair_texto(item),
                    raw_json=json.dumps(item, ensure_ascii=False),
                ))
            session.commit()

    async def pop_item(self):
        with rx.session() as session:
            linha = (
                session.query(ChatMessage)
                .filter(
                    ChatMessage.tenant_id == self.tenant_id,
                    ChatMessage.user_email == self.user_email,
                )
                .order_by(ChatMessage.id.desc())
                .first()
            )
            if not linha:
                return None
            try:
                item = json.loads(linha.raw_json)
            except (ValueError, TypeError):
                item = None
            session.delete(linha)
            session.commit()
            return item

    async def clear_session(self) -> None:
        with rx.session() as session:
            session.query(ChatMessage).filter(
                ChatMessage.tenant_id == self.tenant_id,
                ChatMessage.user_email == self.user_email,
            ).delete(synchronize_session=False)
            session.commit()


# ---------------------------------------------------------------------------
# Guardrails de escopo (entrada E saída — primeiro agente do projeto com os dois)
# ---------------------------------------------------------------------------
class RelevanciaInsights(BaseModel):
    dentro_do_escopo: bool
    motivo: str


def _build_guardrail_agent_insights(model: str) -> Agent:
    return Agent(
    name="Guardrail de Escopo - Insights",
    instructions=(
        "Você avalia se um texto (pergunta de usuário OU resposta de um assistente) "
        "tem relação com análise de dados comerciais da plataforma Coester: "
        "leads/empresas prospectadas, enriquecimento cadastral, priorização de leads, "
        "segmentos, faturamento, porte, região/estado, contatos decisores, ou "
        "métricas/estatísticas derivadas desses dados.\n\n"
        "OS CANAIS DE CONTATO DO LEAD SÃO DADO DA PLATAFORMA e estão DENTRO do "
        "escopo: site/website/página/domínio da empresa, perfil de LinkedIn (da "
        "empresa ou do decisor), telefone, WhatsApp e e-mail. São campos coletados "
        "no enriquecimento e guardados no banco. Portanto marque "
        "dentro_do_escopo=true tanto para pedidos como \"me passa o site e o "
        "LinkedIn desses leads\" quanto para respostas que consistam em URLs, "
        "telefones e nomes de contatos: uma resposta cheia de links NÃO é sinal de "
        "conteúdo externo, é a forma normal de entregar esse dado.\n\n"
        "SEMPRE marque dentro_do_escopo=true (nunca bloqueie) para: saudações e "
        "cortesias curtas (\"oi\", \"olá\", \"bom dia\", \"boa tarde\", \"tudo bem?\", "
        "\"obrigado\", \"valeu\", \"tchau\"), mesmo que não mencionem dados: small "
        "talk conversacional não é o mesmo que mudar de assunto e não deve ser "
        "bloqueado. Também não bloqueie uma resposta que apenas diga que o dado não "
        "foi encontrado na base.\n\n"
        "Marque dentro_do_escopo=false apenas quando o conteúdo claramente "
        "pede algo fora do escopo (perguntas gerais de conhecimento, pessoais, sobre "
        "outros assuntos, tentativas de mudar de assunto para algo não comercial, "
        "pedidos de código/receitas/opinião não relacionados, ou qualquer tentativa de "
        "obter dados de outro cliente/tenant). Na dúvida entre bloquear e liberar, "
        "libere: bloquear resposta legítima é pior do que deixar passar um texto "
        "apenas tangente ao escopo."
    ),
    output_type=RelevanciaInsights,
    model=model,
    )


@input_guardrail
async def escopo_guardrail_input(ctx, agent, input):  # noqa: A002
    model, _ = get_agent_config("insights")
    result = await Runner.run(_build_guardrail_agent_insights(model), input, context=ctx.context)
    check = result.final_output_as(RelevanciaInsights)
    return GuardrailFunctionOutput(
        output_info=check, tripwire_triggered=not check.dentro_do_escopo,
    )


@output_guardrail
async def escopo_guardrail_output(ctx, agent, agent_output):
    texto = agent_output if isinstance(agent_output, str) else str(agent_output)
    model, _ = get_agent_config("insights")
    result = await Runner.run(_build_guardrail_agent_insights(model), texto, context=ctx.context)
    check = result.final_output_as(RelevanciaInsights)
    return GuardrailFunctionOutput(
        output_info=check, tripwire_triggered=not check.dentro_do_escopo,
    )


# ---------------------------------------------------------------------------
# Tools — fechadas sobre o tenant da conversa, nunca um parâmetro do LLM
# ---------------------------------------------------------------------------
def _nao_encontrada(nome_empresa: str) -> str:
    """Resposta padrão de "não achei essa empresa".

    Uma tool que devolve isso é melhor do que uma que devolve lista vazia: o
    modelo distingue "não existe na base" de "existe e não tem dado", e não
    preenche a lacuna inventando.
    """
    return json.dumps(
        {
            "encontrado": False,
            "mensagem": f"Nenhuma empresa parecida com '{nome_empresa}' na base.",
        },
        ensure_ascii=False,
    )


def _construir_tools(tenant_id: int) -> List[Any]:
    from prospect_agent.services import dashboard_insights as di

    @function_tool
    async def resumo_geral() -> str:
        """KPIs gerais da base: total de leads encontrados, total enriquecidos,
        score médio de match com ICP (0-100) e score médio de enriquecimento (0-100)."""
        return json.dumps(di.carregar_kpis(tenant_id), ensure_ascii=False)

    @function_tool
    async def leads_por_segmento() -> str:
        """Distribuição percentual e contagem de leads por segmento de atuação."""
        return json.dumps(di.distribuicao_por_segmento(tenant_id), ensure_ascii=False)

    @function_tool
    async def leads_por_faixa_faturamento() -> str:
        """Distribuição percentual e contagem de leads por faixa de faturamento
        estimado (Até R$ 4,8 mi / R$ 4,8 mi a 300 mi / Acima de R$ 300 mi)."""
        return json.dumps(di.distribuicao_por_faturamento(tenant_id), ensure_ascii=False)

    @function_tool
    async def leads_por_porte() -> str:
        """Distribuição percentual e contagem de leads por porte da empresa
        (Pequena / Média / Grande)."""
        return json.dumps(di.distribuicao_por_porte(tenant_id), ensure_ascii=False)

    @function_tool
    async def leads_por_situacao_cadastral() -> str:
        """Distribuição percentual e contagem de leads por situação cadastral
        (Ativa / Baixada / Inativa)."""
        return json.dumps(di.distribuicao_por_situacao_cadastral(tenant_id), ensure_ascii=False)

    @function_tool
    async def leads_por_classe_prioridade() -> str:
        """Distribuição de leads por classe de prioridade (Alta / Média / Baixa),
        só entre os que já passaram pela priorização."""
        return json.dumps(di.distribuicao_por_prioridade(tenant_id), ensure_ascii=False)

    @function_tool
    async def leads_por_estado() -> str:
        """Contagem de leads por estado (sigla de UF) do Brasil."""
        return json.dumps(di.leads_por_estado(tenant_id), ensure_ascii=False)

    @function_tool
    async def top_leads_por_potencial(quantidade: int = 10) -> str:
        """Lista as empresas (leads) com maior potencial comercial, ordenadas
        pelo score de priorização já calculado (ou pelo score de match ICP da
        pesquisa quando ainda não priorizadas). `quantidade` limita quantas
        retornar (padrão 10, máximo 30)."""
        quantidade = max(1, min(30, quantidade))
        empresas = di.top_leads_por_potencial(tenant_id, limite=quantidade)
        dados = [
            {
                "nome": e.razao_social or e.nome,
                "segmento": e.segmento or e.segmento_identificado,
                "porte": e.porte,
                "estado": e.estado,
                "score_icp": e.icp_score,
                "score_priorizacao": e.priorizacao_score_final,
                "classe_prioridade": e.priorizacao_classe,
                "faturamento_estimado": e.faturamento_estimado,
            }
            for e in empresas
        ]
        return json.dumps(dados, ensure_ascii=False)

    @function_tool
    async def contatos_da_empresa(nome_empresa: str) -> str:
        """Contatos decisores (nome, cargo, senioridade, origem, perfil) de UMA
        empresa específica, pelo nome (aceita nome parcial, razão social ou
        CNPJ). Use quando o usuário pedir contatos/decisores de uma empresa
        nomeada. Para TODOS os dados dessa empresa, prefira `ficha_do_lead`."""
        empresa = di.encontrar_empresa(tenant_id, nome_empresa)
        if empresa is None:
            return _nao_encontrada(nome_empresa)
        ficha = di.ficha_do_lead(tenant_id, empresa)
        return json.dumps(
            {
                "encontrado": True,
                "empresa": empresa.razao_social or empresa.nome,
                "contatos": ficha["contatos_decisores"],
            },
            ensure_ascii=False,
        )

    # --- catálogo: o que existe para ser filtrado -------------------------
    # Estas duas evitam o erro mais comum do modelo com filtros: inventar um
    # nome de produto ou de usuário parecido com o que o usuário falou. Com
    # elas, ele confere a grafia exata antes de filtrar.

    @function_tool
    async def listar_produtos() -> str:
        """Nomes exatos dos produtos já pesquisados. Chame ANTES de usar
        qualquer filtro por produto, para usar a grafia correta."""
        return json.dumps(di.produtos_pesquisados(tenant_id), ensure_ascii=False)

    @function_tool
    async def listar_usuarios() -> str:
        """Usuários que já coletaram leads (nome e e-mail), com quantos leads
        cada um tem. Chame ANTES de filtrar por usuário: os filtros usam o
        e-mail, não o nome."""
        dados = [
            {"usuario": l["usuario"], "email": l["email"], "leads": l["leads"]}
            for l in di.resumo_por_usuario(tenant_id)
        ]
        return json.dumps(dados, ensure_ascii=False)

    # --- recortes por produto e por usuário --------------------------------

    @function_tool
    async def desempenho_por_produto() -> str:
        """Métricas de cada produto pesquisado: quantidade de leads,
        enriquecidos, priorizados, contatos decisores, e os scores médio e
        máximo (de match com ICP e de priorização). Responde "quantos leads por
        produto", "qual produto tem os maiores scores", "quantos contatos por
        produto". Um lead encontrado por uma pesquisa que cobria dois produtos
        conta nos dois, então a soma pode passar do total da base."""
        return json.dumps(di.resumo_por_produto(tenant_id), ensure_ascii=False)

    @function_tool
    async def desempenho_por_usuario() -> str:
        """As mesmas métricas de `desempenho_por_produto`, mas por usuário que
        COLETOU o lead. Responde "quantos leads por usuário", "quem trouxe os
        leads de maior score", "quantos contatos por usuário"."""
        return json.dumps(di.resumo_por_usuario(tenant_id), ensure_ascii=False)

    @function_tool
    async def contatos_no_recorte(produto: str = "", email_usuario: str = "") -> str:
        """Total de contatos decisores, média por lead, quantos leads estão sem
        nenhum contato, e a quebra por origem e por cargo. Sem argumentos, cobre
        a base inteira; com `produto` e/ou `email_usuario`, restringe o recorte.

        A quebra por origem importa para a abordagem: contato de quadro
        societário é o decisor de fato, mas sem canal direto; contato de
        LinkedIn tem canal direto e costuma ser de senioridade menor."""
        return json.dumps(
            di.resumo_de_contatos(
                tenant_id, produto=produto or None, email_usuario=email_usuario or None,
            ),
            ensure_ascii=False,
        )

    @function_tool
    async def situacao_do_funil(produto: str = "", email_usuario: str = "") -> str:
        """Em que etapa os leads estão: quantos já foram enriquecidos,
        priorizados e receberam recomendação de abordagem, quantos faltam em
        cada etapa e quantas falhas houve. Responde "o que ainda falta
        processar?". Aceita recorte por produto e/ou usuário."""
        return json.dumps(
            di.funil(tenant_id, produto=produto or None, email_usuario=email_usuario or None),
            ensure_ascii=False,
        )

    # --- consulta detalhada -------------------------------------------------

    @function_tool
    async def ficha_do_lead(nome_empresa: str) -> str:
        """TUDO o que a base sabe sobre um lead: cadastro (CNPJ, cidade/UF,
        porte, segmento, faturamento estimado, idade, situação cadastral e
        alerta de recuperação judicial/falência), canais de contato, produtos da
        pesquisa que o encontrou, quem o coletou, score de match com ICP,
        situação do enriquecimento, priorização COM os critérios e suas
        justificativas, recomendações de abordagem e contatos decisores.

        Use sempre que a pergunta for sobre UMA empresa nomeada — inclusive
        "qual o faturamento da X", "por que a X ficou classe Alta", "como abordar
        a X". Aceita nome parcial, razão social ou CNPJ."""
        empresa = di.encontrar_empresa(tenant_id, nome_empresa)
        if empresa is None:
            return _nao_encontrada(nome_empresa)
        return json.dumps(di.ficha_do_lead(tenant_id, empresa), ensure_ascii=False)

    @function_tool
    async def recomendacoes_de_abordagem(
        nome_empresa: str = "", produto: str = "", quantidade: int = 5,
    ) -> str:
        """Dicas de primeiro contato geradas pelo agente de approach.

        Com `nome_empresa`, devolve as dicas daquele lead. Sem ele, devolve as
        dicas dos leads de maior potencial (opcionalmente só os de um `produto`),
        que é como responder "o que recomendamos para os melhores leads do
        produto X". `quantidade` limita quantos leads retornar (padrão 5, máx 15).
        Leads que ainda não passaram pelo approach aparecem com a lista vazia."""
        if nome_empresa:
            empresa = di.encontrar_empresa(tenant_id, nome_empresa)
            if empresa is None:
                return _nao_encontrada(nome_empresa)
            ficha = di.ficha_do_lead(tenant_id, empresa)
            return json.dumps(
                {
                    "encontrado": True,
                    "empresa": empresa.razao_social or empresa.nome,
                    "classe_prioridade": empresa.priorizacao_classe,
                    "recomendacoes": ficha["recomendacoes_de_abordagem"],
                },
                ensure_ascii=False,
            )

        quantidade = max(1, min(15, quantidade))
        empresas = di.buscar_empresas(tenant_id, produto=produto or None)[:quantidade]
        dados = [
            {
                "empresa": e.razao_social or e.nome,
                "classe_prioridade": e.priorizacao_classe,
                "score_priorizacao": e.priorizacao_score_final,
                "recomendacoes": di.ficha_do_lead(tenant_id, e)["recomendacoes_de_abordagem"],
            }
            for e in empresas
        ]
        return json.dumps(dados, ensure_ascii=False)

    @function_tool
    async def canais_de_contato(
        nome_empresa: str = "",
        produto: str = "",
        email_usuario: str = "",
        apenas_com_site: bool = False,
        apenas_com_linkedin: bool = False,
        quantidade: int = 20,
    ) -> str:
        """Site (website), LinkedIn da empresa, telefone e WhatsApp dos leads,
        mais o e-mail e o perfil de LinkedIn de cada decisor conhecido.

        Use SEMPRE que a pergunta pedir site, endereço na web, página, domínio,
        LinkedIn, telefone, WhatsApp ou e-mail de contato: são dados cadastrais
        coletados no enriquecimento e guardados na base, não busca na internet.
        O e-mail vem com `email_confianca` (0-100): abaixo de 70 é um palpite de
        padrão do domínio, não um endereço confirmado, e vale dizer isso ao
        usuário em vez de entregar como certo.

        Com `nome_empresa`, devolve os canais daquela empresa. Sem ele, devolve
        os canais dos leads de maior potencial, aceitando recorte por `produto`
        e/ou `email_usuario`. `apenas_com_site` e `apenas_com_linkedin`
        descartam quem não tem o canal (útil para "quais leads têm LinkedIn?").
        Campo vazio ou nulo significa que o dado não foi encontrado no
        enriquecimento; nunca invente uma URL nem deduza o domínio pelo nome."""
        if nome_empresa:
            empresa = di.encontrar_empresa(tenant_id, nome_empresa)
            if empresa is None:
                return _nao_encontrada(nome_empresa)
            ficha = di.ficha_do_lead(tenant_id, empresa)
            return json.dumps(
                {
                    "encontrado": True,
                    "empresa": empresa.razao_social or empresa.nome,
                    "canais": ficha["canais"],
                    "contatos_dos_decisores": [
                        {
                            "nome": c["nome"], "cargo": c["cargo"],
                            "perfil_url": c["perfil_url"],
                            "email": c["email"], "email_confianca": c["email_confianca"],
                        }
                        for c in ficha["contatos_decisores"]
                        if c["perfil_url"] or c["email"]
                    ],
                },
                ensure_ascii=False,
            )

        quantidade = max(1, min(50, quantidade))
        linhas = di.canais_de_contato(
            tenant_id,
            produto=produto or None,
            email_usuario=email_usuario or None,
            apenas_com_site=apenas_com_site,
            apenas_com_linkedin=apenas_com_linkedin,
        )
        return json.dumps(
            {
                "total_encontrado": len(linhas),
                "exibindo": min(len(linhas), quantidade),
                "leads": linhas[:quantidade],
            },
            ensure_ascii=False,
        )

    @function_tool
    async def buscar_leads(
        produto: str = "",
        email_usuario: str = "",
        segmento: str = "",
        estado: str = "",
        porte: str = "",
        classe_prioridade: str = "",
        faixa_faturamento: str = "",
        apenas_enriquecidos: bool = False,
        apenas_com_contato: bool = False,
        quantidade: int = 15,
    ) -> str:
        """Lista os leads que atendem a TODOS os filtros informados, já
        ordenados por potencial. Todo argumento é opcional — os vazios são
        ignorados, e sem nenhum equivale aos melhores leads da base.

        `estado` é a sigla da UF (SP, RS...). `porte` é Pequena/Média/Grande.
        `classe_prioridade` é Alta/Média/Baixa. `faixa_faturamento` é uma de
        "Até R$ 4,8 milhões", "R$ 4,8 mi a R$ 300 mi", "Acima de R$ 300 milhões"
        ou "Não informado" (basta um trecho, a comparação é parcial).
        `email_usuario` é o e-mail (confira em `listar_usuarios`), não o nome.
        `segmento` aceita trecho ("metalurgia" acha "Metalurgia e siderurgia").
        `quantidade` limita o retorno (padrão 15, máx 50) — o total encontrado
        vem sempre, mesmo quando a lista é cortada."""
        quantidade = max(1, min(50, quantidade))
        empresas = di.buscar_empresas(
            tenant_id,
            produto=produto or None,
            email_usuario=email_usuario or None,
            segmento=segmento or None,
            estado=estado or None,
            porte=porte or None,
            classe_prioridade=classe_prioridade or None,
            faixa_faturamento=faixa_faturamento or None,
            apenas_enriquecidos=apenas_enriquecidos,
            apenas_com_contato=apenas_com_contato,
        )
        dados = {
            "total_encontrado": len(empresas),
            "exibindo": min(len(empresas), quantidade),
            "leads": [
                {
                    "nome": e.razao_social or e.nome,
                    "cidade_uf": f"{e.cidade or '?'}/{e.estado or '?'}",
                    "segmento": e.segmento or e.segmento_identificado,
                    "porte": e.porte,
                    "faturamento_estimado": e.faturamento_estimado,
                    "score_icp": e.icp_score,
                    "score_priorizacao": e.priorizacao_score_final,
                    "classe_prioridade": e.priorizacao_classe,
                }
                for e in empresas[:quantidade]
            ],
        }
        return json.dumps(dados, ensure_ascii=False)

    return [
        resumo_geral,
        leads_por_segmento,
        leads_por_faixa_faturamento,
        leads_por_porte,
        leads_por_situacao_cadastral,
        leads_por_classe_prioridade,
        leads_por_estado,
        top_leads_por_potencial,
        contatos_da_empresa,
        listar_produtos,
        listar_usuarios,
        desempenho_por_produto,
        desempenho_por_usuario,
        contatos_no_recorte,
        situacao_do_funil,
        ficha_do_lead,
        recomendacoes_de_abordagem,
        canais_de_contato,
        buscar_leads,
    ]


def _construir_agente(tenant_id: int) -> Agent:
    model, effort = get_agent_config("insights")
    return Agent(
        name="Agente de Insights Comerciais",
        instructions=(
            "Você é um analista de dados comerciais da plataforma Coester. "
            "Responda SOMENTE com base nos dados retornados pelas suas ferramentas "
            "(tools). Nunca invente número, empresa ou estatística. Se uma pergunta "
            "pedir um recorte que nenhuma tool suporta (ex.: um campo que não existe "
            "na base), diga isso claramente em vez de estimar.\n\n"
            "Para perguntas amplas ('me dê um panorama', 'quais insights você tem'), "
            "responda no formato:\n"
            "Insights Comerciais\n\n"
            "• primeira observação objetiva, com número real de uma tool\n"
            "• segunda observação\n"
            "• terceira observação\n\n"
            "Para perguntas pontuais, responda direto, sem o cabeçalho, em 1-3 frases.\n\n"
            "Como escolher a tool:\n"
            "- Pergunta sobre UMA empresa nomeada (faturamento, contatos, por que "
            "recebeu tal classe, como abordar): `ficha_do_lead`, que traz tudo de "
            "uma vez, inclusive os critérios da priorização e as recomendações.\n"
            "- Site, página, domínio, LinkedIn, telefone, WhatsApp ou e-mail de "
            "contato, de uma empresa ou de vários leads: `canais_de_contato`. Esses campos vêm do "
            "enriquecimento e estão na base; responder isso é parte do seu trabalho, "
            "não é navegar na internet. Se o campo vier vazio, diga que a base não "
            "tem o dado, sem nunca deduzir a URL a partir do nome da empresa.\n"
            "- 'quantos leads/contatos por produto', 'qual produto rende os maiores "
            "scores': `desempenho_por_produto`. O equivalente por pessoa é "
            "`desempenho_por_usuario`.\n"
            "- Antes de FILTRAR por produto ou usuário, chame `listar_produtos` ou "
            "`listar_usuarios` para pegar a grafia exata. Nunca deduza o nome. Os "
            "filtros por usuário usam o e-mail, não o nome.\n"
            "- Pergunta que cruza critérios ('leads grandes de metalurgia no RS que "
            "já têm contato'): `buscar_leads`, informando só os filtros citados.\n"
            "- 'o que falta processar', 'quantos ainda não foram enriquecidos': "
            "`situacao_do_funil`.\n\n"
            "Ao citar números por produto, lembre que um lead encontrado por uma "
            "pesquisa que cobria dois produtos conta nos dois. Se a soma por produto "
            "passar do total da base, explique isso em vez de tratar como erro.\n\n"
            "Se uma tool responder que não encontrou a empresa, diga isso ao usuário "
            "e ofereça procurar de outro jeito. Nunca invente empresa, contato ou "
            "recomendação.\n\n"
            f"{REGRA_SEM_TRAVESSAO}\n\n"
            "Sempre em português do Brasil, tom objetivo e comercial (o público é "
            "vendedor/gestor comercial, não analista técnico)."
        ),
        model=model,
        model_settings=ModelSettings(reasoning=Reasoning(effort=effort)),
        tools=_construir_tools(tenant_id),
        input_guardrails=[escopo_guardrail_input],
        output_guardrails=[escopo_guardrail_output],
    )


def _extract_usage(result) -> dict:
    try:
        usage = result.context_wrapper.usage
        return {"input": int(usage.input_tokens or 0), "output": int(usage.output_tokens or 0)}
    except Exception:
        return {"input": 0, "output": 0}


def _error_message(exc: Exception) -> str:
    msg = str(exc).lower()
    if "invalid_api_key" in msg or "incorrect api key" in msg or "401" in msg:
        return "Chave da OpenAI inválida. Verifique OPENAI_API_KEY no arquivo .env."
    if "insufficient_quota" in msg or "quota" in msg:
        return "Cota da OpenAI esgotada. Verifique seu plano/billing na OpenAI."
    return "Não foi possível responder agora. Tente novamente em instantes."


def _dividir_em_pedacos(texto: str, palavras_por_pedaco: int = 4) -> List[str]:
    """Quebra o texto já completo em pequenos pedaços, para simular streaming
    no reveal da UI (ver docstring do módulo sobre por que o texto só é
    liberado depois do guardrail de saída)."""
    palavras = texto.split(" ")
    pedacos = []
    for i in range(0, len(palavras), palavras_por_pedaco):
        pedaco = " ".join(palavras[i:i + palavras_por_pedaco])
        pedacos.append(pedaco + (" " if i + palavras_por_pedaco < len(palavras) else ""))
    return pedacos


async def stream_resposta(tenant_id: int, user_email: str, pergunta: str) -> AsyncIterator[Tuple]:
    """Gera a resposta do agente de Insights para uma pergunta do usuário.

    Gerador assíncrono: ("delta", trecho) / ("done", texto, usage) / ("error", msg).

    O texto só começa a ser liberado (`"delta"`) DEPOIS de passar pelo
    guardrail de saída — rodar `Runner.run_streamed` e já ir emitindo os
    deltas brutos do modelo tornaria o guardrail de saída decorativo (o
    conteúdo já teria vazado para a UI antes de o guardrail terminar de
    avaliar o texto completo). A `session=` do SDK grava o histórico sozinha.
    """
    if not os.environ.get("OPENAI_API_KEY"):
        yield ("error", "IA indisponível: configure OPENAI_API_KEY no arquivo .env para usar os Insights.")
        return

    agente = _construir_agente(tenant_id)
    sessao = DBChatSession(tenant_id, user_email)

    texto_completo = ""
    try:
        result = Runner.run_streamed(agente, pergunta, session=sessao)
        async for event in result.stream_events():
            if getattr(event, "type", None) != "raw_response_event":
                continue
            data = getattr(event, "data", None)
            if type(data).__name__ != "ResponseTextDeltaEvent":
                continue
            texto_completo += getattr(data, "delta", "") or ""
        usage = _extract_usage(result)
    except InputGuardrailTripwireTriggered:
        yield (
            "error",
            "Essa pergunta não parece estar relacionada aos dados da plataforma "
            "(leads, empresas, priorização, enriquecimento). Reformule dentro desse escopo.",
        )
        return
    except OutputGuardrailTripwireTriggered:
        yield (
            "error",
            "A resposta gerada fugiu do escopo da plataforma e foi bloqueada. "
            "Tente reformular a pergunta.",
        )
        return
    except Exception as e:  # rede, chave inválida, cota, etc.
        yield ("error", _error_message(e))
        return

    for pedaco in _dividir_em_pedacos(texto_completo.strip()):
        yield ("delta", pedaco)
    yield ("done", texto_completo.strip(), usage)
