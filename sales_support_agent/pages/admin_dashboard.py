import reflex as rx
from sales_support_agent.components.confirm_dialog import confirm_dialog, dialog_cancel_button
from sales_support_agent.components.dashboard_layout import dashboard_layout
from sales_support_agent.components.data_table import col, table_shell
from sales_support_agent.services.settings import EFFORTS_DISPONIVEIS, MODELOS_DISPONIVEIS
from sales_support_agent.state import AdminState, SettingsState
from sales_support_agent.styles import colors
from sales_support_agent.styles.typography import HEADING_FONT, BODY_FONT


def admin_card(title: str, value, icon: str, subtitle=None) -> rx.Component:
    """Card de métrica. `subtitle` mostra o número bruto embaixo do valor
    principal — é como os cards de custo mantêm visível a contagem de tokens
    que originou o valor em dólar."""
    return rx.box(
        rx.hstack(
            rx.icon(tag=icon, color=colors.HIGHLIGHT, size=24),
            rx.heading(title, size="3", color=colors.TEXT_SEC, font_family=BODY_FONT, font_weight="500"),
            spacing="3",
            align_items="center",
            margin_bottom="1rem",
        ),
        rx.heading(value, size="7", color=colors.TEXT_MAIN, font_family=HEADING_FONT, font_weight="900"),
        *(
            [rx.text(subtitle, size="1", color=colors.TEXT_SEC, font_family=BODY_FONT, margin_top="0.35rem")]
            if subtitle is not None
            else []
        ),
        background=colors.CARD_BG,
        padding="1.5rem",
        border_radius="16px",
        border=f"1px solid {colors.BORDER}",
        width="100%",
    )


def _chart_card(title: str, data, fill: str) -> rx.Component:
    return rx.box(
        rx.heading(title, size="4", color=colors.TEXT_MAIN, font_family=HEADING_FONT, margin_bottom="1rem"),
        rx.recharts.bar_chart(
            rx.recharts.cartesian_grid(stroke_dasharray="3 3", vertical=False),
            rx.recharts.bar(data_key="valor", fill=fill, radius=[6, 6, 0, 0]),
            rx.recharts.x_axis(data_key="mes"),
            rx.recharts.y_axis(),
            rx.recharts.graphing_tooltip(),
            data=data,
            width="100%",
            height=260,
        ),
        background=colors.CARD_BG,
        padding="1.5rem",
        border_radius="16px",
        border=f"1px solid {colors.BORDER}",
        width="100%",
    )


def _user_row(u) -> rx.Component:
    return rx.table.row(
        rx.table.cell(u.name, color=colors.TEXT_MAIN, font_weight="500"),
        rx.table.cell(u.email, color=colors.TEXT_SEC),
        rx.table.cell(
            rx.badge(u.class_label, color_scheme=rx.cond(u.is_superadmin, "purple", "blue")),
        ),
        rx.table.cell(
            rx.badge(u.status, color_scheme=rx.cond(u.status == "Ativo", "green", "amber")),
        ),
        rx.table.cell(
            rx.hstack(
                rx.button(
                    rx.icon(tag="pencil", size=14),
                    on_click=AdminState.open_edit_user(u.id),
                    variant="soft",
                    color_scheme="blue",
                    size="1",
                    cursor="pointer",
                ),
                rx.button(
                    rx.icon(tag="trash-2", size=14),
                    on_click=AdminState.delete_user(u.id),
                    variant="soft",
                    color_scheme="red",
                    size="1",
                    cursor="pointer",
                ),
                spacing="2",
            ),
        ),
        align="center",
        _hover={"background": "rgba(245, 249, 254, 0.5)"},
    )


def render_log_row(log) -> rx.Component:
    return rx.table.row(
        rx.table.cell(log.timestamp, color=colors.TEXT_SEC),
        rx.table.cell(log.tenant_id, color=colors.TEXT_SEC),
        rx.table.cell(log.user_email, color=colors.TEXT_MAIN, font_weight="500"),
        rx.table.cell(rx.badge(log.action, color_scheme="blue")),
        rx.table.cell(log.details, color=colors.TEXT_SEC),
        align="center",
        _hover={"background": "rgba(245, 249, 254, 0.5)"},
    )


# Tabela compartilhada com a tela de Enriquecimento (components/data_table.py).
_table_shell = table_shell
_col = col


def _section_heading(text: str) -> rx.Component:
    return rx.heading(
        text, size="5", color=colors.TEXT_MAIN, font_family=HEADING_FONT,
        margin_top="3rem", margin_bottom="1rem", width="100%",
    )


