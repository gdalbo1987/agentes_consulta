"""Configuração da suíte, e a trava que protege a caixa de e-mails real.

REGRA ABSOLUTA DESTE PROJETO: nenhum teste pode ler, marcar ou mover e-mail na
caixa configurada no `.env`. Ela é a caixa comercial de produção.

A regra não é confiada à disciplina de quem escreve teste. São três camadas
independentes, e qualquer uma delas sozinha já barra o acidente:

1. **Trava de rede** (`_sem_rede_para_a_microsoft`, autouse neste arquivo).
   Substitui o transporte HTTP do httpx e o adaptador do requests. Se qualquer
   código tentar falar com `graph.microsoft.com` ou com o endpoint de login do
   Entra ID, o teste FALHA na hora, com mensagem explicando o que fazer. Um
   mock esquecido vira erro vermelho, não tráfego de verdade.

   A trava **cede a vez para o teste que pede a fixture `respx_mock`**, e isso
   não é uma brecha: o respx roda com `assert_all_mocked` ligado, então rota não
   mockada levanta exceção nele em vez de virar requisição. Pedir a fixture é
   uma declaração explícita de que aquele teste mocka HTTP, e ela está visível
   na assinatura do teste, onde a revisão de código a enxerga.

   A cessão é necessária, não uma conveniência: o respx patcha
   `httpx.Client.send`, que fica ACIMA do transporte onde a trava mora. Sem
   ceder, a trava barraria os próprios testes mockados e o cliente Graph, que é
   o código que mais precisa de teste, ficaria sem cobertura. O teste
   `test_sob_respx_rota_nao_mockada_tambem_nao_escapa` existe só para provar que
   a garantia do respx vale, e quebra se uma versão futura afrouxar o padrão.

2. **Desligado por padrão** (`addopts = -m "not graph_funcional"` no
   `pytest.ini`). Um `pytest` puro nunca executa um teste de rede.

3. **Guarda de identidade** (fixture `graph_funcional`). Mesmo nos testes
   marcados, só se usa a caixa `TESTER_GRAPH_*`, e a fixture pula o teste se
   ela estiver ausente ou for igual à caixa de produção.

O banco também é isolado: `DATABASE_URL` é reapontado para um SQLite temporário
ANTES de qualquer `import reflex`, porque o `rxconfig.py` lê a variável em tempo
de import. O `load_dotenv()` do `rxconfig` não desfaz isso: o padrão dele é
`override=False`, então ele respeita o que já está no ambiente.
"""

import os
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Isolamento do banco. Precisa vir antes de importar reflex/sqlmodel.
# ---------------------------------------------------------------------------
_TMP = Path(tempfile.mkdtemp(prefix="sales_support_agent_test_"))
_DB = _TMP / "teste.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"

# Segredos de mentira, para que a suíte não dependa do `.env` da máquina e não
# use por engano uma credencial de verdade. A chave Fernet é uma chave válida
# gerada só para teste.
os.environ.setdefault("SETTINGS_ENCRYPTION_KEY", "Zt7Zq0Ux1nQO2p8xY5c3vJ4kL6mN8rS0tU2wX4yA6bE=")
os.environ.setdefault("OPENAI_API_KEY", "sk-teste-nao-e-uma-chave-real")
os.environ.setdefault("APP_BASE_URL", "http://localhost:3000")

import pytest  # noqa: E402

# ---------------------------------------------------------------------------
# Trava de rede
# ---------------------------------------------------------------------------

HOSTS_BLOQUEADOS = ("graph.microsoft.com", "login.microsoftonline.com")

_RECADO = (
    "O teste '{teste}' tentou falar com {host} de verdade.\n"
    "Nenhum teste pode tocar a caixa de e-mails real. Use o `respx` para mockar "
    "o Graph, ou a caixa falsa de tests/fakes/graph.py. Se o teste PRECISA de "
    "rede, marque-o com @pytest.mark.graph_funcional e use a fixture "
    "`graph_funcional`, que só libera a caixa TESTER."
)


def _bloqueado(url: str) -> str:
    for host in HOSTS_BLOQUEADOS:
        if host in str(url):
            return host
    return ""




