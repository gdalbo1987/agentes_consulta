"""As duas execuções diárias automáticas da classificação.

Um `AsyncIOScheduler` único, levantado pelo LIFESPAN do ASGI
(`app.register_lifespan_task` em `sales_support_agent.py`), e não em tempo de
import. A distinção é o que impede um `reflex compile` ou um
`reflex db makemigrations` de subir agendador sem querer: os dois importam o
módulo da aplicação, mas nenhum deles serve requisição, então o lifespan não
roda.

Três defesas contra disparo duplicado, porque nenhuma sozinha basta:

1. **`slot_devido`**, em `classificacao_config`, decide se o horário já rodou
   HOJE, consultando `ultima_execucao_agendada`. É função pura, testável sem
   mexer no relógio.
2. **Verificação no startup**, além do gatilho no horário. É o que dá
   recuperação de graça: se a máquina estava desligada às 08:00 e o servidor
   sobe às 09:20, o horário 1 ainda consta como não disparado e a rodada
   acontece. Um gatilho que só olha o instante exato teria perdido a janela.
3. **Advisory lock do PostgreSQL** (`pg_try_advisory_lock`), na hora de
   reivindicar a rodada. Cobre a corrida entre o botão manual e o job agendado,
   e também o cenário de mais de um worker, em que cada um levantaria o seu
   agendador. É mais forte que a flag `is_running` do State, que existe por
   sessão de browser e é invisível para a CLI.

O advisory lock morre com a conexão, então ele não substitui a recuperação de
rodada travada de 20 minutos: um processo morto no meio do caminho solta o lock
mas deixa a linha em `running`.
"""

import asyncio
import logging
from typing import Optional

import reflex as rx

from sales_support_agent.models import ClassificacaoRun, brt_now
from sales_support_agent.services import classificacao, classificacao_config

log = logging.getLogger("sales_support_agent.agendador")

# Um por tenant, derivado de um número fixo. `pg_try_advisory_lock` recebe um
# bigint; usar um literal mais o tenant evita colisão com qualquer outro lock
# que o PostgreSQL venha a hospedar.
_LOCK_BASE = 815_000_000

TENANT_PADRAO = 1

_scheduler = None
_JOBS = ("classificacao_h1", "classificacao_h2")


# ---------------------------------------------------------------------------
# Reivindicação da rodada
# ---------------------------------------------------------------------------


def _e_postgres(session) -> bool:
    return session.get_bind().dialect.name == "postgresql"


def reivindicar_rodada(
    tenant_id: int, *, origem: str, slot: str = "", user_email: str = ""
) -> Optional[int]:
    """Cria a linha da rodada, se ninguém mais estiver executando.

    Devolve o `id` da rodada, ou `None` quando outra já está em andamento.

    A checagem e a criação acontecem na MESMA transação, protegidas pelo
    advisory lock: sem isso, duas invocações simultâneas leriam "nenhuma em
    andamento" e criariam duas rodadas, que classificariam os mesmos e-mails em
    paralelo e brigariam pelo mesmo id no Graph.

    Em SQLite (a suíte de testes) não há advisory lock; a checagem de rodada em
    andamento continua valendo, e a suíte é de processo único.
    """
    classificacao.recuperar_rodada_travada(tenant_id)

    with rx.session() as session:
        if _e_postgres(session):
            from sqlalchemy import text

            obteve = session.execute(
                text("SELECT pg_try_advisory_lock(:chave)"), {"chave": _LOCK_BASE + tenant_id}
            ).scalar()
            if not obteve:
                log.info("Outra execução já detém o lock; esta sai sem fazer nada.")
                return None

        try:
            em_andamento = (
                session.query(ClassificacaoRun)
                .filter(
                    ClassificacaoRun.tenant_id == tenant_id,
                    ClassificacaoRun.status == "running",
                )
                .first()
            )
            if em_andamento:
                return None

            rodada = ClassificacaoRun(
                tenant_id=tenant_id, origem=origem, slot=slot, user_email=user_email
            )
            session.add(rodada)
            session.commit()
            session.refresh(rodada)
            return rodada.id
        finally:
            if _e_postgres(session):
                from sqlalchemy import text

                session.execute(
                    text("SELECT pg_advisory_unlock(:chave)"), {"chave": _LOCK_BASE + tenant_id}
                )
                session.commit()


