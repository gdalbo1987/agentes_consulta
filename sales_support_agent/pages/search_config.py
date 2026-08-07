import reflex as rx
from sales_support_agent.components.confirm_dialog import DIALOG_BG, dialog_cancel_button
from sales_support_agent.components.dashboard_layout import dashboard_layout
from sales_support_agent.state import SearchState
from sales_support_agent.styles import colors
from sales_support_agent.styles.typography import HEADING_FONT, BODY_FONT


def _field_label(text: str) -> rx.Component:
    return rx.text(text, size="2", font_weight="600", color=colors.TEXT_MAIN, margin_bottom="-0.2rem")


def _product_option(p) -> rx.Component:
    return rx.hstack(
        rx.checkbox(
            checked=p.selected,
            on_change=lambda _: SearchState.toggle_product(p.id),
            size="2",
        ),
        rx.vstack(
            rx.text(p.name, color=colors.TEXT_MAIN, font_weight="600", size="2"),
            spacing="0",
            align_items="start",
        ),
        align_items="center",
        spacing="3",
        width="100%",
        padding="0.75rem 1rem",
        border_radius="8px",
        border=f"1px solid {colors.BORDER}",
        background=colors.CARD_BG,
    )


def _products_card() -> rx.Component:
    return rx.box(
        rx.heading("Produtos", size="4", color=colors.TEXT_MAIN, font_family=HEADING_FONT, margin_bottom="0.5rem"),
        rx.text(
            "Selecione um ou mais produtos para orientar a pesquisa de prospecção.",
            color=colors.TEXT_SEC,
            font_family=BODY_FONT,
            size="2",
            margin_bottom="1rem",
        ),
        rx.cond(
            SearchState.available_products.length() > 0,
            rx.vstack(
                rx.foreach(SearchState.available_products, _product_option),
                spacing="2",
                width="100%",
            ),
            rx.hstack(
                rx.icon(tag="triangle-alert", size=18, color="#b45309"),
                rx.text(
                    "Nenhum produto cadastrado ainda. ",
                    color="#92400e",
                    size="2",
                ),
                rx.link("Cadastrar produto", href="/produtos", color=colors.HIGHLIGHT, size="2", font_weight="600"),
                align_items="center",
                spacing="2",
                background="#fef3c7",
                padding="0.75rem 1rem",
                border_radius="8px",
                width="100%",
            ),
        ),
        background=colors.CARD_BG,
        padding="1.5rem",
        border_radius="16px",
        border=f"1px solid {colors.BORDER}",
        width="100%",
    )


def _params_card() -> rx.Component:
    return rx.box(
        rx.heading("Parâmetros da pesquisa", size="4", color=colors.TEXT_MAIN, font_family=HEADING_FONT, margin_bottom="1rem"),
        rx.vstack(
            _field_label("Região de interesse"),
            rx.input(
                placeholder="Ex.: Brasil, Sul, Rio Grande do Sul, Caxias do Sul",
                value=SearchState.regiao,
                on_change=SearchState.set_regiao,
                size="3",
                width="100%",
                variant="surface",
                color="#111827",
                background_color="#e9e8e8",
                _placeholder={"color": "#3a3a3b"},
            ),
            width="100%",
            align_items="start",
            spacing="1",
            margin_bottom="1rem",
        ),
        rx.vstack(
            _field_label("Segmento estratégico"),
            rx.input(
                placeholder="Ex.: indústria metalúrgica, setor automotivo",
                value=SearchState.segmento,
                on_change=SearchState.set_segmento,
                size="3",
                width="100%",
                variant="surface",
                color="#111827",
                background_color="#e9e8e8",
                _placeholder={"color": "#3a3a3b"},
            ),
            width="100%",
            align_items="start",
            spacing="1",
        ),
        background=colors.CARD_BG,
        padding="1.5rem",
        border_radius="16px",
        border=f"1px solid {colors.BORDER}",
        width="100%",
    )


