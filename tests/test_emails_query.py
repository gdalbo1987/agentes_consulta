"""O modelo de leitura compartilhado pelo dashboard e pelo Agente 3.

Compartilhar é o ponto: com duas implementações, o chat poderia dizer "12
urgentes" enquanto a tabela mostra 9, e não haveria como saber qual está certa.
Os testes aqui valem para as duas telas ao mesmo tempo.
"""

from datetime import datetime, timedelta

import pytest

from sales_support_agent.models import (
    ClassificacaoRun,
    EmailClassificado,
    ResumoEmail,
    Tenant,
    brt_now,
)
from sales_support_agent.services import emails_query

TENANT = 1


@pytest.fixture
def base(engine, monkeypatch):
    from sqlmodel import Session

    import reflex as rx

    monkeypatch.setattr(rx, "session", lambda *a, **k: Session(engine))

    with Session(engine) as s:
        if not s.get(Tenant, TENANT):
            s.add(Tenant(id=TENANT, name="Coester"))
            s.commit()

    def _limpar():
        with Session(engine) as s:
            for modelo in (ResumoEmail, EmailClassificado, ClassificacaoRun):
                for linha in s.query(modelo).all():
                    s.delete(linha)
            s.commit()

    _limpar()
    yield engine
    _limpar()


def _email(engine, **kw):
    from sqlmodel import Session

    with Session(engine) as s:
        linha = EmailClassificado(
            tenant_id=TENANT,
            internet_message_id=kw.pop("imid", f"<{kw.get('assunto', 'x')}@t.com>"),
            recebido_em=kw.pop("recebido_em", brt_now()),
            status=kw.pop("status", "classificado"),
            classe=kw.pop("classe", "pedido"),
            **kw,
        )
        s.add(linha)
        s.commit()
        s.refresh(linha)
        return linha.id


def _resumo(engine, email_id, **kw):
    from sqlmodel import Session

    with Session(engine) as s:
        s.add(
            ResumoEmail(
                tenant_id=TENANT, email_id=email_id,
                resumo=kw.pop("resumo", "Resumo."), status=kw.pop("status", "done"),
                **kw,
            )
        )
        s.commit()


# ---------------------------------------------------------------------------
# Métricas
# ---------------------------------------------------------------------------


def test_media_ignora_execucao_em_andamento_e_com_erro(base):
    """A média mede tempo de TRABALHO, não tempo até a falha."""
    from sqlmodel import Session

    inicio = brt_now()
    with Session(base) as s:
        for segundos in (10, 20, 30):
            s.add(ClassificacaoRun(
                tenant_id=TENANT, status="done", started_at=inicio,
                finished_at=inicio + timedelta(seconds=segundos), duracao_segundos=segundos,
            ))
        s.add(ClassificacaoRun(tenant_id=TENANT, status="running"))
        s.add(ClassificacaoRun(tenant_id=TENANT, status="error", duracao_segundos=999))
        s.commit()

    assert emails_query.metricas_execucao(TENANT)["duracao_media"] == "20s"


def test_sem_execucao_concluida_os_cards_mostram_traco_e_nao_zero(base):
    """`0s` diria que a execução foi instantânea; `-` diz que não houve execução."""
    m = emails_query.metricas_execucao(TENANT)

    assert m["duracao_media"] == "-"
    assert m["duracao_ultima"] == "-"
    assert "—" not in m["duracao_media"], "traço tem de ser o simples, nunca travessão"


def test_duracao_e_formatada_em_minutos_e_horas(base):
    assert emails_query._duracao(45) == "45s"
    assert emails_query._duracao(90) == "1min 30s"
    assert emails_query._duracao(120) == "2min"
    assert emails_query._duracao(3900) == "1h 5min"