# Superfície escura fixa dos diálogos, com texto claro (legível independentemente
# do tema claro/escuro do sistema, que antes deixava as letras invisíveis).
_DIALOG_BG = "#1b2842"


def _dialog_label(text: str) -> rx.Component:
    return rx.text(text, size="2", font_weight="600", color="white")


def _create_user_dialog() -> rx.Component:
    """Diálogo de convite — única forma de admitir alguém na plataforma."""
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title("Convidar usuário", color="white"),
            rx.dialog.description(
                "O convidado recebe um e-mail com um link válido por 24 horas para "
                "definir a própria senha. Nenhuma senha é definida aqui.",
                margin_bottom="1rem",
                color="rgba(255, 255, 255, 0.7)",
            ),
            rx.vstack(
                _dialog_label("Nome"),
                rx.input(value=AdminState.new_user_name, on_change=AdminState.set_new_user_name, width="100%"),
                _dialog_label("E-mail"),
                rx.input(value=AdminState.new_user_email, on_change=AdminState.set_new_user_email, type="email", width="100%"),
                _dialog_label("Classe"),
                rx.select(
                    ["Usuário", "Super Admin"],
                    value=AdminState.new_user_class,
                    on_change=AdminState.set_new_user_class,
                    width="100%",
                ),
                spacing="2",
                width="100%",
            ),
            rx.flex(
                rx.dialog.close(dialog_cancel_button()),
                rx.button(
                    "Enviar convite",
                    on_click=AdminState.create_user,
                    background=colors.BTN_GRADIENT,
                    color="white",
                    cursor="pointer",
                    border_radius="8px",
                ),
                spacing="3",
                margin_top="1.5rem",
                justify="end",
            ),
            max_width="450px",
            background=_DIALOG_BG,
        ),
        open=AdminState.create_dialog_open,
        on_open_change=AdminState.set_create_dialog_open,
    )


def _edit_user_dialog() -> rx.Component:
    """Diálogo controlado para editar dados e permissão do usuário."""
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title("Editar usuário", color="white"),
            rx.vstack(
                _dialog_label("Nome"),
                rx.input(value=AdminState.edit_user_name, on_change=AdminState.set_edit_user_name, width="100%"),
                _dialog_label("E-mail"),
                rx.input(value=AdminState.edit_user_email, on_change=AdminState.set_edit_user_email, width="100%"),
                _dialog_label("Classe"),
                rx.select(
                    ["Usuário", "Super Admin"],
                    value=AdminState.edit_user_class,
                    on_change=AdminState.set_edit_user_class,
                    width="100%",
                ),
                spacing="2",
                width="100%",
            ),
            rx.flex(
                rx.dialog.close(dialog_cancel_button()),
                rx.button(
                    "Salvar",
                    on_click=AdminState.save_user,
                    background=colors.BTN_GRADIENT,
                    color="white",
                    cursor="pointer",
                ),
                spacing="3",
                margin_top="1.5rem",
                justify="end",
            ),
            max_width="450px",
            background=_DIALOG_BG,
        ),
        open=AdminState.user_dialog_open,
        on_open_change=AdminState.set_user_dialog_open,
    )


# ==========================================================================
# Configurações de Agentes de IA + Integrações (superadmin-only).
# Vive nesta MESMA página /admin (pedido explícito: sem rota nova) — o
# SettingsState é uma classe própria (não incha AdminState), mas sua UI entra
# aqui embaixo das seções já existentes.
# ==========================================================================
def _config_label(text: str) -> rx.Component:
    """Rótulo de campo sobre o card claro (CARD_BG) — não confundir com
    `_dialog_label`, que é para o fundo escuro fixo dos diálogos."""
    return rx.text(text, size="2", font_weight="600", color=colors.TEXT_MAIN)


_AGENTES_LABELS = {
    "classificacao": "Classificação de e-mails (Agente 1)",
    "resumo": "Resumo dos e-mails classificados (Agente 2)",
    "consulta": "Consulta sobre os e-mails (/consulta)",
}


def _agent_config_card(agent_key: str, model_var, effort_var, set_model, set_effort) -> rx.Component:
    return rx.box(
        rx.text(
            _AGENTES_LABELS[agent_key], size="3", font_weight="700",
            color=colors.TEXT_MAIN, font_family=HEADING_FONT, margin_bottom="0.75rem",
        ),
        rx.vstack(
            _config_label("Modelo"),
            rx.select(list(MODELOS_DISPONIVEIS), value=model_var, on_change=set_model, width="100%"),
            _config_label("Reasoning effort"),
            rx.select(list(EFFORTS_DISPONIVEIS), value=effort_var, on_change=set_effort, width="100%"),
            spacing="2", width="100%",
        ),
        rx.button(
            "Salvar", on_click=SettingsState.save_agent_config(agent_key),
            background=colors.BTN_GRADIENT, color="white", cursor="pointer",
            margin_top="1rem", size="2",
        ),
        background=colors.CARD_BG, padding="1.5rem", border_radius="16px",
        border=f"1px solid {colors.BORDER}", width="100%",
    )


