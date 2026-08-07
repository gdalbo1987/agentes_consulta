"""Envio de e-mails transacionais com a identidade visual da Coester.

Centraliza o envio e o layout de marca: uma faixa de topo com o degradê azul
padrão contendo o logo claro (embutido via CID, para aparecer mesmo sem URL
pública) e um botão de ação com o mesmo degradê.
"""
import os
from datetime import date

# Degradê azul padrão (espelha styles/colors.py: BTN_GRADIENT).
_GRADIENT = "linear-gradient(135deg, #182744, #1d548c)"
_GRADIENT_FALLBACK = "#1d548c"  # cor sólida para clientes sem suporte a degradê (Outlook)

# Caminho do PNG do logo (claro) usado na faixa de topo dos e-mails.
_LOGO_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "assets",
    "logo-coester-white.png",
)


def _branded_wrapper(inner_html: str, cta_label: str, cta_href: str) -> str:
    """Envolve o conteúdo do e-mail no layout de marca (faixa em degradê + card + botão)."""
    ano = date.today().year
    return f"""
    <html>
      <body style="font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; background-color: #f4f7fa; padding: 0; margin: 0;">
        <div style="max-width: 600px; margin: 24px auto; background-color: #ffffff; border-radius: 16px; overflow: hidden; box-shadow: 0 4px 15px rgba(0,0,0,0.05);">

          <!-- Faixa de topo com o degradê azul padrão + logo claro (via CID) -->
          <div style="background: {_GRADIENT_FALLBACK}; background: {_GRADIENT}; padding: 28px 40px; text-align: center;">
            <!-- Só a largura é fixada; `height: auto` deixa a altura seguir o
                 aspect ratio 4:3 do PNG, sem distorcer o logo. -->
            <img src="cid:logo" width="180" alt="Coester" style="display: inline-block; width: 180px; max-width: 70%; height: auto;" />
          </div>

          <!-- Conteúdo -->
          <div style="padding: 40px; text-align: center;">
            {inner_html}

            <a href="{cta_href}" style="background: {_GRADIENT_FALLBACK}; background: {_GRADIENT}; color: #ffffff; text-decoration: none; padding: 16px 32px; border-radius: 8px; font-weight: bold; font-size: 16px; display: inline-block; margin-top: 8px;">
              {cta_label}
            </a>

            <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 40px 0 20px 0;">
            <p style="color: #9ca3af; font-size: 12px; line-height: 1.6;">
              Se você tiver alguma dúvida ou precisar de ajuda, basta responder a este e-mail.<br>
              © {ano} Coester. Todos os direitos reservados.
            </p>
          </div>
        </div>
      </body>
    </html>
    """


def _send_html_email(to_email: str, subject: str, html: str, *, embed_logo: bool = True) -> None:
    """Envia um e-mail HTML pela Microsoft Graph API.

    Falhas NUNCA propagam, de propósito: um convite ou uma redefinição de senha
    não podem falhar porque o e-mail não saiu. O erro vai para o log do servidor
    e o super admin confirma a configuração pelo botão de teste em `/admin`.
    """
    from sales_support_agent.services.graph_mailer import enviar_email

    try:
        enviar_email(
            to_email,
            subject,
            html,
            inline_image_path=_LOGO_PATH if embed_logo else None,
        )
        print(f"E-mail enviado para {to_email}")
    except Exception as e:
        # Sem o assunto na mensagem: o console cp1252 do Windows quebraria com
        # UnicodeEncodeError e mascararia o erro real do envio.
        print(f"Falha ao enviar e-mail para {to_email}: {e}")


def send_invite_email(to_email: str, user_name: str, reset_link: str) -> None:
    """Convite enviado por um super admin — única porta de entrada na plataforma.

    Não carrega senha provisória: leva a um link de uso único (24h) onde o
    convidado define a própria senha."""
    first_name = user_name.split()[0] if user_name.strip() else "Olá"
    inner = f"""
        <h2 style="color: #111827; font-size: 26px; margin: 0 0 10px 0;">Bem-vindo(a), {first_name}!</h2>
        <p style="color: #4b5563; font-size: 16px; line-height: 1.6; margin: 0 0 20px 0;">
            Você foi convidado(a) para acessar a plataforma interna de prospecção da Coester.
        </p>
        <p style="color: #4b5563; font-size: 16px; line-height: 1.6; margin: 0 0 32px 0;">
            Para começar, defina a sua senha de acesso clicando no botão abaixo.
            Este link é válido por 24 horas.
        </p>
    """
    html = _branded_wrapper(inner, "Definir minha senha", reset_link)
    _send_html_email(
        to_email,
        "Seu acesso à plataforma Coester: defina sua senha",
        html,
    )


def send_password_reset_email(to_email: str, user_name: str, reset_link: str) -> None:
    """E-mail com o link exclusivo para redefinição de senha (mesmo layout de marca)."""
    first_name = user_name.split()[0] if user_name.strip() else "Olá"
    inner = f"""
        <h2 style="color: #111827; font-size: 26px; margin: 0 0 10px 0;">Olá, {first_name}!</h2>
        <p style="color: #4b5563; font-size: 16px; line-height: 1.6; margin: 0 0 20px 0;">
            Recebemos uma solicitação para redefinir a senha da sua conta. Clique no botão abaixo
            para escolher uma nova senha. Este link é válido por 1 hora.
        </p>
        <p style="color: #4b5563; font-size: 16px; line-height: 1.6; margin: 0 0 32px 0;">
            Se você não solicitou essa alteração, pode ignorar este e-mail com segurança.
        </p>
    """
    html = _branded_wrapper(inner, "Redefinir minha senha", reset_link)
    _send_html_email(
        to_email,
        "🔑 Redefinição de senha",
        html,
    )


def montar_email_de_teste() -> tuple:
    """(assunto, html, caminho_do_logo) do e-mail de teste do painel `/admin`.

    Fica aqui, e não no State, para o teste exercitar EXATAMENTE o mesmo layout
    de marca e o mesmo logo inline dos e-mails reais — um teste que passasse com
    um HTML mais simples não provaria nada sobre o convite.
    """
    inner = """
        <h2 style="color: #111827; font-size: 26px; margin: 0 0 10px 0;">Configuração validada!</h2>
        <p style="color: #4b5563; font-size: 16px; line-height: 1.6; margin: 0 0 32px 0;">
            Se você está lendo esta mensagem, o envio de e-mails pela Microsoft Graph API
            está funcionando. Convites e redefinições de senha serão entregues normalmente.
        </p>
    """
    html = _branded_wrapper(inner, "Abrir a plataforma", os.environ.get("APP_BASE_URL", "http://localhost:3000"))
    return "Teste de envio da Plataforma Coester", html, _LOGO_PATH
