"""O recorte do corpo: só a mensagem nova chega aos agentes.

Dois grupos de teste, e os dois importam pelo mesmo motivo. O primeiro prova que
o histórico citado é CORTADO, que é a correção em si. O segundo prova que ele é
cortado com medo: texto sem marcador nenhum passa inteiro, e corte que
esvaziaria o corpo é desfeito. Um recorte agressivo demais seria pior que o
defeito original, porque apagaria conteúdo real em vez de apenas somar ruído.

O terceiro grupo amarra o recorte aos dois agentes que o usam, pelo prompt: é o
que impede alguém reintroduzir `email["corpo_texto"]` cru no `_build_prompt` e
descobrir só na próxima rodada em produção.
"""

from datetime import datetime

from sales_support_agent.services.corpo_email import extrair_mensagem_nova

RECEBIDO = datetime(2026, 8, 21, 14, 32)


def _email(corpo: str, assunto: str = "RE: COTAÇÃO") -> dict:
    return {
        "corpo_texto": corpo,
        "assunto": assunto,
        "remetente_nome": "Cliente",
        "remetente_email": "cliente@empresa.com.br",
        "recebido_em": RECEBIDO,
    }


# ---------------------------------------------------------------------------
# O histórico é cortado
# ---------------------------------------------------------------------------


def test_corta_no_cabecalho_de_enviada_para_assunto():
    """O formato que o Exchange gera ao converter o HTML do Outlook em texto."""
    corpo = (
        "Bom dia! Obrigada, fico no aguardo.\n"
        "\n"
        "De: Comercial <comercial@coester.com.br>\n"
        "Enviada em: quinta-feira, 20 de agosto de 2026 09:12\n"
        "Para: Cliente <cliente@empresa.com.br>\n"
        "Assunto: COTAÇÃO - PRAZO: 19/08 - 17:00H\n"
        "\n"
        "Favor abrir PI conforme orçamento aprovado, prazo até sexta.\n"
    )
    assert extrair_mensagem_nova(corpo) == "Bom dia! Obrigada, fico no aguardo."


def test_corta_na_regua_de_sublinhados_do_outlook():
    corpo = (
        "Segue em anexo.\n"
        "\n"
        "________________________________\n"
        "De: Fulano\n"
        "Assunto: PEDIDO EQUALIZADO\n"
    )
    assert extrair_mensagem_nova(corpo) == "Segue em anexo."


def test_corta_no_separador_de_mensagem_original():
    corpo = (
        "O e-mail é spam, desconsiderar.\n"
        "\n"
        "-----Mensagem original-----\n"
        "De: portal@fornecedor.com\n"
        "Assunto: RFQ:YU78690\n"
        "Precisamos de cotação urgente para hoje.\n"
    )
    assert extrair_mensagem_nova(corpo) == "O e-mail é spam, desconsiderar."


def test_corta_no_separador_de_encaminhamento_do_gmail():
    corpo = (
        "Favor avaliar.\n"
        "\n"
        "---------- Forwarded message ---------\n"
        "De: Cliente <cliente@empresa.com.br>\n"
        "Assunto: Pedido de compra 4521\n"
    )
    assert extrair_mensagem_nova(corpo) == "Favor avaliar."


def test_corta_na_atribuicao_em_fulano_escreveu():
    corpo = (
        "Obrigado!\n"
        "\n"
        "Em 12/03/2026 10:00, Fulano <fulano@empresa.com.br> escreveu:\n"
        "Sobre o pedido 4521: precisamos adiar a entrega em duas semanas.\n"
    )
    assert extrair_mensagem_nova(corpo) == "Obrigado!"


def test_corta_na_atribuicao_em_ingles():
    corpo = (
        "Received, thanks.\n"
        "\n"
        "On Wed, Mar 12, 2026 at 10:00, John <john@x.com> wrote:\n"
        "Please issue the purchase order today.\n"
    )
    assert extrair_mensagem_nova(corpo) == "Received, thanks."


def test_corta_na_primeira_linha_citada_com_maior():
    corpo = "Ok, pode seguir.\n\n> Favor abrir pedido conforme anexo.\n> Prazo: hoje.\n"
    assert extrair_mensagem_nova(corpo) == "Ok, pode seguir."


def test_corta_na_primeira_ocorrencia_de_thread_com_varias_rodadas():
    """Thread longa: vale o marcador MAIS ALTO, não o último."""
    corpo = (
        "Não vamos cotar.\n"
        "\n"
        "De: Colega\n"
        "Assunto: RE: COTAÇÃO\n"
        "Abrir oportunidade para o cliente.\n"
        "\n"
        "De: Cliente\n"
        "Assunto: COTAÇÃO\n"
        "Poderiam enviar uma cotação?\n"
    )
    assert extrair_mensagem_nova(corpo) == "Não vamos cotar."


