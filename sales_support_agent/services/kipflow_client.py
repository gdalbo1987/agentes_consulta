"""Client HTTP da API KipFlow (enriquecimento de dados de empresas).

Doc oficial: https://docs.kipflow.io/

Responsabilidades deste módulo (e de mais nenhum outro):
- autenticação via header `X-API-Key`, lida do .env;
- respeito ao rate limit publicado (5 req/s · 100 req/min · 1000 req/hora);
- retry com backoff exponencial em 429/5xx/timeout, honrando `Retry-After`;
- tradução dos erros da API para mensagens acionáveis em pt-BR, no mesmo
  padrão de `services/prospect_agent._error_message`.

SEGREDO: `KIPFLOW_API_KEY` nunca é impressa, logada, colocada em exceção,
em toast ou em qualquer campo de State (o State é serializado para o browser).

Usa `httpx` (assíncrono) e não `requests` porque o enriquecimento roda dentro
de um `@rx.event(background=True)` — uma chamada bloqueante travaria o event
loop do Reflex inteiro.
"""

import asyncio
import os
import random
import time
from typing import Any, Dict, List, Optional

import httpx

# ---------------------------------------------------------------------------
# Configuração. Ajuste aqui — nada disto deve ficar espalhado pelo código.
# ---------------------------------------------------------------------------
# A doc da KipFlow diverge internamente: a página de introdução informa
# `https://api.kipflow.io`, enquanto as páginas de endpoint e os exemplos curl
# mostram `https://data.z-api.driva.io`. Por isso a base é configurável — não é
# mais uma constante de módulo fixada no .env, e sim lida de IntegrationSetting
# (banco), editável pelo super admin em /admin (ver services/settings.py).

TIMEOUT_SEGUNDOS = 30.0

# Rate limit publicado: 5/s, 100/min, 1000/h. Trabalhamos abaixo do teto para
# não depender do relógio do servidor deles.
RATE_LIMIT_RPS = 4
RATE_LIMIT_RPM = 90

# Tentativas extras após a primeira falha retryable (429/5xx/timeout).
MAX_RETRIES = 4
BACKOFF_BASE_SEGUNDOS = 1.0

MAX_ITENS_POR_LOTE = 50  # limite da própria API para os endpoints /batch/*


class KipflowError(Exception):
    """Erro da API KipFlow já traduzido para pt-BR.

    `fatal=True` significa que insistir não adianta e a execução inteira deve
    parar (chave inválida, créditos acabaram, rate limit esgotado). `fatal=False`
    é problema de uma empresa só (CNPJ inválido, não encontrada) e vira aviso.
    """

    def __init__(self, mensagem_pt: str, *, code: str = "", http_status: int = 0, fatal: bool = False):
        super().__init__(mensagem_pt)
        self.mensagem_pt = mensagem_pt
        self.code = code
        self.http_status = http_status
        self.fatal = fatal


class _Throttle:
    """Espaçador de requisições: respeita o teto por segundo e por minuto.

    Todo request passa por aqui — é o único ponto que garante o rate limit.
    """

    def __init__(self, rps: int, rpm: int):
        self._intervalo_minimo = 1.0 / max(rps, 1)
        self._rpm = rpm
        self._lock = asyncio.Lock()
        self._ultimo_envio = 0.0
        self._janela: List[float] = []

    async def aguardar(self) -> None:
        async with self._lock:
            agora = time.monotonic()

            # Teto por segundo: espaçamento mínimo entre chamadas.
            espera = self._intervalo_minimo - (agora - self._ultimo_envio)
            if espera > 0:
                await asyncio.sleep(espera)
                agora = time.monotonic()

            # Teto por minuto: janela deslizante de 60s.
            self._janela = [t for t in self._janela if agora - t < 60.0]
            if len(self._janela) >= self._rpm:
                espera_minuto = 60.0 - (agora - self._janela[0])
                if espera_minuto > 0:
                    await asyncio.sleep(espera_minuto)
                    agora = time.monotonic()
                    self._janela = [t for t in self._janela if agora - t < 60.0]

            self._ultimo_envio = agora
            self._janela.append(agora)


_throttle = _Throttle(RATE_LIMIT_RPS, RATE_LIMIT_RPM)


