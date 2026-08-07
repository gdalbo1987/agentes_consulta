"""As regras puras: as quatro classes e a aritmética de urgência.

Sem I/O, sem rede, sem banco. É aqui que se prova a decisão central do Agente 1:
o modelo estima, o Python decide. A urgência não é um booleano que o modelo
devolve, é uma conta feita sobre o prazo que ele estimou.
"""

import pytest

from sales_support_agent.services import classificacao_rules as regras


def test_sao_exatamente_quatro_classes():
    assert regras.CLASSES == ("pedido", "proposta", "revisao_pedido", "revisao_proposta")


def test_nenhuma_e_valor_do_enum_e_nao_ausencia_de_resposta():
    """Obrigar o modelo a dizer "não se encaixa" evita o erro mais caro.

    Se "fora das classes" fosse a ausência de resposta, uma falha de formato
    seria lida como "ignorar este e-mail", e ele sumiria em silêncio.
    """
    assert regras.CLASSE_NENHUMA in regras.CLASSES_COM_NENHUMA
    assert regras.CLASSE_NENHUMA not in regras.CLASSES
    assert len(regras.CLASSES_COM_NENHUMA) == 5


def test_identificadores_sao_sem_acento_e_o_rotulo_e_acentuado():
    """O banco e o enum usam ascii; a tela usa português de verdade."""
    for classe in regras.CLASSES:
        assert classe.isascii() and " " not in classe
    assert regras.rotulo("revisao_pedido") == "Revisão de pedido"


def test_classe_desconhecida_vira_traco_simples():
    """Valor ausente em tabela se marca com `-`, nunca com travessão."""
    assert regras.rotulo("") == "-"
    assert regras.rotulo("inexistente") == "-"
    assert "—" not in regras.rotulo("")


# ---------------------------------------------------------------------------
# Urgência
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prazo,semantico,janela,esperado",
    [
        (6, False, 24, True),      # prazo dentro da janela
        (24, False, 24, True),     # exatamente na janela: conta
        (72, False, 24, False),    # fora da janela
        (None, False, 24, False),  # sem prazo e sem sinal semântico
        (None, True, 24, True),    # "preciso disso hoje" sem número
        (999, True, 24, True),     # o sinal semântico vence o prazo longo
        (0, False, 24, True),      # vence agora
        (-5, False, 24, True),     # já venceu
    ],
)
def test_calculo_de_urgencia(prazo, semantico, janela, esperado):
    assert regras.calcular_urgencia(prazo, semantico, janela) is esperado


def test_mudar_a_janela_re_marca_sem_chamar_o_modelo():
    """A razão de guardar o prazo em horas separado do booleano.

    Com o prazo na linha, apertar a janela de 24h para 8h é um UPDATE. Se o
    modelo devolvesse só o booleano, a mesma mudança exigiria reprocessar a
    caixa inteira e pagar tudo de novo.
    """
    prazo_do_email = 12  # já estimado e gravado numa rodada anterior

    assert regras.calcular_urgencia(prazo_do_email, False, janela_horas=24) is True
    assert regras.calcular_urgencia(prazo_do_email, False, janela_horas=8) is False


# ---------------------------------------------------------------------------
# Categorias do Outlook
# ---------------------------------------------------------------------------


def test_email_fora_das_classes_nao_recebe_categoria_nenhuma():
    """Requisito do produto: ele fica exatamente como estava, na pasta original."""
    assert regras.categorias_para("nenhuma", urgente=False) == []
    assert regras.categorias_para("nenhuma", urgente=True) == []
    assert regras.categorias_para("", urgente=True) == []


def test_categoria_da_classe_e_o_mesmo_texto_que_o_dashboard_mostra():
    """Quem abre o Outlook tem de ver a mesma palavra que vê na plataforma."""
    assert regras.categorias_para("pedido", urgente=False) == ["Pedido"]
    assert regras.CATEGORIAS["revisao_proposta"] == regras.rotulo("revisao_proposta")


def test_urgente_entra_como_categoria_adicional_e_nao_substitui_a_classe():
    assert regras.categorias_para("proposta", urgente=True) == ["Proposta", "Urgente"]


def test_confianca_e_normalizada_para_0_a_100():
    """O modelo às vezes devolve 0-1, às vezes passa de 100, às vezes texto."""
    assert regras.clamp_confianca(87) == 87
    assert regras.clamp_confianca(150) == 100
    assert regras.clamp_confianca(-3) == 0
    assert regras.clamp_confianca(None) == 0
    assert regras.clamp_confianca("alta") == 0


def test_toda_classe_tem_rotulo_categoria_e_pasta_sugerida():
    """Nenhuma classe pode entrar em CLASSES sem os três mapas acompanharem."""
    for classe in regras.CLASSES:
        assert classe in regras.LABELS
        assert classe in regras.CATEGORIAS
        assert classe in regras.PASTAS_PADRAO


def test_nenhum_travessao_nos_textos_visiveis():
    for texto in list(regras.LABELS.values()) + list(regras.PASTAS_PADRAO.values()):
        assert "—" not in texto and "–" not in texto
