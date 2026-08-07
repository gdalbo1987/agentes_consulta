"""Leitura e escrita na caixa de e-mails pela Microsoft Graph API.

Módulo separado de `graph_mailer.py` de propósito. Aquele tem 130 linhas, um
endpoint só, e está no caminho crítico do convite e da redefinição de senha:
misturar nele um leitor de caixa faria cada bug de um ameaçar o outro. Aquele é
síncrono porque roda dentro de um event handler comum; este é ASSÍNCRONO porque
roda dentro de background task do Reflex e do agendador, onde uma chamada
bloqueante travaria o event loop.

Toda requisição passa por `_request`. Esse funil único é o que dá um lugar só
para `Authorization`, timeout, backoff de 429 e tradução de erro para
português. É também a âncora da trava de rede da suíte de testes.

QUATRO DETALHES DO GRAPH que mudam o resultado se forem ignorados:

1. `PATCH categories` SUBSTITUI o array inteiro. Sem ler as categorias atuais e
   fazer união, o agente apaga as marcações que o usuário fez à mão.
2. `POST /move` INVALIDA o id antigo e devolve um recurso novo, com id novo. Por
   isso a ordem por e-mail é obrigatoriamente: PATCH das categorias, DEPOIS
   move, e então gravar o id devolvido. Categorizar depois de mover exigiria
   rebuscar o id.
3. O corpo vem em HTML por padrão. O header
   `Prefer: outlook.body-content-type="text"` traz texto puro, que é o que
   alimenta o modelo sem gastar token com marcação.
4. `receivedDateTime` vem em UTC, e o projeto inteiro trabalha em BRT ingênuo
   (`brt_now`, UTC-3). A conversão é uma função nomeada com teste próprio: um
   erro de três horas envenenaria silenciosamente a marca d'água da varredura e
   todo o cálculo de urgência.
"""

import asyncio
import random
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import httpx

from sales_support_agent.services.graph_auth import (
    GRAPH,
    GraphAuthError,
    adquirir_token,
    campos_faltando,
)

_TIMEOUT = 30.0

# Retentar só o que adianta retentar. 401 e 403 são configuração errada: repetir
# não conserta e ainda atrasa a rodada.
_STATUS_RETENTAVEIS = (429, 500, 502, 503, 504)
_MAX_TENTATIVAS = 3
_BACKOFF_BASE = 1.0
_RETRY_AFTER_MAXIMO = 60.0

# Truncagem do corpo. Limita duas coisas ao mesmo tempo: o custo em tokens de
# cada e-mail e a superfície de injeção de prompt vinda de texto de terceiros.
LIMITE_CORPO = 8000

# Campos pedidos ao Graph. Explícitos porque o padrão traz a mensagem inteira,
# com anexos e cabeçalhos que nada aqui usa.
_SELECT_MENSAGEM = (
    "id,internetMessageId,conversationId,subject,from,receivedDateTime,"
    "categories,webLink,body,isRead"
)

_PAGINA = 50


class GraphClientError(Exception):
    """Falha ao falar com a Graph.

    `fatal=True` derruba a rodada inteira (credencial inválida, permissão
    faltando). `fatal=False` é problema de um item só e vira aviso, no mesmo
    padrão que o funil anterior usava para os clientes HTTP pagos.
    """

    def __init__(self, mensagem: str, *, http_status: Optional[int] = None, fatal: bool = False):
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.http_status = http_status
        self.fatal = fatal


# ---------------------------------------------------------------------------
# Conversão de tempo
# ---------------------------------------------------------------------------

_BRT = timezone(timedelta(hours=-3))


def utc_para_brt(texto_iso: str) -> datetime:
    """`2026-08-07T12:00:00Z` -> `2026-08-07 09:00:00` ingênuo.

    O Graph devolve sempre UTC com sufixo Z. O projeto guarda tudo em BRT sem
    fuso (`brt_now`). Misturar os dois deslocaria a marca d'água da varredura em
    três horas e faria todo prazo de urgência ser calculado errado, sem erro
    nenhum aparecer.
    """
    limpo = (texto_iso or "").strip().replace("Z", "+00:00")
    momento = datetime.fromisoformat(limpo)
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=timezone.utc)
    return momento.astimezone(_BRT).replace(tzinfo=None, microsecond=0)


