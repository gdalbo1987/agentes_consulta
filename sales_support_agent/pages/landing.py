import reflex as rx
from prospect_agent.components.header import header
from prospect_agent.components.hero import hero
from prospect_agent.components.advantages import advantages
from prospect_agent.components.tutorial import tutorial
from prospect_agent.components.footer import footer
from prospect_agent.styles.theme import custom_css

def landing_page() -> rx.Component:
    return rx.box(
        rx.html(f"<style>{custom_css}</style>"),
        
        header(),
        
        # HERO SOLTO: Vai ocupar 100% da largura do monitor
        hero(),
        
        # CONTEÚDO LIMITADO: Centralizado para não ficar muito espalhado em telas gigantes
        rx.box(
            advantages(),
            tutorial(),
            max_width="1440px",
            margin="0 auto",
        ),
        
        footer(),
        
        style={"animation": "fadeIn 0.8s ease-out"}
    )