def finalizar_rodada(run_id: int, resumo: dict, erro: str = "") -> None:
    """Fecha a rodada, gravando contadores, duração e o consumo de tokens.

    O `TokenUsage` de cada agente entra no MESMO commit dos contadores: ou a
    rodada e o custo dela são registrados juntos, ou nenhum dos dois.
    """
    from sales_support_agent.models import ActivityLog, TokenUsage

    with rx.session() as session:
        rodada = session.get(ClassificacaoRun, run_id)
        if not rodada:
            return

        rodada.finished_at = brt_now()
        if rodada.started_at:
            rodada.duracao_segundos = int((rodada.finished_at - rodada.started_at).total_seconds())

        if erro:
            rodada.status = "error"
            rodada.erro = erro
        else:
            import json

            rodada.status = "done"
            for campo in (
                "total_emails", "processados", "classificados", "ignorados",
                "urgentes", "resumidos", "puladas", "falhas",
            ):
                setattr(rodada, campo, resumo.get(campo, 0))
            rodada.avisos = json.dumps(resumo.get("avisos", []), ensure_ascii=False)

            for agente, chave in (
                ("classificacao_agent", "usage_classificacao"),
                ("resumo_agent", "usage_resumo"),
            ):
                uso = resumo.get(chave) or {}
                if uso.get("input") or uso.get("output"):
                    from sales_support_agent.state import modelo_do_agente

                    session.add(
                        TokenUsage(
                            tenant_id=rodada.tenant_id,
                            agent_name=agente,
                            model=modelo_do_agente(agente),
                            input_tokens=uso.get("input", 0),
                            output_tokens=uso.get("output", 0),
                        )
                    )

        session.add(
            ActivityLog(
                tenant_id=rodada.tenant_id,
                user_email=rodada.user_email or "(agendado)",
                action="CLASSIFICACAO",
                details=(
                    f"Execução {rodada.origem} encerrada como {rodada.status}: "
                    f"{rodada.classificados} classificado(s), {rodada.ignorados} ignorado(s), "
                    f"{rodada.puladas} já conhecido(s), {rodada.falhas} falha(s)."
                ),
            )
        )
        session.commit()


# ---------------------------------------------------------------------------
# Execução
# ---------------------------------------------------------------------------


async def executar(tenant_id: int, *, origem: str, slot: str = "", user_email: str = "") -> dict:
    """Uma rodada completa, do jeito headless. Devolve o resumo.

    É o MESMO `stream_classificacao` que o botão do dashboard consome; a
    diferença é só quem lê os eventos. Um caminho de código só é o que faz o
    botão manual ser um teste de verdade do caminho agendado.
    """
    run_id = reivindicar_rodada(tenant_id, origem=origem, slot=slot, user_email=user_email)
    if run_id is None:
        return {"pulou": True, "motivo": "já existe uma classificação em andamento"}

    resumo, erro = None, ""
    try:
        async for evento in classificacao.stream_classificacao(
            tenant_id, user_email=user_email, origem=origem, run_id=run_id
        ):
            if evento[0] == "done":
                resumo = evento[1]
            elif evento[0] == "error":
                erro = evento[1]
            elif evento[0] == "progress":
                log.info("[%s/%s] %s", evento[1], evento[2], evento[3])
    except Exception as exc:  # noqa: BLE001 - a rodada não pode ficar em running
        erro = f"Falha inesperada na execução: {exc}"

    finalizar_rodada(run_id, resumo or {}, erro)
    return {"pulou": False, "run_id": run_id, "resumo": resumo, "erro": erro}


async def _tick(tenant_id: int = TENANT_PADRAO) -> None:
    """Verifica se algum horário está vencido e, se estiver, roda."""
    cfg = classificacao_config.get_config(tenant_id)
    slot = classificacao_config.slot_devido(brt_now(), cfg)
    if not slot:
        return

    log.info("Horário %s vencido; iniciando a classificação agendada.", slot)
    classificacao_config.marcar_execucao_agendada(tenant_id)
    await executar(tenant_id, origem="agendado", slot=slot)


# ---------------------------------------------------------------------------
# Ciclo de vida do agendador
# ---------------------------------------------------------------------------


def _cron(hhmm: str):
    from apscheduler.triggers.cron import CronTrigger

    horas, minutos = (int(p) for p in hhmm.split(":"))
    return CronTrigger(hour=horas, minute=minutos)


def reprogramar(tenant_id: int = TENANT_PADRAO) -> None:
    """Relê os horários do banco e substitui os dois jobs.

    Chamado pelo handler que salva a configuração, para que mudar o horário no
    dashboard valha imediatamente, sem reiniciar o servidor.
    """
    if _scheduler is None:
        return

    cfg = classificacao_config.get_config(tenant_id)
    for job_id, chave in zip(_JOBS, ("horario_1", "horario_2")):
        hhmm = (cfg.get(chave) or "").strip()
        try:
            gatilho = _cron(hhmm)
        except (ValueError, AttributeError):
            log.warning("Horário inválido em %s: %r. O job %s não foi criado.", chave, hhmm, job_id)
            continue
        _scheduler.add_job(
            _tick, gatilho, id=job_id, replace_existing=True, kwargs={"tenant_id": tenant_id},
            misfire_grace_time=3600, coalesce=True, max_instances=1,
        )
    log.info("Agendador reprogramado para %s e %s.", cfg.get("horario_1"), cfg.get("horario_2"))


async def iniciar(tenant_id: int = TENANT_PADRAO) -> None:
    """Sobe o agendador. Registrado como lifespan task da aplicação."""
    global _scheduler

    if _scheduler is not None:
        return

    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    _scheduler = AsyncIOScheduler()
    _scheduler.start()
    reprogramar(tenant_id)

    # Verificação de partida: recupera o horário perdido enquanto a máquina
    # estava desligada. Em tarefa separada para não segurar o startup do ASGI
    # atrás de uma rodada que pode levar minutos.
    asyncio.create_task(_tick(tenant_id))
    log.info("Agendador da classificação iniciado.")


def parar() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
