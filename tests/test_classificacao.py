"""O Agente 1 e o orquestrador da rodada.

O modelo é dublado: o que se testa aqui é a ORQUESTRAÇÃO, que é onde moram as
decisões caras. Se um e-mail fora das quatro classes é mesmo deixado em paz, se
rodar duas vezes custa zero, se a categoria vai antes do move, se uma falha de
um item não derruba os outros. A qualidade da classificação em si depende do
modelo e não de código nosso.

A caixa de correio é a falsa de `tests/fakes/graph.py`, cujo `mover` devolve um
id NOVO, como o Graph de verdade. É o que faz o teste de idempotência valer
alguma coisa.
"""

import json
from datetime import timedelta

import pytest

from sales_support_agent.models import (
    ClassificacaoRun,
    EmailClassificado,
    ResumoEmail,
    brt_now,
)
from sales_support_agent.services import classificacao, classificacao_config
from sales_support_agent.services.classificacao_rules import CLASSES
from sales_support_agent.services.graph_client import GraphClientError
from tests.fakes.graph import CaixaFalsa, instalar

TENANT = 1


# ---------------------------------------------------------------------------
# Aparato
# ---------------------------------------------------------------------------


@pytest.fixture
def banco(engine, monkeypatch):
    """Aponta o `rx.session()` dos serviços para o banco de teste."""
    from sqlmodel import Session

    import reflex as rx

    monkeypatch.setattr(rx, "session", lambda *a, **k: Session(engine))
    return engine


@pytest.fixture
def organizacao(banco):
    from sqlmodel import Session

    from sales_support_agent.models import Tenant

    with Session(banco) as s:
        if not s.get(Tenant, TENANT):
            s.add(Tenant(id=TENANT, name="Coester"))
            s.commit()

    # Cada teste começa do zero E não deixa rastro. Os testes deste arquivo
    # COMITAM (o código sob teste abre a própria sessão, então uma transação
    # revertida não serviria), e o banco de teste é compartilhado pela suíte
    # inteira. Sem limpar na saída, as linhas comitadas aqui colidiriam com os
    # testes de schema, que também inserem no tenant 1.
    #
    # A configuração e o mapa de pastas entram na limpeza junto com os e-mails:
    # sem isso, um teste que mapeia as pastas deixaria o mapeamento pronto para
    # o teste que verifica a recusa por pasta não configurada.
    from sales_support_agent.models import ClassificacaoConfig, PastaClasse

    tabelas = (ResumoEmail, EmailClassificado, ClassificacaoRun, PastaClasse, ClassificacaoConfig)

    def _limpar():
        with Session(banco) as s:
            for modelo in tabelas:
                for linha in s.query(modelo).all():
                    s.delete(linha)
            s.commit()

    _limpar()
    classificacao_config.ensure_config(TENANT)
    classificacao_config.ensure_pastas(TENANT)

    yield TENANT

    _limpar()


@pytest.fixture
def caixa(monkeypatch, organizacao):
    """Caixa falsa com as quatro pastas já mapeadas."""
    falsa = CaixaFalsa()
    for classe in CLASSES:
        pasta_id = falsa.add_pasta(classe)
        classificacao_config.salvar_pasta(
            TENANT, classe, pasta_nome=classe, pasta_id=pasta_id, pasta_caminho=classe
        )
    instalar(monkeypatch, falsa)
    return falsa


class ModeloDublê:
    """Dublê do agente de classificação, com contador de chamadas."""

    def __init__(self, resposta=None):
        self.chamadas = 0
        self.resposta = resposta or {
            "classe": "pedido", "confianca": 90, "urgencia_prazo_horas": None,
            "urgente": False, "justificativa": "Cliente colocou um pedido.",
        }
        self.por_assunto = {}
        self.erro = None

    async def __call__(self, email, janela):
        self.chamadas += 1
        if self.erro:
            return False, None, self.erro, {"input": 0, "output": 0}
        resposta = self.por_assunto.get(email.get("assunto"), self.resposta)
        return True, dict(resposta), "", {"input": 10, "output": 5}


