"""Quais empresas JÁ CONHECIDAS entram numa nova pesquisa.

Antes de disparar a pesquisa, o usuário responde se quer incluir as empresas
que a base já tem (para renovar as notícias delas) ou apenas as inéditas. Este
módulo monta as duas listas que essa resposta exige:

- `conhecidas`: tudo o que a organização já encontrou, em qualquer pesquisa.
  Vai para o agente como lista de EXCLUSÃO nos dois modos, para o orçamento de
  buscas ser gasto procurando empresa nova em vez de reencontrar as mesmas.
- `reincluir`: as conhecidas que pertencem a ESTA linha de pesquisa. Só é
  preenchida no modo "incluir", e são elas que voltam para a fase de notícias.

O recorte de "esta linha de pesquisa" é decidido por um modelo de linguagem, e
não por comparação de texto, porque região e segmento são digitados à mão: "RS",
"Rio Grande do Sul" e "Serra Gaúcha" descrevem o mesmo território, e uma
igualdade literal exigiria que o usuário repetisse a grafia da pesquisa anterior
para a renovação funcionar. Se o modelo falhar (rede, chave, cota), cai na
comparação literal normalizada, que é conservadora mas nunca reincluí a empresa
errada.

Este módulo não fala com a web: o único agente aqui é um classificador de texto,
sem tools.
"""
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel

from agents import Agent, ModelSettings, Runner
from openai.types.shared import Reasoning

from sales_support_agent.services.prompt_rules import REGRA_SEM_TRAVESSAO
from sales_support_agent.services.prospect_agent import EmpresaConhecidaInput
from sales_support_agent.services.settings import get_agent_config

# Teto de empresas reincluídas numa rodada. Não é limite de negócio, é limite de
# TEMPO: cada 5 empresas reincluídas viram um lote de notícias de até 5 minutos,
# e uma base grande faria uma pesquisa demorar horas sem que ninguém tivesse
# pedido isso. Ao cortar, as de maior icp_score ficam, e a tela avisa.
MAX_REINCLUIR = 60

# Effort fixo e baixo: a tarefa é dizer se dois recortes de pesquisa são o
# mesmo, não pesquisar nada. O modelo acompanha o do agente de prospecção
# (mesma etapa do funil, mesma configuração em /admin), só o esforço é forçado.
EFFORT_ESCOPO = "low"


class EmpresaConhecida(EmpresaConhecidaInput):
    """Empresa da base no formato que a pesquisa consome, mais o `id` local.

    Herda de `EmpresaConhecidaInput` (services/prospect_agent.py) para as listas
    montadas aqui irem direto para `stream_prospect_search`, sem conversão no
    meio. O `id` não é de interesse do agente, só do agrupamento por recorte
    feito neste módulo.
    """

    id: int


class EscopoDaPesquisa(BaseModel):
    """Resultado do módulo, pronto para `stream_prospect_search`."""

    conhecidas: List[EmpresaConhecida] = []
    reincluir: List[EmpresaConhecida] = []
    usage: Dict[str, int] = {"input": 0, "output": 0}
    avisos: List[str] = []


class _RecorteAnterior(BaseModel):
    """Uma pesquisa anterior, do ponto de vista do classificador."""

    indice: int
    regiao: str
    segmento: str
    qtd_empresas: int


class _EscopoAgentOutput(BaseModel):
    indices_equivalentes: List[int] = []


def _sem_acento_minusculo(texto: str) -> str:
    normalizado = unicodedata.normalize("NFKD", (texto or "").strip().lower())
    return "".join(c for c in normalizado if not unicodedata.combining(c))


