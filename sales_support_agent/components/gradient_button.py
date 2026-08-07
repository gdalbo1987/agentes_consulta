import reflex as rx
from prospect_agent.styles import colors
from typing import Optional, Any
from prospect_agent.styles.typography import BODY_FONT

def gradient_button(text: str, href: str = "", is_external: bool = False, on_click: Optional[Any] = None) -> rx.Component:
    # 1. Cria a base do botão (onde o on_click sempre funciona)
    btn = rx.button(
        text,
        background=colors.BTN_GRADIENT,
        color="white",
        border_radius="8px",
        padding_x="1.5rem",
        padding_y="1.5rem",
        font_family=BODY_FONT,
        font_weight="600",
        font_size="1rem",
        transition="all 0.3s ease",
        cursor="pointer",
        on_click=on_click, # Aqui conectamos as funções do AppState!
        _hover={
            "background": colors.BTN_HOVER,
            "transform": "scale(1.03)",
            "box_shadow": "0 8px 16px rgba(29, 84, 140, 0.2)"
        }
    )
    
    # 2. Se houver uma URL (texto), embrulhamos o botão em um rx.link
    if href:
        return rx.link(btn, href=href, is_external=is_external)
        
    # 3. Se não houver URL, devolvemos apenas o botão puro
    return btn