def test_o_gatilho_do_historico_nao_chega_ao_agente():
    """O defeito relatado, na forma em que ele aparece: gatilho só no histórico.

    O conteúdo novo é pura cortesia (Etapa 0.3 do prompt), e o vocabulário de
    abertura da Etapa 1 está no rodapé da thread. Com o corpo inteiro, o Agente
    1 lia "abrir PI" e classificava como pedido.
    """
    corpo = (
        "Boa tarde! Obrigada. Fico aguardando!\n"
        "\n"
        "De: Comercial\n"
        "Assunto: RES: orçamento\n"
        "\n"
        "Favor abrir PI conforme orçamento aprovado. PEDIDO EQUALIZADO.\n"
        "Prazo: 19/08 - 17:00H. URGENTE.\n"
    )
    nova = extrair_mensagem_nova(corpo)
    for gatilho in ("abrir PI", "EQUALIZADO", "Prazo", "URGENTE"):
        assert gatilho not in nova


# ---------------------------------------------------------------------------
# O recorte é conservador
# ---------------------------------------------------------------------------


def test_sem_marcador_o_texto_passa_inteiro():
    corpo = (
        "Prezados, segue nosso pedido de compra nº 4521 para 10 atuadores.\n"
        "Prazo de entrega até 15/04.\n"
        "\n"
        "Atenciosamente,\n"
        "Fulano de Tal\n"
        "Suprimentos\n"
    )
    assert extrair_mensagem_nova(corpo) == corpo.strip()


def test_encaminhamento_sem_comentario_devolve_o_texto_inteiro():
    """Corte que esvazia o corpo é desfeito: aqui o encaminhado É a mensagem."""
    corpo = (
        "De: Cliente <cliente@empresa.com.br>\n"
        "Assunto: Pedido de compra PO-26-038-05395\n"
        "\n"
        "Conforme solicitado, segue em anexo o Pedido de Compra.\n"
    )
    assert extrair_mensagem_nova(corpo) == corpo.strip()


def test_frase_que_comeca_com_de_nao_e_cabecalho():
    """`De:` sozinho não corta: sem cabeçalho vizinho, é texto do remetente."""
    corpo = "De: 10 peças, entregar 4 agora.\nO restante fica para outubro.\n"
    assert extrair_mensagem_nova(corpo) == corpo.strip()


def test_frase_terminada_em_escreveu_sem_data_nem_email_nao_corta():
    corpo = "Conforme o cliente escreveu:\nprecisamos revisar o desconto da proposta.\n"
    assert extrair_mensagem_nova(corpo) == corpo.strip()


def test_corpo_vazio_e_so_espaco():
    assert extrair_mensagem_nova("") == ""
    assert extrair_mensagem_nova("   \n\n  ") == ""


def test_mensagem_nova_curta_e_preservada_como_esta():
    """Cortesia curta é resposta CORRETA, e não motivo para desfazer o corte."""
    corpo = "Obrigado!\n\nDe: Comercial\nAssunto: RE: pedido\nSegue o pedido em anexo.\n"
    assert extrair_mensagem_nova(corpo) == "Obrigado!"


def test_cabecalho_em_ingles_tambem_corta():
    corpo = "Thanks!\n\nFrom: Sales\nSent: Monday\nSubject: Quote\nPlease quote.\n"
    assert extrair_mensagem_nova(corpo) == "Thanks!"


def test_acento_e_caixa_nao_importam_no_marcador():
    corpo = "Segue.\n\nDE: FULANO\nENVIADA EM: ontem\nASSUNTO: PEDIDO\nAbrir pedido.\n"
    assert extrair_mensagem_nova(corpo) == "Segue."


# ---------------------------------------------------------------------------
# Os agentes usam o recorte
# ---------------------------------------------------------------------------

CORPO_COM_HISTORICO = (
    "Boa tarde! Obrigada.\n"
    "\n"
    "De: Comercial\n"
    "Assunto: RES: orçamento\n"
    "\n"
    "Favor abrir PI conforme orçamento aprovado.\n"
)


def test_prompt_do_classificador_nao_leva_o_historico():
    from sales_support_agent.services.classificacao_agent import _build_prompt

    prompt = _build_prompt(_email(CORPO_COM_HISTORICO))
    assert "Boa tarde! Obrigada." in prompt
    assert "abrir PI" not in prompt
    # O assunto continua indo inteiro: é ele que sustenta a regra 7 do prompt.
    assert "RE: COTAÇÃO" in prompt


def test_prompt_do_resumidor_nao_leva_o_historico():
    from sales_support_agent.services.resumo_agent import _build_prompt

    prompt = _build_prompt(_email(CORPO_COM_HISTORICO), "proposta")
    assert "Boa tarde! Obrigada." in prompt
    assert "abrir PI" not in prompt


def test_prompt_com_corpo_todo_recortado_avisa_corpo_vazio():
    """Sem corpo nenhum o marcador `(corpo vazio)` precisa continuar aparecendo."""
    from sales_support_agent.services.classificacao_agent import _build_prompt

    assert "(corpo vazio)" in _build_prompt(_email(""))
