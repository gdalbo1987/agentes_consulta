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
3. **Advisory lock de TRANSAÇÃO do PostgreSQL** (`pg_try_advisory_xact_lock`),
   na hora de reivindicar a rodada. Cobre a corrida entre o botão manual e o
   job agendado, e também o cenário de mais de um worker, em que cada um
   levantaria o seu agendador. É mais forte que a flag `is_running` do State,
   que existe por sessão de browser e é invisível para a CLI.

   De TRANSAÇÃO, e não de sessão: a variante de sessão prende o lock à conexão
   e exige unlock explícito na mesma conexão, coisa que um pool não garante.
   Ver `reivindicar_rodada`, que documenta a falha exata que isso causou.

O lock morre com a transação, então ele não substitui a recuperação de rodada
travada de 20 minutos: um processo morto no meio do caminho solta o lock mas
deixa a linha em `running`.
"""

import asyncio
import logging
from typing import Optional

import reflex as rx

from sales_support_agent.models import ClassificacaoRun, brt_now
from sales_support_agent.services import classificacao, classificacao_config

log = logging.getLogger("sales_support_agent.agendador")

# Um por tenant, derivado de um número fixo. O lock consultivo recebe um
# bigint; usar um literal mais o tenant evita colisão com qualquer outro lock
# que o PostgreSQL venha a hospedar.
_LOCK_BASE = 815_000_000

TENANT_PADRAO = 1

_scheduler = None
_JOBS = ("classificacao_h1", "classificacao_h2")
_JOB_SUPERVISOR = "classificacao_supervisor"

# De quanto em quanto tempo o supervisor acorda. Ele não é um terceiro horário:
# `slot_devido` continua sendo quem decide, e um horário já rodado hoje segue
# recusado. O supervisor existe por dois motivos.
#
# RECUPERAÇÃO. Depender só dos dois gatilhos exatos torna a operação frágil de
# um jeito que não aparece em teste: processo reiniciado às 06:59 perde o
# gatilho das 07:00, `misfire_grace_time` vence, a máquina hiberna, o relógio
# do host anda. Em qualquer desses casos o horário fica perdido até o dia
# seguinte. Com o supervisor, ele é recuperado na próxima passagem.
#
# BATIMENTO. Ele grava `ultimo_tick_em` a cada passagem, então o painel
# consegue distinguir "não rodou porque está parado" de "não rodou porque o
# processo morreu". Sem isso, os dois são o mesmo silêncio na tela.
_SUPERVISOR_MINUTOS = 10


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

    O LOCK É DE TRANSAÇÃO (`pg_try_advisory_xact_lock`), e NUNCA de sessão.
    Esta distinção já custou a operação: com `pg_try_advisory_lock`, o lock
    pertence à CONEXÃO, e soltá-lo exige um `pg_advisory_unlock` na mesma
    conexão. Só que o `session.commit()` daqui devolve a conexão ao pool, e a
    instrução seguinte pega outra: durante uma rodada manual, com o progresso
    gravando a cada e-mail e o painel sondando a cada 3 segundos, o pool está
    em uso e a chance de voltar a mesma conexão é baixa. O unlock ia para a
    conexão errada, devolvia `false` em silêncio, e o lock ficava preso na
    conexão original pelo resto da vida dela.

    O efeito era o agendamento morrer depois de qualquer execução manual: toda
    tentativa seguinte via `pg_try_advisory_lock` devolver `false`, concluía
    "já existe uma classificação em andamento" e saía, para sempre, até
    reiniciar o servidor. O botão manual travava junto, pelo mesmo motivo.

    O lock de transação não tem como vazar: o PostgreSQL o solta no COMMIT ou
    no ROLLBACK, aconteça o que acontecer com o pool. E ele solta exatamente no
    instante em que a linha nova fica visível para as outras transações, então
    não existe janela em que o lock esteja livre e a rodada ainda invisível.
    """
    classificacao.recuperar_rodada_travada(tenant_id)

    with rx.session() as session:
        if _e_postgres(session):
            from sqlalchemy import text

            obteve = session.execute(
                text("SELECT pg_try_advisory_xact_lock(:chave)"),
                {"chave": _LOCK_BASE + tenant_id},
            ).scalar()
            if not obteve:
                log.info("Outra execução já detém o lock; esta sai sem fazer nada.")
                return None

        em_andamento = (
            session.query(ClassificacaoRun)
            .filter(
                ClassificacaoRun.tenant_id == tenant_id,
                ClassificacaoRun.status == "running",
            )
            .first()
        )
        if em_andamento:
            # Sem commit: o rollback do fechamento solta o lock sozinho.
            return None

        rodada = ClassificacaoRun(
            tenant_id=tenant_id, origem=origem, slot=slot, user_email=user_email
        )
        session.add(rodada)
        session.commit()
        session.refresh(rodada)
        return rodada.id


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
    """Verifica se algum horário está vencido e, se estiver, roda.

    TODA passagem grava o que decidiu, inclusive a que não faz nada. Este era o
    buraco que derrubou a operação: o agendador funcionava, decidia
    corretamente não rodar porque o interruptor estava desligado, e saía em
    silêncio. Da tela, "está parado", "deu erro" e "o processo morreu" ficavam
    idênticos, e a caixa passou dias sem classificar sem ninguém saber por quê.
    """
    try:
        cfg = classificacao_config.get_config(tenant_id)

        if not cfg.get("ativo"):
            classificacao_config.registrar_tick(
                tenant_id,
                "Não executou: as execuções automáticas estão paradas. "
                "Use o botão Iniciar.",
            )
            return

        slot = classificacao_config.slot_devido(brt_now(), cfg)
        if not slot:
            classificacao_config.registrar_tick(
                tenant_id, "Nada a fazer: nenhum horário vencido no momento."
            )
            return

        log.info("Horário %s vencido; iniciando a classificação agendada.", slot)

        # A marca do slot vem DEPOIS da reivindicação, e não antes. Marcando
        # antes, uma rodada que nem chegasse a começar (outra em andamento, ou
        # falha ao criar a linha) consumiria o horário do dia assim mesmo, e o
        # agente ficaria em silêncio até o dia seguinte por causa de um erro que
        # ninguém viu.
        resultado = await executar(tenant_id, origem="agendado", slot=slot)

        if resultado.get("pulou"):
            classificacao_config.registrar_tick(
                tenant_id,
                f"Horário {slot} vencido, mas não rodou: {resultado.get('motivo', '')}.",
            )
            return

        classificacao_config.marcar_execucao_agendada(tenant_id)

        if resultado.get("erro"):
            classificacao_config.registrar_tick(
                tenant_id, f"Horário {slot}: a execução terminou com erro. {resultado['erro']}"
            )
            return

        r = resultado.get("resumo") or {}
        classificacao_config.registrar_tick(
            tenant_id,
            f"Horário {slot} executado: {r.get('classificados', 0)} classificado(s), "
            f"{r.get('ignorados', 0)} ignorado(s), {r.get('puladas', 0)} já conhecido(s), "
            f"{r.get('falhas', 0)} falha(s).",
        )

    except Exception as exc:  # noqa: BLE001
        # Uma exceção que escapasse daqui subiria para o APScheduler, que a
        # engole num log que ninguém lê. Pior: com `max_instances=1`, um job
        # que morre de forma estranha pode deixar o agendador sem executar de
        # novo, e o sintoma na tela é o mesmo silêncio de antes.
        log.exception("Falha inesperada no tick do agendador.")
        try:
            classificacao_config.registrar_tick(
                tenant_id, f"Falha inesperada na verificação automática: {exc}"
            )
        except Exception:  # noqa: BLE001 - registrar não pode derrubar o tick
            pass


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

    # O supervisor não depende dos horários, então fica fora do `reprogramar`:
    # ele precisa continuar batendo mesmo que os dois horários estejam
    # inválidos e nenhum job de cron tenha sido criado. É exatamente nesse
    # cenário que o painel mais precisa de sinal de vida.
    from apscheduler.triggers.interval import IntervalTrigger

    _scheduler.add_job(
        _tick,
        IntervalTrigger(minutes=_SUPERVISOR_MINUTOS),
        id=_JOB_SUPERVISOR,
        replace_existing=True,
        kwargs={"tenant_id": tenant_id},
        misfire_grace_time=_SUPERVISOR_MINUTOS * 60,
        coalesce=True,
        max_instances=1,
    )

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
