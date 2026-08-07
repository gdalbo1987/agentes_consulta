"""Agente de IA (OpenAI Agents SDK) que executa prospecção automática de leads.

A partir de um ou mais produtos cadastrados (nome + descrição), uma região de
interesse e um segmento estratégico, o processo devolve:
  - no mínimo 30 empresas com potencial match de ICP (Ideal Customer Profile);
  - para CADA empresa encontrada, até 5 notícias recentes (últimos 6 meses)
    específicas daquela empresa, usadas como gatilho de abordagem comercial.

Executado em DUAS FASES (dois agentes, chamadas separadas):
  1. `company_finder_agent` — só encontra e qualifica as empresas (resposta
     menor e mais rápida de sintetizar).
  2. `news_batch_agent` — roda uma vez por LOTE de empresas (ver
     `EMPRESAS_POR_LOTE_NOTICIAS`), buscando as notícias específicas de cada
     empresa do lote.
Isso existe porque uma única chamada pedindo tudo de uma vez (~30 empresas +
até 5 notícias cada) gera uma resposta final enorme, e em teste real a
conexão de streaming caiu antes da síntese terminar ("peer closed
connection... incomplete chunked read"). Lotes pequenos mantêm cada resposta
individual pequena, reduzindo esse risco — e uma falha de rede num lote de
notícias não derruba a pesquisa inteira, só deixa aquelas empresas sem
notícia dedicada (best-effort, reportado em erros_ou_avisos).

A saída é JSON estruturado (ver `CONTRATO_PESQUISA.md` na raiz do projeto),
consumido por uma etapa seguinte do pipeline — não há apresentação amigável
aqui, apenas dados.

Modelo e reasoning effort não são mais fixados no .env — vêm de
`AgentModelSetting` (agent_key="prospect"), editável pelo super admin em
`/admin` (ver services/settings.py). Por isso `company_finder_agent` e
`news_batch_agent` deixaram de ser singletons de módulo e passaram a ser
construídos a cada chamada de `stream_prospect_search`.

Configuração via .env:
- OPENAI_API_KEY: chave da OpenAI (obrigatória).
"""
import os
import re
import time
import uuid
from datetime import date, datetime, timedelta
from typing import AsyncIterator, List, Optional, Tuple

from pydantic import BaseModel

from agents import Agent, ModelSettings, Runner, WebSearchTool
from openai.types.shared import Reasoning

from prospect_agent.models import brt_now
from prospect_agent.services.prompt_rules import REGRA_SEM_TRAVESSAO
from prospect_agent.services.settings import get_agent_config

try:  # Evita enviar traces para a OpenAI sem necessidade (mesmo padrão do product_agent).
    from agents import set_tracing_disabled

    set_tracing_disabled(True)
except Exception:  # pragma: no cover - compatibilidade entre versões do SDK
    pass

# ==========================================================================
# Orçamentos e configuração. AJUSTE AQUI se precisar de mais/menos
# profundidade — cada chamada extra de busca aumenta custo e tempo.
# Constantes MAX_TOOL_CALLS_* são orçamentos INSTRUÍDOS NO PROMPT (soft
# budget), não um corte rígido do SDK: `web_search` é uma tool HOSPEDADA
# (executada pela OpenAI), e o modelo pode disparar várias buscas dentro de
# uma única resposta — testado empiricamente que `result.cancel(mode=
# "after_turn")` não impede novas buscas de forma confiável nesse cenário. O
# corte real e garantido é o teto de tempo (`MAX_SEARCH_SECONDS_*` abaixo).
# ==========================================================================

# --- Fase 1: busca de empresas ---
MAX_TOOL_CALLS_EMPRESAS = 10
META_EMPRESAS = 30
# Em teste real (26 buscas, RS/indústria metalúrgica, só empresas, sem
# notícias por empresa) levou ~597s para concluir com sucesso; o teto abaixo
# tem folga acima disso.
MAX_SEARCH_SECONDS_EMPRESAS = 720

