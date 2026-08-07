"""Orquestração da fase de Priorização (+ Approach opcional).

Roda sobre leads já enriquecidos (`ProspectCompany.enrichment_status in
("completed", "partial")`), mesmo contrato de eventos das fases anteriores
(services/prospect_agent.py, services/enrichment.py):

    ("progress", processados, total, mensagem)
    ("done", resumo: dict)
    ("error", mensagem_pt_BR)

IDEMPOTÊNCIA (mesmo princípio de custo do enriquecimento, aplicado aqui a
gasto de token OpenAI em vez de crédito KipFlow): lead com
`priorizacao_status == "done"` é pulado por padrão, sem nenhuma chamada de IA.

Falha pontual num lead (agente indisponível, saída malformada) não derruba o
lote inteiro — o lead fica `priorizacao_status="failed"`, é registrado em
avisos, e o processamento continua (mesmo princípio best-effort de
prospect_agent.py e enrichment.py).

Módulo separado de services/enrichment.py de propósito: aquele é
especificamente sobre economia de crédito da KipFlow (um escopo diferente,
já bem definido) — misturar infla um arquivo com propósito próprio.
"""

import json
from types import SimpleNamespace
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import reflex as rx

from sales_support_agent.models import CompanyContact, ProspectCompany, brt_now
from sales_support_agent.services import priorizacao_agent as pa
from sales_support_agent.services import approach_agent as aa
from sales_support_agent.services.enrichment_rules import STATUS_CONSIDERADOS_ENRIQUECIDOS
from sales_support_agent.services.priorizacao_rules import (
    calcular_score_final,
    definir_classe_prioridade,
    validar_criterios,
)

# Status de enriquecimento elegíveis para priorização: o lead precisa ter
# passado pela fase anterior com algum dado (input = leads já enriquecidos).
# Fonte única compartilhada com o Dashboard e o agente de Insights IA.
ENRICHMENT_STATUS_ELEGIVEIS = STATUS_CONSIDERADOS_ENRIQUECIDOS


def _montar_lead_data(c: ProspectCompany, contatos: List[CompanyContact]) -> Dict[str, Any]:
    """Registro do lead usado pelos dois agentes (mesmo formato para ambos)."""
    cidade_uf = " / ".join([p for p in (c.cidade, c.estado) if p])
    return {
        "nome": c.nome,
        "razao_social": c.razao_social,
        "cidade_uf": cidade_uf or c.localizacao,
        "localizacao": c.localizacao,
        # Dados da fase de PROSPECÇÃO (services/prospect_agent.py) — sem eles o
        # critério "Fit com ICP" (30% do peso) não tinha nenhum dado próprio,
        # só o texto livre de "segmento".
        "icp_score": c.icp_score,
        "justificativa_match": c.justificativa_match,
        "porte": c.porte,
        "faturamento_estimado": c.faturamento_estimado,
        "segmento": c.segmento,
        "segmento_identificado": c.segmento_identificado,
        "status_cadastral": c.status_cadastral,
        "alerta_situacao": c.alerta_situacao,
        "idade_empresa_anos": c.idade_empresa_anos,
        "telefone": c.telefone,
        "telefone_whatsapp": c.telefone_whatsapp,
        "website_principal": c.website_principal,
        "website": c.website,
        "linkedin_url": c.linkedin_url,
        "enrichment_percentage": c.enrichment_percentage,
        "contatos": [
            {"nome": ct.nome, "cargo": ct.cargo, "senioridade": ct.senioridade, "origem": ct.origem}
            for ct in contatos
        ],
    }


