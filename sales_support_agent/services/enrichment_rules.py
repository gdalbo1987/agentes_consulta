"""Regras de negócio do enriquecimento — funções PURAS, sem I/O e sem rede.

Tudo aqui é determinístico e testável isoladamente: mapeamentos de porte/status,
cálculo de idade da empresa, escolha de telefone/website e — o mais importante —
a fórmula ÚNICA do percentual de enriquecimento.

Regra do projeto: o percentual é calculado AQUI, persistido em
`ProspectCompany.enrichment_percentage`, e o frontend apenas LÊ o valor
persistido. Nunca recalcule o percentual no frontend nem em outro lugar do
backend — uma fórmula, um lugar.
"""

import re
from datetime import date, datetime
from typing import List, Optional, Tuple

from prospect_agent.services.normalizers import normalizar_cnpj  # noqa: F401 (reexport)

# ---------------------------------------------------------------------------
# Os 12 campos do contrato de enriquecimento. A ordem espelha o requisito de
# negócio (1 = razão social ... 12 = e-mail de contato).
# ---------------------------------------------------------------------------
CAMPOS_ENRIQUECIMENTO: Tuple[str, ...] = (
    "razao_social",           # 1
    "cnpj",                   # 2
    "cidade",                 # 3  (Cidade/Estado — considerado preenchido pela cidade)
    "idade_empresa_anos",     # 4  (calculado a partir de data_inicio_atividade)
    "porte",                  # 5  (Pequena | Média | Grande)
    "segmento",               # 6
    "faturamento_estimado",   # 7  (é uma FAIXA, não valor exato)
    "status_cadastral",       # 8  (Ativa | Inativa | Baixada)
    "website_principal",      # 9
    "telefone",               # 10
    "contatos",               # 11 (preenchido quando há >= 1 contato decisor)
    "email_contato",          # 12 (preenchido quando >= 1 contato tem e-mail)
)

TOTAL_CAMPOS = len(CAMPOS_ENRIQUECIMENTO)  # 12


# ---------------------------------------------------------------------------
# Datasets pedidos à KipFlow. `partners` e `debts` ficam FORA de propósito:
# estão fora do escopo desta fase e custam crédito à toa.
# ---------------------------------------------------------------------------
DATASETS_EMPRESA = ["basic", "complete", "address", "online_presence"]


# ---------------------------------------------------------------------------
# Filtros de contatos decisores. Valores EXATOS dos enums da doc da KipFlow
# (/social/v1/personas/search) — não inventar variações, o filtro é literal.
#
# A busca é feita em NÍVEIS, do mais sênior para o menos (ver
# NIVEIS_SENIORIDADE abaixo). Isso importa porque a KipFlow cobra por pessoa
# retornada e não garante ordenação: pedindo tudo de uma vez, as vagas do plano
# poderiam ser gastas com supervisores enquanto havia um diretor disponível.
# Os níveis são disjuntos entre si, então ninguém é cobrado duas vezes.
# ---------------------------------------------------------------------------

# Nível 1 — quem decide a compra.
SENIORIDADES_DECISORAS = ["C-SUITE / DIRETOR", "GERENTE"]
# Nível 2 — liderança operacional. Aceito por decisão de negócio: medido em
# campo que empresas tradicionais (transporte, logística) simplesmente não têm
# diretor nem gerente no LinkedIn, e sem este nível ficariam com zero contatos.
SENIORIDADES_LIDERANCA = ["COORDENADOR", "SUPERVISOR"]

NIVEIS_SENIORIDADE = (SENIORIDADES_DECISORAS, SENIORIDADES_LIDERANCA)

# Lista original do escopo (Compradores + Líderes Técnicos). Mantida como
# referência: é o filtro mais preciso, porém o que menos encontra.
AREAS_PERFIL_ORIGINAL = [
    "COMPRAS",         # perfil "Compradores"
    "TECNOLOGIA",      # perfis "Líderes Técnicos"
    "DESENVOLVIMENTO",
    "DADOS",
    "ENGENHARIA",
]

# Lista em uso. Acrescenta as áreas operacionais (numa transportadora quem
# decide sobre manutenção/frota está em OPERACOES ou MANUTENCAO, não em
# COMPRAS) e as áreas de dono/gestão, que em empresa pequena são o decisor real.
AREAS_ALVO = AREAS_PERFIL_ORIGINAL + [
    "OPERACOES",
    "MANUTENCAO",
    "LOGISTICA",
    "TRANSPORTES",
    "INDUSTRIAL",
    "QUALIDADE",
    "PRESIDENCIA",
    "SOCIO",
    "ADMINISTRATIVO",
]

