"""Dashboard do usuário padrão: a tela operacional do Agente 1.

Tudo aqui é montado com os componentes que já existiam: `admin_card` para os
indicadores, `table_shell`/`col` para a tabela, e o mesmo padrão de
`rx.dialog.root` que as páginas do funil anterior usavam para o detalhe de uma
linha. A conversão não mexeu no sistema de design, só no que ele mostra.
"""

import reflex as rx

from sales_support_agent.components.confirm_dialog import confirm_dialog
from sales_support_agent.components.dashboard_layout import dashboard_layout
from sales_support_agent.components.data_table import col, table_shell
from sales_support_agent.pages.admin_dashboard import admin_card
from sales_support_agent.state import AppState, DashboardState
from sales_support_agent.styles import colors
from sales_support_agent.styles.typography import BODY_FONT, HEADING_FONT


def _cartao(titulo: str, *filhos) -> rx.Component:
    return rx.box(
        rx.text(
            titulo, size="4", font_weight="700", color=colors.TEXT_MAIN,
            font_family=HEADING_FONT, margin_bottom="1rem",
        ),
        *filhos,
        background=colors.CARD_BG,
        padding="1.5rem",
        border_radius="16px",
        border=f"1px solid {colors.BORDER}",
        width="100%",
    )


def _rotulo(texto: str) -> rx.Component:
    return rx.text(
        texto, size="2", font_weight="600", color=colors.TEXT_SEC,
        font_family=BODY_FONT, margin_bottom="0.25rem",
    )


def _metricas() -> rx.Component:
    return rx.grid(
        admin_card(
            "Duração média das execuções", DashboardState.duracao_media, "timer",
            subtitle="média das execuções concluídas",
        ),
        admin_card(
            "Duração da última", DashboardState.duracao_ultima, "history",
            subtitle=DashboardState.ultima_rodada_quando,
        ),
        admin_card(
            "E-mails classificados", DashboardState.total_classificados, "mails",
            subtitle="total acumulado",
        ),
        admin_card(
            "Na última execução", DashboardState.ultima_rodada_classificados, "mail-check",
            subtitle=DashboardState.ultima_rodada_origem,
        ),
        columns=rx.breakpoints(initial="1", sm="2", lg="4"),
        spacing="4", width="100%", margin_bottom="0.75rem",
    )


def _zerar() -> rx.Component:
    """Discreto de propósito: apaga o histórico de operação inteiro."""
    return rx.hstack(
        rx.spacer(),
        rx.button(
            rx.icon(tag="eraser", size=14),
            "Zerar contadores",
            on_click=DashboardState.set_confirm_zerar_open(True),
            variant="ghost",
            color=colors.TEXT_SEC,
            size="1",
            cursor="pointer",
        ),
        width="100%", margin_bottom="1.5rem",
    )


def _selo_agendamento() -> rx.Component:
    """Diz, em uma olhada, se o agente está no ar ou parado."""
    return rx.cond(
        DashboardState.agendamento_ativo,
        rx.hstack(
            rx.icon(tag="circle-check", size=14, color="#15803d"),
            rx.text(
                "Em execução automática", size="1", font_weight="700", color="#15803d",
                font_family=BODY_FONT,
            ),
            spacing="1", align_items="center",
            background="#dcfce7", padding="0.15rem 0.6rem", border_radius="999px",
        ),
        rx.hstack(
            rx.icon(tag="circle-pause", size=14, color="#b45309"),
            rx.text(
                "Automático parado", size="1", font_weight="700", color="#b45309",
                font_family=BODY_FONT,
            ),
            spacing="1", align_items="center",
            background="#fef3c7", padding="0.15rem 0.6rem", border_radius="999px",
        ),
    )


