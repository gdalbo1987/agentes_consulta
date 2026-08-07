"""Testes que falam com uma caixa REAL da Microsoft Graph.

DESLIGADOS por padrão (`addopts = -m "not graph_funcional"` no pytest.ini). Para
rodar:

    pytest -m graph_funcional

Três coisas protegem a caixa de produção mesmo aqui:

* a fixture `graph_funcional` PULA o teste se `TESTER_GRAPH_SENDER_EMAIL` estiver
  ausente ou for igual a `GRAPH_SENDER_EMAIL`;
* todo teste deste arquivo é SOMENTE LEITURA. Nada aqui marca, move ou envia;
* a configuração do Graph é injetada a partir das variáveis `TESTER_*`, então
  nem por engano estes testes usam a credencial de produção.

O que se prova aqui não dá para provar com mock: que o registro de aplicativo
tem de fato o consentimento de `Mail.ReadWrite`, e que a caixa configurada
existe. É o teste a rodar depois de mexer no Entra ID.
"""

import pytest

from sales_support_agent.services import graph_client as gc

pytestmark = pytest.mark.graph_funcional


@pytest.fixture
def graph_tester(graph_funcional, monkeypatch):
    """Aponta o cliente para a caixa TESTER, nunca para a de produção."""
    cfg = dict(graph_funcional)
    cfg.setdefault("pasta_origem", "inbox")
    monkeypatch.setattr(gc, "_config", lambda: dict(cfg))
    return cfg


async def test_credencial_tester_autentica_e_le_as_pastas(graph_tester):
    """Prova o consentimento de leitura. `Mail.Send` sozinha faria isto dar 403."""
    pastas = await gc.listar_pastas()

    assert pastas, "a caixa TESTER não devolveu nenhuma pasta"
    assert all(p["id"] and p["nome"] for p in pastas)


async def test_caixa_de_entrada_resolve_pelo_nome_bem_conhecido(graph_tester):
    """`inbox` é o alias que funciona em qualquer idioma do locatário.

    Numa caixa em português o `displayName` é "Caixa de Entrada", então depender
    do nome exibido quebraria conforme o idioma de quem configurou a caixa.
    """
    mensagens = await gc.listar_mensagens(
        desde=__import__("datetime").datetime(2000, 1, 1), pasta="inbox", limite=5
    )
    assert isinstance(mensagens, list)


async def test_leitura_traz_os_campos_que_o_agente_precisa(graph_tester):
    from datetime import datetime

    mensagens = await gc.listar_mensagens(desde=datetime(2000, 1, 1), limite=3)
    if not mensagens:
        pytest.skip("a caixa TESTER está vazia")

    primeira = mensagens[0]
    for campo in ("internet_message_id", "graph_message_id", "assunto",
                  "remetente_email", "recebido_em", "corpo_texto"):
        assert campo in primeira

    # O corpo tem de vir em texto puro. Se voltar HTML, o header Prefer parou de
    # ser mandado e o custo em tokens de cada e-mail sobe sem ninguém notar.
    assert "<html" not in primeira["corpo_texto"].lower()


async def test_testar_leitura_responde_o_que_o_painel_mostra(graph_tester):
    resultado = await gc.testar_leitura()

    assert resultado["ok"] is True
    assert resultado["total_pastas"] >= 1
