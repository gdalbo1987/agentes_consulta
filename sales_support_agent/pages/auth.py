import reflex as rx
from sales_support_agent.styles import colors
from sales_support_agent.state import AuthState
from sales_support_agent.styles.typography import HEADING_FONT, BODY_FONT


def auth_input(
    label: str,
    placeholder: str,
    input_type: str,
    field_name: str,
    show_password=None,
    on_toggle_password=None,
) -> rx.Component:
    """Input HTML Nativo com cores personalizadas.

    Para campos de senha, passe `show_password` (Var[bool]) e
    `on_toggle_password` (EventHandler) para habilitar o botão dinâmico de
    mostrar/ocultar a senha: ele fica oculto com o campo vazio e aparece assim
    que o usuário começa a digitar (via CSS `:placeholder-shown`). O botão de
    revelar nativo do navegador (Edge) é suprimido para não duplicar.
    """
    has_toggle = input_type == "password" and show_password is not None

    input_el = rx.input(
        name=field_name,
        placeholder=placeholder,
        type=rx.cond(show_password, "text", "password") if has_toggle else input_type,
        size="3",
        width="100%",
        variant="surface",
        color_scheme="blue",
        required=True,

        # --- NOVAS CORES AQUI ---
        color="#111827",           # Texto bem escuro (quase preto)
        background_color="#e9e8e8", # Fundo claro (cinza bem suave)
        _placeholder={"color": "#3a3a3b"}, # Deixa o texto de dica (placeholder) num cinza legível
        # ------------------------
        **({"padding_right": "2.5rem"} if has_toggle else {}),
    )

    if has_toggle:
        input_el = rx.box(
            input_el,
            rx.icon_button(
                rx.cond(show_password, rx.icon(tag="eye-off", size=18, color="#111827"), rx.icon(tag="eye", size=18, color="#111827")),
                on_click=on_toggle_password,
                type="button",
                variant="ghost",
                size="1",
                cursor="pointer",
                class_name="pw-toggle",
                background_color="#e9e8e8",
                position="absolute",
                top="50%",
                right="0.5rem",
                style={"transform": "translateY(-50%)"},
            ),
            position="relative",
            width="100%",
            background_color="#e9e8e8",
            border_radius="6px",
            style={
                # Suprime o botão de revelar senha nativo do Edge/IE (evita duplicidade).
                "& input::-ms-reveal": {"display": "none"},
                # Botão dinâmico: oculto com o campo vazio, aparece assim que há texto.
                "& .pw-toggle": {"display": "none"},
                "&:has(input:not(:placeholder-shown)) .pw-toggle": {"display": "inline-flex"},
            },
        )

    return rx.vstack(
        rx.text(label, size="2", font_weight="600", color=colors.TEXT_MAIN, margin_bottom="-0.2rem"),
        input_el,
        width="100%",
        align_items="start",
        spacing="1"
    )

def auth_page() -> rx.Component:
    """Tela de acesso — apenas login por e-mail/senha.

    Não há cadastro nem login social: a admissão é exclusivamente por convite de
    um super admin (ver AdminState.create_user).
    """
    return rx.center(
        rx.box(
            # Cabeçalho
            rx.vstack(
                rx.link(rx.image(src="/logo-coester-blue.png", alt="Coester", height="44px", margin_bottom="1rem"), href="/"),
                rx.heading(
                    "Bem-vindo de volta",
                    size="6", font_family=HEADING_FONT, color=colors.TEXT_MAIN
                ),
                rx.text(
                    "Insira seus dados para acessar a plataforma.",
                    size="3", color=colors.TEXT_SEC, font_family=BODY_FONT, margin_bottom="2rem"
                ),
                align_items="center",
                width="100%"
            ),

            # Mensagem de Erro
            rx.cond(
                AuthState.error_message != "",
                rx.box(
                    rx.text(AuthState.error_message, color="red", size="2", font_weight="500"),
                    background="#fee2e2", padding="0.75rem", border_radius="8px", width="100%", margin_bottom="1rem", text_align="center"
                )
            ),

            # ==========================================
            # FORMULÁRIO BLINDADO (Envia tudo de uma vez)
            # ==========================================
            rx.form(
                rx.vstack(
                    # Email e Senha
                    auth_input("E-mail", "seu@email.com", "email", "email_field"),
                    auth_input("Senha", "••••••••", "password", "password_field", AuthState.show_password, AuthState.toggle_password_visibility),

                    # Esqueceu a Senha
                    rx.box(
                        rx.text("Esqueceu sua senha?", color=colors.HIGHLIGHT, size="2", cursor="pointer", font_weight="500", _hover={"text_decoration": "underline"}, on_click=rx.redirect("/esqueci-senha")),
                        width="100%", text_align="right"
                    ),

                    # O Botão de Submit (Dispara a função do Formulário)
                    rx.button(
                        "Entrar",
                        type="submit",
                        font_size="1rem", border_radius="8px", padding_y="1.5rem",
                        width="100%", font_family=BODY_FONT, font_weight="600",
                        background=colors.BTN_GRADIENT, color="white", cursor="pointer", margin_top="0.5rem",
                        _hover={"transform": "scale(1.02)", "box_shadow": "0 8px 16px rgba(29, 84, 140, 0.2)"}
                    ),
                    width="100%",
                    spacing="3"
                ),
                on_submit=AuthState.handle_submit, 
                reset_on_submit=False,
                width="100%"
            ),
            # ==========================================

            # Acesso restrito: sem link de cadastro, porque não existe cadastro
            # público — o acesso é criado por um super admin.
            rx.center(
                rx.text(
                    "Acesso restrito a colaboradores autorizados da Coester.",
                    color=colors.TEXT_SEC, size="2", text_align="center",
                ),
                margin_top="2rem",
                width="100%"
            ),

            background=colors.CARD_BG,
            padding="3rem",
            border_radius="24px",
            box_shadow="0 12px 32px rgba(0,0,0,0.05)",
            border=f"1px solid {colors.BORDER}",
            width=rx.breakpoints(initial="100%", sm="450px")
        ),
        
        width="100%",
        min_height="100vh",
        background=colors.BG,
        padding="1.5rem"
    )