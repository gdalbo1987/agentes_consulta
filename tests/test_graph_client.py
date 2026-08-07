"""O cliente Graph, contra um Graph mockado com respx.

Todo teste aqui pede a fixture `respx_mock`, que é a declaração explícita de que
o teste mocka HTTP. Nenhum deles toca a caixa de verdade.

O foco é nos quatro detalhes do protocolo que, se ignorados, quebram o produto
em produção sem quebrar nada em desenvolvimento: a substituição do array de
categorias pelo PATCH, a troca de id no move, o corpo em HTML e a conversão de
fuso.
"""

from datetime import datetime

import httpx
import pytest

from sales_support_agent.services import graph_client as gc
from sales_support_agent.services.graph_client import GraphClientError

CAIXA = "comercial@teste.local"
BASE = f"https://graph.microsoft.com/v1.0/users/{CAIXA}"

# Capturado no import, ANTES de a fixture autouse abaixo trocar o `_config` por
# um dublê. É a única forma de um teste exercitar o `_config` de verdade.
_CONFIG_REAL = gc._config

CONFIG = {
    "sender_email": CAIXA,
    "tenant_id": "tenant-de-teste",
    "client_id": "client-de-teste",
    "client_secret": "segredo-de-teste",
    "pasta_origem": "inbox",
}


@pytest.fixture(autouse=True)
def _sem_entra_id(monkeypatch):
    """Curto-circuita o MSAL: token de mentira, sem ida ao Entra ID."""
    monkeypatch.setattr(gc, "_config", lambda: dict(CONFIG))
    monkeypatch.setattr(gc, "adquirir_token", lambda *a, **k: "token-de-mentira")
    # Sem espera de verdade: os testes de retry não podem custar segundos.
    monkeypatch.setattr(gc, "_dormir_backoff", _nao_dormir)


async def _nao_dormir(tentativa, retry_after):
    return None


def _pasta(pid, nome, filhas=0, pai=""):
    return {"id": pid, "displayName": nome, "childFolderCount": filhas, "parentFolderId": pai}


# ---------------------------------------------------------------------------
# Fuso e saneamento (puros, mas críticos)
# ---------------------------------------------------------------------------


def test_conversao_de_utc_para_brt():
    """Um erro de três horas aqui envenenaria urgência e marca d'água em silêncio."""
    assert gc.utc_para_brt("2026-08-07T12:00:00Z") == datetime(2026, 8, 7, 9, 0, 0)
    assert gc.utc_para_brt("2026-08-07T01:30:00Z") == datetime(2026, 8, 6, 22, 30, 0)


def test_ida_e_volta_do_fuso_e_estavel():
    momento = datetime(2026, 8, 7, 9, 0, 0)
    assert gc.utc_para_brt(gc.brt_para_utc_iso(momento)) == momento


def test_saneamento_remove_invisiveis_e_trunca():
    """Caracteres invisíveis escondem instrução dentro do corpo de um e-mail."""
    sujo = "Bom​dia‮ amigo"
    assert gc.sanear_corpo(sujo) == "Bomdia amigo"

    assert gc.sanear_corpo("a" * 20000).endswith("[corpo truncado]")
    assert len(gc.sanear_corpo("a" * 20000)) < 20000


# ---------------------------------------------------------------------------
# Pastas
# ---------------------------------------------------------------------------


async def test_pastas_aninhadas_ganham_o_caminho_completo(respx_mock):
    respx_mock.get(f"{BASE}/mailFolders", params__contains={"$top": "100"}).mock(
        return_value=httpx.Response(200, json={"value": [_pasta("p1", "Caixa de Entrada", filhas=1)]})
    )
    respx_mock.get(f"{BASE}/mailFolders/p1/childFolders").mock(
        return_value=httpx.Response(200, json={"value": [_pasta("p2", "Pedidos", pai="p1")]})
    )

    pastas = await gc.listar_pastas()
    caminhos = {p["caminho"] for p in pastas}
    assert caminhos == {"Caixa de Entrada", "Caixa de Entrada/Pedidos"}


