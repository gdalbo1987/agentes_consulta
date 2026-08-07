"""Client HTTP da API Hunter.io v2 (busca do e-mail profissional de uma pessoa).

Doc oficial: https://hunter.io/api-documentation/v2

Usado numa etapa só do pipeline: depois que o enriquecimento já descobriu os
contatos decisores de uma empresa (nome + cargo, via QSA da Receita ou
LinkedIn/KipFlow), este módulo tenta descobrir o e-mail de cada um a partir do
NOME + DOMÍNIO da empresa (`GET /v2/email-finder`).

Responsabilidades deste módulo e de mais nenhum outro:
- autenticação por `api_key` na query string, lida de `HunterAccount`;
- **o balanceamento entre as contas** (ver `Balanceador`): o Hunter vende
  créditos por conta, então N contas dão N vezes a cota. A busca vai sempre
  para a conta com mais créditos sobrando no ciclo;
- rate limit (a doc publica 15 req/s e 500 req/min para este endpoint);
- retry com backoff em 403/5xx/timeout;
- tradução dos erros para pt-BR, no mesmo padrão de `services/kipflow_client`;
- **o gate da cota de créditos do ciclo**, que é o motivo de existir o
  `HunterUsage` (ver `creditos_restantes`). O ciclo segue o aniversário da
  assinatura no Hunter, não o mês civil — ver `inicio_do_ciclo`.

SEGREDO: a chave nunca é impressa, logada, colocada em exceção, em toast ou em
qualquer campo de State (o State é serializado para o browser). Atenção
redobrada aqui em relação à KipFlow: como o Hunter autentica por QUERY STRING,
a URL completa contém a chave — por isso nenhuma mensagem de erro deste módulo
inclui a URL, só o path.

Assíncrono (`httpx`) porque o enriquecimento roda dentro de um
`@rx.event(background=True)`.
"""

import asyncio
import calendar
import random
import re
import time
import unicodedata
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import httpx

BASE_URL = "https://api.hunter.io/v2"
TIMEOUT_SEGUNDOS = 30.0

# A doc publica 15 req/s e 500 req/min para o email-finder. Trabalhamos bem
# abaixo do teto: o volume aqui é baixo por natureza (a cota do plano
# gratuito é de 50 buscas), então não há nada a ganhar correndo perto do limite.
RATE_LIMIT_RPS = 5

MAX_RETRIES = 3
BACKOFF_BASE_SEGUNDOS = 1.0

# `max_duration` do email-finder (3 a 20s). Mais tempo = resultado melhor. 10 é
# o padrão da API e é o que usamos: subir isso encareceria o tempo de uma etapa
# que já roda uma chamada por contato.
MAX_DURATION = 10


class HunterError(Exception):
    """Erro da API Hunter já traduzido para pt-BR.

    `fatal=True` significa que insistir não adianta e a etapa de e-mails inteira
    deve parar (chave inválida, cota do plano esgotada). `fatal=False` é
    problema de um contato só (domínio sem MX, nome inválido, titular pediu
    remoção dos dados) e vira aviso, sem interromper os demais.
    """

    def __init__(self, mensagem_pt: str, *, code: str = "", http_status: int = 0, fatal: bool = False):
        super().__init__(mensagem_pt)
        self.mensagem_pt = mensagem_pt
        self.code = code
        self.http_status = http_status
        self.fatal = fatal


class CotaHunterEsgotada(HunterError):
    """Cota de créditos do ciclo atingida — o limite configurado em `/admin` ou
    o do próprio plano do Hunter (HTTP 429). Tratada à parte porque não é falha:
    é o comportamento esperado, e a mensagem vai para o usuário como aviso."""


class _Throttle:
    """Espaçador de requisições: garante o teto por segundo."""

    def __init__(self, rps: int):
        self._intervalo_minimo = 1.0 / max(rps, 1)
        self._lock = asyncio.Lock()
        self._ultimo_envio = 0.0

    async def aguardar(self) -> None:
        async with self._lock:
            espera = self._intervalo_minimo - (time.monotonic() - self._ultimo_envio)
            if espera > 0:
                await asyncio.sleep(espera)
            self._ultimo_envio = time.monotonic()


_throttle = _Throttle(RATE_LIMIT_RPS)


def api_key_configurada() -> bool:
    """Diz se há ao menos uma conta configurada, sem revelar chave nenhuma."""
    from prospect_agent.services.settings import slots_hunter_configurados

    return bool(slots_hunter_configurados())


