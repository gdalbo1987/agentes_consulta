"""Consultas de leitura sobre os e-mails classificados.

Módulo compartilhado pelo dashboard e pelas tools do Agente 3. Compartilhar é
deliberado: com duas implementações, o chat poderia dizer "12 e-mails urgentes"
enquanto a tabela na tela mostra 9, e não haveria como saber qual está certa.
Uma fonte só torna a divergência impossível.

As consultas devolvem dicionários ACHATADOS. Achatados porque o `foreach` do
Reflex não acessa dicionário aninhado tipado, e porque as tools do agente
serializam o retorno para JSON.

ATENÇÃO AO NOME DO MÓDULO. Ele era só de leitura, e hoje tem DUAS funções de
escrita, no fim do arquivo: `marcar_urgencia_tratada` e `excluir_email`. Elas
vivem aqui porque mexem no mesmo modelo que alimenta o painel, e separá-las num
módulo novo só espalharia a mesma responsabilidade.

Isso NÃO afrouxa a garantia do Agente 3. Ele continua somente leitura por
construção: `consulta_agent._construir_funcoes` monta uma a uma as funções que
viram tool, e nenhuma das duas está na lista. Quem for acrescentar tool nova
precisa manter isso, porque é a defesa estrutural contra injeção vinda do
conteúdo dos e-mails. Há um teste que falha se uma tool de escrita aparecer.
"""

import json
from datetime import datetime, timedelta
from typing import List, Optional

import reflex as rx
from sqlalchemy import func, or_

from sales_support_agent.models import (
    ClassificacaoRun,
    EmailClassificado,
    ResumoEmail,
    brt_now,
)
from sales_support_agent.services.classificacao_rules import rotulo

# Só e-mails que caíram numa das quatro classes aparecem em qualquer lugar. Os
# `ignorado` existem apenas para não serem reprocessados; mostrá-los encheria a
# tela de newsletter e de spam.
def _base(session, tenant_id: int):
    return session.query(EmailClassificado).filter(
        EmailClassificado.tenant_id == tenant_id,
        EmailClassificado.status == "classificado",
    )


def _achatar(email: EmailClassificado, resumo: Optional[ResumoEmail] = None) -> dict:
    dados = {
        "id": email.id,
        "assunto": email.assunto or "(sem assunto)",
        "remetente_nome": email.remetente_nome or "",
        "remetente_email": email.remetente_email or "",
        "cliente": email.remetente_nome or email.remetente_email or "-",
        "recebido_em": email.recebido_em.strftime("%d/%m/%Y %H:%M") if email.recebido_em else "-",
        "recebido_iso": email.recebido_em.isoformat() if email.recebido_em else "",
        "classe": email.classe,
        "classe_label": rotulo(email.classe),
        "urgente": bool(email.urgente),
        "importante": bool(email.importante),
        "urgencia_tratada": email.urgencia_tratada_em is not None,
        # Rótulo pronto para a tela e para o chat: as duas faixas são
        # mutuamente exclusivas, então uma palavra só descreve a linha.
        "prioridade": (
            "Urgente" if email.urgente else ("Importante" if email.importante else "Normal")
        ),
        "urgencia_prazo_horas": email.urgencia_prazo_horas,
        "web_link": email.graph_web_link or "",
    }
    if resumo is not None:
        dados.update(
            {
                "resumo": resumo.resumo or "",
                "acao_sugerida": resumo.acao_sugerida or "",
                "prazo_mencionado": resumo.prazo_mencionado or "",
                "resumo_disponivel": resumo.status == "done" and bool(resumo.resumo),
            }
        )
        try:
            dados["pontos_chave"] = json.loads(resumo.pontos_chave or "[]")
        except (ValueError, TypeError):
            dados["pontos_chave"] = []
    return dados


# ---------------------------------------------------------------------------
# Métricas das rodadas
# ---------------------------------------------------------------------------