async def test_resolver_pasta_ignora_acento_e_caixa(respx_mock):
    respx_mock.get(f"{BASE}/mailFolders").mock(
        return_value=httpx.Response(200, json={"value": [_pasta("p9", "Revisão de Pedidos")]})
    )

    achado = await gc.resolver_pasta("revisao de pedidos")
    assert achado["encontrado"] is True
    assert achado["id"] == "p9"


async def test_nome_ambiguo_nao_e_resolvido_por_chute(respx_mock):
    """Duas pastas com o mesmo nome sob pais diferentes: a UI tem de perguntar.

    Escolher a primeira arquivaria e-mail na pasta errada em silêncio, que é
    pior do que devolver a ambiguidade e pedir para o usuário desambiguar.
    """
    respx_mock.get(f"{BASE}/mailFolders", params__contains={"$top": "100"}).mock(
        return_value=httpx.Response(
            200,
            json={"value": [_pasta("a", "Comercial", filhas=1), _pasta("b", "Arquivo", filhas=1)]},
        )
    )
    respx_mock.get(f"{BASE}/mailFolders/a/childFolders").mock(
        return_value=httpx.Response(200, json={"value": [_pasta("a1", "Pedidos", pai="a")]})
    )
    respx_mock.get(f"{BASE}/mailFolders/b/childFolders").mock(
        return_value=httpx.Response(200, json={"value": [_pasta("b1", "Pedidos", pai="b")]})
    )

    achado = await gc.resolver_pasta("Pedidos")
    assert achado["encontrado"] is False
    assert achado["candidatos"] == ["Arquivo/Pedidos", "Comercial/Pedidos"]

    # Mas o caminho completo desfaz a ambiguidade.
    exato = await gc.resolver_pasta("Comercial/Pedidos")
    assert exato["encontrado"] is True and exato["id"] == "a1"


# ---------------------------------------------------------------------------
# Mensagens
# ---------------------------------------------------------------------------


def _msg(mid, imid="<a@b.com>", assunto="Assunto", corpo="Corpo"):
    return {
        "id": mid,
        "internetMessageId": imid,
        "conversationId": "c1",
        "subject": assunto,
        "from": {"emailAddress": {"address": "Cliente@Empresa.com", "name": "Cliente"}},
        "receivedDateTime": "2026-08-07T12:00:00Z",
        "categories": [],
        "webLink": "https://outlook/x",
        "body": {"content": corpo},
    }


async def test_pede_o_corpo_em_texto_puro(respx_mock):
    """Sem o header Prefer, o Graph devolve HTML e o modelo paga token por tag."""
    rota = respx_mock.get(url__startswith=f"{BASE}/mailFolders/inbox/messages").mock(
        return_value=httpx.Response(200, json={"value": [_msg("m1")]})
    )
    await gc.listar_mensagens(datetime(2026, 8, 1))

    enviado = rota.calls[0].request
    assert enviado.headers.get("Prefer") == 'outlook.body-content-type="text"'


async def test_paginacao_segue_o_nextlink_e_respeita_o_teto(respx_mock):
    respx_mock.get(url__startswith=f"{BASE}/mailFolders/inbox/messages").mock(
        return_value=httpx.Response(
            200,
            json={"value": [_msg("m1"), _msg("m2", imid="<2@b>")], "@odata.nextLink": f"{BASE}/pagina2"},
        )
    )
    respx_mock.get(f"{BASE}/pagina2").mock(
        return_value=httpx.Response(200, json={"value": [_msg("m3", imid="<3@b>")]})
    )

    todas = await gc.listar_mensagens(datetime(2026, 8, 1))
    assert len(todas) == 3

    limitadas = await gc.listar_mensagens(datetime(2026, 8, 1), limite=2)
    assert len(limitadas) == 2


async def test_mensagem_sem_internet_message_id_nao_e_descartada(respx_mock):
    """Rascunho e algumas mensagens de sistema não têm o campo.

    Pular em silêncio esconderia e-mail do usuário. Cair para o id do Graph com
    prefixo é pior para a deduplicação, mas visível: a flag avisa quem chama.
    """
    sem_imid = _msg("m5")
    sem_imid["internetMessageId"] = ""
    respx_mock.get(url__startswith=f"{BASE}/mailFolders/inbox/messages").mock(
        return_value=httpx.Response(200, json={"value": [sem_imid]})
    )

    achadas = await gc.listar_mensagens(datetime(2026, 8, 1))
    assert achadas[0]["internet_message_id"] == "graphid:m5"
    assert achadas[0]["sem_internet_message_id"] is True