def test_contadores_acumulado_e_ultima_execucao(base):
    from sqlmodel import Session

    _email(base, assunto="A")
    _email(base, assunto="B")
    _email(base, assunto="C", status="ignorado", classe="")

    inicio = brt_now()
    with Session(base) as s:
        s.add(ClassificacaoRun(
            tenant_id=TENANT, status="done", classificados=2, origem="agendado",
            started_at=inicio, finished_at=inicio + timedelta(seconds=5), duracao_segundos=5,
        ))
        s.commit()

    m = emails_query.metricas_execucao(TENANT)
    assert m["total_classificados"] == 2, "o ignorado entrou na contagem"
    assert m["ultima_rodada_classificados"] == 2
    assert m["ultima_rodada_origem"] == "agendado"


# ---------------------------------------------------------------------------
# Listagem e filtros
# ---------------------------------------------------------------------------


def test_ignorados_nunca_aparecem(base):
    """Eles existem só para não serem reprocessados; mostrá-los encheria a tela."""
    _email(base, assunto="Pedido")
    _email(base, assunto="Newsletter", status="ignorado", classe="")

    assuntos = [e["assunto"] for e in emails_query.listar_emails(TENANT)]
    assert assuntos == ["Pedido"]


def test_filtro_de_data_e_inclusivo_nas_duas_pontas(base):
    """Filtrar "até 07/08" e não ver o que chegou às 15h do dia 7 surpreenderia."""
    _email(base, assunto="Cedo", imid="<1@t>", recebido_em=datetime(2026, 8, 5, 0, 0))
    _email(base, assunto="Meio", imid="<2@t>", recebido_em=datetime(2026, 8, 6, 12, 0))
    _email(base, assunto="Tarde", imid="<3@t>", recebido_em=datetime(2026, 8, 7, 15, 30))
    _email(base, assunto="Fora", imid="<4@t>", recebido_em=datetime(2026, 8, 9, 9, 0))

    achados = emails_query.listar_emails(
        TENANT, data_inicio="2026-08-05", data_fim="2026-08-07"
    )
    assert {e["assunto"] for e in achados} == {"Cedo", "Meio", "Tarde"}


def test_filtro_de_urgentes(base):
    _email(base, assunto="Urgente", imid="<1@t>", urgente=True)
    _email(base, assunto="Normal", imid="<2@t>", urgente=False)

    achados = emails_query.listar_emails(TENANT, apenas_urgentes=True)
    assert [e["assunto"] for e in achados] == ["Urgente"]


def test_filtro_de_importantes(base):
    _email(base, assunto="Urgente", imid="<1@t>", urgente=True)
    _email(base, assunto="Importante", imid="<2@t>", importante=True)
    _email(base, assunto="Normal", imid="<3@t>")

    achados = emails_query.listar_emails(TENANT, apenas_importantes=True)
    assert [e["assunto"] for e in achados] == ["Importante"]


def test_os_dois_filtros_de_prioridade_juntos_combinam_por_ou(base):
    """As faixas são exclusivas: por E, ligar os dois devolveria SEMPRE vazio.

    E quem ligasse os dois interruptores concluiria que não há e-mail nenhum,
    quando a leitura útil é "tudo o que tem alguma prioridade".
    """
    _email(base, assunto="Urgente", imid="<1@t>", urgente=True)
    _email(base, assunto="Importante", imid="<2@t>", importante=True)
    _email(base, assunto="Normal", imid="<3@t>")

    achados = emails_query.listar_emails(
        TENANT, apenas_urgentes=True, apenas_importantes=True
    )
    assert {e["assunto"] for e in achados} == {"Urgente", "Importante"}


def test_ordenacao_e_mais_recente_primeiro(base):
    _email(base, assunto="Antigo", imid="<1@t>", recebido_em=datetime(2026, 8, 1, 9, 0))
    _email(base, assunto="Novo", imid="<2@t>", recebido_em=datetime(2026, 8, 7, 9, 0))

    assert [e["assunto"] for e in emails_query.listar_emails(TENANT)] == ["Novo", "Antigo"]