# --- Fase 2: notícias por empresa, em lotes ---
EMPRESAS_POR_LOTE_NOTICIAS = 5  # empresas por chamada de notícias (não é o total).
NOTICIAS_POR_EMPRESA = 5
MAX_TOOL_CALLS_POR_LOTE_NOTICIAS = 10
MAX_SEARCH_SECONDS_POR_LOTE_NOTICIAS = 300  # 5 min por lote (resposta pequena)

# Quantas empresas já conhecidas cabem na instrução de exclusão do prompt. O
# corte existe para uma base grande não empurrar o prompt (e o custo de entrada)
# para cima sem retorno: a lista serve para o modelo não repetir o óbvio, e as
# repetições que escaparem são descartadas depois, em Python.
LIMITE_CONHECIDAS_NO_PROMPT = 300

JANELA_NOTICIAS_DIAS = 180  # 6 meses
CONTEXT_SIZE = "low"  # "low" = menos contexto de busca, mais rápido, menos custo
# Reasoning effort não é mais fixo aqui — vem de AgentModelSetting (agent_key="prospect").


# ==========================================================================
# Schemas (contrato de dados — ver CONTRATO_PESQUISA.md)
# ==========================================================================
class Noticia(BaseModel):
    """Notícia recente e específica de uma empresa, usada como gatilho de
    abordagem comercial (não é uma notícia genérica do segmento/região)."""

    titulo: str
    data_publicacao: str  # YYYY-MM-DD
    resumo: str
    url: str
    relevancia: str


class EmpresaICP(BaseModel):
    """Uma empresa potencial cliente, com o resultado da qualificação de ICP
    e até NOTICIAS_POR_EMPRESA notícias recentes específicas dela (a lista de
    notícias é preenchida na fase 2, não pela fase 1 de busca de empresas)."""

    nome: str
    website: Optional[str] = None
    cnpj: Optional[str] = None
    localizacao: str
    segmento_identificado: str
    icp_score: int
    justificativa_match: str
    fontes: List[str] = []
    noticias: List[Noticia] = []


class EmpresasAgentOutput(BaseModel):
    """Saída estruturada da FASE 1 (busca de empresas).

    Campos determinísticos do contrato completo (pesquisa_id, executado_em,
    entrada, total_empresas_encontradas, meta_atingida) são preenchidos em
    Python — nunca pelo modelo, que erra uuid/contagem/data.
    """

    empresas: List[EmpresaICP]
    erros_ou_avisos: List[str] = []
    resumo_da_pesquisa: str


class EmpresaNoticiasResultado(BaseModel):
    """Notícias encontradas para uma empresa dentro de um lote (fase 2)."""

    nome: str  # deve ecoar exatamente o nome enviado no prompt do lote
    noticias: List[Noticia] = []


class LoteNoticiasOutput(BaseModel):
    """Saída estruturada da FASE 2 (notícias de um lote de empresas)."""

    resultados: List[EmpresaNoticiasResultado]


class ProdutoInput(BaseModel):
    id: int
    nome: str


class EmpresaConhecidaInput(BaseModel):
    """Empresa que a base já tem, entrando como insumo da pesquisa.

    Mesmos campos de `EmpresaICP` menos as notícias: é justamente a notícia que
    a reinclusão vai buscar de novo. Quem monta a lista é
    `services/search_scope.py` — aqui só se consome, para este módulo continuar
    sem conhecer o banco.
    """

    nome: str
    website: Optional[str] = None
    cnpj: Optional[str] = None
    localizacao: str = ""
    segmento_identificado: str = ""
    icp_score: int = 0
    justificativa_match: str = ""


class ResultadoPesquisa(BaseModel):
    """Contrato de dados completo, persistido em `SearchRun.result_json` e
    consumido pela próxima etapa do pipeline. Ver CONTRATO_PESQUISA.md."""

    pesquisa_id: str
    executado_em: str  # ISO-8601 em horário de Brasília (UTC-3), via brt_now()
    entrada: dict
    empresas: List[EmpresaICP]  # cada empresa carrega suas próprias notícias
    total_empresas_encontradas: int
    meta_atingida: bool
    erros_ou_avisos: List[str]
    resumo_da_pesquisa: str


