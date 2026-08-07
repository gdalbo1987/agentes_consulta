import reflex as rx
from datetime import date

from prospect_agent.styles import colors
from prospect_agent.styles.typography import BODY_FONT

# E-mail para dúvidas sobre a plataforma interna.
CONTATO_EMAIL = "inovacao@coester.com.br"


def footer() -> rx.Component:
    """Rodapé da landing.

    Enxuto de propósito: como a plataforma deixou de ser um produto comercial e
    passou a ser ferramenta interna da Coester, saíram os links de redes
    sociais, o WhatsApp comercial e a página de Termos/Privacidade. Ficaram
    apenas o aviso de copyright e o canal de contato para dúvidas.
    """
    return rx.box(
        rx.flex(
            # O ano é calculado na compilação da página, não escrito à mão —
            # assim o rodapé não envelhece sozinho na virada do ano.
            rx.text(
                f"© {date.today().year} Coester. Todos os direitos reservados.",
                color=colors.TEXT_SEC,
                font_family=BODY_FONT,
                size="2",
            ),
            rx.link(
                CONTATO_EMAIL,
                href=f"mailto:{CONTATO_EMAIL}",
                color=colors.TEXT_SEC,
                font_family=BODY_FONT,
                size="2",
                font_weight="500",
                _hover={"color": colors.HIGHLIGHT},
            ),
            justify_content="space-between",
            align_items="center",
            flex_direction=rx.breakpoints(initial="column", sm="row"),
            gap="0.75rem",
        ),
        padding_y="3rem",
        padding_x=rx.breakpoints(initial="1.5rem", sm="3rem", lg="6rem"),
        background=colors.BG,
        border_top=f"1px solid {colors.BORDER}",
    )