# Compatibilidade: nome antigo usado antes da busca em níveis.
SENIORIDADES_ALVO = SENIORIDADES_DECISORAS


# ---------------------------------------------------------------------------
# Quadro societário (QSA da Receita Federal, fonte GRATUITA).
#
# É a principal fonte de decisores desde a estratégia híbrida: em empresa
# tradicional/familiar quem decide a compra é o sócio-administrador, não um
# perfil de LinkedIn. Vem sem canal direto (nem e-mail nem telefone pessoal),
# então a abordagem é pelo telefone da empresa perguntando pela pessoa.
# ---------------------------------------------------------------------------

# Qualificações da Receita ordenadas por poder de decisão. A posição na tupla é
# a prioridade: quando há mais sócios que vagas do plano, ficam os primeiros.
PRIORIDADE_QUALIFICACAO_QSA = (
    "PRESIDENTE",
    "DIRETOR",
    "ADMINISTRADOR",
    "SOCIO-ADMINISTRADOR",
    "SOCIO-GERENTE",
    "CONSELHEIRO DE ADMINISTRACAO",
    "SOCIO",
    "TITULAR",
    "PROPRIETARIO",
)


def _rank_qualificacao(qualificacao: str) -> int:
    """Menor número = mais decisório. Desconhecido vai para o fim."""
    q = _sem_acento((qualificacao or "").upper())
    for i, alvo in enumerate(PRIORIDADE_QUALIFICACAO_QSA):
        if alvo in q:
            return i
    return len(PRIORIDADE_QUALIFICACAO_QSA)


def ordenar_socios(socios: List[dict]) -> List[dict]:
    """Ordena o QSA do mais decisório para o menos.

    Presidente antes de Diretor, Diretor antes de Sócio genérico. Empate é
    resolvido pela data de entrada mais antiga (quem está há mais tempo tende a
    ser o sócio de referência).
    """
    return sorted(
        socios or [],
        key=lambda s: (_rank_qualificacao(s.get("qualificacao", "")), s.get("desde") or "9999"),
    )


def socio_para_contato(socio: dict) -> dict:
    """Converte um sócio do QSA no formato de CompanyContact.

    `senioridade` recebe o rótulo do enum da KipFlow mais próximo, para a
    tela e os filtros tratarem QSA e LinkedIn de forma uniforme.
    """
    qualificacao = socio.get("qualificacao", "")
    rank = _rank_qualificacao(qualificacao)
    # Presidente/Diretor equivalem ao topo; o resto entra como sócio/gestão.
    senioridade = "C-SUITE / DIRETOR" if rank <= 1 else "SOCIO / ADMINISTRACAO"
    return {
        "nome": socio.get("nome", "").strip(),
        "cargo": qualificacao,
        "senioridade": senioridade,
        "area": "SOCIO",
        "perfil_url": "",
        "perfil_public_id": None,
        "origem": "qsa",
    }


def calcular_idade_empresa(data_inicio: Optional[str], hoje: Optional[date] = None) -> Optional[int]:
    """Idade em anos completos a partir de `data_inicio_atividade`.

    A API não entrega a idade pronta — é derivada. Aceita 'YYYY-MM-DD' e
    'DD/MM/YYYY'. Retorna None se a data for inválida ou estiver no futuro.
    """
    if not data_inicio:
        return None
    texto = str(data_inicio).strip()[:10]
    inicio = None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            inicio = datetime.strptime(texto, fmt).date()
            break
        except ValueError:
            continue
    if inicio is None:
        return None

    ref = hoje or date.today()
    if inicio > ref:
        return None
    return ref.year - inicio.year - ((ref.month, ref.day) < (inicio.month, inicio.day))


# Sufixos de escala, do mais longo para o mais curto (ordem importa: "MILH" tem
# de ser testado antes de "MIL", e "MIL" antes de "MI", senão "MIL" viraria milhão).
# A API mistura as duas notações — "DE R$ 4,8 MILHÕES A R$ 300 MILHÕES" e "100M A 300M".
_SUFIXOS_ESCALA = (
    ("BILH", 1_000_000_000),
    ("MILH", 1_000_000),
    ("MIL", 1_000),
    ("BI", 1_000_000_000),
    ("MI", 1_000_000),
    ("B", 1_000_000_000),
    ("M", 1_000_000),
    ("K", 1_000),
)

