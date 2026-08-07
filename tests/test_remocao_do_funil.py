"""O funil de prospecção saiu, e não pode voltar por descuido.

Produtos, Pesquisa, Enriquecimento, Priorização e Lista de Leads eram o produto
anterior. Cada um deixava rastro em quatro lugares: página, rota, State e
serviço. Um import esquecido em qualquer um deles não quebra a compilação (o
Reflex só reclama do que ele de fato renderiza), mas deixa código morto que o
próximo leitor vai tentar entender.

O teste que realmente pega tudo é o de imports pendentes, no fim: se algum
módulo removido ainda for importado, o `import` do pacote inteiro estoura.
"""

import importlib
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
PACOTE = RAIZ / "sales_support_agent"

MODULOS_REMOVIDOS = [
    "sales_support_agent.pages.products",
    "sales_support_agent.pages.search_config",
    "sales_support_agent.pages.enrichment",
    "sales_support_agent.pages.priorizacao",
    "sales_support_agent.pages.leads",
    "sales_support_agent.services.prospect_agent",
    "sales_support_agent.services.search_scope",
    "sales_support_agent.services.enrichment",
    "sales_support_agent.services.enrichment_rules",
    "sales_support_agent.services.enrichment_report",
    "sales_support_agent.services.kipflow_client",
    "sales_support_agent.services.receita_client",
    "sales_support_agent.services.hunter_client",
    "sales_support_agent.services.priorizacao",
    "sales_support_agent.services.priorizacao_agent",
    "sales_support_agent.services.priorizacao_rules",
    "sales_support_agent.services.priorizacao_report",
    "sales_support_agent.services.approach_agent",
    "sales_support_agent.services.product_agent",
    "sales_support_agent.services.normalizers",
    "sales_support_agent.services.dashboard_insights",
    "sales_support_agent.services.insights_agent",
    "sales_support_agent.components.brazil_map",
    "sales_support_agent.styles.brazil_geo",
]

STATES_REMOVIDOS = [
    "ProductState",
    "SearchState",
    "EnrichmentState",
    "PriorizacaoState",
    "LeadsState",
    "InsightsState",
]

ROTAS_REMOVIDAS = ["/produtos", "/pesquisa", "/enriquecimento", "/priorizacao", "/leads", "/insights-ia"]


@pytest.mark.parametrize("modulo", MODULOS_REMOVIDOS)
def test_modulo_do_funil_nao_existe_mais(modulo):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(modulo)


@pytest.mark.parametrize("nome", STATES_REMOVIDOS)
def test_state_do_funil_nao_existe_mais(nome):
    import sales_support_agent.state as state

    assert not hasattr(state, nome)


@pytest.mark.parametrize("rota", ROTAS_REMOVIDAS)
def test_rota_do_funil_nao_e_declarada(rota):
    """Nenhum `@rx.page` nem `add_page` pode registrar as rotas antigas."""
    for arquivo in list((PACOTE / "pages").glob("*.py")) + [PACOTE / "sales_support_agent.py"]:
        texto = arquivo.read_text(encoding="utf-8")
        assert f'route="{rota}"' not in texto, f"{arquivo.name} ainda registra {rota}"


def test_menu_lateral_tem_apenas_as_paginas_que_restaram():
    """A barra lateral não pode ter link para rota que não existe.

    Link morto na navegação é o sintoma mais visível de uma remoção incompleta,
    e o único que o usuário encontra antes do desenvolvedor.
    """
    fonte = (PACOTE / "components" / "dashboard_layout.py").read_text(encoding="utf-8")

    import re

    rotas = set(re.findall(r'sidebar_item\([^)]*?"(/[^"]*)"', fonte, re.S))
    assert rotas == {"/dashboard", "/consulta", "/profile", "/admin"}, f"menu inesperado: {rotas}"


def test_o_pacote_inteiro_importa():
    """A rede de segurança de verdade: qualquer import pendente estoura aqui."""
    importlib.import_module("sales_support_agent.sales_support_agent")


def test_o_agente_de_consulta_preservou_o_molde_que_sera_reaproveitado():
    """O chat de Insights virou a base do Agente 3, e não pode ser esvaziado.

    A memória de conversa sobre `ChatMessage`, o par de guardrails e o
    fake-streaming (que só libera o texto depois do guardrail de saída passar)
    são exatamente o que a Fase 10 precisa. Se alguém apagar isso "porque o
    agente vai ser reescrito", a reescrita perde o que havia de mais caro.
    """
    fonte = (PACOTE / "services" / "consulta_agent.py").read_text(encoding="utf-8")

    for peca in ("DBChatSession", "_dividir_em_pedacos", "stream_resposta", "guardrail"):
        assert peca in fonte, f"o molde do agente de consulta perdeu: {peca}"


def test_cota_do_funil_antigo_esta_marcada_como_orfa():
    """`CONSULTA_LIMIT_MENSAL` sobreviveu, mas não pode ser aplicada às cegas.

    Ela era 20 por usuário por ETAPA DO FUNIL. Numa classificação que roda duas
    vezes por dia, aplicar o mesmo número estouraria a cota no dia 10. Enquanto
    a decisão de produto não vier, a constante fica sem uso e com o aviso
    explícito, e este teste garante que ninguém a religue sem tocar no aviso.
    """
    fonte = (PACOTE / "state.py").read_text(encoding="utf-8")

    assert "CONSULTA_LIMIT_MENSAL" in fonte
    assert "órfã" in fonte or "ATENÇÃO" in fonte, "sumiu o aviso sobre a cota estar órfã"
    assert fonte.count("limite_consultas(") == 1, (
        "`limite_consultas` voltou a ser chamada. Antes de aplicar a cota, "
        "defina a QUÊ ela se aplica no produto novo."
    )
