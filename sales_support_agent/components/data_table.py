"""Tabela de registros compartilhada entre as telas do app.

Nasceu dentro de `pages/admin_dashboard.py` (God Mode) e foi extraída para cá
quando a tela de Enriquecimento passou a precisar do mesmo visual — as duas
importam daqui para não haver dois estilos de tabela no produto.
"""

import reflex as rx

from sales_support_agent.styles import colors


def table_shell(header_cells, body_rows) -> rx.Component:
    """Casca da tabela: cabeçalho destacado, cantos arredondados e borda padrão."""
    return rx.box(
        rx.table.root(
            rx.table.header(
                rx.table.row(*header_cells),
                background="rgba(245, 249, 254, 0.8)",
            ),
            rx.table.body(body_rows),
            width="100%",
            size="2",
        ),
        background=colors.CARD_BG,
        border_radius="12px",
        width="100%",
        overflow_x="auto",
        border=f"1px solid {colors.BORDER}",
    )


def col(text: str) -> rx.Component:
    """Célula de cabeçalho da tabela."""
    return rx.table.column_header_cell(rx.text(text, color=colors.TEXT_MAIN, font_weight="700"))
