"""Diálogo de confirmação para ações destrutivas e irreversíveis.

Nasceu dentro de `pages/admin_dashboard.py` ("Limpar contadores"/"Limpar
histórico") e foi extraído para cá quando passou a ter mais chamadores
(limpeza de contadores, exclusão de histórico, remoção de conta) e
mesmo motivo pelo qual `table_shell`/`col` foram extraídos para
`components/data_table.py`.
"""

import reflex as rx

# Fundo fixo escuro para o conteúdo do diálogo (mesmo motivo de
# pages/admin_dashboard.py: o rx.dialog/rx.alert_dialog é um portal Radix que
# herda o tema claro/escuro do sistema, e texto branco sobre o tema claro do
# sistema fica ilegível).
DIALOG_BG = "#1b2842"
_DIALOG_BG = DIALOG_BG


def dialog_cancel_button(label: str = "Cancelar") -> rx.Component:
    """Botão de cancelar dos diálogos, com cor explícita.

    Não use `variant="soft"` aqui: o app fixa o tema Radix em `appearance=
    "light"` (ver rxconfig.py), então o soft cinza pinta o botão com
    `gray-a3` (preto ~5% de opacidade) e texto `gray-11` (cinza escuro). Sobre
    o fundo escuro fixo do diálogo isso vira texto escuro em fundo escuro: o
    botão continua lá e continua clicável, mas fica invisível, que foi
    exatamente o defeito relatado.
    """
    return rx.button(
        label,
        variant="outline",
        color="white",
        background="rgba(255, 255, 255, 0.08)",
        border="1px solid rgba(255, 255, 255, 0.35)",
        box_shadow="none",
        cursor="pointer",
        _hover={"background": "rgba(255, 255, 255, 0.18)"},
    )


def confirm_dialog(
    *, open_var, on_open_change, title: str, body: str, confirm_label: str, on_confirm
) -> rx.Component:
    """Confirmação para ação destrutiva e irreversível (limpeza de dados)."""
    return rx.alert_dialog.root(
        rx.alert_dialog.content(
            rx.alert_dialog.title(title, color="white"),
            rx.alert_dialog.description(body, color="rgba(255, 255, 255, 0.75)"),
            rx.flex(
                rx.alert_dialog.cancel(dialog_cancel_button()),
                rx.alert_dialog.action(
                    rx.button(
                        confirm_label,
                        on_click=on_confirm,
                        color_scheme="red",
                        cursor="pointer",
                    ),
                ),
                spacing="3",
                margin_top="1.5rem",
                justify="end",
            ),
            max_width="440px",
            background=_DIALOG_BG,
        ),
        open=open_var,
        on_open_change=on_open_change,
    )