_PADRAO_VALOR = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(BILH\w*|MILH\w*|MIL\b|BI\b|MI\b|B\b|M\b|K\b)?",
    re.IGNORECASE,
)


def _maior_valor_escalado(texto: Optional[str]) -> Optional[float]:
    """Maior valor de uma faixa, já aplicando o sufixo de escala de cada número.

    'DE R$ 4,8 MILHÕES A R$ 300 MILHÕES' -> 300_000_000
    '100M A 300M'                        -> 300_000_000
    'DE 100 A 250 FUNCIONARIOS'          -> 250
    Cada número carrega a própria escala: um multiplicador global aplicado ao
    texto inteiro erraria em faixas mistas.
    """
    if not texto:
        return None
    valores = []
    for numero, sufixo in _PADRAO_VALOR.findall(str(texto).upper()):
        # pt-BR: vírgula é decimal; ponto pode ser separador de milhar.
        limpo = numero.replace(".", "").replace(",", ".") if "," in numero else numero.replace(".", "")
        try:
            valor = float(limpo)
        except ValueError:
            continue
        escala = 1.0
        if sufixo:
            for prefixo, mult in _SUFIXOS_ESCALA:
                if sufixo.startswith(prefixo):
                    escala = mult
                    break
        valores.append(valor * escala)
    return max(valores) if valores else None


# UFs brasileiras: sigla <- nome por extenso (sem acento, maiúsculo).
_UF_POR_NOME = {
    "ACRE": "AC", "ALAGOAS": "AL", "AMAPA": "AP", "AMAZONAS": "AM",
    "BAHIA": "BA", "CEARA": "CE", "DISTRITO FEDERAL": "DF",
    "ESPIRITO SANTO": "ES", "GOIAS": "GO", "MARANHAO": "MA",
    "MATO GROSSO DO SUL": "MS", "MATO GROSSO": "MT", "MINAS GERAIS": "MG",
    "PARAIBA": "PB", "PARANA": "PR", "PERNAMBUCO": "PE", "PIAUI": "PI",
    "RIO GRANDE DO NORTE": "RN", "RIO GRANDE DO SUL": "RS",
    "RIO DE JANEIRO": "RJ", "RONDONIA": "RO", "RORAIMA": "RR",
    "SANTA CATARINA": "SC", "SAO PAULO": "SP", "SERGIPE": "SE",
    "TOCANTINS": "TO", "PARA": "PA",
}

_SIGLAS_UF = set(_UF_POR_NOME.values())


def _sem_acento(texto: str) -> str:
    return (
        texto.replace("Á", "A").replace("Â", "A").replace("Ã", "A").replace("À", "A")
        .replace("É", "E").replace("Ê", "E").replace("Í", "I")
        .replace("Ó", "O").replace("Ô", "O").replace("Õ", "O")
        .replace("Ú", "U").replace("Ç", "C")
    )


def normalizar_uf(valor: Optional[str]) -> Optional[str]:
    """Converte a UF para a sigla de 2 letras.

    A KipFlow devolve `uf` por extenso ("RIO DE JANEIRO"), não a sigla — sem
    isso a tela mostra "RIO DE JANEIRO / RIO DE JANEIRO" (cidade/UF redundante).
    Aceita tanto sigla quanto nome completo.
    """
    if not valor:
        return None
    t = _sem_acento(str(valor).strip().upper())
    if t in _SIGLAS_UF:
        return t
    return _UF_POR_NOME.get(t)


