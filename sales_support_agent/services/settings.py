"""Fonte única de configuração dos agentes de IA (modelo + reasoning effort) e
das integrações de conta (e-mail via Microsoft Graph, KipFlow e Hunter.io).

A Hunter é a única com mais de uma credencial: as chaves ficam em
`HunterAccount` (uma linha por conta, até `HUNTER_MAX_CONTAS`) e não em
`IntegrationSetting`, que guarda só o que vale para todas elas — o teto de
créditos por conta e o dia da renovação.

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

Segredos (client secret do Graph, chaves KipFlow e Hunter) são sempre gravados/lidos via
`services/crypto.py` (Fernet) — nunca em texto puro no banco, e as funções aqui
nunca devem ser usadas para popular um campo de State com o valor
decriptografado (State é serializado para o browser).
"""
import os
from typing import Optional

import reflex as rx

from prospect_agent.models import (
    AgentModelSetting,
    HunterAccount,
    IntegrationSetting,
    brt_now,
)
from prospect_agent.services import crypto

# ---------------------------------------------------------------------------
# Agentes de IA — modelo + reasoning effort
# ---------------------------------------------------------------------------
AGENT_KEYS = ("product", "prospect", "priorizacao", "insights")

# Modelos oferecidos no dropdown: restritos aos que aceitam reasoning effort,
# para os 4 agentes terem a mesma UI (modelo + esforço) sem caso especial.
#
# ATENÇÃO ao trocar esta tupla: `ensure_agent_settings()` nunca sobrescreve uma
# linha existente, então as 4 linhas já gravadas continuam apontando para o
# modelo ANTIGO — que some do dropdown e deixa o select do `/admin` sem valor
# correspondente. Toda troca aqui exige um UPDATE nas linhas de
# `AgentModelSetting` (ver `scripts/migrar_modelos.py`).
MODELOS_DISPONIVEIS = ("gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano")
EFFORTS_DISPONIVEIS = ("minimal", "low", "medium", "high")

