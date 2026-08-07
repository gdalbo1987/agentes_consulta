"""Executa uma rodada de classificação fora da aplicação web.

Três usos:

1. **Conferência segura da primeira execução.** `--dry-run` lê a caixa e
   classifica, mas não escreve nada: nem no Graph, nem no banco. É o jeito de
   ver o que o agente FARIA numa caixa de produção antes de deixá-lo mexer nela.
2. **Backfill controlado.** `--desde AAAA-MM-DD` amplia a janela de varredura
   uma única vez, sem mudar a configuração.
3. **Saída de emergência do agendador.** Se o APScheduler em processo der
   problema no ambiente, basta apontar o Agendador de Tarefas do Windows para
   este script com `--agendado`, sem reescrever uma linha de código.

Compartilha integralmente o `stream_classificacao` com o botão do dashboard.

    python scripts/classificar.py --manual --dry-run
    python scripts/classificar.py --agendado
"""

import argparse
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

# `load_dotenv` ANTES de importar reflex: o rxconfig lê DATABASE_URL em tempo de
# import, e sem isto o script rodaria contra o banco errado, ou contra nenhum.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import reflex as rx  # noqa: E402

from sales_support_agent.models import brt_now  # noqa: E402
from sales_support_agent.services import (  # noqa: E402
    agendador,
    classificacao,
    classificacao_config,
)


def _argumentos():
    p = argparse.ArgumentParser(description="Roda uma classificação de e-mails.")
    modo = p.add_mutually_exclusive_group()
    modo.add_argument("--agendado", action="store_true",
                      help="só roda se algum horário configurado estiver vencido hoje")
    modo.add_argument("--manual", action="store_true",
                      help="roda agora, independente do horário (padrão)")
    p.add_argument("--tenant-id", type=int, default=agendador.TENANT_PADRAO)
    p.add_argument("--dry-run", action="store_true",
                   help="lê e classifica sem escrever no Graph nem no banco")
    p.add_argument("--desde", metavar="AAAA-MM-DD",
                   help="amplia a janela de varredura só nesta execução")
    return p.parse_args()


async def _rodar_dry_run(tenant_id: int, desde_forcado) -> int:
    """Dry run não cria linha de rodada: ele não pode deixar rastro nenhum."""
    print("MODO DRY RUN: nada será escrito no Outlook nem no banco.\n")

    resumo, erro = None, ""
    async for evento in classificacao.stream_classificacao(tenant_id, dry_run=True):
        if evento[0] == "progress":
            print(f"  [{evento[1]}/{evento[2]}] {evento[3]}")
        elif evento[0] == "done":
            resumo = evento[1]
        elif evento[0] == "error":
            erro = evento[1]

    if erro:
        print(f"\nERRO: {erro}")
        return 1

    print(
        f"\nLidos: {resumo['total_emails']} | classificaria: {resumo['classificados']} | "
        f"ignoraria: {resumo['ignorados']} | já conhecidos: {resumo['puladas']} | "
        f"urgentes: {resumo['urgentes']} | falhas: {resumo['falhas']}"
    )
    for aviso in resumo["avisos"]:
        print(f"  aviso: {aviso}")
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _argumentos()

    cfg = classificacao_config.get_config(args.tenant_id)

    slot = ""
    if args.agendado:
        slot = classificacao_config.slot_devido(brt_now(), cfg) or ""
        if not slot:
            # Silêncio é o comportamento correto: este script roda de tempos em
            # tempos e, na maioria das vezes, não há nada a fazer.
            return 0

    if args.desde:
        try:
            desde = datetime.strptime(args.desde, "%Y-%m-%d")
        except ValueError:
            print("O formato de --desde é AAAA-MM-DD.")
            return 1
        horas = max(1, int((brt_now() - desde).total_seconds() // 3600))
        print(f"Janela ampliada para {horas} hora(s) só nesta execução.")
        classificacao_config.salvar_config(args.tenant_id, lookback_horas=horas)

    if args.dry_run:
        return asyncio.run(_rodar_dry_run(args.tenant_id, args.desde))

    if args.agendado:
        classificacao_config.marcar_execucao_agendada(args.tenant_id)

    resultado = asyncio.run(
        agendador.executar(
            args.tenant_id,
            origem="agendado" if args.agendado else "manual",
            slot=slot,
        )
    )

    if resultado["pulou"]:
        print(f"Nada feito: {resultado['motivo']}.")
        return 0

    if resultado["erro"]:
        print(f"ERRO: {resultado['erro']}")
        return 1

    r = resultado["resumo"] or {}
    print(
        f"Classificados: {r.get('classificados', 0)} | ignorados: {r.get('ignorados', 0)} | "
        f"já conhecidos: {r.get('puladas', 0)} | urgentes: {r.get('urgentes', 0)} | "
        f"resumidos: {r.get('resumidos', 0)} | falhas: {r.get('falhas', 0)}"
    )
    for aviso in r.get("avisos", []):
        print(f"  aviso: {aviso}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
