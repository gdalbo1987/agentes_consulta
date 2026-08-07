# Importação das fontes do Google Fonts e FontAwesome (para ícones de marcas)
FONTS = [
    "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;700;900&family=Manrope:wght@400;500;700;900&display=swap",
    "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css"
]

# Pilha de fallback. **Sempre use estas constantes em `font_family=`, nunca
# "Manrope"/"Inter" sozinhos.** Um nome de fonte solto não tem para onde cair:
# se o Google Fonts não responder (rede fora, CDN bloqueado, primeiro paint em
# conexão ruim), o navegador aplica o padrão dele, que é uma SERIFADA — a
# página inteira vira Times. Com a pilha abaixo, a degradação é para a sans do
# sistema e o layout continua reconhecível.
_SISTEMA = (
    'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, '
    '"Helvetica Neue", Arial, sans-serif'
)
HEADING_FONT = f"Manrope, {_SISTEMA}"
BODY_FONT = f"Inter, {_SISTEMA}"