def _secret_field(label: str, value, on_change, configurado, placeholder: str = "") -> rx.Component:
    """Campo de segredo: começa sempre em branco (nunca o valor real), com um
    badge ao lado dizendo se já existe algo configurado no banco. Deixar em
    branco ao salvar mantém o valor atual."""
    return rx.vstack(
        _config_label(label),
        rx.hstack(
            rx.input(
                value=value, on_change=on_change, type="password",
                placeholder=placeholder or "Deixe em branco para manter o valor atual",
                width="100%",
            ),
            rx.cond(
                configurado,
                rx.badge("Configurado", color_scheme="green"),
                rx.badge("Não configurado", color_scheme="gray"),
            ),
            width="100%", align_items="center", spacing="2",
        ),
        spacing="1", width="100%",
    )


def _integration_card(titulo: str, *filhos, on_save) -> rx.Component:
    return rx.box(
        rx.text(titulo, size="3", font_weight="700", color=colors.TEXT_MAIN, font_family=HEADING_FONT, margin_bottom="0.75rem"),
        rx.vstack(*filhos, spacing="3", width="100%"),
        rx.button(
            "Salvar", on_click=on_save,
            background=colors.BTN_GRADIENT, color="white", cursor="pointer",
            margin_top="1rem", size="2",
        ),
        background=colors.CARD_BG, padding="1.5rem", border_radius="16px",
        border=f"1px solid {colors.BORDER}", width="100%",
    )


def _graph_card() -> rx.Component:
    """Credenciais da Microsoft Graph para envio de e-mail.

    Só o client secret usa `_secret_field` (mascarado, nunca lido de volta para
    o State); remetente, tenant ID e client ID são identificadores públicos do
    registro de aplicativo e ficam em texto normal.
    """
    return _integration_card(
        "E-mail (Microsoft Graph)",
        _config_label("E-mail remetente"),
        rx.input(
            value=SettingsState.graph_sender_email,
            on_change=SettingsState.set_graph_sender_email,
            placeholder="inovacao@coester.com.br",
            width="100%",
        ),
        _config_label("Tenant ID (Directory ID do Entra)"),
        rx.input(
            value=SettingsState.graph_tenant_id,
            on_change=SettingsState.set_graph_tenant_id,
            placeholder="00000000-0000-0000-0000-000000000000",
            width="100%",
        ),
        _config_label("Client ID"),
        rx.input(
            value=SettingsState.graph_client_id,
            on_change=SettingsState.set_graph_client_id,
            placeholder="00000000-0000-0000-0000-000000000000",
            width="100%",
        ),
        _secret_field(
            "Client Secret", SettingsState.graph_client_secret_input,
            SettingsState.set_graph_client_secret_input,
            SettingsState.graph_client_secret_configurado,
        ),
        rx.text(
            "O registro de aplicativo precisa da permissão de APLICAÇÃO "
            "\"Mail.Send\" com consentimento do administrador, e o remetente "
            "precisa ser uma caixa real do locatário.",
            size="1", color=colors.TEXT_SEC,
        ),
        rx.button(
            rx.icon(tag="send", size=14),
            "Enviar e-mail de teste",
            on_click=SettingsState.testar_graph,
            loading=SettingsState.graph_testando,
            disabled=SettingsState.graph_testando,
            variant="soft",
            color_scheme="blue",
            size="2",
            cursor="pointer",
            margin_top="0.5rem",
        ),
        on_save=SettingsState.save_graph_settings,
    )