def mapear_porte(
    porte: Optional[str],
    faixa_funcionarios: Optional[str] = None,
    faixa_faturamento: Optional[str] = None,
) -> Optional[str]:
    """Traduz o porte da KipFlow para as 3 categorias de negócio.

    A API NÃO usa 'Pequena/Média/Grande' — ela devolve valores da Receita
    ('MEI', 'MICRO EMPRESA', 'EMPRESA DE PEQUENO PORTE', 'DEMAIS'). O valor
    'DEMAIS' é um balde genérico que engloba de média a multinacional, então
    quando ele aparece o desempate é feito por número de funcionários e, na
    falta dele, por faturamento.

    Critérios (documentados de propósito, são escolha de negócio):
    - Funcionários (referência IBGE): até 99 -> Pequena; 100-499 -> Média; 500+ -> Grande.
    - Faturamento (referência BNDES): até R$ 4,8 mi -> Pequena;
      até R$ 300 mi -> Média; acima -> Grande.

    Sem nenhum sinal utilizável retorna None — melhor deixar o campo vazio no
    percentual do que chutar um porte errado.
    """
    p = (porte or "").strip().upper()
    if p in ("MEI", "MICRO EMPRESA", "MICROEMPRESA", "ME"):
        return "Pequena"
    if p in ("EMPRESA DE PEQUENO PORTE", "EPP", "PEQUENO PORTE"):
        return "Pequena"

    # 'DEMAIS' (ou vazio/desconhecido): desempata pelos sinais quantitativos.
    n = _maior_valor_escalado(faixa_funcionarios)
    if n is not None:
        if n <= 99:
            return "Pequena"
        if n <= 499:
            return "Média"
        return "Grande"

    f = _maior_valor_escalado(faixa_faturamento)
    if f is not None:
        if f <= 4_800_000:
            return "Pequena"
        if f <= 300_000_000:
            return "Média"
        return "Grande"

    return None


def mapear_status(situacao_cadastral: Optional[str]) -> Optional[str]:
    """Agrupa a situação cadastral da Receita nas 3 categorias de negócio.

    ATIVA -> Ativa | BAIXADA -> Baixada | SUSPENSA, INAPTA, NULA -> Inativa.
    'Baixada' é separada de 'Inativa' de propósito: baixa é encerramento
    definitivo, enquanto suspensa/inapta são situações potencialmente
    reversíveis — muda a decisão comercial.
    """
    s = (situacao_cadastral or "").strip().upper()
    if not s:
        return None
    if s == "ATIVA":
        return "Ativa"
    if s == "BAIXADA":
        return "Baixada"
    if s in ("SUSPENSA", "INAPTA", "NULA"):
        return "Inativa"
    return None


# Situações especiais da Receita, do mais grave para o menos. A ordem importa:
# uma razão social pode citar mais de um termo, e queremos o mais severo.
# Os stems são propositalmente curtos para pegar variações ("FALIDO", "FALIDA",
# "FALENCIA", "EM LIQUIDACAO JUDICIAL", "EM RECUPERACAO EXTRA JUDICIAL"...).
_SITUACOES_ESPECIAIS = (
    ("FALID", "Falência"),
    ("FALENC", "Falência"),
    ("LIQUIDACAO", "Liquidação"),
    ("INTERVENCAO", "Intervenção"),
    ("RECUPERACAO EXTRA", "Recuperação extrajudicial"),
    ("RECUPERACAO JUDICIAL", "Recuperação judicial"),
    ("RECUPERACAO", "Recuperação judicial"),
    ("CONCORDATA", "Concordata"),
)


def detectar_alerta_situacao(
    situacao_especial: Optional[str] = None,
    razao_social: Optional[str] = None,
) -> Optional[str]:
    """Sinaliza recuperação judicial, falência, liquidação e afins.

    Duas entradas porque as fontes divergem:
    - a Receita (BrasilAPI/Minha Receita, grátis) traz o campo estruturado
      `situacao_especial` — é a fonte preferencial;
    - a KipFlow não expõe esse campo, mas a `razao_social` costuma carregar o
      sufixo ("... LTDA EM RECUPERACAO JUDICIAL"), que serve de fallback.

    Isto NÃO é redundante com `mapear_status`: a Receita mantém a empresa como
    "ATIVA" durante a recuperação judicial. Sem esta flag, uma empresa em
    recuperação desde 2019 entraria na lista como saudável.

    Retorna None quando não há situação especial — o caso da grande maioria.
    """
    for texto in (situacao_especial, razao_social):
        if not texto:
            continue
        t = _sem_acento(str(texto).upper())
        for stem, rotulo in _SITUACOES_ESPECIAIS:
            if stem in t:
                return rotulo
    return None


