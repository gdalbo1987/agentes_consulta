"""Fonte única de configuração dos agentes de IA (modelo + reasoning effort) e
da integração com a Microsoft Graph.

Os valores ficam em `AgentModelSetting`/`IntegrationSetting`, editáveis pelo
super admin em `/admin`. `_SEMENTE_AGENTES` e a leitura de `.env` dentro de
`ensure_*` são apenas a SEMENTE da primeira execução — depois disso o banco é a
única fonte, o `.env` deixa de ser consultado para esses valores (exceção:
`DATABASE_URL`, que continua só em `.env` — ver docstring de
`IntegrationSetting` em models.py).

Sem cache: cada leitura abre sessão e consulta o banco na hora, para que uma
mudança do super admin valha na próxima chamada, sem restart (o custo de mais
uma query simples é irrelevante perto do resto do trabalho de cada chamada de
agente/integração).

O client secret do Graph é sempre gravado/lido via `services/crypto.py` (Fernet)
— nunca em texto puro no banco, e as funções aqui nunca devem ser usadas para
popular um campo de State com o valor decriptografado (State é serializado para
o browser).
"""
import os
from typing import Optional

import reflex as rx

from sales_support_agent.models import (
    AgentModelSetting,
    IntegrationSetting,
    brt_now,
)
from sales_support_agent.services import crypto

# ---------------------------------------------------------------------------
# Agentes de IA — modelo + reasoning effort
# ---------------------------------------------------------------------------
AGENT_KEYS = ("classificacao", "resumo", "consulta")

# Modelos oferecidos no dropdown: restritos aos que aceitam reasoning effort,
# para os 3 agentes terem a mesma UI (modelo + esforço) sem caso especial.
#
# ATENÇÃO ao trocar esta tupla: `ensure_agent_settings()` nunca sobrescreve uma
# linha existente, então as linhas já gravadas continuam apontando para o
# modelo ANTIGO — que some do dropdown e deixa o select do `/admin` sem valor
# correspondente. Toda troca aqui exige um UPDATE nas linhas de
# `AgentModelSetting`.
MODELOS_DISPONIVEIS = ("gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano")
EFFORTS_DISPONIVEIS = ("minimal", "low", "medium", "high")

# Semente: nome do agente -> (env var do modelo, default, effort inicial).
#
# Os esforços não são iguais de propósito. A classificação é a etapa de MAIOR
# volume (uma chamada por e-mail, duas vezes por dia) e sua saída é um rótulo de
# enum mais um inteiro e dois booleanos, então esforço alto compra pouco. O
# resumo é compressão de um texto que já está inteiro no prompt, o caso em que
# `minimal` basta. A consulta conversa com o usuário e herda a variável de
# ambiente do antigo agente de insights, para que um `.env` já preenchido
# continue valendo.
_SEMENTE_AGENTES = {
    "classificacao": ("OPENAI_CLASSIFICACAO_MODEL", "gpt-5.4-mini", "low"),
    "resumo": ("OPENAI_RESUMO_MODEL", "gpt-5.4-mini", "minimal"),
    "consulta": ("OPENAI_INSIGHTS_MODEL", "gpt-5.4-mini", "low"),
}


def ensure_agent_settings() -> None:
    """Cria as 4 linhas que faltarem, a partir do `.env` atual. Idempotente —
    nunca sobrescreve uma linha já existente (edição do super admin é
    definitiva)."""
    with rx.session() as session:
        existentes = {a.agent_key for a in session.query(AgentModelSetting).all()}
        novos = [k for k in AGENT_KEYS if k not in existentes]
        for key in novos:
            env_var, default_model, default_effort = _SEMENTE_AGENTES[key]
            session.add(
                AgentModelSetting(
                    agent_key=key,
                    model=os.environ.get(env_var, default_model),
                    effort=default_effort,
                )
            )
        if novos:
            session.commit()


