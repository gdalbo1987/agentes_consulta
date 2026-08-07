import reflex as rx
from prospect_agent.styles import colors
from prospect_agent.state import ResetPasswordState
from prospect_agent.pages.auth import auth_input
from prospect_agent.styles.typography import HEADING_FONT, BODY_FONT


def _invalid_token_view() -> rx.Component:
    return rx.vstack(
        rx.icon(tag="triangle-alert", size=40, color="#b45309", margin_bottom="0.5rem"),
        rx.heading("Link inválido ou expirado", size="5", font_family=HEADING_FONT, color=colors.TEXT_MAIN, text_align="center"),
        rx.text(
            "Solicite um novo link de redefinição de senha.",
            color=colors.TEXT_SEC, font_family=BODY_FONT, text_align="center", margin_bottom="1rem",
        ),
        rx.link(
            rx.button(
                "Solicitar novo link",
                background=colors.BTN_GRADIENT, color="white", cursor="pointer", font_family=BODY_FONT, font_weight="600",
            ),
            href="/esqueci-senha",
        ),
        align_items="center",
        width="100%",
    )


def _success_view() -> rx.Component:
    return rx.vstack(
        rx.icon(tag="circle-check", size=40, color="#166534", margin_bottom="0.5rem"),
        rx.heading("Senha redefinida!", size="5", font_family=HEADING_FONT, color=colors.TEXT_MAIN, text_align="center"),
        rx.text(
            "Sua senha foi atualizada com sucesso. Já pode acessar a plataforma.",
            color=colors.TEXT_SEC, font_family=BODY_FONT, text_align="center", margin_bottom="1rem",
        ),
        rx.link(
            rx.button(
                "Ir para o login",
                background=colors.BTN_GRADIENT, color="white", cursor="pointer", font_family=BODY_FONT, font_weight="600",
            ),
            href="/login",
        ),
        align_items="center",
        width="100%",
    )


def _reset_form_view() -> rx.Component:
    return rx.vstack(
        rx.vstack(
            rx.heading("Defina sua nova senha", size="6", font_family=HEADING_FONT, color=colors.TEXT_MAIN),
            rx.text(
                "Escolha uma nova senha para sua conta.",
                size="3", color=colors.TEXT_SEC, font_family=BODY_FONT, margin_bottom="2rem", text_align="center",
            ),
            align_items="center",
            width="100%",
        ),

        rx.cond(
            ResetPasswordState.error_message != "",
            rx.box(
                rx.text(ResetPasswordState.error_message, color="red", size="2", font_weight="500"),
                background="#fee2e2", padding="0.75rem", border_radius="8px", width="100%", margin_bottom="1rem", text_align="center",
            ),
        ),

        rx.form(
            rx.vstack(
                auth_input("Nova Senha", "••••••••", "password", "new_password_field", ResetPasswordState.show_new_password, ResetPasswordState.toggle_new_password_visibility),
                auth_input("Confirmar Nova Senha", "••••••••", "password", "confirm_password_field", ResetPasswordState.show_confirm_password, ResetPasswordState.toggle_confirm_password_visibility),

                rx.button(
                    "Redefinir senha",
                    type="submit",
                    width="100%", padding_y="1.5rem", font_family=BODY_FONT, font_weight="600", border_radius="8px",
                    background=colors.BTN_GRADIENT, color="white", cursor="pointer", margin_top="0.5rem",
                    _hover={"transform": "scale(1.02)", "box_shadow": "0 8px 16px rgba(29, 84, 140, 0.2)"},
                ),
                width="100%",
                spacing="3",
            ),
            on_submit=ResetPasswordState.do_reset,
            reset_on_submit=False,
            width="100%",
        ),
        width="100%",
    )


@rx.page(route="/redefinir-senha", title="Redefinir Senha | Coester", on_load=ResetPasswordState.load_reset_page)
def reset_password_page() -> rx.Component:
    return rx.center(
        rx.box(
            rx.center(
                rx.link(rx.image(src="/logo-coester-blue.png", alt="Coester", height="44px"), href="/"),
                width="100%",
                margin_bottom="1.5rem",
            ),

            rx.cond(
                ResetPasswordState.checked_token,
                rx.cond(
                    ResetPasswordState.success,
                    _success_view(),
                    rx.cond(
                        ResetPasswordState.token_valid,
                        _reset_form_view(),
                        _invalid_token_view(),
                    ),
                ),
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
