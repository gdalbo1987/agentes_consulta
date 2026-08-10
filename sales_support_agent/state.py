import reflex as rx
import asyncio
import os
import json
import secrets
import uuid
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta
from .models import User, Tenant, ActivityLog, brt_now
# Import no topo (e não preguiçoso, como os demais deste arquivo) porque a
# constante é usada no VALOR PADRÃO de um campo de State, avaliado na criação
# da classe. `services/settings.py` só depende de `models` e `crypto`, então
# não há ciclo.
from .services.settings import MODELOS_DISPONIVEIS
import bcrypt
from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()  # Carrega variáveis de ambiente do arquivo .env


def base_url() -> str:
    """Origem pública do app, usada para montar os links enviados por e-mail
    (convite e redefinição de senha). Vem de `APP_BASE_URL` no `.env` — antes
    estava escrita à mão como `http://localhost:3000` em cada ponto de envio, o
    que quebrava todos os links assim que o app saía da máquina do dev."""
    return os.environ.get("APP_BASE_URL", "http://localhost:3000").rstrip("/")


# ==========================================================================
# Padrão de confirmação de ações (toasts discretos — check + mensagem curta).
# Use `toast_success` em TODA ação de salvar/atualizar/criar (usuário comum ou
# super admin); `toast_error` no lugar de `rx.window_alert` para erros de forma.
# Retornam um EventSpec — devolva-o do event handler, ou numa lista junto com
# outros eventos (ex.: `return [toast_success("..."), OutroState.recarregar]`).
# ==========================================================================
def toast_success(message: str = "Alterações salvas com sucesso."):
    """Toast padrão de confirmação: ícone de check + mensagem curta e discreta."""
    return rx.toast.success(message, position="bottom-right", duration=2500)


def toast_error(message: str):
    """Toast padrão de erro (substitui o window_alert bloqueante)."""
    return rx.toast.error(message, position="bottom-right", duration=3500)


class LogUI(BaseModel):
    """Modelo visual para formatar os logs na tela do Admin"""
    timestamp: str
    tenant_id: str
    user_email: str
    action: str
    details: str

class AppState(rx.State):
    """Estado global da aplicação."""
    user_email: str = ""
    user_name: str = ""
    user_avatar: str = ""
    tenant_id: int = 0
    is_superadmin: bool = False
    is_authenticated: bool = False

    # Variáveis para a tela do Super Admin
    admin_logs: List[LogUI] = []
    total_tenants: int = 0

    def require_auth(self):
        """Guard das páginas internas: exige apenas sessão autenticada.

        Substituiu o antigo `require_paid`, que reconferia a assinatura do tenant
        no banco e mandava para `/checkout`. Sem assinatura, o único requisito é
        estar logado — e, como o acesso é só por convite, estar logado já
        significa ter sido admitido por um super admin.
        """
        if not self.is_authenticated:
            return rx.redirect("/login")

    def load_dashboard(self):
        """on_load do /dashboard: aplica o guard de autenticação."""
        return self.require_auth()

    def log_activity(self, action: str, details: str, session):
        """Função auxiliar que salva os logs já no fuso de Brasília."""
        
        # Ajusta a hora do servidor para UTC-3 (Horário de Brasília)
        br_time = datetime.utcnow() - timedelta(hours=3)
        
        log = ActivityLog(
            tenant_id=self.tenant_id, 
            user_email=self.user_email, 
            action=action, 
            details=details,
            timestamp=br_time # Injetamos a hora corrigida aqui!
        )
        session.add(log)
        session.commit()

    def load_admin_dashboard(self):
        """Carrega os dados consolidados para o God Mode."""
        if not self.is_superadmin:
            return rx.redirect("/dashboard")
            
        with rx.session() as session:
            db_logs = session.query(ActivityLog).order_by(ActivityLog.timestamp.desc()).limit(50).all()
            
            # Convertendo os dados do banco para o modelo visual da tela
            self.admin_logs = [
                LogUI(
                    timestamp=log.timestamp.strftime("%d/%m/%Y %H:%M") if log.timestamp else "",
                    tenant_id=str(log.tenant_id),
                    user_email=log.user_email,
                    action=log.action,
                    details=log.details
                )
                for log in db_logs
            ]
            self.total_tenants = session.query(Tenant).count()

    def clear_old_logs(self):
        """Deleta todos os logs do banco de dados (Ação irreversível)."""
        # Trava de segurança extra: só o Super Admin pode executar isso
        if not self.is_superadmin:
            return
        
        with rx.session() as session:
            # Comando SQLModel para deletar todas as linhas da tabela ActivityLog
            session.query(ActivityLog).delete()
            session.commit()

        # Recarrega o painel vazio na mesma hora
        self.load_admin_dashboard()
        return toast_success("Logs removidos.")

    def logout(self):
        """Encerra a sessão do usuário e redireciona para a home."""
        self.user_email = ""
        self.user_name = ""
        self.user_avatar = ""
        self.tenant_id = 0
        self.is_superadmin = False
        self.is_authenticated = False

        # Limpar os dados confidenciais do God Mode da memória por segurança
        self.admin_logs = []
        self.total_tenants = 0

        # Redireciona para a Landing Page
        return rx.redirect("/")

    def send_password_reset_email(self, to_email: str, user_name: str, reset_link: str):
        """Envia o e-mail de redefinição de senha com a identidade visual da marca.

        A montagem/envio fica em `services/emails.py` (mesmo layout do e-mail de
        convite: logo na faixa em degradê + botão em degradê).
        """
        from sales_support_agent.services.emails import send_password_reset_email as _send
        _send(to_email, user_name, reset_link)

class AuthState(AppState):
    """Gerencia o estado da página de Login (não há cadastro público)."""

    form_email: str = ""
    form_password: str = ""

    error_message: str = ""
    show_password: bool = False

    def toggle_password_visibility(self):
        """Mostra/oculta o texto do campo de senha (acionado pelo botão do olho)."""
        self.show_password = not self.show_password

    def login(self):
        """Verifica e-mail e senha."""
        self.error_message = ""
        with rx.session() as session:
            user = session.query(User).filter(User.email == self.form_email).first()

            if not user or not user.hashed_password:
                # Sem hash = convite ainda não aceito (o usuário existe, mas só
                # tem `reset_token`). Mensagem genérica de propósito: não revela
                # quais e-mails estão cadastrados.
                self.error_message = "E-mail ou senha incorretos."
                return

            # Verifica se a senha bate com o hash salvo
            if bcrypt.checkpw(self.form_password.encode('utf-8'), user.hashed_password.encode('utf-8')):
                self._login_user(user, session)
                
                # O roteador inteligente: Admin vai para um lado, Cliente para outro
                if self.is_superadmin:
                    return rx.redirect("/admin")
                else:
                    return rx.redirect("/dashboard")
            else:
                self.error_message = "E-mail ou senha incorretos."

    def _login_user(self, user: User, session):
        """Função interna para injetar os dados do usuário na memória principal."""
        self.user_email = user.email
        self.user_name = user.name
        self.user_avatar = user.avatar_url or ""
        self.tenant_id = user.tenant_id
        self.is_superadmin = user.is_superadmin
        self.is_authenticated = True

        # Limpa os formulários por segurança
        self.form_password = ""

        # Registra no log
        self.log_activity("LOGIN", "Usuário acessou o sistema.", session)

    def handle_submit(self, form_data: dict):
        """Recebe os dados do formulário de login de uma vez só."""
        self.error_message = ""

        # Puxa os dados que o navegador enviou
        self.form_email = form_data.get("email_field", "")
        self.form_password = form_data.get("password_field", "")

        return self.login()


