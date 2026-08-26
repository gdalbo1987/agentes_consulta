"""O agendador: qual horário está vencido e quem pode reivindicar a rodada.

`slot_devido` é função pura, então dá para testar exaustivamente sem mexer no
relógio da máquina. É deliberado: a regra "já rodou hoje?" é exatamente o tipo
de coisa que, testada só com o relógio de verdade, passa em desenvolvimento e
falha na virada do dia.
"""

from datetime import datetime, timedelta

import pytest

from sales_support_agent.models import ClassificacaoRun
from sales_support_agent.services import agendador, classificacao, classificacao_config

HOJE = datetime(2026, 8, 7)


def _cfg(h1="08:00", h2="16:00", ultima=None, ativo=True):
    return {
        "horario_1": h1, "horario_2": h2, "ativo": ativo,
        "ultima_execucao_agendada": ultima,
        "janela_urgencia_horas": 24, "lookback_horas": 48, "max_emails_por_execucao": 200,
    }


def _em(hora, minuto=0):
    return HOJE.replace(hour=hora, minute=minuto)


# ---------------------------------------------------------------------------
# slot_devido
# ---------------------------------------------------------------------------


def test_antes_do_primeiro_horario_nao_ha_nada_a_fazer():
    assert classificacao_config.slot_devido(_em(7, 59), _cfg()) is None


def test_no_horario_o_slot_vence():
    assert classificacao_config.slot_devido(_em(8, 0), _cfg()) == "h1"
    assert classificacao_config.slot_devido(_em(16, 0), _cfg()) == "h2"


def test_ja_tendo_rodado_depois_do_horario_nao_roda_de_novo():
    """A trava contra disparo duplo dentro do mesmo dia."""
    cfg = _cfg(ultima=_em(8, 5))
    assert classificacao_config.slot_devido(_em(9, 0), cfg) is None


def test_maquina_desligada_no_horario_recupera_ao_ligar():
    """O motivo de a decisão não ser "disparar no instante exato".

    Se a máquina estava desligada às 08:00 e o servidor sobe às 09:20, o
    horário 1 ainda consta como não disparado hoje e a rodada acontece. Um
    gatilho preso ao instante teria perdido a janela em silêncio.
    """
    assert classificacao_config.slot_devido(_em(9, 20), _cfg()) == "h1"


def test_com_os_dois_vencidos_roda_o_mais_tarde():
    """O horário mais tarde varre uma janela maior e cobre o que o outro pegaria."""
    assert classificacao_config.slot_devido(_em(18, 0), _cfg()) == "h2"


def test_a_virada_do_dia_libera_os_horarios_de_novo():
    ontem = _em(16, 5) - timedelta(days=1)
    assert classificacao_config.slot_devido(_em(8, 0), _cfg(ultima=ontem)) == "h1"


def test_desligado_nunca_dispara():
    assert classificacao_config.slot_devido(_em(18, 0), _cfg(ativo=False)) is None


def test_horario_invalido_e_ignorado_sem_derrubar_o_agendador():
    """Configuração ruim não pode impedir o OUTRO horário de funcionar."""
    assert classificacao_config.slot_devido(_em(18, 0), _cfg(h1="vinte e cinco horas")) == "h2"
    assert classificacao_config.slot_devido(_em(18, 0), _cfg(h1="99:99", h2="")) is None


def test_proxima_execucao_atravessa_a_meia_noite():
    assert classificacao_config.proxima_execucao(_em(18, 0), _cfg()) == _em(8, 0) + timedelta(days=1)
    assert classificacao_config.proxima_execucao(_em(9, 0), _cfg()) == _em(16, 0)


# ---------------------------------------------------------------------------
# Reivindicação da rodada
# ---------------------------------------------------------------------------


@pytest.fixture
def banco_agendador(engine, monkeypatch):
    from sqlmodel import Session

    import reflex as rx

    from sales_support_agent.models import Tenant

    monkeypatch.setattr(rx, "session", lambda *a, **k: Session(engine))
    with Session(engine) as s:
        if not s.get(Tenant, 1):
            s.add(Tenant(id=1, name="Coester"))
            s.commit()

    # `finalizar_rodada` grava TokenUsage e ActivityLog junto com a rodada, então
    # os três entram na limpeza. Sem isso, um teste que conta logs veria também
    # os dos testes anteriores, já que estes comitam num banco compartilhado.
    # `ClassificacaoConfig` entra na limpeza porque os testes de autoria
    # gravam nela. Sem isso, um horário deixado aqui apareceria como estado
    # inicial de outro arquivo da suíte, que compartilha o mesmo banco.
    from sales_support_agent.models import ActivityLog, ClassificacaoConfig, TokenUsage

    def _limpar():
        with Session(engine) as s:
            for modelo in (TokenUsage, ActivityLog, ClassificacaoRun, ClassificacaoConfig):
                for linha in s.query(modelo).all():
                    s.delete(linha)
            s.commit()

    _limpar()
    yield engine
    _limpar()


