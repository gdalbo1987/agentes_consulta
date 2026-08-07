"""Consulta GRATUITA de dados cadastrais de CNPJ (Receita Federal).

Fontes públicas, sem chave de API e sem custo:
  1. BrasilAPI      — https://brasilapi.com.br/api/cnpj/v1/{cnpj}
  2. Minha Receita  — https://minhareceita.org/{cnpj}   (fallback)

Por que existe: a maior parte dos 12 campos do enriquecimento é dado público da
Receita, e o quadro societário (QSA) traz **os decisores de fato** — sócios,
presidente e diretores — de graça. Medido na Viação Vila Real S/A: o QSA
devolveu Presidente + 2 Diretores sem custo, enquanto a busca paga no LinkedIn
custava R$ 0,49 e devolvia um supervisor.

A KipFlow continua sendo usada, mas só para o que estas fontes NÃO têm:
faixa de faturamento, faixa de funcionários, segmento, website, LinkedIn da
empresa — e para resolver o CNPJ a partir do nome, que nenhuma fonte gratuita faz.

LIMITAÇÕES ASSUMIDAS (são serviços comunitários, não têm SLA):
- só consultam por CNPJ (não por nome/razão social);
- podem ficar indisponíveis ou impor rate limit sem aviso — por isso há duas
  fontes e um throttle conservador;
- falha aqui NUNCA derruba o enriquecimento: cai para o caminho pago da KipFlow.
"""

import asyncio
import time
from typing import Any, Dict, List, Optional

import httpx

from sales_support_agent.services.normalizers import normalizar_cnpj

TIMEOUT_SEGUNDOS = 20.0

# Conservador de propósito: são APIs públicas e gratuitas, mantidas pela
# comunidade. Não vale a pena martelá-las para ganhar alguns segundos.
RATE_LIMIT_RPS = 3

MAX_TENTATIVAS = 2

BRASILAPI_URL = "https://brasilapi.com.br/api/cnpj/v1/{cnpj}"
MINHARECEITA_URL = "https://minhareceita.org/{cnpj}"


class ReceitaIndisponivelError(Exception):
    """Nenhuma das fontes gratuitas conseguiu responder (rede, 5xx, rate limit).

    É DIFERENTE de "CNPJ não encontrado": aqui a fonte está fora do ar, não é o
    dado que não existe. A distinção é crítica para o custo — se a Receita cai e
    o enriquecimento seguir em frente, TODAS as empresas caem no caminho pago da
    KipFlow (personas a R$ 0,49/pessoa + telefones), o que numa rodada de 34
    empresas sai ~20x mais caro. Por isso esta exceção existe e é fatal.
    """


# CNPJ público e estável (Banco do Brasil S.A.) usado só como sonda de saúde.
CNPJ_SONDA = "00000000000191"


class _Throttle:
    """Espaçador simples de requisições (mesma ideia do client da KipFlow)."""

    def __init__(self, rps: int):
        self._intervalo = 1.0 / max(rps, 1)
        self._lock = asyncio.Lock()
        self._ultimo = 0.0

    async def aguardar(self) -> None:
        async with self._lock:
            espera = self._intervalo - (time.monotonic() - self._ultimo)
            if espera > 0:
                await asyncio.sleep(espera)
            self._ultimo = time.monotonic()


_throttle = _Throttle(RATE_LIMIT_RPS)


async def consultar_cnpj(cnpj: str) -> Optional[Dict[str, Any]]:
    """Dados cadastrais + QSA de um CNPJ.

    Retorna o dict em caso de sucesso e **None quando o CNPJ não existe** nas
    bases (404) — situação legítima, específica daquela empresa.

    Levanta `ReceitaIndisponivelError` quando nenhuma das duas fontes conseguiu
    dar uma resposta definitiva (rede, timeout, 5xx, rate limit). O chamador
    PRECISA tratar isso como falha global e abortar: seguir em frente empurraria
    todas as empresas para o caminho pago da KipFlow.
    """
    digitos = normalizar_cnpj(cnpj)
    if not digitos:
        return None

    houve_resposta_definitiva = False
    for url in (BRASILAPI_URL, MINHARECEITA_URL):
        status, dados = await _tentar_fonte(url.format(cnpj=digitos))
        if status == "ok":
            return dados
        if status == "nao_encontrado":
            houve_resposta_definitiva = True

    if houve_resposta_definitiva:
        return None  # o CNPJ realmente não está nas bases
    raise ReceitaIndisponivelError(
        "Nenhuma fonte gratuita da Receita respondeu (BrasilAPI e Minha Receita)."
    )


