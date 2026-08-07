"""Reconcilia as linhas de `AgentModelSetting` com as chaves do produto novo.

Por que um script, e não uma migration: `AgentModelSetting` é CONFIGURAÇÃO, não
schema. A tabela não muda de forma na conversão, só o conjunto de chaves que
mora nela. E `ensure_agent_settings()` nunca sobrescreve linha existente, de
propósito (é o que impede o seed de desfazer a escolha do super admin), então
ele cria as chaves novas mas não sabe aposentar as velhas.

O que este script faz:

1. Renomeia `insights` para `consulta`, PRESERVANDO modelo e esforço. O agente
   de consulta é o mesmo chat com outra fonte de dados, então a escolha que o
   super admin já tinha feito continua valendo. Fosse uma linha nova, ele
   voltaria ao padrão sem avisar.
2. Cria `classificacao` e `resumo` pela semente, se faltarem.
3. Apaga `product`, `prospect` e `priorizacao`.

O passo 3 é seguro, e vale registrar por quê: `TokenUsage.model` guarda o nome
do modelo DESNORMALIZADO, como texto, e não uma chave estrangeira para cá. O
custo histórico em `/admin` continua fechando depois que estas linhas somem.
Deixá-las só órfãs seria pior: três linhas que nenhum código lê, esperando
confundir o próximo leitor.

Idempotente: rodar de novo imprime "nada a fazer".

    python scripts/migrar_agentes.py
"""

import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import reflex as rx  # noqa: E402

from sales_support_agent.models import AgentModelSetting, brt_now  # noqa: E402
from sales_support_agent.services.settings import (  # noqa: E402
    AGENT_KEYS,
    ensure_agent_settings,
)

RENOMEAR = {"insights": "consulta"}
APOSENTADAS = ("product", "prospect", "priorizacao")


def main() -> None:
    alteracoes = 0

    with rx.session() as session:
        existentes = {
            linha.agent_key: linha for linha in session.query(AgentModelSetting).all()
        }

        for antiga, nova in RENOMEAR.items():
            linha = existentes.get(antiga)
            if not linha:
                continue
            if nova in existentes:
                # A chave nova já existe (o seed rodou antes deste script).
                # Manter a nova e apagar a antiga: reescrever a nova com os
                # valores da antiga poderia desfazer uma escolha recente.
                print(f"'{nova}' já existe; removendo a linha antiga '{antiga}'.")
                session.delete(linha)
            else:
                print(f"{antiga} -> {nova} (modelo {linha.model}, esforço {linha.effort} preservados)")
                linha.agent_key = nova
                linha.updated_at = brt_now()
            alteracoes += 1

        for chave in APOSENTADAS:
            linha = existentes.get(chave)
            if linha:
                print(f"removendo a chave aposentada '{chave}'")
                session.delete(linha)
                alteracoes += 1

        session.commit()

    # Cria o que ainda faltar (`classificacao`, `resumo`) pela semente.
    ensure_agent_settings()

    with rx.session() as session:
        finais = sorted(
            (linha.agent_key, linha.model, linha.effort)
            for linha in session.query(AgentModelSetting).all()
        )

    print()
    if not alteracoes:
        print("Nada a fazer: as chaves já estavam reconciliadas.")
    print("Configuração final dos agentes:")
    for chave, modelo, esforco in finais:
        print(f"  {chave:<14} {modelo:<14} esforço={esforco}")

    inesperadas = {c for c, _, _ in finais} - set(AGENT_KEYS)
    if inesperadas:
        print(f"\nAVISO: chaves fora de AGENT_KEYS continuam no banco: {sorted(inesperadas)}")


if __name__ == "__main__":
    main()
