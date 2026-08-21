"""Orquestração de uma rodada de classificação.

Gerador assíncrono com o mesmo contrato de eventos que o funil anterior usava:

    ("progress", processados, total, mensagem)
    ("done", resumo)
    ("error", mensagem_em_portugues)

Um caminho de código só, dois consumidores: o botão "Classificar agora" do
dashboard e o `scripts/classificar.py` que o agendador dispara. É isso que faz o
botão manual ser um teste de verdade do caminho agendado.

IDEMPOTÊNCIA É CONTROLE DE CUSTO, e não elegância. Cada e-mail reprocessado é
uma chamada paga à OpenAI. Um e-mail cujo `internet_message_id` já está no banco
é PULADO sem nenhuma chamada ao modelo. Vale para os ignorados também: sem linha
para eles, toda rodada mandaria a mesma newsletter ao modelo de novo.

SESSÃO DE BANCO NUNCA ATRAVESSA UM `await` DE REDE. Abre, lê, fecha, chama o
modelo e o Graph, reabre, grava. Uma sessão aberta durante a chamada do modelo
seguraria uma conexão do pool por segundos, e com a rodada inteira aberta numa
transação só, uma falha no último e-mail desfaria os anteriores.
"""

import json
from datetime import timedelta
from typing import AsyncIterator, Optional, Tuple

import reflex as rx

from sales_support_agent.models import (
    ClassificacaoRun,
    EmailClassificado,
    brt_now,
)
from sales_support_agent.services import classificacao_config, graph_client
from sales_support_agent.services.classificacao_rules import (
    CATEGORIA_URGENTE,
    CATEGORIAS,
    categorias_para,
)
from sales_support_agent.services.graph_client import GraphClientError

# Sobreposição de segurança na marca d'água. Reler duas horas já lidas é
# gratuito por causa do UNIQUE em (tenant_id, internet_message_id), e cobre
# e-mail que chegou enquanto a rodada anterior já estava varrendo.
SOBREPOSICAO_HORAS = 2

# Rodada `running` mais velha que isto é considerada morta. Sem esse limite, um
# processo derrubado deixaria a UI girando para sempre e travaria a rodada
# seguinte, que se recusa a começar com outra em andamento.
LIMITE_TRAVADO_MINUTOS = 20


def marca_dagua(tenant_id: int, lookback_horas: int) -> "datetime":
    """A partir de quando varrer.

    O e-mail mais recente já conhecido, menos a sobreposição, com piso em
    `agora - lookback_horas`. O piso é o que impede a primeira execução de uma
    caixa com anos de histórico de tentar classificar tudo.
    """
    agora = brt_now()
    piso = agora - timedelta(hours=max(1, lookback_horas))

    with rx.session() as session:
        mais_recente = (
            session.query(EmailClassificado.recebido_em)
            .filter(EmailClassificado.tenant_id == tenant_id)
            .order_by(EmailClassificado.recebido_em.desc())
            .first()
        )

    if not mais_recente or not mais_recente[0]:
        return piso
    return max(piso, mais_recente[0] - timedelta(hours=SOBREPOSICAO_HORAS))


def recuperar_rodada_travada(tenant_id: int) -> Optional[str]:
    """Fecha como erro a rodada que ficou `running` além do limite.

    Chamado no `on_load` do dashboard e antes de cada rodada nova. Sem isso, um
    processo morto deixa a plataforma achando que ainda há classificação em
    andamento, para sempre.
    """
    limite = brt_now() - timedelta(minutes=LIMITE_TRAVADO_MINUTOS)
    with rx.session() as session:
        travadas = (
            session.query(ClassificacaoRun)
            .filter(
                ClassificacaoRun.tenant_id == tenant_id,
                ClassificacaoRun.status == "running",
                ClassificacaoRun.started_at < limite,
            )
            .all()
        )
        if not travadas:
            return None
        for rodada in travadas:
            rodada.status = "error"
            rodada.erro = "Execução anterior não finalizou (processo interrompido)."
            rodada.finished_at = brt_now()
        session.commit()
        return f"{len(travadas)} execução(ões) travada(s) foram encerradas."