class ForgotPasswordState(AppState):
    """Estado da página '/esqueci-senha': solicita o envio do link de redefinição."""

    message: str = ""
    error_message: str = ""
    is_sending: bool = False

    def load_forgot_password(self):
        self.message = ""
        self.error_message = ""

    async def send_reset_link(self, form_data: dict):
        self.error_message = ""
        self.message = ""

        email = form_data.get("email_field", "").strip()
        if not email:
            self.error_message = "Digite seu e-mail para recuperar a senha."
            return

        self.is_sending = True
        yield

        with rx.session() as session:
            user = session.query(User).filter(User.email == email).first()
            # Qualquer usuário admitido pode redefinir a senha — inclusive quem
            # foi convidado e ainda não tem `hashed_password` (perdeu o link do
            # convite). Antes havia um filtro por `hashed_password` para excluir
            # contas do Google, que não existem mais.
            if user:
                token = secrets.token_urlsafe(32)
                user.reset_token = token
                user.reset_token_expires = brt_now() + timedelta(hours=1)
                session.commit()

                reset_link = f"{base_url()}/redefinir-senha?token={token}"
                self.send_password_reset_email(user.email, user.name, reset_link)

        # Mensagem sempre genérica, para não revelar se o e-mail existe na base.
        self.message = "Se o e-mail informado existir em nossa base, enviamos um link de redefinição de senha. Confira sua caixa de entrada."
        self.is_sending = False


class ResetPasswordState(AppState):
    """Estado da página '/redefinir-senha': valida o token e define a nova senha."""

    token: str = ""
    token_valid: bool = False
    checked_token: bool = False
    error_message: str = ""
    success: bool = False
    show_new_password: bool = False
    show_confirm_password: bool = False

    def toggle_new_password_visibility(self):
        self.show_new_password = not self.show_new_password

    def toggle_confirm_password_visibility(self):
        self.show_confirm_password = not self.show_confirm_password

    def load_reset_page(self):
        self.token = self.router.url.query_parameters.get("token", "")
        self.error_message = ""
        self.success = False
        self.checked_token = True

        if not self.token:
            self.token_valid = False
            return

        with rx.session() as session:
            user = session.query(User).filter(User.reset_token == self.token).first()
            self.token_valid = bool(
                user and user.reset_token_expires and user.reset_token_expires > brt_now()
            )

    def do_reset(self, form_data: dict):
        self.error_message = ""

        new_password = form_data.get("new_password_field", "")
        confirm_password = form_data.get("confirm_password_field", "")

        if not new_password or not confirm_password:
            self.error_message = "Preencha os dois campos de senha."
            return
        if new_password != confirm_password:
            self.error_message = "As senhas não coincidem."
            return
        if len(new_password) < 6:
            self.error_message = "A senha deve ter ao menos 6 caracteres."
            return

        with rx.session() as session:
            user = session.query(User).filter(User.reset_token == self.token).first()
            if not user or not user.reset_token_expires or user.reset_token_expires <= brt_now():
                self.token_valid = False
                self.error_message = "Este link expirou. Solicite um novo."
                return

            user.hashed_password = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
            user.reset_token = None
            user.reset_token_expires = None
            session.commit()

        self.success = True


