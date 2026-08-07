import reflex as rx
from prospect_agent.styles import colors
from prospect_agent.state import AppState
from prospect_agent.styles.typography import BODY_FONT

def sidebar_item(text: str, icon: str, href: str) -> rx.Component:
    # Compacto de propósito: são até 9 itens + logo + menu do usuário dentro de
    # 100vh. Cada 4px a mais de padding aqui vira ~36px na coluna inteira, que é
    # o que fazia a barra de rolagem aparecer em telas de notebook.
    return rx.link(
        rx.hstack(
            rx.icon(tag=icon, size=18, flex_shrink="0"),
            rx.text(text, font_family=BODY_FONT, font_weight="500", size="2", white_space="nowrap"),
            color="rgba(255, 255, 255, 0.7)",
            padding_y="0.5rem",
            padding_x="0.75rem",
            border_radius="8px",
            align_items="center",
            spacing="2",
            transition="all 0.2s",
            _hover={"background": "rgba(255, 255, 255, 0.1)", "color": "white"}
        ),
        href=href,
        width="100%",
        _hover={"text_decoration": "none"}
    )

def user_menu() -> rx.Component:
    return rx.vstack(
        rx.divider(border_color="rgba(255,255,255,0.1)", margin_bottom="0.5rem", width="100%"),
        
        # 1. Identificação do usuário: avatar + nome, sem o e-mail. Quem está
        # logado já sabe o próprio e-mail, e ele é o texto mais longo da coluna
        # — era ele que estourava os 260px e criava a rolagem horizontal. Quem
        # precisar conferir a conta tem o e-mail em "Meu Perfil".
        rx.hstack(
            rx.avatar(name=AppState.user_name, src=AppState.user_avatar, size="2", flex_shrink="0"),
            # Nome longo termina em "..." em vez de quebrar a largura da coluna.
            # `min_width="0"` é o que faz o ellipsis funcionar: sem ele o item
            # de flex não encolhe abaixo do próprio conteúdo (`min-width: auto`)
            # e o nome seria cortado a seco pelo `overflow` do hstack.
            rx.text(
                AppState.user_name,
                color="white", font_family=BODY_FONT, font_weight="600", size="2",
                overflow="hidden", text_overflow="ellipsis", white_space="nowrap",
                min_width="0",
            ),
            spacing="2",
            align_items="center",
            width="100%",
            overflow="hidden",
        ),

        # 2. Botão de Sair isolado na última linha, com o mesmo estilo visual do menu
        rx.hstack(
            rx.icon(tag="log-out", size=18, flex_shrink="0"),
            rx.text("Sair", font_family=BODY_FONT, font_weight="500", size="2"),

            color="rgba(255, 120, 120, 0.9)", # Vermelho suave
            padding_y="0.5rem",
            padding_x="0.5rem",
            border_radius="8px",
            width="100%",
            align_items="center",
            spacing="2",
            cursor="pointer",
            transition="all 0.2s",
            on_click=AppState.logout,
            _hover={"background": "rgba(255, 50, 50, 0.1)", "color": "#ff4d4d"}, # Efeito hover vermelho
            margin_top="0.25rem"
        ),

        width="100%",
        padding="0.5rem",
        spacing="1"
    )

def dashboard_layout(*children: rx.Component) -> rx.Component:
    return rx.flex(
        rx.vstack(
            # Sidebar tem fundo em degradê escuro: usa a versão branca do logo.
            # Altura fixa e largura automática preservam o aspect ratio 4:3.
            rx.image(src="/logo-coester-white.png", height="44px", alt="Coester", margin_bottom="1.25rem", margin_top="0.25rem", flex_shrink="0"),

            rx.vstack(
                sidebar_item("Dashboard", "layout-dashboard", "/dashboard"),
                sidebar_item("Lista de Leads", "users", "/leads"),
                sidebar_item("Produtos", "package", "/produtos"),
                sidebar_item("Pesquisa", "search", "/pesquisa"),
                sidebar_item("Enriquecimento", "database-zap", "/enriquecimento"),
                sidebar_item("Priorização", "target", "/priorizacao"),
                sidebar_item("Insights IA", "sparkles", "/insights-ia"),

                # ADICIONADO: Link para a página de perfil
                sidebar_item("Meu Perfil", "circle-user", "/profile"),
                
                rx.cond(
                    AppState.is_superadmin,
                    sidebar_item("Painel Admin", "shield-alert", "/admin")
                ),
                
                spacing="1",
                width="100%",
                align_items="start"
            ),

            rx.spacer(),
            user_menu(),

            width="260px",
            height="100vh",
            background=colors.BTN_GRADIENT,
            padding_x="0.75rem",
            padding_y="1rem",
            position="sticky",
            top="0",
            spacing="0",
            # `overflow_x="hidden"` mata a rolagem horizontal de vez: nada aqui
            # deve vazar dos 260px (os textos já são `nowrap` + ellipsis). No
            # eixo Y fica "auto", não "hidden": o conteúdo foi enxugado para
            # caber em 100vh sem barra, mas numa janela muito baixa é melhor
            # rolar do que esconder o botão de Sair.
            overflow_x="hidden",
            overflow_y="auto",
        ),
        rx.box(
            *children,
            width="100%", height="100vh", overflow_y="auto",
            background=colors.BG, padding="2rem",
        ),
        # `height` fixo (não `min_height`) + `overflow="hidden"`: a página
        # inteira nunca ultrapassa 100vh, então só o box de conteúdo acima
        # (que já tem seu próprio `overflow_y="auto"`) rola — sem isso a
        # página também rolava, gerando duas barras de scroll.
        width="100%", height="100vh", overflow="hidden",
    )