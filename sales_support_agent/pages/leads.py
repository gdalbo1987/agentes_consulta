import reflex as rx

from sales_support_agent.components.confirm_dialog import confirm_dialog
from sales_support_agent.components.dashboard_layout import dashboard_layout
from sales_support_agent.components.data_table import col, table_shell
from sales_support_agent.state import LeadsState
from sales_support_agent.styles import colors
from sales_support_agent.styles.typography import HEADING_FONT, BODY_FONT


def _classe_badge(classe) -> rx.Component:
    return rx.badge(
        classe,
        color_scheme=rx.cond(
            classe == "Alta", "green",
            rx.cond(classe == "Média", "amber", rx.cond(classe == "Baixa", "red", "gray")),
        ),
    )


def _enriquecimento_badge(status_label) -> rx.Component:
    """Completo (verde) / Parcial (âmbar) / Falhou (vermelho) / Em andamento
    (azul) / Incompleto (cinza) — cada situação com sua própria cor, não só
    "Completo vs. resto"."""
    return rx.badge(
        status_label,
        color_scheme=rx.cond(
            status_label == "Completo", "green",
            rx.cond(
                status_label == "Parcial", "amber",
                rx.cond(
                    status_label == "Falhou", "red",
                    rx.cond(status_label == "Em andamento", "blue", "gray"),
                ),
            ),
        ),
    )


def _lead_row(lead) -> rx.Component:
    return rx.table.row(
        rx.table.cell(lead.nome, color=colors.TEXT_MAIN, font_weight="500"),
        rx.table.cell(lead.empresa, color=colors.TEXT_SEC),
        rx.table.cell(lead.cidade_uf, color=colors.TEXT_SEC),
        rx.table.cell(_enriquecimento_badge(lead.enrichment_status_label)),
        rx.table.cell(_classe_badge(lead.priorizacao_classe)),
        rx.table.cell(
            rx.icon_button(
                rx.icon(tag="trash-2", size=16),
                on_click=[
                    LeadsState.ask_delete_lead(lead.id),
                    rx.stop_propagation,
                ],
                variant="soft",
                color_scheme="red",
                size="1",
                cursor="pointer",
            ),
        ),
        align="center",
        # A linha (fora do botão de excluir) abre o detalhe.
        on_click=LeadsState.open_lead_detail_view(lead.id),
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
    """Mesmo padrão de pop-up de /enriquecimento (CompanyDetailUI reaproveitado)."""
    d = LeadsState.detail
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
        open=LeadsState.detail_open,
        on_open_change=LeadsState.set_detail_open,
    )


def _empty_state() -> rx.Component:
    return rx.hstack(
        rx.icon(tag="triangle-alert", size=18, color="#b45309"),
        rx.text(
            "Nenhum lead encontrado ainda. Rode uma pesquisa para começar. ",
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


@rx.page(route="/leads", on_load=LeadsState.load_leads)
def leads_page() -> rx.Component:
    return dashboard_layout(
        rx.vstack(
            rx.vstack(
                rx.heading("Lista de Leads", size="8", color=colors.TEXT_MAIN, font_family=HEADING_FONT),
                rx.text(
                    "Repositório central de todas as empresas já encontradas, independente da "
                    "pesquisa que as originou.",
                    color=colors.TEXT_SEC,
                    font_family=BODY_FONT,
                ),
                rx.cond(
                    ~LeadsState.sem_leads,
                    rx.hstack(
                        rx.text(
                            f"{LeadsState.total_leads} lead(s) no total.",
                            color=colors.TEXT_SEC,
                            size="2",
                            font_family=BODY_FONT,
                        ),
                        rx.spacer(),
                        # Os dois filtros são combináveis: produto E coletor.
                        rx.select(
                            LeadsState.usuario_options,
                            value=LeadsState.usuario_filter,
                            on_change=LeadsState.set_usuario_filter,
                            size="2",
                        ),
                        rx.select(
                            LeadsState.produto_options,
                            value=LeadsState.produto_filter,
                            on_change=LeadsState.set_produto_filter,
                            size="2",
                        ),
                        rx.button(
                            rx.icon(tag="user-x", size=16),
                            "Apagar todos os contatos",
                            on_click=LeadsState.set_bulk_delete_contatos_dialog_open(True),
                            color_scheme="red",
                            variant="soft",
                            size="2",
                            cursor="pointer",
                        ),
                        width="100%",
                        align_items="center",
                        margin_top="0.5rem",
                    ),
                ),
                spacing="1",
                align_items="start",
                width="100%",
                max_width="1200px",
                margin_bottom="1.5rem",
            ),
            rx.vstack(
                rx.cond(LeadsState.sem_leads, _empty_state()),
                rx.cond(
                    ~LeadsState.sem_leads,
                    table_shell(
                        [
                            col("Nome"), col("Empresa"), col("Cidade / UF"),
                            col("Enriquecimento"), col("Priorização"), col(""),
                        ],
                        rx.foreach(LeadsState.leads, _lead_row),
                    ),
                ),
                spacing="4",
                width="100%",
                max_width="1200px",
                align_items="start",
            ),
            _detail_dialog(),
            confirm_dialog(
                open_var=LeadsState.delete_dialog_open,
                on_open_change=LeadsState.set_delete_dialog_open,
                title="Excluir este lead?",
                body=(
                    "Isso apaga a empresa e todos os seus contatos decisores. "
                    "A ação não pode ser desfeita."
                ),
                confirm_label="Excluir",
                on_confirm=LeadsState.confirm_delete_lead,
            ),
            confirm_dialog(
                open_var=LeadsState.bulk_delete_contatos_dialog_open,
                on_open_change=LeadsState.set_bulk_delete_contatos_dialog_open,
                title="Apagar todos os contatos?",
                body=(
                    "Isso apaga todos os contatos decisores de TODAS as empresas "
                    "desta conta. As empresas em si NÃO são apagadas, só os "
                    "contatos. O percentual de enriquecimento de cada empresa é "
                    "recalculado. A ação não pode ser desfeita."
                ),
                confirm_label="Apagar contatos",
                on_confirm=LeadsState.confirm_bulk_delete_contatos,
            ),
            align_items="center",
            width="100%",
        )
    )