async def stream_priorizacao(
    tenant_id: int,
    search_run_id: int,
    incluir_approach: bool,
    company_ids: Optional[List[int]] = None,
) -> AsyncIterator[Tuple]:
    """Executa a priorização (e o approach, se `incluir_approach`) sobre os
    leads elegíveis de uma pesquisa.

    `company_ids=None` processa todos os elegíveis do search_run; uma lista
    processa só os informados (execução individual usa uma lista de 1 item).
    """
    from sales_support_agent.services.enrichment import noticias_por_empresa

    avisos: List[str] = []

    with rx.session() as session:
        query = session.query(ProspectCompany).filter(
            ProspectCompany.tenant_id == tenant_id,
            ProspectCompany.search_run_id == search_run_id,
        )
        if company_ids is not None:
            query = query.filter(ProspectCompany.id.in_(company_ids))
        todas = query.all()

        elegiveis = [c for c in todas if c.enrichment_status in ENRICHMENT_STATUS_ELEGIVEIS]
        nao_enriquecidas = len(todas) - len(elegiveis)
        ja_priorizadas = [c for c in elegiveis if c.priorizacao_status == "done"]
        pendentes_ids = [c.id for c in elegiveis if c.priorizacao_status != "done"]
        puladas = len(ja_priorizadas)

    if nao_enriquecidas:
        avisos.append(
            f"{nao_enriquecidas} lead(s) ainda não enriquecido(s) o suficiente e foram ignorados."
        )
    if puladas:
        avisos.append(f"{puladas} lead(s) já tinham priorização e foram pulados.")

    total = len(pendentes_ids)
    if total == 0:
        yield ("done", {
            "processados": 0, "puladas": puladas, "falhas": 0, "avisos": avisos,
            "usage_priorizacao": {"input": 0, "output": 0},
            "usage_approach": {"input": 0, "output": 0},
        })
        return

    yield ("progress", 0, total, "Preparando leads para priorização...")

    usage_priorizacao = {"input": 0, "output": 0}
    usage_approach = {"input": 0, "output": 0}
    processados = 0
    falhas = 0

    for cid in pendentes_ids:
        with rx.session() as session:
            c = session.get(ProspectCompany, cid)
            if not c:
                continue
            nome = c.nome
            contatos = (
                session.query(CompanyContact)
                .filter(CompanyContact.company_id == cid)
                .all()
            )
            lead_data = _montar_lead_data(c, contatos)

        # Notícias específicas da empresa (fase de prospecção, ver
        # noticias_por_empresa) — buscadas uma vez só e reaproveitadas pelos
        # dois agentes (priorização E approach), sem I/O duplicado.
        _lead_ref = SimpleNamespace(nome=nome, id=cid)
        noticias = list(noticias_por_empresa(tenant_id, search_run_id, [_lead_ref]).get(cid, []))

        ok, resultado, erro, usage = await pa.classificar_lead(lead_data, noticias)
        usage_priorizacao["input"] += usage["input"]
        usage_priorizacao["output"] += usage["output"]

        if not ok or resultado is None:
            with rx.session() as session:
                c = session.get(ProspectCompany, cid)
                if c:
                    c.priorizacao_status = "failed"
                    c.priorizacao_erro = erro
                    session.commit()
            falhas += 1
            avisos.append(f"'{nome}': falha na priorização ({erro}).")
            processados += 1
            yield ("progress", processados, total, f"Priorizando leads - {processados}/{total} ({nome[:40]})")
            continue

        criterios = validar_criterios([cr.model_dump() for cr in resultado.criterios])
        score_final = calcular_score_final(criterios)
        classe = definir_classe_prioridade(score_final)

        with rx.session() as session:
            c = session.get(ProspectCompany, cid)
            if c:
                c.priorizacao_status = "done"
                c.priorizacao_score_final = score_final
                c.priorizacao_classe = classe
                c.priorizacao_criterios = json.dumps(criterios, ensure_ascii=False)
                c.priorizacao_executado_em = brt_now()
                c.priorizacao_erro = ""
                session.commit()

        if incluir_approach:
            ok_a, resultado_a, erro_a, usage_a = await aa.classificar_approach(
                lead_data, criterios, score_final, classe, noticias
            )
            usage_approach["input"] += usage_a["input"]
            usage_approach["output"] += usage_a["output"]
            with rx.session() as session:
                c = session.get(ProspectCompany, cid)
                if c:
                    if ok_a and resultado_a is not None:
                        c.approach_status = "done"
                        c.approach_dicas = json.dumps(
                            [d.model_dump() for d in resultado_a.dicas], ensure_ascii=False
                        )
                        c.approach_executado_em = brt_now()
                        c.approach_erro = ""
                    else:
                        c.approach_status = "failed"
                        c.approach_erro = erro_a
                        avisos.append(f"'{nome}': falha no approach ({erro_a}).")
                    session.commit()

        processados += 1
        yield ("progress", processados, total, f"Priorizando leads - {processados}/{total} ({nome[:40]})")

    yield ("done", {
        "processados": processados,
        "puladas": puladas,
        "falhas": falhas,
        "avisos": avisos,
        "usage_priorizacao": usage_priorizacao,
        "usage_approach": usage_approach,
    })