async def test_remetente_e_normalizado_para_minusculas(respx_mock):
    """`remetente_email` é a chave de "por cliente" nas consultas do Agente 3."""
    respx_mock.get(url__startswith=f"{BASE}/mailFolders/inbox/messages").mock(
        return_value=httpx.Response(200, json={"value": [_msg("m1")]})
    )
    achadas = await gc.listar_mensagens(datetime(2026, 8, 1))
    assert achadas[0]["remetente_email"] == "cliente@empresa.com"


# ---------------------------------------------------------------------------
# Categorias e move: os dois detalhes que quebram em produção
# ---------------------------------------------------------------------------


async def test_patch_de_categorias_preserva_as_que_o_usuario_marcou(respx_mock):
    """O PATCH do Graph SUBSTITUI o array inteiro.

    Mandar só as categorias novas apagaria as que o usuário marcou à mão no
    Outlook, e ele não teria como saber que foi a plataforma.
    """
    respx_mock.get(f"{BASE}/messages/m1").mock(
        return_value=httpx.Response(200, json={"categories": ["Marcado por mim"]})
    )
    patch = respx_mock.patch(f"{BASE}/messages/m1").mock(return_value=httpx.Response(200, json={}))

    final = await gc.aplicar_categorias("m1", ["Pedido", "Urgente"])

    assert final == ["Marcado por mim", "Pedido", "Urgente"]
    import json as _json

    enviado = _json.loads(patch.calls[0].request.content)
    assert enviado["categories"] == ["Marcado por mim", "Pedido", "Urgente"]


async def test_nao_faz_patch_quando_nao_ha_o_que_acrescentar(respx_mock):
    respx_mock.get(f"{BASE}/messages/m1").mock(
        return_value=httpx.Response(200, json={"categories": ["Pedido"]})
    )
    patch = respx_mock.patch(f"{BASE}/messages/m1").mock(return_value=httpx.Response(200, json={}))

    await gc.aplicar_categorias("m1", ["Pedido"])
    assert patch.call_count == 0


async def test_move_devolve_o_id_novo(respx_mock):
    """O POST /move cria um recurso NOVO: o id antigo deixa de existir.

    Quem chama precisa guardar o retorno; senão o próximo PATCH bate num id
    morto e volta 404. É também por isso que a categoria vai ANTES do move.
    """
    respx_mock.post(f"{BASE}/messages/m1/move").mock(
        return_value=httpx.Response(201, json={"id": "id-completamente-novo"})
    )
    assert await gc.mover_mensagem("m1", "pasta-destino") == "id-completamente-novo"


async def test_move_sem_id_novo_e_erro_e_nao_silencio(respx_mock):
    respx_mock.post(f"{BASE}/messages/m1/move").mock(return_value=httpx.Response(201, json={}))

    with pytest.raises(GraphClientError, match="não devolveu o id novo"):
        await gc.mover_mensagem("m1", "pasta-destino")


# ---------------------------------------------------------------------------
# Erros
# ---------------------------------------------------------------------------


async def test_429_e_retentado_honrando_o_retry_after(respx_mock):
    rota = respx_mock.get(f"{BASE}/mailFolders")
    rota.side_effect = [
        httpx.Response(429, headers={"Retry-After": "1"}, json={}),
        httpx.Response(200, json={"value": [_pasta("p1", "Caixa de Entrada")]}),
    ]

    pastas = await gc.listar_pastas()
    assert len(pastas) == 1
    assert rota.call_count == 2


async def test_429_insistente_desiste_com_mensagem_em_portugues(respx_mock):
    respx_mock.get(f"{BASE}/mailFolders").mock(return_value=httpx.Response(429, json={}))

    with pytest.raises(GraphClientError) as erro:
        await gc.listar_pastas()
    assert "limitou a taxa" in str(erro.value)
    assert erro.value.fatal is False  # é transitório, não derruba a configuração