def get_agent_config(agent_key: str) -> tuple:
    """(model, effort) do agente. Fallback defensivo na semente se a linha
    ainda não existir (não deveria acontecer após `ensure_agent_settings()`,
    mas evita um `None` se algo rodar fora de ordem)."""
    with rx.session() as session:
        linha = (
            session.query(AgentModelSetting)
            .filter(AgentModelSetting.agent_key == agent_key)
            .first()
        )
        if linha:
            return linha.model, linha.effort
    _, default_model, default_effort = _SEMENTE_AGENTES[agent_key]
    return default_model, default_effort


def salvar_agent_config(agent_key: str, model: str, effort: str) -> None:
    with rx.session() as session:
        linha = (
            session.query(AgentModelSetting)
            .filter(AgentModelSetting.agent_key == agent_key)
            .first()
        )
        if not linha:
            linha = AgentModelSetting(agent_key=agent_key, model=model, effort=effort)
            session.add(linha)
        else:
            linha.model = model
            linha.effort = effort
            linha.updated_at = brt_now()
        session.commit()


# ---------------------------------------------------------------------------
# Integração de conta — linha única (id=1)
# ---------------------------------------------------------------------------


def ensure_integration_settings() -> None:
    """Cria a linha única, semeada do `.env` atual, se ainda não existir."""
    with rx.session() as session:
        linha = session.query(IntegrationSetting).first()
        if not linha:
            session.add(
                IntegrationSetting(
                    graph_sender_email=os.environ.get("GRAPH_SENDER_EMAIL", ""),
                    graph_tenant_id=os.environ.get("GRAPH_TENANT_ID", ""),
                    graph_client_id=os.environ.get("GRAPH_CLIENT_ID", ""),
                    graph_client_secret_enc=crypto.encrypt(os.environ.get("GRAPH_CLIENT_SECRET", "")),
                )
            )
            session.commit()


def _linha_integracao(session) -> Optional[IntegrationSetting]:
    return session.query(IntegrationSetting).first()


def get_graph_config() -> dict:
    """Credenciais da Microsoft Graph, para envio e para leitura da caixa.

    `tenant_id` aqui é o Directory (tenant) ID do Entra ID — nada a ver com o
    `tenant_id` da aplicação. O client secret sai decriptografado: use apenas
    dentro de `services/graph_mailer.py` e `services/graph_client.py`, NUNCA
    para popular um campo de State (State é serializado para o browser).
    """
    with rx.session() as session:
        linha = _linha_integracao(session)
        if not linha:
            return {
                "sender_email": "",
                "tenant_id": "",
                "client_id": "",
                "client_secret": "",
                "pasta_origem": "inbox",
            }
        return {
            "sender_email": linha.graph_sender_email,
            "tenant_id": linha.graph_tenant_id,
            "client_id": linha.graph_client_id,
            "client_secret": crypto.decrypt(linha.graph_client_secret_enc),
            # Nome bem-conhecido (`inbox`) ou id de pasta. `inbox` resolve a
            # Caixa de Entrada em qualquer idioma do locatário, o que um
            # displayName não faz.
            "pasta_origem": (linha.graph_pasta_origem or "inbox"),
        }


def salvar_integration_settings(**campos) -> None:
    """Só sobrescreve os campos passados (chave ausente = não mexe).

    O campo secreto (`graph_client_secret`) é criptografado antes de gravar.
    Quem decide "não digitou nada, não mexe" é o chamador (SettingsState): esta
    função só grava o que recebeu, e receber string vazia aqui APAGA o segredo.
    """
    campos_secretos = {
        "graph_client_secret": "graph_client_secret_enc",
    }
    with rx.session() as session:
        linha = _linha_integracao(session)
        if not linha:
            linha = IntegrationSetting()
            session.add(linha)

        for chave, valor in campos.items():
            if chave in campos_secretos:
                setattr(linha, campos_secretos[chave], crypto.encrypt(valor))
            else:
                setattr(linha, chave, valor)
        linha.updated_at = brt_now()
        session.commit()