def test_a_segunda_reivindicacao_simultanea_sai_de_maos_vazias(banco_agendador):
    """Botão manual e job agendado não podem criar duas rodadas.

    Duas rodadas em paralelo classificariam os mesmos e-mails e brigariam pelo
    mesmo id no Graph, com uma movendo a mensagem que a outra ainda vai marcar.
    """
    primeira = agendador.reivindicar_rodada(1, origem="manual", user_email="a@b.com")
    segunda = agendador.reivindicar_rodada(1, origem="agendado", slot="h1")

    assert primeira is not None
    assert segunda is None

    from sqlmodel import Session

    with Session(banco_agendador) as s:
        assert s.query(ClassificacaoRun).count() == 1


def test_depois_de_finalizada_uma_nova_rodada_pode_comecar(banco_agendador):
    from sqlmodel import Session

    primeira = agendador.reivindicar_rodada(1, origem="manual")
    agendador.finalizar_rodada(primeira, {"classificados": 3, "avisos": []})

    segunda = agendador.reivindicar_rodada(1, origem="agendado", slot="h1")
    assert segunda is not None

    with Session(banco_agendador) as s:
        fechada = s.get(ClassificacaoRun, primeira)
    assert fechada.status == "done"
    assert fechada.classificados == 3
    assert fechada.finished_at is not None
    assert fechada.duracao_segundos is not None


def test_finalizar_com_erro_marca_a_rodada_e_libera_a_proxima(banco_agendador):
    from sqlmodel import Session

    run_id = agendador.reivindicar_rodada(1, origem="manual")
    agendador.finalizar_rodada(run_id, {}, erro="Credenciais inválidas.")

    with Session(banco_agendador) as s:
        linha = s.get(ClassificacaoRun, run_id)
    assert linha.status == "error"
    assert linha.erro == "Credenciais inválidas."

    assert agendador.reivindicar_rodada(1, origem="manual") is not None


def test_rodada_travada_e_recuperada_antes_de_reivindicar(banco_agendador):
    """Um processo morto não pode bloquear a plataforma para sempre."""
    from sqlmodel import Session

    from sales_support_agent.models import brt_now
    from sales_support_agent.services.classificacao import LIMITE_TRAVADO_MINUTOS

    velha = brt_now() - timedelta(minutes=LIMITE_TRAVADO_MINUTOS + 1)
    with Session(banco_agendador) as s:
        s.add(ClassificacaoRun(tenant_id=1, status="running", started_at=velha))
        s.commit()

    assert agendador.reivindicar_rodada(1, origem="agendado", slot="h1") is not None


def test_a_rodada_agendada_nao_e_atribuida_a_ninguem(banco_agendador):
    from sqlmodel import Session

    run_id = agendador.reivindicar_rodada(1, origem="agendado", slot="h2")
    with Session(banco_agendador) as s:
        linha = s.get(ClassificacaoRun, run_id)

    assert linha.user_email == ""
    assert linha.origem == "agendado"
    assert linha.slot == "h2"


def test_o_consumo_de_tokens_e_gravado_junto_com_a_rodada(banco_agendador):
    """Mesmo commit: ou a rodada e o custo dela são registrados, ou nenhum dos dois."""
    from sqlmodel import Session

    from sales_support_agent.models import ActivityLog, TokenUsage

    run_id = agendador.reivindicar_rodada(1, origem="manual", user_email="a@b.com")
    agendador.finalizar_rodada(
        run_id,
        {
            "classificados": 2, "avisos": [],
            "usage_classificacao": {"input": 100, "output": 20},
            "usage_resumo": {"input": 50, "output": 30},
        },
    )

    with Session(banco_agendador) as s:
        usos = {u.agent_name: u for u in s.query(TokenUsage).all()}
        logs = s.query(ActivityLog).all()

    assert usos["classificacao_agent"].input_tokens == 100
    assert usos["resumo_agent"].output_tokens == 30
    assert len(logs) == 1 and logs[0].action == "CLASSIFICACAO"


# ---------------------------------------------------------------------------
# Autoria da configuração
# ---------------------------------------------------------------------------