def api_key_configurada() -> bool:
    """Diz se há chave configurada (Integrações, God Mode), sem revelar o valor."""
    from prospect_agent.services.settings import get_kipflow_api_key

    return bool(get_kipflow_api_key())


def _mensagem_por_status(status: int, code: str) -> tuple:
    """Traduz (status HTTP, código interno) -> (mensagem pt-BR, é_fatal)."""
    c = (code or "").upper()

    if status == 401 or c in ("API_KEY_MISSING", "API_KEY_INVALID"):
        return ("Chave da KipFlow inválida ou ausente. Verifique KIPFLOW_API_KEY no arquivo .env.", True)
    if status == 402 or c == "INSUFFICIENT_CREDITS":
        return ("Créditos da KipFlow esgotados. Adicione créditos em platform.kipflow.io.", True)
    if status == 429 or c == "RATE_LIMIT_EXCEEDED":
        return ("Limite de requisições da KipFlow atingido. Tente novamente em alguns minutos.", True)
    if c in ("INVALID_CNPJ", "INVALID_CPF", "INVALID_DATASETS") or status == 400:
        return ("Parâmetros inválidos na consulta à KipFlow (CNPJ/domínio/datasets).", False)
    if status == 404 or c in ("COMPANY_NOT_FOUND", "NOT_FOUND"):
        return ("Empresa não encontrada na base da KipFlow.", False)
    if status in (500, 502, 503, 504):
        return ("Serviço da KipFlow indisponível no momento. Tente novamente em instantes.", False)
    return (f"Falha na consulta à KipFlow (HTTP {status}).", False)


def _codigo_do_payload(payload: Any) -> str:
    if isinstance(payload, dict):
        erro = payload.get("error")
        if isinstance(erro, dict):
            return str(erro.get("code") or "")
        if isinstance(erro, str):
            return erro
        return str(payload.get("code") or "")
    return ""