def _build_agent(model: str) -> Agent:
    return Agent(
        name="Escopo de Pesquisa",
        instructions=(
            "Você compara recortes de prospecção B2B no Brasil. Recebe o recorte de "
            "uma pesquisa que está começando (região e segmento) e uma lista de "
            "pesquisas já executadas, cada uma com seu próprio recorte.\n\n"
            "Sua tarefa: dizer quais pesquisas anteriores cobrem o MESMO conjunto de "
            "empresas que a pesquisa atual pretende alcançar. Considere equivalentes "
            "os recortes em que as empresas encontradas por uma seriam candidatas "
            "legítimas da outra, mesmo com texto diferente. Trate como equivalentes:\n"
            "- sinônimos, abreviações e siglas ('RS' e 'Rio Grande do Sul'; 'TI' e "
            "'tecnologia da informação');\n"
            "- grafias, plurais e acentuação diferentes;\n"
            "- recorte geográfico que contém o outro ('Brasil' contém 'Serra Gaúcha'; "
            "'Rio Grande do Sul' contém 'Caxias do Sul');\n"
            "- descrições do mesmo setor com palavras diferentes ('indústria "
            "metalúrgica' e 'metalurgia e siderurgia').\n\n"
            "NÃO trate como equivalentes recortes de setores realmente distintos "
            "(mineração e educação) nem regiões que não se contêm ('Nordeste' e 'Sul').\n\n"
            "Na dúvida entre marcar e não marcar, MARQUE: o custo de reprocessar uma "
            "empresa parecida é pequeno, e deixar de fora uma empresa da mesma linha "
            "de prospecção faz o usuário perder a atualização que pediu.\n\n"
            "Responda apenas com a lista de índices das pesquisas equivalentes. Lista "
            "vazia é uma resposta válida quando nenhuma delas se encaixa.\n\n"
            f"{REGRA_SEM_TRAVESSAO}"
        ),
        model=model,
        model_settings=ModelSettings(reasoning=Reasoning(effort=EFFORT_ESCOPO)),
        output_type=_EscopoAgentOutput,
    )


def _build_prompt(regiao: str, segmento: str, anteriores: List[_RecorteAnterior]) -> str:
    linhas = "\n".join(
        f"{r.indice}. região: {r.regiao} | segmento: {r.segmento} "
        f"({r.qtd_empresas} empresa(s) na base)"
        for r in anteriores
    )
    return (
        f"Pesquisa atual:\n- região: {regiao}\n- segmento: {segmento}\n\n"
        f"Pesquisas já executadas:\n{linhas}\n\n"
        "Quais índices cobrem o mesmo conjunto de empresas da pesquisa atual?"
    )


def _extrair_usage(result) -> Dict[str, int]:
    try:
        usage = result.context_wrapper.usage
        return {"input": int(usage.input_tokens or 0), "output": int(usage.output_tokens or 0)}
    except Exception:
        return {"input": 0, "output": 0}


def _equivalentes_por_texto(
    regiao: str, segmento: str, anteriores: List[_RecorteAnterior]
) -> List[int]:
    """Plano B do classificador: igualdade de texto normalizado (sem acento,
    minúsculo). Só acerta a repetição literal da pesquisa anterior, que é o caso
    mais comum, e nunca reinclui empresa de outro recorte."""
    alvo = (_sem_acento_minusculo(regiao), _sem_acento_minusculo(segmento))
    return [
        r.indice
        for r in anteriores
        if (_sem_acento_minusculo(r.regiao), _sem_acento_minusculo(r.segmento)) == alvo
    ]


async def _classificar(
    regiao: str, segmento: str, anteriores: List[_RecorteAnterior]
) -> Tuple[List[int], Dict[str, int], List[str]]:
    """(índices equivalentes, tokens gastos, avisos)."""
    model, _ = get_agent_config("prospect")
    try:
        resultado = await Runner.run(
            _build_agent(model), _build_prompt(regiao, segmento, anteriores), max_turns=2
        )
    except Exception:
        # Falhar aqui não pode derrubar a pesquisa: ela ainda tem tudo de que
        # precisa para rodar, só a renovação fica mais restrita.
        return (
            _equivalentes_por_texto(regiao, segmento, anteriores),
            {"input": 0, "output": 0},
            [
                "Não foi possível comparar esta pesquisa com as anteriores por IA. "
                "A comparação caiu no modo literal: só entram empresas de pesquisas "
                "com região e segmento escritos exatamente iguais."
            ],
        )

    saida: Any = resultado.final_output
    indices = list(getattr(saida, "indices_equivalentes", []) or [])
    validos = {r.indice for r in anteriores}
    return ([i for i in indices if i in validos], _extrair_usage(resultado), [])


