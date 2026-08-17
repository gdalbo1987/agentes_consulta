"""Troca a categoria "Classificado por IA" por "IA" nos e-mails já arquivados.

Existe porque renomear a marca no código não mexe no que já está no Outlook: as
mensagens arquivadas antes da troca continuam com o rótulo antigo, e a caixa
ficaria com as duas marcas convivendo, o que derrota o propósito da marca de
procedência (achar de uma vez tudo o que o agente tocou).

CUSTO ZERO EM TOKENS. Não há chamada de modelo: a classe já está no banco e o
que falta é um PATCH de categorias no Graph.

POR QUE NÃO USA `aplicar_categorias`. Aquela função manda a UNIÃO das
categorias, de propósito, para nunca apagar o que uma pessoa marcou à mão. Aqui
é preciso REMOVER um item, então o PATCH é montado explicitamente. A lista
enviada preserva tudo o que existia, tirando apenas o rótulo antigo e
acrescentando o novo: nenhuma categoria de terceiro é perdida no caminho.

SEGURO DE REPETIR. Mensagem que já está com "IA" e sem o rótulo antigo é
contada como pronta e não recebe requisição de escrita.

    python scripts/renomear_categoria_ia.py --dry-run   # mostra o que faria
    python scripts/renomear_categoria_ia.py             # aplica
    python scripts/renomear_categoria_ia.py --reverter  # volta ao nome antigo
"""

import argparse
import asyncio
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(RAIZ / ".env")

import reflex as rx  # noqa: E402

from sales_support_agent.models import EmailClassificado  # noqa: E402
from sales_support_agent.services import graph_client  # noqa: E402
from sales_support_agent.services.classificacao_rules import (  # noqa: E402
    CATEGORIA_IA,
    CATEGORIA_IA_ANTERIOR,
)
from sales_support_agent.services.graph_client import GraphClientError  # noqa: E402

TENANT_PADRAO = 1


def _alvos(tenant_id: int):
    """Só os classificados, e só os que ainda têm id do Graph.

    E-mail `ignorado` fica de fora porque nunca recebeu marca nenhuma: ele não
    foi tocado pela plataforma.
    """
    with rx.session() as session:
        return [
            (e.id, e.assunto or "(sem assunto)", e.graph_message_id)
            for e in session.query(EmailClassificado)
            .filter(
                EmailClassificado.tenant_id == tenant_id,
                EmailClassificado.status == "classificado",
                EmailClassificado.graph_message_id != "",
            )
            .order_by(EmailClassificado.id)
            .all()
        ]


def _nova_lista(atuais: list, de: str, para: str) -> list:
    """Tudo o que existia, menos o rótulo antigo, mais o novo no fim."""
    mantidas = [c for c in atuais if c not in (de, para)]
    return mantidas + [para]


async def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tenant-id", type=int, default=TENANT_PADRAO)
    p.add_argument(
        "--dry-run", action="store_true",
        help="lê as categorias atuais e mostra o que faria, sem escrever nada",
    )
    p.add_argument(
        "--reverter", action="store_true",
        help="desfaz: volta de 'IA' para o nome anterior",
    )
    args = p.parse_args()

    de, para = (
        (CATEGORIA_IA, CATEGORIA_IA_ANTERIOR)
        if args.reverter
        else (CATEGORIA_IA_ANTERIOR, CATEGORIA_IA)
    )

    alvos = _alvos(args.tenant_id)
    if not alvos:
        print("Nenhum e-mail classificado com id do Graph. Nada a fazer.")
        return 0

    print(f"{len(alvos)} e-mail(s) classificado(s).")
    print(f"Trocando {de!r} por {para!r}.")
    if args.dry_run:
        print("MODO DRY RUN: nada será escrito no Outlook.\n")
    else:
        print("Aplicando. As demais categorias de cada mensagem são preservadas.\n")

    trocados = ja_ok = falhas = 0
    for eid, assunto, gid in alvos:
        try:
            resposta = await graph_client._request(
                "GET", f"/messages/{gid}?$select=categories"
            )
            atuais = resposta.json().get("categories") or []
        except GraphClientError as erro:
            falhas += 1
            print(f"  [{eid}] {assunto[:52]}\n        FALHA ao ler: {erro}")
            continue

        novas = _nova_lista(atuais, de, para)
        if novas == atuais:
            ja_ok += 1
            print(f"  [{eid}] {assunto[:52]}\n        já estava certo: {atuais}")
            continue

        if args.dry_run:
            trocados += 1
            print(f"  [{eid}] {assunto[:52]}\n        {atuais} -> {novas}")
            continue

        try:
            await graph_client._request(
                "PATCH", f"/messages/{gid}", json={"categories": novas}
            )
            trocados += 1
            print(f"  [{eid}] {assunto[:52]}\n        {atuais} -> {novas}")
        except GraphClientError as erro:
            falhas += 1
            print(f"  [{eid}] {assunto[:52]}\n        FALHA ao gravar: {erro}")

    verbo = "seriam trocados" if args.dry_run else "trocados"
    print(f"\n{verbo}: {trocados} | já estavam certos: {ja_ok} | falhas: {falhas}")
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