def _progress_box() -> rx.Component:
    return rx.cond(
        SearchState.is_running,
        rx.box(
            rx.hstack(
                rx.spinner(size="3", color=colors.HIGHLIGHT),
                rx.vstack(
                    rx.text(
                        "O agente de prospecção está trabalhando...",
                        color=colors.TEXT_MAIN,
                        size="2",
                        font_weight="600",
                        font_family=BODY_FONT,
                    ),
                    rx.text(
                        SearchState.progress_message,
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
            background="#eef4fb",
            padding="0.75rem 1rem",
            border_radius="8px",
            border=f"1px solid {colors.BORDER}",
            width="100%",
        ),
    )


def _result_card() -> rx.Component:
    return rx.cond(
        SearchState.has_result,
        rx.box(
            rx.hstack(
                rx.badge(
                    rx.cond(SearchState.last_status == "error", "Pesquisa com erro", "Pesquisa finalizada"),
                    color_scheme=rx.cond(SearchState.last_status == "error", "red", "green"),
                    size="2",
                ),
                rx.cond(
                    SearchState.last_status != "error",
                    rx.badge(
                        rx.cond(SearchState.last_meta_atingida, "Meta atingida", "Meta não atingida"),
                        color_scheme=rx.cond(SearchState.last_meta_atingida, "blue", "amber"),
                        size="2",
                    ),
                ),
                spacing="2",
                margin_bottom="0.75rem",
            ),
            rx.text(
                f"Última pesquisa em {SearchState.last_search_at} - "
                f"{SearchState.last_total_empresas} empresas encontradas.",
                color=colors.TEXT_SEC,
                size="2",
                font_family=BODY_FONT,
                margin_bottom="0.75rem",
            ),
            rx.cond(
                SearchState.last_resumo != "",
                rx.box(
                    rx.text("Resumo da pesquisa", color=colors.TEXT_MAIN, font_weight="600", size="2", margin_bottom="0.25rem"),
                    rx.text(SearchState.last_resumo, color=colors.TEXT_SEC, size="2", font_family=BODY_FONT),
                    margin_bottom="1rem",
                ),
            ),
            rx.cond(
                SearchState.last_avisos.length() > 0,
                rx.box(
                    rx.hstack(
                        rx.icon(tag="info", size=16, color="#92400e"),
                        rx.text("Avisos da pesquisa", color="#92400e", font_weight="600", size="2"),
                        align_items="center",
                        spacing="2",
                        margin_bottom="0.4rem",
                    ),
                    rx.foreach(
                        SearchState.last_avisos,
                        lambda aviso: rx.text(f"• {aviso}", color="#92400e", size="2", font_family=BODY_FONT, margin_bottom="0.15rem"),
                    ),
                    background="#fef3c7",
                    padding="0.75rem 1rem",
                    border_radius="8px",
                    margin_bottom="1rem",
                ),
            ),           
            rx.button(
                rx.icon(tag="arrow-right", size=16),
                "Avançar para próxima etapa",
                on_click=SearchState.advance_to_next_step,
                disabled=~SearchState.has_result | (SearchState.last_status == "error"),
                size="3",
                background=colors.BTN_GRADIENT,
                color="white",
                cursor="pointer",
                border_radius="8px",
                font_weight="600",
            ),
            background=colors.CARD_BG,
            padding="1.5rem",
            border_radius="16px",
            border=f"1px solid {colors.BORDER}",
            width="100%",
        ),
    )


def _opcao_confirmacao(
    titulo: str, descricao: str, icone: str, on_click, destaque: bool
) -> rx.Component:
    """Uma das duas escolhas do diálogo, como bloco clicável inteiro.

    Bloco, e não um par de botões pequenos: a diferença entre as opções está na
    consequência (custo, tempo e o que sai no resultado), e isso não cabe num
    rótulo de botão.
    """
    return rx.box(
        rx.hstack(
            rx.icon(tag=icone, size=18, color="white"),
            rx.vstack(
                rx.text(titulo, color="white", font_weight="600", size="2"),
                rx.text(descricao, color="rgba(255, 255, 255, 0.75)", size="1", font_family=BODY_FONT),
                spacing="1",
                align_items="start",
            ),
            align_items="start",
            spacing="3",
        ),
        on_click=on_click,
        cursor="pointer",
        padding="0.9rem 1rem",
        border_radius="10px",
        width="100%",
        background=rx.cond(destaque, "rgba(59, 130, 246, 0.22)", "rgba(255, 255, 255, 0.06)"),
        border=rx.cond(
            destaque,
            "1px solid rgba(147, 197, 253, 0.55)",
            "1px solid rgba(255, 255, 255, 0.22)",
        ),
        _hover={"background": "rgba(255, 255, 255, 0.16)"},
    )


def _confirm_dialog() -> rx.Component:
    """Pergunta o que fazer com as empresas que a base já tem.

    A pergunta é feita a cada execução (e não guardada como preferência) porque
    a resposta certa muda: numa rodada o objetivo é ampliar a lista, na seguinte
    é atualizar o que já se tem antes de abordar.
    """
    return rx.alert_dialog.root(
        rx.alert_dialog.content(
            rx.alert_dialog.title("Empresas já encontradas", color="white"),
            rx.alert_dialog.description(
                rx.cond(
                    SearchState.empresas_na_base > 0,
                    rx.text(
                        f"A base já tem {SearchState.empresas_na_base} empresa(s). "
                        "O que fazer com as que pertencem a esta linha de pesquisa?",
                        color="rgba(255, 255, 255, 0.75)",
                    ),
                    rx.text(
                        "A base ainda não tem empresas, então esta pesquisa vai "
                        "trazer só resultados novos de qualquer forma.",
                        color="rgba(255, 255, 255, 0.75)",
                    ),
                ),
            ),
            rx.vstack(
                _opcao_confirmacao(
                    "Apenas empresas novas",
                    "A busca ignora o que a base já tem e usa todo o orçamento "
                    "procurando empresas inéditas. As notícias das empresas antigas "
                    "continuam as da última pesquisa.",
                    "sparkles",
                    SearchState.start_search(False),
                    destaque=False,
                ),
                _opcao_confirmacao(
                    "Incluir as empresas já encontradas",
                    "Além das inéditas, as empresas que a base já tem nesta mesma "
                    "linha de pesquisa voltam ao resultado com as notícias buscadas "
                    "de novo. A rodada demora mais, proporcionalmente a quantas forem.",
                    "refresh-cw",
                    SearchState.start_search(True),
                    destaque=True,
                ),
                spacing="2",
                width="100%",
                margin_top="1.25rem",
            ),
            rx.flex(
                rx.alert_dialog.cancel(dialog_cancel_button()),
                margin_top="1.25rem",
                justify="end",
            ),
            max_width="520px",
            background=DIALOG_BG,
        ),
        open=SearchState.confirm_open,
        on_open_change=SearchState.set_confirm_open,
    )


def _error_callout() -> rx.Component:
    return rx.cond(
        SearchState.search_error != "",
        rx.callout(
            SearchState.search_error,
            icon="triangle-alert",
            color_scheme="red",
            width="100%",
        ),
    )


@rx.page(route="/pesquisa", on_load=SearchState.load_search_config)
def search_config_page() -> rx.Component:
    return dashboard_layout(
        rx.vstack(
            rx.vstack(
                rx.heading("Configuração de Pesquisa", size="8", color=colors.TEXT_MAIN, font_family=HEADING_FONT),
                rx.text(
                    "Encontre empresas com potencial de compra (ICP) e notícias recentes para abordagem comercial.",
                    color=colors.TEXT_SEC,
                    font_family=BODY_FONT,
                ),
                spacing="1",
                align_items="start",
                width="100%",
                max_width="800px",
                margin_bottom="1rem",
            ),

            # Cota mensal
            rx.hstack(
                rx.progress(
                    value=SearchState.searches_this_month,
                    max=SearchState.search_limit,
                    size="3",
                    width="100%",
                    border_radius="999px",
                    style={
                        "background": "#dbeafe",
                        ".rt-ProgressIndicator": {"background": colors.BTN_GRADIENT},
                    },
                ),
                rx.text(
                    f"{SearchState.searches_this_month} / {SearchState.search_limit} pesquisas este mês",
                    color=colors.TEXT_SEC,
                    size="2",
                    font_weight="600",
                    font_family=BODY_FONT,
                    white_space="nowrap",
                ),
                align_items="center",
                spacing="3",
                width="100%",
                max_width="800px",
                margin_bottom="2rem",
            ),

            rx.vstack(
                _products_card(),
                _params_card(),
                # Sem isso o botão só fica cinza e o usuário não sabe por quê.
                rx.cond(
                    SearchState.quota_reached,
                    rx.callout(
                        f"Você atingiu o seu limite de {SearchState.search_limit} consultas mensais. "
                        "A cota é individual e renovada no início do próximo mês.",
                        icon="circle-alert",
                        color_scheme="amber",
                        width="100%",
                    ),
                ),
                rx.button(
                    rx.icon(tag="search", size=16),
                    rx.cond(SearchState.is_running, "Pesquisando...", "Executar pesquisa"),
                    # Abre a confirmação; quem dispara a pesquisa é o diálogo.
                    on_click=SearchState.abrir_confirmacao,
                    loading=SearchState.is_running,
                    disabled=SearchState.is_running | SearchState.quota_reached,
                    size="3",
                    background=colors.BTN_GRADIENT,
                    color="white",
                    cursor="pointer",
                    border_radius="8px",
                    font_weight="600",
                    width="fit-content",
                ),
                _confirm_dialog(),
                _progress_box(),
                _error_callout(),
                _result_card(),
                spacing="4",
                width="100%",
                max_width="800px",
                align_items="start",
            ),

            align_items="center",
            width="100%",
        )
    )
