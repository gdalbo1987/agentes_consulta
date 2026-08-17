"""Regras PURAS da classificação de e-mails: sem I/O, sem rede, sem banco.

Este módulo é a fonte única da verdade sobre as quatro classes. Ele existe
separado do agente pelo mesmo motivo que `priorizacao_rules` existia separado do
`priorizacao_agent` no produto anterior: o modelo classifica, o Python calcula.
Tudo o que é determinístico (aritmética de prazo, rótulo exibível, nome de
categoria no Outlook) fica aqui, testável sem chamar a OpenAI e sem subir nada.
"""

from typing import Optional

# ---------------------------------------------------------------------------
# As quatro classes
# ---------------------------------------------------------------------------
# Identificadores sem acento e sem espaço: é o que vai para o banco, para o
# enum do JSON Schema e para as tools do agente de consulta. O texto bonito,
# acentuado, mora em LABELS e só aparece na tela.
CLASSES = ("pedido", "proposta", "revisao_pedido", "revisao_proposta")

# "nenhuma" é valor de primeira classe do enum do modelo, e não a ausência de
# resposta. Obrigar o modelo a escolher explicitamente "não se encaixa" evita
# que uma falha de formato seja lida como "fora das classes", que é a
# interpretação errada mais cara: ela faria o e-mail ser ignorado em silêncio.
CLASSE_NENHUMA = "nenhuma"
CLASSES_COM_NENHUMA = CLASSES + (CLASSE_NENHUMA,)

LABELS = {
    "pedido": "Pedido",
    "proposta": "Proposta",
    "revisao_pedido": "Revisão de pedido",
    "revisao_proposta": "Revisão de proposta",
}

# Nome da categoria aplicada no Outlook (`message.categories`). Igual ao rótulo
# exibível de propósito: quem abre o Outlook tem de ver a mesma palavra que vê
# no dashboard.
CATEGORIAS = dict(LABELS)
CATEGORIA_URGENTE = "Urgente"
CATEGORIA_IMPORTANTE = "Importante"
# Marca de procedência: todo e-mail que a plataforma classificou leva esta
# categoria. Existe para que quem abre o Outlook consiga separar, de olho, o
# que a IA arquivou do que uma pessoa arquivou à mão. É também o que torna
# reversível uma execução mal calibrada: dá para achar tudo o que o agente
# tocou com uma busca por categoria.
#
# O nome é curto de propósito. As categorias aparecem lado a lado na coluna do
# Outlook, e um e-mail classificado leva SEMPRE duas ("Pedido" mais esta) e às
# vezes três (mais "Urgente" ou "Importante"). Com um rótulo longo, as outras
# duas ficavam cortadas na largura normal da coluna, e a marca de procedência
# escondia justamente a informação que o time precisa ler de relance.
CATEGORIA_IA = "IA"
# Nome anterior desta mesma marca. Fica registrado porque as mensagens já
# arquivadas continuam com ele no Outlook até a migração passar, e porque é o
# que `scripts/renomear_categoria_ia.py` procura para substituir.
CATEGORIA_IA_ANTERIOR = "Classificado por IA"

# Sugestão inicial de nome de pasta, só para o campo do dashboard não nascer
# vazio. O nome real é o que o usuário digitar, e o backend resolve o id pelo
# Graph: a plataforma nunca cria pasta.
PASTAS_PADRAO = {
    "pedido": "Pedidos",
    "proposta": "Propostas",
    "revisao_pedido": "Revisão de Pedidos",
    "revisao_proposta": "Revisão de Propostas",
}


def rotulo(classe: str) -> str:
    """Texto exibível de uma classe. Classe desconhecida ou vazia vira `-`.

    O traço simples é deliberado: em tabela, valor ausente se marca com `-`,
    nunca com travessão."""
    return LABELS.get(classe, "-")


def classe_valida(classe: str) -> bool:
    return classe in CLASSES


# ---------------------------------------------------------------------------
# Urgência
# ---------------------------------------------------------------------------


def calcular_prioridade(
    prazo_em_horas: Optional[int],
    urgente_semantico: bool,
    janela_horas: int,
) -> tuple:
    """Devolve `(urgente, importante)`. A conta é do Python, não do modelo.

    O agente estima duas coisas independentes: `prazo_em_horas`, quando o texto
    indica um prazo, e `urgente_semantico`, para o caso de "preciso disso hoje
    sem falta" sem número nenhum. A comparação com a janela configurada acontece
    aqui.

    A regra tem TRÊS faixas, e não duas:

    * **Urgente**: existe data e ela cai dentro da janela configurada. É o que
      precisa ser atendido agora.
    * **Importante**: existe data, mas ela está além da janela. Antes isso caía
      no mesmo balde do e-mail sem data nenhuma, e o efeito prático era ruim:
      um pedido com entrega marcada para daqui a duas semanas ficava
      indistinguível de um e-mail sem compromisso de data, e só voltava a
      aparecer quando já era tarde. Ter data é um compromisso assumido, mesmo
      que distante.
    * **Nenhum dos dois**: nenhuma data e nenhum sinal de urgência no texto.

    As duas são mutuamente exclusivas: urgente é a faixa mais forte e absorve a
    outra. `urgente_semantico` sem data continua urgente, porque "estamos
    parados esperando" é compromisso mesmo sem número.

    Separar assim tem um retorno concreto: mudar a janela de 24h para 8h
    re-marca todos os e-mails já gravados com um UPDATE, porque
    `urgencia_prazo_horas` e `urgente_semantico` estão na linha. Se o modelo
    devolvesse só o booleano, a mesma mudança exigiria reprocessar a caixa
    inteira e pagar de novo.

    Prazo negativo ou zero conta como urgente: o pedido já venceu.
    """
    if prazo_em_horas is not None:
        if int(prazo_em_horas) <= max(0, int(janela_horas)):
            return True, False
        return False, True
    if urgente_semantico:
        return True, False
    return False, False


def categorias_para(classe: str, urgente: bool, importante: bool = False) -> list:
    """Categorias do Outlook para um e-mail classificado.

    Nunca devolve nada para uma classe fora das quatro: e-mail que não se
    encaixa não é marcado nem movido, fica onde estava.

    Todo e-mail classificado leva `CATEGORIA_IA`, mesmo sem prioridade nenhuma:
    é a marca de procedência, não um adorno da urgência.
    """
    if not classe_valida(classe):
        return []
    nomes = [CATEGORIAS[classe]]
    if urgente:
        nomes.append(CATEGORIA_URGENTE)
    elif importante:
        nomes.append(CATEGORIA_IMPORTANTE)
    nomes.append(CATEGORIA_IA)
    return nomes


def clamp_confianca(valor) -> int:
    """Confiança em 0-100. O modelo às vezes devolve 0-1 ou passa de 100."""
    try:
        return max(0, min(int(valor), 100))
    except (TypeError, ValueError):
        return 0