# ---------------------------------------------------------------------------
# Cota por ciclo — o gate que impede estourar o pacote de créditos
# ---------------------------------------------------------------------------
def _ultimo_dia(ano: int, mes: int) -> int:
    return calendar.monthrange(ano, mes)[1]


def _no_dia(ano: int, mes: int, dia: int) -> datetime:
    """Meia-noite do `dia` naquele mês, encolhido para o último dia quando o mês
    é curto: quem assinou dia 31 renova em 28/02 (ou 29), não em 03/03."""
    return datetime(ano, mes, min(dia, _ultimo_dia(ano, mes)))


def inicio_do_ciclo(dia_renovacao: int, agora: Optional[datetime] = None) -> datetime:
    """Começo do ciclo de créditos VIGENTE.

    O Hunter renova no aniversário da assinatura, não no dia 1º. Com
    `dia_renovacao=17` e hoje sendo 05/08, o ciclo corrente começou em 17/07;
    sendo 20/08, começou em 17/08.

    Função pura (recebe `agora`) para o comportamento em virada de mês ser
    testável sem mexer no relógio da máquina.
    """
    from prospect_agent.models import brt_now

    agora = agora or brt_now()
    dia = max(1, min(int(dia_renovacao or 1), 31))

    deste_mes = _no_dia(agora.year, agora.month, dia)
    if agora >= deste_mes:
        return deste_mes

    # O dia ainda não chegou neste mês: o ciclo corrente abriu no mês passado.
    ano, mes = (agora.year - 1, 12) if agora.month == 1 else (agora.year, agora.month - 1)
    return _no_dia(ano, mes, dia)


def proxima_renovacao(dia_renovacao: int, agora: Optional[datetime] = None) -> datetime:
    """Quando a cota zera de novo. Usado só para exibição."""
    from prospect_agent.models import brt_now

    agora = agora or brt_now()
    inicio = inicio_do_ciclo(dia_renovacao, agora)
    dia = max(1, min(int(dia_renovacao or 1), 31))
    ano, mes = (inicio.year + 1, 1) if inicio.month == 12 else (inicio.year, inicio.month + 1)
    return _no_dia(ano, mes, dia)


def creditos_usados_por_conta(tenant_id: int) -> Dict[int, int]:
    """{slot: créditos gastos} no ciclo corrente, para TODOS os slots que já
    consumiram algo — inclusive contas removidas depois.

    Soma `HunterUsage.creditos` (e não a contagem de linhas): busca sem
    resultado não consome crédito no Hunter, e contá-la faria a plataforma se
    bloquear antes da hora.

    Por conta, e não em um total só, porque o teto do Hunter é por conta: com
    duas contas de 50 e 50 créditos já gastos numa delas, ainda cabem 50 buscas
    no ciclo, mas nenhuma naquela conta.
    """
    import reflex as rx

    from prospect_agent.models import HunterUsage
    from prospect_agent.services.settings import get_hunter_dia_renovacao

    inicio = inicio_do_ciclo(get_hunter_dia_renovacao())
    with rx.session() as session:
        linhas = (
            session.query(HunterUsage)
            .filter(
                HunterUsage.tenant_id == tenant_id,
                HunterUsage.created_at >= inicio,
            )
            .all()
        )
    por_conta: Dict[int, int] = {}
    for l in linhas:
        por_conta[l.account_slot] = por_conta.get(l.account_slot, 0) + l.creditos
    return por_conta


def creditos_usados_no_ciclo(tenant_id: int) -> int:
    """Total consumido no ciclo, somando todas as contas."""
    return sum(creditos_usados_por_conta(tenant_id).values())


def creditos_restantes_por_conta(tenant_id: int) -> Dict[int, int]:
    """{slot: créditos ainda disponíveis} das contas CONFIGURADAS.

    Uma conta removida some daqui mesmo tendo consumo no ciclo: ela não pode
    mais receber busca, então o que sobrava nela não é orçamento.
    """
    from prospect_agent.services.settings import (
        get_hunter_creditos_mensais,
        slots_hunter_configurados,
    )

    limite = get_hunter_creditos_mensais()
    usados = creditos_usados_por_conta(tenant_id)
    return {slot: max(0, limite - usados.get(slot, 0)) for slot in slots_hunter_configurados()}


def creditos_restantes(tenant_id: int) -> int:
    """Quantos créditos ainda cabem no ciclo, somando as contas configuradas.

    Este é um controle LOCAL, contado pelo que a própria plataforma gastou. Ele
    não conhece consumo feito fora daqui (alguém usando a mesma chave no site do
    Hunter, por exemplo) — por isso o 429 da API continua sendo tratado como
    fim de cota daquela conta, e não como erro.
    """
    return sum(creditos_restantes_por_conta(tenant_id).values())


