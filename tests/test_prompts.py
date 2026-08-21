"""O prompt do classificador, editável pela organização.

O que se protege aqui não é quem pode editar, e sim que uma edição ruim não
derrube a classificação da caixa inteira e que sempre haja caminho de volta.
"""

import pytest

from sales_support_agent.models import PromptAgente, Tenant
from sales_support_agent.services import prompts
from sales_support_agent.services.classificacao_agent import (
    MARCADORES,
    PROMPT_PADRAO,
    renderizar,
)

TENANT = 1


@pytest.fixture
def banco(engine, monkeypatch):
    from sqlmodel import Session

    import reflex as rx

    monkeypatch.setattr(rx, "session", lambda *a, **k: Session(engine))

    with Session(engine) as s:
        if not s.get(Tenant, TENANT):
            s.add(Tenant(id=TENANT, name="Coester"))
            s.commit()

    def _limpar():
        with Session(engine) as s:
            for linha in s.query(PromptAgente).all():
                s.delete(linha)
            s.commit()

    _limpar()
    yield engine
    _limpar()


# ---------------------------------------------------------------------------
# Renderização
# ---------------------------------------------------------------------------


def test_todos_os_marcadores_sao_substituidos():
    saida = renderizar(PROMPT_PADRAO, 8)

    restantes = [m for m in MARCADORES if m in saida]
    assert not restantes, f"marcadores não substituídos: {restantes}"


def test_a_janela_configurada_chega_ao_texto():
    assert "8" in renderizar("Prazo de {janela_horas} horas.", 8)
    assert renderizar("Prazo de {janela_horas} horas.", 24) == "Prazo de 24 horas."


def test_chave_solta_no_texto_nao_derruba_a_classificacao():
    """O campo é editável por qualquer usuário, e alguém vai digitar uma chave.

    Com `str.format`, um `{` avulso levantaria KeyError no meio da rodada e
    pararia a classificação da caixa inteira. Com `replace`, o pior caso é a
    chave chegar ao modelo como texto.
    """
    saida = renderizar("Use o formato {assim} e {outro_qualquer}.", 8)

    assert saida == "Use o formato {assim} e {outro_qualquer}."


# ---------------------------------------------------------------------------
# Texto em vigor
# ---------------------------------------------------------------------------


def test_sem_linha_no_banco_vale_o_padrao_do_codigo(banco):
    assert prompts.texto_em_vigor(TENANT, "classificacao") == PROMPT_PADRAO
    assert prompts.get_prompt(TENANT, "classificacao")["no_padrao"] is True


def test_depois_de_salvar_vale_o_texto_do_banco(banco):
    prompts.salvar_prompt(
        TENANT, "classificacao", "Classifique tudo como pedido.",
        autor_nome="Ana", autor_email="ana@x.com",
    )

    assert prompts.texto_em_vigor(TENANT, "classificacao") == "Classifique tudo como pedido."
    assert prompts.get_prompt(TENANT, "classificacao")["no_padrao"] is False


def test_prompt_vazio_e_recusado(banco):
    """Um prompt em branco transformaria o classificador num rotulador aleatório."""
    with pytest.raises(ValueError) as erro:
        prompts.salvar_prompt(TENANT, "classificacao", "   ")

    assert "Restaurar padrão" in str(erro.value)


def test_texto_em_vigor_nunca_devolve_vazio(banco):
    """Mesmo com a linha em branco no banco, o agente recebe instruções."""
    from sqlmodel import Session

    with Session(banco) as s:
        s.add(PromptAgente(tenant_id=TENANT, chave="classificacao", texto="", versao=3))
        s.commit()

    assert prompts.texto_em_vigor(TENANT, "classificacao") == PROMPT_PADRAO


# ---------------------------------------------------------------------------
# Versão, autoria e volta ao padrão
# ---------------------------------------------------------------------------


def test_cada_gravacao_avanca_a_versao_e_registra_o_autor(banco):
    v1 = prompts.salvar_prompt(
        TENANT, "classificacao", "texto A", autor_nome="Ana", autor_email="ana@x.com"
    )
    v2 = prompts.salvar_prompt(
        TENANT, "classificacao", "texto B", autor_nome="Bruno", autor_email="b@x.com",
        versao_esperada=v1,
    )

    assert (v1, v2) == (1, 2)
    dados = prompts.get_prompt(TENANT, "classificacao")
    assert dados["atualizado_por_nome"] == "Bruno"
    assert dados["updated_at"] is not None


def test_salvar_com_versao_velha_e_recusado(banco):
    """Aqui o lost update custa uma calibragem inteira, não um horário."""
    prompts.salvar_prompt(TENANT, "classificacao", "de Ana", autor_nome="Ana")
    versao_de_b = 0  # B abriu a tela quando ainda estava no padrão

    with pytest.raises(prompts.PromptDesatualizado):
        prompts.salvar_prompt(
            TENANT, "classificacao", "de Bruno", autor_nome="Bruno",
            versao_esperada=versao_de_b,
        )

    assert prompts.texto_em_vigor(TENANT, "classificacao") == "de Ana"


def test_restaurar_apaga_a_linha_em_vez_de_copiar_o_padrao(banco):
    """Copiado, o texto congelaria na versão do dia em que o botão foi apertado.

    Qualquer melhoria futura do padrão deixaria de chegar a quem restaurou, e
    ninguém perceberia, porque a tela continuaria dizendo "padrão".
    """
    from sqlmodel import Session

    prompts.salvar_prompt(TENANT, "classificacao", "customizado", autor_nome="Ana")
    prompts.restaurar_padrao(TENANT, "classificacao")

    with Session(banco) as s:
        assert s.query(PromptAgente).count() == 0, "restaurar deixou linha para trás"

    assert prompts.texto_em_vigor(TENANT, "classificacao") == PROMPT_PADRAO
    assert prompts.get_prompt(TENANT, "classificacao")["no_padrao"] is True


def test_chave_desconhecida_nao_cria_linha_orfa(banco):
    with pytest.raises(ValueError):
        prompts.salvar_prompt(TENANT, "inventada", "texto")


def test_o_prompt_padrao_mantem_as_defesas_estruturais():
    """Editar o texto não pode ser o caminho para remover a proteção.

    O `output_type` continua sendo a defesa principal contra injeção, e é de
    código. Mas os delimitadores de conteúdo não confiável vivem no TEXTO, e
    quem editar precisa vê-los ali: por isso eles entram como marcador, e não
    embutidos fora do alcance de quem calibra.
    """
    assert "{_ABRE}" in PROMPT_PADRAO
    assert "{_FECHA}" in PROMPT_PADRAO
    assert "{REGRA_SEM_TRAVESSAO}" in PROMPT_PADRAO