# ==========================================================================
# Agentes
# ==========================================================================
def _build_company_finder_agent(model: str, effort: str) -> Agent:
    return Agent(
    name="Prospector de Leads - Empresas",
    instructions=(
        "Você é um pesquisador especialista em prospecção B2B (ICP research) para o "
        "mercado brasileiro. Sua tarefa é usar a ferramenta de busca na web para "
        "encontrar empresas que sejam potenciais clientes de um produto/serviço, "
        "dentro de uma região e de um segmento estratégico informados.\n\n"
        "DEFINIÇÃO DE MATCH DE ICP (Ideal Customer Profile), avalie os três sinais:\n"
        "1. Aderência do segmento de atuação da empresa ao segmento informado;\n"
        "2. Presença geográfica dentro da região informada;\n"
        "3. Sinais de necessidade do produto (menções a processos, matérias-primas, "
        "operações ou desafios compatíveis com a descrição do produto).\n\n"
        "ESTRATÉGIA DE BUSCA (deep research): uma única busca não traz empresas "
        "suficientes. Gere e execute MÚLTIPLAS queries de busca variando: sinônimos "
        "do segmento, sub-segmentos correlatos, cidades/polos industriais dentro da "
        "região, associações/federações setoriais da região, e termos ligados ao "
        "produto (ex.: 'fornecedores de X em [região]', 'indústrias de [segmento] em "
        "[cidade]', 'associação de [segmento] [estado]'). Você tem um orçamento "
        f"limitado de até {MAX_TOOL_CALLS_EMPRESAS} chamadas de busca, planeje as "
        "queries para maximizar a cobertura dentro desse limite.\n\n"
        "META: reunir NO MÍNIMO 30 empresas qualificadas (que atendam ao menos 2 dos 3 "
        "sinais de ICP). Para cada empresa, preencha: nome, website (se encontrado), "
        "CNPJ (se encontrado, senão null), localização, segmento identificado, "
        "icp_score de 0 a 100 (quanto mais sinais fortes, maior o score), uma "
        "justificativa curta e objetiva do match, e a lista de URLs-fonte usadas para "
        "encontrá-la/qualificá-la (rastreabilidade obrigatória). Não preencha notícias "
        "nesta etapa, isso é feito depois, em outra chamada.\n\n"
        "DEDUPLICAÇÃO: nunca repita a mesma empresa (mesmo nome, mesmo domínio de "
        "website ou mesmo CNPJ). Cada empresa deve aparecer uma única vez.\n\n"
        f"{REGRA_SEM_TRAVESSAO}\n\n"
        "SE NÃO ATINGIR A META de 30 empresas mesmo usando todo o orçamento de "
        "buscas, NÃO invente empresas para completar o número. Retorne apenas as que "
        "encontrou de verdade e registre em erros_ou_avisos uma explicação clara do "
        "motivo (ex.: 'região muito específica com poucas empresas do segmento "
        "indexadas na web') e quantas foram encontradas.\n\n"
        "Responda SOMENTE com os dados estruturados solicitados, em português do Brasil."
    ),
    model=model,
    model_settings=ModelSettings(reasoning=Reasoning(effort=effort)),
    tools=[WebSearchTool(search_context_size=CONTEXT_SIZE)],
    output_type=EmpresasAgentOutput,
    )