def _linha_preco_modelo(modelo: str) -> rx.Component:
    """Entrada e saída de UM modelo, lado a lado.

    O `modelo` é uma str Python (vem de MODELOS_DISPONIVEIS, não do State), por
    isso o `lambda` no `on_change` funciona: ele é resolvido na compilação e
    vira um handler com o nome do modelo já embutido.
    """
    return rx.vstack(
        rx.text(modelo, size="2", font_weight="700", color=colors.TEXT_MAIN, font_family=HEADING_FONT),
        rx.grid(
            rx.vstack(
                _config_label("Entrada - US$ por 1M"),
                rx.input(
                    value=SettingsState.token_input_prices[modelo],
                    on_change=lambda v: SettingsState.set_token_input_price(modelo, v),
                    placeholder="0,25",
                    width="100%",
                ),
                spacing="1", width="100%", align_items="start",
            ),
            rx.vstack(
                _config_label("Saída - US$ por 1M"),
                rx.input(
                    value=SettingsState.token_output_prices[modelo],
                    on_change=lambda v: SettingsState.set_token_output_price(modelo, v),
                    placeholder="2,00",
                    width="100%",
                ),
                spacing="1", width="100%", align_items="start",
            ),
            columns="2", spacing="3", width="100%",
        ),
        spacing="1", width="100%", align_items="start",
    )


def _token_pricing_card() -> rx.Component:
    """Preço do token de cada modelo, em USD por 1 MILHÃO de tokens.

    Por milhão e não por token porque é a unidade da tabela de preços da OpenAI
    e porque digitar 0,00000025 num input é convite a erro de zero. A conversão
    para preço por token acontece ao salvar (services/token_pricing.py).

    Um bloco por modelo de MODELOS_DISPONIVEIS: incluir um modelo novo lá faz
    seus campos aparecerem aqui sozinhos.
    """
    return rx.box(
        rx.text(
            "Custo dos tokens OpenAI", size="3", font_weight="700",
            color=colors.TEXT_MAIN, font_family=HEADING_FONT, margin_bottom="0.75rem",
        ),
        rx.vstack(
            *[_linha_preco_modelo(m) for m in MODELOS_DISPONIVEIS],
            rx.text(
                "Cada consumo é multiplicado pelo preço do modelo que o gerou. "
                "Os cards e gráficos acima somam esses valores. Consumo "
                "registrado antes desta tela existir é atribuído ao modelo que "
                "o agente usa hoje.",
                size="1", color=colors.TEXT_SEC,
            ),
            rx.button(
                "Salvar",
                on_click=SettingsState.save_token_pricing,
                background=colors.BTN_GRADIENT,
                color="white",
                cursor="pointer",
                size="2",
                margin_top="0.5rem",
                align_self="end",
            ),
            spacing="3", width="100%", align_items="start",
        ),
        background=colors.CARD_BG, padding="1.5rem", border_radius="16px",
        border=f"1px solid {colors.BORDER}", width="100%", margin_top="1.5rem",
    )


def _database_card() -> rx.Component:
    return rx.box(
        rx.text("Banco de dados", size="3", font_weight="700", color=colors.TEXT_MAIN, font_family=HEADING_FONT, margin_bottom="0.75rem"),
        rx.text(SettingsState.db_url_display, size="2", color=colors.TEXT_SEC, font_family="monospace", word_break="break-all"),
        rx.text(
            "Definida via variável de ambiente (DATABASE_URL) no deploy - Não é "
            "configurável por aqui, já que o app precisa dessa conexão para "
            "conseguir ler qualquer outra configuração do próprio banco.",
            size="1", color=colors.TEXT_SEC, margin_top="0.75rem",
        ),
        background=colors.CARD_BG, padding="1.5rem", border_radius="16px",
        border=f"1px solid {colors.BORDER}", width="100%",
    )