def _acao() -> rx.Component:
    return rx.hstack(
        rx.vstack(
            rx.hstack(
                rx.text(
                    "Próxima execução automática", size="2", color=colors.TEXT_SEC,
                    font_family=BODY_FONT,
                ),
                _selo_agendamento(),
                spacing="2", align_items="center",
            ),
            rx.text(
                rx.cond(
                    DashboardState.agendamento_ativo,
                    DashboardState.proxima_execucao,
                    "Parado",
                ),
                size="4", font_weight="700",
                color=colors.TEXT_MAIN, font_family=HEADING_FONT,
            ),
            spacing="0", align_items="start",
        ),
        rx.spacer(),
        # Iniciar e Parar controlam SÓ o automático. "Classificar agora" fica
        # sempre disponível, inclusive com o automático parado: é assim que se
        # confere a configuração antes de soltar o agente na caixa.
        rx.cond(
            DashboardState.agendamento_ativo,
            rx.button(
                rx.icon(tag="square", size=16),
                "Parar automático",
                on_click=DashboardState.parar_agendamento,
                background="transparent", color="#b45309", cursor="pointer",
                border="1px solid #f59e0b",
            ),
            rx.button(
                rx.icon(tag="power", size=16),
                "Iniciar automático",
                on_click=DashboardState.iniciar_agendamento,
                disabled=DashboardState.pastas_pendentes,
                background="transparent", color="#15803d", cursor="pointer",
                border="1px solid #22c55e",
            ),
        ),
        rx.button(
            rx.icon(tag="play", size=16),
            "Classificar agora",
            on_click=DashboardState.classificar_agora,
            loading=DashboardState.is_running,
            disabled=DashboardState.is_running | DashboardState.pastas_pendentes,
            background=colors.BTN_GRADIENT, color="white", cursor="pointer",
        ),
        width="100%", align_items="center", spacing="3", margin_bottom="1.5rem",
        wrap="wrap",
    )


def _progresso() -> rx.Component:
    """Barra de progresso e aviso de conclusão.

    Serve as duas origens: o botão manual alimenta o State pelo próprio stream,
    e a execução automática chega aqui pela sondagem do banco
    (`DashboardState.monitorar_execucao`). Da tela, as duas são iguais.
    """
    return rx.cond(
        DashboardState.is_running,
        rx.box(
            rx.hstack(
                rx.spinner(size="2"),
                rx.text(
                    DashboardState.execucao_rotulo, size="2", font_weight="700",
                    color=colors.TEXT_MAIN, font_family=BODY_FONT,
                ),
                rx.spacer(),
                rx.text(
                    DashboardState.progresso_contagem, size="2", color=colors.TEXT_SEC,
                    font_family=BODY_FONT,
                ),
                width="100%", align_items="center", spacing="2",
            ),
            rx.progress(
                value=DashboardState.progresso_percentual, max=100,
                width="100%", margin_top="0.6rem", margin_bottom="0.4rem",
            ),
            rx.text(
                DashboardState.progresso_texto, size="1", color=colors.TEXT_SEC,
                font_family=BODY_FONT,
            ),
            width="100%", padding="1rem", border_radius="12px",
            background=colors.CARD_BG, border=f"1px solid {colors.BORDER}",
            margin_bottom="1.5rem",
        ),
        rx.cond(
            DashboardState.conclusao_texto != "",
            rx.callout(
                DashboardState.conclusao_texto,
                icon=rx.cond(DashboardState.conclusao_erro, "triangle-alert", "circle-check"),
                color_scheme=rx.cond(DashboardState.conclusao_erro, "red", "green"),
                width="100%", margin_bottom="1.5rem",
            ),
        ),
    )