@pytest.fixture
def modelo(monkeypatch):
    dublê = ModeloDublê()
    monkeypatch.setattr(
        "sales_support_agent.services.classificacao_agent.classificar_email", dublê
    )

    async def _resumo_ok(email, classe):
        return True, {
            "resumo": "Resumo de teste.", "pontos_chave": ["a", "b"],
            "acao_sugerida": "Responder.", "prazo_mencionado": "", "modelo": "gpt-teste",
        }, "", {"input": 4, "output": 2}

    monkeypatch.setattr("sales_support_agent.services.resumo_agent.resumir_email", _resumo_ok)
    return dublê


async def _rodar(tenant=TENANT, **kw):
    """Executa uma rodada inteira e devolve (eventos, resumo_ou_None, erro)."""
    from sqlmodel import Session

    import reflex as rx

    origem = kw.pop("origem", "manual")
    with rx.session() as s:
        rodada = ClassificacaoRun(tenant_id=tenant, origem=origem)
        s.add(rodada)
        s.commit()
        s.refresh(rodada)
        run_id = rodada.id

    eventos, resumo, erro = [], None, None
    async for evento in classificacao.stream_classificacao(
        tenant, run_id=run_id, origem=origem, **kw
    ):
        eventos.append(evento)
        if evento[0] == "done":
            resumo = evento[1]
        elif evento[0] == "error":
            erro = evento[1]
    return eventos, resumo, erro


# ---------------------------------------------------------------------------
# As quatro classes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("classe", CLASSES)
async def test_cada_classe_vai_para_a_sua_pasta_com_a_sua_categoria(caixa, modelo, classe):
    caixa.add_email(assunto="Assunto", corpo="corpo")
    modelo.resposta = {
        "classe": classe, "confianca": 90, "urgencia_prazo_horas": None,
        "urgente": False, "justificativa": "x",
    }

    _, resumo, erro = await _rodar()

    assert erro is None
    assert resumo["classificados"] == 1

    pasta = classificacao_config.get_pastas(TENANT)
    destino = next(p["pasta_id"] for p in pasta if p["classe"] == classe)
    assert len(caixa.emails_em(destino)) == 1

    from sales_support_agent.services.classificacao_rules import CATEGORIAS

    movida = caixa.emails_em(destino)[0]
    assert CATEGORIAS[classe] in movida["categorias"]


# ---------------------------------------------------------------------------
# Urgência
# ---------------------------------------------------------------------------


async def test_urgente_recebe_a_categoria_adicional(caixa, modelo):
    caixa.add_email(assunto="Preciso hoje")
    modelo.resposta = {
        "classe": "pedido", "confianca": 90, "urgencia_prazo_horas": 6,
        "urgente": True, "justificativa": "prazo curto",
    }

    _, resumo, _ = await _rodar()

    assert resumo["urgentes"] == 1
    movida = next(iter(caixa.mensagens.values()))
    assert "Urgente" in movida["categorias"]
    assert "Pedido" in movida["categorias"]


async def test_nao_urgente_nao_recebe_a_categoria(caixa, modelo):
    caixa.add_email(assunto="Sem pressa")
    modelo.resposta = {
        "classe": "pedido", "confianca": 90, "urgencia_prazo_horas": 240,
        "urgente": False, "justificativa": "prazo longo",
    }

    _, resumo, _ = await _rodar()

    assert resumo["urgentes"] == 0
    assert "Urgente" not in next(iter(caixa.mensagens.values()))["categorias"]


async def test_o_prazo_fica_gravado_para_a_janela_poder_mudar_depois(caixa, modelo, banco):
    """Sem o prazo na linha, mudar a janela exigiria reprocessar tudo no modelo."""
    from sqlmodel import Session

    caixa.add_email(assunto="Com prazo")
    modelo.resposta = {
        "classe": "pedido", "confianca": 90, "urgencia_prazo_horas": 12,
        "urgente": True, "justificativa": "x",
    }
    await _rodar()

    with Session(banco) as s:
        linha = s.query(EmailClassificado).first()
    assert linha.urgencia_prazo_horas == 12


