"""Configuração operacional do Agente 1: horários, janela de urgência e pastas.

Separado de `services/settings.py` de propósito. Aquele guarda CREDENCIAL, é do
super admin, e o que a UI lê de lá é só um booleano "configurado". Este guarda
configuração de operação, é editado pelo usuário PADRÃO no `/dashboard`, é por
organização, e a UI precisa ler os valores de volta para reexibi-los.

Mesma disciplina de `ensure_*` do resto do projeto: cria o que falta e NUNCA
sobrescreve o que já existe, para que rodar o seed de novo não apague a
configuração de quem já usa.

Sem cache: cada leitura abre sessão e consulta na hora, para que uma mudança
valha na próxima rodada sem reiniciar o servidor.
"""

from datetime import datetime, timedelta
from typing import Optional

import reflex as rx

from sales_support_agent.models import ClassificacaoConfig, PastaClasse, brt_now
from sales_support_agent.services.classificacao_rules import CLASSES, PASTAS_PADRAO


class ConfiguracaoDesatualizada(Exception):
    """Alguém salvou a configuração depois que esta tela a carregou.

    A configuração é da ORGANIZAÇÃO e a tela guarda os valores em campos de
    formulário. Sem esta checagem, um usuário com o painel aberto desde antes
    salvaria os valores VELHOS que ainda estão na tela dele e desfaria, em
    silêncio, o que o colega acabou de gravar. Pior: a linha de autoria passaria
    a creditá-lo por um valor que ele nunca escolheu, e a única pista seria
    alguém reparar que o horário voltou sozinho.
    """

    def __init__(self, autor: str, quando: Optional[datetime]):
        self.autor = autor
        self.quando = quando
        super().__init__(self.mensagem)

    @property
    def mensagem(self) -> str:
        quem = self.autor or "Outro usuário"
        if self.quando:
            return (
                f"{quem} alterou esta configuração em "
                f"{self.quando.strftime('%d/%m/%Y às %H:%M')}. Recarregue antes "
                "de salvar, para não desfazer o que a pessoa gravou."
            )
        return (
            f"{quem} alterou esta configuração depois que esta tela carregou. "
            "Recarregue antes de salvar."
        )


# ---------------------------------------------------------------------------
# Configuração geral
# ---------------------------------------------------------------------------


def ensure_config(tenant_id: int) -> None:
    """Cria a linha de configuração da organização, se ainda não existir."""
    with rx.session() as session:
        linha = (
            session.query(ClassificacaoConfig)
            .filter(ClassificacaoConfig.tenant_id == tenant_id)
            .first()
        )
        if not linha:
            session.add(ClassificacaoConfig(tenant_id=tenant_id))
            session.commit()


def get_config(tenant_id: int) -> dict:
    """Configuração como dicionário achatado.

    Achatado porque o `foreach` do Reflex não acessa dicionário aninhado
    tipado, e porque quem consome isto (o orquestrador e o dashboard) não deve
    depender do objeto do SQLModel continuar vivo depois que a sessão fecha.
    """
    with rx.session() as session:
        linha = (
            session.query(ClassificacaoConfig)
            .filter(ClassificacaoConfig.tenant_id == tenant_id)
            .first()
        )
        if not linha:
            linha = ClassificacaoConfig(tenant_id=tenant_id)

        return {
            "horario_1": linha.horario_1,
            "horario_2": linha.horario_2,
            "janela_urgencia_horas": int(linha.janela_urgencia_horas),
            "lookback_horas": int(linha.lookback_horas),
            "max_emails_por_execucao": int(linha.max_emails_por_execucao),
            "ativo": bool(linha.ativo),
            "ultima_execucao_agendada": linha.ultima_execucao_agendada,
            "atualizado_por_nome": linha.atualizado_por_nome or "",
            "atualizado_por_email": linha.atualizado_por_email or "",
            "updated_at": linha.updated_at,
            "versao": int(linha.versao or 0),
            "ultimo_tick_em": linha.ultimo_tick_em,
            "ultimo_tick_resultado": linha.ultimo_tick_resultado or "",
        }


def carimbo_config(tenant_id: int) -> dict:
    """Só quem alterou por último e quando. Uma linha, três colunas.

    Existe separado de `get_config` porque a sondagem do painel chama isto a
    cada poucos segundos, em toda aba aberta, e só para comparar o carimbo. Ler
    a configuração inteira para descartar tudo menos a data seria desperdício
    multiplicado pelo número de telas abertas.
    """
    with rx.session() as session:
        linha = (
            session.query(ClassificacaoConfig)
            .filter(ClassificacaoConfig.tenant_id == tenant_id)
            .first()
        )
        if not linha:
            return {"versao": 0, "nome": "", "email": ""}
        return {
            "versao": int(linha.versao or 0),
            "nome": linha.atualizado_por_nome or "",
            "email": linha.atualizado_por_email or "",
        }


