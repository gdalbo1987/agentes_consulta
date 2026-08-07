import reflex as rx
from prospect_agent.styles import colors
from prospect_agent.styles.typography import HEADING_FONT, BODY_FONT

def card_vantagem(icon_tag: str, title: str, description: str) -> rx.Component:
    return rx.box(
        rx.icon(tag=icon_tag, color=colors.HIGHLIGHT, size=32, margin_bottom="1.5rem"),
        rx.heading(title, size="5", font_family=HEADING_FONT, font_weight="700", color=colors.TEXT_MAIN, margin_bottom="0.5rem"),
        rx.text(description, font_family=BODY_FONT, font_weight="400", color=colors.TEXT_SEC, size="3", line_height="1.5"),
        
        background=colors.CARD_BG,
        padding="2rem",
        border_radius="16px",
        border=f"1px solid {colors.BORDER}",
        
        # A "barra azul" inferior escondida (border-top é melhor para UI SaaS)
        border_top="4px solid transparent",
        transition="all 0.3s cubic-bezier(0.4, 0, 0.2, 1)",
        
        _hover={
            "transform": "translateY(-6px)",
            "box_shadow": "0 20px 40px rgba(0,0,0,0.04)",
            "border_top": f"4px solid {colors.HIGHLIGHT}"
        }
    )

def advantages() -> rx.Component:
    cards_data = [
        ("cpu", "IA para Priorização", "Define automaticamente quais leads possuem maior potencial."),
        ("database", "Enriquecimento Inteligente", "Consulta informações empresariais automaticamente."),
        ("bar-chart-3", "Score Comercial", "Calcula um score baseado em ICP, porte, faturamento e presença digital."),
        ("lightbulb", "Insights da Empresa", "Resume informações importantes antes do primeiro contato."),
        ("target", "Estratégia Comercial", "Sugere abordagem personalizada para cada empresa."),
        ("clock", "Economia de Tempo", "Seu time atende primeiro quem realmente importa."),
    ]
    
    return rx.box(
        rx.center(
            rx.vstack(
                rx.heading("Muito mais do que uma simples prospecção de clientes.", font_family=HEADING_FONT, font_weight="900", size="8", color=colors.TEXT_MAIN),
                rx.text("Uma plataforma que utiliza agentes de IA para transformar dados em inteligência comercial.", font_family=BODY_FONT, color=colors.TEXT_SEC, size="4", text_align="center", max_width="600px"),
                spacing="4",
                align_items="center",
                margin_bottom="4rem"
            )
        ),
        
        rx.grid(
            *[card_vantagem(icon, title, desc) for icon, title, desc in cards_data],
            columns=rx.breakpoints(initial="1", sm="2", lg="3"),
            spacing="6",
            width="100%",
        ),
        
        padding_y="6rem",
        padding_x=["1.5rem", "3rem", "6rem"],
        id="vantagens"
    )