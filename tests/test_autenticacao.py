"""Os invariantes de acesso da plataforma, que a conversão não pode afrouxar.

O acesso é **somente por convite**: não há cadastro público nem login social. O
super admin convida informando nome, e-mail e classe, e **nenhuma senha é
definida no convite**; o convidado escolhe a dele por um link de uso único que
vale 24 horas.

Estes testes exercitam a regra pelo banco e pelo código-fonte dos handlers, e
não dirigindo o State do Reflex, que exigiria roteador e sessão de browser. O
que se protege aqui é a regra de negócio, que é o que uma refatoração
distraída quebra: um `hashed_password` preenchido no convite, um token que
sobrevive ao uso, ou uma expiração que deixa de ser checada.
"""

import re
from datetime import timedelta
from pathlib import Path

import bcrypt
import pytest

from sales_support_agent.models import User, brt_now

FONTE_STATE = (
    Path(__file__).resolve().parents[1] / "sales_support_agent" / "state.py"
).read_text(encoding="utf-8")


def _bloco(nome: str) -> str:
    """Recorta o corpo de um método de `state.py` para inspeção estrutural."""
    achado = re.search(rf"\n    def {nome}\(.*?(?=\n    def |\nclass )", FONTE_STATE, re.S)
    assert achado, f"método {nome} não encontrado em state.py"
    return achado.group(0)


def _convidar(sessao, email: str, nome: str, super_admin: bool = False) -> tuple[User, str]:
    """Reproduz exatamente o que `AdminState.create_user` grava."""
    import secrets

    token = secrets.token_urlsafe(32)
    usuario = User(
        email=email,
        name=nome,
        tenant_id=1,
        is_superadmin=super_admin,
        reset_token=token,
        reset_token_expires=brt_now() + timedelta(hours=24),
    )
    sessao.add(usuario)
    sessao.commit()
    sessao.refresh(usuario)
    return usuario, token


# ---------------------------------------------------------------------------
# Convite
# ---------------------------------------------------------------------------


def test_convite_nao_define_senha(tenant, sessao):
    """O convidado nasce sem hash de senha.

    É o que garante que nunca circula senha provisória: quem convida não sabe, e
    nunca soube, a senha de quem foi convidado.
    """
    usuario, token = _convidar(sessao, "novo@coester.com.br", "Novo")

    assert usuario.hashed_password is None
    assert usuario.reset_token == token


def test_create_user_nao_escreve_hashed_password():
    """Trava estrutural: o handler de convite não pode ganhar uma senha.

    O teste acima verifica o dado; este verifica o código, para que a regra não
    seja quebrada por alguém "facilitando" o convite com uma senha padrão.
    """
    corpo = _bloco("create_user")

    assert "hashed_password" not in corpo, (
        "create_user passou a definir senha no convite. O convidado tem de "
        "escolher a própria senha pelo link de uso único."
    )
    assert "reset_token" in corpo
    assert "hours=24" in corpo


def test_token_do_convite_vale_24_horas(tenant, sessao):
    usuario, _ = _convidar(sessao, "prazo@coester.com.br", "Prazo")

    restante = usuario.reset_token_expires - brt_now()
    assert timedelta(hours=23) < restante <= timedelta(hours=24)


def test_usuario_sem_senha_nao_autentica(tenant, sessao):
    """Convite ainda não aceito não entra.

    `AuthState.login` recusa quando `hashed_password` é nulo, e devolve a mesma
    mensagem genérica de senha errada, de propósito: a tela de login não pode
    servir para descobrir quais e-mails existem na base.
    """
    usuario, _ = _convidar(sessao, "pendente@coester.com.br", "Pendente")

    assert not usuario.hashed_password

    corpo = _bloco("login")
    assert "not user.hashed_password" in corpo
    assert corpo.count("E-mail ou senha incorretos.") == 2, (
        "as duas recusas do login precisam usar a MESMA mensagem, senão a "
        "diferença entre elas revela se o e-mail existe"
    )


# ---------------------------------------------------------------------------
# Aceite do convite e redefinição
# ---------------------------------------------------------------------------