# ---------------------------------------------------------------------------
# Fora das quatro classes
# ---------------------------------------------------------------------------


async def test_email_fora_das_classes_nao_e_marcado_nem_movido(caixa, modelo, banco):
    """O requisito mais explícito do produto: ele fica exatamente onde estava."""
    from sqlmodel import Session

    graph_id = caixa.add_email(assunto="Newsletter semanal")
    modelo.resposta = {
        "classe": "", "confianca": 20, "urgencia_prazo_horas": None,
        "urgente": False, "justificativa": "não se encaixa",
    }

    _, resumo, _ = await _rodar()

    assert resumo["ignorados"] == 1
    assert resumo["classificados"] == 0
    assert not caixa.houve("aplicar_categorias", graph_id), "marcou um e-mail que devia ficar em paz"
    assert not caixa.houve("mover_mensagem", graph_id), "moveu um e-mail que devia ficar em paz"
    assert caixa.mensagens[graph_id]["_pasta"] == "inbox"

    with Session(banco) as s:
        linha = s.query(EmailClassificado).first()
    assert linha.status == "ignorado"
    assert linha.classe == ""


async def test_ignorado_tambem_vira_linha_para_nao_custar_de_novo(caixa, modelo):
    """A linha existe justamente para ele não voltar ao modelo na próxima rodada."""
    caixa.add_email(assunto="Spam")
    modelo.resposta = {
        "classe": "", "confianca": 10, "urgencia_prazo_horas": None,
        "urgente": False, "justificativa": "spam",
    }

    await _rodar()
    chamadas_apos_primeira = modelo.chamadas

    await _rodar()
    assert modelo.chamadas == chamadas_apos_primeira, (
        "o e-mail ignorado voltou ao modelo na segunda rodada, e isso é dinheiro"
    )


async def test_ignorado_nao_ganha_resumo(caixa, modelo, banco):
    from sqlmodel import Session

    caixa.add_email(assunto="Nada a ver")
    modelo.resposta = {
        "classe": "", "confianca": 10, "urgencia_prazo_horas": None,
        "urgente": False, "justificativa": "x",
    }
    await _rodar()

    with Session(banco) as s:
        assert s.query(ResumoEmail).count() == 0


# ---------------------------------------------------------------------------
# Idempotência
# ---------------------------------------------------------------------------


async def test_rodar_duas_vezes_nao_reclassifica(caixa, modelo):
    """A trava de custo: e-mail já visto não volta ao modelo.

    Depois de classificados os e-mails saem da caixa de entrada, então a
    segunda varredura normalmente nem os enxerga. O caso que importa é o da
    JANELA SOBREPOSTA, em que eles reaparecem na leitura: é aí que a
    deduplicação precisa agir. Por isso o teste os traz de volta antes da
    segunda rodada, em vez de se contentar com a caixa vazia, que passaria sem
    exercitar nada.
    """
    caixa.add_email(assunto="Pedido 123")
    caixa.add_email(assunto="Pedido 456")

    _, primeira, _ = await _rodar()
    assert primeira["processados"] == 2
    assert modelo.chamadas == 2

    for mensagem in caixa.mensagens.values():
        mensagem["_pasta"] = "inbox"

    _, segunda, _ = await _rodar()
    assert segunda["total_emails"] == 2, "a segunda varredura não reencontrou os e-mails"
    assert segunda["processados"] == 0
    assert segunda["puladas"] == 2
    assert modelo.chamadas == 2, "a segunda rodada chamou o modelo de novo"