def brt_para_utc_iso(momento: datetime) -> str:
    """BRT ingênuo -> ISO-8601 em UTC, para o `$filter` do Graph."""
    return (
        momento.replace(tzinfo=_BRT)
        .astimezone(timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


# ---------------------------------------------------------------------------
# Saneamento de texto
# ---------------------------------------------------------------------------

# Caracteres invisiveis: controle, marcas de direcionalidade e espacos de
# largura zero. Nao aparecem para quem le o e-mail, mas chegam inteiros ao
# modelo, entao um "ignore as instrucoes anteriores" escondido entre marcas de
# direcao passaria despercebido na revisao humana. Saem antes de virar prompt.
#
# As faixas sao NUMERICAS de proposito. Escreve-las como escape (ou pior, como
# o caractere literal) ja custou um modulo que o Python recusou carregar:
# "source code string cannot contain null bytes". Com numeros, o arquivo-fonte
# nao contem nenhum caractere estranho.
_FAIXAS_INVISIVEIS = (
    (0x00, 0x08), (0x0B, 0x0C), (0x0E, 0x1F), (0x7F, 0x7F),
    (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x206F), (0xFEFF, 0xFEFF),
)
_INVISIVEIS = re.compile(
    "[" + "".join(f"{chr(a)}-{chr(b)}" for a, b in _FAIXAS_INVISIVEIS) + "]"
)
_ESPACOS = re.compile(r"[ \t]+")
_LINHAS = re.compile(r"\n{3,}")


def sanear_corpo(texto: str, limite: int = LIMITE_CORPO) -> str:
    """Texto puro, sem invisíveis, com espaços colapsados e truncado."""
    if not texto:
        return ""
    limpo = unicodedata.normalize("NFC", texto)
    limpo = _INVISIVEIS.sub("", limpo)
    limpo = limpo.replace("\r\n", "\n").replace("\r", "\n")
    limpo = _ESPACOS.sub(" ", limpo)
    limpo = _LINHAS.sub("\n\n", limpo).strip()
    if len(limpo) > limite:
        limpo = limpo[:limite].rstrip() + "\n\n[corpo truncado]"
    return limpo


# ---------------------------------------------------------------------------
# Configuração e token
# ---------------------------------------------------------------------------


def _config() -> dict:
    from sales_support_agent.services.settings import get_graph_config

    cfg = get_graph_config()
    faltando = campos_faltando(cfg)
    if faltando:
        raise GraphClientError(
            "Microsoft Graph não configurada. Faltam: " + ", ".join(faltando) +
            ". Preencha em /admin, na seção de Integrações.",
            fatal=True,
        )
    return cfg


async def _token(cfg: dict) -> str:
    # O MSAL é síncrono e tem cache próprio; sai da thread do event loop.
    try:
        return await asyncio.to_thread(
            adquirir_token, cfg["tenant_id"], cfg["client_id"], cfg["client_secret"]
        )
    except GraphAuthError as erro:
        raise GraphClientError(str(erro), http_status=401, fatal=True) from erro


def _mensagem_de_erro(status: int, corpo: str) -> tuple:
    """(mensagem em português, é fatal?) para um status de erro do Graph."""
    if status == 401:
        return (
            "Credenciais da Microsoft Graph inválidas ou expiradas. Confira "
            "tenant ID, client ID e client secret em /admin.",
            True,
        )
    if status == 403:
        return (
            "A Microsoft Graph recusou o acesso à caixa. O registro de "
            "aplicativo precisa da permissão DE APLICAÇÃO Mail.ReadWrite com "
            "consentimento de administrador concedido. A permissão Mail.Send, "
            "usada para enviar convites, não cobre ler, marcar nem mover.",
            True,
        )
    if status == 404:
        # Dois 404 bem diferentes chegam aqui, e confundi-los custa horas de
        # procura no lugar errado. `ErrorInvalidUser` significa que a CAIXA não
        # existe no locatário: é erro de configuração e é fatal. Qualquer outro
        # 404 é uma pasta ou mensagem que sumiu, problema de um item só.
        if "ErrorInvalidUser" in corpo or "ResourceNotFound" in corpo:
            return (
                "A caixa de e-mails configurada não existe neste locatário do "
                "Entra ID. O fluxo de aplicação da Microsoft Graph só enxerga "
                "caixas do próprio locatário, então uma conta pessoal "
                "(hotmail.com, outlook.com, gmail.com) nunca funciona aqui, "
                "mesmo com as credenciais certas. Use uma caixa corporativa "
                "real do locatário e confira o endereço em /admin.",
                True,
            )
        return ("Pasta ou mensagem não encontrada na caixa configurada.", False)
    if status == 429:
        return ("A Microsoft Graph limitou a taxa de requisições (429).", False)
    if status >= 500:
        return (f"A Microsoft Graph respondeu com erro interno (HTTP {status}).", False)
    return (f"A Microsoft Graph recusou a requisição (HTTP {status}): {corpo[:300]}", False)


async def _dormir_backoff(tentativa: int, retry_after: Optional[str]) -> None:
    """Honra `Retry-After` quando o Graph manda um; senão, exponencial com jitter."""
    if retry_after:
        try:
            await asyncio.sleep(min(float(retry_after), _RETRY_AFTER_MAXIMO))
            return
        except (TypeError, ValueError):
            pass
    await asyncio.sleep(_BACKOFF_BASE * (2 ** tentativa) + random.uniform(0, 0.3))


# ---------------------------------------------------------------------------
# O funil único de requisição
# ---------------------------------------------------------------------------


async def _request(metodo: str, caminho_ou_url: str, *, cfg=None, prefer_texto=False, **kw):
    """Uma requisição ao Graph, com retry e erro traduzido.

    `caminho_ou_url` aceita tanto um caminho relativo a /v1.0/users/{caixa}
    quanto uma URL completa, que é o que o `@odata.nextLink` devolve.
    """
    cfg = cfg or _config()
    token = await _token(cfg)

    if caminho_ou_url.startswith("http"):
        url = caminho_ou_url
    else:
        url = f"{GRAPH}/v1.0/users/{cfg['sender_email']}{caminho_ou_url}"

    cabecalhos = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    if prefer_texto:
        cabecalhos["Prefer"] = 'outlook.body-content-type="text"'

    async with httpx.AsyncClient(timeout=_TIMEOUT) as cliente:
        for tentativa in range(_MAX_TENTATIVAS):
            try:
                resposta = await cliente.request(metodo, url, headers=cabecalhos, **kw)
            except (httpx.TimeoutException, httpx.TransportError) as erro:
                if tentativa == _MAX_TENTATIVAS - 1:
                    raise GraphClientError(
                        f"Não foi possível falar com a Microsoft Graph: {erro}"
                    ) from erro
                await _dormir_backoff(tentativa, None)
                continue

            if resposta.status_code < 400:
                return resposta

            if resposta.status_code in _STATUS_RETENTAVEIS and tentativa < _MAX_TENTATIVAS - 1:
                await _dormir_backoff(tentativa, resposta.headers.get("Retry-After"))
                continue

            mensagem, fatal = _mensagem_de_erro(resposta.status_code, resposta.text)
            raise GraphClientError(mensagem, http_status=resposta.status_code, fatal=fatal)

    raise GraphClientError("A Microsoft Graph não respondeu depois de várias tentativas.")


# ---------------------------------------------------------------------------
# Pastas
# ---------------------------------------------------------------------------


async def listar_pastas(max_niveis: int = 3) -> List[dict]:
    """Todas as pastas da caixa, achatadas, com o caminho completo montado.

    Achatado porque o `foreach` do Reflex não acessa dicionário aninhado, e o
    caminho ("Caixa de Entrada/Pedidos") é o que desfaz a ambiguidade de duas
    pastas com o mesmo nome sob pais diferentes.
    """
    cfg = _config()
    pastas: List[dict] = []

    async def _nivel(caminho_base: str, url: str, profundidade: int):
        if profundidade > max_niveis:
            return
        while url:
            resposta = await _request("GET", url, cfg=cfg)
            dados = resposta.json()
            for item in dados.get("value", []):
                nome = item.get("displayName", "")
                caminho = f"{caminho_base}/{nome}" if caminho_base else nome
                pastas.append(
                    {
                        "id": item.get("id", ""),
                        "nome": nome,
                        "caminho": caminho,
                        "parent_id": item.get("parentFolderId", ""),
                    }
                )
                if item.get("childFolderCount", 0):
                    await _nivel(
                        caminho,
                        f"/mailFolders/{item['id']}/childFolders?$top=100",
                        profundidade + 1,
                    )
            url = dados.get("@odata.nextLink", "")

    await _nivel("", "/mailFolders?$top=100", 1)
    return pastas


def _normalizar(texto: str) -> str:
    """Minúsculas e sem acento, para casar "Revisão" com "revisao"."""
    sem_acento = unicodedata.normalize("NFD", (texto or "").strip())
    return "".join(c for c in sem_acento if unicodedata.category(c) != "Mn").lower()


async def resolver_pasta(nome_ou_caminho: str) -> dict:
    """Nome digitado pelo usuário -> id da pasta.

    Devolve `{encontrado, id, caminho, candidatos}`. Ambiguidade NÃO é resolvida
    por chute: duas pastas com o mesmo nome sob pais diferentes devolvem
    `encontrado=False` e os dois caminhos em `candidatos`, para a UI perguntar
    qual delas. Escolher a primeira arquivaria e-mail na pasta errada em
    silêncio, que é pior do que pedir para o usuário desambiguar.
    """
    alvo = (nome_ou_caminho or "").strip()
    if not alvo:
        return {"encontrado": False, "id": "", "caminho": "", "candidatos": []}

    pastas = await listar_pastas()
    normalizado = _normalizar(alvo)

    # Caminho completo primeiro: é a forma sem ambiguidade possível.
    por_caminho = [p for p in pastas if _normalizar(p["caminho"]) == normalizado]
    if len(por_caminho) == 1:
        achada = por_caminho[0]
        return {"encontrado": True, "id": achada["id"], "caminho": achada["caminho"], "candidatos": []}

    por_nome = [p for p in pastas if _normalizar(p["nome"]) == normalizado]
    if len(por_nome) == 1:
        achada = por_nome[0]
        return {"encontrado": True, "id": achada["id"], "caminho": achada["caminho"], "candidatos": []}

    return {
        "encontrado": False,
        "id": "",
        "caminho": "",
        "candidatos": sorted(p["caminho"] for p in por_nome),
    }


# ---------------------------------------------------------------------------
# Mensagens
# ---------------------------------------------------------------------------


def _achatar_mensagem(item: dict) -> dict:
    """Mensagem do Graph -> dicionário plano, no fuso e no formato do projeto."""
    remetente = ((item.get("from") or {}).get("emailAddress") or {})
    corpo = (item.get("body") or {}).get("content", "")

    # `internetMessageId` falta em rascunho e em algumas mensagens de sistema.
    # Cair para o `id` com prefixo é melhor do que pular o e-mail em silêncio;
    # a rodada registra o aviso.
    imid = (item.get("internetMessageId") or "").strip()
    if not imid:
        imid = f"graphid:{item.get('id', '')}"

    return {
        "internet_message_id": imid,
        "graph_message_id": item.get("id", ""),
        "graph_conversation_id": item.get("conversationId", ""),
        "graph_web_link": item.get("webLink", ""),
        "assunto": (item.get("subject") or "").strip(),
        "remetente_email": (remetente.get("address") or "").strip().lower(),
        "remetente_nome": (remetente.get("name") or "").strip(),
        "recebido_em": utc_para_brt(item.get("receivedDateTime", "")),
        "corpo_texto": sanear_corpo(corpo),
        "categorias": list(item.get("categories") or []),
        "sem_internet_message_id": not (item.get("internetMessageId") or "").strip(),
    }


async def listar_mensagens(desde: datetime, pasta: str = "", limite: int = 200) -> List[dict]:
    """Mensagens recebidas a partir de `desde` (BRT ingênuo), mais novas primeiro.

    Varre por `$filter` em `receivedDateTime`, e não por delta query. O delta é a
    resposta certa em geral e a errada aqui: o token é escopado por pasta, e o
    comportamento normal deste agente é TIRAR mensagens dessa pasta toda rodada,
    o que gera eventos de remoção e deixa a semântica do token hostil ao que se
    quer, que é só "o que chegou de novo". A deduplicação por
    `internet_message_id` já torna gratuito reler uma janela sobreposta.
    """
    cfg = _config()
    pasta = pasta or cfg.get("pasta_origem") or "inbox"

    url = (
        f"/mailFolders/{pasta}/messages"
        f"?$select={_SELECT_MENSAGEM}"
        f"&$filter=receivedDateTime ge {brt_para_utc_iso(desde)}"
        f"&$orderby=receivedDateTime desc"
        f"&$top={_PAGINA}"
    )

    mensagens: List[dict] = []
    while url and len(mensagens) < limite:
        resposta = await _request("GET", url, cfg=cfg, prefer_texto=True)
        dados = resposta.json()
        for item in dados.get("value", []):
            mensagens.append(_achatar_mensagem(item))
            if len(mensagens) >= limite:
                break
        url = dados.get("@odata.nextLink", "")

    return mensagens


async def aplicar_categorias(message_id: str, categorias: List[str]) -> List[str]:
    """Acrescenta categorias à mensagem, PRESERVANDO as que já existem.

    O `PATCH` do Graph substitui o array inteiro. Mandar só as categorias novas
    apagaria as que o usuário marcou à mão no Outlook, e ele não teria como
    saber que foi a plataforma. Por isso lê antes e manda a união.

    Devolve a lista final. Não faz nada quando não há o que acrescentar.
    """
    if not categorias:
        return []

    cfg = _config()
    atual = await _request("GET", f"/messages/{message_id}?$select=categories", cfg=cfg)
    existentes = list(atual.json().get("categories") or [])

    final = list(existentes)
    for nome in categorias:
        if nome not in final:
            final.append(nome)

    if final == existentes:
        return final

    await _request("PATCH", f"/messages/{message_id}", cfg=cfg, json={"categories": final})
    return final


async def mover_mensagem(message_id: str, pasta_id: str) -> str:
    """Move a mensagem e devolve o ID NOVO.

    O `POST /move` cria um recurso novo na pasta de destino: o id antigo deixa
    de existir. Quem chama PRECISA guardar o retorno, senão o próximo PATCH bate
    num id morto e volta 404. É também por isso que a categoria é aplicada
    ANTES do move, nunca depois.
    """
    resposta = await _request(
        "POST", f"/messages/{message_id}/move", json={"destinationId": pasta_id}
    )
    novo_id = (resposta.json() or {}).get("id", "")
    if not novo_id:
        raise GraphClientError(
            "A Microsoft Graph moveu a mensagem mas não devolveu o id novo. "
            "Sem ele, a próxima rodada não consegue mais marcá-la."
        )
    return novo_id


async def garantir_categorias_mestre(nomes: List[str]) -> bool:
    """Registra as categorias na lista mestra do Outlook, para saírem coloridas.

    Best-effort de verdade: categoria fora da lista mestra FUNCIONA, só aparece
    sem cor. Isso depende de `MailboxSettings.ReadWrite`, uma permissão a mais
    que não vale exigir para uma questão estética. Um 403 aqui é engolido.
    """
    cores = ["preset0", "preset1", "preset2", "preset3", "preset4"]
    try:
        cfg = _config()
        resposta = await _request("GET", "/outlook/masterCategories", cfg=cfg)
        existentes = {c.get("displayName") for c in resposta.json().get("value", [])}

        for indice, nome in enumerate(nomes):
            if nome in existentes:
                continue
            await _request(
                "POST",
                "/outlook/masterCategories",
                cfg=cfg,
                json={"displayName": nome, "color": cores[indice % len(cores)]},
            )
        return True
    except GraphClientError:
        return False


async def testar_leitura() -> dict:
    """Prova que a credencial cobre LEITURA, e não só envio.

    `Mail.Send` e `Mail.ReadWrite` falham de formas diferentes. Sem um teste
    próprio para a leitura, o primeiro sinal de um consentimento faltando seria
    a rodada das 08:00 falhando, num horário em que ninguém está olhando.
    """
    pastas = await listar_pastas()
    return {"ok": True, "total_pastas": len(pastas), "pastas": [p["caminho"] for p in pastas[:20]]}
