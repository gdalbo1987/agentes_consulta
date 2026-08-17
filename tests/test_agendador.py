"""O agendador: qual horário está vencido e quem pode reivindicar a rodada.

`slot_devido` é função pura, então dá para testar exaustivamente sem mexer no
relógio da máquina. É deliberado: a regra "já rodou hoje?" é exatamente o tipo
de coisa que, testada só com o relógio de verdade, passa em desenvolvimento e
falha na virada do dia.
"""

from datetime import datetime, timedelta

import pytest

from sales_support_agent.models import ClassificacaoRun
from sales_support_agent.services import agendador, classificacao_config

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