# ---------------------------------------------------------------------------
# Detalhe
# ---------------------------------------------------------------------------


def test_detalhe_traz_o_resumo_do_agente_2(base):
    import json

    email_id = _email(base, assunto="Pedido 900")
    _resumo(
        base, email_id, resumo="Cliente quer 200 peças.",
        pontos_chave=json.dumps(["200 peças", "entrega em 10 dias"], ensure_ascii=False),
        acao_sugerida="Confirmar estoque.",
    )

    d = emails_query.detalhe_email(TENANT, email_id)
    assert d["resumo"] == "Cliente quer 200 peças."
    assert d["pontos_chave"] == ["200 peças", "entrega em 10 dias"]
    assert d["resumo_disponivel"] is True


def test_resumo_que_falhou_e_dito_e_nao_deixado_em_branco(base):
    """Espaço vazio parece defeito de tela; a ausência precisa ser explícita."""
    email_id = _email(base, assunto="Sem resumo")
    _resumo(base, email_id, resumo="", status="failed")

    d = emails_query.detalhe_email(TENANT, email_id)
    assert d["resumo_disponivel"] is False


def test_email_sem_resumo_nenhum_tambem_e_tratado(base):
    email_id = _email(base, assunto="Ainda sem resumo")

    d = emails_query.detalhe_email(TENANT, email_id)
    assert d is not None
    assert d["resumo_disponivel"] is False
    assert d["pontos_chave"] == []


def test_detalhe_de_outro_tenant_nao_vaza(base):
    from sqlmodel import Session

    with Session(base) as s:
        if not s.get(Tenant, 2):
            s.add(Tenant(id=2, name="Outra"))
            s.commit()
        alheio = EmailClassificado(
            tenant_id=2, internet_message_id="<alheio@t>", recebido_em=brt_now(),
            status="classificado", classe="pedido", assunto="Segredo",
        )
        s.add(alheio)
        s.commit()
        s.refresh(alheio)
        alheio_id = alheio.id

    assert emails_query.detalhe_email(TENANT, alheio_id) is None


# ---------------------------------------------------------------------------
# Agregados e busca
# ---------------------------------------------------------------------------


def test_resumo_da_caixa_conta_por_classe(base):
    _email(base, assunto="A", imid="<1@t>", classe="pedido")
    _email(base, assunto="B", imid="<2@t>", classe="pedido")
    _email(base, assunto="C", imid="<3@t>", classe="proposta", urgente=True)

    r = emails_query.resumo_da_caixa(TENANT)
    assert r["total"] == 3
    assert r["urgentes"] == 1
    assert r["por_classe"]["Pedido"] == 2
    assert r["por_classe"]["Proposta"] == 1


def test_listar_clientes_agrupa_e_ordena_por_volume(base):
    _email(base, assunto="A", imid="<1@t>", remetente_email="a@x.com", remetente_nome="Alfa")
    _email(base, assunto="B", imid="<2@t>", remetente_email="a@x.com", remetente_nome="Alfa")
    _email(base, assunto="C", imid="<3@t>", remetente_email="b@y.com", remetente_nome="Beta")

    clientes = emails_query.listar_clientes(TENANT)
    assert clientes[0]["email"] == "a@x.com"
    assert clientes[0]["total"] == 2


def test_busca_por_cliente_aceita_parte_do_nome(base):
    _email(base, assunto="A", remetente_email="compras@metalurgica.com.br", remetente_nome="Metalúrgica Silva")

    assert len(emails_query.buscar_por_cliente(TENANT, "metalurgica")) == 1
    assert len(emails_query.buscar_por_cliente(TENANT, "Silva")) == 1
    assert len(emails_query.buscar_por_cliente(TENANT, "inexistente")) == 0