def _build_news_batch_agent(model: str, effort: str) -> Agent:
    return Agent(
    name="Prospector de Leads - Noticias por Empresa",
    instructions=(
        "Você recebe uma lista de empresas (nome, website, localização, segmento) e "
        "deve buscar na web, para CADA UMA delas, notícias recentes e ESPECÍFICAS "
        "daquela empresa (não notícias genéricas do segmento ou da região), usadas "
        "como gatilho de abordagem comercial personalizado (ex.: expansão, nova "
        "planta, investimento, contratação, prêmio, problema operacional, fusão/"
        "aquisição).\n\n"
        f"Para cada empresa, inclua até {NOTICIAS_POR_EMPRESA} notícias, publicadas "
        "dentro da janela de tempo indicada no prompt do usuário. Cada notícia precisa "
        "de título, data de publicação (YYYY-MM-DD), resumo curto, URL da fonte e uma "
        "nota de relevância (por que ela serve como gatilho para ESSA empresa "
        "especificamente).\n\n"
        "NÃO invente notícias nem datas. Se não encontrar notícias de uma empresa, "
        "retorne a lista de notícias dela vazia (isso é esperado e normal, não é "
        "erro). NÃO deixe de incluir uma empresa da lista no resultado só porque não "
        "achou notícias dela.\n\n"
        "Retorne o campo `nome` de cada empresa EXATAMENTE como foi fornecido no "
        "prompt (mesma grafia), para permitir associação automática com os dados "
        "originais.\n\n"
        f"Você tem um orçamento limitado de até {MAX_TOOL_CALLS_POR_LOTE_NOTICIAS} "
        "chamadas de busca para este lote de empresas, priorize as buscas mais "
        "prováveis de trazer notícia relevante para cada empresa.\n\n"
        f"{REGRA_SEM_TRAVESSAO}\n\n"
        "Responda SOMENTE com os dados estruturados solicitados, em português do Brasil."
    ),
    model=model,
    model_settings=ModelSettings(reasoning=Reasoning(effort=effort)),
    tools=[WebSearchTool(search_context_size=CONTEXT_SIZE)],
    output_type=LoteNoticiasOutput,
    )


def _extract_usage(result) -> dict:
    """Lê os tokens de entrada/saída do resultado do Runner (tolerante a versões do SDK)."""
    try:
        usage = result.context_wrapper.usage
        return {"input": int(usage.input_tokens or 0), "output": int(usage.output_tokens or 0)}
    except Exception:
        return {"input": 0, "output": 0}


def _error_message(exc: Exception) -> str:
    """Traduz exceções de rede/SDK em mensagens amigáveis em pt-BR (mesmo padrão do product_agent)."""
    msg = str(exc).lower()
    if "invalid_api_key" in msg or "incorrect api key" in msg or "401" in msg:
        return "Chave da OpenAI inválida. Verifique OPENAI_API_KEY no arquivo .env."
    if "insufficient_quota" in msg or "quota" in msg:
        return "Cota da OpenAI esgotada. Verifique seu plano/billing na OpenAI."
    return "Não foi possível concluir a pesquisa agora. Tente novamente em instantes."


def _normalize_domain(website: Optional[str]) -> Optional[str]:
    if not website:
        return None
    w = website.strip().lower()
    w = re.sub(r"^https?://", "", w)
    w = re.sub(r"^www\.", "", w)
    w = w.split("/")[0]
    return w or None


def _normalize_cnpj(cnpj: Optional[str]) -> Optional[str]:
    if not cnpj:
        return None
    digits = re.sub(r"\D", "", cnpj)
    return digits or None


