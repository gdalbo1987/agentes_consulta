"""Agente de IA (OpenAI Agents SDK) que ajuda o usuário a redigir a descrição
de um produto/serviço profissional.

Fluxo:
- Um *input guardrail* classifica se o texto tem afinidade com um produto ou
  serviço profissional/corporativo. Se não tiver, dispara o tripwire e a geração
  é recusada (evita que o campo seja usado para conteúdo fora de contexto).
- O agente redator complementa/aprimora a descrição em pt-BR, mantendo tom
  profissional. O texto retornado volta para o campo (que segue editável).

Modelo e reasoning effort não são mais fixados no .env — vêm de
`AgentModelSetting` (agent_key="product"), editável pelo super admin em
`/admin` (ver services/settings.py). Por isso os `Agent(...)` deste módulo
deixaram de ser singletons de módulo (um objeto fixado na importação nunca
refletiria uma troca de modelo sem reiniciar o processo) e passaram a ser
construídos a cada chamada por `_build_writer_agent()`/`_build_guardrail_agent()`.

Configuração via .env:
- OPENAI_API_KEY: chave da OpenAI (obrigatória para funcionar).
"""
import os

from pydantic import BaseModel

from typing import AsyncIterator, Tuple

from agents import (
    Agent,
    ModelSettings,
    Runner,
    input_guardrail,
    GuardrailFunctionOutput,
    InputGuardrailTripwireTriggered,
)
from openai.types.shared import Reasoning

from sales_support_agent.services.settings import get_agent_config

try:  # Evita enviar traces para a OpenAI sem necessidade.
    from agents import set_tracing_disabled

    set_tracing_disabled(True)
except Exception:  # pragma: no cover - compatibilidade entre versões do SDK
    pass


class RelevanceCheck(BaseModel):
    """Saída estruturada do guardrail de relevância."""

    is_professional_product: bool
    reasoning: str


def _build_guardrail_agent(model: str) -> Agent:
    """Classificador simples — mesmo padrão de `guardrail_agent_insights`, sem
    reasoning effort (não é o agente principal, só um sim/não rápido)."""
    return Agent(
        name="Guardrail de Relevancia",
        instructions=(
            "Você avalia se o texto do usuário descreve um PRODUTO ou SERVIÇO "
            "profissional/corporativo legítimo (ex.: software, equipamento, "
            "consultoria, insumo, serviço B2B/B2C sério). Marque "
            "is_professional_product=false quando o conteúdo for irrelevante, "
            "pessoal, ofensivo, ilegal, ou claramente sem afinidade com um produto "
            "comercial profissional. Seja tolerante com rascunhos curtos, desde que "
            "indiquem um produto/serviço real."
        ),
        output_type=RelevanceCheck,
        model=model,
    )


async def _professional_guardrail_factory(model: str):
    @input_guardrail
    async def professional_guardrail(ctx, agent, input):  # noqa: A002 - nome exigido pelo SDK
        result = await Runner.run(_build_guardrail_agent(model), input, context=ctx.context)
        check = result.final_output_as(RelevanceCheck)
        return GuardrailFunctionOutput(
            output_info=check,
            tripwire_triggered=not check.is_professional_product,
        )

    return professional_guardrail


async def _build_writer_agent() -> Agent:
    model, effort = get_agent_config("product")
    guardrail = await _professional_guardrail_factory(model)
    return Agent(
        name="Redator de Produtos",
        instructions=(
            "Você é um redator especialista em descrições de produtos e serviços "
            "corporativos, escrevendo em português do Brasil (pt-BR). A partir do "
            "nome e de um texto inicial (que pode estar incompleto), complemente e "
            "aprimore a descrição: destaque benefícios, diferenciais e aplicação "
            "profissional, com tom claro, objetivo e persuasivo. Não invente dados "
            "técnicos específicos (preços, certificações) que não foram informados. "
            "Responda APENAS com a descrição final, sem títulos ou comentários."
        ),
        model=model,
        model_settings=ModelSettings(reasoning=Reasoning(effort=effort)),
        input_guardrails=[guardrail],
    )


def _extract_usage(result) -> dict:
    """Lê os tokens de entrada/saída do resultado do Runner (tolerante a versões do SDK)."""
    try:
        usage = result.context_wrapper.usage
        return {"input": int(usage.input_tokens or 0), "output": int(usage.output_tokens or 0)}
    except Exception:
        return {"input": 0, "output": 0}


