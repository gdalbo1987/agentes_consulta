"""Agente de IA (OpenAI Agents SDK) que pontua um lead já enriquecido.

Fase 3 do funil: roda sobre leads que já passaram por `/enriquecimento`. O
agente SÓ classifica cada um dos 7 critérios fixos (services/priorizacao_rules
.PESOS_CRITERIOS) em 0/10/25 pontos + 1 frase de justificativa — não calcula o
score final nem a classe de prioridade. Essa aritmética é feita em Python
(priorizacao_rules.calcular_score_final/definir_classe_prioridade), o mesmo
princípio já usado em services/prospect_agent.py (ids/contagens/datas nunca
confiados ao modelo).

Não usa `web_search`: a entrada é só o registro do lead já no banco (dados de
enriquecimento + contatos decisores), sem necessidade de busca na web.

Modelo e reasoning effort não são mais fixados no .env — vêm de
`AgentModelSetting` (agent_key="priorizacao", compartilhado com
approach_agent.py), editável pelo super admin em `/admin` (ver
services/settings.py). Por isso `priorizacao_agent` deixou de ser um
singleton de módulo e passou a ser construído a cada chamada de
`classificar_lead`.

Configuração via .env:
- OPENAI_API_KEY: chave da OpenAI (obrigatória).
"""
import os
from typing import List, Literal, Optional

from pydantic import BaseModel

from agents import Agent, ModelSettings, Runner
from openai.types.shared import Reasoning

from prospect_agent.services.prompt_rules import REGRA_SEM_TRAVESSAO
from prospect_agent.services.settings import get_agent_config

try:  # Evita enviar traces para a OpenAI sem necessidade (mesmo padrão dos demais agentes).
    from agents import set_tracing_disabled

    set_tracing_disabled(True)
except Exception:  # pragma: no cover - compatibilidade entre versões do SDK
    pass

# Critérios e pesos fixos — importados aqui só para interpolar no prompt.
# A fonte única continua sendo priorizacao_rules.PESOS_CRITERIOS.
from prospect_agent.services.priorizacao_rules import NOMES_CRITERIOS, PESOS_CRITERIOS  # noqa: E402

# `Literal[NOMES_CRITERIOS]` (NOMES_CRITERIOS é uma tupla de 7 strings) vira um
# enum no JSON Schema entregue ao modelo — o próprio schema estruturado força
# a resposta a usar exatamente uma dessas 7 strings, byte a byte. Antes disso
# o campo era `str` livre e as instruções só citavam os nomes dentro do texto
# numerado da rubrica ("1. Fit com ICP (peso 30%): ..."); o modelo podia (e
# empiricamente passou a) devolver variações como "1. Fit com ICP" ou "Fit com
# ICP (peso 30%)", que `validar_criterios` (priorizacao_rules.py) casa por
# igualdade exata de string — um nome fora do dicionário `PESOS_CRITERIOS`
# NUNCA batia, e como o desvio de formatação era sistemático (o modelo aplica
# o mesmo estilo aos 7), os 7 critérios caíam simultaneamente no default
# "não avaliado pelo modelo" com 0 pontos, mascarando como "dado ausente" um
# problema que na verdade era só de formatação do rótulo.
class CriterioClassificado(BaseModel):
    criterio: Literal[NOMES_CRITERIOS]
    pontos: int  # 0 | 10 | 25 — validado/clampado em Python (nunca confiado cru)
    justificativa: str  # 1 frase


class PriorizacaoResultado(BaseModel):
    """Saída estruturada do agente: só a classificação por critério.
    score_final/classe_prioridade são calculados em Python."""

    criterios: List[CriterioClassificado]


