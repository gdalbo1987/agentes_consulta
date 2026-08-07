"""Relatório .pdf da classificação de priorização.

Ao contrário do relatório de enriquecimento (.xlsx, "insumo de trabalho" para
filtrar/colar em CRM — ver services/enrichment_report.py), este é um documento
de APRESENTAÇÃO: uma tabela-resumo (nome, empresa, score, classe) seguida de
uma seção por lead com o breakdown dos 7 critérios e as dicas de approach —
pensado para ser lido/impresso por um gestor, não filtrado em planilha.

Usa `reportlab` (pura Python, sem binário externo tipo wkhtmltopdf) — única
dependência nova do projeto para isto, já que o repositório só tinha
`openpyxl` (xlsx) até aqui.
"""
import io
import os
from typing import Any, Dict, List, Optional

import reportlab
from reportlab.lib import colors as rl_colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from prospect_agent.services.normalizers import formatar_cnpj

# As fontes padrão do reportlab (Helvetica/Times, os "Adobe standard 14") só
# suportam WinAnsiEncoding — cobre acentos do pt-BR, mas não símbolos fora do
# Latin-1 que o texto livre gerado pela IA (justificativas, dicas de
# approach) pode eventualmente usar. "Vera" (Bitstream Vera Sans) já vem
# EMPACOTADA dentro do próprio reportlab (não é dependência nova) e tem
# cobertura Unicode bem mais ampla — por isso é a fonte usada no relatório
# inteiro, em vez de Helvetica.
_FONTS_DIR = os.path.join(os.path.dirname(reportlab.__file__), "fonts")
_FONTE_REGULAR = "Vera"
_FONTE_NEGRITO = "Vera-Bold"
pdfmetrics.registerFont(TTFont(_FONTE_REGULAR, os.path.join(_FONTS_DIR, "Vera.ttf")))
pdfmetrics.registerFont(TTFont(_FONTE_NEGRITO, os.path.join(_FONTS_DIR, "VeraBd.ttf")))
pdfmetrics.registerFontFamily(_FONTE_REGULAR, normal=_FONTE_REGULAR, bold=_FONTE_NEGRITO)

_AZUL = rl_colors.HexColor("#1D548C")
_CINZA_CLARO = rl_colors.HexColor("#F5F9FE")

_COR_CLASSE = {
    "Alta": rl_colors.HexColor("#15803d"),
    "Média": rl_colors.HexColor("#b45309"),
    "Baixa": rl_colors.HexColor("#b91c1c"),
}

# Mesmo a Vera (bem mais completa que Helvetica) não cobre TODO o Unicode —
# confirmado renderizando um "→" de teste: virou um retângulo vazio (glifo
# ausente). Como justificativas/dicas vêm de texto livre gerado pela IA (não
# são strings nossas, controladas), símbolos comuns em raciocínio de LLM
# (setas, check/x, estrelas) são trocados por um equivalente ASCII antes de
# entrar no PDF; qualquer outro caractere fora do Unicode básico (emoji,
# pictogramas, símbolos raros) é descartado — melhor sumir do texto do que
# virar uma caixa vazia no documento.
_SUBSTITUICOES_SIMBOLOS = {
    "→": "->", "←": "<-", "↔": "<->", "⇒": "=>", "⇐": "<=",
    "✓": "OK", "✔": "OK", "✗": "X", "✘": "X",
    "★": "*", "☆": "*", "…": "...",
}
# Faixas de risco real (setas, símbolos diversos/dingbats, emoji) — fora
# delas, a Vera cobre bem o texto normal em pt-BR (letras, acentos, pontuação
# geral como travessão/aspas curvas, já confirmado por render manual).
_FAIXAS_SEM_SUPORTE = (
    (0x2190, 0x21FF),  # Setas
    (0x2300, 0x27BF),  # Símbolos técnicos diversos, dingbats
    (0x1F000, 0x1FFFF),  # Emoji e pictogramas
)


def _sanitizar_texto(texto: Optional[str]) -> str:
    """Símbolos fora do que a Vera cobre -> substituídos/removidos (acima).
    Uso: texto puro, como células de `Table` (que NÃO interpretam XML — só
    substitui glifo, não escapa `&`/`<`/`>`, ou apareceria "&amp;" literal)."""
    if not texto:
        return ""
    for simbolo, troca in _SUBSTITUICOES_SIMBOLOS.items():
        texto = texto.replace(simbolo, troca)

    def _suportado(ch: str) -> bool:
        cp = ord(ch)
        return not any(inicio <= cp <= fim for inicio, fim in _FAIXAS_SEM_SUPORTE)

    return "".join(ch for ch in texto if _suportado(ch))


def _sanitizar_paragrafo(texto: Optional[str]) -> str:
    """Mesma limpeza de `_sanitizar_texto`, seguida de escapar `&`/`<`/`>` —
    para texto que vai dentro de um `Paragraph` (que interpreta `<b>` etc.
    como mini-XML). Texto livre da IA contendo esses caracteres (ex.:
    "score < 50") quebraria o parser sem isso; não é só estética, sem
    escapar o relatório inteiro falha ao gerar quando a IA usa esses símbolos.
    """
    texto = _sanitizar_texto(texto)
    return texto.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _estilos():
    base = getSampleStyleSheet()
    return {
        "titulo": ParagraphStyle("titulo", parent=base["Title"], textColor=_AZUL, fontSize=18, fontName=_FONTE_NEGRITO),
        "subtitulo": ParagraphStyle("subtitulo", parent=base["Normal"], textColor=rl_colors.grey, fontSize=10, spaceAfter=12, fontName=_FONTE_REGULAR),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], textColor=_AZUL, spaceBefore=6, spaceAfter=4, fontName=_FONTE_NEGRITO),
        "normal": ParagraphStyle("normal", parent=base["Normal"], fontName=_FONTE_REGULAR),
        "criterio": ParagraphStyle("criterio", parent=base["Normal"], spaceAfter=4, leading=13, fontName=_FONTE_REGULAR),
        "dica": ParagraphStyle("dica", parent=base["Normal"], spaceAfter=4, leading=13, leftIndent=8, fontName=_FONTE_REGULAR),
    }