async def verificar_disponibilidade() -> bool:
    """Sonda de saúde das fontes gratuitas, com um CNPJ público conhecido.

    Serve para abortar o enriquecimento ANTES de qualquer chamada paga: se a
    Receita está fora, a rodada inteira custaria ~20x mais na KipFlow. Custa
    R$ 0,00 porque as duas fontes são gratuitas.
    """
    try:
        return await consultar_cnpj(CNPJ_SONDA) is not None
    except ReceitaIndisponivelError:
        return False


async def _tentar_fonte(url: str):
    """Consulta uma fonte. Devolve (status, dados).

    status: "ok" | "nao_encontrado" | "indisponivel"
    A distinção entre os dois últimos é o que protege o custo — ver
    `ReceitaIndisponivelError`.
    """
    for tentativa in range(MAX_TENTATIVAS):
        await _throttle.aguardar()
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SEGUNDOS) as client:
                resposta = await client.get(url, headers={"Accept": "application/json"})
        except (httpx.TimeoutException, httpx.TransportError):
            if tentativa + 1 < MAX_TENTATIVAS:
                await asyncio.sleep(1.0)
                continue
            return ("indisponivel", None)

        if resposta.status_code == 200:
            try:
                dados = resposta.json()
            except ValueError:
                return ("indisponivel", None)
            return ("ok", dados) if isinstance(dados, dict) else ("indisponivel", None)

        # 404: a fonte respondeu e disse que o CNPJ não existe. É definitivo.
        if resposta.status_code == 404:
            return ("nao_encontrado", None)

        # 429 (rate limit) ou 5xx: vale uma segunda tentativa antes de desistir
        # e deixar a próxima fonte assumir.
        if resposta.status_code == 429 or resposta.status_code >= 500:
            if tentativa + 1 < MAX_TENTATIVAS:
                await asyncio.sleep(2.0)
                continue
        return ("indisponivel", None)
    return ("indisponivel", None)


def extrair_telefone(dados: Dict[str, Any]) -> Optional[str]:
    """Telefone registrado na Receita (campo `ddd_telefone_1`).

    Evita a chamada paga de telefones da KipFlow. É o número declarado no
    cadastro — pode estar desatualizado e não vem com indicação de WhatsApp.
    """
    for chave in ("ddd_telefone_1", "ddd_telefone_2"):
        valor = dados.get(chave)
        if valor and str(valor).strip():
            return str(valor).strip()
    return None


def extrair_qsa(dados: Dict[str, Any]) -> List[Dict[str, str]]:
    """Quadro de sócios e administradores, já normalizado.

    Devolve dicts com `nome`, `qualificacao` e `desde`. Filtra o que não serve
    como contato comercial:
    - sócios PESSOA JURÍDICA (holdings) — não há a quem ligar;
    - entradas sem nome.
    A priorização por cargo fica em `enrichment_rules`, não aqui: este módulo
    só lê a fonte, não decide regra de negócio.
    """
    bruto = dados.get("qsa")
    if not isinstance(bruto, list):
        return []

    saida: List[Dict[str, str]] = []
    for s in bruto:
        if not isinstance(s, dict):
            continue
        nome = (s.get("nome_socio") or s.get("nome") or "").strip()
        if not nome:
            continue
        qualificacao = str(
            s.get("qualificacao_socio") or s.get("qualificacao_representante_legal") or ""
        ).strip()
        # Descarta pessoa jurídica: "Sócio Pessoa Jurídica", holdings etc.
        if "PESSOA JURIDICA" in _maiusculo_sem_acento(qualificacao):
            continue
        identificador = str(s.get("identificador_socio") or "").strip()
        if identificador in ("1", "PESSOA JURIDICA"):
            continue
        saida.append({
            "nome": nome,
            "qualificacao": qualificacao,
            "desde": str(s.get("data_entrada_sociedade") or ""),
        })
    return saida


def _maiusculo_sem_acento(texto: str) -> str:
    t = texto.upper()
    for de, para in (("Á", "A"), ("Â", "A"), ("Ã", "A"), ("É", "E"), ("Ê", "E"),
                     ("Í", "I"), ("Ó", "O"), ("Ô", "O"), ("Õ", "O"), ("Ú", "U"),
                     ("Ç", "C")):
        t = t.replace(de, para)
    return t
