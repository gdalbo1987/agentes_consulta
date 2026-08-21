"""As convenções do Reflex que quebram em RUNTIME, e não na compilação.

Esta versão do Reflex não gera setters automáticos: um campo ligado a
`on_change` sem o seu `set_<campo>` escrito à mão compila perfeitamente e
explode quando o usuário digita. É a classe de bug mais cara deste projeto,
porque só aparece com alguém usando a tela.

Estes testes leem o código-fonte. É grosseiro de propósito: instanciar States do
Reflex exigiria roteador e sessão de browser, e o que se quer garantir aqui é
uma propriedade sintática, não comportamento.
"""

import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
PACOTE = RAIZ / "sales_support_agent"
FONTE_STATE = (PACOTE / "state.py").read_text(encoding="utf-8")

PAGINAS = sorted((PACOTE / "pages").glob("*.py"))


def _setters_declarados() -> set:
    return set(re.findall(r"def (set_\w+)\(", FONTE_STATE))


def _handlers_declarados() -> set:
    return set(re.findall(r"def (\w+)\(", FONTE_STATE))


@pytest.mark.parametrize("pagina", PAGINAS, ids=lambda p: p.name)
def test_todo_on_change_aponta_para_um_handler_que_existe(pagina):
    """Um `on_change=State.set_x` sem `set_x` no State quebra ao digitar.

    O Reflex desta versão não cria o setter sozinho, e a ligação só é resolvida
    quando o evento chega do browser. Compila, sobe, e falha na mão do usuário.
    """
    texto = pagina.read_text(encoding="utf-8")
    declarados = _handlers_declarados()

    referenciados = set(
        re.findall(r"on_(?:change|open_change|click|submit)=(\w+State)\.(\w+)", texto)
    )

    faltando = [
        f"{estado}.{metodo}"
        for estado, metodo in referenciados
        if metodo not in declarados and not metodo.startswith("load_")
    ]
    assert not faltando, (
        f"{pagina.name} liga eventos a handlers que não existem no state.py: {faltando}"
    )


def test_campos_de_formulario_do_dashboard_tem_setter():
    """Cada campo do dashboard ligado a on_change precisa do seu setter."""
    declarados = _setters_declarados()

    for campo in (
        "set_horario_1", "set_horario_2", "set_janela_urgencia_horas",
        "set_lookback_horas", "set_filtro_data_inicio", "set_filtro_data_fim",
        "set_filtro_apenas_urgentes", "set_filtro_apenas_importantes",
        "set_pasta_input", "set_detalhe_aberto",
        "set_prompt_texto", "set_prompt_aberto",
        "set_confirm_restaurar_prompt_open",
    ):
        assert campo in declarados, f"falta o setter {campo} no DashboardState"


def test_dialogos_controlados_tem_setter_de_abertura():
    """`rx.dialog.root(open=..., on_open_change=...)` precisa dos dois lados."""
    declarados = _setters_declarados()

    for campo in ("set_detalhe_aberto", "set_clear_dialog_open",
                  "set_confirm_counters_open", "set_confirm_logs_open",
                  "set_confirm_zerar_open"):
        assert campo in declarados, f"falta {campo}"


def test_nenhum_travessao_em_texto_de_interface():
    """Regra permanente do projeto, e vale para tudo que o usuário lê.

    Docstring e comentário de código não contam: eles não chegam à tela. O que
    se procura é travessão dentro de string literal.
    """
    culpados = []
    for arquivo in list((PACOTE / "pages").glob("*.py")) + list(
        (PACOTE / "components").glob("*.py")
    ):
        fonte = arquivo.read_text(encoding="utf-8")
        # Tira docstrings antes de procurar, para não acusar comentário.
        sem_docstring = re.sub(r'"""[\s\S]*?"""', "", fonte)
        for numero, linha in enumerate(sem_docstring.splitlines(), 1):
            despido = linha.split("#")[0]
            if ("—" in despido or "–" in despido) and ('"' in despido or "'" in despido):
                culpados.append(f"{arquivo.name}:{numero}: {linha.strip()[:80]}")

    assert not culpados, "travessão em texto de interface:\n  " + "\n  ".join(culpados)


def test_servicos_achatam_os_dados_antes_de_mandar_para_a_ui():
    """`foreach` do Reflex não acessa dicionário aninhado tipado.

    O modelo de leitura precisa devolver campos de topo. Este teste confere a
    propriedade onde ela importa: nas funções que alimentam a tabela e o painel
    de urgências.
    """
    from sales_support_agent.services import emails_query

    for funcao in (emails_query.listar_emails, emails_query.urgencias):
        assert funcao.__doc__, f"{funcao.__name__} sem docstring"

    # Um dicionário achatado: nenhum valor pode ser dict.
    exemplo = {
        "id": 1, "assunto": "x", "cliente": "y", "recebido_em": "z",
        "classe_label": "Pedido", "urgente": False,
    }
    assert not any(isinstance(v, dict) for v in exemplo.values())


def test_handlers_de_background_snapshotam_o_state_antes_do_trabalho_longo():
    """`@rx.event(background=True)` não pode ler `self.*` fora do lock.

    O padrão do projeto é abrir `async with self:` e copiar o que precisa para
    variáveis locais ANTES do `await` demorado.
    """
    blocos = re.findall(
        r"@rx\.event\(background=True\)\s*\n\s*async def (\w+)\(self[^)]*\):([\s\S]*?)(?=\n    @|\n    def |\nclass |\Z)",
        FONTE_STATE,
    )
    assert blocos, "nenhum handler de background encontrado: o padrão sumiu?"

    for nome, corpo in blocos:
        assert "async with self:" in corpo, (
            f"o handler de background {nome} não abre `async with self:` antes do trabalho"
        )