async def test_401_e_fatal_e_nao_e_retentado(respx_mock):
    rota = respx_mock.get(f"{BASE}/mailFolders").mock(return_value=httpx.Response(401, json={}))

    with pytest.raises(GraphClientError) as erro:
        await gc.listar_pastas()

    assert erro.value.fatal is True
    assert "Credenciais" in str(erro.value)
    assert rota.call_count == 1, "credencial errada não melhora com repetição"


async def test_403_nomeia_a_permissao_que_esta_faltando(respx_mock):
    """A mensagem tem de dizer O QUE fazer, não só que deu errado.

    Mail.Send e Mail.ReadWrite falham igual na tela e são coisas diferentes no
    Entra ID: sem nomear a permissão, o operador procura no lugar errado.
    """
    respx_mock.get(f"{BASE}/mailFolders").mock(return_value=httpx.Response(403, json={}))

    with pytest.raises(GraphClientError) as erro:
        await gc.listar_pastas()

    texto = str(erro.value)
    assert "Mail.ReadWrite" in texto
    assert "Mail.Send" in texto
    assert erro.value.fatal is True


async def test_404_de_caixa_inexistente_diz_o_que_esta_errado(respx_mock):
    """Caixa que não existe no locatário e pasta que sumiu são 404 diferentes.

    O caso real que motivou a distinção: uma conta pessoal (hotmail.com) posta
    como caixa a monitorar. O fluxo de APLICAÇÃO da Graph só enxerga caixas do
    próprio locatário, então ela devolve `ErrorInvalidUser` e nenhuma
    credencial resolve. Sem nomear isso, a mensagem "pasta não encontrada"
    manda o operador procurar a pasta errada por horas.
    """
    respx_mock.get(f"{BASE}/mailFolders").mock(
        return_value=httpx.Response(
            404,
            json={"error": {"code": "ErrorInvalidUser", "message": "The requested user is invalid."}},
        )
    )

    with pytest.raises(GraphClientError) as erro:
        await gc.listar_pastas()

    texto = str(erro.value)
    assert "não existe neste locatário" in texto
    assert "conta pessoal" in texto
    assert erro.value.fatal is True, "caixa inexistente é configuração: retentar não resolve"


async def test_404_de_pasta_continua_sendo_erro_de_um_item_so(respx_mock):
    respx_mock.post(f"{BASE}/messages/m1/move").mock(
        return_value=httpx.Response(404, json={"error": {"code": "ErrorItemNotFound"}})
    )

    with pytest.raises(GraphClientError) as erro:
        await gc.mover_mensagem("m1", "pasta-x")

    assert erro.value.fatal is False, "um item perdido não pode derrubar a rodada inteira"


async def test_erro_5xx_e_retentado(respx_mock):
    rota = respx_mock.get(f"{BASE}/mailFolders")
    rota.side_effect = [
        httpx.Response(503, json={}),
        httpx.Response(200, json={"value": []}),
    ]
    assert await gc.listar_pastas() == []
    assert rota.call_count == 2


def test_configuracao_incompleta_para_antes_de_qualquer_requisicao(monkeypatch):
    """Sem credencial completa, nem a primeira requisição sai.

    Usa `_CONFIG_REAL`, capturado no import antes de a fixture autouse trocar o
    `_config` pelo de mentira. Sem essa captura o teste exercitaria o próprio
    dublê e passaria dizendo nada.
    """
    monkeypatch.setattr(
        "sales_support_agent.services.settings.get_graph_config",
        lambda: {"sender_email": "", "tenant_id": "", "client_id": "", "client_secret": ""},
    )

    with pytest.raises(GraphClientError) as erro:
        _CONFIG_REAL()

    assert erro.value.fatal is True, "configuração faltando é fatal: não adianta retentar"
    for rotulo in ("remetente", "tenant ID", "client ID", "client secret"):
        assert rotulo in str(erro.value), f"a mensagem não diz que falta {rotulo}"


async def test_categorias_mestre_engolem_403(respx_mock):
    """Categoria fora da lista mestra FUNCIONA, só aparece sem cor.

    Não vale exigir a permissão MailboxSettings.ReadWrite por uma questão
    estética, então o 403 aqui é engolido e a rodada segue.
    """
    respx_mock.get(f"{BASE}/outlook/masterCategories").mock(return_value=httpx.Response(403, json={}))
    assert await gc.garantir_categorias_mestre(["Pedido"]) is False
