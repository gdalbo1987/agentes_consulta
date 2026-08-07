"""Dashboard do usuário padrão.

Esqueleto. O conteúdo real entra na Fase 8: métricas das rodadas de
classificação (duração média e da última, e-mails classificados no acumulado e
na última), configuração dos dois horários automáticos e da janela de urgência,
mapeamento das quatro pastas do Outlook por NOME, botão de classificar agora, e
a tabela de e-mails classificados com filtro por data e por urgente, abrindo o
resumo do Agente 2 ao clicar na linha.

A página existe desde já, e não só a partir da Fase 8, para que a rota continue
registrada e protegida pelo gate de autenticação enquanto o resto é construído.
"""

import reflex as rx

from sales_support_agent.components.dashboard_layout import dashboard_layout
from sales_support_agent.state import AppState, DashboardState
from sales_support_agent.styles import colors
from sales_support_agent.styles.typography import BODY_FONT, HEADING_FONT


@rx.page(
    route="/dashboard",
    title="Dashboard | Coester",
    on_load=[AppState.load_dashboard, DashboardState.load_dashboard_data],
)
def dashboard_page() -> rx.Component:
    return dashboard_layout(
        rx.vstack(
            rx.heading(
                f"Bem-vindo, {AppState.user_name}!",
                size="8",
                color=colors.TEXT_MAIN,
                font_family=HEADING_FONT,
            ),
            rx.text(
                "Acompanhe aqui a classificação dos e-mails da caixa comercial.",
                color=colors.TEXT_SEC,
                font_family=BODY_FONT,
            ),
            rx.callout(
                "O painel de classificação está em construção.",
                icon="hammer",
                color_scheme="blue",
                width="100%",
                margin_top="1.5rem",
            ),
            spacing="1",
            align_items="start",
            width="100%",
        )
    )