def escolher_website(sites: Optional[List[dict]]) -> Optional[str]:
    """Site oficial de maior confiabilidade (campos reais do SiteDto).

    Descarta sites do contador — são o escritório de contabilidade, não a
    empresa. Ordena por `confiabilidade` desc.
    """
    if not sites:
        return None
    candidatos = [
        s for s in sites
        if isinstance(s, dict) and s.get("site") and not s.get("pertence_contador")
    ]
    if not candidatos:
        return None
    candidatos.sort(key=lambda s: s.get("confiabilidade") or 0, reverse=True)
    return candidatos[0].get("site")


def escolher_telefone(telefones: Optional[List[dict]]) -> Tuple[Optional[str], bool]:
    """Melhor telefone (campos reais do TelefoneDto) -> (telefone, é_whatsapp).

    Prioridade: descarta telefone do contador, prefere WhatsApp (canal de
    abordagem comercial) e, dentro disso, o maior `score_original`.
    """
    if not telefones:
        return (None, False)
    candidatos = [
        t for t in telefones
        if isinstance(t, dict) and t.get("telefone_completo") and not t.get("pertence_contador")
    ]
    if not candidatos:
        return (None, False)
    candidatos.sort(
        key=lambda t: (bool(t.get("whatsapp")), t.get("score_original") or 0),
        reverse=True,
    )
    melhor = candidatos[0]
    return (melhor.get("telefone_completo"), bool(melhor.get("whatsapp")))


# Prefixos de serviço (não-geográficos): não têm DDD.
_PREFIXOS_NAO_GEOGRAFICOS = ("0300", "0500", "0800", "0900")


def formatar_telefone(valor: Optional[str]) -> Optional[str]:
    """Padroniza o telefone para (XX) XXXXX-XXXX / (XX) XXXX-XXXX.

    As duas fontes entregam formatos diferentes — a Receita manda
    `ddd_telefone_1` colado ("2125551234"), a KipFlow manda `telefone_completo`
    às vezes com DDI ("5521999998888") e às vezes já mascarado — então a tabela
    misturava estilos. Normaliza pelos dígitos:

    - 13/12 dígitos começando em 55 → descarta o DDI (é sempre Brasil aqui);
    - 11 dígitos → celular, `(XX) 9XXXX-XXXX`;
    - 10 dígitos → fixo, `(XX) XXXX-XXXX`.

    Qualquer outro tamanho é devolvido como veio (só aparado): um número que
    não bate com o padrão brasileiro ainda é melhor exibido do que descartado —
    quem for ligar precisa vê-lo.
    """
    if not valor:
        return None
    texto = str(valor).strip()
    if not texto:
        return None
    digitos = re.sub(r"\D", "", texto)

    # DDI do Brasil: só remove se o que sobra tiver tamanho de telefone.
    if len(digitos) in (12, 13) and digitos.startswith("55"):
        digitos = digitos[2:]

    # Um zero à esquerda tem dois significados opostos, e confundi-los estraga
    # o número (ambos os casos vieram de dados reais da Receita):
    #  - 0800/0300/0500/0900 são NÃO-geográficos: não têm DDD a separar. Sem a
    #    guarda, "0800 123 4567" (11 dígitos) virava "(08) 00123-4567";
    #  - "02124450910" é o prefixo de discagem antes do DDD — aí o zero sobra
    #    e precisa ser descartado para o DDD real (21) aparecer.
    if digitos[:4] in _PREFIXOS_NAO_GEOGRAFICOS:
        return texto
    if digitos.startswith("0"):
        digitos = digitos.lstrip("0")

    if len(digitos) == 11:
        return f"({digitos[:2]}) {digitos[2:7]}-{digitos[7:]}"
    if len(digitos) == 10:
        return f"({digitos[:2]}) {digitos[2:6]}-{digitos[6:]}"
    return texto


def extrair_linkedin_public_id(linkedin_url: Optional[str]) -> Optional[str]:
    """Extrai o `company_public_id` da URL do LinkedIn.

    ECONOMIA DE CRÉDITO: o `linkedin_url` já vem no dataset `online_presence`,
    que nós pagamos de qualquer jeito. Extrair o public_id daqui evita a
    chamada de R$ 0,49 em /social/v1/companies/search — numa rodada de 30
    empresas isso é ~R$ 15 economizados.
    """
    if not linkedin_url:
        return None
    m = re.search(r"linkedin\.com/company/([^/?#\s]+)", str(linkedin_url), re.IGNORECASE)
    if not m:
        return None
    return m.group(1).strip().strip("/") or None


