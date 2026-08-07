"""Envio de e-mail pela Microsoft Graph API (substituiu o SMTP).

Fluxo *client credentials* (aplicação, sem usuário): o app se autentica no Entra
ID com `graph_client_id` + `graph_client_secret` dentro do locatário
`graph_tenant_id`, recebe um token de aplicação e envia como a caixa
`graph_sender_email`.

Pré-requisitos no Entra ID — sem eles o Graph responde 403 e nenhum e-mail sai:

* o registro de aplicativo precisa da permissão **de aplicação** `Mail.Send`
  (não a delegada), COM consentimento do administrador concedido;
* `graph_sender_email` precisa ser uma caixa de correio real do locatário.

Use o botão "Enviar e-mail de teste" em `/admin` para confirmar os dois antes de
depender disso num convite de verdade.
"""

import base64
import os
from typing import Optional

import msal
import requests

_GRAPH = "https://graph.microsoft.com"
_SCOPE = [f"{_GRAPH}/.default"]

# O envio é síncrono e roda dentro de um event handler; um Graph pendurado não
# pode segurar a requisição indefinidamente.
_TIMEOUT = 30


class GraphMailerError(Exception):
    """Falha de configuração ou de envio. Quem chama decide se propaga.

    `services/emails.py` NÃO propaga: um e-mail que não sai não pode derrubar o
    convite ou a redefinição de senha. Já o teste manual do painel propaga, para
    o super admin ver a mensagem exata do erro.
    """


def _adquirir_token(tenant_id: str, client_id: str, client_secret: str) -> str:
    app = msal.ConfidentialClientApplication(
        client_id,
        authority=f"https://login.microsoftonline.com/{tenant_id}",
        client_credential=client_secret,
    )
    # Tenta o cache em memória do MSAL antes de ir à rede (tokens valem ~1h).
    resultado = app.acquire_token_silent(_SCOPE, account=None) or app.acquire_token_for_client(
        scopes=_SCOPE
    )
    token = resultado.get("access_token")
    if not token:
        raise GraphMailerError(
            "Falha ao autenticar na Microsoft Graph: "
            f"{resultado.get('error_description') or resultado.get('error') or 'erro desconhecido'}"
        )
    return token


def enviar_email(
    to_email: str,
    subject: str,
    html: str,
    *,
    inline_image_path: Optional[str] = None,
    inline_content_id: str = "logo",
) -> None:
    """Envia um e-mail HTML. Levanta `GraphMailerError` em qualquer falha.

    `inline_image_path` embute uma imagem referenciada no HTML por `cid:<id>` —
    é assim que o logo aparece no e-mail sem depender de uma URL pública.
    """
    from prospect_agent.services.settings import get_graph_config

    cfg = get_graph_config()
    faltando = [
        rotulo
        for rotulo, valor in (
            ("remetente", cfg["sender_email"]),
            ("tenant ID", cfg["tenant_id"]),
            ("client ID", cfg["client_id"]),
            ("client secret", cfg["client_secret"]),
        )
        if not valor
    ]
    if faltando:
        raise GraphMailerError(
            "Microsoft Graph não configurada. Faltam: " + ", ".join(faltando) +
            ". Preencha em /admin → Integrações → E-mail (Microsoft Graph)."
        )

    token = _adquirir_token(cfg["tenant_id"], cfg["client_id"], cfg["client_secret"])

    mensagem = {
        "subject": subject,
        "body": {"contentType": "HTML", "content": html},
        "toRecipients": [{"emailAddress": {"address": to_email}}],
    }

    if inline_image_path and os.path.exists(inline_image_path):
        with open(inline_image_path, "rb") as f:
            conteudo = base64.b64encode(f.read()).decode()
        mensagem["attachments"] = [
            {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": os.path.basename(inline_image_path),
                "contentType": "image/png",
                # isInline + contentId é o equivalente Graph do MIME "related"
                # que o SMTP usava: a imagem não vira anexo visível, ela resolve
                # o `src="cid:logo"` do corpo.
                "isInline": True,
                "contentId": inline_content_id,
                "contentBytes": conteudo,
            }
        ]

    resposta = requests.post(
        f"{_GRAPH}/v1.0/users/{cfg['sender_email']}/sendMail",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={"message": mensagem, "saveToSentItems": True},
        timeout=_TIMEOUT,
    )
    # sendMail devolve 202 Accepted sem corpo quando dá certo.
    if resposta.status_code != 202:
        raise GraphMailerError(
            f"Microsoft Graph recusou o envio (HTTP {resposta.status_code}): {resposta.text[:400]}"
        )