def _configuracao() -> rx.Component:
    return _cartao(
        "Execuções automáticas e urgência",
        rx.grid(
            rx.vstack(
                _rotulo("Primeiro horário"),
                rx.input(
                    type="time", value=DashboardState.horario_1,
                    on_change=DashboardState.set_horario_1, width="100%",
                ),
                spacing="0", align_items="start",
            ),
            rx.vstack(
                _rotulo("Segundo horário"),
                rx.input(
                    type="time", value=DashboardState.horario_2,
                    on_change=DashboardState.set_horario_2, width="100%",
                ),
                spacing="0", align_items="start",
            ),
            rx.vstack(
                _rotulo("Janela de urgência (horas)"),
                rx.input(
                    type="number", value=DashboardState.janela_urgencia_horas,
                    on_change=DashboardState.set_janela_urgencia_horas, width="100%",
                ),
                spacing="0", align_items="start",
            ),
            rx.vstack(
                _rotulo("Varrer as últimas (horas)"),
                rx.input(
                    type="number", value=DashboardState.lookback_horas,
                    on_change=DashboardState.set_lookback_horas, width="100%",
                ),
                spacing="0", align_items="start",
            ),
            columns=rx.breakpoints(initial="1", sm="2", lg="4"),
            spacing="4", width="100%",
        ),
        rx.text(
            "Um e-mail é marcado como urgente quando pede entrega ou resposta "
            "dentro da janela acima. Mudar a janela revê a marcação dos e-mails "
            "já classificados, sem reprocessá-los.",
            size="1", color=colors.TEXT_SEC, font_family=BODY_FONT, margin_top="0.75rem",
        ),
        rx.text(
            "Esta configuração é da organização: ela vale para todos e o que "
            "um usuário salvar os demais passam a ver.",
            size="1", color=colors.TEXT_SEC, font_family=BODY_FONT,
            margin_top="0.35rem",
        ),
        rx.button(
            "Salvar configuração",
            on_click=DashboardState.salvar_configuracao,
            background=colors.BTN_GRADIENT, color="white", cursor="pointer",
            margin_top="1rem",
        ),
        # Só aparece depois que alguém salvou. Numa instalação que vem de antes
        # do registro de autoria não há o que mostrar, e uma linha vazia dizendo
        # "alterado por -" seria pior que silêncio.
        rx.cond(
            DashboardState.config_autoria != "",
            rx.hstack(
                rx.icon(tag="user-round", size=13, color=colors.TEXT_SEC),
                rx.text(
                    DashboardState.config_autoria, size="1", color=colors.TEXT_SEC,
                    font_family=BODY_FONT,
                ),
                spacing="2", align_items="center", margin_top="0.75rem",
            ),
        ),
    )


def _linha_pasta(pasta) -> rx.Component:
    return rx.hstack(
        rx.text(
            pasta.classe_label, size="2", font_weight="600", color=colors.TEXT_MAIN,
            font_family=BODY_FONT, width="180px", flex_shrink="0",
        ),
        rx.input(
            placeholder="Nome da pasta no Outlook",
            value=DashboardState.pasta_inputs[pasta.classe],
            on_change=lambda v: DashboardState.set_pasta_input(pasta.classe, v),
            width="100%",
        ),
        rx.cond(
            pasta.resolvido,
            rx.badge(pasta.pasta_caminho, color_scheme="green", size="2"),
            rx.badge("não vinculada", color_scheme="red", size="2"),
        ),
        rx.button(
            "Vincular",
            on_click=lambda: DashboardState.salvar_pasta(pasta.classe),
            variant="soft", cursor="pointer", size="2",
        ),
        width="100%", spacing="3", align_items="center",
    )


def _pastas() -> rx.Component:
    return _cartao(
        "Pastas do Outlook por classe",
        rx.cond(
            DashboardState.pastas_pendentes,
            rx.callout(
                "Enquanto houver classe sem pasta vinculada, a classificação não "
                "roda.",
                icon="triangle-alert", color_scheme="amber",
                width="100%", margin_bottom="1rem",
            ),
        ),
        rx.vstack(
            rx.foreach(DashboardState.pastas, _linha_pasta),
            spacing="3", width="100%",
        ),
        rx.hstack(
            rx.button(
                rx.icon(tag="folder-search", size=16),
                "Listar pastas da caixa",
                on_click=DashboardState.listar_pastas_do_outlook,
                loading=DashboardState.listando_pastas,
                variant="soft", cursor="pointer", size="2",
            ),
            rx.text(
                "Informe o nome da pasta e o sistema descobre o identificador dela.",
                size="1", color=colors.TEXT_SEC, font_family=BODY_FONT,
            ),
            spacing="3", align_items="center", margin_top="1rem",
        ),
        rx.cond(
            DashboardState.pastas_disponiveis.length() > 0,
            rx.box(
                rx.text(
                    "Pastas encontradas:", size="1", font_weight="600",
                    color=colors.TEXT_SEC, font_family=BODY_FONT, margin_bottom="0.25rem",
                ),
                rx.hstack(
                    rx.foreach(
                        DashboardState.pastas_disponiveis,
                        lambda caminho: rx.badge(caminho, color_scheme="gray", size="1"),
                    ),
                    spacing="1", wrap="wrap",
                ),
                margin_top="0.75rem",
            ),
        ),
    )