def registrar_uso(
    tenant_id: int, *, contact_id: Optional[int], dominio: str, encontrado: bool,
    account_slot: int,
) -> None:
    """Grava a tentativa. `creditos` = 1 só quando veio e-mail (ver HunterUsage).

    `account_slot` é obrigatório: sem ele a linha não diz de qual conta saiu o
    crédito, e o balanceamento do próximo ciclo ficaria cego.
    """
    import reflex as rx

    from prospect_agent.models import HunterUsage

    with rx.session() as session:
        session.add(HunterUsage(
            tenant_id=tenant_id,
            contact_id=contact_id,
            dominio=dominio or "",
            encontrado=encontrado,
            creditos=1 if encontrado else 0,
            account_slot=account_slot,
        ))
        session.commit()


# ---------------------------------------------------------------------------
# Preparo dos dados de entrada
# ---------------------------------------------------------------------------
_SUFIXOS_SOCIETARIOS = (
    "ltda", "me", "epp", "eireli", "sa", "s/a", "s.a", "cia", "filho", "filha",
    "junior", "jr", "neto", "sobrinho",
)


def separar_nome(nome_completo: str) -> Tuple[str, str]:
    """(primeiro nome, último sobrenome) a partir do nome do contato.

    O Hunter aceita `full_name`, mas a doc recomenda `first_name` + `last_name`,
    e o QSA da Receita devolve nomes longos ("MARIA DAS GRACAS DE SOUZA LIMA")
    onde o meio é ruído. Sufixos de tratamento no fim ("JUNIOR", "NETO") são
    descartados para não virarem sobrenome: buscar "Maria Junior" não acha nada.
    """
    partes = [p for p in re.split(r"\s+", (nome_completo or "").strip()) if p]
    # Partículas ("de", "da", "dos") não são nome nem sobrenome.
    partes = [p for p in partes if p.lower().strip(".") not in ("de", "da", "do", "das", "dos", "e")]
    if not partes:
        return "", ""
    if len(partes) == 1:
        return partes[0], ""

    primeiro = partes[0]
    resto = partes[1:]
    while len(resto) > 1 and resto[-1].lower().strip(".") in _SUFIXOS_SOCIETARIOS:
        resto = resto[:-1]
    return primeiro, resto[-1]


def normalizar_dominio(valor: str) -> str:
    """Domínio nu, pronto para o Hunter ("https://www.alfa.com.br/sobre" ->
    "alfa.com.br"). A URL completa é recusada com `invalid_domain`.

    A extração em si é delegada ao normalizador canônico do projeto
    (`services/normalizers.normalizar_dominio`, o mesmo que a pesquisa e o
    enriquecimento usam para deduplicar empresa) — duplicar essa regra aqui
    criaria duas noções de "domínio da empresa" que poderiam divergir. O que se
    acrescenta é só o que é específico desta API: descartar query string e
    âncora, tolerar um e-mail gravado por engano no campo de site, e exigir que
    sobre algo com ponto.
    """
    from prospect_agent.services.normalizers import normalizar_dominio as _canonico

    base = _canonico(valor) or ""
    base = base.split("?")[0].split("#")[0].split("@")[-1]
    return base if "." in base else ""


def _sem_acento(texto: str) -> str:
    """O email-finder trabalha melhor com ASCII: "João" -> "Joao"."""
    normalizado = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in normalizado if not unicodedata.combining(c))


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def _mensagem_por_status(status: int, code: str) -> Tuple[str, bool]:
    """(status HTTP, id do erro) -> (mensagem pt-BR, é_fatal)."""
    c = (code or "").lower()

    if status == 401:
        return ("Chave da Hunter inválida ou ausente. Verifique em Integrações no painel do super admin.", True)
    if status == 429:
        return (
            "Cota de créditos da Hunter esgotada na conta. Os e-mails restantes não foram buscados.",
            True,
        )
    if status == 403:
        return ("Limite de requisições da Hunter atingido. Tente novamente em alguns minutos.", True)
    if c == "invalid_domain":
        return ("Domínio da empresa inválido ou sem servidor de e-mail.", False)
    if c in ("invalid_first_name", "invalid_last_name", "invalid_full_name"):
        return ("Nome do contato em formato que a Hunter não aceita.", False)
    if c == "claimed_email":
        return ("O titular pediu à Hunter para não processar os dados dele.", False)
    if status == 400 or status == 422:
        return ("Parâmetros inválidos na consulta à Hunter (nome ou domínio).", False)
    if status in (500, 502, 503, 504):
        return ("Serviço da Hunter indisponível no momento. Tente novamente em instantes.", False)
    return (f"Falha na consulta à Hunter (HTTP {status}).", False)