def _normalize_name(name: str) -> str:
    n = name.strip().lower()
    n = re.sub(r"[^\w\s]", "", n)
    # Sufixos societários comuns que não ajudam a distinguir empresas diferentes.
    for suf in (" ltda", " sa", " s a", " eireli", " me", " epp"):
        n = re.sub(rf"{suf}\b", "", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n


def _dedupe_empresas(empresas: List[EmpresaICP]) -> List[EmpresaICP]:
    """Deduplica por domínio do website → CNPJ (só dígitos) → nome normalizado."""
    seen: set = set()
    out: List[EmpresaICP] = []
    for e in empresas:
        key = _normalize_domain(e.website) or _normalize_cnpj(e.cnpj) or _normalize_name(e.nome)
        if key in seen:
            continue
        seen.add(key)
        e.icp_score = max(0, min(100, e.icp_score))
        out.append(e)
    return out


def _filtrar_noticias(noticias: List[Noticia], data_limite: date) -> List[Noticia]:
    """Mantém só notícias dentro da janela de tempo, ordena por data desc e corta
    em NOTICIAS_POR_EMPRESA. Usado por empresa, não numa lista global."""
    validas = []
    for n in noticias:
        try:
            dt = datetime.strptime(n.data_publicacao, "%Y-%m-%d").date()
        except ValueError:
            continue
        if dt >= data_limite:
            validas.append((dt, n))
    validas.sort(key=lambda t: t[0], reverse=True)
    return [n for _, n in validas[:NOTICIAS_POR_EMPRESA]]


def _aplicar_noticias_do_lote(
    lote: List[EmpresaICP], lote_output: LoteNoticiasOutput, data_limite: date
) -> None:
    """Associa as notícias retornadas pelo agente do lote de volta às empresas
    originais (casando por nome normalizado, robusto a pequenas variações de
    grafia do modelo) e aplica a mesma janela/corte de _filtrar_noticias."""
    por_nome = {_normalize_name(r.nome): r.noticias for r in lote_output.resultados}
    for empresa in lote:
        brutas = por_nome.get(_normalize_name(empresa.nome), [])
        empresa.noticias = _filtrar_noticias(brutas, data_limite)


def _build_prompt_empresas(
    produtos: List[ProdutoInput], regiao: str, segmento: str,
    conhecidas: Optional[List["EmpresaConhecidaInput"]] = None,
) -> str:
    produtos_txt = "\n".join(f"- {p.nome}" for p in produtos)
    prompt = (
        f"Produto(s)/serviço(s) a prospectar:\n{produtos_txt}\n\n"
        f"Região de interesse: {regiao}\n"
        f"Segmento estratégico: {segmento}\n\n"
        f"Data de hoje: {brt_now().date().isoformat()}\n\n"
    )
    if conhecidas:
        # A lista vai para o modelo em AMBOS os modos de execução. Mesmo quando
        # o usuário pediu para reincluir as empresas conhecidas, elas voltam
        # pela via determinística (a plataforma já tem os dados delas) — o que
        # não pode acontecer é o orçamento de buscas, que é o recurso escasso
        # desta fase, ser gasto reencontrando o que a base já tem.
        lista = "\n".join(
            f"- {e.nome}" + (f" ({e.website})" if e.website else "")
            for e in conhecidas[:LIMITE_CONHECIDAS_NO_PROMPT]
        )
        prompt += (
            "EMPRESAS QUE A BASE JÁ TEM (não retorne nenhuma delas; elas já foram "
            "encontradas em pesquisas anteriores e são tratadas fora desta busca). "
            "Use o orçamento de buscas para encontrar empresas DIFERENTES destas:\n"
            f"{lista}\n\n"
        )
    return prompt + "Execute a pesquisa de empresas e retorne os dados estruturados."


def _build_prompt_noticias(lote: List[EmpresaICP], data_limite: date) -> str:
    empresas_txt = "\n".join(
        f"- nome: {e.nome} | website: {e.website or 'desconhecido'} | "
        f"localização: {e.localizacao} | segmento: {e.segmento_identificado}"
        for e in lote
    )
    return (
        f"Empresas deste lote:\n{empresas_txt}\n\n"
        f"Data de hoje: {brt_now().date().isoformat()}\n"
        f"Janela de notícias: apenas a partir de {data_limite.isoformat()} (últimos 6 meses).\n\n"
        "Busque as notícias de cada empresa e retorne os dados estruturados."
    )


async def _run_phase(agent: Agent, prompt: str, max_seconds: int) -> AsyncIterator[Tuple]:
    """Executa um agente em streaming, com o mesmo mecanismo de corte por
    tempo usado em ambas as fases: yields ('progress', n) durante as buscas e,
    ao final, ('result', output, usage) ou ('timeout',) se estourar o tempo.

    `max_turns=30` é só um teto de segurança grosseiro (turnos, não chamadas
    de tool) — o corte real e garantido é `max_seconds`.
    """
    result = Runner.run_streamed(agent, prompt, max_turns=30)
    n = 0
    t0 = time.monotonic()
    async for event in result.stream_events():
        if time.monotonic() - t0 > max_seconds:
            result.cancel(mode="immediate")
            yield ("timeout",)
            return
        if getattr(event, "type", None) != "run_item_stream_event":
            continue
        if getattr(event, "name", None) == "tool_called":
            n += 1
            yield ("progress", n)
    yield ("result", result.final_output, _extract_usage(result))


def _chave_de_dedupe(nome: str, website: Optional[str], cnpj: Optional[str]) -> str:
    """Mesma prioridade de `_dedupe_empresas`: domínio, CNPJ, nome normalizado."""
    return _normalize_domain(website) or _normalize_cnpj(cnpj) or _normalize_name(nome)


async def stream_prospect_search(
    produtos: List[ProdutoInput], regiao: str, segmento: str,
    conhecidas: Optional[List[EmpresaConhecidaInput]] = None,
    reincluir: Optional[List[EmpresaConhecidaInput]] = None,
) -> AsyncIterator[Tuple]:
    """Executa a pesquisa de prospecção em duas fases (empresas, depois
    notícias em lotes), em streaming.

    `conhecidas` é a base atual da organização: entra no prompt da fase 1 como
    lista de exclusão e é aplicada de novo em Python sobre o que o modelo
    devolver, porque uma instrução no prompt é pedido, não garantia.

    `reincluir` são as empresas já conhecidas que o usuário mandou incluir nesta
    pesquisa para renovar as notícias. Elas não passam pela fase 1 (a base já
    tem os dados delas, e reencontrá-las custaria orçamento de busca), entram
    direto na fase 2 junto com as novas e saem no resultado como qualquer outra
    empresa — inclusive para a materialização, que vai reapontá-las para esta
    pesquisa e assim expor as notícias novas à priorização.

    Gerador assíncrono que emite eventos de progresso e o resultado final,
    para a UI acompanhar uma execução longa:
      ("progress", n_buscas, mensagem)
      ("done", resultado: ResultadoPesquisa, usage)
      ("error", mensagem pt-BR)
    """
    if not os.environ.get("OPENAI_API_KEY"):
        yield (
            "error",
            "IA indisponível: configure OPENAI_API_KEY no arquivo .env para usar "
            "o assistente de prospecção.",
        )
        return

    model, effort = get_agent_config("prospect")
    data_limite = brt_now().date() - timedelta(days=JANELA_NOTICIAS_DIAS)
    usage_total = {"input": 0, "output": 0}
    total_buscas = 0

    # ---------- FASE 1: busca de empresas ----------
    prompt_empresas = _build_prompt_empresas(produtos, regiao, segmento, conhecidas)
    empresas_output: Optional[EmpresasAgentOutput] = None
    try:
        company_finder_agent = _build_company_finder_agent(model, effort)
        async for ev in _run_phase(company_finder_agent, prompt_empresas, MAX_SEARCH_SECONDS_EMPRESAS):
            if ev[0] == "progress":
                total_buscas += 1
                yield ("progress", total_buscas, f"Buscando empresas - busca {total_buscas}...")
            elif ev[0] == "timeout":
                yield (
                    "error",
                    f"A busca de empresas excedeu {MAX_SEARCH_SECONDS_EMPRESAS // 60} minutos "
                    "e foi interrompida. Tente novamente com uma região ou segmento mais específicos.",
                )
                return
            elif ev[0] == "result":
                empresas_output, usage = ev[1], ev[2]
                usage_total["input"] += usage["input"]
                usage_total["output"] += usage["output"]
    except Exception as e:  # rede, chave inválida, cota, structured-output, etc.
        yield ("error", _error_message(e))
        return

    novas = _dedupe_empresas(empresas_output.empresas)
    avisos = list(empresas_output.erros_ou_avisos)

    # O modelo pode devolver uma empresa da lista de exclusão mesmo instruído a
    # não fazê-lo. Descartar aqui é o que garante que "apenas novas" signifique
    # apenas novas, e evita a mesma empresa aparecer duas vezes no modo de
    # reinclusão (uma vinda da busca, outra vinda da base).
    if conhecidas:
        ja_na_base = {_chave_de_dedupe(e.nome, e.website, e.cnpj) for e in conhecidas}
        antes = len(novas)
        novas = [
            e for e in novas
            if _chave_de_dedupe(e.nome, e.website, e.cnpj) not in ja_na_base
        ]
        repetidas = antes - len(novas)
        if repetidas:
            avisos.append(
                f"{repetidas} empresa(s) devolvida(s) pela busca já estavam na base e "
                "foram descartadas do resultado."
            )

    # A meta é sobre o que a BUSCA encontrou. Contar as reincluídas aqui faria
    # uma pesquisa que não achou nada de novo aparecer como meta atingida.
    meta_atingida = len(novas) >= META_EMPRESAS
    if not meta_atingida:
        avisos.append(
            f"Meta de {META_EMPRESAS} empresas não atingida: foram encontradas apenas "
            f"{len(novas)} empresas qualificadas dentro do orçamento de "
            f"{MAX_TOOL_CALLS_EMPRESAS} buscas."
        )

    reincluidas = [
        EmpresaICP(
            nome=e.nome, website=e.website, cnpj=e.cnpj, localizacao=e.localizacao,
            segmento_identificado=e.segmento_identificado, icp_score=e.icp_score,
            justificativa_match=e.justificativa_match,
        )
        for e in (reincluir or [])
    ]
    if reincluidas:
        avisos.append(
            f"{len(reincluidas)} empresa(s) já conhecida(s) foram incluídas nesta "
            "pesquisa para renovar as notícias."
        )

    empresas = novas + reincluidas
    total = len(empresas)

    # ---------- FASE 2: notícias por empresa, em lotes ----------
    lotes = [
        empresas[i : i + EMPRESAS_POR_LOTE_NOTICIAS]
        for i in range(0, len(empresas), EMPRESAS_POR_LOTE_NOTICIAS)
    ]
    total_lotes = len(lotes)
    news_batch_agent = _build_news_batch_agent(model, effort)
    for idx, lote in enumerate(lotes, start=1):
        prompt_lote = _build_prompt_noticias(lote, data_limite)
        try:
            async for ev in _run_phase(news_batch_agent, prompt_lote, MAX_SEARCH_SECONDS_POR_LOTE_NOTICIAS):
                if ev[0] == "progress":
                    total_buscas += 1
                    yield (
                        "progress",
                        total_buscas,
                        f"Buscando notícias - lote {idx}/{total_lotes}...",
                    )
                elif ev[0] == "timeout":
                    avisos.append(
                        f"Lote {idx}/{total_lotes} de notícias excedeu o tempo e foi pulado "
                        "- as empresas desse lote ficaram sem notícia dedicada."
                    )
                elif ev[0] == "result":
                    lote_output, usage = ev[1], ev[2]
                    usage_total["input"] += usage["input"]
                    usage_total["output"] += usage["output"]
                    _aplicar_noticias_do_lote(lote, lote_output, data_limite)
        except Exception as e:
            # Falha de um lote (ex.: rede) não derruba a pesquisa inteira — só
            # deixa aquelas empresas sem notícia dedicada (best-effort).
            avisos.append(
                f"Lote {idx}/{total_lotes} de notícias falhou ({_error_message(e)}). "
                "as empresas desse lote ficaram sem notícia dedicada."
            )
            continue

    sem_noticias = sum(1 for e in empresas if not e.noticias)
    if sem_noticias:
        avisos.append(
            f"{sem_noticias} de {total} empresas não têm notícias recentes específicas "
            "encontradas (nenhuma notícia foi inventada)."
        )

    resultado = ResultadoPesquisa(
        pesquisa_id=str(uuid.uuid4()),
        executado_em=brt_now().isoformat(),
        entrada={
            "produtos": [p.model_dump() for p in produtos],
            "regiao": regiao,
            "segmento": segmento,
            # Quantas das empresas do resultado vieram da base em vez da busca.
            # Fica registrado porque muda a leitura do total: uma pesquisa de 45
            # empresas com 15 reincluídas encontrou 30, não 45.
            "empresas_reincluidas": len(reincluidas),
        },
        empresas=empresas,
        total_empresas_encontradas=total,
        meta_atingida=meta_atingida,
        erros_ou_avisos=avisos,
        resumo_da_pesquisa=empresas_output.resumo_da_pesquisa,
    )
    yield ("done", resultado, usage_total)
