import reflex as rx

from sales_support_agent.components.confirm_dialog import confirm_dialog
from sales_support_agent.components.dashboard_layout import dashboard_layout
from sales_support_agent.state import ConsultaState
from sales_support_agent.styles import colors
from sales_support_agent.styles.typography import BODY_FONT, HEADING_FONT


def _bubble(msg) -> rx.Component:
    eh_usuario = msg.role == "user"
    return rx.hstack(
        rx.box(
            rx.cond(
                msg.content != "",
                rx.text(msg.content, size="2", color=rx.cond(eh_usuario, "white", colors.TEXT_MAIN),
                        font_family=BODY_FONT, white_space="pre-wrap"),
                rx.spinner(size="2", color=colors.HIGHLIGHT),
            ),
            background=rx.cond(eh_usuario, colors.BTN_GRADIENT, "#eef4fb"),
            color=rx.cond(eh_usuario, "white", colors.TEXT_MAIN),
            padding="0.75rem 1rem",
            border_radius="14px",
            max_width="70%",
        ),
        width="100%",
        justify_content=rx.cond(eh_usuario, "flex-end", "flex-start"),
    )


def _sugestao(texto: str) -> rx.Component:
    return rx.button(
        texto,
        on_click=[ConsultaState.set_pergunta(texto), ConsultaState.enviar_pergunta],
        variant="soft",
        color_scheme="gray",
        size="2",
        cursor="pointer",
        border_radius="999px",
    )


def _empty_state() -> rx.Component:
    return rx.vstack(
        rx.icon(tag="sparkles", size=32, color=colors.HIGHLIGHT),
        rx.text("Pergunte sobre os e-mails classificados", size="4", font_weight="700", color=colors.TEXT_MAIN, font_family=HEADING_FONT),
        rx.text(
            "Urgências, pedidos, propostas, revisões, o que chegou de cada cliente e em que data. O agente responde com base nos e-mails que a plataforma já classificou.",
            size="2", color=colors.TEXT_SEC, font_family=BODY_FONT, text_align="center",
        ),
        rx.hstack(
            _sugestao("Quais e-mails estão urgentes?"),
            _sugestao("O que chegou esta semana?"),
            _sugestao("Quantas propostas em aberto?"),
            spacing="2", wrap="wrap", justify_content="center",
        ),
        spacing="3", align_items="center", justify_content="center",
        height="100%", width="100%", padding="2rem",
    )


@rx.page(route="/consulta", on_load=ConsultaState.load_consulta)
def consulta_page() -> rx.Component:
    return dashboard_layout(
        rx.vstack(
            rx.vstack(
                rx.hstack(
                    rx.vstack(
                        rx.heading("Consulta IA", size="8", color=colors.TEXT_MAIN, font_family=HEADING_FONT),
                        rx.text(
                            "Converse com um agente sobre os e-mails já classificados e resumidos.",
                            color=colors.TEXT_SEC, font_family=BODY_FONT,
                        ),
                        spacing="1", align_items="start",
                    ),
                    rx.spacer(),
                    rx.button(
                        rx.icon(tag="trash-2", size=16),
                        "Limpar conversa",
                        on_click=ConsultaState.set_clear_dialog_open(True),
                        variant="soft", color_scheme="red", cursor="pointer",
                    ),
                    width="100%", align_items="start", margin_bottom="1rem",
                ),

                rx.box(
                    rx.cond(
                        ConsultaState.sem_mensagens,
                        _empty_state(),
                        rx.vstack(
                            rx.foreach(ConsultaState.messages, _bubble),
                            # Âncora invisível: alvo do rx.scroll_to disparado
                            # pelo state a cada nova mensagem/pedaço da
                            # resposta (rolagem automática do chat).
                            rx.box(id="chat-anchor", height="1px"),
                            spacing="3", width="100%",
                        ),
                    ),
                    width="100%", flex="1", overflow_y="auto",
                    padding="1.5rem", background=colors.CARD_BG,
                    border_radius="16px", border=f"1px solid {colors.BORDER}",
                    margin_bottom="1rem",
                ),

                rx.cond(
                    ConsultaState.error != "",
                    rx.callout(ConsultaState.error, icon="triangle-alert", color_scheme="red", width="100%", margin_bottom="0.75rem"),
                ),

                rx.form(
                    rx.hstack(
                        rx.input(
                            placeholder="Pergunte sobre os e-mails classificados...",
                            value=ConsultaState.pergunta,
                            on_change=ConsultaState.set_pergunta,
                            size="3", width="100%", variant="surface",
                            color="#111827", background_color="#e9e8e8",
                            disabled=ConsultaState.is_sending,
                        ),
                        rx.button(
                            rx.icon(tag="send", size=16),
                            type="submit",
                            loading=ConsultaState.is_sending,
                            disabled=ConsultaState.is_sending,
                            background=colors.BTN_GRADIENT, color="white",
                            cursor="pointer", border_radius="8px", size="3",
                        ),
                        width="100%", spacing="2",
                    ),
                    on_submit=ConsultaState.enviar_pergunta,
                    width="100%",
                ),

                confirm_dialog(
                    open_var=ConsultaState.clear_dialog_open,
                    on_open_change=ConsultaState.set_clear_dialog_open,
                    title="Limpar toda a conversa?",
                    body="Isso apaga todo o histórico deste chat (só o seu, neste workspace). A ação não pode ser desfeita.",
                    confirm_label="Limpar",
                    on_confirm=ConsultaState.confirm_limpar_conversa,
                ),

                width="100%", max_width="900px", height="80vh", align_items="start",
            ),
            width="100%", align_items="center",
        )
    )
