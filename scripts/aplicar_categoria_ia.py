"""Aplica a categoria "Classificado por IA" aos e-mails JÁ classificados.

Existe porque a marca de procedência foi acrescentada depois que a caixa já
tinha e-mails arquivados pelo agente. Eles nunca a receberiam sozinhos: a
deduplicação por `internetMessageId` pula e-mail já conhecido sem nenhuma
chamada ao modelo, que é justamente a trava de custo do pipeline.

CUSTO ZERO EM TOKENS. Não há chamada de modelo aqui: a classe já está no banco,
e o que falta é só um PATCH de categorias no Graph.

SEGURO DE REPETIR. `graph_client.aplicar_categorias` lê as categorias atuais e
manda a UNIÃO, então rodar duas vezes não duplica nada e nunca apaga categoria
que uma pessoa marcou à mão no Outlook.

    python scripts/aplicar_categoria_ia.py --dry-run   # mostra o que faria
    python scripts/aplicar_categoria_ia.py             # aplica
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
from sales_support_agent.services.classificacao_rules import CATEGORIA_IA  # noqa: E402
from sales_support_agent.services.graph_client import GraphClientError  # noqa: E402

TENANT_PADRAO = 1


def _alvos(tenant_id: int):
    """Só os classificados, e só os que ainda têm id do Graph.

    E-mail `ignorado` fica de fora de propósito: ele não foi tocado pela
    plataforma, e marcá-lo como "classificado por IA" seria mentira no Outlook.
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


async def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tenant-id", type=int, default=TENANT_PADRAO)
    p.add_argument(
        "--dry-run", action="store_true",
        help="lê as categorias atuais e mostra o que faria, sem escrever nada",
    )
    args = p.parse_args()

    alvos = _alvos(args.tenant_id)
    if not alvos:
        print("Nenhum e-mail classificado com id do Graph. Nada a fazer.")
        return 0

    print(f"{len(alvos)} e-mail(s) classificado(s).")
    print(f"Categoria a acrescentar: {CATEGORIA_IA!r}")
    if args.dry_run:
        print("MODO DRY RUN: nada será escrito no Outlook.\n")
    else:
        print("Aplicando (a união preserva as categorias existentes).\n")

    ja_tinha = aplicados = falhas = 0
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

        if CATEGORIA_IA in atuais:
            ja_tinha += 1
            print(f"  [{eid}] {assunto[:52]}\n        já tinha: {atuais}")
            continue

        if args.dry_run:
            print(f"  [{eid}] {assunto[:52]}\n        {atuais} -> {atuais + [CATEGORIA_IA]}")
            aplicados += 1
            continue

        try:
            novas = await graph_client.aplicar_categorias(gid, [CATEGORIA_IA])
            aplicados += 1
            print(f"  [{eid}] {assunto[:52]}\n        {atuais} -> {novas}")
        except GraphClientError as erro:
            falhas += 1
            print(f"  [{eid}] {assunto[:52]}\n        FALHA ao aplicar: {erro}")

    verbo = "seriam marcados" if args.dry_run else "marcados"
    print(f"\n{verbo}: {aplicados} | já tinham: {ja_tinha} | falhas: {falhas}")
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