def montar_relatorio(
    leads: List[Any],
    criterios_por_lead: Dict[int, List[dict]],
    dicas_por_lead: Dict[int, List[dict]],
    *,
    regiao: str = "",
    segmento: str = "",
) -> bytes:
    """Monta o .pdf em memória e devolve os bytes (para rx.download).

    `leads` são objetos com os atributos de ProspectCompany (id, nome,
    razao_social, cnpj, priorizacao_score_final, priorizacao_classe).
    """
    estilos = _estilos()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        topMargin=1.5 * cm, bottomMargin=1.5 * cm, leftMargin=1.5 * cm, rightMargin=1.5 * cm,
    )

    elementos = [
        Paragraph("Relatório de Priorização de Leads", estilos["titulo"]),
        Paragraph(
            f"{_sanitizar_paragrafo(segmento) or '-'} · {_sanitizar_paragrafo(regiao) or '-'} · {len(leads)} lead(s) classificado(s)",
            estilos["subtitulo"],
        ),
    ]

    # --- tabela-resumo ---
    cabecalho = ["Empresa", "CNPJ", "Score", "Classe"]
    linhas = [cabecalho]
    for lead in leads:
        linhas.append([
            _sanitizar_texto(lead.razao_social or lead.nome),
            formatar_cnpj(lead.cnpj) or "-",
            str(lead.priorizacao_score_final if lead.priorizacao_score_final is not None else "-"),
            lead.priorizacao_classe or "-",
        ])

    tabela = Table(linhas, colWidths=[7 * cm, 4 * cm, 2.5 * cm, 3 * cm], repeatRows=1)
    estilo_tabela = [
        ("FONTNAME", (0, 0), (-1, -1), _FONTE_REGULAR),
        ("BACKGROUND", (0, 0), (-1, 0), _AZUL),
        ("TEXTCOLOR", (0, 0), (-1, 0), rl_colors.white),
        ("FONTNAME", (0, 0), (-1, 0), _FONTE_NEGRITO),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [rl_colors.white, _CINZA_CLARO]),
        ("GRID", (0, 0), (-1, -1), 0.5, rl_colors.HexColor("#e5e7eb")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    for i, lead in enumerate(leads, start=1):
        cor = _COR_CLASSE.get(lead.priorizacao_classe)
        if cor:
            estilo_tabela.append(("TEXTCOLOR", (3, i), (3, i), cor))
            estilo_tabela.append(("FONTNAME", (3, i), (3, i), _FONTE_NEGRITO))
    tabela.setStyle(TableStyle(estilo_tabela))
    elementos.append(tabela)

    # --- uma seção por lead ---
    for lead in leads:
        elementos.append(PageBreak())
        elementos.append(Paragraph(_sanitizar_paragrafo(lead.razao_social or lead.nome), estilos["titulo"]))
        score = lead.priorizacao_score_final if lead.priorizacao_score_final is not None else "-"
        elementos.append(
            Paragraph(f"Score final: <b>{score}/100</b> · Classe: <b>{lead.priorizacao_classe or '-'}</b>", estilos["subtitulo"])
        )

        elementos.append(Paragraph("Breakdown por critério", estilos["h2"]))
        for c in criterios_por_lead.get(lead.id, []):
            elementos.append(
                Paragraph(
                    f"<b>{_sanitizar_paragrafo(c.get('criterio'))}</b> · {c.get('pontos')} pontos: {_sanitizar_paragrafo(c.get('justificativa'))}",
                    estilos["criterio"],
                )
            )

        dicas = dicas_por_lead.get(lead.id, [])
        if dicas:
            elementos.append(Spacer(1, 6))
            elementos.append(Paragraph("Dicas de approach", estilos["h2"]))
            rotulo_tipo = {
                "gancho": "Gancho de abertura", "canal": "Canal recomendado",
                "dor": "Ponto de dor provável", "timing": "Timing sugerido",
            }
            for d in dicas:
                rotulo = rotulo_tipo.get(d.get("tipo"), d.get("tipo", "").title())
                elementos.append(Paragraph(f"<b>{rotulo}:</b> {_sanitizar_paragrafo(d.get('dica'))}", estilos["dica"]))

    doc.build(elementos)
    return buffer.getvalue()


def nome_do_arquivo(regiao: str, segmento: str, quando) -> str:
    """Mesmo padrão de nome do relatório de enriquecimento: data primeiro,
    sem acento nem espaço."""
    import re
    import unicodedata

    def _slug(texto: str) -> str:
        sem_acento = "".join(
            ch for ch in unicodedata.normalize("NFD", texto or "")
            if unicodedata.category(ch) != "Mn"
        )
        limpo = re.sub(r"[^A-Za-z0-9]+", "-", sem_acento).strip("-").lower()
        return limpo[:40]

    partes = [quando.strftime("%Y-%m-%d"), "priorizacao"]
    for t in (segmento, regiao):
        s = _slug(t)
        if s:
            partes.append(s)
    return "-".join(partes) + ".pdf"
