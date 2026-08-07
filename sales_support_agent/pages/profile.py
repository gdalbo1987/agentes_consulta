import reflex as rx
from prospect_agent.components.dashboard_layout import dashboard_layout
from prospect_agent.state import AppState, ProfileState 
from prospect_agent.styles import colors
from prospect_agent.styles.typography import BODY_FONT

def profile_input(
    label: str,
    placeholder: str,
    input_type: str,
    value_bind,
    on_change_bind,
    show_password=None,
    on_toggle_password=None,
) -> rx.Component:
    """Input HTML com o mesmo padrão de cores do Auth.

    Para campos de senha, passe `show_password` (Var[bool]) e
    `on_toggle_password` (EventHandler) para habilitar o botão dinâmico de
    mostrar/ocultar a senha: ele fica oculto com o campo vazio e aparece assim
    que há texto (via CSS `:placeholder-shown`), suprimindo o botão nativo do
    navegador para não duplicar.
    """
    has_toggle = input_type == "password" and show_password is not None

    input_el = rx.input(
        placeholder=placeholder,
        type=rx.cond(show_password, "text", "password") if has_toggle else input_type,
        value=value_bind,
        on_change=on_change_bind,
        size="3",
        width="100%",
        variant="surface",
        color_scheme="blue",

        # --- CORES ESPELHADAS DO SEU LOGIN ---
        color="#111827",
        background_color="#e9e8e8",
        _placeholder={"color": "#3a3a3b"},
        # -------------------------------------
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
                "& input::-ms-reveal": {"display": "none"},
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

@rx.page(route="/profile", on_load=ProfileState.load_profile)
def profile_page() -> rx.Component:
    return dashboard_layout(
        rx.vstack(
            rx.heading(
                "Meu Perfil",
                size="8",
                color=colors.TEXT_MAIN,
                margin_bottom="2rem",
                width="100%",
                max_width="600px",
                text_align="left",
            ),

            # Card de Perfil
            rx.box(
                rx.hstack(
                    rx.avatar(name=AppState.user_name, src=AppState.user_avatar, size="8"),
                    rx.vstack(
                        rx.heading(AppState.user_name, size="6", color=colors.TEXT_MAIN),
                        rx.text(AppState.user_email, color=colors.TEXT_SEC, font_family=BODY_FONT),
                        rx.badge(rx.cond(AppState.is_superadmin, "Administrador", "Cliente"), color_scheme="blue", margin_top="0.5rem"),
                    ),
                    spacing="4",
                    align_items="center",
                    margin_bottom="2rem"
                ),
                
                rx.divider(margin_y="2rem", border_color=colors.BORDER),
                
                # ==========================================
                # SESSÃO 1: ATUALIZAR FOTO
                # ==========================================
                rx.heading("Atualizar Foto", size="4", color=colors.TEXT_MAIN, margin_bottom="0.5rem"),
                rx.text("Cole o link (URL) de uma imagem para ser o seu novo avatar.", color=colors.TEXT_SEC, margin_bottom="1.5rem"),
                
                rx.hstack(
                    rx.box(
                        profile_input("URL da Imagem", "https://exemplo.com/minha-foto.jpg", "text", ProfileState.new_avatar_url, ProfileState.set_new_avatar_url),
                        width="100%"
                    ),
                    rx.button(
                        "Salvar",
                        on_click=ProfileState.update_avatar,
                        size="3",
                        background=colors.BTN_GRADIENT,
                        color="white",
                        cursor="pointer",
                        margin_top="1.5rem", # Alinha a base do botão com a base do input
                        border_radius="8px",
                    ),
                    width="100%",
                    spacing="3",
                    align_items="end"
                ),
                
                rx.divider(margin_y="2rem", border_color=colors.BORDER),
                
                # ==========================================
                # SESSÃO 2: ATUALIZAR SENHA
                # ==========================================
                rx.heading("Segurança", size="4", color=colors.TEXT_MAIN, margin_bottom="0.5rem"),
                rx.text("Atualize sua senha de acesso.", color=colors.TEXT_SEC, margin_bottom="1.5rem"),
                
                rx.vstack(
                    profile_input("Senha Atual", "••••••••", "password", ProfileState.current_password, ProfileState.set_current_password, ProfileState.show_current_password, ProfileState.toggle_current_password_visibility),
                    profile_input("Nova Senha", "••••••••", "password", ProfileState.new_password, ProfileState.set_new_password, ProfileState.show_new_password, ProfileState.toggle_new_password_visibility),
                    
                    rx.button(
                        "Atualizar Senha",
                        on_click=ProfileState.update_password,
                        size="3",
                        width="100%",
                        background=colors.BTN_GRADIENT,
                        color="white",
                        cursor="pointer",
                        margin_top="0.5rem",
                        border_radius="8px"
                    ),
                    spacing="3",
                    width="100%"
                ),

                background=colors.CARD_BG,
                padding="2rem",
                border_radius="16px",
                border=f"1px solid {colors.BORDER}",
                width="100%",
                max_width="600px"
            ),
            align_items="center",
            width="100%",
        )
    )