import reflex as rx
from sales_support_agent.components.dashboard_layout import dashboard_layout
from sales_support_agent.state import ProductState
from sales_support_agent.styles import colors
from sales_support_agent.styles.typography import HEADING_FONT, BODY_FONT


def _field_label(text: str) -> rx.Component:
    return rx.text(text, size="2", font_weight="600", color=colors.TEXT_MAIN, margin_bottom="-0.2rem")


def _product_card(product) -> rx.Component:
    """Card de um produto já cadastrado, com ações de editar/excluir."""
    return rx.box(
        rx.hstack(
            rx.vstack(
                rx.heading(product.name, size="4", font_family=HEADING_FONT, color=colors.TEXT_MAIN),
                rx.text(
                    product.description,
                    color=colors.TEXT_SEC,
                    font_family=BODY_FONT,
                    size="2",
                    no_of_lines=3,
                ),
                spacing="1",
                align_items="start",
                width="100%",
            ),
            rx.hstack(
                rx.button(
                    rx.icon(tag="pencil", size=16),
                    on_click=ProductState.edit_product(product.id),
                    variant="soft",
                    color_scheme="blue",
                    cursor="pointer",
                ),
                rx.button(
                    rx.icon(tag="trash-2", size=16),
                    on_click=ProductState.delete_product(product.id),
                    variant="soft",
                    color_scheme="red",
                    cursor="pointer",
                ),
                spacing="2",
            ),
            align_items="start",
            justify_content="between",
            width="100%",
        ),
        background=colors.CARD_BG,
        padding="1.5rem",
        border_radius="16px",
        border=f"1px solid {colors.BORDER}",
        width="100%",
    )


def _form_card() -> rx.Component:
    """Card com o formulário de cadastro/edição + assistente de IA."""
    return rx.box(
        rx.vstack(
            rx.heading(
                rx.cond(ProductState.is_editing, "Editar produto", "Novo produto"),
                size="5",
                font_family=HEADING_FONT,
                color=colors.TEXT_MAIN,
                margin_bottom="0.5rem",
            ),

            # Nome
            rx.vstack(
                _field_label("Nome do produto"),
                rx.input(
                    placeholder="Ex: Consultoria de Performance Comercial",
                    value=ProductState.form_name,
                    on_change=ProductState.set_form_name,
                    size="3",
                    width="100%",
                    variant="surface",
                    color_scheme="blue",
                    color="#111827",
                    background_color="#e9e8e8",
                    _placeholder={"color": "#3a3a3b"},
                ),
                width="100%",
                align_items="start",
                spacing="1",
            ),

            # Descrição
            rx.vstack(
                rx.hstack(
                    _field_label("Descrição"),
                    rx.spacer(),
                    rx.button(
                        rx.icon(tag="sparkles", size=16),
                        rx.cond(ProductState.is_generating, "Gerando...", "Complementar com IA"),
                        on_click=ProductState.generate_description,
                        loading=ProductState.is_generating,
                        disabled=ProductState.is_generating,
                        size="2",
                        variant="soft",
                        color_scheme="blue",
                        cursor="pointer",
                    ),
                    width="100%",
                    align_items="center",
                ),
                rx.text_area(
                    placeholder="Descreva o produto/serviço. Você pode escrever um rascunho e clicar em 'Complementar com IA'.",
                    value=ProductState.form_description,
                    on_change=ProductState.set_form_description,
                    width="100%",
                    rows="6",
                    size="3",
                    variant="surface",
                    color="#111827",
                    background_color="#e9e8e8",
                    _placeholder={"color": "#3a3a3b"},
                ),
                rx.text(
                    "O texto gerado permanece editável até você salvar.",
                    color=colors.TEXT_SEC,
                    size="1",
                    font_family=BODY_FONT,
                ),
                width="100%",
                align_items="start",
                spacing="1",
            ),

            # Aviso "o agente está trabalhando" enquanto a IA roda
            rx.cond(
                ProductState.is_generating,
                rx.box(
                    rx.hstack(
                        rx.spinner(size="3", color=colors.HIGHLIGHT),
                        rx.vstack(
                            rx.text(
                                "O assistente de IA está trabalhando...",
                                color=colors.TEXT_MAIN,
                                size="2",
                                font_weight="600",
                                font_family=BODY_FONT,
                            ),
                            rx.text(
                                "Gerando e aprimorando a descrição do seu produto. Isso pode levar alguns segundos.",
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
            ),

            # Mensagem de erro da IA / guardrail
            rx.cond(
                ProductState.ai_error != "",
                rx.box(
                    rx.text(ProductState.ai_error, color="red", size="2", font_weight="500"),
                    background="#fee2e2",
                    padding="0.75rem",
                    border_radius="8px",
                    width="100%",
                ),
            ),

            # Ações
            rx.hstack(
                rx.button(
                    rx.icon(tag="save", size=16),
                    rx.cond(ProductState.is_editing, "Salvar alterações", "Cadastrar produto"),
                    on_click=ProductState.save_product,
                    size="3",
                    background=colors.BTN_GRADIENT,
                    border_radius="8px",
                    color="white",
                    cursor="pointer",
                    font_family=BODY_FONT,
                    font_weight="600",
                ),
                rx.cond(
                    ProductState.is_editing,
                    rx.button(
                        "Cancelar",
                        on_click=ProductState.new_product,
                        size="3",
                        variant="soft",
                        color_scheme="gray",
                        cursor="pointer",
                    ),
                ),
                spacing="3",
                margin_top="0.5rem",
            ),

            spacing="4",
            width="100%",
            align_items="start",
        ),
        background=colors.CARD_BG,
        padding="2rem",
        border_radius="16px",
        border=f"1px solid {colors.BORDER}",
        width="100%",
        max_width="800px",
        # id usado por edit_product() para rolar o formulário até a visão do usuário.
        id="product-form",
    )


@rx.page(route="/produtos", on_load=ProductState.load_products)
def products_page() -> rx.Component:
    return dashboard_layout(
        rx.vstack(
            # Cabeçalho
            rx.vstack(
                rx.heading("Produtos", size="8", color=colors.TEXT_MAIN, font_family=HEADING_FONT),
                rx.text(
                    "Cadastre seus produtos e serviços. O catálogo é compartilhado por "
                    "toda a equipe e usado pelos agentes de IA nas próximas etapas.",
                    color=colors.TEXT_SEC,
                    font_family=BODY_FONT,
                ),
                spacing="1",
                align_items="start",
                width="100%",
                max_width="800px",
                margin_bottom="1rem",
            ),

            _form_card(),

            rx.divider(margin_y="2rem", border_color=colors.BORDER, width="100%", max_width="800px"),

            rx.heading(
                "Produtos cadastrados",
                size="5",
                color=colors.TEXT_MAIN,
                font_family=HEADING_FONT,
                margin_bottom="1rem",
                width="100%",
                max_width="800px",
                text_align="left",
            ),
            rx.cond(
                ProductState.product_count > 0,
                rx.vstack(
                    rx.foreach(ProductState.products, _product_card),
                    spacing="3",
                    width="100%",
                    max_width="800px",
                ),
                rx.text(
                    "Nenhum produto cadastrado ainda.",
                    color=colors.TEXT_SEC,
                    font_family=BODY_FONT,
                    width="100%",
                    max_width="800px",
                    text_align="left",
                ),
            ),

            align_items="center",
            width="100%",
        )
    )