_RUBRICA = (
    "1. Fit com ICP (peso 30%): 25 = aderência total ao ICP (segmento + porte + "
    "característica-chave batem); 10 = aderência parcial (2 de 3 sinais do ICP); "
    "0 = fora do ICP.\n"
    "2. Potencial financeiro (peso 20%): 25 = porte/faturamento compatível com "
    "ticket alto; 10 = porte médio; 0 = porte pequeno ou não identificado.\n"
    "3. Facilidade de contato (peso 20%): 25 = contato direto validado (e-mail "
    "corporativo + telefone/WhatsApp + decisor identificado); 10 = contato parcial "
    "(só um canal, ou contato não é decisor); 0 = sem contato direto além de dados "
    "institucionais genéricos.\n"
    "4. Maturidade da empresa (peso 5%): 25 = empresa estruturada (presença "
    "digital consistente, tempo de mercado relevante); 10 = estrutura "
    "intermediária; 0 = empresa incipiente/sem presença digital.\n"
    "5. Segmento estratégico (peso 10%): 25 = segmento prioritário; 10 = segmento "
    "secundário; 0 = segmento não estratégico.\n"
    "6. Região de localização (peso 5%): 25 = região prioritária de atuação; "
    "10 = região secundária/expansão; 0 = fora da área de atuação.\n"
    "7. Sinais de investimento futuro (peso 10%): 25 = sinais fortes (rodada "
    "recente, expansão anunciada, contratação em massa); 10 = sinais fracos/"
    "indiretos; 0 = nenhum sinal identificado."
)


def _build_priorizacao_agent(model: str, effort: str) -> Agent:
    return Agent(
    name="Agente de Priorização de Leads",
    instructions=(
        "Você avalia leads B2B já enriquecidos (dados cadastrais e comerciais "
        "reais, coletados de fontes pagas e gratuitas) para priorização comercial. "
        "Para CADA UM dos 7 critérios fixos abaixo, classifique o lead em "
        "exatamente uma das 3 classes (25, 10 ou 0 pontos) e escreva uma "
        "justificativa objetiva de 1 frase, baseada SOMENTE nos dados fornecidos "
        "no registro do lead.\n\n"
        f"{_RUBRICA}\n\n"
        "REGRA CRÍTICA: não invente dado que não está no registro. Se um dado "
        "necessário para avaliar um critério estiver ausente, classifique de "
        "forma conservadora (10 ou 0, nunca 25) e diga na justificativa que o "
        "dado está ausente.\n\n"
        "Não calcule score final nem classifique o lead em Alta/Média/Baixa; "
        "isso é feito por outro processo.\n\n"
        f"{REGRA_SEM_TRAVESSAO}\n\n"
        "Retorne SOMENTE a classificação dos 7 "
        "critérios, na mesma ordem em que foram apresentados, em português do "
        "Brasil."
    ),
    model=model,
    model_settings=ModelSettings(reasoning=Reasoning(effort=effort)),
    output_type=PriorizacaoResultado,
    )


def _extract_usage(result) -> dict:
    """Lê os tokens de entrada/saída do resultado do Runner (mesmo padrão dos demais agentes)."""
    try:
        usage = result.context_wrapper.usage
        return {"input": int(usage.input_tokens or 0), "output": int(usage.output_tokens or 0)}
    except Exception:
        return {"input": 0, "output": 0}


def _error_message(exc: Exception) -> str:
    """Traduz exceções de rede/SDK em mensagens amigáveis em pt-BR (mesmo padrão dos demais agentes)."""
    msg = str(exc).lower()
    if "invalid_api_key" in msg or "incorrect api key" in msg or "401" in msg:
        return "Chave da OpenAI inválida. Verifique OPENAI_API_KEY no arquivo .env."
    if "insufficient_quota" in msg or "quota" in msg:
        return "Cota da OpenAI esgotada. Verifique seu plano/billing na OpenAI."
    return "Não foi possível priorizar este lead agora. Tente novamente em instantes."