def metricas_execucao(tenant_id: int) -> dict:
    """Números dos cards do dashboard.

    A duração média sai de `AVG(duracao_segundos)` sobre as rodadas concluídas,
    e não de subtrair timestamps em Python: é para isso que a coluna existe.
    Rodadas em andamento e com erro ficam de fora, senão a média mediria o
    tempo até a falha e não o tempo de trabalho.
    """
    with rx.session() as session:
        media = (
            session.query(func.avg(ClassificacaoRun.duracao_segundos))
            .filter(
                ClassificacaoRun.tenant_id == tenant_id,
                ClassificacaoRun.status == "done",
                ClassificacaoRun.duracao_segundos.isnot(None),
            )
            .scalar()
        )

        ultima = (
            session.query(ClassificacaoRun)
            .filter(
                ClassificacaoRun.tenant_id == tenant_id,
                ClassificacaoRun.status == "done",
            )
            .order_by(ClassificacaoRun.started_at.desc())
            .first()
        )

        total = _base(session, tenant_id).count()

        em_andamento = (
            session.query(ClassificacaoRun)
            .filter(
                ClassificacaoRun.tenant_id == tenant_id,
                ClassificacaoRun.status == "running",
            )
            .first()
        )

    return {
        "duracao_media": _duracao(media),
        "duracao_ultima": _duracao(ultima.duracao_segundos if ultima else None),
        "total_classificados": total,
        "ultima_rodada_classificados": ultima.classificados if ultima else 0,
        "ultima_rodada_quando": (
            ultima.finished_at.strftime("%d/%m/%Y %H:%M") if ultima and ultima.finished_at else "-"
        ),
        "ultima_rodada_origem": ultima.origem if ultima else "",
        "em_andamento": em_andamento is not None,
    }


def progresso_execucao(tenant_id: int) -> dict:
    """Andamento da rodada, lido do BANCO e não do State do navegador.

    É o que permite a barra de progresso funcionar para a execução AGENDADA,
    que roda noutro processo e nunca tocou o State de ninguém. Devolve também a
    identidade da última rodada concluída, para a tela saber que ela ACABOU de
    terminar e mostrar o aviso de concluído uma vez só.
    """
    with rx.session() as session:
        emandamento = (
            session.query(ClassificacaoRun)
            .filter(
                ClassificacaoRun.tenant_id == tenant_id,
                ClassificacaoRun.status == "running",
            )
            .order_by(ClassificacaoRun.started_at.desc())
            .first()
        )
        ultima = (
            session.query(ClassificacaoRun)
            .filter(
                ClassificacaoRun.tenant_id == tenant_id,
                ClassificacaoRun.status.in_(("done", "error")),
            )
            .order_by(ClassificacaoRun.id.desc())
            .first()
        )

        return {
            "em_andamento": emandamento is not None,
            "run_id": emandamento.id if emandamento else 0,
            "origem": emandamento.origem if emandamento else "",
            "processados": emandamento.processados if emandamento else 0,
            "total": emandamento.total_emails if emandamento else 0,
            "ultima_run_id": ultima.id if ultima else 0,
            "ultima_status": ultima.status if ultima else "",
            "ultima_origem": ultima.origem if ultima else "",
            "ultima_classificados": ultima.classificados if ultima else 0,
            "ultima_ignorados": ultima.ignorados if ultima else 0,
            "ultima_puladas": ultima.puladas if ultima else 0,
            "ultima_erro": ultima.erro if ultima else "",
        }


def _duracao(segundos) -> str:
    """Duração legível. Sem rodada concluída, `-`, e nunca "0s".

    "0s" diria que a rodada foi instantânea; `-` diz que não houve rodada, que
    é a informação verdadeira. O traço é simples, não travessão.
    """
    if segundos is None:
        return "-"
    segundos = int(segundos)
    if segundos < 60:
        return f"{segundos}s"
    minutos, resto = divmod(segundos, 60)
    if minutos < 60:
        return f"{minutos}min {resto}s" if resto else f"{minutos}min"
    horas, resto_min = divmod(minutos, 60)
    return f"{horas}h {resto_min}min"


# ---------------------------------------------------------------------------
# Listagens
# ---------------------------------------------------------------------------


