"""Caixa de correio falsa, em memória, no lugar da Microsoft Graph.

Substitui as funções públicas de `services/graph_client.py` nos testes do
orquestrador e do Agente 1, onde o que importa é a SEQUÊNCIA de ações (marcou
antes de mover? gravou o id novo? deixou o e-mail ignorado em paz?) e não o
protocolo HTTP. Os testes do protocolo em si usam `respx`.

O detalhe que faz esta caixa valer a pena: **`mover` devolve um id NOVO**, como
o Graph de verdade. Sem isso, a lógica de deduplicação por `internetMessageId`
nunca seria de fato exercitada, e o bug mais caro do produto (reclassificar e
recobrar tudo o que a rodada anterior moveu) passaria por todos os testes.

Ela também REGISTRA tudo o que foi chamado, em `chamadas`, para um teste poder
afirmar o que interessa: que um e-mail fora das quatro classes não gerou
nenhum PATCH nem nenhum move.
"""

from datetime import timedelta
from typing import List, Optional

from sales_support_agent.models import brt_now


class CaixaFalsa:
    def __init__(self):
        self.pastas: List[dict] = []
        self.mensagens: dict = {}      # id do Graph -> mensagem
        self.chamadas: List[tuple] = []  # (operacao, id, detalhe)
        self._sequencia = 0

        # Falhas programáveis: {(operacao, id_da_mensagem): Exception}
        self.falhas: dict = {}

    # ------------------------------------------------------------------ setup
    def add_pasta(self, nome: str, caminho: Optional[str] = None) -> str:
        self._sequencia += 1
        pasta_id = f"pasta-{self._sequencia}"
        self.pastas.append(
            {"id": pasta_id, "nome": nome, "caminho": caminho or nome, "parent_id": ""}
        )
        return pasta_id

    def add_email(
        self,
        *,
        assunto: str = "",
        corpo: str = "",
        remetente: str = "cliente@empresa.com.br",
        nome: str = "Cliente",
        imid: Optional[str] = None,
        horas_atras: int = 1,
        categorias: Optional[List[str]] = None,
    ) -> str:
        self._sequencia += 1
        graph_id = f"msg-{self._sequencia}"
        self.mensagens[graph_id] = {
            "internet_message_id": imid or f"<{graph_id}@dominio.com>",
            "graph_message_id": graph_id,
            "graph_conversation_id": f"conv-{self._sequencia}",
            "graph_web_link": f"https://outlook.office.com/{graph_id}",
            "assunto": assunto,
            "remetente_email": remetente,
            "remetente_nome": nome,
            "recebido_em": brt_now() - timedelta(hours=horas_atras),
            "corpo_texto": corpo,
            "categorias": list(categorias or []),
            "sem_internet_message_id": False,
            "_pasta": "inbox",
        }
        return graph_id

    def programar_falha(self, operacao: str, message_id: str, erro: Exception) -> None:
        self.falhas[(operacao, message_id)] = erro

    # ----------------------------------------------------------- consultas
    def emails_em(self, pasta_id: str) -> List[dict]:
        return [m for m in self.mensagens.values() if m["_pasta"] == pasta_id]

    def houve(self, operacao: str, message_id: Optional[str] = None) -> bool:
        return any(
            c[0] == operacao and (message_id is None or c[1] == message_id)
            for c in self.chamadas
        )

    def quantas(self, operacao: str) -> int:
        return sum(1 for c in self.chamadas if c[0] == operacao)

    # ------------------------------------------- a API que o cliente expõe
    async def listar_pastas(self, max_niveis: int = 3) -> List[dict]:
        self.chamadas.append(("listar_pastas", None, None))
        return list(self.pastas)

    async def resolver_pasta(self, nome_ou_caminho: str) -> dict:
        self.chamadas.append(("resolver_pasta", None, nome_ou_caminho))
        alvo = (nome_ou_caminho or "").strip().lower()
        achadas = [p for p in self.pastas if p["nome"].lower() == alvo or p["caminho"].lower() == alvo]
        if len(achadas) == 1:
            return {"encontrado": True, "id": achadas[0]["id"], "caminho": achadas[0]["caminho"], "candidatos": []}
        return {
            "encontrado": False,
            "id": "",
            "caminho": "",
            "candidatos": sorted(p["caminho"] for p in achadas),
        }

    async def listar_mensagens(self, desde, pasta: str = "", limite: int = 200) -> List[dict]:
        self.chamadas.append(("listar_mensagens", None, desde))
        pasta = pasta or "inbox"
        recebidas = [
            {k: v for k, v in m.items() if not k.startswith("_")}
            for m in self.mensagens.values()
            if m["_pasta"] == pasta and m["recebido_em"] >= desde
        ]
        recebidas.sort(key=lambda m: m["recebido_em"], reverse=True)
        return recebidas[:limite]

    async def aplicar_categorias(self, message_id: str, categorias: List[str]) -> List[str]:
        self.chamadas.append(("aplicar_categorias", message_id, list(categorias)))
        if erro := self.falhas.get(("aplicar_categorias", message_id)):
            raise erro
        if not categorias:
            return []

        mensagem = self.mensagens[message_id]
        # Mesmo comportamento do cliente de verdade: UNIÃO, nunca substituição.
        # Se este fake substituísse, o teste que protege a marcação manual do
        # usuário passaria contra um cliente que a apaga.
        for nome in categorias:
            if nome not in mensagem["categorias"]:
                mensagem["categorias"].append(nome)
        return list(mensagem["categorias"])

    async def mover_mensagem(self, message_id: str, pasta_id: str) -> str:
        self.chamadas.append(("mover_mensagem", message_id, pasta_id))
        if erro := self.falhas.get(("mover_mensagem", message_id)):
            raise erro

        mensagem = self.mensagens.pop(message_id)
        # O ID MUDA. É o comportamento real do POST /move, e é a razão de a
        # deduplicação usar internetMessageId.
        self._sequencia += 1
        novo_id = f"msg-movida-{self._sequencia}"
        mensagem["graph_message_id"] = novo_id
        mensagem["_pasta"] = pasta_id
        self.mensagens[novo_id] = mensagem
        return novo_id

    async def garantir_categorias_mestre(self, nomes: List[str]) -> bool:
        self.chamadas.append(("garantir_categorias_mestre", None, list(nomes)))
        return True

    async def testar_leitura(self) -> dict:
        return {"ok": True, "total_pastas": len(self.pastas), "pastas": [p["caminho"] for p in self.pastas]}


def instalar(monkeypatch, caixa: CaixaFalsa):
    """Troca as funções do `graph_client` pelas da caixa falsa."""
    from sales_support_agent.services import graph_client

    for nome in (
        "listar_pastas",
        "resolver_pasta",
        "listar_mensagens",
        "aplicar_categorias",
        "mover_mensagem",
        "garantir_categorias_mestre",
        "testar_leitura",
    ):
        monkeypatch.setattr(graph_client, nome, getattr(caixa, nome))
    return caixa
