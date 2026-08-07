import reflex as rx
from sales_support_agent.styles import colors
from sales_support_agent.components.gradient_button import gradient_button
from sales_support_agent.state import AppState

def header() -> rx.Component:
    return rx.flex(
        # Logo com link para voltar à Home
        rx.link(
            # Só a altura é fixada: a largura acompanha o aspect ratio nativo
            # do PNG (4:3), então o logo nunca aparece esticado.
            rx.image(src="/logo-coester-blue.png", height="44px", alt="Coester"),
            href="/",
            _hover={"opacity": 0.8} # Leve efeito ao passar o mouse na logo
        ),
        
        # Menu Central (Escondido no Mobile)
        rx.hstack(
            rx.link("Home", href="/", color=colors.TEXT_SEC, font_weight="500", transition="color 0.2s", _hover={"color": colors.HIGHLIGHT}),
            rx.link("Vantagens", href="/#vantagens", color=colors.TEXT_SEC, font_weight="500", transition="color 0.2s", _hover={"color": colors.HIGHLIGHT}),
            rx.link("Como funciona", href="/#tutorial", color=colors.TEXT_SEC, font_weight="500", transition="color 0.2s", _hover={"color": colors.HIGHLIGHT}),
            spacing="8",
            display=rx.breakpoints(initial="none", lg="flex"),
        ),
        
        # CTA (Você pode alterar o href deste botão futuramente para a tela de Login/App)
        gradient_button("Acessar Plataforma", href="/login"),
        
        width="100%",
        height="80px",
        align_items="center",
        justify_content="space-between",
        padding_x=rx.breakpoints(initial="1.5rem", sm="3rem", lg="6rem"),
        position="sticky",
        top="0",
        z_index="999",
        # Branco quase opaco: a barra é sticky e passa por cima do vídeo do hero
        # e das seções seguintes — com pouca opacidade ela encampava a cor do que
        # estivesse atrás e lia como cinza. Os 8% restantes bastam para o blur
        # ainda dar a sensação de vidro fosco ao rolar.
        background="rgba(255, 255, 255, 0.92)",
        backdrop_filter="blur(16px)",          # Efeito Blur
        border_bottom=f"1px solid {colors.BORDER}",
    )