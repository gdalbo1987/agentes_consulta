"""Agente de IA (OpenAI Agents SDK) que sugere dicas de primeiro contato.

Fase 3 do funil, roda logo após o Agente de Priorização (services/
priorizacao_agent.py) sobre o mesmo lead: recebe o registro enriquecido do
lead + o resultado da priorização (pontos fracos/fortes por critério) e
sugere de 2 a 4 dicas objetivas de abordagem comercial — gancho de abertura,
canal recomendado, dor provável e timing sugerido — personalizadas com os
dados reais do lead, sem inventar nada que não esteja na base.

Compartilha o mesmo `AgentModelSetting` (agent_key="priorizacao") do Agente de
Priorização — lido via `services/settings.get_agent_config`, não mais uma
constante fixa importada de `priorizacao_agent.py`. Sem `web_search`.
"""
import os
from typing import List, Optional

from pydantic import BaseModel

from agents import Agent, ModelSettings, Runner
from openai.types.shared import Reasoning

from sales_support_agent.services.prompt_rules import REGRA_SEM_TRAVESSAO
from sales_support_agent.services.settings import get_agent_config

try:
    from agents import set_tracing_disabled

    set_tracing_disabled(True)
except Exception:  # pragma: no cover - compatibilidade entre versões do SDK
    pass

from sales_support_agent.services.priorizacao_agent import (  # noqa: E402
    formatar_registro_lead,
    formatar_noticias,
)


class DicaApproach(BaseModel):
    tipo: str  # "gancho" | "canal" | "dor" | "timing"
    dica: str  # 1-2 frases objetivas


class ApproachResultado(BaseModel):
    dicas: List[DicaApproach]  # 2 a 4 itens


def _build_approach_agent(model: str, effort: str) -> Agent:
    return Agent(
    name="Agente de Approach Comercial",
    instructions=(
        "Você é um especialista em prospecção B2B. A partir do registro completo "
        "de um lead já enriquecido e da classificação de priorização feita para "
        "ele (pontos por critério + justificativas), sugira de 2 a 4 dicas "
        "OBJETIVAS de primeiro contato, dos tipos:\n"
        "- gancho: um gancho de abertura personalizado com dados reais do lead "
        "(ex.: notícia recente, segmento, porte);\n"
        "- canal: o canal recomendado de abordagem (telefone, WhatsApp, e-mail, "
        "LinkedIn), considerando os contatos e dados de contato disponíveis;\n"
        "- dor: um ponto de dor provável, coerente com o segmento/porte/situação "
        "cadastral do lead;\n"
        "- timing: um timing sugerido para o contato, se houver algum sinal no "
        "registro que justifique (ex.: sinal de investimento, alerta de situação "
        "especial).\n\n"
        "Use no máximo um item de cada tipo, no total entre 2 e 4 dicas: não é "
        "obrigatório usar os 4 tipos, use apenas os que fizerem sentido com os "
        "dados disponíveis.\n\n"
        f"{REGRA_SEM_TRAVESSAO}\n\n"
        "REGRA CRÍTICA: nunca invente dado que não está no registro do lead nem "
        "na classificação de priorização fornecida. Se não houver dado suficiente "
        "para um tipo de dica, simplesmente não inclua esse tipo.\n\n"
        "Responda SOMENTE com os dados estruturados solicitados, em português do Brasil."
    ),
    model=model,
    model_settings=ModelSettings(reasoning=Reasoning(effort=effort)),
    output_type=ApproachResultado,
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
    return "Não foi possível gerar as dicas de approach agora. Tente novamente em instantes."


def _formatar_priorizacao(criterios: List[dict], score_final: int, classe: str) -> str:
    linhas = [f"Score final: {score_final}/100 (classe: {classe})", "Critérios avaliados:"]
    for c in criterios:
        linhas.append(f"  - {c['criterio']}: {c['pontos']} pontos. {c['justificativa']}")
    return "\n".join(linhas)


def _build_prompt(lead_data: dict, criterios: List[dict], score_final: int, classe: str, noticias: Optional[List[dict]] = None) -> str:
    partes = [
        "Registro do lead:",
        formatar_registro_lead(lead_data),
        "",
        "Resultado da priorização já calculada para este lead:",
        _formatar_priorizacao(criterios, score_final, classe),
        "",
        formatar_noticias(noticias),
        "",
        "Sugira as dicas de primeiro contato para este lead.",
    ]
    return "\n".join(partes)


async def classificar_approach(
    lead_data: dict, criterios: List[dict], score_final: int, classe: str, noticias: Optional[List[dict]] = None,
) -> tuple:
    """Retorna (ok, resultado: ApproachResultado | None, mensagem_erro_pt_BR, usage)."""
    if not os.environ.get("OPENAI_API_KEY"):
        return (
            False,
            None,
            "IA indisponível: configure OPENAI_API_KEY no arquivo .env para usar o approach.",
            {"input": 0, "output": 0},
        )

    prompt = _build_prompt(lead_data, criterios, score_final, classe, noticias)
    try:
        model, effort = get_agent_config("priorizacao")
        agent = _build_approach_agent(model, effort)
        result = await Runner.run(agent, prompt)
        resultado: ApproachResultado = result.final_output
        return True, resultado, "", _extract_usage(result)
    except Exception as e:
        return False, None, _error_message(e), {"input": 0, "output": 0}