def _carregar_base(tenant_id: int) -> Tuple[List[EmpresaConhecida], Dict[int, Tuple[str, str]]]:
    """(empresas da organização, {company_id: (região, segmento) da pesquisa de origem}).

    A pesquisa de origem é a MAIS RECENTE que encontrou a empresa: é o que
    `ensure_companies_materialized` mantém em `search_run_id` ao reencontrar uma
    empresa já conhecida.
    """
    import reflex as rx

    from sales_support_agent.models import ProspectCompany, SearchRun

    with rx.session() as session:
        empresas = (
            session.query(ProspectCompany)
            .filter(ProspectCompany.tenant_id == tenant_id)
            .all()
        )
        runs = session.query(SearchRun).filter(SearchRun.tenant_id == tenant_id).all()

    recorte_do_run = {r.id: (r.regiao or "", r.segmento or "") for r in runs}
    conhecidas = [
        EmpresaConhecida(
            id=c.id,
            nome=c.nome,
            website=c.website_principal or c.website,
            cnpj=c.cnpj,
            localizacao=c.localizacao or "",
            segmento_identificado=c.segmento_identificado or "",
            icp_score=c.icp_score or 0,
            justificativa_match=c.justificativa_match or "",
        )
        for c in empresas
    ]
    recorte_da_empresa = {
        c.id: recorte_do_run.get(c.search_run_id, ("", "")) for c in empresas
    }
    return conhecidas, recorte_da_empresa


async def montar_escopo(
    tenant_id: int, regiao: str, segmento: str, incluir_conhecidas: bool,
) -> EscopoDaPesquisa:
    """Monta as listas de empresas conhecidas e reincluídas desta pesquisa.

    `incluir_conhecidas=False` devolve só a lista de exclusão: nenhuma empresa
    da base volta para a fase de notícias, e o classificador nem chega a rodar
    (não haveria o que decidir, e seria custo de token à toa).
    """
    conhecidas, recorte_da_empresa = _carregar_base(tenant_id)
    escopo = EscopoDaPesquisa(conhecidas=conhecidas)
    if not incluir_conhecidas or not conhecidas:
        return escopo

    # Um recorte por par (região, segmento): pesquisas repetidas do mesmo
    # recorte viram uma linha só, e o classificador recebe uma lista curta.
    por_recorte: Dict[Tuple[str, str], List[EmpresaConhecida]] = {}
    for empresa in conhecidas:
        por_recorte.setdefault(recorte_da_empresa.get(empresa.id, ("", "")), []).append(empresa)

    recortes = [
        _RecorteAnterior(indice=i, regiao=chave[0], segmento=chave[1], qtd_empresas=len(v))
        for i, (chave, v) in enumerate(sorted(por_recorte.items()), start=1)
        if chave != ("", "")
    ]
    if not recortes:
        escopo.avisos.append(
            f"As {len(conhecidas)} empresa(s) da base não têm a região e o segmento "
            "da pesquisa que as encontrou, então não foi possível identificar quais "
            "pertencem a esta linha de pesquisa. Nenhuma foi reincluída."
        )
        return escopo

    indices, usage, avisos = await _classificar(regiao, segmento, recortes)
    escopo.usage = usage
    escopo.avisos = list(avisos)

    por_indice = {r.indice: (r.regiao, r.segmento) for r in recortes}
    selecionadas: List[EmpresaConhecida] = []
    for indice in indices:
        selecionadas.extend(por_recorte.get(por_indice[indice], []))

    # Reinclusão pedida e nada compatível: dizer isso é obrigatório, não
    # cosmético. Sem a mensagem, o usuário recebe uma pesquisa só de empresas
    # novas e não tem como distinguir "a base não tinha nada desta linha de
    # pesquisa" de "a funcionalidade não funcionou".
    if not selecionadas:
        escopo.avisos.append(
            f"Nenhuma das {len(conhecidas)} empresa(s) da base pertence a esta linha "
            f"de pesquisa ({regiao} / {segmento}), então nenhuma foi reincluída. As "
            "notícias delas continuam as da pesquisa que as encontrou."
        )
        return escopo

    # Maior icp_score primeiro: se o corte por tempo entrar em ação, ele tira as
    # empresas de menor aderência, não as primeiras da lista.
    selecionadas.sort(key=lambda e: -e.icp_score)
    if len(selecionadas) > MAX_REINCLUIR:
        escopo.avisos.append(
            f"{len(selecionadas)} empresas conhecidas se encaixam nesta pesquisa, "
            f"acima do teto de {MAX_REINCLUIR} por rodada. As {MAX_REINCLUIR} de maior "
            "aderência tiveram as notícias renovadas; as demais entram numa próxima "
            "rodada."
        )
        selecionadas = selecionadas[:MAX_REINCLUIR]

    escopo.reincluir = selecionadas
    return escopo
