import reflex as rx

from prospect_agent.components.confirm_dialog import confirm_dialog
from prospect_agent.components.dashboard_layout import dashboard_layout
from prospect_agent.components.data_table import col, table_shell
from prospect_agent.state import EnrichmentState
from prospect_agent.styles import colors
from prospect_agent.styles.typography import HEADING_FONT, BODY_FONT


def _badge_percentual(p) -> rx.Component:
    """Percentual de enriquecimento: badge colorido + mini barra.

    100% = todos os 12 campos preenchidos. O valor vem calculado e persistido
    pelo backend (services/enrichment_rules.calcular_percentual) — a UI só lê.
    """
    return rx.vstack(
        rx.badge(
            f"{p}%",
            color_scheme=rx.cond(p >= 100, "green", rx.cond(p > 0, "amber", "gray")),
            size="2",
        ),
        rx.progress(
            value=p,
            max=100,
            size="1",
            width="80px",
            border_radius="999px",
            style={
                "background": "#dbeafe",
                ".rt-ProgressIndicator": {"background": colors.BTN_GRADIENT},
            },
        ),
        spacing="1",
        align_items="start",
    )


def _company_row(c) -> rx.Component:
    return rx.table.row(
        rx.table.cell(c.nome, color=colors.TEXT_MAIN, font_weight="500"),
        rx.table.cell(c.cnpj, color=colors.TEXT_SEC),
        rx.table.cell(c.cidade_uf, color=colors.TEXT_SEC),
        rx.table.cell(c.porte, color=colors.TEXT_SEC),
        rx.table.cell(
            rx.vstack(
                rx.badge(
                    c.status_cadastral,
                    color_scheme=rx.cond(c.status_cadastral == "Ativa", "green", "gray"),
                ),
                # A Receita mantém "ATIVA" durante recuperação judicial, então
                # o alerta vai como um segundo badge, em vermelho.
                rx.cond(
                    c.alerta_situacao != "",
                    rx.badge(c.alerta_situacao, color_scheme="red", variant="solid"),
                ),
                spacing="1",
                align_items="start",
            ),
        ),
        # nowrap: sem isso "(21) 2156-3600" quebra no meio, em duas linhas.
        rx.table.cell(c.telefone, color=colors.TEXT_SEC, white_space="nowrap"),
        rx.table.cell(c.contatos_label, color=colors.TEXT_SEC),
        rx.table.cell(_badge_percentual(c.percentual)),
        align="center",
        # A linha inteira abre o detalhe: alvo de clique grande, sem competir
        # com um botão de "ver" numa coluna extra.
        on_click=EnrichmentState.open_company_detail(c.id),
        cursor="pointer",
        _hover={"background": "rgba(29, 84, 140, 0.06)"},
    )