def test_a_configuracao_registra_quem_salvou(banco_agendador):
    """A configuração é da ORGANIZAÇÃO, então a tela precisa dizer de quem é.

    Todo usuário padrão edita a mesma linha. Sem a autoria, quem abre o painel
    encontra horários que não reconhece sem saber se um colega mudou.
    """
    classificacao_config.salvar_config(
        1, autor_nome="Ana Souza", autor_email="ana@coester.com.br", horario_1="09:30"
    )

    cfg = classificacao_config.get_config(1)
    assert cfg["horario_1"] == "09:30"
    assert cfg["atualizado_por_nome"] == "Ana Souza"
    assert cfg["atualizado_por_email"] == "ana@coester.com.br"


def test_a_configuracao_do_colega_e_a_que_o_proximo_usuario_ve(banco_agendador):
    """Persistir entre usuários é o ponto: a última gravação é a que vale."""
    classificacao_config.salvar_config(
        1, autor_nome="Ana", autor_email="ana@x.com", horario_1="09:00"
    )
    classificacao_config.salvar_config(
        1, autor_nome="Bruno", autor_email="bruno@x.com", horario_1="10:00"
    )

    cfg = classificacao_config.get_config(1)
    assert cfg["horario_1"] == "10:00"
    assert cfg["atualizado_por_nome"] == "Bruno"


def test_o_disparo_agendado_nao_se_passa_por_quem_configurou(banco_agendador):
    """`marcar_execucao_agendada` escreve na mesma linha, duas vezes por dia.

    Se ela empurrasse a autoria ou o carimbo, o painel diria "alterado hoje às
    16:00" depois de toda rodada automática, atribuindo a um usuário uma
    alteração que ninguém fez.
    """
    classificacao_config.salvar_config(
        1, autor_nome="Ana", autor_email="ana@x.com", horario_1="09:00"
    )
    antes = classificacao_config.get_config(1)["updated_at"]

    classificacao_config.marcar_execucao_agendada(1)

    depois = classificacao_config.get_config(1)
    assert depois["atualizado_por_nome"] == "Ana", "o agendador virou autor da config"
    assert depois["updated_at"] == antes, "o disparo automático mexeu no carimbo"
    assert depois["ultima_execucao_agendada"] is not None


# ---------------------------------------------------------------------------
# Dois usuários editando a mesma configuração
# ---------------------------------------------------------------------------


def _versao(tenant=1) -> int:
    return classificacao_config.carimbo_config(tenant)["versao"]


def test_a_versao_nao_depende_da_resolucao_do_relogio(banco_agendador):
    """Duas gravações no MESMO segundo têm de ser distinguíveis.

    `brt_now()` trunca em segundos inteiros, então o `updated_at` das duas sai
    idêntico. Uma trava baseada nele passaria batido justamente na corrida que
    ela existe para pegar, e pareceria funcionar em todo teste mais lento.
    """
    classificacao_config.salvar_config(1, autor_nome="Ana", autor_email="a@x.com")
    primeira = classificacao_config.get_config(1)

    classificacao_config.salvar_config(1, autor_nome="Bruno", autor_email="b@x.com")
    segunda = classificacao_config.get_config(1)

    assert segunda["versao"] > primeira["versao"]


def test_salvar_com_a_versao_atual_passa(banco_agendador):
    classificacao_config.salvar_config(
        1, autor_nome="Ana", autor_email="ana@x.com", horario_1="09:00"
    )
    classificacao_config.salvar_config(
        1, autor_nome="Ana", autor_email="ana@x.com",
        versao_esperada=_versao(), horario_1="09:30",
    )

    assert classificacao_config.get_config(1)["horario_1"] == "09:30"


def test_salvar_com_versao_velha_e_recusado(banco_agendador):
    """O lost update: B tem o painel aberto desde antes e salva por cima de A.

    Sem esta recusa, o horário de A voltaria ao valor que estava na tela de B,
    e a linha de autoria creditaria B por um valor que ele nunca escolheu. A
    única pista seria alguém reparar que o horário voltou sozinho.
    """
    classificacao_config.salvar_config(
        1, autor_nome="Bruno", autor_email="bruno@x.com", horario_1="08:00"
    )
    versao_de_b = _versao()  # B carregou o painel aqui

    classificacao_config.salvar_config(
        1, autor_nome="Ana", autor_email="ana@x.com", horario_1="09:00"
    )

    with pytest.raises(classificacao_config.ConfiguracaoDesatualizada) as erro:
        classificacao_config.salvar_config(
            1, autor_nome="Bruno", autor_email="bruno@x.com",
            versao_esperada=versao_de_b, horario_1="08:00",
        )

    assert "Ana" in erro.value.mensagem
    # E, o que mais importa: o que Ana gravou continua de pé.
    cfg = classificacao_config.get_config(1)
    assert cfg["horario_1"] == "09:00"
    assert cfg["atualizado_por_nome"] == "Ana"


