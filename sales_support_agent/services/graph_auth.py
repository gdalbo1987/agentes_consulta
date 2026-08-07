"""Autenticação client-credentials na Microsoft Graph, compartilhada.

Extraída de `graph_mailer.py` quando o cliente de leitura da caixa
(`graph_client.py`) passou a precisar do mesmo token. Uma implementação só, e
não duas: o cache em memória do MSAL vale por processo, então dois
`ConfidentialClientApplication` separados pediriam dois tokens para o mesmo
locatário e dobrariam as idas ao Entra ID.

Continua SÍNCRONA. O MSAL não tem API assíncrona e mantém cache próprio; quem
chama de dentro de código async usa `asyncio.to_thread`, que é o que
`graph_client.py` faz.

Pré-requisitos no Entra ID, sem os quais o Graph responde 403 e nada funciona:

* permissão **de aplicação** (não delegada), COM consentimento de administrador:
  `Mail.Send` para o envio transacional e `Mail.ReadWrite` para ler, marcar e
  mover mensagens. `Mail.Read` sozinha não basta para marcar nem mover.
* `graph_sender_email` precisa ser uma caixa real do locatário.

Recomendação de segurança que vale registrar: permissão de APLICAÇÃO
`Mail.ReadWrite` dá acesso a TODAS as caixas do locatário. Restrinja o client ID
à caixa comercial com uma ApplicationAccessPolicy do Exchange Online. Sem isso,
um client secret vazado lê o e-mail da empresa inteira.
"""

import msal

GRAPH = "https://graph.microsoft.com"
SCOPE = [f"{GRAPH}/.default"]


class GraphAuthError(Exception):
    """Falha ao obter o token de aplicação no Entra ID."""


def adquirir_token(tenant_id: str, client_id: str, client_secret: str) -> str:
    app = msal.ConfidentialClientApplication(
        client_id,
        authority=f"https://login.microsoftonline.com/{tenant_id}",
        client_credential=client_secret,
    )
    # Tenta o cache em memória do MSAL antes de ir à rede (tokens valem ~1h).
    resultado = app.acquire_token_silent(SCOPE, account=None) or app.acquire_token_for_client(
        scopes=SCOPE
    )
    token = resultado.get("access_token")
    if not token:
        raise GraphAuthError(
            "Falha ao autenticar na Microsoft Graph: "
            f"{resultado.get('error_description') or resultado.get('error') or 'erro desconhecido'}"
        )
    return token


def campos_faltando(cfg: dict) -> list:
    """Rótulos em português dos campos de configuração ainda vazios.

    Compartilhado pelos dois clientes para que a mensagem de "não configurado"
    seja a mesma, venha ela do envio de um convite ou da leitura da caixa.
    """
    return [
        rotulo
        for rotulo, valor in (
            ("remetente", cfg.get("sender_email")),
            ("tenant ID", cfg.get("tenant_id")),
            ("client ID", cfg.get("client_id")),
            ("client secret", cfg.get("client_secret")),
        )
        if not valor
    ]