def listar_emails(
    tenant_id: int,
    *,
    data_inicio: str = "",
    data_fim: str = "",
    apenas_urgentes: bool = False,
    apenas_importantes: bool = False,
    classe: str = "",
    limite: int = 200,
) -> List[dict]:
    """Tabela do dashboard. Datas em `AAAA-MM-DD`, INCLUSIVAS nas duas pontas.

    O fim vira 23:59:59 do dia informado: filtrar "até 07/08" e não ver o que
    chegou às 15h do dia 7 seria surpreendente para quem usa.

    Os dois filtros de prioridade combinam por OU, e não por E. As faixas são
    mutuamente exclusivas (`calcular_prioridade` devolve uma ou outra, nunca as
    duas), então intersectá-las devolveria lista vazia SEMPRE, e quem ligasse os
    dois interruptores concluiria que não há e-mail nenhum. Com os dois ligados,
    a leitura útil é "tudo o que tem alguma prioridade".
    """
    with rx.session() as session:
        consulta = _base(session, tenant_id)

        if data_inicio:
            consulta = consulta.filter(EmailClassificado.recebido_em >= _dia(data_inicio))
        if data_fim:
            consulta = consulta.filter(
                EmailClassificado.recebido_em <= _dia(data_fim, fim_do_dia=True)
            )
        if apenas_urgentes and apenas_importantes:
            consulta = consulta.filter(
                or_(
                    EmailClassificado.urgente.is_(True),
                    EmailClassificado.importante.is_(True),
                )
            )
        elif apenas_urgentes:
            consulta = consulta.filter(EmailClassificado.urgente.is_(True))
        elif apenas_importantes:
            consulta = consulta.filter(EmailClassificado.importante.is_(True))
        if classe:
            consulta = consulta.filter(EmailClassificado.classe == classe)

        linhas = consulta.order_by(EmailClassificado.recebido_em.desc()).limit(limite).all()
        return [_achatar(linha) for linha in linhas]


def _dia(texto: str, fim_do_dia: bool = False) -> datetime:
    momento = datetime.strptime(texto.strip(), "%Y-%m-%d")
    return momento.replace(hour=23, minute=59, second=59) if fim_do_dia else momento


def detalhe_email(tenant_id: int, email_id: int) -> Optional[dict]:
    """Um e-mail com o resumo do Agente 2, para o diálogo de detalhe."""
    with rx.session() as session:
        email = (
            session.query(EmailClassificado)
            .filter(
                EmailClassificado.tenant_id == tenant_id,
                EmailClassificado.id == email_id,
            )
            .first()
        )
        if not email:
            return None
        resumo = (
            session.query(ResumoEmail).filter(ResumoEmail.email_id == email_id).first()
        )

    dados = _achatar(email, resumo)
    if resumo is None:
        # Resumo ainda não gerado é diferente de resumo que falhou, e o painel
        # tem de dizer qual dos dois, em vez de mostrar um espaço em branco.
        dados.update(
            {"resumo": "", "pontos_chave": [], "acao_sugerida": "",
             "prazo_mencionado": "", "resumo_disponivel": False}
        )
    return dados


def urgencias(tenant_id: int, limite: int = 20) -> List[dict]:
    """Os urgentes AINDA NÃO TRATADOS, mais recentes primeiro, já com o resumo.

    O painel é uma FILA de trabalho, e não um relatório: o que já foi resolvido
    sai dela. Quem foi tratado continua no banco, na tabela e no Outlook, com a
    marcação de urgente intacta; some só daqui.
    """
    with rx.session() as session:
        linhas = (
            _base(session, tenant_id)
            .filter(EmailClassificado.urgente.is_(True))
            .filter(EmailClassificado.urgencia_tratada_em.is_(None))
            .order_by(EmailClassificado.recebido_em.desc())
            .limit(limite)
            .all()
        )
        ids = [linha.id for linha in linhas]
        resumos = {
            r.email_id: r
            for r in session.query(ResumoEmail).filter(ResumoEmail.email_id.in_(ids)).all()
        } if ids else {}

        return [_achatar(linha, resumos.get(linha.id)) for linha in linhas]


