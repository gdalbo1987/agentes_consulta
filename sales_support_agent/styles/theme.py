import reflex as rx
from prospect_agent.styles.colors import BG, TEXT_MAIN, HIGHLIGHT

# ... restante do código (custom_css, BASE_STYLE, etc)

# Estilos globais e animações customizadas
custom_css = """
@keyframes float {
    0% { transform: translateY(0px); }
    50% { transform: translateY(-12px); }
    100% { transform: translateY(0px); }
}

@keyframes fadeIn {
    0% { opacity: 0; transform: translateY(15px); }
    100% { opacity: 1; transform: translateY(0px); }
}
"""

BASE_STYLE = {
    "::selection": {
        "background_color": HIGHLIGHT,
        "color": "white",
    },
    "font_family": "Inter, sans-serif",
    "background_color": BG,
    "color": TEXT_MAIN,
    # Sem isso, o `margin` padrão do navegador no <body> (8px) soma aos
    # containers de 100vh usados nas páginas autenticadas (dashboard_layout) e
    # o body passa a rolar TAMBÉM — duas barras de scroll (a da página inteira
    # + a do conteúdo interno, que já tem seu próprio `overflow_y="auto"`).
    # Só reseta o margin (seguro em qualquer página, inclusive a landing, que
    # rola pelo body normalmente); o corte de overflow fica escopado ao
    # próprio dashboard_layout, não aqui — isso quebraria o scroll da landing.
    # (chave no MESMO nível das demais: o Reflex já aplica este dicionário ao
    # <body>. Aninhá-la como `"body": {...}` gerava o seletor `body body`, que
    # não casa com nada.)
    "margin": "0",
}