def formatar_registro_lead(lead_data: dict) -> str:
    """Renderiza o registro do lead (dados de PROSPECÇÃO + ENRIQUECIMENTO +
    contatos) como texto simples para o prompt. Reaproveitado por
    approach_agent.py — mesmo registro, mesma formatação, para as duas IAs
    enxergarem o mesmo lead.

    Os campos de prospecção (score/justificativa de match com o ICP) vêm do
    agente de pesquisa (services/prospect_agent.py) e SÃO críticos para o
    critério "Fit com ICP" — sem eles o agente de priorização não tinha base
    nenhuma para avaliar esse critério (o mais pesado, 30%) além do que já
    estava em "segmento".
    """
    linhas = [
        f"Nome: {lead_data.get('nome') or '(desconhecido)'}",
        f"Razão social: {lead_data.get('razao_social') or '(não informado)'}",
        f"Localização: {lead_data.get('cidade_uf') or lead_data.get('localizacao') or '(não informado)'}",
        f"Score de match com o ICP (da pesquisa): {lead_data.get('icp_score', 0)}/100",
        f"Justificativa do match com o ICP (da pesquisa): {lead_data.get('justificativa_match') or '(não informado)'}",
        f"Porte: {lead_data.get('porte') or '(não identificado)'}",
        f"Faturamento estimado: {lead_data.get('faturamento_estimado') or '(não identificado)'}",
        f"Segmento (enriquecido): {lead_data.get('segmento') or '(não informado)'}",
        f"Segmento (identificado na pesquisa): {lead_data.get('segmento_identificado') or '(não informado)'}",
        f"Status cadastral: {lead_data.get('status_cadastral') or '(não informado)'}",
        f"Alerta de situação especial: {lead_data.get('alerta_situacao') or '(nenhum)'}",
        f"Idade da empresa: {lead_data.get('idade_empresa_anos')} anos" if lead_data.get('idade_empresa_anos') is not None else "Idade da empresa: (não identificada)",
        f"Telefone: {lead_data.get('telefone') or '(não encontrado)'}"
        + (" (WhatsApp)" if lead_data.get("telefone_whatsapp") else ""),
        f"Website: {lead_data.get('website_principal') or lead_data.get('website') or '(não encontrado)'}",
        f"LinkedIn: {lead_data.get('linkedin_url') or '(não encontrado)'}",
        f"Percentual de enriquecimento: {lead_data.get('enrichment_percentage', 0)}%",
    ]

    contatos = lead_data.get("contatos") or []
    if contatos:
        linhas.append(f"Contatos decisores encontrados ({len(contatos)}):")
        for ct in contatos:
            origem = "quadro societário (sem canal direto)" if ct.get("origem") == "qsa" else "LinkedIn (canal direto)"
            linhas.append(
                f"  - {ct.get('nome')} | cargo: {ct.get('cargo') or '(não informado)'} | "
                f"senioridade: {ct.get('senioridade') or '(não informada)'} | origem: {origem}"
            )
    else:
        linhas.append("Contatos decisores encontrados: nenhum.")

    return "\n".join(linhas)


def formatar_noticias(noticias: Optional[List[dict]]) -> str:
    """Bloco de notícias recentes específicas da empresa (gatilhos possíveis
    de investimento/expansão), para injetar no prompt. Reaproveitado por
    approach_agent.py — mesma fonte (services/enrichment.noticias_por_empresa),
    mesma formatação nas duas IAs."""
    if not noticias:
        return "Notícias recentes específicas desta empresa: nenhuma encontrada."
    linhas = ["Notícias recentes específicas desta empresa (gatilhos possíveis):"]
    for n in noticias[:5]:
        linhas.append(f"  - [{n.get('data_publicacao') or n.get('data') or '?'}] {n.get('titulo')}: {n.get('resumo') or ''}")
    return "\n".join(linhas)


def _build_prompt(lead_data: dict, noticias: Optional[List[dict]] = None) -> str:
    return (
        "Registro do lead a avaliar:\n\n"
        f"{formatar_registro_lead(lead_data)}\n\n"
        f"{formatar_noticias(noticias)}\n\n"
        "Use as notícias apenas como sinal para o critério \"Sinais de "
        "investimento futuro\": não invente relação com os demais critérios "
        "se a notícia não for pertinente a eles.\n\n"
        "Classifique os 7 critérios fixos para este lead."
    )


async def classificar_lead(lead_data: dict, noticias: Optional[List[dict]] = None) -> tuple:
    """Roda o agente de priorização sobre um lead.

    Retorna (ok, resultado: PriorizacaoResultado | None, mensagem_erro_pt_BR, usage).
    Chamada não-streaming: o output_type é um objeto estruturado pequeno, sem
    nada útil para transmitir token a token — o progresso da UI vem do loop
    por lead no orquestrador (services/priorizacao.py).
    """
    if not os.environ.get("OPENAI_API_KEY"):
        return (
            False,
            None,
            "IA indisponível: configure OPENAI_API_KEY no arquivo .env para usar a priorização.",
            {"input": 0, "output": 0},
        )

    prompt = _build_prompt(lead_data, noticias)
    try:
        model, effort = get_agent_config("priorizacao")
        agent = _build_priorizacao_agent(model, effort)
        result = await Runner.run(agent, prompt)
        resultado: PriorizacaoResultado = result.final_output
        return True, resultado, "", _extract_usage(result)
    except Exception as e:  # rede, chave inválida, cota, structured-output, etc.
        return False, None, _error_message(e), {"input": 0, "output": 0}