# Semente: nome do agente -> (env var do modelo, default, effort fixo hoje).
_SEMENTE_AGENTES = {
    "product": ("OPENAI_MODEL", "gpt-5.4-mini", "low"),
    "prospect": ("OPENAI_SEARCH_MODEL", "gpt-5.4-mini", "high"),
    "priorizacao": ("OPENAI_PRIORIZACAO_MODEL", "gpt-5.4-mini", "low"),
    "insights": ("OPENAI_INSIGHTS_MODEL", "gpt-5.4-mini", "low"),
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
# Integrações de conta — linha única (id=1)
# ---------------------------------------------------------------------------
# Teto de créditos/ciclo de CADA conta do Hunter na primeira execução: 50 é o
# plano gratuito. É só a SEMENTE — a partir daí o valor vem do banco, editável
# em `/admin`, porque migrar para um plano pago não pode exigir alterar código.
HUNTER_CREDITOS_MENSAIS_PADRAO = 50

# Quantas contas da Hunter a plataforma aceita balancear. O teto existe para a
# UI do super admin ter um número fixo de campos e para o balanceador nunca
# varrer uma lista aberta; subir esse número é seguro (nada além da UI depende
# dele), descer exige checar se algum slot acima do novo teto está em uso.
HUNTER_MAX_CONTAS = 8

# Dia da renovação na primeira execução. 1 é só um valor neutro para não
# inventar uma data de assinatura: o número certo é o dia em que a conta do
# Hunter foi criada, e quem sabe isso é o super admin, em `/admin`.
HUNTER_DIA_RENOVACAO_PADRAO = 1


def _inteiro(valor: Optional[str], padrao: int) -> int:
    try:
        return max(0, int(str(valor).strip()))
    except (TypeError, ValueError):
        return padrao


def ensure_integration_settings() -> None:
    """Cria a linha única, semeada do `.env` atual, se ainda não existir.

    Também garante a primeira conta da Hunter (ver `ensure_hunter_accounts`),
    porque as duas coisas são a mesma configuração para quem instala.
    """
    with rx.session() as session:
        linha = session.query(IntegrationSetting).first()
        if not linha:
            session.add(
                IntegrationSetting(
                    graph_sender_email=os.environ.get("GRAPH_SENDER_EMAIL", ""),
                    graph_tenant_id=os.environ.get("GRAPH_TENANT_ID", ""),
                    graph_client_id=os.environ.get("GRAPH_CLIENT_ID", ""),
                    graph_client_secret_enc=crypto.encrypt(os.environ.get("GRAPH_CLIENT_SECRET", "")),
                    kipflow_api_key_enc=crypto.encrypt(os.environ.get("KIPFLOW_API_KEY", "")),
                    kipflow_base_url=os.environ.get("KIPFLOW_BASE_URL", "https://api.kipflow.io"),
                    hunter_creditos_mensais=_inteiro(
                        os.environ.get("HUNTER_CREDITOS_MENSAIS"), HUNTER_CREDITOS_MENSAIS_PADRAO
                    ),
                    hunter_dia_renovacao=_inteiro(
                        os.environ.get("HUNTER_DIA_RENOVACAO"), HUNTER_DIA_RENOVACAO_PADRAO
                    ),
                )
            )
            session.commit()
    ensure_hunter_accounts()


# ---------------------------------------------------------------------------
# Contas da Hunter — até HUNTER_MAX_CONTAS, balanceadas na busca de e-mail
# ---------------------------------------------------------------------------
def ensure_hunter_accounts() -> None:
    """Semeia a conta do slot 1 com `HUNTER_API_KEY` do `.env`, se não houver
    NENHUMA conta cadastrada.

    Só age na base sem conta alguma: uma vez que o super admin cadastrou as
    contas em `/admin`, o `.env` deixa de ser consultado, como em todo o resto
    deste módulo. Sem essa semente, quem instala com uma chave no `.env` subiria
    o app com a busca de e-mails desligada e sem pista do motivo.
    """
    chave = os.environ.get("HUNTER_API_KEY", "").strip()
    if not chave:
        return
    with rx.session() as session:
        if session.query(HunterAccount).first():
            return
        session.add(HunterAccount(slot=1, api_key_enc=crypto.encrypt(chave)))
        session.commit()


def slots_hunter_configurados() -> list:
    """Slots com chave gravada, em ordem. É o que a UI pode saber sobre as
    contas: o valor da chave nunca sai daqui para o State."""
    with rx.session() as session:
        linhas = session.query(HunterAccount).order_by(HunterAccount.slot).all()
        return [c.slot for c in linhas if c.api_key_enc and crypto.decrypt(c.api_key_enc)]


def get_hunter_accounts() -> list:
    """[(slot, chave decriptografada)] das contas configuradas, em ordem de slot.

    Uso restrito a `services/hunter_client.py`. NUNCA para popular State.
    """
    with rx.session() as session:
        linhas = session.query(HunterAccount).order_by(HunterAccount.slot).all()
    contas = []
    for c in linhas:
        chave = crypto.decrypt(c.api_key_enc)
        if chave:
            contas.append((c.slot, chave))
    return contas


def salvar_hunter_account(slot: int, api_key: str) -> None:
    """Grava (ou substitui) a chave de um slot. Chave em branco é ignorada —
    quem quer remover a conta chama `remover_hunter_account`, para que um campo
    esquecido em branco nunca apague uma credencial em uso."""
    api_key = (api_key or "").strip()
    if not api_key:
        return
    with rx.session() as session:
        linha = session.query(HunterAccount).filter(HunterAccount.slot == slot).first()
        if linha:
            linha.api_key_enc = crypto.encrypt(api_key)
            linha.updated_at = brt_now()
        else:
            session.add(HunterAccount(slot=slot, api_key_enc=crypto.encrypt(api_key)))
        session.commit()


def remover_hunter_account(slot: int) -> None:
    """Apaga a credencial do slot. O consumo já registrado em `HunterUsage`
    permanece: ele é histórico de custo, não configuração."""
    with rx.session() as session:
        linha = session.query(HunterAccount).filter(HunterAccount.slot == slot).first()
        if linha:
            session.delete(linha)
            session.commit()


def _linha_integracao(session) -> Optional[IntegrationSetting]:
    return session.query(IntegrationSetting).first()


def get_graph_config() -> dict:
    """Credenciais da Microsoft Graph para envio de e-mail.

    `tenant_id` aqui é o Directory (tenant) ID do Entra ID — nada a ver com o
    `tenant_id` da aplicação. O client secret sai decriptografado: use apenas
    dentro de `services/graph_mailer.py`, NUNCA para popular um campo de State.
    """
    with rx.session() as session:
        linha = _linha_integracao(session)
        if not linha:
            return {"sender_email": "", "tenant_id": "", "client_id": "", "client_secret": ""}
        return {
            "sender_email": linha.graph_sender_email,
            "tenant_id": linha.graph_tenant_id,
            "client_id": linha.graph_client_id,
            "client_secret": crypto.decrypt(linha.graph_client_secret_enc),
        }


def get_kipflow_api_key() -> str:
    with rx.session() as session:
        linha = _linha_integracao(session)
        return crypto.decrypt(linha.kipflow_api_key_enc) if linha else ""


def get_kipflow_base_url() -> str:
    with rx.session() as session:
        linha = _linha_integracao(session)
        return (linha.kipflow_base_url if linha else "https://api.kipflow.io").rstrip("/")


def get_hunter_creditos_mensais() -> int:
    """Teto de créditos por ciclo DE UMA conta. O orçamento do ciclo inteiro é
    este número vezes as contas configuradas — ver `get_hunter_creditos_totais`.
    Usado pelo gate de cota em `services/hunter_client`."""
    with rx.session() as session:
        linha = _linha_integracao(session)
        if not linha:
            return HUNTER_CREDITOS_MENSAIS_PADRAO
        return max(0, int(linha.hunter_creditos_mensais or 0))


def get_hunter_creditos_totais() -> int:
    """Orçamento do ciclo somando todas as contas configuradas.

    É o número que o painel exibe e o que responde "quanto ainda dá para
    buscar": acrescentar uma conta ao balanceador aumenta a cota da plataforma
    sem trocar de plano.
    """
    return get_hunter_creditos_mensais() * len(slots_hunter_configurados())


def get_hunter_dia_renovacao() -> int:
    """Dia do mês em que o Hunter renova os créditos (aniversário da
    assinatura). Define a janela contada em `hunter_client.inicio_do_ciclo`."""
    with rx.session() as session:
        linha = _linha_integracao(session)
        if not linha:
            return HUNTER_DIA_RENOVACAO_PADRAO
        return max(1, min(int(linha.hunter_dia_renovacao or 1), 31))


def salvar_integration_settings(**campos) -> None:
    """Só sobrescreve os campos passados (chave ausente = não mexe). Campos
    secretos (graph_client_secret, kipflow_api_key) são criptografados antes de
    gravar; string vazia para um desses é ignorada (mantém o valor atual) —
    quem decide "não digitou nada, não mexe" é o chamador (SettingsState), esta
    função só grava o que recebeu.
    """
    campos_secretos = {
        "graph_client_secret": "graph_client_secret_enc",
        "kipflow_api_key": "kipflow_api_key_enc",
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