async def test_a_deduplicacao_sobrevive_a_mudanca_de_id_do_graph(caixa, modelo):
    """O teste que justifica a escolha da chave de deduplicação.

    Depois do move, a caixa falsa devolve um id NOVO, como o Graph. Se a
    deduplicação usasse o id, a terceira rodada não reconheceria o e-mail que ela
    mesma moveu e o reprocessaria, pagando tudo de novo.
    """
    caixa.add_email(assunto="Pedido único", imid="<estavel@cliente.com>")

    await _rodar()
    chamadas = modelo.chamadas

    # A mensagem agora está na pasta de destino, com id diferente. Ela volta a
    # aparecer na varredura se a pasta de origem for a mesma, então simula-se
    # isso trazendo-a de volta para a caixa de entrada com o id novo.
    movida = next(m for m in caixa.mensagens.values() if m["_pasta"] != "inbox")
    movida["_pasta"] = "inbox"

    await _rodar()
    assert modelo.chamadas == chamadas, (
        "o e-mail foi reprocessado depois de mudar de id: a deduplicação está "
        "presa ao id do Graph em vez do internetMessageId"
    )


# ---------------------------------------------------------------------------
# Ordem das ações e falhas
# ---------------------------------------------------------------------------


async def test_a_categoria_e_aplicada_antes_do_move(caixa, modelo):
    """Depois do move o id antigo não existe: um PATCH nele voltaria 404."""
    graph_id = caixa.add_email(assunto="Pedido")
    await _rodar()

    ordem = [c[0] for c in caixa.chamadas if c[1] == graph_id]
    assert ordem.index("aplicar_categorias") < ordem.index("mover_mensagem")


async def test_falha_em_um_email_nao_derruba_os_outros(caixa, modelo, banco):
    from sqlmodel import Session

    caixa.add_email(assunto="Pedido A")
    ruim = caixa.add_email(assunto="Pedido B")
    caixa.add_email(assunto="Pedido C")
    caixa.programar_falha(
        "mover_mensagem", ruim, GraphClientError("Pasta sumiu", http_status=404, fatal=False)
    )

    _, resumo, erro = await _rodar()

    assert erro is None, "um item com problema derrubou a rodada inteira"
    assert resumo["classificados"] == 2
    assert resumo["falhas"] == 1
    assert any("Pedido B" in a for a in resumo["avisos"])

    with Session(banco) as s:
        falhou = s.query(EmailClassificado).filter(EmailClassificado.status == "falhou").one()
    assert falhou.categoria_aplicada is True, "a categoria tinha sido aplicada antes da falha"
    assert falhou.movido is False


async def test_erro_fatal_do_graph_encerra_a_rodada(caixa, modelo):
    """Credencial ou permissão errada: insistir nos próximos só gasta tempo."""
    caixa.add_email(assunto="Pedido A")
    caixa.add_email(assunto="Pedido B")
    primeiro = list(caixa.mensagens)[0]
    caixa.programar_falha(
        "aplicar_categorias", primeiro,
        GraphClientError("Credenciais inválidas", http_status=401, fatal=True),
    )

    _, resumo, erro = await _rodar()

    assert erro is not None
    assert "Credenciais" in erro
    assert resumo is None, "a rodada terminou como 'done' apesar do erro fatal"


async def test_falha_do_modelo_marca_o_email_e_continua(caixa, modelo, banco):
    from sqlmodel import Session

    caixa.add_email(assunto="Qualquer")
    modelo.erro = "Cota da OpenAI esgotada."

    _, resumo, erro = await _rodar()

    assert erro is None
    assert resumo["falhas"] == 1
    with Session(banco) as s:
        linha = s.query(EmailClassificado).one()
    assert linha.status == "falhou"
    assert "Cota" in linha.erro


# ---------------------------------------------------------------------------
# Configuração incompleta
# ---------------------------------------------------------------------------