def admin_dashboard() -> rx.Component:
    return dashboard_layout(
        rx.vstack(
            rx.heading("Visão Geral - Super Admin", font_family=HEADING_FONT, size="8", color=colors.TEXT_MAIN, margin_bottom="2rem"),

            # Cards de métricas (entrada e saída de tokens são indicadores separados)
            rx.grid(
                admin_card("Usuários", AdminState.total_users.to_string(), "users"),
                admin_card(
                    "Custo tokens de entrada", AdminState.input_cost_label, "arrow-down-to-line",
                    subtitle=f"Mês atual: {AdminState.input_cost_month_label} · "
                             f"{AdminState.total_input_tokens} tokens acumulados",
                ),
                admin_card(
                    "Custo tokens de saída", AdminState.output_cost_label, "arrow-up-from-line",
                    subtitle=f"Mês atual: {AdminState.output_cost_month_label} · "
                             f"{AdminState.total_output_tokens} tokens acumulados",
                ),
                columns=rx.breakpoints(initial="1", sm="2", lg="3"),
                spacing="4",
                width="100%",
                margin_bottom="1rem",
            ),

            # Gráficos mensais (séries independentes, todos na mesma janela de
            # 6 meses para poderem ser lidos lado a lado).
            #
            # Restaram só os de token: os cards e gráficos de KipFlow (em reais)
            # e Hunter (em créditos) saíram junto com o funil de prospecção, que
            # era quem consumia essas APIs.
            rx.grid(
                _chart_card("Custo tokens de entrada em US$ (últimos 6 meses)", AdminState.monthly_input_cost, "#1d548c"),
                _chart_card("Custo tokens de saída em US$ (últimos 6 meses)", AdminState.monthly_output_cost, "#7c3aed"),
                columns=rx.breakpoints(initial="1", lg="2"),
                spacing="4",
                width="100%",
            ),

            # Botão de limpeza dos contadores de consumo (TokenUsage) — fica
            # junto dos cards/gráficos de consumo acima.
            rx.hstack(
                rx.spacer(),
                rx.button(
                    rx.icon(tag="eraser", size=16),
                    "Limpar contadores",
                    on_click=AdminState.set_confirm_counters_open(True),
                    color_scheme="red",
                    variant="soft",
                    cursor="pointer",
                ),
                width="100%",
                align_items="center",
                margin_top="1rem",
            ),

            # Gestão de usuários
            rx.hstack(
                _section_heading("Usuários"),
                rx.spacer(),
                rx.button(
                    rx.icon(tag="user-plus", size=16),
                    "Convidar usuário",
                    on_click=AdminState.open_create_user,
                    background=colors.BTN_GRADIENT,
                    color="white",
                    cursor="pointer",
                    font_weight="600",
                    margin_top="3rem",
                ),
                width="100%",
                align_items="center",
            ),
            _table_shell(
                [_col("Nome"), _col("E-mail"), _col("Classe"), _col("Status"), _col("Ações")],
                rx.foreach(AdminState.admin_users, _user_row),
            ),

            # Feed de auditoria
            rx.hstack(
                _section_heading("Feed de Atividades (Tempo Real)"),
                rx.spacer(),
                rx.button(
                    rx.icon(tag="trash-2", size=16),
                    "Limpar Histórico",
                    color_scheme="red",
                    variant="soft",
                    on_click=AdminState.set_confirm_logs_open(True),
                    cursor="pointer",
                    margin_top="3rem",
                ),
                width="100%",
                align_items="center",
            ),
            _table_shell(
                [_col("Data/Hora"), _col("Tenant ID"), _col("Usuário"), _col("Ação"), _col("Detalhes")],
                rx.foreach(AdminState.admin_logs, render_log_row),
            ),

            # Configurações de Agentes de IA
            _section_heading("Configurações de Agentes de IA"),
            rx.grid(
                _agent_config_card(
                    "classificacao", SettingsState.classificacao_model, SettingsState.classificacao_effort,
                    SettingsState.set_classificacao_model, SettingsState.set_classificacao_effort,
                ),
                _agent_config_card(
                    "resumo", SettingsState.resumo_model, SettingsState.resumo_effort,
                    SettingsState.set_resumo_model, SettingsState.set_resumo_effort,
                ),
                _agent_config_card(
                    "consulta", SettingsState.consulta_model, SettingsState.consulta_effort,
                    SettingsState.set_consulta_model, SettingsState.set_consulta_effort,
                ),
                columns=rx.breakpoints(initial="1", sm="2"),
                spacing="4", width="100%",
            ),

            _token_pricing_card(),

            # Integrações
            _section_heading("Integrações"),
            rx.grid(
                _graph_card(),
                columns=rx.breakpoints(initial="1", sm="2"),
                spacing="4", width="100%", margin_bottom="1.5rem",
            ),
            _database_card(),

            _edit_user_dialog(),
            _create_user_dialog(),
            confirm_dialog(
                open_var=AdminState.confirm_counters_open,
                on_open_change=AdminState.set_confirm_counters_open,
                title="Limpar contadores de consumo?",
                body=(
                    "Isso apaga todo o histórico de consumo de tokens de IA, "
                    "zerando os indicadores e os gráficos de custo. "
                    "Usuários, e-mails classificados, execuções e logs não são "
                    "afetados. A ação não pode ser desfeita."
                ),
                confirm_label="Limpar contadores",
                on_confirm=AdminState.clear_counters,
            ),
            confirm_dialog(
                open_var=AdminState.confirm_logs_open,
                on_open_change=AdminState.set_confirm_logs_open,
                title="Limpar o histórico de atividades?",
                body=(
                    "Isso apaga todos os registros do feed de auditoria, de "
                    "todos os usuários. A ação não pode ser desfeita."
                ),
                confirm_label="Limpar histórico",
                on_confirm=AdminState.clear_old_logs,
            ),

            width="100%",
            max_width="1200px",
            align_items="start",
        )
    )
