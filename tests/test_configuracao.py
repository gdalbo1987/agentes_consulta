"""Configuração e conexão: o que a Fase 0 consertou não pode voltar a quebrar.

A aplicação passou um tempo sem subir porque o diretório do pacote foi renomeado
para `sales_support_agent/` sem que o `app_name` do `rxconfig.py` e os imports
acompanhassem. Era um erro de uma linha que só aparecia ao rodar. Estes testes
transformam isso em falha de suíte.
"""

import os
import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
PACOTE = RAIZ / "sales_support_agent"


def test_app_name_do_rxconfig_bate_com_o_diretorio_do_pacote():
    """O `app_name` tem de ser o nome da pasta, senão o Reflex não acha o módulo.

    O Reflex resolve o ponto de entrada como `<app_name>/<app_name>.py`. Com o
    `app_name` errado ele procura um arquivo que não existe e a aplicação não
    sobe, sem erro de compilação que denuncie a causa.
    """
    fonte = (RAIZ / "rxconfig.py").read_text(encoding="utf-8")
    achado = re.search(r'app_name\s*=\s*["\']([^"\']+)["\']', fonte)

    assert achado, "rxconfig.py não declara app_name"
    nome = achado.group(1)

    assert nome == PACOTE.name == "sales_support_agent"
    assert (PACOTE / f"{nome}.py").exists(), f"falta o ponto de entrada {nome}/{nome}.py"


def test_nao_sobrou_import_do_pacote_antigo():
    """Nenhum `from prospect_agent.` pode voltar ao código.

    A varredura ignora de propósito o texto solto `prospect_agent`, que ainda
    aparece como DADO: é o `agent_name` gravado em `TokenUsage` e uma chave de
    `AGENTE_PARA_CHAVE_DE_CONFIG`. Reescrever esses literais quebraria a
    atribuição de custo do histórico, então o que se proíbe é o caminho de
    import, não a palavra.
    """
    culpados = []
    for arquivo in list(PACOTE.rglob("*.py")) + list((RAIZ / "scripts").rglob("*.py")):
        for numero, linha in enumerate(arquivo.read_text(encoding="utf-8").splitlines(), 1):
            if "from prospect_agent." in linha or "import prospect_agent." in linha:
                culpados.append(f"{arquivo.relative_to(RAIZ)}:{numero}")

    assert not culpados, "imports do pacote antigo encontrados em:\n  " + "\n  ".join(culpados)


def test_database_url_vem_do_ambiente_e_nao_esta_no_codigo():
    """`DATABASE_URL` é a única configuração que fica no `.env`.

    Ela não pode ser movida para o painel do super admin pelo motivo óbvio: não
    dá para guardar no banco o endereço do banco. O teste também garante que
    ninguém escreveu uma string de conexão no código.
    """
    fonte = (RAIZ / "rxconfig.py").read_text(encoding="utf-8")

    assert "DATABASE_URL" in fonte
    assert "os.environ" in fonte or "getenv" in fonte
    assert not re.search(r'db_url\s*=\s*["\']postgresql', fonte), "string de conexão fixa no rxconfig.py"


def test_a_suite_esta_apontada_para_um_banco_de_teste():
    """A suíte nunca pode escrever no banco de trabalho.

    O `conftest.py` reaponta `DATABASE_URL` antes de qualquer import do Reflex.
    Se essa ordem se perder, os testes passariam a mexer no banco real, e é o
    tipo de estrago que só se percebe depois.
    """
    url = os.environ["DATABASE_URL"]

    assert url.startswith("sqlite:///"), f"a suíte não está no SQLite temporário: {url}"
    assert "sales_support_agent_test" in url


def test_o_dotenv_do_rxconfig_nao_atropela_o_ambiente():
    """`load_dotenv()` precisa continuar sem `override=True`.

    O isolamento do banco na suíte depende disso: o `conftest` define a variável
    antes, e o `rxconfig` só preenche o que estiver faltando. Com `override=True`
    o `.env` venceria e a suíte cairia no banco de trabalho.
    """
    fonte = (RAIZ / "rxconfig.py").read_text(encoding="utf-8")
    assert "load_dotenv()" in fonte
    assert "override=True" not in fonte


@pytest.mark.parametrize(
    "modulo",
    [
        "sales_support_agent.models",
        "sales_support_agent.state",
        "sales_support_agent.services.settings",
        "sales_support_agent.services.crypto",
        "sales_support_agent.services.graph_mailer",
    ],
)
def test_modulos_centrais_importam(modulo):
    """Prova que o pacote renomeado resolve de ponta a ponta."""
    __import__(modulo)


def test_chave_de_criptografia_e_lida_do_ambiente():
    """Sem `SETTINGS_ENCRYPTION_KEY` os segredos não abrem, e isso tem de doer."""
    from sales_support_agent.services import crypto

    texto = "um-segredo-qualquer"
    assert crypto.decrypt(crypto.encrypt(texto)) == texto
    # String vazia não é segredo: não vira token, e volta vazia.
    assert crypto.encrypt("") == ""
    assert crypto.decrypt("") == ""