def _urgencias() -> rx.Component:
    return _cartao(
        "Urgências",
        rx.cond(
            DashboardState.sem_urgentes,
            rx.text(
                "Nenhum e-mail urgente no momento.", size="2",
                color=colors.TEXT_SEC, font_family=BODY_FONT,
            ),
            rx.vstack(
                rx.foreach(
                    DashboardState.urgentes,
                    lambda email: rx.box(
                        rx.hstack(
                            rx.badge("Urgente", color_scheme="red", size="1"),
                            rx.text(
                                email.assunto, size="2", font_weight="600",
                                color=colors.TEXT_MAIN, font_family=BODY_FONT,
                            ),
                            rx.spacer(),
                            rx.text(
                                email.recebido_em, size="1", color=colors.TEXT_SEC,
                                font_family=BODY_FONT,
                            ),
                            # Tira da FILA, não do banco. `stop_propagation`
                            # porque o cartão inteiro abre o detalhe.
                            rx.tooltip(
                                rx.button(
                                    rx.icon(tag="check", size=14),
                                    on_click=DashboardState.remover_da_urgencia(
                                        email.id
                                    ).stop_propagation,
                                    variant="soft", color_scheme="green",
                                    size="1", cursor="pointer",
                                ),
                                content="Remover das urgências",
                            ),
                            width="100%", align_items="center", spacing="2",
                        ),
                        rx.text(
                            email.resumo, size="2", color=colors.TEXT_SEC,
                            font_family=BODY_FONT, margin_top="0.25rem",
                        ),
                        on_click=lambda: DashboardState.abrir_detalhe(email.id),
                        cursor="pointer", width="100%",
                        padding="0.75rem", border_radius="10px",
                        _hover={"background": "#eef4fb"},
                    ),
                ),
                spacing="2", width="100%",
            ),
        ),
    )


def _linha_email(email) -> rx.Component:
    return rx.table.row(
        rx.table.cell(
            rx.text(
                email.assunto, size="2", color=colors.TEXT_MAIN, font_family=BODY_FONT,
            )
        ),
        rx.table.cell(rx.text(email.cliente, size="2", font_family=BODY_FONT)),
        rx.table.cell(rx.text(email.recebido_em, size="2", font_family=BODY_FONT)),
        rx.table.cell(rx.badge(email.classe_label, color_scheme="blue", size="1")),
        rx.table.cell(
            rx.cond(
                email.urgente,
                rx.badge("Urgente", color_scheme="red", size="1"),
                rx.cond(
                    email.importante,
                    rx.badge("Importante", color_scheme="amber", size="1"),
                    rx.text("-", size="2", color=colors.TEXT_SEC),
                ),
            )
        ),
        rx.table.cell(
            rx.tooltip(
                rx.button(
                    rx.icon(tag="trash-2", size=14),
                    # `stop_propagation` porque a linha inteira abre o detalhe:
                    # sem isso, clicar em apagar abriria o diálogo de detalhe
                    # por cima do de confirmação.
                    on_click=DashboardState.pedir_exclusao(
                        email.id, email.assunto
                    ).stop_propagation,
                    variant="ghost", color_scheme="red", size="1", cursor="pointer",
                ),
                content="Excluir do banco",
            ),
            width="48px",
        ),
        on_click=lambda: DashboardState.abrir_detalhe(email.id),
        cursor="pointer",
        _hover={"background": "#eef4fb"},
    )


