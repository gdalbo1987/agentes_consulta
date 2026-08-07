"""Regras de negócio da priorização — funções PURAS, sem I/O e sem rede.

O agente de IA (services/priorizacao_agent.py) SÓ classifica cada um dos 7
critérios fixos em 0/10/25 pontos, com uma justificativa curta. A aritmética
do score final, o clamp de valores fora do domínio esperado e a definição da
faixa de prioridade são feitos AQUI, nunca confiados ao LLM — mesmo princípio
já usado em services/prospect_agent.py (ids/contagens/datas são sempre
determinísticos) e em services/enrichment_rules.py (uma fórmula, um lugar).
"""

from typing import Dict, List, Tuple

# ---------------------------------------------------------------------------
# Peso fixo (%) de cada critério. A ORDEM IMPORTA: é a mesma ordem exigida do
# agente de IA no prompt, para casar índice a índice na exibição da UI.
# Classes/faixas provisórias, conforme especificado no pedido original —
# ajustáveis depois só no prompt do agente (ver priorizacao_agent.py).
# ---------------------------------------------------------------------------
PESOS_CRITERIOS: Dict[str, float] = {
    "Fit com ICP": 30,
    "Potencial financeiro": 20,
    "Facilidade de contato": 20,
    "Maturidade da empresa": 5,
    "Segmento estratégico": 10,
    "Região de localização": 5,
    "Sinais de investimento futuro": 10,
}  # soma = 100

NOMES_CRITERIOS: Tuple[str, ...] = tuple(PESOS_CRITERIOS.keys())
TOTAL_CRITERIOS = len(NOMES_CRITERIOS)

# Cada critério é classificado em exatamente uma destas 3 classes.
PONTOS_VALIDOS: Tuple[int, ...] = (0, 10, 25)
_PONTOS_MAXIMOS = 25

# Faixas de classe de prioridade sobre o score final (0-100).
LIMITE_ALTA = 70
LIMITE_MEDIA = 40


def _clampar_pontos(pontos) -> int:
    """Qualquer valor fora de {0,10,25} vira o mais próximo — nunca infla o
    score por um valor mal-formado devolvido pelo modelo."""
    try:
        p = int(pontos)
    except (TypeError, ValueError):
        return 0
    if p in PONTOS_VALIDOS:
        return p
    return min(PONTOS_VALIDOS, key=lambda v: abs(v - p))


def validar_criterios(criterios: List[dict]) -> List[dict]:
    """Garante que os 7 critérios fixos estão presentes, na ordem oficial.

    Critério ausente na resposta do agente entra com pontos=0 e uma
    justificativa padrão — o cálculo do score nunca depende de uma resposta
    incompleta ou fora do domínio esperado do modelo.
    """
    por_nome = {}
    for item in criterios or []:
        nome = (item.get("criterio") or "").strip()
        if nome in PESOS_CRITERIOS and nome not in por_nome:
            por_nome[nome] = item

    resultado = []
    for nome in NOMES_CRITERIOS:
        item = por_nome.get(nome)
        if item is None:
            resultado.append({
                "criterio": nome,
                "pontos": 0,
                "justificativa": "Critério não avaliado pelo modelo (dado ausente ou erro de resposta).",
            })
            continue
        resultado.append({
            "criterio": nome,
            "pontos": _clampar_pontos(item.get("pontos")),
            "justificativa": (item.get("justificativa") or "").strip() or "Sem justificativa informada.",
        })
    return resultado


def calcular_score_final(criterios: List[dict]) -> int:
    """score_final = Σ [(pontos/25) × peso%], clampado em [0,100].

    `criterios` já deve ter passado por `validar_criterios` (7 itens, pontos
    em {0,10,25}). Chamar com uma lista incompleta ainda funciona — pesos de
    critérios ausentes simplesmente não contribuem — mas o caminho correto é
    sempre validar antes.
    """
    por_nome = {c["criterio"]: c for c in criterios}
    total = 0.0
    for nome, peso in PESOS_CRITERIOS.items():
        item = por_nome.get(nome)
        pontos = _clampar_pontos(item.get("pontos")) if item else 0
        total += (pontos / _PONTOS_MAXIMOS) * peso
    return max(0, min(100, round(total)))


def definir_classe_prioridade(score: int) -> str:
    """Alta (>=70) | Média (40-69) | Baixa (<40)."""
    if score >= LIMITE_ALTA:
        return "Alta"
    if score >= LIMITE_MEDIA:
        return "Média"
    return "Baixa"


def cor_classe_prioridade(classe: str) -> str:
    """Cor do badge na tabela — verde/âmbar/vermelho, mesmo espírito de
    _badge_percentual em pages/enrichment.py."""
    return {"Alta": "green", "Média": "amber", "Baixa": "red"}.get(classe, "gray")