async def test_pasta_nao_mapeada_impede_a_rodada_antes_de_gastar_token(
    monkeypatch, organizacao, modelo
):
    """A recusa vem ANTES da primeira chamada ao modelo.

    Começar e parar no meio deixaria metade dos e-mails arquivados e metade na
    caixa de entrada, que é o pior dos dois mundos.
    """
    falsa = CaixaFalsa()
    falsa.add_email(assunto="Pedido")
    instalar(monkeypatch, falsa)
    # Nenhuma pasta mapeada: as linhas existem com pasta_id vazio.

    _, resumo, erro = await _rodar()

    assert erro is not None
    assert "pasta" in erro.lower()
    assert modelo.chamadas == 0, "chamou o modelo antes de checar a configuração"
    assert not falsa.houve("mover_mensagem")


async def test_agendado_nao_roda_com_o_automatico_parado(caixa, modelo):
    """O botão Parar do painel desliga o agendado, e desliga ANTES do primeiro token."""
    caixa.add_email(assunto="Pedido")
    classificacao_config.salvar_config(TENANT, ativo=False)

    _, resumo, erro = await _rodar(origem="agendado")

    assert erro is not None and "paradas" in erro
    assert modelo.chamadas == 0
    assert not caixa.houve("mover_mensagem")


async def test_botao_manual_roda_mesmo_com_o_automatico_parado(caixa, modelo):
    """"Classificar agora" é ação deliberada de quem está olhando a tela.

    Precisa funcionar justamente com o automático parado: é assim que se testa
    a configuração antes de soltar o agente na caixa.
    """
    caixa.add_email(assunto="Pedido")
    classificacao_config.salvar_config(TENANT, ativo=False)

    _, resumo, erro = await _rodar(origem="manual")

    assert erro is None
    assert resumo["classificados"] == 1


async def test_o_automatico_nasce_desligado():
    """Configurar horário não é o mesmo que autorizar o agente a mexer na caixa."""
    from sales_support_agent.models import ClassificacaoConfig

    assert ClassificacaoConfig().ativo is False


async def test_dry_run_nao_escreve_em_lugar_nenhum(caixa, modelo, banco):
    """O jeito seguro de conferir a primeira execução numa caixa de produção."""
    from sqlmodel import Session

    caixa.add_email(assunto="Pedido")

    _, resumo, erro = await _rodar(dry_run=True)

    assert erro is None
    assert resumo["classificados"] == 1
    assert not caixa.houve("mover_mensagem")
    assert not caixa.houve("aplicar_categorias")
    with Session(banco) as s:
        assert s.query(EmailClassificado).count() == 0


# ---------------------------------------------------------------------------
# Resumo (Agente 2) dentro da rodada
# ---------------------------------------------------------------------------


async def test_o_resumo_e_gravado_ligado_ao_email(caixa, modelo, banco):
    from sqlmodel import Session

    caixa.add_email(assunto="Pedido")
    _, resumo, _ = await _rodar()

    assert resumo["resumidos"] == 1
    with Session(banco) as s:
        email = s.query(EmailClassificado).one()
        linha = s.query(ResumoEmail).one()
    assert linha.email_id == email.id
    assert linha.status == "done"
    assert json.loads(linha.pontos_chave) == ["a", "b"]


async def test_falha_no_resumo_nao_desfaz_a_classificacao(caixa, monkeypatch, modelo, banco):
    """O e-mail já foi movido: desfazer não é possível, e o resumo pode esperar."""
    from sqlmodel import Session

    async def _resumo_ruim(email, classe):
        return False, None, "Modelo indisponível.", {"input": 0, "output": 0}

    monkeypatch.setattr("sales_support_agent.services.resumo_agent.resumir_email", _resumo_ruim)
    caixa.add_email(assunto="Pedido")

    _, resumo, erro = await _rodar()

    assert erro is None
    assert resumo["classificados"] == 1
    assert resumo["resumidos"] == 0
    with Session(banco) as s:
        assert s.query(EmailClassificado).one().status == "classificado"
        assert s.query(ResumoEmail).one().status == "failed"