def sigla_uf_de_localizacao(localizacao: Optional[str]) -> Optional[str]:
    """Tenta extrair a sigla da UF do texto livre de localização da pesquisa.

    Usado apenas para melhorar o fallback de busca por nome
    (/intelligence/v1/company-match aceita `sigla_uf`).
    """
    if not localizacao:
        return None
    texto = str(localizacao).upper()
    ufs = {
        "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS",
        "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC",
        "SP", "SE", "TO",
    }
    for m in re.findall(r"\b([A-Z]{2})\b", texto):
        if m in ufs:
            return m

    limpo = _sem_acento(texto)
    for nome, sigla in _UF_POR_NOME.items():
        if nome in limpo:
            return sigla
    return None


def calcular_percentual(company, qtd_contatos: int = 0, qtd_emails: int = 0) -> int:
    """% de enriquecimento = campos preenchidos / 12, arredondado.

    FÓRMULA ÚNICA do projeto (ver docstring do módulo). `company` é qualquer
    objeto com os atributos de ProspectCompany (duck typing, para manter esta
    função pura e sem importar models).

    `qtd_contatos` e `qtd_emails` entram por parâmetro, e não são lidos de
    `company`, porque moram em outra tabela (`CompanyContact`) — ler dali
    obrigaria esta função a fazer I/O e deixaria de ser pura.

    CONSEQUÊNCIA DESEJADA de o e-mail ser o 12º campo: uma empresa com todo o
    cadastro e contatos, mas sem nenhum e-mail, para em 92% e fica `partial`,
    não `completed`. Como só `completed` é pulada na próxima execução, ela volta
    à fila e ganha nova chance de e-mail quando a cota da Hunter renovar.
    """
    preenchidos = 0
    for campo in CAMPOS_ENRIQUECIMENTO:
        if campo == "contatos":
            if qtd_contatos > 0:
                preenchidos += 1
            continue
        if campo == "email_contato":
            if qtd_emails > 0:
                preenchidos += 1
            continue
        valor = getattr(company, campo, None)
        if valor is None:
            continue
        if isinstance(valor, str) and not valor.strip():
            continue
        preenchidos += 1
    return round(preenchidos / TOTAL_CAMPOS * 100)


def definir_status(percentual: int) -> str:
    """Status derivado do percentual: 100% -> completed; 1-99% -> partial; 0 -> failed.

    Só `completed` faz a empresa ser PULADA numa próxima execução (regra de
    economia de crédito) — `partial` e `failed` continuam elegíveis para
    reprocessamento dos campos que faltam.
    """
    if percentual >= 100:
        return "completed"
    if percentual > 0:
        return "partial"
    return "failed"


# `enrichment_status` para o qual o lead conta como "já enriquecido" (tem
# dado suficiente pra ser útil em priorização/dashboards/insights) — usado por
# services/priorizacao.py, o Dashboard e o agente de Insights IA. Só
# `completed`/`partial` contam: `pending`/`in_progress`/`failed` não têm dado
# aproveitável ainda.
STATUS_CONSIDERADOS_ENRIQUECIDOS: Tuple[str, ...] = ("completed", "partial")


# Os mesmos limiares de faturamento já usados em `mapear_porte` (referência
# BNDES: até R$ 4,8 mi = pequena, até R$ 300 mi = média, acima = grande) — uma
# só fonte de verdade para os dois lugares que precisam bucketizar faturamento.
_LIMITE_FATURAMENTO_PEQUENA = 4_800_000
_LIMITE_FATURAMENTO_MEDIA = 300_000_000


def bucket_faturamento(faturamento_estimado: Optional[str]) -> str:
    """Agrupa a faixa livre de faturamento (ex.: '100M A 300M', 'DE R$ 4,8
    MILHÕES A R$ 300 MILHÕES') num de 4 baldes fixos, para gráficos.

    Reaproveita `_maior_valor_escalado` (o mesmo parser usado por
    `mapear_porte`) em vez de reinterpretar o texto — uma fórmula, um lugar.
    """
    valor = _maior_valor_escalado(faturamento_estimado)
    if valor is None:
        return "Não informado"
    if valor <= _LIMITE_FATURAMENTO_PEQUENA:
        return "Até R$ 4,8 milhões"
    if valor <= _LIMITE_FATURAMENTO_MEDIA:
        return "R$ 4,8 mi a R$ 300 mi"
    return "Acima de R$ 300 milhões"