def test_sem_versao_esperada_a_gravacao_nao_e_checada(banco_agendador):
    """Iniciar e Parar gravam um campo só e não podem ser recusados.

    Eles não têm como desfazer horário nenhum, e barrar um "Parar" por causa de
    um carimbo velho impediria alguém de frear o agente pelo motivo errado.
    """
    classificacao_config.salvar_config(
        1, autor_nome="Ana", autor_email="ana@x.com", horario_1="09:00"
    )
    classificacao_config.salvar_config(
        1, autor_nome="Bruno", autor_email="bruno@x.com", ativo=True
    )

    cfg = classificacao_config.get_config(1)
    assert cfg["ativo"] is True
    assert cfg["horario_1"] == "09:00", "o Iniciar mexeu num campo que não é dele"


def test_o_disparo_agendado_nao_invalida_a_tela_de_quem_esta_editando(banco_agendador):
    """A rodada automática não pode transformar a tela de ninguém em desatualizada.

    Ela escreve na mesma linha duas vezes por dia. Se mexesse na versão, todo
    painel aberto passaria a acusar "outra pessoa alterou" às 08:00 e às 16:00,
    e a recusa ao salvar viraria um obstáculo diário sem nenhuma alteração real
    por trás.
    """
    classificacao_config.salvar_config(
        1, autor_nome="Ana", autor_email="ana@x.com", horario_1="09:00"
    )
    versao = _versao()

    classificacao_config.marcar_execucao_agendada(1)

    assert _versao() == versao
    classificacao_config.salvar_config(
        1, autor_nome="Ana", autor_email="ana@x.com",
        versao_esperada=versao, horario_1="10:00",
    )
    assert classificacao_config.get_config(1)["horario_1"] == "10:00"


# ---------------------------------------------------------------------------
# Batimento do agendador: por que ele não rodou
# ---------------------------------------------------------------------------


async def test_o_tick_registra_que_nao_rodou_porque_esta_parado(banco_agendador):
    """A falha exata que tirou a operação do ar por dias.

    O agendador NÃO quebrou: ele decidiu corretamente não rodar, porque o
    interruptor estava desligado, e saiu em silêncio. Da tela, "está parado",
    "deu erro" e "o processo morreu" eram o mesmo nada.
    """
    classificacao_config.salvar_config(
        1, autor_nome="Ana", autor_email="a@x.com", ativo=False,
        horario_1="00:01", horario_2="00:02",
    )

    await agendador._tick(1)

    cfg = classificacao_config.get_config(1)
    assert cfg["ultimo_tick_em"] is not None, "o tick não deixou rastro nenhum"
    assert "paradas" in cfg["ultimo_tick_resultado"]
    assert "Iniciar" in cfg["ultimo_tick_resultado"], "o recado não diz o que fazer"


async def test_o_tick_registra_quando_nao_ha_horario_vencido(banco_agendador):
    """Silêncio normal também precisa de rastro.

    Sem isto, "verifiquei e não havia o que fazer" fica indistinguível de "não
    verifiquei", que é justamente a diferença entre estar tudo bem e o processo
    ter morrido.
    """
    classificacao_config.salvar_config(
        1, autor_nome="Ana", autor_email="a@x.com", ativo=True,
        horario_1="23:58", horario_2="23:59",
    )

    await agendador._tick(1)

    cfg = classificacao_config.get_config(1)
    assert cfg["ultimo_tick_em"] is not None
    assert "Nada a fazer" in cfg["ultimo_tick_resultado"]


async def test_o_tick_nao_consome_o_horario_quando_a_rodada_nem_comeca(banco_agendador):
    """Marcar o slot ANTES de rodar perdia o dia por causa de um erro invisível.

    Com outra rodada em andamento, esta sai sem fazer nada. Se o horário fosse
    marcado assim mesmo, o agente ficaria mudo até o dia seguinte, e o motivo
    não estaria em lugar nenhum.
    """
    classificacao_config.salvar_config(
        1, autor_nome="Ana", autor_email="a@x.com", ativo=True,
        horario_1="00:01", horario_2="00:02",
    )
    agendador.reivindicar_rodada(1, origem="manual")  # ocupa a vaga

    await agendador._tick(1)

    cfg = classificacao_config.get_config(1)
    assert cfg["ultima_execucao_agendada"] is None, "o horário do dia foi consumido à toa"
    assert "não rodou" in cfg["ultimo_tick_resultado"]