def test_aceitar_convite_define_a_senha_e_queima_o_token(tenant, sessao):
    """O link é de uso único: usar consome.

    Sem zerar o token, o mesmo link redefiniria a senha de novo, e um e-mail
    encaminhado ou vazado viraria uma porta permanente.
    """
    usuario, token = _convidar(sessao, "aceita@coester.com.br", "Aceita")

    # O que ResetPasswordState.do_reset faz.
    usuario.hashed_password = bcrypt.hashpw(b"senha-escolhida", bcrypt.gensalt()).decode()
    usuario.reset_token = None
    usuario.reset_token_expires = None
    sessao.commit()

    assert usuario.reset_token is None
    assert usuario.reset_token_expires is None
    assert bcrypt.checkpw(b"senha-escolhida", usuario.hashed_password.encode())

    encontrado = sessao.query(User).filter(User.reset_token == token).first()
    assert encontrado is None, "o token continuou válido depois de usado"


def test_do_reset_checa_expiracao_e_confirmacao():
    """Trava estrutural das três recusas da redefinição."""
    corpo = _bloco("do_reset")

    assert "reset_token_expires" in corpo and "<= brt_now()" in corpo, "expiração não é checada"
    assert "não coincidem" in corpo, "a confirmação de senha não é checada"
    assert "len(new_password) < 6" in corpo, "o tamanho mínimo da senha não é checado"
    assert "user.reset_token = None" in corpo, "o token não é consumido"


def test_senha_errada_nao_autentica(tenant, sessao):
    usuario, _ = _convidar(sessao, "senha@coester.com.br", "Senha")
    usuario.hashed_password = bcrypt.hashpw(b"a-certa", bcrypt.gensalt()).decode()
    sessao.commit()

    assert bcrypt.checkpw(b"a-certa", usuario.hashed_password.encode())
    assert not bcrypt.checkpw(b"a-errada", usuario.hashed_password.encode())


# ---------------------------------------------------------------------------
# Papéis
# ---------------------------------------------------------------------------


def test_existem_apenas_dois_papeis(tenant, sessao):
    """Não há tabela de papéis: o booleano `is_superadmin` é a hierarquia toda."""
    comum, _ = _convidar(sessao, "comum@coester.com.br", "Comum", super_admin=False)
    chefe, _ = _convidar(sessao, "chefe@coester.com.br", "Chefe", super_admin=True)

    assert comum.is_superadmin is False
    assert chefe.is_superadmin is True


def test_login_roteia_super_admin_para_o_painel():
    corpo = _bloco("login")

    assert 'rx.redirect("/admin")' in corpo
    assert 'rx.redirect("/dashboard")' in corpo
    assert corpo.index("is_superadmin") < corpo.index('rx.redirect("/admin")')


def test_super_admin_pode_promover_outro_super_admin():
    """Invariante do produto: a hierarquia se propaga a partir do painel."""
    corpo = _bloco("save_user")

    assert "is_superadmin" in corpo
    assert "Super Admin" in corpo


def test_convite_e_a_unica_admissao():
    """Nenhuma rota de cadastro público pode reaparecer."""
    raiz = Path(__file__).resolve().parents[1] / "sales_support_agent"
    entrada = (raiz / "sales_support_agent.py").read_text(encoding="utf-8")

    for proibida in ("/signup", "/cadastro", "/register", "/checkout"):
        assert proibida not in entrada, f"rota {proibida} reapareceu no ponto de entrada"

    paginas = {p.name for p in (raiz / "pages").glob("*.py")}
    for proibida in ("signup.py", "cadastro.py", "register.py", "checkout.py"):
        assert proibida not in paginas, f"página {proibida} reapareceu"


@pytest.mark.parametrize("campo", ["email", "reset_token"])
def test_campos_de_busca_de_usuario_sao_indexados(campo):
    """`email` e `reset_token` são procurados a cada login e a cada redefinição."""
    coluna = User.__table__.columns[campo]
    assert coluna.index, f"User.{campo} precisa de índice"