async def test_o_consumo_de_cada_agente_e_contado_separado(caixa, modelo):
    """Sem separar, o painel não mostraria qual etapa custa o quê."""
    caixa.add_email(assunto="Pedido")
    _, resumo, _ = await _rodar()

    assert resumo["usage_classificacao"]["input"] == 10
    assert resumo["usage_resumo"]["input"] == 4


# ---------------------------------------------------------------------------
# Marca d'água e rodada travada
# ---------------------------------------------------------------------------


async def test_a_janela_de_varredura_respeita_o_piso_do_lookback(caixa, modelo):
    """Primeira execução numa caixa antiga não pode tentar classificar tudo."""
    classificacao_config.salvar_config(TENANT, lookback_horas=24)
    caixa.add_email(assunto="Recente", horas_atras=2)
    caixa.add_email(assunto="Antigo", horas_atras=100)

    _, resumo, _ = await _rodar()

    assert resumo["total_emails"] == 1
    assert resumo["classificados"] == 1


async def test_rodada_travada_e_encerrada_como_erro(banco, organizacao):
    """Sem isso, um processo morto deixa a UI girando para sempre."""
    from sqlmodel import Session

    velha = brt_now() - timedelta(minutes=classificacao.LIMITE_TRAVADO_MINUTOS + 5)
    with Session(banco) as s:
        s.add(ClassificacaoRun(tenant_id=TENANT, status="running", started_at=velha))
        s.commit()

    aviso = classificacao.recuperar_rodada_travada(TENANT)

    assert aviso is not None
    with Session(banco) as s:
        linha = s.query(ClassificacaoRun).one()
    assert linha.status == "error"
    assert "não finalizou" in linha.erro
    assert linha.finished_at is not None


async def test_rodada_recente_em_andamento_nao_e_encerrada(banco, organizacao):
    from sqlmodel import Session

    with Session(banco) as s:
        s.add(ClassificacaoRun(tenant_id=TENANT, status="running"))
        s.commit()

    assert classificacao.recuperar_rodada_travada(TENANT) is None
    assert classificacao.ha_rodada_em_andamento(TENANT) is True


# ---------------------------------------------------------------------------
# Zerar contadores: o que o botão do painel apaga, e o que ele NÃO pode apagar
# ---------------------------------------------------------------------------
def _zerar(tenant=TENANT):
    """Reproduz o que `DashboardState.zerar_contadores` faz no banco.

    O handler vive no State e depende de sessão de browser; o que importa
    travar aqui é a REGRA: quais tabelas somem e quais sobrevivem.
    """
    import reflex as rx

    from sales_support_agent.models import (
        ClassificacaoRun, EmailClassificado, ResumoEmail,
    )

    with rx.session() as s:
        ids = [
            e.id for e in s.query(EmailClassificado)
            .filter(EmailClassificado.tenant_id == tenant).all()
        ]
        resumos = 0
        if ids:
            resumos = (
                s.query(ResumoEmail).filter(ResumoEmail.email_id.in_(ids))
                .delete(synchronize_session=False)
            )
            s.flush()
        emails = (
            s.query(EmailClassificado).filter(EmailClassificado.tenant_id == tenant)
            .delete(synchronize_session=False)
        )
        rodadas = (
            s.query(ClassificacaoRun).filter(ClassificacaoRun.tenant_id == tenant)
            .delete(synchronize_session=False)
        )
        s.commit()
    return emails, resumos, rodadas


async def test_zerar_apaga_execucoes_emails_e_resumos(caixa, modelo, banco):
    from sqlmodel import Session

    from sales_support_agent.models import ClassificacaoRun, EmailClassificado, ResumoEmail

    caixa.add_email(assunto="Pedido")
    await _rodar()

    emails, resumos, rodadas = _zerar()

    assert emails == 1 and resumos == 1 and rodadas == 1
    with Session(banco) as s:
        assert s.query(EmailClassificado).count() == 0
        assert s.query(ResumoEmail).count() == 0
        assert s.query(ClassificacaoRun).count() == 0


