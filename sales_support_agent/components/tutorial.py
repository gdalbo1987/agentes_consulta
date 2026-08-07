import reflex as rx
from sales_support_agent.styles import colors
from sales_support_agent.styles.typography import HEADING_FONT

def step_circle(number: str, title: str, icon_tag: str) -> rx.Component:
    return rx.vstack(
        rx.flex(
            rx.icon(tag=icon_tag, color=colors.HIGHLIGHT, size=24),
            rx.text(number, font_family=HEADING_FONT, font_weight="900", color=colors.HIGHLIGHT, margin_left="0.5rem"),
            width="80px", height="80px",
            border_radius="50%",
            background=colors.CARD_BG,
            border=f"2px solid {colors.BORDER}",
            align_items="center", justify_content="center",
            margin_bottom="1.5rem",
            box_shadow="0 8px 16px rgba(0,0,0,0.02)",
            z_index="2"
        ),
        rx.text(title, font_family=HEADING_FONT, font_weight="700", color=colors.TEXT_MAIN, text_align="center", size="4"),
        align_items="center",
        # Reduzida levemente a largura para caberem 4 perfeitamente
        width=rx.breakpoints(initial="100%", sm="180px") 
    )

def tutorial() -> rx.Component:
    return rx.box(
        rx.center(rx.heading("Como funciona", font_family=HEADING_FONT, font_weight="900", size="8", color=colors.TEXT_MAIN, margin_bottom="4rem")),
        
        rx.box(
            rx.flex(
                step_circle("1", "Insira os descritivos dos seus produtos", "package"),
                rx.icon(tag="arrow-right", color=colors.TEXT_SEC, opacity="0.45", size=32, display=rx.breakpoints(initial="none", lg="block")),
                
                step_circle("2", "Defina seus segmento alvo", "target"),
                rx.icon(tag="arrow-right", color=colors.TEXT_SEC, opacity="0.45", size=32, display=rx.breakpoints(initial="none", lg="block")),
                
                step_circle("3", "IA procura os clientes e enriquece os dados", "sparkles"),
                rx.icon(tag="arrow-right", color=colors.TEXT_SEC, opacity="0.45", size=32, display=rx.breakpoints(initial="none", lg="block")),
                
                step_circle("4", "Receba o ranking inteligente", "list-ordered"),
                
                justify_content="space-between",
                align_items="center",
                flex_direction=rx.breakpoints(initial="column", lg="row"),
                gap=rx.breakpoints(initial="3rem", lg="1rem")
            ),
            position="relative",
            max_width="1100px", # Aumentado para acomodar os 4 passos no desktop
            margin="0 auto"
        ),
        
        padding_y="8rem",
        padding_x=rx.breakpoints(initial="1.5rem", sm="3rem", lg="6rem"),
        id="tutorial"
    )