def _primeiro_erro(payload: Any) -> str:
    if isinstance(payload, dict):
        erros = payload.get("errors")
        if isinstance(erros, list) and erros and isinstance(erros[0], dict):
            return str(erros[0].get("id") or "")
    return ""


async def _dormir_backoff(tentativa: int) -> None:
    await asyncio.sleep(BACKOFF_BASE_SEGUNDOS * (2 ** tentativa) + random.uniform(0, 0.3))


async def _request(path: str, params: Dict[str, Any], chave: str) -> Dict[str, Any]:
    """Uma requisição autenticada com a chave RECEBIDA — este módulo não escolhe
    a conta aqui: quem escolhe é o `Balanceador`, que precisa saber qual conta
    pagou para registrar o crédito no slot certo."""
    if not chave:
        raise HunterError(
            "Nenhuma conta da Hunter configurada. Cadastre ao menos uma em "
            "Integrações no painel do super admin.",
            code="api_key_missing",
            fatal=True,
        )

    url = f"{BASE_URL}{path}"
    params = {**params, "api_key": chave}

    for tentativa in range(MAX_RETRIES + 1):
        await _throttle.aguardar()
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SEGUNDOS) as client:
                resposta = await client.get(url, params=params)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            if tentativa < MAX_RETRIES:
                await _dormir_backoff(tentativa)
                continue
            raise HunterError(
                "Não foi possível falar com a Hunter (rede ou timeout).",
                code=type(exc).__name__,
                fatal=False,
            )

        if resposta.status_code == 200:
            try:
                return resposta.json()
            except ValueError:
                raise HunterError("Resposta inválida da Hunter (não é JSON).", http_status=200)

        try:
            payload = resposta.json()
        except ValueError:
            payload = {}
        code = _primeiro_erro(payload)
        mensagem, fatal = _mensagem_por_status(resposta.status_code, code)

        # 403 é "muitas requisições agora"; 429 é "acabou a cota do plano" e
        # insistir só queima tempo.
        if resposta.status_code in (403, 500, 502, 503, 504) and tentativa < MAX_RETRIES:
            await _dormir_backoff(tentativa)
            continue

        if resposta.status_code == 429:
            raise CotaHunterEsgotada(mensagem, code=code, http_status=429, fatal=True)
        raise HunterError(mensagem, code=code, http_status=resposta.status_code, fatal=fatal)

    raise HunterError("Falha desconhecida na Hunter.", fatal=False)


# ---------------------------------------------------------------------------
# Endpoint usado pelo enriquecimento
# ---------------------------------------------------------------------------
async def buscar_email(dominio: str, nome_completo: str, chave: str) -> Optional[Dict[str, Any]]:
    """GET /v2/email-finder — e-mail profissional de uma pessoa numa empresa.

    Devolve `{"email", "confianca", "verificacao"}` ou `None` quando a Hunter
    não achou nada (que, segundo a doc, NÃO consome crédito).

    Levanta `HunterError` (fatal=True para chave inválida) e
    `CotaHunterEsgotada` quando a conta do Hunter recusa por limite. Note que os
    dois erros são da CONTA usada, não da plataforma: quem decide se ainda há
    outra conta para tentar é o `Balanceador`.
    """
    dominio = normalizar_dominio(dominio)
    primeiro, ultimo = separar_nome(nome_completo)
    if not dominio or not primeiro:
        return None

    params: Dict[str, Any] = {
        "domain": dominio,
        "first_name": _sem_acento(primeiro),
        "max_duration": MAX_DURATION,
    }
    # Sem sobrenome o Hunter recusa `first_name` sozinho; manda-se o nome
    # completo, que é a outra forma aceita pela API.
    if ultimo:
        params["last_name"] = _sem_acento(ultimo)
    else:
        params.pop("first_name")
        params["full_name"] = _sem_acento(primeiro)

    payload = await _request("/email-finder", params, chave)
    dados = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(dados, dict) or not dados.get("email"):
        return None

    verificacao = dados.get("verification")
    return {
        "email": str(dados.get("email")).strip().lower(),
        "confianca": int(dados.get("score") or 0),
        "verificacao": (verificacao or {}).get("status") if isinstance(verificacao, dict) else None,
    }


