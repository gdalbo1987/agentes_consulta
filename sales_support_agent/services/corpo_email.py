"""Recorte do corpo: só a mensagem NOVA, sem a conversa citada abaixo dela.

Por que existe. O Graph devolve o corpo inteiro da mensagem, e num e-mail
corporativo isso quase nunca é só o que a pessoa acabou de escrever: vem a
resposta nova no topo e, abaixo, a thread inteira citada, com os cabeçalhos
`De:/Enviada em:/Para:/Assunto:` de cada rodada anterior, prazos vencidos,
números de pedido antigos e assinaturas. `sanear_corpo` limita esse texto por
TAMANHO (`LIMITE_CORPO`), o que é controle de custo e de superfície de injeção,
mas não separa o que é novo do que é histórico.

Para o Agente 1 a diferença é o produto inteiro: as regras de classificação são
gatilhos textuais ("abrir PI", "PEDIDO EQUALIZADO", "está atrasado", "prazo:
19/08"), e um gatilho desses citado no rodapé de uma thread vale tanto quanto o
mesmo gatilho escrito agora. O resultado é o e-mail ser classificado pelo que
alguém pediu semanas atrás, e um prazo já vencido do histórico virar urgência de
hoje. O Agente 2 sofre do mesmo defeito, um degrau adiante: o resumo e a ação
sugerida descreviam a thread, não a mensagem que chegou.

O corte é HEURÍSTICO, e a heurística assume o padrão de resposta no topo, que é
o que todo cliente de e-mail corporativo faz. Por isso ela é conservadora nos
dois sentidos:

- **Na dúvida, não corta.** Nenhum marcador reconhecido significa texto inteiro.
- **Corte que esvazia o corpo é desfeito.** Um encaminhamento sem comentário
  nenhum começa direto no cabeçalho citado; ali o conteúdo encaminhado É a
  mensagem, e devolver vazio faria o agente classificar um e-mail em branco.
  Nesse caso volta o texto completo.

O que ele NÃO faz: remover assinatura. Assinatura é ruído barato e cortá-la
exigiria adivinhar onde ela começa, com risco de levar junto a última frase útil.
Sobra da mensagem nova que seja só assinatura é caso previsto: o prompt do
Agente 1 já manda decidir pelo assunto quando o corpo é neutro.

Este módulo é puro e sem dependência de rede ou banco, de propósito: é o que
permite testá-lo inteiro sem caixa de e-mails e sem PostgreSQL.
"""

import re
import unicodedata
from typing import List, Optional


def _normalizar(linha: str) -> str:
    """Minúsculas, sem acento e sem espaço nas bordas, só para CASAR marcador.

    O texto devolvido ao chamador é sempre o original: o normalizado serve
    apenas para reconhecer "Enviada em:" escrito de qualquer jeito.
    """
    sem_acento = unicodedata.normalize("NFD", (linha or "").strip())
    return "".join(c for c in sem_acento if unicodedata.category(c) != "Mn").lower()


# `-----Mensagem original-----`, `----- Original Message -----`,
# `---------- Forwarded message ---------`. Marcador explícito de início do
# histórico, posto pelo próprio cliente de e-mail.
_SEPARADOR = re.compile(
    r"^-{2,}\s*"
    r"(mensagem original|mensagem encaminhada|original message|forwarded message)"
    r"\s*-{2,}$"
)

# A régua de sublinhados que o Exchange emite ao converter o HTML em texto,
# logo acima do bloco `De:`. Exige 5 ou mais para não confundir com um `___`
# solto de formatação.
_SUBLINHADO = re.compile(r"^_{5,}$")

# Primeira linha do cabeçalho da mensagem citada.
_CABECALHO_INICIO = re.compile(r"^(de|from|remetente)\s*:")

# Confirmação de que aquele `De:` é mesmo um cabeçalho, e não uma frase que
# começa com "De:". Sozinho, `_CABECALHO_INICIO` cortaria texto legítimo.
_CABECALHO_CONFIRMA = re.compile(
    r"^(assunto|subject|enviad[ao]( em)?|sent|para|to|cc|data|date)\s*:"
)
_LINHAS_DE_CONFIRMACAO = 8

# `Em 12/03/2026 10:00, Fulano escreveu:` e `On Wed, Mar 12, 2026 at 10:00,
# Fulano <f@x.com> wrote:`. A exigência de dígito ou arroba na mesma linha é o
# que separa a atribuição de citação de uma frase comum terminada em
# "escreveu:", que cortaria a mensagem nova ao meio.
_ATRIBUICAO = re.compile(r"(escreveu|wrote)\s*:$")
_TEM_DATA_OU_EMAIL = re.compile(r"[\d@]")
_ATRIBUICAO_MAX = 250


def _e_marcador(norm: str, seguintes: List[str]) -> bool:
    """A linha normalizada abre o histórico citado?"""
    if not norm:
        return False
    if _SEPARADOR.match(norm) or _SUBLINHADO.match(norm):
        return True
    # Citação com `>`, do texto puro de clientes que não são Outlook.
    if norm.startswith(">"):
        return True
    if _CABECALHO_INICIO.match(norm):
        return any(_CABECALHO_CONFIRMA.match(l) for l in seguintes)
    if (
        _ATRIBUICAO.search(norm)
        and len(norm) <= _ATRIBUICAO_MAX
        and _TEM_DATA_OU_EMAIL.search(norm)
    ):
        return True
    return False


def _indice_corte(linhas: List[str]) -> Optional[int]:
    """Índice da PRIMEIRA linha que já é histórico, ou None se não houver."""
    normalizadas = [_normalizar(linha) for linha in linhas]
    for indice, norm in enumerate(normalizadas):
        seguintes = normalizadas[indice + 1 : indice + 1 + _LINHAS_DE_CONFIRMACAO]
        if _e_marcador(norm, seguintes):
            return indice
    return None


def extrair_mensagem_nova(texto: str) -> str:
    """Devolve só o que foi escrito nesta mensagem, sem a thread citada.

    Devolve o texto inteiro quando não reconhece nenhum marcador e quando o
    corte deixaria o corpo vazio (encaminhamento sem comentário). As duas saídas
    são o mesmo princípio: classificar com histórico junto é ruim, classificar
    sem conteúdo nenhum é pior.
    """
    if not texto or not texto.strip():
        return ""

    linhas = texto.split("\n")
    corte = _indice_corte(linhas)
    if corte is None:
        return texto.strip()

    nova = "\n".join(linhas[:corte]).strip()
    return nova or texto.strip()