def _status_panel() -> rx.Component:
    """Painel de progresso do processo (N/M empresas, custo e mensagem atual)."""
    return rx.box(
        rx.hstack(
            rx.progress(
                value=EnrichmentState.progress_atual,
                max=EnrichmentState.progress_total,
                size="3",
                width="100%",
                border_radius="999px",
                style={
                    "background": "#dbeafe",
                    ".rt-ProgressIndicator": {"background": colors.BTN_GRADIENT},
                },
            ),
            rx.text(
                f"{EnrichmentState.progress_atual} / {EnrichmentState.progress_total} empresas processadas",
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
            EnrichmentState.is_running,
            rx.hstack(
                rx.spinner(size="3", color=colors.HIGHLIGHT),
                rx.vstack(
                    rx.text(
                        "Enriquecendo dados...",
                        color=colors.TEXT_MAIN,
                        size="2",
                        font_weight="600",
                        font_family=BODY_FONT,
                    ),
                    rx.text(
                        EnrichmentState.progress_message,
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
            EnrichmentState.has_result,
            rx.hstack(
                rx.badge(
                    rx.cond(
                        EnrichmentState.last_status == "error",
                        "Enriquecimento com erro",
                        "Enriquecimento concluído",
                    ),
                    color_scheme=rx.cond(EnrichmentState.last_status == "error", "red", "green"),
                    size="2",
                ),
                rx.text(
                    f"{EnrichmentState.last_run_at} - "
                    f"{EnrichmentState.last_processadas} processada(s), "
                    f"{EnrichmentState.last_puladas} pulada(s) " ,
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
        EnrichmentState.last_avisos.length() > 0,
        rx.box(
            rx.hstack(
                rx.icon(tag="info", size=16, color="#92400e"),
                rx.text("Avisos do enriquecimento", color="#92400e", font_weight="600", size="2"),
                align_items="center",
                spacing="2",
                margin_bottom="0.4rem",
            ),
            rx.foreach(
                EnrichmentState.last_avisos,
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
    """Sem isso o botão só fica cinza e o usuário não sabe por quê."""
    return rx.cond(
        EnrichmentState.quota_reached,
        rx.callout(
            f"Você atingiu o seu limite de {EnrichmentState.consulta_limit} consultas mensais. "
            "A cota é individual e renovada no início do próximo mês.",
            icon="circle-alert",
            color_scheme="amber",
            width="100%",
        ),
    )


def _error_callout() -> rx.Component:
    return rx.cond(
        EnrichmentState.enrichment_error != "",
        rx.callout(
            EnrichmentState.enrichment_error,
            icon="triangle-alert",
            color_scheme="red",
            width="100%",
        ),
    )


def _campo(rotulo, valor) -> rx.Component:
    """Par rótulo/valor. Some quando vazio: campo em branco não informa nada e
    ainda faz o pop-up parecer cheio de buracos."""
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
        rx.text(titulo, size="2", color=colors.HIGHLIGHT, font_weight="700",
                font_family=HEADING_FONT),
        rx.divider(margin_y="0.35rem"),
        *filhos,
        spacing="2",
        align_items="start",
        width="100%",
    )


def _grade_campos(*campos) -> rx.Component:
    return rx.grid(
        *campos,
        columns=rx.breakpoints(initial="1", sm="2"),
        spacing="3",
        width="100%",
    )


def _contato_item(ct) -> rx.Component:
    return rx.box(
        rx.hstack(
            rx.text(ct.nome, size="2", font_weight="600", color=colors.TEXT_MAIN),
            rx.badge(ct.origem, color_scheme="blue", variant="soft"),
            spacing="2",
            align_items="center",
            wrap="wrap",
        ),
        rx.text(ct.cargo, size="2", color=colors.TEXT_SEC, font_family=BODY_FONT),
        # E-mail clicável (mailto): esta é a tela em que o vendedor age sobre o
        # contato, então vale o link, e não só o texto do rótulo.
        rx.cond(
            ct.email != "",
            rx.link(ct.email_label, href=f"mailto:{ct.email}",
                    size="1", color=colors.HIGHLIGHT, font_family=BODY_FONT),
        ),
        rx.cond(
            ct.perfil_url != "",
            rx.link("Ver perfil", href=ct.perfil_url, is_external=True,
                    size="1", color=colors.HIGHLIGHT),
        ),
        background="#f5f9fe",
        padding="0.6rem 0.8rem",
        border_radius="8px",
        border=f"1px solid {colors.BORDER}",
        width="100%",
    )


def _noticia_item(n) -> rx.Component:
    return rx.box(
        rx.hstack(
            rx.icon(tag="newspaper", size=14, color=colors.HIGHLIGHT),
            rx.text(n.data, size="1", color=colors.TEXT_SEC, font_weight="600"),
            spacing="2",
            align_items="center",
        ),
        rx.text(n.titulo, size="2", font_weight="600", color=colors.TEXT_MAIN,
                font_family=BODY_FONT, margin_y="0.2rem"),
        rx.text(n.resumo, size="2", color=colors.TEXT_SEC, font_family=BODY_FONT),
        rx.cond(
            n.url != "",
            rx.link("Abrir notícia", href=n.url, is_external=True,
                    size="1", color=colors.HIGHLIGHT),
        ),
        background="#f5f9fe",
        padding="0.7rem 0.9rem",
        border_radius="8px",
        border=f"1px solid {colors.BORDER}",
        width="100%",
    )


def _detail_dialog() -> rx.Component:
    """Pop-up com tudo que foi coletado sobre a empresa clicada."""
    d = EnrichmentState.detail
    return rx.dialog.root(
        rx.dialog.content(
            rx.hstack(
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
                ),
                rx.spacer(),
                _badge_percentual(d.percentual),
                align_items="start",
                width="100%",
                margin_bottom="0.5rem",
            ),

            # Alerta de risco em primeiro lugar: recuperação judicial muda a
            # decisão de abordar, então não pode ficar enterrado no meio.
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
                        _campo("Início de atividade", d.data_inicio_atividade),
                        _campo("Idade da empresa", d.idade_empresa),
                    ),
                ),
                _bloco(
                    "Localização",
                    _grade_campos(
                        _campo("Endereço", d.endereco_completo),
                        _campo("Cidade / UF", d.cidade_uf),
                        _campo("CEP", d.cep),
                    ),
                ),
                _bloco(
                    "Perfil comercial",
                    _grade_campos(
                        _campo("Porte", d.porte),
                        _campo("Faturamento estimado", d.faturamento_estimado),
                        _campo("Segmento", d.segmento),
                    ),
                ),
                _bloco(
                    "Canais de contato",
                    _grade_campos(
                        _campo(
                            rx.cond(d.telefone_whatsapp, "Telefone (WhatsApp)", "Telefone"),
                            d.telefone,
                        ),
                        _campo("Website", d.website),
                        _campo("LinkedIn", d.linkedin_url),
                    ),
                ),
                _bloco(
                    f"Contatos decisores ({d.contatos.length()})",
                    rx.cond(
                        d.contatos.length() > 0,
                        rx.vstack(rx.foreach(d.contatos, _contato_item),
                                  spacing="2", width="100%"),
                        rx.text("Nenhum contato decisor encontrado.",
                                size="2", color=colors.TEXT_SEC, font_family=BODY_FONT),
                    ),
                ),
                _bloco(
                    f"Notícias recentes ({d.noticias.length()})",
                    rx.cond(
                        d.noticias.length() > 0,
                        rx.vstack(rx.foreach(d.noticias, _noticia_item),
                                  spacing="2", width="100%"),
                        rx.text("Nenhuma notícia encontrada na pesquisa.",
                                size="2", color=colors.TEXT_SEC, font_family=BODY_FONT),
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
            max_width="760px",
            # O conteúdo varia muito (uma empresa com 5 notícias é longa):
            # rola dentro do próprio diálogo em vez de estourar a viewport.
            max_height="85vh",
            overflow_y="auto",
            background=colors.CARD_BG,
        ),
        open=EnrichmentState.detail_open,
        on_open_change=EnrichmentState.set_detail_open,
    )


def _empty_state() -> rx.Component:
    return rx.hstack(
        rx.icon(tag="triangle-alert", size=18, color="#b45309"),
        rx.text(
            "Nenhuma pesquisa concluída ainda. Rode uma pesquisa antes de enriquecer. ",
            color="#92400e",
            size="2",
        ),
        rx.link("Ir para Pesquisa", href="/pesquisa", color=colors.HIGHLIGHT, size="2", font_weight="600"),
        align_items="center",
        spacing="2",
        background="#fef3c7",
        padding="0.75rem 1rem",
        border_radius="8px",
        width="100%",
    )


@rx.page(route="/enriquecimento", on_load=EnrichmentState.load_enrichment)
def enrichment_page() -> rx.Component:
    return dashboard_layout(
        rx.vstack(
            rx.vstack(
                rx.heading(
                    "Enriquecimento de Empresas",
                    size="8",
                    color=colors.TEXT_MAIN,
                    font_family=HEADING_FONT,
                ),
                rx.text(
                    "Completa dados cadastrais, comerciais e contatos decisores das empresas "
                    "encontradas na pesquisa.",
                    color=colors.TEXT_SEC,
                    font_family=BODY_FONT,
                ),
                rx.cond(
                    ~EnrichmentState.sem_empresas,
                    rx.text(
                        f"{EnrichmentState.total_empresas} empresa(s) da pesquisa de "
                        f"{EnrichmentState.origem_segmento} em {EnrichmentState.origem_regiao} · "
                        f"até {EnrichmentState.contact_limit} contato(s) por empresa.",
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

            # Cota mensal — é a MESMA do plano na fase de pesquisa
            # (PLAN_CONSULTA_LIMITS): 5 no Smart, 15 no Smart Plus.
            rx.hstack(
                rx.progress(
                    value=EnrichmentState.enrichments_this_month,
                    max=EnrichmentState.consulta_limit,
                    size="3",
                    width="100%",
                    border_radius="999px",
                    style={
                        "background": "#dbeafe",
                        ".rt-ProgressIndicator": {"background": colors.BTN_GRADIENT},
                    },
                ),
                rx.text(
                    f"{EnrichmentState.enrichments_this_month} / {EnrichmentState.consulta_limit} "
                    "enriquecimentos este mês",
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
                rx.cond(EnrichmentState.sem_empresas, _empty_state()),
                _quota_callout(),
                rx.button(
                    rx.icon(tag="database-zap", size=16),
                    rx.cond(EnrichmentState.is_running, "Enriquecendo...", "Iniciar Enriquecimento"),
                    on_click=EnrichmentState.start_enrichment,
                    loading=EnrichmentState.is_running,
                    disabled=(
                        EnrichmentState.is_running
                        | EnrichmentState.sem_empresas
                        | EnrichmentState.quota_reached
                    ),
                    size="3",
                    background=colors.BTN_GRADIENT,
                    color="white",
                    cursor="pointer",
                    border_radius="8px",
                    font_weight="600",
                    width="fit-content",
                ),
                rx.cond(
                    ~EnrichmentState.sem_empresas,
                    rx.hstack(
                        rx.button(
                            rx.icon(tag="file-spreadsheet", size=16),
                            "Baixar relatório (.xlsx)",
                            on_click=EnrichmentState.export_report,
                            size="3",
                            variant="outline",
                            color=colors.HIGHLIGHT,
                            border=f"1px solid {colors.HIGHLIGHT}",
                            cursor="pointer",
                            border_radius="8px",
                            font_weight="600",
                        ),
                        rx.text(
                            "Empresas, contatos e notícias em três abas.",
                            color=colors.TEXT_SEC,
                            size="1",
                            font_family=BODY_FONT,
                        ),
                        rx.spacer(),
                        rx.button(
                            rx.icon(tag="trash-2", size=16),
                            "Excluir empresas sem enriquecimento",
                            on_click=EnrichmentState.set_bulk_delete_dialog_open(True),
                            size="3",
                            variant="soft",
                            color_scheme="red",
                            cursor="pointer",
                            border_radius="8px",
                            font_weight="600",
                        ),
                        align_items="center",
                        spacing="3",
                        wrap="wrap",
                        width="100%",
                    ),
                ),
                rx.cond(~EnrichmentState.sem_empresas, _status_panel()),
                _error_callout(),
                _avisos_box(),
                rx.cond(
                    ~EnrichmentState.sem_empresas,
                    table_shell(
                        [
                            col("Empresa"),
                            col("CNPJ"),
                            col("Cidade / UF"),
                            col("Porte"),
                            col("Status"),
                            col("Telefone"),
                            col("Contatos"),
                            col("% enriquecido"),
                        ],  # a linha inteira é clicável: abre o detalhe
                        rx.foreach(EnrichmentState.companies, _company_row),
                    ),
                ),
                rx.button(
                    rx.icon(tag="arrow-right", size=16),
                    "Avançar para próxima fase",
                    on_click=EnrichmentState.advance_to_next_phase,
                    # Mesma regra da fase anterior: basta ter uma execução que não
                    # falhou — enriquecimento parcial não impede o avanço.
                    disabled=~EnrichmentState.has_result | (EnrichmentState.last_status == "error"),
                    size="3",
                    background=colors.BTN_GRADIENT,
                    color="white",
                    cursor="pointer",
                    border_radius="8px",
                    font_weight="600",
                    width="fit-content",
                ),
                spacing="4",
                width="100%",
                max_width="1200px",
                align_items="start",
            ),
            _detail_dialog(),
            confirm_dialog(
                open_var=EnrichmentState.bulk_delete_dialog_open,
                on_open_change=EnrichmentState.set_bulk_delete_dialog_open,
                title="Excluir empresas sem enriquecimento?",
                body=(
                    "Isso apaga as empresas desta pesquisa que ainda estão em 0% de "
                    "enriquecimento (nenhum dado coletado). Leads parciais ou "
                    "completos não são afetados. A ação não pode ser desfeita."
                ),
                confirm_label="Excluir",
                on_confirm=EnrichmentState.confirm_bulk_delete_zeradas,
            ),
            align_items="center",
            width="100%",
        )
    )