# ---------------------------------------------------------------------------
# Agregados (dashboard e tools do Agente 3)
# ---------------------------------------------------------------------------


def resumo_da_caixa(tenant_id: int) -> dict:
    from sales_support_agent.services.classificacao_rules import CLASSES

    with rx.session() as session:
        base = _base(session, tenant_id)
        total = base.count()
        urgentes = base.filter(EmailClassificado.urgente.is_(True)).count()
        importantes = base.filter(EmailClassificado.importante.is_(True)).count()

        por_classe = {}
        for classe in CLASSES:
            por_classe[classe] = (
                _base(session, tenant_id).filter(EmailClassificado.classe == classe).count()
            )

        periodo = session.query(
            func.min(EmailClassificado.recebido_em), func.max(EmailClassificado.recebido_em)
        ).filter(
            EmailClassificado.tenant_id == tenant_id,
            EmailClassificado.status == "classificado",
        ).first()

    return {
        "total": total,
        "urgentes": urgentes,
        "importantes": importantes,
        "por_classe": {rotulo(c): n for c, n in por_classe.items()},
        "periodo_de": periodo[0].strftime("%d/%m/%Y") if periodo and periodo[0] else "-",
        "periodo_ate": periodo[1].strftime("%d/%m/%Y") if periodo and periodo[1] else "-",
    }


def listar_clientes(tenant_id: int, limite: int = 50) -> List[dict]:
    """Remetentes distintos com contagem.

    Existe para o Agente 3 poder consultar a grafia exata ANTES de filtrar por
    cliente: o modelo não sabe se o cadastro diz "Metalúrgica Silva" ou
    "METALURGICA SILVA LTDA", e chutar produziria uma busca vazia que ele
    reportaria como "não há e-mails desse cliente".
    """
    with rx.session() as session:
        linhas = (
            session.query(
                EmailClassificado.remetente_email,
                func.max(EmailClassificado.remetente_nome),
                func.count(EmailClassificado.id),
            )
            .filter(
                EmailClassificado.tenant_id == tenant_id,
                EmailClassificado.status == "classificado",
            )
            .group_by(EmailClassificado.remetente_email)
            .order_by(func.count(EmailClassificado.id).desc())
            .limit(limite)
            .all()
        )

    return [
        {"email": email or "", "nome": nome or "", "total": total}
        for email, nome, total in linhas
    ]


def buscar_por_cliente(tenant_id: int, termo: str, limite: int = 20) -> List[dict]:
    padrao = f"%{(termo or '').strip()}%"
    with rx.session() as session:
        linhas = (
            _base(session, tenant_id)
            .filter(
                EmailClassificado.remetente_email.ilike(padrao)
                | EmailClassificado.remetente_nome.ilike(padrao)
            )
            .order_by(EmailClassificado.recebido_em.desc())
            .limit(limite)
            .all()
        )
        return [_achatar(linha) for linha in linhas]


def buscar_conteudo(tenant_id: int, termos: str, limite: int = 15) -> List[dict]:
    """Busca por assunto e pelo RESUMO, não pelo corpo cru.

    Duas razões. O resumo já passou pelo saneamento e pelo Agente 2, então é
    texto menor e menos hostil que o corpo original, o que reduz a superfície de
    injeção quando o resultado volta para o Agente 3. E o corpo cru traz
    assinatura, aviso de confidencialidade e histórico de resposta, que geram
    resultado falso em quase toda busca.
    """
    padrao = f"%{(termos or '').strip()}%"
    with rx.session() as session:
        linhas = (
            _base(session, tenant_id)
            .outerjoin(ResumoEmail, ResumoEmail.email_id == EmailClassificado.id)
            .filter(
                EmailClassificado.assunto.ilike(padrao) | ResumoEmail.resumo.ilike(padrao)
            )
            .order_by(EmailClassificado.recebido_em.desc())
            .limit(limite)
            .all()
        )
        return [_achatar(linha) for linha in linhas]


