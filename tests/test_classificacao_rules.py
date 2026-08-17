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
        # (urgente, importante). As duas faixas são mutuamente exclusivas.
        (6, False, 24, (True, False)),      # data dentro da janela
        (24, False, 24, (True, False)),     # exatamente na janela: conta
        (72, False, 24, (False, True)),     # data além da janela: IMPORTANTE
        (999, False, 24, (False, True)),    # data muito distante ainda é compromisso
        (None, False, 24, (False, False)),  # sem data e sem sinal: nada
        (None, True, 24, (True, False)),    # "preciso disso hoje" sem número
        (0, False, 24, (True, False)),      # vence agora
        (-5, False, 24, (True, False)),     # já venceu
    ],
)
def test_calculo_de_prioridade(prazo, semantico, janela, esperado):
    assert regras.calcular_prioridade(prazo, semantico, janela) == esperado


def test_ter_data_distante_nao_e_o_mesmo_que_nao_ter_data():
    """A razão de existir a faixa "importante".

    Antes as duas caíam no mesmo balde, e um pedido com entrega marcada para
    daqui a duas semanas ficava indistinguível de um e-mail sem compromisso
    nenhum de data.
    """
    com_data_distante = regras.calcular_prioridade(336, False, 24)
    sem_data = regras.calcular_prioridade(None, False, 24)

    assert com_data_distante == (False, True)
    assert sem_data == (False, False)
    assert com_data_distante != sem_data


def test_o_prazo_vence_o_sinal_semantico_quando_ha_data():
    """Com data declarada, quem decide é a conta, não o adjetivo do remetente.

    Um e-mail que diz "urgente" mas marca entrega para daqui a 40 dias é
    importante, não urgente: é isso que impede a lista de urgências de encher
    de coisa que não é para agora.
    """
    assert regras.calcular_prioridade(960, True, 24) == (False, True)


def test_mudar_a_janela_re_marca_sem_chamar_o_modelo():
    """A razão de guardar o prazo em horas separado dos booleanos.

    Com o prazo na linha, apertar a janela de 24h para 8h é um UPDATE. Se o
    modelo devolvesse só o booleano, a mesma mudança exigiria reprocessar a
    caixa inteira e pagar tudo de novo.

    Estreitar a janela NÃO apaga a prioridade: o e-mail migra de urgente para
    importante, porque o compromisso continua existindo.
    """
    prazo_do_email = 12  # já estimado e gravado numa rodada anterior

    assert regras.calcular_prioridade(prazo_do_email, False, 24) == (True, False)
    assert regras.calcular_prioridade(prazo_do_email, False, 8) == (False, True)


# ---------------------------------------------------------------------------
# Categorias do Outlook
# ---------------------------------------------------------------------------


def test_email_fora_das_classes_nao_recebe_categoria_nenhuma():
    """Requisito do produto: ele fica exatamente como estava, na pasta original.

    Nem a marca de procedência entra aqui: e-mail não classificado não foi
    tocado pela plataforma, e dizer que foi seria mentira no Outlook.
    """
    assert regras.categorias_para("nenhuma", urgente=False) == []
    assert regras.categorias_para("nenhuma", urgente=True, importante=True) == []
    assert regras.categorias_para("", urgente=True) == []


def test_categoria_da_classe_e_o_mesmo_texto_que_o_dashboard_mostra():
    """Quem abre o Outlook tem de ver a mesma palavra que vê na plataforma."""
    assert regras.categorias_para("pedido", urgente=False) == ["Pedido", "IA"]
    assert regras.CATEGORIAS["revisao_proposta"] == regras.rotulo("revisao_proposta")


def test_todo_email_classificado_leva_a_marca_de_procedencia():
    """A marca de IA não é adorno da urgência: vale para qualquer classificado.

    É ela que permite separar no Outlook o que o agente arquivou do que uma
    pessoa arquivou à mão, e achar tudo o que ele tocou numa execução mal
    calibrada.
    """
    for classe in regras.CLASSES:
        assert regras.CATEGORIA_IA in regras.categorias_para(classe, urgente=False)
        assert regras.CATEGORIA_IA in regras.categorias_para(classe, urgente=True)
        assert regras.CATEGORIA_IA in regras.categorias_para(
            classe, urgente=False, importante=True
        )


def test_urgente_entra_como_categoria_adicional_e_nao_substitui_a_classe():
    assert regras.categorias_para("proposta", urgente=True) == [
        "Proposta", "Urgente", "IA",
    ]


def test_importante_entra_no_lugar_de_urgente_e_nunca_junto():
    """As duas faixas são exclusivas: duas etiquetas de prioridade confundiriam."""
    saida = regras.categorias_para("pedido", urgente=False, importante=True)

    assert saida == ["Pedido", "Importante", "IA"]
    assert regras.CATEGORIA_URGENTE not in saida

    urgente = regras.categorias_para("pedido", urgente=True, importante=True)
    assert regras.CATEGORIA_IMPORTANTE not in urgente


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