async def test_uma_falha_no_tick_vira_recado_e_nao_excecao_perdida(banco_agendador, monkeypatch):
    """Exceção que escapa sobe para o APScheduler e morre num log que ninguém lê."""
    classificacao_config.salvar_config(
        1, autor_nome="Ana", autor_email="a@x.com", ativo=True,
        horario_1="00:01", horario_2="00:02",
    )

    async def _explode(*a, **k):
        raise RuntimeError("a internet caiu")

    monkeypatch.setattr(agendador, "executar", _explode)

    await agendador._tick(1)  # não pode levantar

    cfg = classificacao_config.get_config(1)
    assert "a internet caiu" in cfg["ultimo_tick_resultado"]


def test_o_bloqueio_diz_ate_quando_vai_durar(banco_agendador):
    """"Já existe uma classificação em andamento" sozinho é um beco."""
    agendador.reivindicar_rodada(1, origem="agendado", slot="h1")

    recado = classificacao.descrever_rodada_em_andamento(1)

    assert "automática" in recado
    assert "liberada automaticamente" in recado


def test_sem_rodada_no_banco_o_recado_manda_atualizar_a_pagina(banco_agendador):
    """O bloqueio veio da tela, não da execução: a saída é recarregar."""
    recado = classificacao.descrever_rodada_em_andamento(1)

    assert "não há nenhuma no banco" in recado
    assert "Atualize a página" in recado


def test_o_supervisor_e_registrado_com_o_tick_como_alvo():
    """A recuperação depende de o supervisor chamar o MESMO tick dos horários.

    Se alguém apontá-lo para outra função, o horário perdido por reinício de
    processo volta a se perder, e o sintoma é o silêncio de sempre.
    """
    fonte = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "sales_support_agent" / "services" / "agendador.py"
    ).read_text(encoding="utf-8")

    assert "IntervalTrigger(minutes=_SUPERVISOR_MINUTOS)" in fonte
    assert "id=_JOB_SUPERVISOR" in fonte


def test_o_lock_da_rodada_e_de_transacao_e_nunca_de_sessao():
    """Guarda de CÓDIGO, porque nenhum teste de runtime aqui pega isto.

    A suíte roda em SQLite, que não tem advisory lock: `reivindicar_rodada`
    pula o bloco inteiro. Por isso o defeito sobreviveu com a suíte verde, e
    até `test_depois_de_finalizada_uma_nova_rodada_pode_comecar`, que exercita
    exatamente manual -> finalizar -> agendado, passava com ele presente.

    O QUE ESTAVA ERRADO. `pg_try_advisory_lock` prende o lock à CONEXÃO, e
    soltá-lo exige `pg_advisory_unlock` na mesma conexão. Só que o
    `session.commit()` da reivindicação devolve a conexão ao pool, e a
    instrução seguinte pega outra. Durante uma execução manual o pool está em
    uso (progresso a cada e-mail, sondagem do painel a cada 3s), então o unlock
    ia para a conexão errada, devolvia `false` em silêncio, e o lock ficava
    preso. Toda tentativa posterior, agendada ou manual, era recusada com "já
    existe uma classificação em andamento" até reiniciar o servidor.

    `pg_try_advisory_xact_lock` não tem como vazar: o PostgreSQL solta no
    COMMIT ou no ROLLBACK, independentemente do pool.
    """
    fonte = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "sales_support_agent" / "services" / "agendador.py"
    ).read_text(encoding="utf-8")

    assert "pg_try_advisory_xact_lock" in fonte

    # A forma de sessão não pode voltar, nem o unlock manual que ela exige.
    codigo = [
        linha for linha in fonte.splitlines()
        if "pg_" in linha and not linha.strip().startswith("#")
        and "`" not in linha  # ignora as menções em docstring
    ]
    assert not [l for l in codigo if "pg_try_advisory_lock(" in l], codigo
    assert not [l for l in codigo if "pg_advisory_unlock" in l], codigo


def test_o_agendador_sobe_pelo_lifespan_e_nao_no_import():
    """A distinção que impede `reflex compile` de levantar um agendador.

    `compile` e `db makemigrations` importam o módulo da aplicação, mas nenhum
    dos dois serve requisição. Com o registro no lifespan, o agendador só sobe
    quando o servidor de fato começa a atender.
    """
    fonte = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "sales_support_agent" / "sales_support_agent.py"
    ).read_text(encoding="utf-8")

    assert "register_lifespan_task(iniciar_agendador)" in fonte
    assert "asyncio.run(" not in fonte
    assert "iniciar_agendador()" not in fonte, "o agendador está sendo chamado em tempo de import"
