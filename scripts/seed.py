"""Seed inicial da plataforma interna da Coester.

Cria o que o app precisa para subir pela primeira vez:

1. A organização única — `Tenant(id=1, name="Coester")`. Como o modelo
   multi-tenant foi descontinuado, esta é a ÚNICA linha que deve existir nesta
   tabela; todos os usuários e todos os dados operacionais apontam para ela.
2. O primeiro super admin. É o único usuário que nasce fora do fluxo de convite
   (não haveria quem o convidasse). Daí em diante, toda admissão passa por
   `/admin` → "Convidar usuário".
3. As linhas de configuração de agentes de IA e integrações, semeadas do `.env`.

Idempotente: rodar de novo não duplica nada nem sobrescreve uma senha já
trocada pelo usuário. Rodá-lo de novo também é o caminho de RESGATE quando a
organização fica sem super admin (um super admin pode se rebaixar sozinho em
`/admin`): a permissão é restaurada sem tocar na senha.

    SUPER_ADMIN_SENHA="uma-senha-forte" python scripts/seed.py
"""

import os
import sys
from pathlib import Path

import bcrypt
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlmodel import Session, select

# Permite rodar como `python scripts/seed.py` a partir da raiz do projeto.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from sales_support_agent.models import Tenant, User  # noqa: E402

ORGANIZACAO = "Coester"

# Identidade do primeiro super admin. Vem do ambiente para o script não carregar
# o nome de uma pessoa específica — em outra instalação basta trocar o `.env`.
SUPER_ADMIN_EMAIL = os.environ.get("SUPER_ADMIN_EMAIL", "giuliano@coester.com.br")
SUPER_ADMIN_NOME = os.environ.get("SUPER_ADMIN_NOME", "Giuliano Dal Bó")

# A senha inicial NÃO tem valor padrão, de propósito. Ela é lida de
# `SUPER_ADMIN_SENHA` e só é usada para gerar o hash bcrypt gravado no banco —
# em texto puro ela não vai para lugar nenhum. Deixar um default aqui
# publicaria uma credencial real no repositório, e um default genérico
# ("admin", "changeme") seria pior ainda: viraria a senha de produção de quem
# esquecesse de definir a variável. Sem a variável, o seed para e diz o que
# fazer. Troque a senha no primeiro acesso em /profile.
SUPER_ADMIN_SENHA = os.environ.get("SUPER_ADMIN_SENHA", "")

_ERRO_SEM_SENHA = (
    "SUPER_ADMIN_SENHA não configurada. É a senha inicial do primeiro super "
    f"admin ({SUPER_ADMIN_EMAIL}), necessária apenas nesta primeira execução.\n"
    "Defina no .env (ou na variável de ambiente do deploy) e rode de novo:\n"
    '    SUPER_ADMIN_SENHA="uma-senha-forte"\n'
    "Só o hash bcrypt vai para o banco. Troque a senha no primeiro acesso, em "
    "/profile, e remova a variável depois."
)


def main() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL não configurada no .env.")

    engine = create_engine(database_url)
    with Session(engine) as session:
        tenant = session.exec(select(Tenant).where(Tenant.name == ORGANIZACAO)).first()
        if not tenant:
            tenant = Tenant(name=ORGANIZACAO)
            session.add(tenant)
            session.commit()
            session.refresh(tenant)
            print(f"Organização criada: {ORGANIZACAO} (id={tenant.id})")
        else:
            print(f"Organização já existia: {ORGANIZACAO} (id={tenant.id})")

        user = session.exec(
            select(User).where(User.email == SUPER_ADMIN_EMAIL)
        ).first()
        if user:
            # Não reescreve a senha: se o super admin já trocou a dele, o seed
            # não pode devolvê-la ao valor de fábrica. Só garante a permissão.
            if not user.is_superadmin:
                user.is_superadmin = True
                session.add(user)
                session.commit()
                print(f"Super admin restaurado: {SUPER_ADMIN_EMAIL}")
            else:
                print(f"Super admin já existia: {SUPER_ADMIN_EMAIL}")
        else:
            # A senha só é exigida quando há usuário a CRIAR. Rodar o seed para
            # restaurar a permissão de um super admin existente (o caso acima)
            # continua funcionando sem a variável — é o caminho de resgate
            # quando alguém se rebaixa sozinho e tranca o /admin.
            if not SUPER_ADMIN_SENHA:
                raise SystemExit(_ERRO_SEM_SENHA)
            hashed = bcrypt.hashpw(
                SUPER_ADMIN_SENHA.encode("utf-8"), bcrypt.gensalt()
            ).decode("utf-8")
            session.add(
                User(
                    email=SUPER_ADMIN_EMAIL,
                    name=SUPER_ADMIN_NOME,
                    hashed_password=hashed,
                    tenant_id=tenant.id,
                    is_superadmin=True,
                )
            )
            session.commit()
            print(f"Super admin criado: {SUPER_ADMIN_EMAIL}")

    # As duas funções abrem sua própria sessão (via rx.session) e são
    # idempotentes — mesma rotina que o /admin já executa no on_load.
    from sales_support_agent.services.settings import (
        ensure_agent_settings,
        ensure_integration_settings,
    )

    ensure_agent_settings()
    ensure_integration_settings()
    print("Configurações de agentes e integrações semeadas.")


if __name__ == "__main__":
    main()