def descrever_rodada_em_andamento(tenant_id: int) -> str:
    """Por que a rodada está bloqueada, e até quando.

    "Já existe uma classificação em andamento" sozinho não ajuda: quem lê não
    sabe se é de agora, se é de ontem, se ela vai sair sozinha ou se precisa
    chamar alguém. Com origem, idade e o prazo de liberação, a mensagem vira
    uma decisão ("espero 3 minutos") em vez de um beco.
    """
    with rx.session() as session:
        rodada = (
            session.query(ClassificacaoRun)
            .filter(
                ClassificacaoRun.tenant_id == tenant_id,
                ClassificacaoRun.status == "running",
            )
            .order_by(ClassificacaoRun.started_at.desc())
            .first()
        )
        if not rodada:
            # Sem linha no banco, o bloqueio veio da tela e não da execução.
            return (
                "A tela achava que havia uma classificação em andamento, mas não "
                "há nenhuma no banco. Atualize a página e tente de novo."
            )

        origem = "automática" if rodada.origem == "agendado" else "manual"
        inicio = rodada.started_at
        if not inicio:
            return f"Já existe uma classificação {origem} em andamento."

        minutos = max(0, int((brt_now() - inicio).total_seconds() // 60))
        restam = LIMITE_TRAVADO_MINUTOS - minutos
        quando = inicio.strftime("%H:%M")

        if restam > 0:
            return (
                f"Já existe uma classificação {origem} em andamento, iniciada às "
                f"{quando} (há {minutos} min). Se ela tiver travado, será liberada "
                f"automaticamente em {restam} min."
            )
        return (
            f"Havia uma classificação {origem} parada desde as {quando}. Ela acabou "
            "de ser encerrada como travada; tente novamente agora."
        )


def ha_rodada_em_andamento(tenant_id: int) -> bool:
    with rx.session() as session:
        return (
            session.query(ClassificacaoRun)
            .filter(
                ClassificacaoRun.tenant_id == tenant_id,
                ClassificacaoRun.status == "running",
            )
            .first()
            is not None
        )


def _ja_conhecidos(tenant_id: int, ids: list) -> set:
    """Quais `internet_message_id` já estão no banco. A trava de custo."""
    if not ids:
        return set()
    with rx.session() as session:
        linhas = (
            session.query(EmailClassificado.internet_message_id)
            .filter(
                EmailClassificado.tenant_id == tenant_id,
                EmailClassificado.internet_message_id.in_(ids),
            )
            .all()
        )
    return {linha[0] for linha in linhas}


def _marcar_progresso(run_id: Optional[int], processados: int, total: int) -> None:
    """Escreve o andamento na linha da rodada, a cada e-mail.

    Sem isto, o progresso só existiria no State do navegador de quem clicou, e
    a execução AGENDADA rodaria invisível: quem abrisse o painel às 08:01 veria
    uma tela parada, sem saber que o agente estava trabalhando. Gravar na linha
    faz o andamento sobreviver a um reload e ficar visível para todo mundo.

    Sessão curta e própria, pelo mesmo motivo do resto do módulo: nada de
    segurar conexão atravessando chamada de rede.
    """
    if not run_id:
        return
    with rx.session() as session:
        rodada = session.get(ClassificacaoRun, run_id)
        if not rodada:
            return
        rodada.processados = processados
        rodada.total_emails = total
        session.commit()


def _gravar(tenant_id: int, run_id: int, email: dict, resultado: dict, **extra) -> int:
    with rx.session() as session:
        linha = EmailClassificado(
            tenant_id=tenant_id,
            run_id=run_id,
            internet_message_id=email["internet_message_id"],
            graph_message_id=extra.get("graph_message_id", email["graph_message_id"]),
            graph_conversation_id=email.get("graph_conversation_id", ""),
            graph_web_link=email.get("graph_web_link", ""),
            remetente_email=email.get("remetente_email", ""),
            remetente_nome=email.get("remetente_nome", ""),
            assunto=email.get("assunto", ""),
            corpo_texto=email.get("corpo_texto", ""),
            recebido_em=email["recebido_em"],
            classe=resultado.get("classe", ""),
            urgente=resultado.get("urgente", False),
            importante=resultado.get("importante", False),
            urgencia_prazo_horas=resultado.get("urgencia_prazo_horas"),
            urgente_semantico=resultado.get("urgente_semantico", False),
            confianca=resultado.get("confianca", 0),
            justificativa=resultado.get("justificativa", ""),
            status=extra.get("status", "classificado"),
            categoria_aplicada=extra.get("categoria_aplicada", False),
            movido=extra.get("movido", False),
            pasta_destino_id=extra.get("pasta_destino_id", ""),
            erro=extra.get("erro", ""),
            classificado_em=brt_now(),
        )
        session.add(linha)
        session.commit()
        session.refresh(linha)
        return linha.id


async def stream_classificacao(
    tenant_id: int,
    *,
    user_email: str = "",
    origem: str = "manual",
    run_id: Optional[int] = None,
    dry_run: bool = False,
) -> AsyncIterator[Tuple]:
    """Executa uma rodada. `run_id` já criado pelo chamador, que fecha a linha.

    `dry_run` lê e classifica mas NÃO escreve nada no Graph nem no banco. É o
    jeito seguro de conferir a primeira execução numa caixa de produção antes de
    deixar o agente mexer nela.
    """
    avisos = []
    resumo = {
        "total_emails": 0, "processados": 0, "classificados": 0, "ignorados": 0,
        "urgentes": 0, "importantes": 0, "puladas": 0, "falhas": 0, "resumidos": 0,
        "avisos": avisos, "usage_classificacao": {"input": 0, "output": 0},
        "usage_resumo": {"input": 0, "output": 0},
    }

    # 1. Configuração. Falha aqui é ANTES de gastar o primeiro token.
    cfg = classificacao_config.get_config(tenant_id)
    # O interruptor vale só para o AGENDADO. "Classificar agora" é ação
    # deliberada de uma pessoa que está olhando a tela, e precisa funcionar
    # justamente quando o automático está parado: é assim que se testa a
    # configuração antes de ligar o agente para valer.
    if origem == "agendado" and not cfg["ativo"]:
        yield (
            "error",
            "As execuções automáticas estão paradas. Use o botão Iniciar no painel.",
        )
        return

    pastas = {p["classe"]: p for p in classificacao_config.get_pastas(tenant_id)}
    pendentes = [c for c, p in pastas.items() if not p["pasta_id"]]
    if pendentes:
        faltando = ", ".join(sorted(pendentes))
        yield (
            "error",
            "Configure a pasta do Outlook de cada classe antes de classificar. "
            f"Faltam: {faltando}. O mapeamento fica no painel, e a rodada não "
            "começa sem ele para não arquivar e-mail na pasta errada.",
        )
        return

    # 2. Leitura da caixa.
    desde = marca_dagua(tenant_id, cfg["lookback_horas"])
    try:
        mensagens = await graph_client.listar_mensagens(
            desde=desde, limite=cfg["max_emails_por_execucao"]
        )
    except GraphClientError as erro:
        yield ("error", erro.mensagem)
        return

    resumo["total_emails"] = len(mensagens)
    if not mensagens:
        yield ("done", resumo)
        return

    # 3. Idempotência: quem já está no banco não custa nada.
    conhecidos = _ja_conhecidos(tenant_id, [m["internet_message_id"] for m in mensagens])
    pendentes_msgs = [m for m in mensagens if m["internet_message_id"] not in conhecidos]
    resumo["puladas"] = len(mensagens) - len(pendentes_msgs)

    total = len(pendentes_msgs)
    _marcar_progresso(run_id, 0, total)
    if not total:
        yield ("progress", 0, 0, f"{resumo['puladas']} e-mail(s) já classificados; nada novo.")
        yield ("done", resumo)
        return

    from sales_support_agent.services.classificacao_agent import classificar_email
    from sales_support_agent.services.resumo_agent import resumir_email

    # Melhor esforço: categoria fora da lista mestra funciona, só sai sem cor.
    if not dry_run:
        await graph_client.garantir_categorias_mestre(
            list(CATEGORIAS.values()) + [CATEGORIA_URGENTE]
        )

    for indice, email in enumerate(pendentes_msgs, start=1):
        assunto = (email.get("assunto") or "(sem assunto)")[:60]
        _marcar_progresso(run_id, indice - 1, total)
        yield ("progress", indice - 1, total, f"Classificando: {assunto}")

        if email.get("sem_internet_message_id"):
            avisos.append(
                f"O e-mail '{assunto}' não trouxe internetMessageId; a "
                "deduplicação dele usa o id do Graph e é menos confiável."
            )

        # --- classificação -------------------------------------------------
        ok, resultado, erro, usage = await classificar_email(email, cfg["janela_urgencia_horas"])
        resumo["usage_classificacao"]["input"] += usage["input"]
        resumo["usage_classificacao"]["output"] += usage["output"]

        if not ok:
            resumo["falhas"] += 1
            avisos.append(f"Falha ao classificar '{assunto}': {erro}")
            if not dry_run:
                _gravar(tenant_id, run_id, email, {}, status="falhou", erro=erro)
            _marcar_progresso(run_id, indice, total)
            yield ("progress", indice, total, f"Falha ao classificar: {assunto}")
            continue

        resumo["processados"] += 1

        # --- fora das quatro classes: não marca, não move -------------------
        if not resultado["classe"]:
            resumo["ignorados"] += 1
            if not dry_run:
                _gravar(tenant_id, run_id, email, resultado, status="ignorado")
            _marcar_progresso(run_id, indice, total)
            yield ("progress", indice, total, f"Fora das quatro classes: {assunto}")
            continue

        if dry_run:
            resumo["classificados"] += 1
            resumo["urgentes"] += int(resultado["urgente"])
            resumo["importantes"] += int(resultado.get("importante", False))
            _marcar_progresso(run_id, indice, total)
            yield ("progress", indice, total, f"Classificado (simulação): {assunto}")
            continue

        # --- marcar ANTES de mover -----------------------------------------
        # Depois do move o id antigo não existe mais, e um PATCH nele volta 404.
        destino = pastas[resultado["classe"]]["pasta_id"]
        graph_id = email["graph_message_id"]
        categoria_ok = False
        movido = False

        try:
            await graph_client.aplicar_categorias(
                graph_id,
                categorias_para(
                    resultado["classe"],
                    resultado["urgente"],
                    resultado.get("importante", False),
                ),
            )
            categoria_ok = True
            graph_id = await graph_client.mover_mensagem(graph_id, destino)
            movido = True
        except GraphClientError as erro_graph:
            if erro_graph.fatal:
                # Credencial ou permissão: insistir nos próximos é desperdício.
                _gravar(
                    tenant_id, run_id, email, resultado, status="falhou",
                    erro=erro_graph.mensagem, categoria_aplicada=categoria_ok,
                    movido=movido, graph_message_id=graph_id,
                )
                yield ("error", erro_graph.mensagem)
                return

            resumo["falhas"] += 1
            avisos.append(f"Falha ao arquivar '{assunto}': {erro_graph.mensagem}")
            _gravar(
                tenant_id, run_id, email, resultado, status="falhou",
                erro=erro_graph.mensagem, categoria_aplicada=categoria_ok,
                movido=movido, graph_message_id=graph_id,
                pasta_destino_id=destino if movido else "",
            )
            _marcar_progresso(run_id, indice, total)
            yield ("progress", indice, total, f"Falha ao arquivar: {assunto}")
            continue

        email_id = _gravar(
            tenant_id, run_id, email, resultado, status="classificado",
            categoria_aplicada=categoria_ok, movido=movido,
            graph_message_id=graph_id, pasta_destino_id=destino,
        )
        resumo["classificados"] += 1
        resumo["urgentes"] += int(resultado["urgente"])
        resumo["importantes"] += int(resultado.get("importante", False))

        # --- resumo (Agente 2) ---------------------------------------------
        # Best effort: uma falha aqui não desfaz a classificação, que já moveu
        # o e-mail. O resumo pode ser gerado depois; o arquivamento, não.
        #
        # O contador FICA em `indice - 1` aqui de propósito: o e-mail ainda não
        # terminou. Só o texto muda, para a tela mostrar que o trabalho passou
        # da classificação para o resumo. Sem este aviso a barra parecia travada
        # durante a chamada mais demorada do e-mail, e quem olhava concluía que
        # o processo tinha morrido.
        yield ("progress", indice - 1, total, f"Resumindo: {assunto}")

        ok_r, resumo_txt, erro_r, usage_r = await resumir_email(email, resultado["classe"])
        resumo["usage_resumo"]["input"] += usage_r["input"]
        resumo["usage_resumo"]["output"] += usage_r["output"]
        if ok_r:
            _gravar_resumo(tenant_id, email_id, resumo_txt)
            resumo["resumidos"] += 1
        else:
            _gravar_resumo(tenant_id, email_id, None, erro=erro_r)
            avisos.append(f"Não foi possível resumir '{assunto}': {erro_r}")

        _marcar_progresso(run_id, indice, total)
        yield ("progress", indice, total, f"Classificado e resumido: {assunto}")

    # O total é reafirmado aqui porque um e-mail que caiu num `continue` de
    # falha pode ter deixado o contador atrás. A tela precisa fechar em 100%
    # antes de a barra sair, ou o usuário fica com a impressão de que o
    # processo foi interrompido no meio.
    _marcar_progresso(run_id, total, total)
    yield ("progress", total, total, "Finalizando...")
    yield ("done", resumo)


def _gravar_resumo(tenant_id: int, email_id: int, dados: Optional[dict], erro: str = "") -> None:
    """Grava (ou marca como falho) o resumo do Agente 2.

    Importado aqui embaixo e não no topo porque o `ResumoEmail` só interessa a
    esta função; manter o import local deixa o topo do módulo com o que a
    classificação de fato usa.
    """
    from sales_support_agent.models import ResumoEmail

    with rx.session() as session:
        linha = (
            session.query(ResumoEmail).filter(ResumoEmail.email_id == email_id).first()
        )
        if not linha:
            linha = ResumoEmail(tenant_id=tenant_id, email_id=email_id)
            session.add(linha)

        if dados:
            linha.resumo = dados.get("resumo", "")
            linha.pontos_chave = json.dumps(dados.get("pontos_chave", []), ensure_ascii=False)
            linha.acao_sugerida = dados.get("acao_sugerida", "")
            linha.prazo_mencionado = dados.get("prazo_mencionado", "")
            linha.modelo = dados.get("modelo", "")
            linha.status = "done"
            linha.erro = ""
            linha.gerado_em = brt_now()
        else:
            linha.status = "failed"
            linha.erro = erro
        session.commit()
