"""Ponto de entrada da aplicação Reflex — Agente de Suporte ao Comercial da Coester.

Aqui só se monta o `rx.App` e se registram as rotas. Toda a lógica vive em
`state.py` (estados/event handlers), `services/` (regras e integrações) e
`pages/`/`components/` (UI).

Duas formas de registro convivem, de propósito:

* As páginas internas (`/dashboard`, `/consulta`, `/profile`, ...) se declaram
  com `@rx.page(...)` no próprio módulo, junto do seu `on_load`. Basta
  importá-las aqui para que o Reflex as registre.
* As três abaixo (`/`, `/login`, `/admin`) são registradas à mão porque não têm
  decorator: a landing e o login não têm `on_load`, e `/admin` precisa de DOIS
  `on_load` de estados diferentes (AdminState + SettingsState).

Não existem rotas de checkout nem de termos/privacidade: o modelo de assinatura
e o cadastro público foram descontinuados na conversão para uso interno.
"""

import reflex as rx

# Importados pelo efeito colateral do @rx.page — cada módulo registra sua rota.
from sales_support_agent.pages import (  # noqa: F401
    consulta,
    dashboard,
    forgot_password,
    profile,
    reset_password,
)
from sales_support_agent.pages.admin_dashboard import admin_dashboard
from sales_support_agent.pages.auth import auth_page
from sales_support_agent.pages.landing import landing_page
from sales_support_agent.state import AdminState, SettingsState
from sales_support_agent.styles.theme import BASE_STYLE

# O tema (claro, fixo) fica no `RadixThemesPlugin` em `rxconfig.py` — passá-lo
# aqui como `theme=` está deprecado desde a 0.9.
app = rx.App(style=BASE_STYLE)

app.add_page(landing_page, route="/", title="Coester | Plataforma de Prospecção")
app.add_page(auth_page, route="/login", title="Acessar | Coester")
app.add_page(
    admin_dashboard,
    route="/admin",
    title="Super Admin | Coester",
    # Os dois estados alimentam seções distintas da MESMA página: AdminState
    # traz métricas/usuários/logs e SettingsState os cards de configuração.
    on_load=[AdminState.load_dashboard, SettingsState.load_settings],
)