def _tabela() -> rx.Component:
    return _cartao(
        "E-mails classificados",
        rx.hstack(
            rx.vstack(
                _rotulo("De"),
                rx.input(
                    type="date", value=DashboardState.filtro_data_inicio,
                    on_change=DashboardState.set_filtro_data_inicio,
                ),
                spacing="0", align_items="start",
            ),
            rx.vstack(
                _rotulo("Até"),
                rx.input(
                    type="date", value=DashboardState.filtro_data_fim,
                    on_change=DashboardState.set_filtro_data_fim,
                ),
                spacing="0", align_items="start",
            ),
            rx.vstack(
                _rotulo("Apenas urgentes"),
                rx.switch(
                    checked=DashboardState.filtro_apenas_urgentes,
                    on_change=DashboardState.set_filtro_apenas_urgentes,
                ),
                spacing="0", align_items="start",
            ),
            rx.vstack(
                _rotulo("Apenas importantes"),
                rx.switch(
                    checked=DashboardState.filtro_apenas_importantes,
                    on_change=DashboardState.set_filtro_apenas_importantes,
                ),
                spacing="0", align_items="start",
            ),
            rx.spacer(),
            rx.button(
                rx.icon(tag="refresh-cw", size=14),
                "Atualizar",
                on_click=DashboardState.atualizar_lista,
                variant="soft", cursor="pointer", size="2",
            ),
            rx.button(
                "Limpar filtros", on_click=DashboardState.limpar_filtros,
                variant="soft", color_scheme="gray", cursor="pointer", size="2",
            ),
            width="100%", spacing="4", align_items="end", margin_bottom="1rem",
        ),
        rx.cond(
            DashboardState.sem_emails,
            rx.text(
                "Nenhum e-mail classificado no período.", size="2",
                color=colors.TEXT_SEC, font_family=BODY_FONT,
            ),
            table_shell(
                [col("Título"), col("Cliente"), col("Recebido em"), col("Classe"),
                 col("Prioridade"), col("")],
                rx.foreach(DashboardState.emails, _linha_email),
            ),
        ),
    )


def _detalhe() -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title(DashboardState.detalhe_assunto),
            rx.hstack(
                rx.badge(DashboardState.detalhe_classe, color_scheme="blue"),
                rx.cond(
                    DashboardState.detalhe_urgente,
                    rx.badge("Urgente", color_scheme="red"),
                ),
                rx.text(
                    DashboardState.detalhe_recebido, size="1", color=colors.TEXT_SEC,
                    font_family=BODY_FONT,
                ),
                spacing="2", align_items="center", margin_bottom="0.75rem",
            ),
            rx.text(
                DashboardState.detalhe_cliente, size="2", font_weight="600",
                color=colors.TEXT_MAIN, font_family=BODY_FONT, margin_bottom="1rem",
            ),
            rx.cond(
                DashboardState.detalhe_disponivel,
                rx.vstack(
                    rx.text(
                        DashboardState.detalhe_resumo, size="2", color=colors.TEXT_MAIN,
                        font_family=BODY_FONT,
                    ),
                    rx.cond(
                        DashboardState.detalhe_pontos.length() > 0,
                        rx.vstack(
                            _rotulo("Pontos principais"),
                            rx.foreach(
                                DashboardState.detalhe_pontos,
                                lambda p: rx.text(
                                    f"- {p}", size="2", color=colors.TEXT_SEC,
                                    font_family=BODY_FONT,
                                ),
                            ),
                            spacing="1", align_items="start", width="100%",
                        ),
                    ),
                    rx.box(
                        _rotulo("Próximo passo"),
                        rx.text(
                            DashboardState.detalhe_acao, size="2", color=colors.TEXT_MAIN,
                            font_family=BODY_FONT,
                        ),
                        width="100%",
                    ),
                    rx.box(
                        _rotulo("Prazo mencionado"),
                        rx.text(
                            DashboardState.detalhe_prazo, size="2", color=colors.TEXT_MAIN,
                            font_family=BODY_FONT,
                        ),
                        width="100%",
                    ),
                    spacing="3", align_items="start", width="100%",
                ),
                # Resumo indisponível é dito, e não deixado em branco: espaço
                # vazio parece defeito de tela, não ausência de dado.
                rx.callout(
                    "O resumo deste e-mail ainda não está disponível.",
                    icon="info", color_scheme="gray", width="100%",
                ),
            ),
            rx.hstack(
                rx.cond(
                    DashboardState.detalhe_link != "",
                    rx.link(
                        rx.button(
                            rx.icon(tag="external-link", size=14),
                            "Abrir no Outlook", variant="soft", cursor="pointer", size="2",
                        ),
                        href=DashboardState.detalhe_link, is_external=True,
                    ),
                ),
                # Desfazer o "já tratei". Tirar da fila é um clique só, sem
                # confirmação, então precisa ter volta.
                rx.cond(
                    DashboardState.detalhe_urgencia_tratada,
                    rx.button(
                        rx.icon(tag="undo-2", size=14),
                        "Voltar para urgências",
                        on_click=DashboardState.devolver_para_urgencia(
                            DashboardState.detalhe_id
                        ),
                        variant="soft", color_scheme="amber", cursor="pointer", size="2",
                    ),
                ),
                rx.spacer(),
                rx.button(
                    rx.icon(tag="trash-2", size=14),
                    "Excluir do banco",
                    on_click=DashboardState.pedir_exclusao(
                        DashboardState.detalhe_id, DashboardState.detalhe_assunto
                    ),
                    variant="soft", color_scheme="red", cursor="pointer", size="2",
                ),
                rx.dialog.close(
                    rx.button("Fechar", variant="soft", color_scheme="gray", cursor="pointer")
                ),
                width="100%", margin_top="1.5rem", align_items="center", spacing="2",
            ),
            max_width="640px",
        ),
        open=DashboardState.detalhe_aberto,
        on_open_change=DashboardState.set_detalhe_aberto,
    )


