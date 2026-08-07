import reflex as rx

from sales_support_agent.components.brazil_map import brazil_map
from sales_support_agent.components.dashboard_layout import dashboard_layout
from sales_support_agent.components.data_table import col, table_shell
from sales_support_agent.pages.admin_dashboard import admin_card
from sales_support_agent.state import AppState, DashboardState
from sales_support_agent.styles import colors
from sales_support_agent.styles.typography import BODY_FONT, HEADING_FONT


def _donut_card(titulo: str, data, altura: int = 280) -> rx.Component:
    return rx.box(
        rx.text(titulo, size="4", font_weight="700", color=colors.TEXT_MAIN, font_family=HEADING_FONT, margin_bottom="0.5rem"),
        rx.cond(
            data.length() > 0,
            rx.recharts.pie_chart(
                rx.recharts.pie(
                    rx.foreach(data, lambda item: rx.recharts.cell(fill=item["cor"])),
                    data=data,
                    data_key="quantidade",
                    name_key="nome",
                    inner_radius="50%",
                    outer_radius="70%",
                    padding_angle=2,
                    label=True,
                ),
                rx.recharts.graphing_tooltip(),
                rx.recharts.legend(font_size="0.8rem"),
                margin={"top": 20, "right": 30, "bottom": 10, "left": 30},
                width="100%",
                height=altura,
            ),
            rx.center(
                rx.text("Sem dados suficientes ainda.", color=colors.TEXT_SEC, size="2", font_family=BODY_FONT),
                height=f"{altura}px",
            ),
        ),
        background=colors.CARD_BG,
        padding="1.5rem",
        border_radius="16px",
        border=f"1px solid {colors.BORDER}",
        width="100%",
    )


def _map_card() -> rx.Component:
    return rx.box(
        rx.text(
            "Concentração de leads por estado",
            size="4", font_weight="700", color=colors.TEXT_MAIN, font_family=HEADING_FONT, margin_bottom="0.5rem",
        ),
        rx.center(brazil_map(
            DashboardState.cores_por_estado, DashboardState.contagem_por_estado,
            width="100%", height="380px",
        )),
        background=colors.CARD_BG,
        padding="1.5rem",
        border_radius="16px",
        border=f"1px solid {colors.BORDER}",
        width="100%",
    )


def _lead_row(lead) -> rx.Component:
    return rx.table.row(
        rx.table.cell(lead.nome, color=colors.TEXT_MAIN, font_weight="500"),
        rx.table.cell(lead.segmento, color=colors.TEXT_SEC),
        rx.table.cell(lead.porte, color=colors.TEXT_SEC),
        rx.table.cell(
            rx.hstack(
                rx.badge(f"{lead.score}", color_scheme="blue"),
                rx.text(lead.score_label, size="1", color=colors.TEXT_SEC),
                spacing="2", align_items="center",
            ),
        ),
        rx.table.cell(
            rx.cond(
                lead.classe != "-",
                rx.badge(lead.classe, color_scheme=lead.classe_cor),
                rx.text("-", color=colors.TEXT_SEC),
            ),
        ),
        rx.table.cell(lead.status_enriquecimento, color=colors.TEXT_SEC),
        align="center",
        # Linha clicável: abre o mesmo pop-up de detalhe usado em /leads e
        # /enriquecimento (mesmos dados, mesma view-model CompanyDetailUI).
        on_click=DashboardState.open_lead_detail(lead.id),
        cursor="pointer",
        _hover={"background": "rgba(29, 84, 140, 0.06)"},
    )


def _campo(rotulo, valor) -> rx.Component:
    return rx.cond(
        valor != "",
        rx.vstack(
            rx.text(rotulo, size="1", color=colors.TEXT_SEC, font_weight="600",
                    font_family=BODY_FONT, text_transform="uppercase", letter_spacing="0.04em"),
            rx.text(valor, size="2", color=colors.TEXT_MAIN, font_family=BODY_FONT),
            spacing="0",
            align_items="start",
        ),
    )


