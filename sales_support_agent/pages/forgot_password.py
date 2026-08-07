import reflex as rx
from prospect_agent.styles import colors
from prospect_agent.state import ForgotPasswordState
from prospect_agent.pages.auth import auth_input
from prospect_agent.styles.typography import HEADING_FONT, BODY_FONT


@rx.page(route="/esqueci-senha", title="Recuperar Senha | Coester", on_load=ForgotPasswordState.load_forgot_password)
def forgot_password_page() -> rx.Component:
    return rx.center(
        rx.box(
            # Cabeçalho
            rx.vstack(
                rx.link(rx.image(src="/logo-coester-blue.png", alt="Coester", height="44px", margin_bottom="1rem"), href="/"),
                rx.heading("Recuperar senha", size="6", font_family=HEADING_FONT, color=colors.TEXT_MAIN),
                rx.text(
                    "Informe o e-mail da sua conta para receber um link de redefinição de senha.",
                    size="3", color=colors.TEXT_SEC, font_family=BODY_FONT, margin_bottom="2rem", text_align="center",
                ),
                align_items="center",
                width="100%",
            ),

            # Mensagem de erro
            rx.cond(
                ForgotPasswordState.error_message != "",
                rx.box(
                    rx.text(ForgotPasswordState.error_message, color="red", size="2", font_weight="500"),
                    background="#fee2e2", padding="0.75rem", border_radius="8px", width="100%", margin_bottom="1rem", text_align="center",
                ),
            ),

            # Mensagem de sucesso
            rx.cond(
                ForgotPasswordState.message != "",
                rx.box(
                    rx.text(ForgotPasswordState.message, color="#166534", size="2", font_weight="500"),
                    background="#dcfce7", padding="0.75rem", border_radius="8px", width="100%", margin_bottom="1rem", text_align="center",
                ),
            ),

            rx.form(
                rx.vstack(
                    auth_input("E-mail", "seu@email.com", "email", "email_field"),

                    rx.button(
                        rx.cond(ForgotPasswordState.is_sending, "Enviando...", "Enviar link de recuperação"),
                        type="submit",
                        loading=ForgotPasswordState.is_sending,
                        disabled=ForgotPasswordState.is_sending,
                        width="100%", padding_y="1.5rem", font_family=BODY_FONT, font_weight="600", border_radius="8px",
                        background=colors.BTN_GRADIENT, color="white", cursor="pointer", margin_top="0.5rem",
                        _hover={"transform": "scale(1.02)", "box_shadow": "0 8px 16px rgba(29, 84, 140, 0.2)"},
                    ),
                    width="100%",
                    spacing="3",
                ),
                on_submit=ForgotPasswordState.send_reset_link,
                reset_on_submit=False,
                width="100%",
            ),

            rx.center(
                rx.link("Voltar para o login", href="/login", color=colors.HIGHLIGHT, size="2", font_weight="600", _hover={"text_decoration": "underline"}),
                margin_top="2rem",
                width="100%",
            ),

            background=colors.CARD_BG,
            padding="3rem",
            border_radius="24px",
            box_shadow="0 12px 32px rgba(0,0,0,0.05)",
            border=f"1px solid {colors.BORDER}",
            width=rx.breakpoints(initial="100%", sm="450px"),
        ),

        width="100%",
        min_height="100vh",
        background=colors.BG,
        padding="1.5rem",
    )