# ---------------------------------------------------------------------------
# Balanceador entre as contas
# ---------------------------------------------------------------------------
class Balanceador:
    """Distribui as buscas entre as contas da Hunter durante UMA execução.

    Existe porque o Hunter vende créditos por conta: oito contas do plano
    gratuito valem oito vezes a cota, mas só se cada busca for para uma conta
    que ainda tenha crédito. A escolha é sempre a conta com MAIS créditos
    sobrando no ciclo (empate: menor slot), o que espalha o consumo de forma
    parelha em vez de queimar a conta 1 antes de tocar na 2 — assim uma conta
    que ficar indisponível não leva junto a maior parte do orçamento.

    Duas exclusões valem só enquanto o objeto vive, e de propósito:

    - **cota esgotada (429)**: a própria Hunter recusou. Pode haver consumo
      feito fora da plataforma, que o contador local não enxerga, então a conta
      sai da roda desta execução mesmo que o contador local ache que sobrava
      crédito. No ciclo seguinte ela volta a ser considerada.
    - **chave inválida (401)**: uma credencial errada não pode derrubar a etapa
      inteira quando há outras sete contas boas. A conta sai da roda e o
      problema vira aviso ao final, para o super admin corrigir a chave.

    Instância por execução (não é singleton de módulo): duas rodadas
    simultâneas não devem herdar as exclusões uma da outra.
    """

    def __init__(self, tenant_id: int):
        self.tenant_id = tenant_id
        self._fora: set = set()          # slots excluídos nesta execução
        self._chaves_invalidas: set = set()  # subconjunto de _fora, para o aviso final
        # {slot: chave}. Lido uma vez: trocar a configuração no meio de uma
        # execução em background não deve mudar as contas usadas por ela.
        from prospect_agent.services.settings import get_hunter_accounts

        self._chaves = dict(get_hunter_accounts())

    @property
    def slots_com_chave_invalida(self) -> list:
        return sorted(self._chaves_invalidas)

    def creditos_restantes(self) -> int:
        """Créditos disponíveis nas contas ainda em jogo nesta execução."""
        disponiveis = creditos_restantes_por_conta(self.tenant_id)
        return sum(v for slot, v in disponiveis.items() if slot not in self._fora)

    def _escolher_conta(self) -> Optional[Tuple[int, str]]:
        disponiveis = creditos_restantes_por_conta(self.tenant_id)
        candidatos = [
            (restante, slot)
            for slot, restante in disponiveis.items()
            if restante > 0 and slot not in self._fora and slot in self._chaves
        ]
        if not candidatos:
            return None
        # Mais crédito primeiro; empate resolvido pelo menor slot, para a
        # escolha ser determinística (e os testes, reproduzíveis).
        candidatos.sort(key=lambda t: (-t[0], t[1]))
        slot = candidatos[0][1]
        return slot, self._chaves[slot]

    async def buscar_email(
        self, *, contact_id: Optional[int], dominio: str, nome_completo: str,
    ) -> Optional[Dict[str, Any]]:
        """Busca o e-mail na melhor conta disponível e já registra o consumo.

        Levanta `CotaHunterEsgotada` quando NENHUMA conta tem crédito (é o fim
        do orçamento do ciclo, não falha) e `HunterError` fatal quando não há
        conta utilizável. Erros de um par (contato, empresa) continuam subindo
        como `HunterError` não fatal, para o chamador seguir para o próximo.
        """
        while True:
            escolhida = self._escolher_conta()
            if not escolhida:
                if not self._chaves:
                    raise HunterError(
                        "Nenhuma conta da Hunter configurada. Cadastre ao menos uma "
                        "em Integrações no painel do super admin.",
                        code="api_key_missing", fatal=True,
                    )
                if len(self._chaves_invalidas) == len(self._chaves):
                    raise HunterError(
                        "Nenhuma chave da Hunter válida: verifique as contas em "
                        "Integrações no painel do super admin.",
                        code="api_key_invalid", fatal=True,
                    )
                raise CotaHunterEsgotada(
                    "Cota de créditos da Hunter esgotada em todas as contas configuradas.",
                    code="quota_local", fatal=True,
                )

            slot, chave = escolhida
            try:
                resultado = await buscar_email(dominio, nome_completo, chave)
            except CotaHunterEsgotada:
                # Cota daquela conta, não da plataforma: tira da roda e tenta a
                # próxima. Só quando acabarem todas o erro sobe.
                self._fora.add(slot)
                continue
            except HunterError as exc:
                if exc.http_status == 401 or exc.code == "api_key_invalid":
                    self._fora.add(slot)
                    self._chaves_invalidas.add(slot)
                    continue
                raise

            registrar_uso(
                self.tenant_id, contact_id=contact_id, dominio=dominio,
                encontrado=bool(resultado), account_slot=slot,
            )
            return resultado