def ultima_execucao(tenant_id: int) -> Optional[dict]:
    with rx.session() as session:
        rodada = (
            session.query(ClassificacaoRun)
            .filter(ClassificacaoRun.tenant_id == tenant_id)
            .order_by(ClassificacaoRun.started_at.desc())
            .first()
        )
        if not rodada:
            return None
        return {
            "status": rodada.status,
            "origem": rodada.origem,
            "quando": rodada.started_at.strftime("%d/%m/%Y %H:%M") if rodada.started_at else "-",
            "duracao": _duracao(rodada.duracao_segundos),
            "classificados": rodada.classificados,
            "ignorados": rodada.ignorados,
            "urgentes": rodada.urgentes,
            "falhas": rodada.falhas,
        }


def recalcular_urgencia(tenant_id: int, janela_horas: int) -> int:
    """Re-marca os e-mails já gravados quando a janela de urgência muda.

    É por isto que `urgencia_prazo_horas` e `urgente_semantico` ficam guardados
    separados dos booleanos: mudar a janela é um UPDATE, e não uma
    reclassificação da caixa inteira no modelo. Devolve quantas linhas mudaram.

    Re-marca as DUAS faixas. Estreitar a janela não apaga a prioridade de um
    e-mail com data: ele deixa de ser urgente e passa a importante, porque o
    compromisso continua existindo, só não é mais para agora.
    """
    from sales_support_agent.services.classificacao_rules import calcular_prioridade

    alteradas = 0
    with rx.session() as session:
        for linha in _base(session, tenant_id).all():
            urgente, importante = calcular_prioridade(
                linha.urgencia_prazo_horas,
                bool(linha.urgente_semantico),
                janela_horas,
            )
            if urgente != linha.urgente or importante != linha.importante:
                linha.urgente = urgente
                linha.importante = importante
                alteradas += 1
        if alteradas:
            session.commit()
    return alteradas


# ---------------------------------------------------------------------------
# Escrita: as duas únicas operações de gravação deste módulo
# ---------------------------------------------------------------------------
# Elas moram aqui, e não no State, porque mexem no mesmo modelo de leitura que
# alimenta o painel E as tools do Agente 3. O agente continua SÓ LEITURA: ele
# não recebe nenhuma destas funções, e nunca deve receber.


def marcar_urgencia_tratada(tenant_id: int, email_id: int, tratada: bool = True) -> bool:
    """Tira (ou devolve) um e-mail da fila de urgências, SEM apagá-lo.

    Não mexe em `urgente`: aquilo é fato calculado a partir do prazo, e o
    recálculo da janela o reescreveria, trazendo de volta o que já foi tratado.
    Também não mexe no Outlook: a categoria `Urgente` continua na mensagem,
    porque ela descreve o e-mail, não a fila de trabalho de quem o lê.
    """
    with rx.session() as session:
        linha = (
            session.query(EmailClassificado)
            .filter(
                EmailClassificado.tenant_id == tenant_id,
                EmailClassificado.id == email_id,
            )
            .first()
        )
        if not linha:
            return False
        linha.urgencia_tratada_em = brt_now() if tratada else None
        session.commit()
        return True


def excluir_email(tenant_id: int, email_id: int) -> dict:
    """Apaga UM e-mail classificado e o resumo dele. Não toca no Outlook.

    Devolve `{"apagado": bool, "assunto": str}` para o chamador montar a
    mensagem sem precisar reabrir a linha.

    O resumo sai ANTES do e-mail: ele referencia `emailclassificado.id` por
    chave estrangeira, e na ordem inversa o PostgreSQL recusa e a transação
    inteira reverte.
    """
    with rx.session() as session:
        linha = (
            session.query(EmailClassificado)
            .filter(
                EmailClassificado.tenant_id == tenant_id,
                EmailClassificado.id == email_id,
            )
            .first()
        )
        if not linha:
            return {"apagado": False, "assunto": ""}

        assunto = linha.assunto or "(sem assunto)"
        session.query(ResumoEmail).filter(ResumoEmail.email_id == linha.id).delete(
            synchronize_session=False
        )
        session.flush()
        session.delete(linha)
        session.commit()
        return {"apagado": True, "assunto": assunto}
