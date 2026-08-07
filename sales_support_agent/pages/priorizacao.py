import reflex as rx

from sales_support_agent.components.dashboard_layout import dashboard_layout
from sales_support_agent.components.data_table import col, table_shell
from sales_support_agent.state import PriorizacaoState
from sales_support_agent.styles import colors
from sales_support_agent.styles.typography import HEADING_FONT, BODY_FONT


def _lead_row(lead) -> rx.Component:
    return rx.table.row(
        rx.table.cell(lead.nome, color=colors.TEXT_MAIN, font_weight="500"),
        rx.table.cell(lead.empresa, color=colors.TEXT_SEC),
        rx.table.cell(
            rx.cond(lead.classe != "", lead.score_final.to_string(), "-"),
            color=colors.TEXT_MAIN, font_weight="600",
        ),
        rx.table.cell(
            rx.cond(
                lead.classe != "",
                rx.badge(lead.classe, color_scheme=lead.classe_cor, size="2"),
                rx.badge("Não priorizado", color_scheme="gray", size="2"),
            ),
        ),
        align="center",
        # A linha inteira abre o detalhe, mesmo padrão de /enriquecimento.
        on_click=PriorizacaoState.open_lead_detail(lead.id),
        cursor="pointer",
        _hover={"background": "rgba(29, 84, 140, 0.06)"},
    )


def _status_panel() -> rx.Component:
    return rx.box(
        rx.hstack(
            rx.progress(
                value=PriorizacaoState.progress_atual,
                max=PriorizacaoState.progress_total,
                size="3",
                width="100%",
                border_radius="999px",
                style={
                    "background": "#dbeafe",
                    ".rt-ProgressIndicator": {"background": colors.BTN_GRADIENT},
                },
            ),
            rx.text(
                f"{PriorizacaoState.progress_atual} / {PriorizacaoState.progress_total} leads processados",
                color=colors.TEXT_SEC,
                size="2",
                font_weight="600",
                font_family=BODY_FONT,
                white_space="nowrap",
            ),
            align_items="center",
            spacing="3",
            width="100%",
            margin_bottom="0.75rem",
        ),
        rx.cond(
            PriorizacaoState.is_running,
            rx.hstack(
                rx.spinner(size="3", color=colors.HIGHLIGHT),
                rx.vstack(
                    rx.text(
                        "Priorizando leads...",
                        color=colors.TEXT_MAIN,
                        size="2",
                        font_weight="600",
                        font_family=BODY_FONT,
                    ),
                    rx.text(
                        PriorizacaoState.progress_message,
                        color=colors.TEXT_SEC,
                        size="1",
                        font_family=BODY_FONT,
                    ),
                    spacing="0",
                    align_items="start",
                ),
                align_items="center",
                spacing="3",
            ),
        ),
        rx.cond(
            PriorizacaoState.has_result,
            rx.hstack(
                rx.badge(
                    rx.cond(
                        PriorizacaoState.last_status == "error",
                        "Priorização com erro",
                        "Priorização concluída",
                    ),
                    color_scheme=rx.cond(PriorizacaoState.last_status == "error", "red", "green"),
                    size="2",
                ),
                rx.text(
                    f"{PriorizacaoState.last_run_at} - "
                    f"{PriorizacaoState.last_processados} processado(s), "
                    f"{PriorizacaoState.last_puladas} pulado(s), "
                    f"{PriorizacaoState.last_falhas} falha(s)",
                    color=colors.TEXT_SEC,
                    size="2",
                    font_family=BODY_FONT,
                ),
                align_items="center",
                spacing="3",
                wrap="wrap",
            ),
        ),
        background="#eef4fb",
        padding="1rem",
        border_radius="12px",
        border=f"1px solid {colors.BORDER}",
        width="100%",
    )


def _avisos_box() -> rx.Component:
    return rx.cond(
        PriorizacaoState.last_avisos.length() > 0,
        rx.box(
            rx.hstack(
                rx.icon(tag="info", size=16, color="#92400e"),
                rx.text("Avisos da priorização", color="#92400e", font_weight="600", size="2"),
                align_items="center",
                spacing="2",
                margin_bottom="0.4rem",
            ),
            rx.foreach(
                PriorizacaoState.last_avisos,
                lambda aviso: rx.text(
                    f"• {aviso}", color="#92400e", size="2", font_family=BODY_FONT, margin_bottom="0.15rem"
                ),
            ),
            background="#fef3c7",
            padding="0.75rem 1rem",
            border_radius="8px",
            width="100%",
        ),
    )


def _quota_callout() -> rx.Component:
    return rx.cond(
        PriorizacaoState.quota_reached,
        rx.callout(
            f"Você atingiu o seu limite de {PriorizacaoState.consulta_limit} consultas mensais. "
            "A cota é individual e renovada no início do próximo mês.",
            icon="circle-alert",
            color_scheme="amber",
            width="100%",
        ),
    )