def _build_prompt(name: str, description: str) -> str:
    return (
        f"Nome do produto/serviço: {name or '(não informado)'}\n"
        f"Texto atual: {description or '(vazio)'}\n\n"
        "Complemente e aprimore a descrição profissional deste produto/serviço."
    )


def _error_message(exc: Exception) -> str:
    """Traduz exceções de rede/SDK em mensagens amigáveis em pt-BR."""
    msg = str(exc).lower()
    if "invalid_api_key" in msg or "incorrect api key" in msg or "401" in msg:
        return "Chave da OpenAI inválida. Verifique OPENAI_API_KEY no arquivo .env."
    if "insufficient_quota" in msg or "quota" in msg:
        return "Cota da OpenAI esgotada. Verifique seu plano/billing na OpenAI."
    return "Não foi possível gerar a descrição agora. Tente novamente em instantes."


async def stream_product_text(name: str, description: str) -> AsyncIterator[Tuple]:
    """Versão em *streaming* de `complement_product_text`.

    Gerador assíncrono que emite tuplas de evento, consumidas pelo state para
    exibir o texto aos poucos:
      ("delta", trecho)        -> pedaço incremental do texto gerado
      ("done", texto, usage)   -> texto final completo + tokens usados (input/output)
      ("error", mensagem)      -> falha (guardrail, rede, chave, cota); encerra o fluxo
    """
    if not os.environ.get("OPENAI_API_KEY"):
        yield (
            "error",
            "IA indisponível: configure OPENAI_API_KEY no arquivo .env para usar "
            "o assistente de redação.",
        )
        return

    prompt = _build_prompt(name, description)
    try:
        agent = await _build_writer_agent()
        # run_streamed retorna imediatamente; os eventos chegam ao iterar.
        result = Runner.run_streamed(agent, prompt)
        full = ""
        async for event in result.stream_events():
            # Só interessam os deltas de texto do modelo (Responses API).
            if getattr(event, "type", None) != "raw_response_event":
                continue
            data = getattr(event, "data", None)
            if type(data).__name__ != "ResponseTextDeltaEvent":
                continue
            delta = getattr(data, "delta", "") or ""
            if delta:
                full += delta
                yield ("delta", delta)
        yield ("done", full.strip(), _extract_usage(result))
    except InputGuardrailTripwireTriggered:
        yield (
            "error",
            "O texto não parece ter afinidade com um produto ou serviço "
            "profissional/corporativo. Ajuste o conteúdo e tente novamente.",
        )
    except Exception as e:  # rede, chave inválida, cota, etc.
        yield ("error", _error_message(e))


async def complement_product_text(name: str, description: str) -> tuple[bool, str, dict]:
    """Complementa a descrição de um produto.

    Retorna (ok, texto, usage). `usage` = {"input": int, "output": int} com os
    tokens consumidos (0/0 em erro). Se ok=False, `texto` é uma mensagem de erro
    amigável para exibir ao usuário.
    """
    if not os.environ.get("OPENAI_API_KEY"):
        return (
            False,
            "IA indisponível: configure OPENAI_API_KEY no arquivo .env para usar "
            "o assistente de redação.",
            {"input": 0, "output": 0},
        )

    prompt = (
        f"Nome do produto/serviço: {name or '(não informado)'}\n"
        f"Texto atual: {description or '(vazio)'}\n\n"
        "Complemente e aprimore a descrição profissional deste produto/serviço."
    )
    try:
        agent = await _build_writer_agent()
        result = await Runner.run(agent, prompt)
        return True, (result.final_output or "").strip(), _extract_usage(result)
    except InputGuardrailTripwireTriggered:
        return (
            False,
            "O texto não parece ter afinidade com um produto ou serviço "
            "profissional/corporativo. Ajuste o conteúdo e tente novamente.",
            {"input": 0, "output": 0},
        )
    except Exception as e:  # rede, chave inválida, cota, etc.
        msg = str(e).lower()
        if "invalid_api_key" in msg or "incorrect api key" in msg or "401" in msg:
            return (
                False,
                "Chave da OpenAI inválida. Verifique OPENAI_API_KEY no arquivo .env.",
                {"input": 0, "output": 0},
            )
        if "insufficient_quota" in msg or "quota" in msg:
            return False, "Cota da OpenAI esgotada. Verifique seu plano/billing na OpenAI.", {"input": 0, "output": 0}
        return False, "Não foi possível gerar a descrição agora. Tente novamente em instantes.", {"input": 0, "output": 0}