@rx.page(
    route="/dashboard",
    title="Dashboard | Coester",
    on_load=[
        AppState.load_dashboard,
        DashboardState.load_dashboard_data,
        # Sonda o andamento pelo banco. É o que faz a execução AGENDADA, que
        # roda noutro processo, aparecer nesta tela.
        DashboardState.monitorar_execucao,
    ],
)
def dashboard_page() -> rx.Component:
    return dashboard_layout(
        rx.vstack(
            rx.heading(
                f"Bem-vindo, {AppState.user_name}!", size="8",
                color=colors.TEXT_MAIN, font_family=HEADING_FONT,
            ),
            rx.text(
                "Acompanhe aqui a classificação dos e-mails da conta consulta@coester.com.br.",
                color=colors.TEXT_SEC, font_family=BODY_FONT, margin_bottom="1.5rem",
            ),
            _metricas(),
            _zerar(),
            _acao(),
            _progresso(),
            _urgencias(),
            rx.box(height="1.5rem"),
            _configuracao(),
            rx.box(height="1.5rem"),
            _pastas(),
            rx.box(height="1.5rem"),
            _tabela(),
            _detalhe(),
            confirm_dialog(
                open_var=DashboardState.confirm_zerar_open,
                on_open_change=DashboardState.set_confirm_zerar_open,
                title="Zerar os contadores do painel?",
                body=(
                    "Isso apaga TODAS as execuções, os e-mails classificados e os "
                    "resumos, zerando os indicadores, a lista de urgências e a "
                    "tabela. Vale para toda a equipe, não só para você.\n\n"
                    "Nada é desfeito no Outlook: os e-mails já arquivados continuam "
                    "nas pastas, com as categorias que receberam.\n\n"
                    "Atenção ao custo: é o registro apagado aqui que impede pagar "
                    "duas vezes pelo mesmo e-mail. Sem ele, a próxima execução "
                    "reclassifica tudo o que estiver dentro da janela de varredura "
                    "e cobra de novo por isso.\n\n"
                    "A configuração, as pastas vinculadas e o histórico de custo em "
                    "/admin não são afetados. A ação não pode ser desfeita."
                ),
                confirm_label="Zerar contadores",
                on_confirm=DashboardState.zerar_contadores,
            ),
            confirm_dialog(
                open_var=DashboardState.confirm_excluir_open,
                on_open_change=DashboardState.set_confirm_excluir_open,
                title="Excluir este e-mail do banco?",
                # Concatenação com `+` e não f-string: `excluir_assunto` é um
                # Var do Reflex, e interpolá-lo numa f-string gravaria o repr do
                # Var no HTML em vez do assunto.
                body=(
                    "Vai sair do painel, da lista de urgências e das respostas "
                    "da Consulta IA:\n\n"
                    + DashboardState.excluir_assunto
                    + "\n\nA mensagem NÃO é apagada do Outlook: ela continua na "
                    "pasta em que foi arquivada, com as categorias que recebeu."
                    "\n\nAtenção ao custo: é este registro que impede pagar duas "
                    "vezes pelo mesmo e-mail. Sem ele, se a mensagem voltar para "
                    "a caixa de entrada dentro da janela de varredura, ela será "
                    "reclassificada e cobrada de novo."
                    "\n\nA ação não pode ser desfeita."
                ),
                confirm_label="Excluir do banco",
                on_confirm=DashboardState.excluir_email,
            ),
            spacing="0", align_items="start", width="100%",
        )
    )