def salvar_config(
    tenant_id: int,
    *,
    autor_nome: str = "",
    autor_email: str = "",
    versao_esperada: int = -1,
    **campos,
) -> None:
    """Grava só os campos passados (chave ausente não é tocada).

    `autor_*` identifica a PESSOA que salvou, e é o que o painel exibe para
    todos os usuários: a configuração é da organização, então quem abre a tela
    precisa saber de quem é o horário que está no ar.

    `updated_at` só avança quando há autor, e isso é deliberado. Quem mais
    escreve nesta linha é `marcar_execucao_agendada`, duas vezes por dia, sem
    nenhuma intervenção humana. Se ela empurrasse o carimbo, o painel diria
    "alterado hoje às 16:00" depois de toda rodada automática, e o registro de
    quem configurou apontaria para uma alteração que ninguém fez. O disparo
    agendado tem a coluna própria dele, `ultima_execucao_agendada`, então nada
    se perde ao deixar este carimbo para as edições de gente.

    `versao_esperada` é a versão que a tela leu ao carregar. Quando informada
    (>= 0), a gravação só acontece se a linha ainda estiver nela; caso
    contrário levanta `ConfiguracaoDesatualizada`. A comparação e a escrita
    ficam na MESMA sessão de propósito: checar numa sessão e gravar noutra
    deixaria aberta exatamente a janela que a checagem existe para fechar.

    É um contador e não o `updated_at` porque `brt_now()` trunca em segundos
    inteiros: duas gravações no mesmo segundo carimbam o mesmo horário, e a
    trava passaria batido justo na corrida real.

    Só quem envia a tela inteira de volta precisa passar `versao_esperada`. Os
    botões Iniciar e Parar não passam, e não é esquecimento: eles gravam um
    campo só (`ativo`), então não têm como desfazer horário nenhum, e recusar
    um "Parar" por causa de uma versão velha seria impedir alguém de frear o
    agente pelo motivo errado.
    """
    with rx.session() as session:
        linha = (
            session.query(ClassificacaoConfig)
            .filter(ClassificacaoConfig.tenant_id == tenant_id)
            .first()
        )
        if not linha:
            linha = ClassificacaoConfig(tenant_id=tenant_id)
            session.add(linha)
        elif versao_esperada >= 0 and int(linha.versao or 0) != versao_esperada:
            raise ConfiguracaoDesatualizada(
                linha.atualizado_por_nome or linha.atualizado_por_email,
                linha.updated_at,
            )

        for chave, valor in campos.items():
            setattr(linha, chave, valor)

        if autor_nome or autor_email:
            linha.atualizado_por_nome = autor_nome
            linha.atualizado_por_email = autor_email
            linha.updated_at = brt_now()
            linha.versao = int(linha.versao or 0) + 1
        session.commit()


def registrar_tick(tenant_id: int, resultado: str) -> None:
    """Grava que o agendador acordou e o que ele decidiu.

    Chamado em TODA passagem, inclusive nas que não fazem nada. É justamente a
    passagem que não faz nada que precisa deixar rastro: sem ela, "não rodou
    porque está parado" e "o processo morreu" são indistinguíveis na tela, e
    foi exatamente essa confusão que deixou a caixa sem classificar por dias.

    Não passa autor nem mexe na versão: isto é batimento de máquina, não edição
    de gente (ver `salvar_config`).
    """
    salvar_config(
        tenant_id, ultimo_tick_em=brt_now(), ultimo_tick_resultado=resultado[:400]
    )


def marcar_execucao_agendada(tenant_id: int, quando: Optional[datetime] = None) -> None:
    """Registra que um horário agendado já disparou.

    É o que impede o mesmo horário de rodar duas vezes no mesmo dia quando o
    agendador acorda mais de uma vez, e o que `slot_devido` consulta.
    """
    salvar_config(tenant_id, ultima_execucao_agendada=quando or brt_now())


# ---------------------------------------------------------------------------
# Qual horário está vencido
# ---------------------------------------------------------------------------


def _hhmm_valido(texto: str) -> bool:
    try:
        horas, minutos = texto.split(":")
        return 0 <= int(horas) <= 23 and 0 <= int(minutos) <= 59
    except (ValueError, AttributeError):
        return False


def _momento_de_hoje(agora: datetime, hhmm: str) -> datetime:
    horas, minutos = (int(p) for p in hhmm.split(":"))
    return agora.replace(hour=horas, minute=minutos, second=0, microsecond=0)