def _bloco(titulo: str, *filhos) -> rx.Component:
    return rx.vstack(
        rx.text(titulo, size="2", color=colors.HIGHLIGHT, font_weight="700", font_family=HEADING_FONT),
        rx.divider(margin_y="0.35rem"),
        *filhos,
        spacing="2",
        align_items="start",
        width="100%",
    )


def _grade_campos(*campos) -> rx.Component:
    return rx.grid(*campos, columns=rx.breakpoints(initial="1", sm="2"), spacing="3", width="100%")


def _detail_dialog() -> rx.Component:
    """Mesmo pop-up de detalhe usado em /leads (CompanyDetailUI reaproveitado)."""
    d = DashboardState.detail
    return rx.dialog.root(
        rx.dialog.content(
            rx.vstack(
                rx.dialog.title(
                    rx.cond(d.razao_social != "", d.razao_social, d.nome),
                    color=colors.TEXT_MAIN,
                    margin="0",
                ),
                rx.cond(
                    d.razao_social != "",
                    rx.text(d.nome, size="1", color=colors.TEXT_SEC, font_family=BODY_FONT),
                ),
                spacing="0",
                align_items="start",
                margin_bottom="0.5rem",
            ),
            rx.cond(
                d.alerta_situacao != "",
                rx.callout(
                    f"{d.alerta_situacao} desde {d.alerta_situacao_desde}",
                    icon="triangle-alert",
                    color_scheme="red",
                    size="1",
                    width="100%",
                    margin_bottom="0.75rem",
                ),
            ),
            rx.vstack(
                _bloco(
                    "Identificação",
                    _grade_campos(
                        _campo("CNPJ", d.cnpj),
                        _campo("Situação cadastral", d.status_cadastral),
                        _campo("Cidade / UF", d.cidade_uf),
                        _campo("Porte", d.porte),
                    ),
                ),
                _bloco(
                    "Canais de contato",
                    _grade_campos(
                        _campo(rx.cond(d.telefone_whatsapp, "Telefone (WhatsApp)", "Telefone"), d.telefone),
                        _campo("Website", d.website),
                        _campo("LinkedIn", d.linkedin_url),
                    ),
                ),
                _bloco(
                    f"Contatos decisores ({d.contatos.length()})",
                    rx.cond(
                        d.contatos.length() > 0,
                        rx.vstack(
                            rx.foreach(
                                d.contatos,
                                lambda ct: rx.vstack(
                                    rx.text(
                                        f"{ct.nome} · {ct.cargo} ({ct.origem})",
                                        size="2", color=colors.TEXT_SEC, font_family=BODY_FONT,
                                    ),
                                    # O e-mail só aparece quando existe: uma
                                    # linha vazia sob cada contato sugeriria que
                                    # a busca falhou, quando na maioria das
                                    # vezes ela nem chegou a rodar (cota).
                                    rx.cond(
                                        ct.email_label != "",
                                        rx.text(
                                            ct.email_label,
                                            size="1", color=colors.HIGHLIGHT,
                                            font_family=BODY_FONT,
                                        ),
                                    ),
                                    spacing="0", align_items="start", width="100%",
                                ),
                            ),
                            spacing="1", width="100%",
                        ),
                        rx.text("Nenhum contato decisor encontrado.", size="2", color=colors.TEXT_SEC, font_family=BODY_FONT),
                    ),
                ),
                _bloco(
                    "Enriquecimento",
                    _grade_campos(
                        _campo("Situação", d.status_label),
                        _campo("Concluído em", d.enriquecido_em),
                    ),
                ),
                spacing="5",
                width="100%",
                align_items="start",
            ),
            rx.flex(
                rx.dialog.close(
                    rx.button("Fechar", variant="soft", color_scheme="gray", cursor="pointer"),
                ),
                justify="end",
                margin_top="1.5rem",
            ),
            max_width="700px",
            max_height="85vh",
            overflow_y="auto",
            background=colors.CARD_BG,
        ),
        open=DashboardState.detail_open,
        on_open_change=DashboardState.set_detail_open,
    )


