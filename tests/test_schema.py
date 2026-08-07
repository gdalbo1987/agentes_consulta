"""O schema novo, e as duas restrições que sustentam a idempotência.

Idempotência aqui não é elegância: é controle de custo. Cada e-mail
reprocessado é uma chamada paga à OpenAI. As duas travas que impedem isso
(`UNIQUE (tenant_id, internet_message_id)` e `email_id` único em `ResumoEmail`)
são do BANCO, não do código, justamente para que um `if` esquecido no
orquestrador não vire uma fatura.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from sales_support_agent.models import (
    ClassificacaoConfig,
    ClassificacaoRun,
    EmailClassificado,
    PastaClasse,
    ResumoEmail,
    brt_now,
)


def _email(sessao, imid: str, **kw) -> EmailClassificado:
    linha = EmailClassificado(
        tenant_id=1,
        internet_message_id=imid,
        recebido_em=kw.pop("recebido_em", brt_now()),
        **kw,
    )
    sessao.add(linha)
    sessao.commit()
    sessao.refresh(linha)
    return linha


# ---------------------------------------------------------------------------
# Deduplicação
# ---------------------------------------------------------------------------


def test_o_mesmo_internet_message_id_nao_entra_duas_vezes(tenant, sessao):
    """A trava de custo da plataforma, garantida pelo banco.

    Sem ela, uma rodada interrompida no meio e repetida reclassificaria e
    recobraria os e-mails que já tinham sido processados.
    """
    _email(sessao, "<abc@dominio.com>")

    with pytest.raises(IntegrityError):
        _email(sessao, "<abc@dominio.com>")


def test_o_id_do_graph_pode_mudar_sem_quebrar_a_deduplicacao(tenant, sessao):
    """Este teste é a justificativa da escolha da chave.

    O `id` de uma mensagem no Graph MUDA quando ela é movida de pasta, e mover
    é o que o Agente 1 faz toda rodada. Se a deduplicação usasse o `id`, a
    rodada seguinte não reconheceria o e-mail que ela mesma moveu e o
    processaria de novo, do zero, pagando de novo.
    """
    linha = _email(sessao, "<estavel@dominio.com>", graph_message_id="AAA-id-antes-do-move")

    # O POST /move devolve um recurso novo, com id novo.
    linha.graph_message_id = "ZZZ-id-depois-do-move"
    linha.movido = True
    sessao.commit()

    encontrado = (
        sessao.query(EmailClassificado)
        .filter(EmailClassificado.internet_message_id == "<estavel@dominio.com>")
        .first()
    )
    assert encontrado is not None, "o e-mail sumiu da deduplicação depois de ser movido"
    assert encontrado.graph_message_id == "ZZZ-id-depois-do-move"


def test_um_email_tem_no_maximo_um_resumo(tenant, sessao):
    """`email_id` é único: o "já resumido, pula" do Agente 2 é do banco."""
    email = _email(sessao, "<resumo@dominio.com>", classe="pedido", status="classificado")

    sessao.add(ResumoEmail(tenant_id=1, email_id=email.id, resumo="Primeiro"))
    sessao.commit()

    sessao.add(ResumoEmail(tenant_id=1, email_id=email.id, resumo="Segundo"))
    with pytest.raises(IntegrityError):
        sessao.commit()


def test_uma_pasta_por_classe(tenant, sessao):
    sessao.add(PastaClasse(tenant_id=1, classe="pedido", pasta_nome="Pedidos"))
    sessao.commit()

    sessao.add(PastaClasse(tenant_id=1, classe="pedido", pasta_nome="Outra"))
    with pytest.raises(IntegrityError):
        sessao.commit()


# ---------------------------------------------------------------------------
# E-mail fora das quatro classes
# ---------------------------------------------------------------------------


def test_email_ignorado_e_persistido_sem_classe_e_sem_acao(tenant, sessao):
    """Ele vira linha para não ser reprocessado, mas não é marcado nem movido.

    É a única forma de "não classificar" sem pagar de novo pelo mesmo e-mail em
    toda rodada seguinte.
    """
    linha = _email(sessao, "<newsletter@dominio.com>", status="ignorado")

    assert linha.classe == ""
    assert linha.categoria_aplicada is False
    assert linha.movido is False
    assert linha.pasta_destino_id == ""


def test_categoria_e_move_sao_booleanos_separados(tenant, sessao):
    """A rodada pode morrer ENTRE marcar e mover, e isso precisa ser legível.

    Com dois booleanos, a rodada seguinte sabe que a categoria já foi aplicada e
    só falta mover, e termina o serviço sem chamar o modelo de novo.
    """
    linha = _email(sessao, "<meio-caminho@dominio.com>", classe="pedido", categoria_aplicada=True)

    assert linha.categoria_aplicada is True
    assert linha.movido is False


# ---------------------------------------------------------------------------
# Rodada
# ---------------------------------------------------------------------------


def test_a_rodada_nasce_em_running_e_sem_fim(tenant, sessao):
    rodada = ClassificacaoRun(tenant_id=1, origem="manual", user_email="a@b.com")
    sessao.add(rodada)
    sessao.commit()
    sessao.refresh(rodada)

    assert rodada.status == "running"
    assert rodada.finished_at is None
    assert rodada.duracao_segundos is None
    assert rodada.started_at is not None


def test_rodada_agendada_nao_tem_usuario(tenant, sessao):
    """Ninguém dispara a rodada agendada, então ela não pode ser cobrada de ninguém."""
    rodada = ClassificacaoRun(tenant_id=1, origem="agendado", slot="h1")
    sessao.add(rodada)
    sessao.commit()

    assert rodada.user_email == ""


def test_duracao_e_materializada_para_a_media_ser_um_avg(tenant, sessao):
    """A coluna existe para o card do dashboard não subtrair timestamps em Python.

    Ela é derivável, sim; o ponto é que sem ela a média seria calculada linha a
    linha, em Python, sobre timestamps anuláveis, toda vez que a página carrega.
    """
    inicio = brt_now()
    for segundos in (10, 20, 30):
        sessao.add(
            ClassificacaoRun(
                tenant_id=1, status="done", started_at=inicio,
                finished_at=inicio + timedelta(seconds=segundos),
                duracao_segundos=segundos,
            )
        )
    # Uma em andamento e uma com erro: nenhuma das duas pode entrar na média.
    sessao.add(ClassificacaoRun(tenant_id=1, status="running"))
    sessao.add(ClassificacaoRun(tenant_id=1, status="error", duracao_segundos=999))
    sessao.commit()

    concluidas = (
        sessao.query(ClassificacaoRun)
        .filter(ClassificacaoRun.status == "done", ClassificacaoRun.duracao_segundos.isnot(None))
        .all()
    )
    media = sum(r.duracao_segundos for r in concluidas) / len(concluidas)
    assert media == 20


# ---------------------------------------------------------------------------
# O que a conversão levou embora
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tabela",
    ["lead", "product", "searchconfig", "searchrun", "prospectcompany", "companycontact",
     "enrichmentrun", "priorizacaorun", "kipflowusage", "hunteraccount", "hunterusage"],
)
def test_tabela_do_funil_antigo_saiu_da_metadata(tabela):
    from sqlmodel import SQLModel

    assert tabela not in SQLModel.metadata.tables


@pytest.mark.parametrize(
    "tabela",
    ["user", "tenant", "activitylog", "tokenusage", "tokenpricing", "chatmessage",
     "agentmodelsetting", "integrationsetting"],
)
def test_tabela_preservada_continua_na_metadata(tabela):
    """As sagradas. A migration da conversão não podia encostar em nenhuma."""
    from sqlmodel import SQLModel

    assert tabela in SQLModel.metadata.tables


def test_integrationsetting_perdeu_kipflow_e_hunter_e_ganhou_a_pasta_de_origem():
    from sales_support_agent.models import IntegrationSetting

    colunas = set(IntegrationSetting.__table__.columns.keys())

    assert not {"kipflow_api_key_enc", "kipflow_base_url",
                "hunter_creditos_mensais", "hunter_dia_renovacao"} & colunas
    assert "graph_pasta_origem" in colunas
    assert "graph_client_secret_enc" in colunas


def test_config_operacional_nao_mora_na_tabela_de_credencial():
    """Separação deliberada: uma é do super admin, a outra do usuário padrão.

    `IntegrationSetting` não tem `tenant_id` e é lida pela UI só como booleano
    "configurado". Horário e janela de urgência precisam do oposto das duas
    coisas, então não caberiam lá.
    """
    from sales_support_agent.models import IntegrationSetting

    credencial = set(IntegrationSetting.__table__.columns.keys())
    operacional = set(ClassificacaoConfig.__table__.columns.keys())

    assert not {"horario_1", "horario_2", "janela_urgencia_horas"} & credencial
    assert {"horario_1", "horario_2", "janela_urgencia_horas"} <= operacional
    assert "tenant_id" in operacional
    assert "tenant_id" not in credencial


@pytest.mark.parametrize(
    "modelo,campo",
    [
        (EmailClassificado, "internet_message_id"),
        (EmailClassificado, "recebido_em"),
        (EmailClassificado, "classe"),
        (EmailClassificado, "urgente"),
        (EmailClassificado, "remetente_email"),
        (EmailClassificado, "status"),
        (ClassificacaoRun, "status"),
        (ClassificacaoRun, "started_at"),
    ],
)
def test_campo_de_filtro_e_indexado(modelo, campo):
    """Tudo o que o dashboard e as tools do Agente 3 filtram precisa de índice."""
    assert modelo.__table__.columns[campo].index, f"{modelo.__name__}.{campo} sem índice"