def test_busca_de_conteudo_olha_assunto_e_resumo(base):
    """Não olha o corpo cru: ele traz assinatura e histórico, que geram falso positivo."""
    a = _email(base, assunto="Cotação de válvulas", imid="<1@t>", corpo_texto="corpo qualquer")
    b = _email(base, assunto="Outro assunto", imid="<2@t>", corpo_texto="válvulas no corpo")
    _resumo(base, a, resumo="Cliente quer preço.")
    _resumo(base, b, resumo="Nada a ver.")

    achados = emails_query.buscar_conteudo(TENANT, "válvulas")
    assert [e["assunto"] for e in achados] == ["Cotação de válvulas"]


# ---------------------------------------------------------------------------
# Recálculo de urgência
# ---------------------------------------------------------------------------


def test_apertar_a_janela_re_marca_sem_chamar_o_modelo(base):
    """A razão de `urgencia_prazo_horas` ser persistido separado dos booleanos."""
    _email(base, assunto="Prazo 12h", imid="<1@t>", urgencia_prazo_horas=12, urgente=True)
    _email(base, assunto="Prazo 6h", imid="<2@t>", urgencia_prazo_horas=6, urgente=True)

    alteradas = emails_query.recalcular_urgencia(TENANT, janela_horas=8)

    assert alteradas == 1
    urgentes = {e["assunto"] for e in emails_query.listar_emails(TENANT, apenas_urgentes=True)}
    assert urgentes == {"Prazo 6h"}


def test_apertar_a_janela_rebaixa_para_importante_e_nao_apaga(base):
    """Compromisso com data não vira "sem prioridade" só porque a janela mudou."""
    _email(base, assunto="Prazo 12h", urgencia_prazo_horas=12, urgente=True)

    emails_query.recalcular_urgencia(TENANT, janela_horas=8)

    linha = emails_query.listar_emails(TENANT)[0]
    assert linha["urgente"] is False
    assert linha["importante"] is True
    assert linha["prioridade"] == "Importante"


def test_alargar_a_janela_volta_a_marcar(base):
    _email(base, assunto="Prazo 12h", urgencia_prazo_horas=12, urgente=False)

    assert emails_query.recalcular_urgencia(TENANT, janela_horas=24) == 1
    assert len(emails_query.listar_emails(TENANT, apenas_urgentes=True)) == 1


def test_urgencia_sem_prazo_e_preservada(base):
    """Ela veio do sinal semântico ("estamos parados"), que a janela não afeta.

    O sinal é PERSISTIDO: antes o recálculo o adivinhava pela ausência de
    prazo, e não sabia distinguir "urgente porque a data cabe" de "urgente
    porque o texto diz".
    """
    _email(
        base, assunto="Urgente sem prazo", urgencia_prazo_horas=None,
        urgente=True, urgente_semantico=True,
    )

    emails_query.recalcular_urgencia(TENANT, janela_horas=1)

    assert len(emails_query.listar_emails(TENANT, apenas_urgentes=True)) == 1


def test_sem_prazo_e_sem_sinal_semantico_nao_ganha_prioridade(base):
    """Contraprova: sem os dois, o recálculo desmarca, e deve mesmo."""
    _email(
        base, assunto="Sem nada", urgencia_prazo_horas=None,
        urgente=True, urgente_semantico=False,
    )

    emails_query.recalcular_urgencia(TENANT, janela_horas=24)

    linha = emails_query.listar_emails(TENANT)[0]
    assert linha["prioridade"] == "Normal"


# ---------------------------------------------------------------------------
# Fila de urgências: tratar tira da fila, não do banco
# ---------------------------------------------------------------------------
def test_tratar_tira_da_fila_de_urgencias_mas_nao_do_banco(base):
    """A distinção que o botão do painel promete."""
    eid = _email(base, assunto="Urgente", urgencia_prazo_horas=6, urgente=True)

    assert len(emails_query.urgencias(TENANT)) == 1

    assert emails_query.marcar_urgencia_tratada(TENANT, eid, True) is True

    assert emails_query.urgencias(TENANT) == []
    # Continua no banco, na tabela e com a marcação de urgente intacta.
    linha = emails_query.listar_emails(TENANT)[0]
    assert linha["id"] == eid
    assert linha["urgente"] is True
    assert linha["urgencia_tratada"] is True
    assert len(emails_query.listar_emails(TENANT, apenas_urgentes=True)) == 1