async def _request(
    method: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Executa uma requisição com throttle, retry e erros traduzidos."""
    from prospect_agent.services.settings import get_kipflow_api_key, get_kipflow_base_url

    chave = get_kipflow_api_key()
    if not chave:
        raise KipflowError(
            "Chave da KipFlow não configurada. Configure-a em Integrações (God Mode).",
            code="API_KEY_MISSING",
            fatal=True,
        )

    url = f"{get_kipflow_base_url()}{path}"
    headers = {"X-API-Key": chave, "Accept": "application/json"}
    ultimo_erro: Optional[KipflowError] = None

    for tentativa in range(MAX_RETRIES + 1):
        await _throttle.aguardar()
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SEGUNDOS) as client:
                resposta = await client.request(
                    method, url, headers=headers, params=params, json=json_body
                )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            # Rede/timeout: vale insistir.
            ultimo_erro = KipflowError(
                "Não foi possível falar com a KipFlow (rede ou timeout).",
                code=type(exc).__name__,
                fatal=False,
            )
            if tentativa < MAX_RETRIES:
                await _dormir_backoff(tentativa)
                continue
            raise ultimo_erro

        if resposta.status_code == 200:
            try:
                return resposta.json()
            except ValueError:
                raise KipflowError(
                    "Resposta inválida da KipFlow (não é JSON).",
                    http_status=200,
                    fatal=False,
                )

        try:
            payload = resposta.json()
        except ValueError:
            payload = {}
        code = _codigo_do_payload(payload)
        mensagem, fatal = _mensagem_por_status(resposta.status_code, code)

        retryable = resposta.status_code == 429 or resposta.status_code >= 500
        if retryable and tentativa < MAX_RETRIES:
            await _dormir_backoff(tentativa, resposta.headers.get("Retry-After"))
            continue

        raise KipflowError(mensagem, code=code, http_status=resposta.status_code, fatal=fatal)

    # Só chega aqui se o loop terminar sem retornar nem levantar.
    raise ultimo_erro or KipflowError("Falha desconhecida na KipFlow.", fatal=False)


async def _dormir_backoff(tentativa: int, retry_after: Optional[str] = None) -> None:
    """Backoff exponencial com jitter; respeita `Retry-After` quando enviado."""
    if retry_after:
        try:
            await asyncio.sleep(min(float(retry_after), 60.0))
            return
        except (TypeError, ValueError):
            pass
    espera = BACKOFF_BASE_SEGUNDOS * (2 ** tentativa)
    await asyncio.sleep(espera + random.uniform(0, 0.3))


def extrair_custo(payload: Any) -> float:
    """Soma o custo cobrado numa resposta (campo `cost`, em BRL).

    Vem no nível raiz na maioria dos endpoints; nos endpoints de lote pode
    aparecer por item, então somamos os dois de forma defensiva.
    """
    if not isinstance(payload, dict):
        return 0.0
    total = 0.0
    raiz = payload.get("cost")
    if isinstance(raiz, (int, float)):
        total += float(raiz)
    resultados = payload.get("results")
    if isinstance(resultados, list):
        for item in resultados:
            if isinstance(item, dict) and isinstance(item.get("cost"), (int, float)):
                total += float(item["cost"])
    return round(total, 4)


# ---------------------------------------------------------------------------
# Endpoints usados pela fase de enriquecimento
# ---------------------------------------------------------------------------

async def buscar_empresas_por_cnpj_em_lote(cnpjs: List[str], datasets: List[str]) -> Dict[str, Any]:
    """POST /companies/v1/search/batch/cnpj — até 50 CNPJs por requisição.

    Este é o método PRINCIPAL: 30 empresas viram 1 chamada, não 30.
    """
    if len(cnpjs) > MAX_ITENS_POR_LOTE:
        raise ValueError(f"Lote de CNPJs excede o máximo de {MAX_ITENS_POR_LOTE}.")
    return await _request(
        "POST",
        "/companies/v1/search/batch/cnpj",
        json_body={"cnpjs": cnpjs, "datasets": datasets},
    )


async def buscar_empresas_por_dominio_em_lote(dominios: List[str], datasets: List[str]) -> Dict[str, Any]:
    """POST /companies/v1/search/batch/domain — fallback para quem só tem domínio."""
    if len(dominios) > MAX_ITENS_POR_LOTE:
        raise ValueError(f"Lote de domínios excede o máximo de {MAX_ITENS_POR_LOTE}.")
    return await _request(
        "POST",
        "/companies/v1/search/batch/domain",
        json_body={"domains": dominios, "datasets": datasets},
    )


async def casar_empresa_por_nome(nome: str, sigla_uf: Optional[str] = None) -> Dict[str, Any]:
    """GET /intelligence/v1/company-match — último recurso: sem CNPJ nem domínio.

    Devolve `data.matches[]` com `cnpj` e `similarity`.
    """
    params: Dict[str, Any] = {"name": nome}
    if sigla_uf:
        params["sigla_uf"] = sigla_uf
    return await _request("GET", "/intelligence/v1/company-match", params=params)


async def buscar_decisores(
    company_public_id: str,
    *,
    senioridades: List[str],
    areas: List[str],
    quantidade: int,
) -> Dict[str, Any]:
    """POST /social/v1/personas/search — decisores da empresa no LinkedIn.

    CUSTO: R$ 0,49 por PESSOA retornada — `quantidade` ($size) é o principal
    fator de custo do enriquecimento inteiro.
    """
    return await _request(
        "POST",
        "/social/v1/personas/search",
        json_body={
            "$filter": {
                "company_public_id": company_public_id,
                "seniority": senioridades,
                "area": areas,
            },
            "$page": 0,
            "$size": quantidade,
        },
    )


async def buscar_empresa_linkedin(company_public_id: str) -> Dict[str, Any]:
    """GET /social/v1/companies/search — R$ 0,49.

    Fallback DESLIGADO por padrão no orquestrador: o `company_public_id` sai
    de graça do `linkedin_url` (dataset online_presence).
    """
    return await _request(
        "GET", "/social/v1/companies/search", params={"company_public_id": company_public_id}
    )


async def buscar_telefones(cnpj: str, limite: int = 1) -> Dict[str, Any]:
    """GET /contacts/v1/phones — só quando `telefones` vem vazio nos datasets.

    Cobrado por telefone retornado, por isso o limite padrão é 1.
    """
    return await _request(
        "GET",
        "/contacts/v1/phones",
        params={
            "cnpj": cnpj,
            "phone_limit": max(1, min(limite, 50)),
            "exclude_contador": "true",
        },
    )