def _error_callout() -> rx.Component:
    return rx.cond(
        PriorizacaoState.priorizacao_error != "",
        rx.callout(
            PriorizacaoState.priorizacao_error,
            icon="triangle-alert",
            color_scheme="red",
            width="100%",
        ),
    )


def _empty_state() -> rx.Component:
    return rx.hstack(
        rx.icon(tag="triangle-alert", size=18, color="#b45309"),
        rx.text(
            "Nenhum lead enriquecido ainda. Rode o enriquecimento antes de priorizar. ",
            color="#92400e",
            size="2",
        ),
        rx.link("Ir para Enriquecimento", href="/enriquecimento", color=colors.HIGHLIGHT, size="2", font_weight="600"),
        align_items="center",
        spacing="2",
        background="#fef3c7",
        padding="0.75rem 1rem",
        border_radius="8px",
        width="100%",
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


def _criterio_item(c) -> rx.Component:
    return rx.box(
        rx.hstack(
            rx.text(c.criterio, size="2", font_weight="600", color=colors.TEXT_MAIN),
            rx.badge(f"peso {c.peso}", color_scheme="gray", variant="soft"),
            rx.spacer(),
            rx.badge(f"{c.pontos} pts", color_scheme=rx.cond(c.pontos >= 25, "green", rx.cond(c.pontos > 0, "amber", "red"))),
            align_items="center",
            spacing="2",
            width="100%",
        ),
        rx.text(c.justificativa, size="2", color=colors.TEXT_SEC, font_family=BODY_FONT, margin_top="0.2rem"),
        background="#f5f9fe",
        padding="0.6rem 0.8rem",
        border_radius="8px",
        border=f"1px solid {colors.BORDER}",
        width="100%",
    )


def _dica_item(d) -> rx.Component:
    return rx.box(
        rx.text(d.tipo_label, size="1", color=colors.HIGHLIGHT, font_weight="700",
                text_transform="uppercase", letter_spacing="0.04em"),
        rx.text(d.dica, size="2", color=colors.TEXT_MAIN, font_family=BODY_FONT, margin_top="0.15rem"),
        background="#f5f9fe",
        padding="0.6rem 0.8rem",
        border_radius="8px",
        border=f"1px solid {colors.BORDER}",
        width="100%",
    )


def _detail_dialog() -> rx.Component:
    d = PriorizacaoState.detail
    empresa = d.empresa
    return rx.dialog.root(
        rx.dialog.content(
            rx.hstack(
                rx.vstack(
                    rx.dialog.title(
                        rx.cond(empresa.razao_social != "", empresa.razao_social, empresa.nome),
                        color=colors.TEXT_MAIN,
                        margin="0",
                    ),
                    rx.cond(
                        empresa.razao_social != "",
                        rx.text(empresa.nome, size="1", color=colors.TEXT_SEC, font_family=BODY_FONT),
                    ),
                    spacing="0",
                    align_items="start",
                ),
                rx.spacer(),
                rx.vstack(
                    rx.badge(f"Score {d.score_final}/100", color_scheme="blue", size="2"),
                    rx.cond(
                        d.classe != "",
                        rx.badge(
                            d.classe,
                            color_scheme=rx.cond(
                                d.classe == "Alta", "green",
                                rx.cond(d.classe == "Média", "amber", "red"),
                            ),
                            size="2",
                        ),
                    ),
                    spacing="1",
                    align_items="end",
                ),
                align_items="start",
                width="100%",
                margin_bottom="0.5rem",
            ),

            rx.cond(
                empresa.alerta_situacao != "",
                rx.callout(
                    f"{empresa.alerta_situacao} desde {empresa.alerta_situacao_desde}",
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
                        _campo("CNPJ", empresa.cnpj),
                        _campo("Situação cadastral", empresa.status_cadastral),
                        _campo("Cidade / UF", empresa.cidade_uf),
                        _campo("Porte", empresa.porte),
                    ),
                ),
                _bloco(
                    "Canais de contato",
                    _grade_campos(
                        _campo(rx.cond(empresa.telefone_whatsapp, "Telefone (WhatsApp)", "Telefone"), empresa.telefone),
                        _campo("Website", empresa.website),
                        _campo("LinkedIn", empresa.linkedin_url),
                    ),
                ),
                _bloco(
                    f"Contatos decisores ({empresa.contatos.length()})",
                    rx.cond(
                        empresa.contatos.length() > 0,
                        rx.vstack(
                            rx.foreach(
                                empresa.contatos,
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
                    "Breakdown dos 7 critérios",
                    rx.cond(
                        d.criterios.length() > 0,
                        rx.vstack(rx.foreach(d.criterios, _criterio_item), spacing="2", width="100%"),
                        rx.text("Este lead ainda não foi priorizado.", size="2", color=colors.TEXT_SEC, font_family=BODY_FONT),
                    ),
                ),
                rx.cond(
                    d.tem_approach,
                    _bloco(
                        "Dicas de approach",
                        rx.vstack(rx.foreach(d.dicas_approach, _dica_item), spacing="2", width="100%"),
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
            max_width="760px",
            max_height="85vh",
            overflow_y="auto",
            background=colors.CARD_BG,
        ),
        open=PriorizacaoState.detail_open,
        on_open_change=PriorizacaoState.set_detail_open,
    )


@rx.page(route="/priorizacao", on_load=PriorizacaoState.load_priorizacao)
def priorizacao_page() -> rx.Component:
    return dashboard_layout(
        rx.vstack(
            rx.vstack(
                rx.heading(
                    "Priorização de Leads",
                    size="8",
                    color=colors.TEXT_MAIN,
                    font_family=HEADING_FONT,
                ),
                rx.text(
                    "Pontua cada lead enriquecido em 7 critérios ponderados e sugere dicas de "
                    "primeiro contato.",
                    color=colors.TEXT_SEC,
                    font_family=BODY_FONT,
                ),
                rx.cond(
                    ~PriorizacaoState.sem_leads,
                    rx.text(
                        f"{PriorizacaoState.total_leads} lead(s) enriquecido(s) da pesquisa de "
                        f"{PriorizacaoState.origem_segmento} em {PriorizacaoState.origem_regiao}.",
                        color=colors.TEXT_SEC,
                        size="2",
                        font_family=BODY_FONT,
                        margin_top="0.5rem",
                    ),
                ),
                spacing="1",
                align_items="start",
                width="100%",
                max_width="1200px",
                margin_bottom="1rem",
            ),

            # Cota mensal — a MESMA de pesquisa/enriquecimento.
            rx.hstack(
                rx.progress(
                    value=PriorizacaoState.priorizacoes_this_month,
                    max=PriorizacaoState.consulta_limit,
                    size="3",
                    width="100%",
                    border_radius="999px",
                    style={
                        "background": "#dbeafe",
                        ".rt-ProgressIndicator": {"background": colors.BTN_GRADIENT},
                    },
                ),
                rx.text(
                    f"{PriorizacaoState.priorizacoes_this_month} / {PriorizacaoState.consulta_limit} "
                    "priorizações este mês",
                    color=colors.TEXT_SEC,
                    size="2",
                    font_weight="600",
                    font_family=BODY_FONT,
                    white_space="nowrap",
                ),
                align_items="center",
                spacing="3",
                width="100%",
                max_width="1200px",
                margin_bottom="1.5rem",
            ),

            rx.vstack(
                rx.cond(PriorizacaoState.sem_leads, _empty_state()),
                _quota_callout(),
                rx.hstack(
                    rx.button(
                        rx.icon(tag="target", size=16),
                        rx.cond(PriorizacaoState.is_running, "Priorizando...", "Iniciar priorização"),
                        on_click=PriorizacaoState.start_priorizacao,
                        loading=PriorizacaoState.is_running,
                        disabled=(
                            PriorizacaoState.is_running
                            | PriorizacaoState.sem_leads
                            | PriorizacaoState.quota_reached
                        ),
                        size="3",
                        background=colors.BTN_GRADIENT,
                        color="white",
                        cursor="pointer",
                        border_radius="8px",
                        font_weight="600",
                        width="fit-content",
                    ),
                    rx.hstack(
                        rx.checkbox(
                            checked=PriorizacaoState.incluir_approach,
                            on_change=PriorizacaoState.set_incluir_approach,
                            size="2",
                        ),
                        rx.text("Incluir approach (dicas de primeiro contato)", size="2", color=colors.TEXT_SEC, font_family=BODY_FONT),
                        align_items="center",
                        spacing="2",
                    ),
                    align_items="center",
                    spacing="4",
                    wrap="wrap",
                ),
                rx.cond(
                    ~PriorizacaoState.sem_leads,
                    rx.button(
                        rx.icon(tag="file-text", size=16),
                        "Baixar relatório (.pdf)",
                        on_click=PriorizacaoState.export_report,
                        size="3",
                        variant="outline",
                        color=colors.HIGHLIGHT,
                        border=f"1px solid {colors.HIGHLIGHT}",
                        cursor="pointer",
                        border_radius="8px",
                        font_weight="600",
                        width="fit-content",
                    ),
                ),
                rx.cond(~PriorizacaoState.sem_leads, _status_panel()),
                _error_callout(),
                _avisos_box(),
                rx.cond(
                    ~PriorizacaoState.sem_leads,
                    table_shell(
                        [col("Nome"), col("Empresa"), col("Score final"), col("Classe")],
                        rx.foreach(PriorizacaoState.companies, _lead_row),
                    ),
                ),
                spacing="4",
                width="100%",
                max_width="1200px",
                align_items="start",
            ),
            _detail_dialog(),
            align_items="center",
            width="100%",
        )
    )
