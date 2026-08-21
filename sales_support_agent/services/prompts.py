"""As instruções dos agentes, editáveis pela organização.

Hoje só o classificador é editável. Ele é o agente cuja saída decide para qual
pasta cada e-mail vai, e calibrá-lo é trabalho de quem convive com os e-mails,
não de quem tem acesso ao servidor.

AUSÊNCIA DE LINHA SIGNIFICA "PADRÃO DO CÓDIGO", e não "sem prompt". É por isso
que `restaurar_padrao` APAGA a linha em vez de copiar o texto padrão para
dentro dela: copiado, o texto congelaria na versão do dia em que o botão foi
apertado, e uma melhoria futura no padrão nunca chegaria a quem "restaurou".
Apagando, a instalação volta a seguir o código, hoje e nas versões seguintes.

A checagem de edição concorrente é a mesma de `classificacao_config`, e existe
pelo mesmo motivo: o texto vive numa caixa de edição, e duas pessoas com o
painel aberto sobrescreveriam uma à outra em silêncio. Aqui o estrago é maior,
porque o que se perde não é um horário e sim uma calibragem inteira.
"""

from typing import Optional

import reflex as rx

from sales_support_agent.models import PromptAgente, brt_now

# As chaves que podem ser editadas. Fica explícito para que uma chave inventada
# na UI não crie linha órfã que nenhum agente lê.
CHAVES = ("classificacao",)


class PromptDesatualizado(Exception):
    """Outra pessoa salvou o prompt depois que esta tela o carregou."""

    def __init__(self, autor: str, quando):
        self.autor = autor
        self.quando = quando
        super().__init__(self.mensagem)

    @property
    def mensagem(self) -> str:
        quem = self.autor or "Outro usuário"
        if self.quando:
            return (
                f"{quem} salvou uma nova versão deste prompt em "
                f"{self.quando.strftime('%d/%m/%Y às %H:%M')}. Recarregue antes de "
                "salvar, para não descartar a calibragem da pessoa."
            )
        return f"{quem} salvou uma nova versão deste prompt. Recarregue antes de salvar."


def _padrao(chave: str) -> str:
    """O texto que o CÓDIGO traz, para a chave pedida."""
    if chave == "classificacao":
        from sales_support_agent.services.classificacao_agent import PROMPT_PADRAO

        return PROMPT_PADRAO
    return ""


def texto_em_vigor(tenant_id: int, chave: str) -> str:
    """O texto que os agentes de fato usam. Nunca devolve vazio.

    Se a linha não existe, ou existe e está em branco, vale o padrão do código.
    Um prompt vazio chegando ao modelo transformaria o classificador num
    rotulador aleatório, e um campo apagado sem querer no painel não pode ter
    esse poder.
    """
    dados = get_prompt(tenant_id, chave)
    return dados["texto"] or _padrao(chave)


def get_prompt(tenant_id: int, chave: str) -> dict:
    """O prompt para a tela: texto, versão, autoria e se está no padrão."""
    with rx.session() as session:
        linha = (
            session.query(PromptAgente)
            .filter(PromptAgente.tenant_id == tenant_id, PromptAgente.chave == chave)
            .first()
        )

        if not linha:
            return {
                "texto": _padrao(chave),
                "versao": 0,
                "no_padrao": True,
                "atualizado_por_nome": "",
                "atualizado_por_email": "",
                "updated_at": None,
            }

        return {
            "texto": linha.texto or "",
            "versao": int(linha.versao or 0),
            "no_padrao": False,
            "atualizado_por_nome": linha.atualizado_por_nome or "",
            "atualizado_por_email": linha.atualizado_por_email or "",
            "updated_at": linha.updated_at,
        }


def salvar_prompt(
    tenant_id: int,
    chave: str,
    texto: str,
    *,
    autor_nome: str = "",
    autor_email: str = "",
    versao_esperada: int = -1,
) -> int:
    """Grava uma versão nova e devolve o número dela.

    A checagem de versão e a escrita ficam na MESMA sessão: checar numa e
    gravar noutra deixaria aberta a janela que a checagem existe para fechar.
    """
    if chave not in CHAVES:
        raise ValueError(f"Prompt desconhecido: {chave!r}.")

    limpo = (texto or "").strip()
    if not limpo:
        raise ValueError(
            "O prompt não pode ficar vazio. Para voltar ao texto original, use "
            "Restaurar padrão."
        )

    with rx.session() as session:
        linha = (
            session.query(PromptAgente)
            .filter(PromptAgente.tenant_id == tenant_id, PromptAgente.chave == chave)
            .first()
        )

        if linha is None:
            # Versão 0 é "estava no padrão do código". Quem carregou a tela sem
            # linha e salva primeiro não conflita com ninguém.
            if versao_esperada > 0:
                raise PromptDesatualizado("", None)
            linha = PromptAgente(tenant_id=tenant_id, chave=chave, texto=limpo, versao=0)
            session.add(linha)
        elif versao_esperada >= 0 and int(linha.versao or 0) != versao_esperada:
            raise PromptDesatualizado(
                linha.atualizado_por_nome or linha.atualizado_por_email,
                linha.updated_at,
            )

        linha.texto = limpo
        linha.versao = int(linha.versao or 0) + 1
        linha.atualizado_por_nome = autor_nome
        linha.atualizado_por_email = autor_email
        linha.updated_at = brt_now()
        session.commit()
        return int(linha.versao)


def restaurar_padrao(tenant_id: int, chave: str) -> None:
    """Volta ao texto do código APAGANDO a linha.

    Apagar e não copiar: copiado, o texto congelaria na versão do dia em que o
    botão foi apertado, e qualquer melhoria futura do padrão deixaria de chegar
    a quem restaurou. Sem linha, a instalação volta a seguir o código.
    """
    with rx.session() as session:
        linha = (
            session.query(PromptAgente)
            .filter(PromptAgente.tenant_id == tenant_id, PromptAgente.chave == chave)
            .first()
        )
        if linha:
            session.delete(linha)
            session.commit()
