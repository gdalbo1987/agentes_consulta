"""Reaponta as linhas de `AgentModelSetting` para os modelos oferecidos hoje.

Necessário porque `services/settings.ensure_agent_settings()` só CRIA linhas que
faltam — ele nunca sobrescreve uma existente, já que a escolha do super admin em
`/admin` é definitiva. Consequência: quando `MODELOS_DISPONIVEIS` muda, as linhas
já gravadas ficam apontando para um modelo que sumiu do dropdown, e o `rx.select`
do painel passa a renderizar sem valor correspondente.

Só mexe no campo `model`, e só nas linhas cujo modelo está em `_DE_PARA`. O
`effort` de cada agente é preservado — ele é ajustado a mão pelo super admin
(hoje `prospect` roda em "medium", diferente da semente).

Idempotente: rodar duas vezes não muda nada na segunda.

    python scripts/migrar_modelos.py
"""

import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import reflex as rx  # noqa: E402  (precisa do .env carregado antes do import)

from prospect_agent.models import AgentModelSetting, brt_now  # noqa: E402

# modelo antigo -> modelo novo
_DE_PARA = {
    "gpt-5": "gpt-5.4",
    "gpt-5-mini": "gpt-5.4-mini",
    "gpt-5-nano": "gpt-5.4-nano",
}


def main() -> None:
    with rx.session() as session:
        linhas = session.query(AgentModelSetting).all()
        alteradas = 0
        for linha in linhas:
            novo = _DE_PARA.get(linha.model)
            if not novo:
                print(f"  {linha.agent_key:12} {linha.model:15} -> (sem mudança)")
                continue
            print(f"  {linha.agent_key:12} {linha.model:15} -> {novo}")
            linha.model = novo
            linha.updated_at = brt_now()
            alteradas += 1
        if alteradas:
            session.commit()
        print(f"\n{alteradas} de {len(linhas)} linha(s) atualizada(s).")


if __name__ == "__main__":
    main()
