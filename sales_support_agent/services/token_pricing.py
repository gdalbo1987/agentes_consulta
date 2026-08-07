"""Preço do token de cada modelo OpenAI — fonte única do custo exibido em `/admin`.

Mesmo padrão de `services/settings.py`: as linhas nascem na primeira leitura,
são editáveis pelo super admin e não têm cache (uma mudança vale na próxima
carga do painel, sem restart).

Uma linha POR MODELO de `settings.MODELOS_DISPONIVEIS`. O consumo é cruzado com
o preço pelo campo `TokenUsage.model`, gravado no momento da chamada — por isso
o custo do painel é cálculo, não rateio: cada consumo é multiplicado pelo preço
do modelo que efetivamente o gerou, e trocar o modelo de um agente não reescreve
o histórico.

Unidades — a distinção importa e é a origem mais provável de confusão:

* No BANCO o valor é **USD por token** (ex.: 0.00000025), que é como o cálculo
  é feito: `input_tokens * input_cost_per_token`.
* Na UI o super admin digita **USD por 1 milhão de tokens** (ex.: 0.25), que é
  o formato da tabela de preços publicada pela OpenAI. `de_por_milhao` e
  `para_por_milhao` fazem a conversão nas duas pontas.
"""

from typing import Callable, Dict, Tuple

import reflex as rx

from prospect_agent.models import TokenPricing, brt_now
from prospect_agent.services.settings import MODELOS_DISPONIVEIS

UM_MILHAO = 1_000_000


def de_por_milhao(valor_por_milhao: float) -> float:
    """USD por 1M de tokens (o que o admin digita) -> USD por token (o que grava)."""
    return valor_por_milhao / UM_MILHAO


def para_por_milhao(valor_por_token: float) -> float:
    """USD por token (banco) -> USD por 1M de tokens (o que o admin lê)."""
    return valor_por_token * UM_MILHAO


def ensure_token_pricing() -> None:
    """Cria, zeradas, as linhas dos modelos que ainda não existem. Idempotente.

    Nasce com preço zero de propósito: um valor "padrão" inventado apareceria
    nos cards como se fosse custo real. Zerado, fica evidente que falta
    configurar.

    Só CRIA — nunca mexe numa linha existente (o preço digitado pelo super admin
    é definitivo) e nunca apaga a linha de um modelo que saiu de
    `MODELOS_DISPONIVEIS`, porque o histórico de `TokenUsage` pode continuar
    apontando para ele e o custo passado deixaria de fechar.
    """
    with rx.session() as session:
        existentes = {linha.model for linha in session.query(TokenPricing).all()}
        novos = [m for m in MODELOS_DISPONIVEIS if m not in existentes]
        for modelo in novos:
            session.add(TokenPricing(model=modelo))
        if novos:
            session.commit()


def get_pricing() -> Dict[str, Tuple[float, float]]:
    """{modelo: (custo_entrada_por_token, custo_saida_por_token)} em USD.

    Devolve TODAS as linhas gravadas, inclusive de modelos que já saíram do
    dropdown — são elas que fazem o custo histórico continuar fechando.
    """
    with rx.session() as session:
        return {
            linha.model: (linha.input_cost_per_token, linha.output_cost_per_token)
            for linha in session.query(TokenPricing).all()
        }


def funcoes_de_custo(
    precos: Dict[str, Tuple[float, float]],
    modelo_por_agente: Dict[str, str],
) -> Tuple[Callable, Callable]:
    """(custo_entrada, custo_saida) — cada uma recebe UMA linha de `TokenUsage`
    e devolve o custo em USD daquela linha.

    Devolvidas como funções de um argumento só para poderem ser passadas direto
    ao `_monthly_series` do painel, que é o mesmo agregador dos outros gráficos.

    `modelo_por_agente` só entra em ação para linhas com `model` vazio — as
    gravadas antes da coluna existir. Modelo sem preço cadastrado custa zero:
    é o mesmo efeito de um preço zerado e evita que um modelo aposentado
    derrube a página com KeyError.
    """

    def _precos(linha) -> Tuple[float, float]:
        modelo = linha.model or modelo_por_agente.get(linha.agent_name, "")
        return precos.get(modelo, (0.0, 0.0))

    def custo_entrada(linha) -> float:
        return linha.input_tokens * _precos(linha)[0]

    def custo_saida(linha) -> float:
        return linha.output_tokens * _precos(linha)[1]

    return custo_entrada, custo_saida


def salvar_pricing(model: str, input_cost_per_token: float, output_cost_per_token: float) -> None:
    with rx.session() as session:
        linha = session.query(TokenPricing).filter(TokenPricing.model == model).first()
        if not linha:
            linha = TokenPricing(model=model)
            session.add(linha)
        linha.input_cost_per_token = input_cost_per_token
        linha.output_cost_per_token = output_cost_per_token
        linha.updated_at = brt_now()
        session.commit()