class ProfileState(AppState):
    """Estado exclusivo para a tela de perfil."""
    new_avatar_url: str = ""
    current_password: str = ""
    new_password: str = ""
    show_current_password: bool = False
    show_new_password: bool = False

    def load_profile(self):
        """on_load do /profile: exige apenas sessão autenticada."""
        return self.require_auth()

    def toggle_current_password_visibility(self):
        self.show_current_password = not self.show_current_password

    def toggle_new_password_visibility(self):
        self.show_new_password = not self.show_new_password

    def set_new_avatar_url(self, value: str):
        self.new_avatar_url = value

    def set_current_password(self, value: str):
        self.current_password = value

    def set_new_password(self, value: str):
        self.new_password = value

    def update_avatar(self):
        """Salva a nova foto no banco de dados."""
        if not self.new_avatar_url:
            return toast_error("Cole o link de uma imagem primeiro.")

        with rx.session() as session:
            from sales_support_agent.models import User
            user = session.query(User).filter(User.email == self.user_email).first()
            if user:
                user.avatar_url = self.new_avatar_url
                session.commit()

                self.user_avatar = self.new_avatar_url
                self.new_avatar_url = ""
                return toast_success("Foto de perfil atualizada.")

    def update_password(self):
        """Valida a senha antiga e criptografa a nova."""
        if not self.current_password or not self.new_password:
            return toast_error("Preencha a senha atual e a nova senha.")

        with rx.session() as session:
            from sales_support_agent.models import User
            user = session.query(User).filter(User.email == self.user_email).first()

            # Sem hash gravado o usuário ainda não aceitou o convite: não há
            # "senha atual" para conferir — o caminho dele é o link do convite
            # ou o "esqueci minha senha".
            if user and user.hashed_password:
                # Verifica se a senha atual digitada bate com o hash do banco
                if bcrypt.checkpw(self.current_password.encode('utf-8'), user.hashed_password.encode('utf-8')):
                    # Criptografa a nova senha
                    new_hashed = bcrypt.hashpw(self.new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
                    user.hashed_password = new_hashed
                    session.commit()

                    # Limpa os campos por segurança
                    self.current_password = ""
                    self.new_password = ""
                    return toast_success("Senha atualizada com sucesso.")
                else:
                    return toast_error("A senha atual está incorreta.")
            else:
                return toast_error(
                    "Sua senha ainda não foi definida. Use o link do convite ou "
                    "a opção 'Esqueci minha senha'."
                )

# ---------------------------------------------------------------------------
# Limites de uso — fixos para toda a organização (não há mais planos).
#
# Antes estes números vinham de `PlanSetting` (tabela editável por plano, lida
# via services/plans.py). Com o modelo comercial descontinuado eles viraram
# constantes: um só valor vale para todo mundo.
# ---------------------------------------------------------------------------

# Cota mensal de consultas POR USUÁRIO. Escopo é o usuário, não a organização:
# dois usuários da Coester têm 20 cada um. Execuções com erro não consomem cota.
#
# ATENÇÃO: esta constante está órfã no momento. Ela era o invariante do funil de
# prospecção, contada separadamente em pesquisa, enriquecimento e priorização,
# etapas que não existem mais. No Agente de Suporte ao Comercial ela não pode
# valer como estava: a classificação roda DUAS VEZES POR DIA por agenda, e um
# teto de 20 por mês estouraria no dia 10. Fica aqui, sem uso, até a decisão de
# a quê ela passa a se aplicar (execução manual? perguntas ao agente de
# consulta?), que é mudança de invariante de produto e depende do usuário.
CONSULTA_LIMIT_MENSAL = 20


def limite_consultas(is_superadmin: bool) -> int:
    """Cota mensal de consultas do usuário. Super admin segue ilimitado, como
    sempre foi — as etapas que ele dispara custam tokens de IA (custo nosso,
    visível no painel de custos), não crédito comprado de terceiro."""
    return 9999 if is_superadmin else CONSULTA_LIMIT_MENSAL


# ---------------------------------------------------------------------------
# Consumo de tokens: de qual agente veio -> qual configuração de modelo o gerou
# ---------------------------------------------------------------------------
# `agent_name` gravado em `TokenUsage` -> chave em `AgentModelSetting`.
# Um agente, uma configuração: classificação, resumo e consulta escolhem modelo
# e esforço separadamente em /admin, porque têm perfis de custo bem diferentes
# (a classificação é a de maior volume, o resumo é compressão pura).
AGENTE_PARA_CHAVE_DE_CONFIG = {
    "classificacao_agent": "classificacao",
    "resumo_agent": "resumo",
    "consulta_agent": "consulta",
}


def modelo_do_agente(agent_name: str) -> str:
    """Modelo configurado HOJE para o agente — usado ao gravar `TokenUsage`.

    Ler no momento da gravação (e não no cálculo do custo) é o que faz o
    histórico de gasto ficar imune a uma troca de modelo posterior em `/admin`.
    """
    from sales_support_agent.services.settings import get_agent_config

    chave = AGENTE_PARA_CHAVE_DE_CONFIG.get(agent_name)
    return get_agent_config(chave)[0] if chave else ""


class EmailUI(BaseModel):
    """Linha da tabela e do painel de urgências, já achatada.

    Achatada porque o `foreach` do Reflex não acessa dicionário aninhado
    tipado: o serviço entrega campos de topo e a UI só lê.
    """

    id: int
    assunto: str
    cliente: str
    recebido_em: str
    classe_label: str
    urgente: bool
    resumo: str = ""
    acao_sugerida: str = ""


class PastaUI(BaseModel):
    classe: str
    classe_label: str
    pasta_nome: str
    pasta_caminho: str
    resolvido: bool
    erro_resolucao: str


class DashboardState(AppState):
    """Dashboard do usuário padrão: a tela operacional do Agente 1.

    ATENÇÃO: esta versão do Reflex NÃO gera setters automáticos. Todo campo
    ligado a `on_change` precisa do seu `set_<campo>` escrito à mão aqui
    embaixo, e esquecer quebra em runtime, não na compilação.
    """

    # --- métricas ---
    duracao_media: str = "-"
    duracao_ultima: str = "-"
    total_classificados: int = 0
    ultima_rodada_classificados: int = 0
    ultima_rodada_quando: str = "-"
    ultima_rodada_origem: str = ""

    # --- execução ---
    is_running: bool = False
    progresso_texto: str = ""
    progresso_atual: int = 0
    progresso_total: int = 0

    # --- configuração (espelha ClassificacaoConfig) ---
    horario_1: str = "08:00"
    horario_2: str = "16:00"
    janela_urgencia_horas: str = "24"
    lookback_horas: str = "48"
    proxima_execucao: str = "-"

    # --- pastas ---
    pastas: List[PastaUI] = []
    pasta_inputs: Dict[str, str] = {}
    pastas_disponiveis: List[str] = []
    listando_pastas: bool = False

    # --- tabela ---
    emails: List[EmailUI] = []
    urgentes: List[EmailUI] = []
    filtro_data_inicio: str = ""
    filtro_data_fim: str = ""
    filtro_apenas_urgentes: bool = False

    # --- diálogo de detalhe ---
    detalhe_aberto: bool = False
    detalhe_assunto: str = ""
    detalhe_cliente: str = ""
    detalhe_recebido: str = ""
    detalhe_classe: str = ""
    detalhe_urgente: bool = False
    detalhe_resumo: str = ""
    detalhe_pontos: List[str] = []
    detalhe_acao: str = ""
    detalhe_prazo: str = ""
    detalhe_disponivel: bool = False
    detalhe_link: str = ""

    # ------------------------------------------------------- setters à mão
    def set_horario_1(self, value: str):
        self.horario_1 = value

    def set_horario_2(self, value: str):
        self.horario_2 = value

    def set_janela_urgencia_horas(self, value: str):
        self.janela_urgencia_horas = value

    def set_lookback_horas(self, value: str):
        self.lookback_horas = value

    def set_filtro_data_inicio(self, value: str):
        self.filtro_data_inicio = value
        return DashboardState.carregar_tabela

    def set_filtro_data_fim(self, value: str):
        self.filtro_data_fim = value
        return DashboardState.carregar_tabela

    def set_filtro_apenas_urgentes(self, value: bool):
        self.filtro_apenas_urgentes = value
        return DashboardState.carregar_tabela

    def set_pasta_input(self, classe: str, value: str):
        self.pasta_inputs[classe] = value

    def set_detalhe_aberto(self, value: bool):
        self.detalhe_aberto = value

    def limpar_filtros(self):
        self.filtro_data_inicio = ""
        self.filtro_data_fim = ""
        self.filtro_apenas_urgentes = False
        return DashboardState.carregar_tabela

    # ------------------------------------------------------- load
    def load_dashboard_data(self):
        if not self.is_authenticated:
            return rx.redirect("/login")

        from sales_support_agent.services import classificacao, classificacao_config
        from sales_support_agent.services.classificacao_rules import rotulo

        # Uma rodada que morreu com o processo deixaria a tela girando para
        # sempre e travaria a próxima. Recuperar aqui é o que garante que abrir
        # o dashboard sempre mostra um estado coerente.
        classificacao.recuperar_rodada_travada(self.tenant_id)
        classificacao_config.ensure_config(self.tenant_id)
        classificacao_config.ensure_pastas(self.tenant_id)

        cfg = classificacao_config.get_config(self.tenant_id)
        self.horario_1 = cfg["horario_1"]
        self.horario_2 = cfg["horario_2"]
        self.janela_urgencia_horas = str(cfg["janela_urgencia_horas"])
        self.lookback_horas = str(cfg["lookback_horas"])

        proxima = classificacao_config.proxima_execucao(brt_now(), cfg)
        self.proxima_execucao = proxima.strftime("%d/%m/%Y %H:%M") if proxima else "-"

        self.pastas = [
            PastaUI(
                classe=p["classe"],
                classe_label=rotulo(p["classe"]),
                pasta_nome=p["pasta_nome"],
                pasta_caminho=p["pasta_caminho"],
                resolvido=p["resolvido"],
                erro_resolucao=p["erro_resolucao"],
            )
            for p in classificacao_config.get_pastas(self.tenant_id)
        ]
        self.pasta_inputs = {p.classe: p.pasta_nome for p in self.pastas}

        self.is_running = classificacao.ha_rodada_em_andamento(self.tenant_id)
        self._carregar_metricas()
        self.carregar_tabela()

    def _carregar_metricas(self):
        from sales_support_agent.services import emails_query

        m = emails_query.metricas_execucao(self.tenant_id)
        self.duracao_media = m["duracao_media"]
        self.duracao_ultima = m["duracao_ultima"]
        self.total_classificados = m["total_classificados"]
        self.ultima_rodada_classificados = m["ultima_rodada_classificados"]
        self.ultima_rodada_quando = m["ultima_rodada_quando"]
        self.ultima_rodada_origem = m["ultima_rodada_origem"]

    def carregar_tabela(self):
        from sales_support_agent.services import emails_query

        def _ui(dados: dict) -> EmailUI:
            return EmailUI(
                id=dados["id"],
                assunto=dados["assunto"],
                cliente=dados["cliente"],
                recebido_em=dados["recebido_em"],
                classe_label=dados["classe_label"],
                urgente=dados["urgente"],
                resumo=dados.get("resumo", ""),
                acao_sugerida=dados.get("acao_sugerida", ""),
            )

        self.emails = [
            _ui(d)
            for d in emails_query.listar_emails(
                self.tenant_id,
                data_inicio=self.filtro_data_inicio,
                data_fim=self.filtro_data_fim,
                apenas_urgentes=self.filtro_apenas_urgentes,
            )
        ]
        self.urgentes = [_ui(d) for d in emails_query.urgencias(self.tenant_id, limite=10)]

    @rx.var
    def sem_emails(self) -> bool:
        return len(self.emails) == 0

    @rx.var
    def sem_urgentes(self) -> bool:
        return len(self.urgentes) == 0

    @rx.var
    def pastas_pendentes(self) -> bool:
        return any(not p.resolvido for p in self.pastas)

    # ------------------------------------------------------- configuração
    def salvar_configuracao(self):
        from sales_support_agent.services import agendador, classificacao_config, emails_query

        for rotulo_campo, valor in (("horário 1", self.horario_1), ("horário 2", self.horario_2)):
            if not _hhmm_ok(valor):
                return toast_error(f"O {rotulo_campo} precisa estar no formato HH:MM.")

        try:
            janela = int(self.janela_urgencia_horas)
            lookback = int(self.lookback_horas)
        except (TypeError, ValueError):
            return toast_error("A janela de urgência e o lookback precisam ser números inteiros.")
        if janela < 1 or lookback < 1:
            return toast_error("A janela de urgência e o lookback precisam ser maiores que zero.")

        classificacao_config.salvar_config(
            self.tenant_id,
            horario_1=self.horario_1,
            horario_2=self.horario_2,
            janela_urgencia_horas=janela,
            lookback_horas=lookback,
        )

        # Mudar a janela re-marca os e-mails JÁ classificados, com um UPDATE.
        # É para isso que o prazo em horas fica guardado separado do booleano:
        # sem ele, a mesma mudança exigiria reprocessar a caixa no modelo.
        alteradas = emails_query.recalcular_urgencia(self.tenant_id, janela)

        # Sem reiniciar o servidor: o agendador relê os horários agora.
        agendador.reprogramar(self.tenant_id)

        with rx.session() as session:
            self.log_activity(
                "CONFIG_CLASSIFICACAO",
                f"Horários {self.horario_1} e {self.horario_2}, janela de urgência {janela}h.",
                session,
            )

        recado = "Configuração salva."
        if alteradas:
            recado += f" {alteradas} e-mail(s) tiveram a marcação de urgente revista."
        return [toast_success(recado), DashboardState.load_dashboard_data]

    @rx.event(background=True)
    async def listar_pastas_do_outlook(self):
        """Busca as pastas para o usuário escolher em vez de digitar."""
        async with self:
            if self.listando_pastas:
                return
            self.listando_pastas = True

        from sales_support_agent.services import graph_client
        from sales_support_agent.services.graph_client import GraphClientError

        try:
            pastas = await graph_client.listar_pastas()
        except GraphClientError as erro:
            async with self:
                self.listando_pastas = False
            yield toast_error(erro.mensagem)
            return

        async with self:
            self.pastas_disponiveis = sorted(p["caminho"] for p in pastas)
            self.listando_pastas = False
        yield toast_success(f"{len(pastas)} pasta(s) encontradas na caixa.")

    @rx.event(background=True)
    async def salvar_pasta(self, classe: str):
        """Resolve o NOME digitado para o id da pasta, pelo Graph.

        O usuário nunca vê nem digita um id. Nome ambíguo NÃO sobrescreve o
        mapeamento que já funcionava: uma tentativa malsucedida de
        reconfiguração não pode derrubar a rodada agendada seguinte.
        """
        async with self:
            nome = (self.pasta_inputs.get(classe) or "").strip()
            tenant_id = self.tenant_id

        if not nome:
            yield toast_error("Informe o nome da pasta.")
            return

        from sales_support_agent.services import classificacao_config, graph_client
        from sales_support_agent.services.graph_client import GraphClientError

        try:
            achado = await graph_client.resolver_pasta(nome)
        except GraphClientError as erro:
            yield toast_error(erro.mensagem)
            return

        if not achado["encontrado"]:
            if achado["candidatos"]:
                caminhos = ", ".join(achado["candidatos"])
                recado = (
                    f"Há mais de uma pasta chamada '{nome}': {caminhos}. "
                    "Informe o caminho completo para não arquivar no lugar errado."
                )
            else:
                recado = f"Não encontrei a pasta '{nome}' na caixa configurada."

            classificacao_config.salvar_pasta(
                tenant_id, classe, pasta_nome=nome, erro_resolucao=recado
            )
            yield toast_error(recado)
            yield DashboardState.load_dashboard_data
            return

        classificacao_config.salvar_pasta(
            tenant_id, classe, pasta_nome=nome,
            pasta_id=achado["id"], pasta_caminho=achado["caminho"],
        )
        yield toast_success(f"Pasta '{achado['caminho']}' vinculada.")
        yield DashboardState.load_dashboard_data

    # ------------------------------------------------------- execução manual
    @rx.event(background=True)
    async def classificar_agora(self):
        """Dispara a MESMA rodada que o agendador dispara.

        Um caminho de código só é o que faz este botão ser um teste de verdade
        do que roda sozinho às 08:00.
        """
        async with self:
            if self.is_running:
                yield toast_error("Já existe uma classificação em andamento.")
                return
            tenant_id = self.tenant_id
            user_email = self.user_email
            self.is_running = True
            self.progresso_texto = "Preparando..."
            self.progresso_atual = 0
            self.progresso_total = 0

        from sales_support_agent.services import agendador, classificacao

        run_id = agendador.reivindicar_rodada(
            tenant_id, origem="manual", user_email=user_email
        )
        if run_id is None:
            async with self:
                self.is_running = False
                self.progresso_texto = ""
            yield toast_error("Já existe uma classificação em andamento.")
            return

        resumo, erro = None, ""
        async for evento in classificacao.stream_classificacao(
            tenant_id, user_email=user_email, origem="manual", run_id=run_id
        ):
            if evento[0] == "progress":
                async with self:
                    self.progresso_atual = evento[1]
                    self.progresso_total = evento[2]
                    self.progresso_texto = evento[3]
            elif evento[0] == "done":
                resumo = evento[1]
            elif evento[0] == "error":
                erro = evento[1]

        agendador.finalizar_rodada(run_id, resumo or {}, erro)

        async with self:
            self.is_running = False
            self.progresso_texto = ""

        if erro:
            yield toast_error(erro)
        else:
            r = resumo or {}
            yield toast_success(
                f"{r.get('classificados', 0)} classificado(s), "
                f"{r.get('ignorados', 0)} ignorado(s), "
                f"{r.get('puladas', 0)} já conhecido(s)."
            )
        yield DashboardState.load_dashboard_data

    # ------------------------------------------------------- detalhe
    def abrir_detalhe(self, email_id: int):
        from sales_support_agent.services import emails_query

        dados = emails_query.detalhe_email(self.tenant_id, email_id)
        if not dados:
            return toast_error("E-mail não encontrado.")

        self.detalhe_assunto = dados["assunto"]
        self.detalhe_cliente = dados["cliente"]
        self.detalhe_recebido = dados["recebido_em"]
        self.detalhe_classe = dados["classe_label"]
        self.detalhe_urgente = dados["urgente"]
        self.detalhe_resumo = dados["resumo"]
        self.detalhe_pontos = dados["pontos_chave"]
        self.detalhe_acao = dados["acao_sugerida"]
        self.detalhe_prazo = dados["prazo_mencionado"] or "-"
        self.detalhe_disponivel = dados["resumo_disponivel"]
        self.detalhe_link = dados["web_link"]
        self.detalhe_aberto = True


def _hhmm_ok(texto: str) -> bool:
    try:
        horas, minutos = str(texto).split(":")
        return 0 <= int(horas) <= 23 and 0 <= int(minutos) <= 59
    except (ValueError, AttributeError):
        return False


class ChatMessageUI(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class ConsultaState(AppState):
    """Chat com o agente de Consulta — histórico, envio, streaming e limpeza.

    A casca de UI vem do antigo chat de Insights IA e foi mantida inteira: a
    fonte de dados por trás das tools muda na Fase 10, mas o comportamento de
    tela (streaming, rolagem, limpeza de conversa) já era o desejado.
    """

    messages: List[ChatMessageUI] = []
    pergunta: str = ""
    is_sending: bool = False
    error: str = ""
    clear_dialog_open: bool = False

    def set_pergunta(self, value: str):
        self.pergunta = value

    def set_clear_dialog_open(self, value: bool):
        self.clear_dialog_open = value

    @rx.var
    def sem_mensagens(self) -> bool:
        return len(self.messages) == 0

    def load_consulta(self):
        """on_load de /consulta: mesmo gate de autenticação das demais páginas."""
        if not self.is_authenticated:
            return rx.redirect("/login")

        from sales_support_agent.models import ChatMessage

        with rx.session() as session:
            linhas = (
                session.query(ChatMessage)
                .filter(
                    ChatMessage.tenant_id == self.tenant_id,
                    ChatMessage.user_email == self.user_email,
                    ChatMessage.content != "",
                )
                .order_by(ChatMessage.id.asc())
                .all()
            )
            self.messages = [ChatMessageUI(role=r.role, content=r.content) for r in linhas]
        yield rx.scroll_to("chat-anchor", align_to_top=False)

    @rx.event(background=True)
    async def enviar_pergunta(self):
        """Envia a pergunta ao agente e vai preenchendo a resposta conforme o
        streaming chega (ver `services.consulta_agent.stream_resposta` sobre
        por que o texto só é liberado depois do guardrail de saída). Rola o
        chat para o fim a cada atualização (`rx.scroll_to`) e espaça os
        pedaços com uma pausa pequena — sem ela, todos os pedaços chegam e são
        aplicados praticamente juntos (o texto já veio inteiro do backend) e
        a rolagem em si dá a única pista visual de "streaming"; a pausa faz o
        preenchimento parecer digitação de verdade."""
        async with self:
            if self.is_sending:
                return
            pergunta = self.pergunta.strip()
            if not pergunta:
                return
            tenant_id = self.tenant_id
            user_email = self.user_email

            self.pergunta = ""
            self.error = ""
            self.is_sending = True
            self.messages = self.messages + [
                ChatMessageUI(role="user", content=pergunta),
                ChatMessageUI(role="assistant", content=""),
            ]
        yield rx.scroll_to("chat-anchor", align_to_top=False)

        from sales_support_agent.models import TokenUsage
        from sales_support_agent.services.consulta_agent import stream_resposta

        def _gravar_consumo(usage):
            """O turno gastou, então o turno é cobrado.

            Vale também para o turno que terminou em erro: um guardrail que
            bloqueia já consumiu as suas chamadas, e não gravar aqui faria esse
            gasto sumir do custo em `/admin`.
            """
            if not (usage["input"] or usage["output"]):
                return
            with rx.session() as session:
                session.add(TokenUsage(
                    tenant_id=tenant_id, agent_name="consulta_agent",
                    model=modelo_do_agente("consulta_agent"),
                    input_tokens=usage["input"], output_tokens=usage["output"],
                ))
                session.commit()

        texto_atual = ""
        async for event in stream_resposta(tenant_id, user_email, pergunta):
            kind = event[0]

            if kind == "delta":
                _, pedaco = event
                texto_atual += pedaco
                async with self:
                    self.messages = self.messages[:-1] + [
                        ChatMessageUI(role="assistant", content=texto_atual)
                    ]
                yield rx.scroll_to("chat-anchor", align_to_top=False)
                await asyncio.sleep(0.04)

            elif kind == "done":
                _, texto, usage = event
                _gravar_consumo(usage)
                async with self:
                    self.messages = self.messages[:-1] + [
                        ChatMessageUI(role="assistant", content=texto)
                    ]
                yield rx.scroll_to("chat-anchor", align_to_top=False)

            elif kind == "error":
                _, msg, usage = event
                _gravar_consumo(usage)
                async with self:
                    # Remove o placeholder vazio do assistente — só a pergunta
                    # do usuário e o erro ficam visíveis.
                    self.messages = self.messages[:-1]
                    self.error = msg
                    yield toast_error(msg)

        async with self:
            self.is_sending = False

    def confirm_limpar_conversa(self):
        """Apaga todo o histórico da conversa (usuário atual, neste tenant)."""
        from sales_support_agent.models import ChatMessage

        with rx.session() as session:
            session.query(ChatMessage).filter(
                ChatMessage.tenant_id == self.tenant_id,
                ChatMessage.user_email == self.user_email,
            ).delete(synchronize_session=False)
            session.commit()

        self.clear_dialog_open = False
        self.messages = []
        return toast_success("Conversa limpa.")


# ==========================================================================
# God Mode (Super Admin): métricas, gráficos e gestão de usuários.
# ==========================================================================
def _brl(value: float) -> str:
    """Formata um número como moeda brasileira (R$ 1.234,56)."""
    s = f"{value:,.2f}"  # ex.: 1,234.56 (padrão en-US)
    s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"


def _usd(value: float) -> str:
    """Formata em dólar (US$ 1.234,56) — a OpenAI cobra em USD.

    Mantém a pontuação pt-BR (ponto de milhar, vírgula decimal) para ler igual
    aos valores em real do mesmo painel; só o símbolo muda."""
    s = f"{value:,.2f}"
    s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"US$ {s}"


# Janela dos gráficos do God Mode. Todos usam a mesma para serem comparáveis
# lado a lado — séries com eixos diferentes induzem leitura errada.
MESES_NO_GRAFICO = 6


def _monthly_series(rows, value_fn, meses: int = MESES_NO_GRAFICO) -> List[dict]:
    """Soma value_fn por mês nos últimos `meses`, do mais antigo ao atual.

    Os meses sem registro entram com zero: sem isso, um indicador com um único
    mês de dados vira uma barra solitária que não deixa comparar evolução.
    Agregado em Python para ser portável entre sqlite/postgres."""
    from collections import defaultdict

    agg: dict = defaultdict(float)
    for r in rows:
        dt = r.created_at
        agg[(dt.year, dt.month)] += value_fn(r)

    hoje = brt_now()
    chaves: List[tuple] = []
    ano, mes = hoje.year, hoje.month
    for _ in range(meses):
        chaves.append((ano, mes))
        mes -= 1
        if mes == 0:
            ano, mes = ano - 1, 12
    chaves.reverse()

    return [
        {"mes": f"{m:02d}/{a}", "valor": round(agg.get((a, m), 0.0), 2)}
        for a, m in chaves
    ]


class UserUI(BaseModel):
    """Linha da tabela de gestão de usuários no God Mode."""
    id: int
    name: str
    email: str
    is_superadmin: bool
    class_label: str
    # "Ativo" (já definiu a senha) | "Convite pendente" (ainda não aceitou).
    status: str


class AdminState(AppState):
    """Estado do painel do Super Admin (God Mode). Centraliza os dados do painel
    para não inchar o AppState base herdado por todas as páginas."""

    # Métricas
    total_users: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    # Custo dos tokens: cada consumo x o preço do modelo que o gerou
    # (TokenPricing tem uma linha por modelo). Acumulado e mês corrente.
    input_cost_label: str = "US$ 0,00"
    output_cost_label: str = "US$ 0,00"
    input_cost_month_label: str = "US$ 0,00"
    output_cost_month_label: str = "US$ 0,00"

    # Séries mensais para os gráficos (entrada e saída são indicadores separados).
    #
    # O custo de terceiros (KipFlow em BRL, Hunter em créditos) saiu na conversão
    # junto com o funil de prospecção. Restou um consumo medido só: tokens da
    # OpenAI, multiplicados pelo preço do modelo que os gerou.
    monthly_input_tokens: List[dict] = []
    monthly_output_tokens: List[dict] = []
    monthly_input_cost: List[dict] = []
    monthly_output_cost: List[dict] = []

    # Listas
    admin_users: List[UserUI] = []

    # Edição de usuário (diálogo controlado)
    user_dialog_open: bool = False
    edit_user_id: int = 0
    edit_user_name: str = ""
    edit_user_email: str = ""
    edit_user_class: str = "Usuário"

    # Confirmações destrutivas (diálogos controlados)
    confirm_counters_open: bool = False
    confirm_logs_open: bool = False

    # Convite de novo usuário (diálogo controlado). Não há campo de senha: o
    # convidado define a própria senha pelo link enviado por e-mail.
    create_dialog_open: bool = False
    new_user_name: str = ""
    new_user_email: str = ""
    new_user_class: str = "Usuário"

    def load_dashboard(self):
        """on_load do /admin: carrega métricas, séries, usuários e logs (superadmin-only)."""
        if not self.is_superadmin:
            return rx.redirect("/dashboard")

        from sales_support_agent.models import TokenUsage
        from sales_support_agent.services.token_pricing import (
            ensure_token_pricing,
            funcoes_de_custo,
            get_pricing,
        )

        with rx.session() as session:
            users = session.query(User).all()
            self.total_users = len(users)

            # Tokens (entrada e saída separados — têm preços diferentes)
            tks = session.query(TokenUsage).all()
            self.total_input_tokens = sum(t.input_tokens for t in tks)
            self.total_output_tokens = sum(t.output_tokens for t in tks)
            self.monthly_input_tokens = _monthly_series(tks, lambda t: t.input_tokens)
            self.monthly_output_tokens = _monthly_series(tks, lambda t: t.output_tokens)

            # Custo dos tokens: cada linha de consumo é multiplicada pelo preço
            # do SEU modelo (`TokenUsage.model`), não por um preço único — os
            # modelos da mesma família diferem em uma ordem de grandeza.
            #
            # Linhas antigas, gravadas antes de `TokenUsage.model` existir, têm
            # modelo vazio: caem no modelo que o agente delas usa hoje. É
            # aproximação, mas é a única atribuição possível para elas — e vale
            # só para o histórico, não para consumo novo.
            ensure_token_pricing()
            _custo_entrada, _custo_saida = funcoes_de_custo(
                get_pricing(),
                {nome: modelo_do_agente(nome) for nome in AGENTE_PARA_CHAVE_DE_CONFIG},
            )

            self.input_cost_label = _usd(sum(_custo_entrada(t) for t in tks))
            self.output_cost_label = _usd(sum(_custo_saida(t) for t in tks))

            # Mesmo `_monthly_series` (e mesma janela de 6 meses) dos demais
            # gráficos, só trocando a função de valor.
            self.monthly_input_cost = _monthly_series(tks, _custo_entrada)
            self.monthly_output_cost = _monthly_series(tks, _custo_saida)

            mes_atual = brt_now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            tks_mes = [t for t in tks if t.created_at >= mes_atual]
            self.input_cost_month_label = _usd(sum(_custo_entrada(t) for t in tks_mes))
            self.output_cost_month_label = _usd(sum(_custo_saida(t) for t in tks_mes))

            # Usuários. "Convite pendente" = convidado que ainda não clicou no
            # link para definir a senha (por isso `hashed_password` é nulo).
            self.admin_users = [
                UserUI(
                    id=u.id,
                    name=u.name,
                    email=u.email,
                    is_superadmin=u.is_superadmin,
                    class_label="Super Admin" if u.is_superadmin else "Usuário",
                    status="Ativo" if u.hashed_password else "Convite pendente",
                )
                for u in users
            ]

            # Feed de auditoria (mesmo formato do painel antigo)
            db_logs = session.query(ActivityLog).order_by(ActivityLog.timestamp.desc()).limit(50).all()
            self.admin_logs = [
                LogUI(
                    timestamp=log.timestamp.strftime("%d/%m/%Y %H:%M") if log.timestamp else "",
                    tenant_id=str(log.tenant_id),
                    user_email=log.user_email,
                    action=log.action,
                    details=log.details,
                )
                for log in db_logs
            ]
            self.total_tenants = session.query(Tenant).count()

    # --- Confirmações ---
    # Nesta versão do Reflex os setters NÃO são gerados automaticamente
    # (get_state_auto_setters()), então todo campo ligado a um on_change/
    # on_open_change precisa do seu `set_*` declarado aqui.
    def set_confirm_counters_open(self, value: bool):
        self.confirm_counters_open = value

    def set_confirm_logs_open(self, value: bool):
        self.confirm_logs_open = value

    # --- Gestão de usuários ---
    def set_user_dialog_open(self, value: bool):
        self.user_dialog_open = value

    def set_edit_user_name(self, value: str):
        self.edit_user_name = value

    def set_edit_user_email(self, value: str):
        self.edit_user_email = value

    def set_edit_user_class(self, value: str):
        self.edit_user_class = value

    # --- Convite de usuário (super admin) ---
    def set_create_dialog_open(self, value: bool):
        self.create_dialog_open = value

    def set_new_user_name(self, value: str):
        self.new_user_name = value

    def set_new_user_email(self, value: str):
        self.new_user_email = value

    def set_new_user_class(self, value: str):
        self.new_user_class = value

    def open_create_user(self):
        """Abre o diálogo de convite com o formulário limpo."""
        self.new_user_name = ""
        self.new_user_email = ""
        self.new_user_class = "Usuário"
        self.create_dialog_open = True

    def create_user(self):
        """Convida um novo usuário (superadmin-only) — única forma de admissão
        na plataforma, já que não existe cadastro público.

        O super admin informa apenas nome, e-mail e classe: NENHUMA senha é
        definida aqui. O convidado recebe um link de uso único (24h) e escolhe a
        própria senha em /redefinir-senha, então nunca existe uma senha
        provisória circulando por e-mail ou conhecida por terceiros.
        """
        if not self.is_superadmin:
            return
        name = self.new_user_name.strip()
        email = self.new_user_email.strip()
        if not name or not email:
            return toast_error("Preencha nome e e-mail.")

        token = secrets.token_urlsafe(32)
        with rx.session() as session:
            if session.query(User).filter(User.email == email).first():
                return toast_error("Este e-mail já está cadastrado.")

            # Organização única: todo convidado entra no mesmo tenant do super
            # admin que o convidou (a Coester).
            user = User(
                email=email,
                name=name,
                # Sem hash: o usuário só passa a ter senha ao aceitar o convite.
                tenant_id=self.tenant_id,
                is_superadmin=self.new_user_class == "Super Admin",
                reset_token=token,
                reset_token_expires=brt_now() + timedelta(hours=24),
            )
            session.add(user)
            session.commit()
            self.log_activity("USER_INVITE", f"Convidou o usuário {email}.", session)

        from sales_support_agent.services.emails import send_invite_email
        send_invite_email(email, name, f"{base_url()}/redefinir-senha?token={token}")

        self.create_dialog_open = False
        return [toast_success("Convite enviado."), AdminState.load_dashboard]

    def open_edit_user(self, user_id: int):
        """Carrega o usuário no formulário do diálogo e abre o diálogo."""
        for u in self.admin_users:
            if u.id == user_id:
                self.edit_user_id = u.id
                self.edit_user_name = u.name
                self.edit_user_email = u.email
                self.edit_user_class = u.class_label
                break
        self.user_dialog_open = True

    def save_user(self):
        """Salva nome, e-mail e classe do usuário editado (superadmin-only).

        Promover/rebaixar super admin é feito aqui: `edit_user_class` alterna
        entre "Usuário" e "Super Admin" — é assim que a hierarquia se propaga
        (um super admin pode criar outros super admins).
        """
        if not self.is_superadmin or not self.edit_user_id:
            return
        with rx.session() as session:
            u = session.get(User, self.edit_user_id)
            if not u:
                return toast_error("Usuário não encontrado.")
            u.name = self.edit_user_name.strip()
            u.email = self.edit_user_email.strip()
            u.is_superadmin = self.edit_user_class == "Super Admin"
            session.add(u)
            session.commit()
            self.log_activity("USER_EDIT", f"Editou o usuário {u.email}.", session)
        self.user_dialog_open = False
        return [toast_success("Usuário atualizado."), AdminState.load_dashboard]

    def delete_user(self, user_id: int):
        """Revoga o acesso de um usuário (superadmin-only, não a si mesmo).

        MUDANÇA IMPORTANTE em relação ao modelo SaaS: antes cada usuário tinha
        seu próprio tenant, então excluí-lo levava junto o tenant e todos os
        dados dele. Agora existe UM único tenant (Coester) compartilhado — apagar
        por `tenant_id` destruiria a base inteira da empresa. Por isso o que sai
        daqui é só o usuário e o que é inequivocamente pessoal dele (a conversa
        privada do Insights IA). Leads, pesquisas e enriquecimentos são
        patrimônio da organização e permanecem, com a autoria preservada em
        `user_email` para o filtro de /leads continuar fazendo sentido.
        """
        if not self.is_superadmin:
            return
        from sales_support_agent.models import ChatMessage

        with rx.session() as session:
            u = session.get(User, user_id)
            if not u:
                return toast_error("Usuário não encontrado.")
            if u.email == self.user_email:
                return toast_error("Você não pode excluir a si mesmo.")

            email = u.email
            session.query(ChatMessage).filter(ChatMessage.user_email == email).delete()
            session.delete(u)
            session.commit()
            self.log_activity("USER_DELETE", f"Excluiu o usuário {email}.", session)
        return [toast_success("Usuário excluído."), AdminState.load_dashboard]

    def clear_counters(self):
        """Zera o contador de consumo de tokens de IA.

        NÃO toca em usuários, leads nem no feed de auditoria — é só o histórico
        de consumo que alimenta os cards e gráficos de custo.

        Depois da conversão restou um contador só: `TokenUsage`. Os de terceiros
        (KipFlow em reais, Hunter em créditos) saíram junto com o funil de
        prospecção. O de Hunter era o único que NÃO podia ser zerado, porque não
        era indicador e sim o gate da cota do ciclo; com ele fora, o botão voltou
        a ser o que aparenta.

        `ClassificacaoRun` e os e-mails classificados não entram aqui: são
        histórico de operação, não contador de consumo."""
        if not self.is_superadmin:
            return
        from sales_support_agent.models import TokenUsage

        with rx.session() as session:
            tokens = session.query(TokenUsage).delete()
            session.commit()
            self.log_activity(
                "CONTADORES_LIMPOS",
                f"{tokens} registro(s) de consumo de tokens removidos.",
                session,
            )
        self.confirm_counters_open = False
        return [
            toast_success("Contadores de consumo zerados."),
            AdminState.load_dashboard,
        ]

    def clear_old_logs(self):
        """Limpa o feed de auditoria e recarrega o painel (superadmin-only)."""
        if not self.is_superadmin:
            return
        with rx.session() as session:
            session.query(ActivityLog).delete()
            session.commit()
        self.confirm_logs_open = False
        self.load_dashboard()
        return toast_success("Logs removidos.")


def _formatar_preco_milhao(valor: float) -> str:
    """Preço por 1M de tokens como texto para o input.

    Sem notação científica e sem zeros à direita inúteis: `0.25` e não
    `2.5e-01` nem `0.250000`, que é o que o super admin espera ver de volta
    depois de digitar."""
    return f"{valor:.6f}".rstrip("0").rstrip(".") or "0"


def _mascarar_db_url(url: str) -> str:
    """"postgresql://user:senha@host/db" -> "postgresql://user:***@host/db" —
    exibição só-leitura em /admin, nunca o valor real da senha."""
    import re

    return re.sub(r"://([^:/@]+):([^@/]+)@", r"://\1:***@", url)


class SettingsState(AppState):
    """Configuração de agentes de IA (modelo + reasoning effort) e da integração
    com a Microsoft Graph — superadmin-only.

    Classe própria (não incha AdminState), mesmo motivo de DashboardState/
    ConsultaState serem separadas de AppState — mas sua UI é renderizada
    DENTRO da página /admin já existente (pedido explícito: sem rota nova), e
    seu `load_settings` entra na mesma lista de `on_load` da rota `/admin`
    junto com `AdminState.load_dashboard`.

    Campos de segredo (`*_input`) NUNCA são populados com o valor
    decriptografado no load — só o `*_configurado` (bool) é lido, para mostrar
    "configurado"/"não configurado". Salvar com o campo em branco mantém o
    valor atual (não apaga) — só grava um segredo novo quando o super admin
    efetivamente digitar algo.
    """

    # --- Agentes de IA (3 pares modelo/effort) ---
    classificacao_model: str = ""
    classificacao_effort: str = ""
    resumo_model: str = ""
    resumo_effort: str = ""
    consulta_model: str = ""
    consulta_effort: str = ""

    # --- E-mail (Microsoft Graph API) ---
    # `graph_tenant_id` é o Directory (tenant) ID do Entra ID — não confundir
    # com o `tenant_id` da aplicação, herdado do AppState.
    graph_sender_email: str = ""
    graph_tenant_id: str = ""
    graph_client_id: str = ""
    graph_client_secret_input: str = ""
    graph_client_secret_configurado: bool = False
    graph_testando: bool = False
    # `inbox` (nome bem-conhecido) ou o id de uma pasta específica. Fica aqui,
    # e não na configuração operacional do dashboard, porque é nível de conexão.
    graph_pasta_origem: str = "inbox"
    graph_testando_leitura: bool = False

    # --- Preço do token OpenAI, por modelo ---
    # {modelo: valor digitado}. Texto (não float) porque é o que o input
    # devolve; a conversão e a validação ficam no `salvar`. Valores em USD por
    # 1 MILHÃO de tokens — formato da tabela de preços da OpenAI (o banco
    # guarda por token).
    # Já nascem com uma chave por modelo: o `rx.input` do card lê
    # `token_input_prices[modelo]` e, com o dicionário vazio, o valor chegaria
    # `undefined` no primeiro render (antes do on_load), tornando o campo
    # não-controlado no React.
    token_input_prices: Dict[str, str] = {m: "0" for m in MODELOS_DISPONIVEIS}
    token_output_prices: Dict[str, str] = {m: "0" for m in MODELOS_DISPONIVEIS}

    # --- Banco de dados (só leitura — não configurável aqui, ver Context) ---
    db_url_display: str = ""

    # ------------------------------------------------------- setters (auto-setters desligados)
    def set_classificacao_model(self, value: str):
        self.classificacao_model = value

    def set_classificacao_effort(self, value: str):
        self.classificacao_effort = value

    def set_resumo_model(self, value: str):
        self.resumo_model = value

    def set_resumo_effort(self, value: str):
        self.resumo_effort = value

    def set_consulta_model(self, value: str):
        self.consulta_model = value

    def set_consulta_effort(self, value: str):
        self.consulta_effort = value

    def set_graph_sender_email(self, value: str):
        self.graph_sender_email = value

    def set_graph_tenant_id(self, value: str):
        self.graph_tenant_id = value

    def set_graph_client_id(self, value: str):
        self.graph_client_id = value

    def set_graph_client_secret_input(self, value: str):
        self.graph_client_secret_input = value

    def set_graph_pasta_origem(self, value: str):
        self.graph_pasta_origem = value

    # Um par de setters para os 3 modelos: o modelo vem como argumento, ligado
    # no `on_change` da UI (`lambda v: ...set_token_input_price(modelo, v)`).
    # Assim incluir um 4º modelo em MODELOS_DISPONIVEIS não exige mexer aqui.
    def set_token_input_price(self, modelo: str, value: str):
        self.token_input_prices[modelo] = value

    def set_token_output_price(self, modelo: str, value: str):
        self.token_output_prices[modelo] = value

    # ------------------------------------------------------- load
    def load_settings(self):
        """Entra na mesma lista de on_load de /admin (ver sales_support_agent.py),
        junto com AdminState.load_dashboard — mesmo gate de superadmin,
        redundante por segurança (cada handler de escrita abaixo re-checa por
        conta própria)."""
        if not self.is_authenticated:
            return rx.redirect("/login")
        if not self.is_superadmin:
            return rx.redirect("/dashboard")

        from sales_support_agent.models import IntegrationSetting
        from sales_support_agent.services.settings import (
            AGENT_KEYS,
            ensure_agent_settings,
            ensure_integration_settings,
            get_agent_config,
        )
        from sales_support_agent.services import token_pricing

        ensure_agent_settings()
        ensure_integration_settings()

        for key in AGENT_KEYS:
            model, effort = get_agent_config(key)
            setattr(self, f"{key}_model", model)
            setattr(self, f"{key}_effort", effort)

        with rx.session() as session:
            linha = session.query(IntegrationSetting).first()
            if linha:
                self.graph_sender_email = linha.graph_sender_email
                self.graph_tenant_id = linha.graph_tenant_id
                self.graph_client_id = linha.graph_client_id
                self.graph_client_secret_configurado = bool(linha.graph_client_secret_enc)
                self.graph_pasta_origem = linha.graph_pasta_origem or "inbox"

        # Campos de segredo: sempre em branco no load (nunca o valor real).
        self.graph_client_secret_input = ""

        # Preço do token, por modelo: banco guarda por token, a UI mostra por 1M.
        # Itera MODELOS_DISPONIVEIS (e não as linhas do banco) para que a UI
        # mostre exatamente os modelos oferecidos hoje — uma linha remanescente
        # de modelo aposentado continua no banco, contando no custo histórico,
        # mas não vira campo editável.
        from sales_support_agent.services.settings import MODELOS_DISPONIVEIS

        token_pricing.ensure_token_pricing()
        precos = token_pricing.get_pricing()
        self.token_input_prices = {}
        self.token_output_prices = {}
        for modelo in MODELOS_DISPONIVEIS:
            preco_in, preco_out = precos.get(modelo, (0.0, 0.0))
            self.token_input_prices[modelo] = _formatar_preco_milhao(
                token_pricing.para_por_milhao(preco_in)
            )
            self.token_output_prices[modelo] = _formatar_preco_milhao(
                token_pricing.para_por_milhao(preco_out)
            )

        self.db_url_display = _mascarar_db_url(os.environ.get("DATABASE_URL", "sqlite:///reflex.db"))

    # ------------------------------------------------------- salvar
    def save_agent_config(self, agent_key: str):
        if not self.is_superadmin:
            return toast_error("Apenas super admin pode alterar configurações.")
        from sales_support_agent.services.settings import salvar_agent_config

        model = getattr(self, f"{agent_key}_model")
        effort = getattr(self, f"{agent_key}_effort")
        salvar_agent_config(agent_key, model, effort)
        with rx.session() as session:
            self.log_activity(
                "CONFIG_AGENTE_IA",
                f"Atualizou o agente '{agent_key}' para modelo {model} (effort {effort}).",
                session,
            )
            session.commit()
        return toast_success("Configuração do agente salva com sucesso.")

    def save_graph_settings(self):
        """Salva as credenciais da Microsoft Graph. O client secret só é gravado
        se o campo tiver sido preenchido — em branco preserva o valor atual."""
        if not self.is_superadmin:
            return toast_error("Apenas super admin pode alterar configurações.")
        from sales_support_agent.services.settings import salvar_integration_settings

        campos = {
            "graph_sender_email": self.graph_sender_email.strip(),
            "graph_tenant_id": self.graph_tenant_id.strip(),
            "graph_client_id": self.graph_client_id.strip(),
            "graph_pasta_origem": self.graph_pasta_origem.strip() or "inbox",
        }
        if self.graph_client_secret_input.strip():
            campos["graph_client_secret"] = self.graph_client_secret_input.strip()
        salvar_integration_settings(**campos)
        with rx.session() as session:
            self.log_activity(
                "CONFIG_INTEGRACAO",
                "Atualizou a configuração de e-mail (Microsoft Graph).",
                session,
            )
            session.commit()
        return [
            toast_success("Configuração de e-mail salva com sucesso."),
            SettingsState.load_settings,
        ]

    @rx.event(background=True)
    async def testar_graph(self):
        """Envia um e-mail de teste para o próprio super admin.

        Existe porque os erros mais comuns do Graph (falta da permissão de
        aplicação `Mail.Send` com consentimento de admin, ou um remetente que
        não é uma caixa real) só aparecem no momento do envio — sem este botão,
        a primeira notícia do problema seria um convite que nunca chegou.

        Em background porque são 2 chamadas HTTP (token + envio); num handler
        comum a UI congelaria sem feedback.
        """
        async with self:
            if not self.is_superadmin:
                return
            destino = self.user_email
            self.graph_testando = True

        from sales_support_agent.services.emails import montar_email_de_teste
        from sales_support_agent.services.graph_mailer import enviar_email, GraphMailerError

        assunto, html, logo = montar_email_de_teste()
        try:
            enviar_email(destino, assunto, html, inline_image_path=logo)
            resultado = toast_success(f"E-mail de teste enviado para {destino}.")
        except GraphMailerError as e:
            resultado = toast_error(str(e))
        except Exception as e:  # rede fora do ar, DNS, etc.
            resultado = toast_error(f"Falha inesperada no envio: {e}")

        async with self:
            self.graph_testando = False
        yield resultado

    @rx.event(background=True)
    async def testar_leitura_graph(self):
        """Confere que a credencial cobre LEITURA da caixa, e não só envio.

        Botão separado do "enviar e-mail de teste" porque `Mail.Send` e
        `Mail.ReadWrite` são permissões distintas no Entra ID e falham de formas
        distintas. Um envio bem-sucedido não diz nada sobre a leitura, e sem
        este teste o primeiro sinal de um consentimento faltando seria a
        execução automática das 08:00 falhando, num horário em que ninguém está
        olhando o painel.
        """
        async with self:
            if not self.is_superadmin:
                return
            self.graph_testando_leitura = True

        from sales_support_agent.services import graph_client
        from sales_support_agent.services.graph_client import GraphClientError

        try:
            resposta = await graph_client.testar_leitura()
            resultado = toast_success(
                f"Leitura confirmada: {resposta['total_pastas']} pasta(s) na caixa."
            )
        except GraphClientError as e:
            resultado = toast_error(e.mensagem)
        except Exception as e:  # rede fora do ar, DNS
            resultado = toast_error(f"Falha inesperada na leitura: {e}")

        async with self:
            self.graph_testando_leitura = False
        yield resultado

    def save_token_pricing(self):
        """Salva o preço por 1M de tokens de TODOS os modelos de uma vez
        (convertido para preço por token).

        Valida os três antes de gravar qualquer um: gravar parcialmente e depois
        falhar deixaria o painel com uma mistura de preço novo e antigo, sem o
        admin saber quais entraram.
        """
        if not self.is_superadmin:
            return toast_error("Apenas super admin pode alterar configurações.")
        from sales_support_agent.services import token_pricing
        from sales_support_agent.services.settings import MODELOS_DISPONIVEIS

        valores = {}
        for modelo in MODELOS_DISPONIVEIS:
            try:
                # Aceita vírgula: o admin digita como preferir.
                entrada = float(self.token_input_prices.get(modelo, "").replace(",", ".").strip() or 0)
                saida = float(self.token_output_prices.get(modelo, "").replace(",", ".").strip() or 0)
            except ValueError:
                return toast_error(f"Preço inválido em {modelo}. Use números (ex.: 0,25).")
            if entrada < 0 or saida < 0:
                return toast_error(f"Os preços de {modelo} não podem ser negativos.")
            valores[modelo] = (entrada, saida)

        for modelo, (entrada, saida) in valores.items():
            token_pricing.salvar_pricing(
                modelo,
                token_pricing.de_por_milhao(entrada),
                token_pricing.de_por_milhao(saida),
            )

        resumo = "; ".join(
            f"{modelo}: US$ {entrada}/1M entrada, US$ {saida}/1M saída"
            for modelo, (entrada, saida) in valores.items()
        )
        with rx.session() as session:
            self.log_activity("CONFIG_CUSTO_TOKEN", f"Atualizou o preço dos tokens. {resumo}.", session)
            session.commit()
        return [
            toast_success("Preço dos tokens salvo com sucesso."),
            AdminState.load_dashboard,
            SettingsState.load_settings,
        ]