async def test_zerar_nao_toca_no_custo_nem_na_configuracao(caixa, modelo, banco):
    """O gasto aconteceu de verdade, e a pasta vinculada não é contador."""
    from sqlmodel import Session

    from sales_support_agent.models import PastaClasse, TokenUsage

    caixa.add_email(assunto="Pedido")
    await _rodar()

    with Session(banco) as s:
        s.add(TokenUsage(
            tenant_id=TENANT, agent_name="classificacao_agent",
            model="gpt-5.4-mini", input_tokens=100, output_tokens=10,
        ))
        s.commit()
        pastas_antes = s.query(PastaClasse).count()

    _zerar()

    with Session(banco) as s:
        assert s.query(TokenUsage).count() == 1, "zerar o painel reescreveu o custo"
        assert s.query(PastaClasse).count() == pastas_antes, "zerar desvinculou as pastas"
        cfg = classificacao_config.get_config(TENANT)
        assert cfg["horario_1"] and cfg["horario_2"], "zerar apagou os horários"


async def test_depois_de_zerar_o_mesmo_email_e_reprocessado(caixa, modelo, banco):
    """A consequência de custo que o aviso do diálogo precisa declarar.

    O registro apagado é justamente a trava que impede pagar duas vezes pelo
    mesmo e-mail.
    """
    caixa.add_email(assunto="Pedido")
    await _rodar()
    chamadas_primeira = modelo.chamadas
    assert chamadas_primeira > 0

    # De volta à caixa de entrada, como estaria numa janela sobreposta: é o
    # único cenário em que a deduplicação de fato age.
    def _devolver():
        for mensagem in caixa.mensagens.values():
            mensagem["_pasta"] = "inbox"

    _devolver()
    _, resumo, erro = await _rodar()
    assert resumo["puladas"] == 1, "a deduplicação não agiu antes de zerar"

    _zerar()
    _devolver()
    _, resumo, erro = await _rodar()

    assert erro is None
    assert resumo["puladas"] == 0
    assert resumo["classificados"] == 1
    assert modelo.chamadas > chamadas_primeira, "não reclassificou depois de zerar"


# ---------------------------------------------------------------------------
# Prioridade em duas faixas e marca de procedência
# ---------------------------------------------------------------------------
async def test_data_alem_da_janela_vira_importante_e_nao_urgente(caixa, modelo, banco):
    """Data distante é compromisso assumido, e antes sumia junto com o resto."""
    from sqlmodel import Session

    caixa.add_email(assunto="Entrega em duas semanas")
    modelo.resposta = {
        "classe": "pedido", "confianca": 90, "urgencia_prazo_horas": 336,
        "urgente_semantico": False, "urgente": False, "importante": True,
        "justificativa": "x",
    }

    _, resumo, erro = await _rodar()

    assert erro is None
    assert resumo["urgentes"] == 0
    assert resumo["importantes"] == 1

    marcada = next(iter(caixa.mensagens.values()))
    assert "Importante" in marcada["categorias"]
    assert "Urgente" not in marcada["categorias"]

    with Session(banco) as s:
        linha = s.query(EmailClassificado).first()
        assert linha.importante is True and linha.urgente is False


async def test_todo_classificado_recebe_a_marca_de_classificado_por_ia(caixa, modelo):
    """Permite separar no Outlook o que o agente arquivou do que foi à mão."""
    from sales_support_agent.services.classificacao_rules import CATEGORIA_IA

    caixa.add_email(assunto="Pedido")

    await _rodar()

    assert CATEGORIA_IA in next(iter(caixa.mensagens.values()))["categorias"]


async def test_ignorado_nao_recebe_marca_nenhuma(caixa, modelo):
    """Ele não foi tocado, e dizer que foi seria mentira no Outlook."""
    caixa.add_email(assunto="Newsletter")
    modelo.resposta = {
        "classe": "", "confianca": 20, "urgencia_prazo_horas": None,
        "urgente_semantico": False, "urgente": False, "importante": False,
        "justificativa": "x",
    }

    _, resumo, _ = await _rodar()

    assert resumo["ignorados"] == 1
    assert next(iter(caixa.mensagens.values()))["categorias"] == []
    assert not caixa.houve("mover_mensagem")


