"""Mapa do Brasil (SVG estático) com coloração por estado.

Não existe nenhuma lib de mapa/geo no projeto — em vez de adicionar uma
dependência JS nova (react-simple-maps + topojson), os 27 contornos de estado
ficam embutidos como paths SVG fixos (`styles/brazil_geo.py`, gerados uma vez
a partir de um GeoJSON público do IBGE, projeção equiretangular simplificada
— suficiente para um mapa estilizado de dashboard, não para uso cartográfico
de precisão). Só a COR de cada estado é dinâmica (`cores_por_estado`, um Var
Dict[str,str] uf->cor, calculado no backend via
`services.dashboard_insights.cor_por_intensidade`); a geometria nunca muda.

O hover mostra a contagem de leads (`contagem_por_estado`, um Var Dict[str,int])
via `<title>` nativo do SVG — tooltip do próprio navegador, sem dependência
nova nem componente de overlay customizado.
"""

import reflex as rx

from prospect_agent.styles.brazil_geo import PATHS, VIEWBOX


def brazil_map(cores_por_estado, contagem_por_estado, width: str = "100%", height: str = "420px") -> rx.Component:
    estados = [
        rx.el.path(
            rx.el.title(f"{uf}: {contagem_por_estado[uf]} lead(s)"),
            d=path_d,
            fill=cores_por_estado[uf],
            stroke="#ffffff",
            stroke_width="1.2",
            style={"transition": "fill 0.3s ease", "cursor": "default"},
        )
        for uf, path_d in PATHS.items()
    ]
    return rx.el.svg(*estados, view_box=VIEWBOX, width=width, height=height)