def _empty_state() -> rx.Component:
    return rx.hstack(
        rx.icon(tag="rocket", size=18, color="#b45309"),
        rx.text(
            "Ainda não há leads suficientes para gerar os indicadores. ",
            color="#92400e", size="2",
        ),
        rx.link("Ir para Pesquisa", href="/pesquisa", color=colors.HIGHLIGHT, size="2", font_weight="600"),
        align_items="center", spacing="2",
        background="#fef3c7", padding="0.75rem 1rem", border_radius="8px", width="100%",
    )


@rx.page(route="/dashboard", on_load=[AppState.load_dashboard, DashboardState.load_dashboard_data])
def dashboard_page() -> rx.Component:
    return dashboard_layout(
        rx.vstack(
            rx.vstack(
                rx.heading(f"Bem-vindo, {AppState.user_name}!", size="8", color=colors.TEXT_MAIN, font_family=HEADING_FONT),
                rx.text(
                    "Visão geral dos leads encontrados e enriquecidos em todas as pesquisas.",
                    color=colors.TEXT_SEC, font_family=BODY_FONT,
                ),
                spacing="1", align_items="start", width="100%", max_width="1200px", margin_bottom="1.5rem",
            ),

            rx.cond(DashboardState.sem_dados, _empty_state()),

            rx.cond(
                ~DashboardState.sem_dados,
                rx.vstack(
                    rx.grid(
                        admin_card("Leads encontrados", DashboardState.kpi_leads_encontrados.to_string(), "search"),
                        admin_card("Leads enriquecidos", DashboardState.kpi_leads_enriquecidos.to_string(), "database-zap"),
                        admin_card("Score médio de match ICP", f"{DashboardState.kpi_score_icp}/100", "target"),
                        columns=rx.breakpoints(initial="1", sm="3"),
                        spacing="4", width="100%", margin_bottom="1.5rem",
                    ),

                    rx.grid(
                        _donut_card("Leads por segmento", DashboardState.chart_segmento),
                        _donut_card("Leads por faixa de faturamento", DashboardState.chart_faturamento),
                        _donut_card("Leads por porte", DashboardState.chart_porte),
                        _donut_card("Leads por situação cadastral", DashboardState.chart_situacao),
                        columns=rx.breakpoints(initial="1", lg="2"),
                        spacing="4", width="100%", margin_bottom="1.5rem",
                    ),

                    rx.grid(
                        _map_card(),
                        _donut_card("Contatos decisores por cargo", DashboardState.chart_contatos_cargo, altura=380),
                        columns=rx.breakpoints(initial="1", lg="2"),
                        spacing="4", width="100%", margin_bottom="1.5rem",
                    ),

                    rx.hstack(
                        rx.text(
                            "Leads com maior potencial", size="5", font_weight="700",
                            color=colors.TEXT_MAIN, font_family=HEADING_FONT,
                        ),
                        rx.spacer(),
                        rx.select(
                            DashboardState.produto_options,
                            value=DashboardState.produto_filter,
                            on_change=DashboardState.set_produto_filter,
                            size="2",
                        ),
                        rx.select(
                            ["5", "10", "15", "todos"],
                            value=DashboardState.top_leads_limit,
                            on_change=DashboardState.set_top_leads_limit,
                            size="2",
                        ),
                        width="100%", align_items="center", margin_bottom="0.5rem",
                    ),
                    table_shell(
                        [
                            col("Empresa"), col("Segmento"), col("Porte"),
                            col("Score"), col("Prioridade"), col("Enriquecimento"),
                        ],
                        rx.foreach(DashboardState.top_leads, _lead_row),
                    ),

                    spacing="0", width="100%", align_items="start",
                ),
            ),

            _detail_dialog(),

            width="100%", max_width="1200px", align_items="start",
        )
    )