def test_tratado_sobrevive_ao_recalculo_da_janela(base):
    """A razão de existir uma coluna própria em vez de `urgente = False`.

    A urgência é recalculada a partir do prazo sempre que a janela muda. Se
    tratar fosse desmarcar, o próximo recálculo traria de volta tudo o que já
    tinha sido resolvido.
    """
    eid = _email(base, assunto="Urgente", urgencia_prazo_horas=6, urgente=True)
    emails_query.marcar_urgencia_tratada(TENANT, eid, True)

    emails_query.recalcular_urgencia(TENANT, janela_horas=48)

    assert emails_query.urgencias(TENANT) == [], "o recálculo ressuscitou um tratado"


def test_devolver_para_a_fila_desfaz(base):
    """Tirar da fila é um clique só, sem confirmação, então precisa ter volta."""
    eid = _email(base, assunto="Urgente", urgencia_prazo_horas=6, urgente=True)
    emails_query.marcar_urgencia_tratada(TENANT, eid, True)

    emails_query.marcar_urgencia_tratada(TENANT, eid, False)

    assert len(emails_query.urgencias(TENANT)) == 1


def test_tratar_email_inexistente_nao_explode(base):
    assert emails_query.marcar_urgencia_tratada(TENANT, 99999, True) is False


def test_tratar_nao_atravessa_organizacoes(base):
    """O `tenant_id` entra no WHERE, e não só no dado lido."""
    eid = _email(base, assunto="Urgente", urgencia_prazo_horas=6, urgente=True)

    assert emails_query.marcar_urgencia_tratada(999, eid, True) is False
    assert len(emails_query.urgencias(TENANT)) == 1


# ---------------------------------------------------------------------------
# Exclusão de um e-mail
# ---------------------------------------------------------------------------
def test_excluir_apaga_o_email_e_o_resumo(base):
    """O resumo referencia o e-mail por chave estrangeira e sai primeiro."""
    from sqlmodel import Session

    from sales_support_agent.models import EmailClassificado, ResumoEmail

    eid = _email(base, assunto="Para apagar")
    _resumo(base, eid, resumo="resumo qualquer")

    resultado = emails_query.excluir_email(TENANT, eid)

    assert resultado == {"apagado": True, "assunto": "Para apagar"}
    with Session(base) as s:
        assert s.get(EmailClassificado, eid) is None
        assert s.query(ResumoEmail).filter(ResumoEmail.email_id == eid).count() == 0


def test_excluir_nao_toca_nos_outros(base):
    _email(base, assunto="Fica", imid="<fica@t>")
    eid = _email(base, assunto="Sai", imid="<sai@t>")

    emails_query.excluir_email(TENANT, eid)

    assert {e["assunto"] for e in emails_query.listar_emails(TENANT)} == {"Fica"}


def test_excluir_email_inexistente_devolve_nao_apagado(base):
    assert emails_query.excluir_email(TENANT, 99999) == {"apagado": False, "assunto": ""}


def test_excluir_nao_atravessa_organizacoes(base):
    eid = _email(base, assunto="De outra org")

    assert emails_query.excluir_email(999, eid)["apagado"] is False
    assert len(emails_query.listar_emails(TENANT)) == 1


def test_excluir_sem_resumo_funciona(base):
    """Nem todo classificado tem resumo: o Agente 2 pode ter falhado."""
    eid = _email(base, assunto="Sem resumo")

    assert emails_query.excluir_email(TENANT, eid)["apagado"] is True
