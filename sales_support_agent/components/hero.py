import reflex as rx
from sales_support_agent.styles import colors
from sales_support_agent.styles.typography import HEADING_FONT, BODY_FONT

def hero() -> rx.Component:
    return rx.box(
        # 1. Vídeo Nativo de Fundo (HTML5)
        rx.el.video(
            src="/hero-coester.mp4",
            auto_play=True,
            loop=True,
            muted=True,
            plays_inline=True,
            style={
                "position": "absolute",
                "top": "0",
                "left": "0",
                "width": "100%",
                "height": "100%",
                "object_fit": "cover", # Garante que o vídeo estique sem achatar
                "z_index": "0",
            }
        ),
        
        # Camada semi-transparente para leitura do texto
        rx.box(
            position="absolute",
            top="0",
            left="0",
            width="100%",
            height="100%",
            # Véu claro sobre o vídeo: quanto MENOR a opacidade, mais o vídeo
            # aparece e mais escura fica a seção. É o único número a mexer para
            # calibrar isso — mas não suba muito além de 0.6 sem trocar a cor do
            # texto, que é escuro (colors.TEXT_MAIN) e depende deste véu claro
            # para continuar legível.
            background="rgba(245, 249, 254, 0.6)",
            backdrop_filter="blur(4px)",
            z_index="1"
        ),

        # 2. Conteúdo sobreposto (Texto e Botões)
        rx.vstack(
            rx.heading(
                "Plataforma Coester de Prospecção", 
                font_family=HEADING_FONT, 
                font_weight="900", 
                size="9", 
                color=colors.TEXT_MAIN, 
                text_align="center",
                max_width="1200px",
                margin_bottom="5.0rem", 
                line_height="1.1"
            ),
            rx.heading(
                "Priorize seus leads com Inteligência Artificial.", 
                font_family=HEADING_FONT, 
                font_weight="900", 
                size="8", 
                color=colors.HIGHLIGHT, 
                text_align="center",
                max_width="800px",
                margin_bottom="1.5rem", 
                line_height="1.1"
            ),
            rx.text(
                "Ferramenta interna do Grupo Coester: pesquisa, enriquece dados empresariais, calcula o potencial de cada lead e recomenda quem a equipe comercial deve atender primeiro.", 
                font_family=BODY_FONT, 
                font_weight="400", 
                size="5", 
                color=colors.TEXT_SEC, 
                text_align="center",
                max_width="680px",
                line_height="1.6"
            ),
            # Sem CTA aqui: o "Acessar Plataforma" do header já fica visível o
            # tempo todo (barra sticky), então repeti-lo só polui o hero.
            position="relative",
            z_index="2", # Fica acima do vídeo e do overlay
            align_items="center",
            width="100%"
        ),

        position="relative",
        width="100%",
        overflow="hidden", # Esconde qualquer rebarba do vídeo
        padding_y=rx.breakpoints(initial="6rem", sm="8rem", lg="10rem"),
        padding_x=rx.breakpoints(initial="1.5rem", sm="3rem", lg="6rem"),
    )