@pytest.fixture(autouse=True)
def _sem_rede_para_a_microsoft(request, monkeypatch):
    """Faz o teste falhar se ele tentar sair para a Microsoft de verdade."""
    if request.node.get_closest_marker("graph_funcional"):
        return  # teste funcional: a guarda de identidade cuida dele

    if "respx_mock" in request.fixturenames:
        # O teste declarou que mocka HTTP. Quem protege a partir daqui é o
        # próprio respx, com `assert_all_mocked`: rota não mockada levanta
        # exceção nele em vez de virar requisição. Ver
        # test_sob_respx_rota_nao_mockada_tambem_nao_escapa, que existe só para
        # provar isso e quebrar se uma versão futura do respx afrouxar o padrão.
        #
        # A cessão é necessária: a trava vive no transporte, e o respx patcha
        # `httpx.Client.send`, acima dela. Sem ceder, a trava barraria os
        # próprios testes mockados e o cliente Graph ficaria sem cobertura.
        return

    nome = request.node.name

    def _falhar(url):
        host = _bloqueado(url)
        if host:
            pytest.fail(_RECADO.format(teste=nome, host=host), pytrace=False)

    # httpx, no nível do transporte (é onde o respx entra, então mock com respx
    # continua funcionando: ele intercepta antes de chegar aqui).
    import httpx

    original_sync = httpx.HTTPTransport.handle_request
    original_async = httpx.AsyncHTTPTransport.handle_async_request

    def guarda_sync(self, request_):
        _falhar(request_.url)
        return original_sync(self, request_)

    async def guarda_async(self, request_):
        _falhar(request_.url)
        return await original_async(self, request_)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", guarda_sync)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", guarda_async)

    # requests, usado pelo services/graph_mailer.py e por dentro do MSAL.
    import requests.adapters

    original_adapter = requests.adapters.HTTPAdapter.send

    def guarda_requests(self, request_, *a, **kw):
        _falhar(request_.url)
        return original_adapter(self, request_, *a, **kw)

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", guarda_requests)


@pytest.fixture
def graph_funcional():
    """Credenciais da caixa TESTER, e a recusa de rodar contra a de produção.

    Devolve o dicionário de config só quando existe uma caixa TESTER de verdade
    E ela é diferente da caixa de produção. Sem isso, pula o teste. É a razão de
    um teste funcional não conseguir, nem por configuração errada, endereçar a
    caixa comercial.
    """
    from dotenv import dotenv_values

    env = dotenv_values(Path(__file__).resolve().parents[1] / ".env")

    tester = (env.get("TESTER_GRAPH_SENDER_EMAIL") or "").strip().lower()
    producao = (env.get("GRAPH_SENDER_EMAIL") or "").strip().lower()

    if not tester:
        pytest.skip("TESTER_GRAPH_SENDER_EMAIL não configurado no .env")
    if tester == producao:
        pytest.skip(
            "TESTER_GRAPH_SENDER_EMAIL é igual a GRAPH_SENDER_EMAIL. O teste "
            "funcional só roda contra uma caixa dedicada de teste."
        )

    faltando = [
        chave
        for chave in ("TESTER_GRAPH_TENANT_ID", "TESTER_GRAPH_CLIENT_ID", "TESTER_GRAPH_CLIENT_SECRET")
        if not (env.get(chave) or "").strip()
    ]
    if faltando:
        pytest.skip("Faltam credenciais TESTER no .env: " + ", ".join(faltando))

    return {
        "sender_email": tester,
        "tenant_id": env["TESTER_GRAPH_TENANT_ID"].strip(),
        "client_id": env["TESTER_GRAPH_CLIENT_ID"].strip(),
        "client_secret": env["TESTER_GRAPH_CLIENT_SECRET"].strip(),
    }


# ---------------------------------------------------------------------------
# Banco
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def engine():
    """Engine do SQLite temporário, com o schema montado a partir do SQLModel."""
    from sqlmodel import SQLModel, create_engine

    import sales_support_agent.models  # noqa: F401  (registra as tabelas na metadata)

    eng = create_engine(os.environ["DATABASE_URL"])
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture
def sessao(engine):
    """Sessão limpa por teste: tudo o que o teste escrever some no fim."""
    from sqlmodel import Session

    conexao = engine.connect()
    transacao = conexao.begin()
    with Session(bind=conexao) as s:
        yield s
    transacao.rollback()
    conexao.close()


@pytest.fixture
def tenant(sessao):
    """A organização única. Invariante 1: existe exatamente uma."""
    from sales_support_agent.models import Tenant

    linha = Tenant(id=1, name="Coester")
    sessao.add(linha)
    sessao.commit()
    return linha