def slot_devido(agora: datetime, cfg: dict) -> Optional[str]:
    """Qual horário automático está vencido e ainda não rodou hoje.

    Função PURA: recebe o instante e a configuração, devolve `"h1"`, `"h2"` ou
    `None`. Sem I/O, o que a torna exaustivamente testável sem manipular relógio.

    Ela dá recuperação de graça, e é por isso que a decisão não é simplesmente
    "disparar no horário": se a máquina estava desligada às 08:00 e o servidor
    sobe às 09:20, o horário 1 ainda consta como não disparado hoje e a rodada
    acontece. Um gatilho que só olha o instante exato perderia a janela.

    Quando os dois estão vencidos, devolve o MAIS TARDE: ele varre uma janela
    maior e cobre o que o outro teria pego.
    """
    if not cfg.get("ativo", True):
        return None

    ultima = cfg.get("ultima_execucao_agendada")
    candidatos = []
    for slot, chave in (("h1", "horario_1"), ("h2", "horario_2")):
        hhmm = (cfg.get(chave) or "").strip()
        if not _hhmm_valido(hhmm):
            continue
        momento = _momento_de_hoje(agora, hhmm)
        if momento > agora:
            continue  # ainda não chegou
        if ultima is not None and ultima >= momento:
            continue  # já rodou depois deste horário
        candidatos.append((momento, slot))

    if not candidatos:
        return None
    return max(candidatos)[1]


def proxima_execucao(agora: datetime, cfg: dict) -> Optional[datetime]:
    """Quando o próximo horário automático dispara. Só para exibir no painel."""
    if not cfg.get("ativo", True):
        return None

    momentos = []
    for chave in ("horario_1", "horario_2"):
        hhmm = (cfg.get(chave) or "").strip()
        if not _hhmm_valido(hhmm):
            continue
        momento = _momento_de_hoje(agora, hhmm)
        momentos.append(momento if momento > agora else momento + timedelta(days=1))

    return min(momentos) if momentos else None


# ---------------------------------------------------------------------------
# Pastas por classe
# ---------------------------------------------------------------------------


def ensure_pastas(tenant_id: int) -> None:
    """Cria as quatro linhas de pasta, com nome sugerido e id ainda vazio.

    O id fica vazio de propósito: quem resolve nome para id é o Graph, na hora
    em que o usuário salva. Uma linha com `pasta_id` vazio é o sinal de que a
    configuração ainda não está completa, e é o que faz o orquestrador recusar
    a rodada antes de gastar o primeiro token.
    """
    with rx.session() as session:
        existentes = {
            p.classe
            for p in session.query(PastaClasse).filter(PastaClasse.tenant_id == tenant_id).all()
        }
        novas = [
            PastaClasse(tenant_id=tenant_id, classe=classe, pasta_nome=PASTAS_PADRAO[classe])
            for classe in CLASSES
            if classe not in existentes
        ]
        if novas:
            session.add_all(novas)
            session.commit()


def get_pastas(tenant_id: int) -> list:
    """As quatro pastas, achatadas e sempre na ordem de `CLASSES`."""
    with rx.session() as session:
        por_classe = {
            p.classe: p
            for p in session.query(PastaClasse).filter(PastaClasse.tenant_id == tenant_id).all()
        }

    resultado = []
    for classe in CLASSES:
        linha = por_classe.get(classe)
        resultado.append(
            {
                "classe": classe,
                "pasta_nome": linha.pasta_nome if linha else PASTAS_PADRAO[classe],
                "pasta_caminho": linha.pasta_caminho if linha else "",
                "pasta_id": linha.pasta_id if linha else "",
                "resolvido": bool(linha and linha.pasta_id),
                "erro_resolucao": linha.erro_resolucao if linha else "",
            }
        )
    return resultado


def salvar_pasta(
    tenant_id: int,
    classe: str,
    *,
    pasta_nome: str,
    pasta_id: str = "",
    pasta_caminho: str = "",
    erro_resolucao: str = "",
) -> None:
    """Grava o mapeamento de uma classe.

    `pasta_id` vazio NÃO apaga o id já resolvido: se o usuário salvou um nome
    ambíguo ou inexistente, a mensagem de erro é gravada mas o mapeamento que
    funcionava continua valendo. O contrário faria uma tentativa malsucedida de
    reconfiguração derrubar a rodada agendada seguinte.
    """
    with rx.session() as session:
        linha = (
            session.query(PastaClasse)
            .filter(PastaClasse.tenant_id == tenant_id, PastaClasse.classe == classe)
            .first()
        )
        if not linha:
            linha = PastaClasse(tenant_id=tenant_id, classe=classe)
            session.add(linha)

        linha.pasta_nome = pasta_nome
        linha.erro_resolucao = erro_resolucao
        if pasta_id:
            linha.pasta_id = pasta_id
            linha.pasta_caminho = pasta_caminho
            linha.resolvido_em = brt_now()
        linha.updated_at = brt_now()
        session.commit()


def pastas_pendentes(tenant_id: int) -> list:
    """Classes ainda sem `pasta_id`. Vazio significa configuração completa."""
    return [p["classe"] for p in get_pastas(tenant_id) if not p["resolvido"]]
