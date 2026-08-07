"""Testa a própria trava que impede os testes de tocarem a caixa real.

Uma trava de segurança que ninguém testa é uma trava que talvez não exista. Aqui
se prova que ela dispara, nos três caminhos por onde o código poderia sair para
a Microsoft: httpx assíncrono (o cliente Graph novo), httpx síncrono e requests
(o graph_mailer e o MSAL por dentro).

`pytest.fail` levanta `Failed`, então dá para capturá-lo e afirmar que a trava
agiu, sem que a suíte fique vermelha.
"""

import httpx
import pytest
import requests
from _pytest.outcomes import Failed


def test_trava_barra_httpx_sincrono():
    with pytest.raises(Failed, match="graph.microsoft.com"):
        httpx.get("https://graph.microsoft.com/v1.0/me/messages", timeout=5)


async def test_trava_barra_httpx_assincrono():
    with pytest.raises(Failed, match="graph.microsoft.com"):
        async with httpx.AsyncClient(timeout=5) as cliente:
            await cliente.get("https://graph.microsoft.com/v1.0/users/x/messages")


def test_trava_barra_requests():
    with pytest.raises(Failed, match="graph.microsoft.com"):
        requests.post("https://graph.microsoft.com/v1.0/users/x/sendMail", timeout=5)


def test_trava_barra_a_aquisicao_de_token():
    """O login do Entra ID também é bloqueado.

    Sem isto, um teste distraído autenticaria de verdade no locatário da
    empresa. Não vaza e-mail, mas é tráfego real com credencial real.
    """
    with pytest.raises(Failed, match="login.microsoftonline.com"):
        requests.post("https://login.microsoftonline.com/abc/oauth2/v2.0/token", timeout=5)


def test_trava_e_cirurgica_e_so_pega_a_microsoft():
    """A trava deixa passar host que não é da Microsoft.

    Deliberadamente SEM `respx_mock`: com a fixture, a trava cede a vez e o
    teste passaria sem provar nada. Aqui ela está armada, e o alvo é a porta 9
    do próprio localhost (descarte), que recusa conexão na hora. O que se afirma
    é o tipo do erro: `ConnectError` significa que a trava deixou seguir e quem
    recusou foi a rede. Se a trava tivesse pegado, viria `Failed`.
    """
    with pytest.raises(httpx.ConnectError):
        httpx.get("http://127.0.0.1:9/qualquer-coisa", timeout=3)


def test_graph_mockado_com_respx_funciona(respx_mock):
    """Mock do Graph com respx passa; é assim que a suíte da Fase 5 funciona.

    A trava cede a vez quando o respx está no comando. Sem essa cessão ela
    barraria os próprios testes mockados e seria impossível testar o cliente
    Graph, que é justamente o código que mais precisa de teste.
    """
    respx_mock.get("https://graph.microsoft.com/v1.0/users/x/mailFolders").mock(
        return_value=httpx.Response(200, json={"value": [{"id": "1", "displayName": "Caixa de Entrada"}]})
    )
    resposta = httpx.get("https://graph.microsoft.com/v1.0/users/x/mailFolders")
    assert resposta.status_code == 200
    assert resposta.json()["value"][0]["displayName"] == "Caixa de Entrada"


def test_sob_respx_rota_nao_mockada_tambem_nao_escapa(respx_mock):
    """Esta é a prova de que ceder a vez ao respx não abre buraco.

    Com o respx ativo, a trava fica quieta. Se uma rota do Graph não estiver
    mockada, quem tem de barrar é o respx, pelo `assert_all_mocked`. Se um dia
    esse padrão mudar numa versão nova do respx, este teste quebra e avisa antes
    que algum teste comece a falar com a caixa de verdade.
    """
    respx_mock.get("https://graph.microsoft.com/v1.0/rota-mockada").mock(
        return_value=httpx.Response(200, json={})
    )
    with pytest.raises(Exception) as erro:
        httpx.get("https://graph.microsoft.com/v1.0/rota-QUE-NINGUEM-MOCKOU")

    assert "AllMockedAssertionError" in type(erro.value).__name__ or "not mocked" in str(erro.value).lower(), (
        "O respx deixou passar uma rota não mockada. A trava cede a vez para ele, "
        f"então isso seria uma chamada real. Exceção recebida: {type(erro.value).__name__}: {erro.value}"
    )