async def test_o_sinal_semantico_e_persistido_para_o_recalculo(caixa, modelo, banco):
    """Sem persistir, o recálculo teria de adivinhar pela ausência de prazo."""
    from sqlmodel import Session

    caixa.add_email(assunto="Estamos parados esperando")
    modelo.resposta = {
        "classe": "pedido", "confianca": 90, "urgencia_prazo_horas": None,
        "urgente_semantico": True, "urgente": True, "importante": False,
        "justificativa": "x",
    }

    await _rodar()

    with Session(banco) as s:
        assert s.query(EmailClassificado).first().urgente_semantico is True


# ---------------------------------------------------------------------------
# Progresso visível para a execução agendada
# ---------------------------------------------------------------------------
async def test_o_andamento_e_gravado_na_rodada_a_cada_email(caixa, modelo, banco):
    """É o que faz a barra de progresso funcionar para o AGENDADO.

    A rodada agendada acontece noutro processo e nunca toca o State do
    navegador de ninguém: sem o andamento na linha, quem abre o painel às 08:01
    vê uma tela parada enquanto o agente trabalha.
    """
    from sqlmodel import Session

    from sales_support_agent.services import emails_query

    # O agendado só roda com o interruptor ligado (ver o botão Iniciar do
    # painel); sem isto a rodada recusaria antes de qualquer progresso.
    classificacao_config.salvar_config(TENANT, ativo=True)
    for i in range(3):
        caixa.add_email(assunto=f"Pedido {i}", imid=f"<p{i}@t.com>")

    vistos = []
    original = classificacao._marcar_progresso

    def _espiao(run_id, processados, total):
        original(run_id, processados, total)
        with Session(banco) as s:
            linha = s.get(ClassificacaoRun, run_id)
            vistos.append((linha.processados, linha.total_emails))

    classificacao._marcar_progresso = _espiao
    try:
        await _rodar(origem="agendado")
    finally:
        classificacao._marcar_progresso = original

    assert (0, 3) in vistos, "o total precisa aparecer antes do primeiro e-mail"
    assert (3, 3) in vistos, "o andamento final precisa chegar à linha da rodada"


async def test_progresso_execucao_enxerga_a_rodada_em_andamento(caixa, modelo, banco):
    """A consulta que a tela sonda enquanto o agente trabalha."""
    from sqlmodel import Session

    from sales_support_agent.services import emails_query

    with Session(banco) as s:
        rodada = ClassificacaoRun(
            tenant_id=TENANT, origem="agendado", status="running",
            processados=2, total_emails=5,
        )
        s.add(rodada)
        s.commit()

    p = emails_query.progresso_execucao(TENANT)

    assert p["em_andamento"] is True
    assert p["origem"] == "agendado"
    assert (p["processados"], p["total"]) == (2, 5)


async def test_progresso_execucao_reporta_a_ultima_concluida(caixa, modelo, banco):
    """É a comparação de id que transforma "terminou" em "acabou de terminar"."""
    from sales_support_agent.services import emails_query

    from sqlmodel import Session

    from sales_support_agent.services import agendador

    caixa.add_email(assunto="Pedido")
    eventos, resumo, _ = await _rodar()

    # `_rodar` não fecha a linha: quem fecha, no código de verdade, é o
    # `finalizar_rodada` do agendador, e é ele que muda o status para "done".
    with Session(banco) as s:
        run_id = s.query(ClassificacaoRun).order_by(ClassificacaoRun.id.desc()).first().id
    agendador.finalizar_rodada(run_id, resumo or {}, "")

    p = emails_query.progresso_execucao(TENANT)

    assert p["em_andamento"] is False
    assert p["ultima_run_id"] == run_id
    assert p["ultima_status"] == "done"
    assert p["ultima_classificados"] == 1
