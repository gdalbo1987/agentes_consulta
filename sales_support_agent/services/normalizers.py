"""Normalizadores compartilhados entre o agente de prospecção e o enriquecimento.

Ficam num módulo neutro (e não dentro de um dos dois serviços) porque as duas
fases do funil precisam das mesmas chaves canônicas — domínio, CNPJ e nome
normalizado — para casar/deduplicar a mesma empresa.
"""

import re
from typing import Optional


def normalizar_dominio(website: Optional[str]) -> Optional[str]:
    """Extrai o domínio nu de uma URL: 'https://www.Empresa.com.br/sobre' -> 'empresa.com.br'."""
    if not website:
        return None
    w = str(website).strip().lower()
    w = re.sub(r"^https?://", "", w)
    w = re.sub(r"^www\.", "", w)
    w = w.split("/")[0]
    return w or None


def apenas_digitos_cnpj(cnpj: Optional[str]) -> Optional[str]:
    """Só os dígitos do CNPJ, sem completar zeros. Usado pelo dedupe da pesquisa."""
    if cnpj is None or cnpj == "":
        return None
    digits = re.sub(r"\D", "", str(cnpj))
    return digits or None


def normalizar_cnpj(cnpj) -> Optional[str]:
    """CNPJ canônico: 14 dígitos, com zeros à esquerda.

    ATENÇÃO: a KipFlow devolve `cnpj` como NÚMERO (ex.: 35965725000107), então
    um CNPJ que começa com zero chega com 13 dígitos e quebraria o casamento
    com o que gravamos. O zfill(14) é o que conserta isso — é o motivo deste
    normalizador existir separado de `apenas_digitos_cnpj`.
    Retorna None se não der 14 dígitos (lixo entra, lixo não sai).
    """
    digits = apenas_digitos_cnpj(cnpj)
    if not digits:
        return None
    digits = digits.zfill(14)
    return digits if len(digits) == 14 else None


def formatar_cnpj(cnpj: Optional[str]) -> str:
    """Formata para exibição: '35965725000107' -> '35.965.725/0001-07'."""
    d = normalizar_cnpj(cnpj)
    if not d:
        return ""
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"


def normalizar_nome(name: str) -> str:
    """Nome comparável: minúsculo, sem pontuação e sem sufixo societário."""
    n = (name or "").strip().lower()
    n = re.sub(r"[^\w\s]", "", n)
    # Sufixos societários comuns que não ajudam a distinguir empresas diferentes.
    for suf in (" ltda", " sa", " s a", " eireli", " me", " epp"):
        n = re.sub(rf"{suf}\b", "", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n
