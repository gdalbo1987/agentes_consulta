import reflex as rx
import os
from dotenv import load_dotenv

load_dotenv()

config = rx.Config(
    app_name="sales_support_agent",
    db_url=os.environ.get("DATABASE_URL"),
    # Remove o selo flutuante "Built with Reflex" de todas as páginas. Ele só é
    # injetado em modo prod (`reflex run --env prod`), e o padrão é ligado, então
    # sem esta linha ele volta — é ferramenta interna da Coester, não vitrine.
    show_built_with_reflex=False,
    plugins=[
        rx.plugins.SitemapPlugin(),
        rx.plugins.TailwindV4Plugin(),
        # `appearance="light"` é obrigatório, não preferência: a paleta em
        # `sales_support_agent/styles/colors.py` só tem tons claros (texto #17233d
        # sobre fundo #f5f9fe). Sem fixar isso, o Radix herda o tema do SISTEMA
        # OPERACIONAL do visitante e, em quem usa modo escuro, o <body> vem
        # preto — as seções que não declaram fundo próprio (vantagens,
        # tutorial) ficam escuras com texto escuro.
        rx.plugins.RadixThemesPlugin(theme=rx.theme(appearance="light")),
    ]
)