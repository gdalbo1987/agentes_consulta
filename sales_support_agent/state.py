import reflex as rx
import asyncio
import os
import json
import secrets
import uuid
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta
from .models import User, Tenant, Lead, ActivityLog, brt_now
# Import no topo (e não preguiçoso, como os demais deste arquivo) porque a
# constante é usada no VALOR PADRÃO de um campo de State, avaliado na criação
# da classe. `services/settings.py` só depende de `models` e `crypto`, então
# não há ciclo.
from .services.settings import HUNTER_MAX_CONTAS, MODELOS_DISPONIVEIS

# Slots das contas da Hunter, 1..HUNTER_MAX_CONTAS. Materializado aqui porque
# tanto o valor padrão dos campos de State quanto o `foreach` da UI precisam da
# lista pronta, e não do teto.
HUNTER_SLOTS = tuple(range(1, HUNTER_MAX_CONTAS + 1))
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

# Cota mensal de consultas POR USUÁRIO, aplicada em cada etapa do funil
# (pesquisa, enriquecimento e priorização contam separadamente, como sempre
# contaram). Escopo é o usuário, não a organização: dois usuários da Coester
# têm 20 cada um. Execuções com erro não consomem cota.
CONSULTA_LIMIT_MENSAL = 20

# Contatos decisores buscados por empresa no enriquecimento. A KipFlow cobra
# ~R$ 0,49 por PESSOA retornada, então este número é o principal fator de custo
# da fase — por isso vale para todos, inclusive super admin.
CONTACT_LIMIT = 4


def limite_consultas(is_superadmin: bool) -> int:
    """Cota mensal de consultas do usuário. Super admin segue ilimitado, como
    sempre foi — as etapas que ele dispara custam tokens de IA (custo nosso,
    visível no painel de custos), não crédito comprado de terceiro."""
    return 9999 if is_superadmin else CONSULTA_LIMIT_MENSAL


# ---------------------------------------------------------------------------
# Consumo de tokens: de qual agente veio -> qual configuração de modelo o gerou
# ---------------------------------------------------------------------------
# `agent_name` gravado em `TokenUsage` -> chave em `AgentModelSetting`.
# São cinco agentes para quatro configurações: `approach_agent` não tem a sua,
# roda no mesmo modelo da priorização (ver services/approach_agent.py:130).
AGENTE_PARA_CHAVE_DE_CONFIG = {
    "product_agent": "product",
    "prospect_agent": "prospect",
    "priorizacao_agent": "priorizacao",
    "approach_agent": "priorizacao",
    "insights_agent": "insights",
}


def modelo_do_agente(agent_name: str) -> str:
    """Modelo configurado HOJE para o agente — usado ao gravar `TokenUsage`.

    Ler no momento da gravação (e não no cálculo do custo) é o que faz o
    histórico de gasto ficar imune a uma troca de modelo posterior em `/admin`.
    """
    from sales_support_agent.services.settings import get_agent_config

    chave = AGENTE_PARA_CHAVE_DE_CONFIG.get(agent_name)
    return get_agent_config(chave)[0] if chave else ""


class ProductUI(BaseModel):
    """Modelo visual para listar produtos na tela (sem expor o SQLModel)."""
    id: int
    name: str
    description: str


class ProductState(AppState):
    """Cadastro de produtos da organização (criar, editar, excluir) com
    assistente de redação por IA.

    O catálogo é COMPARTILHADO: todos os usuários da Coester veem e usam os
    mesmos produtos (o filtro por `tenant_id` abaixo é o do tenant único). Não
    há limite de cadastros — o antigo teto vinha do plano de assinatura, que
    deixou de existir.
    """

    products: List[ProductUI] = []

    # Formulário
    form_name: str = ""
    form_description: str = ""
    editing_id: int = 0  # 0 = novo cadastro; >0 = editando

    # Assistente de IA
    is_generating: bool = False
    ai_error: str = ""

    def set_form_name(self, value: str):
        self.form_name = value

    def set_form_description(self, value: str):
        self.form_description = value

    @rx.var
    def product_count(self) -> int:
        return len(self.products)

    @rx.var
    def is_editing(self) -> bool:
        return self.editing_id != 0

    def load_products(self):
        """on_load: exige autenticação e carrega os produtos da organização."""
        if not self.is_authenticated:
            return rx.redirect("/login")

        with rx.session() as session:
            from sales_support_agent.models import Product

            rows = (
                session.query(Product)
                .filter(Product.tenant_id == self.tenant_id)
                .order_by(Product.created_at.desc())
                .all()
            )
            self.products = [
                ProductUI(id=p.id, name=p.name, description=p.description) for p in rows
            ]
        # Começa sempre com o formulário limpo
        self.new_product()

    def new_product(self):
        """Limpa o formulário para um novo cadastro."""
        self.editing_id = 0
        self.form_name = ""
        self.form_description = ""
        self.ai_error = ""
        self.is_generating = False

    def edit_product(self, product_id: int):
        """Carrega um produto existente no formulário para edição.

        O formulário fica acima da lista de produtos; ao clicar em editar num
        produto lá embaixo, o formulário é preenchido mas sai da tela — por isso
        rolamos até ele (e focamos a descrição) para dar retorno visual claro.
        """
        self.ai_error = ""
        for p in self.products:
            if p.id == product_id:
                self.editing_id = p.id
                self.form_name = p.name
                self.form_description = p.description
                break
        return rx.call_script(
            "const f=document.getElementById('product-form');"
            "if(f){f.scrollIntoView({behavior:'smooth',block:'start'});"
            "const ta=f.querySelector('textarea');if(ta){setTimeout(()=>ta.focus(),400);}}"
        )

    def save_product(self):
        """Cria ou atualiza o produto, respeitando o limite de cadastros."""
        self.ai_error = ""
        if not self.form_name.strip():
            return toast_error("Informe o nome do produto.")
        if not self.form_description.strip():
            return toast_error("Escreva (ou gere com a IA) a descrição do produto.")

        was_edit = bool(self.editing_id)
        with rx.session() as session:
            from sales_support_agent.models import Product

            if self.editing_id:
                # Atualização (com checagem de posse pelo tenant)
                prod = session.get(Product, self.editing_id)
                if not prod or prod.tenant_id != self.tenant_id:
                    return toast_error("Produto não encontrado.")
                prod.name = self.form_name.strip()
                prod.description = self.form_description.strip()
                session.commit()
                self.log_activity("PRODUTO_EDIT", f"Editou o produto '{prod.name}'.", session)
            else:
                prod = Product(
                    tenant_id=self.tenant_id,
                    name=self.form_name.strip(),
                    description=self.form_description.strip(),
                )
                session.add(prod)
                session.commit()
                self.log_activity("PRODUTO_NOVO", f"Cadastrou o produto '{prod.name}'.", session)

        self.new_product()
        msg = "Produto atualizado com sucesso." if was_edit else "Produto cadastrado com sucesso."
        return [toast_success(msg), ProductState.load_products]

    def delete_product(self, product_id: int):
        """Exclui um produto do tenant."""
        removed = False
        with rx.session() as session:
            from sales_support_agent.models import Product

            prod = session.get(Product, product_id)
            if prod and prod.tenant_id == self.tenant_id:
                session.delete(prod)
                session.commit()
                self.log_activity("PRODUTO_DEL", f"Removeu o produto '{prod.name}'.", session)
                removed = True

        if self.editing_id == product_id:
            self.new_product()
        if removed:
            return [toast_success("Produto removido."), ProductState.load_products]
        return ProductState.load_products

    async def generate_description(self):
        """Chama o agente de IA para complementar a descrição, exibindo o texto
        em streaming (aparece aos poucos). O campo continua editável e aplica o
        guardrail de afinidade com produto profissional."""
        self.ai_error = ""
        name = self.form_name
        draft = self.form_description
        if not name.strip() and not draft.strip():
            self.ai_error = "Escreva ao menos o nome ou um rascunho antes de usar a IA."
            return

        self.is_generating = True
        self.form_description = ""  # limpa o campo para receber o texto em streaming
        yield  # mostra o estado de carregando com o campo já vazio

        from sales_support_agent.services.product_agent import stream_product_text

        usage = {"input": 0, "output": 0}
        had_error = False
        async for event in stream_product_text(name, draft):
            kind = event[0]
            if kind == "delta":
                self.form_description += event[1]
                yield  # empurra o novo trecho para a UI
            elif kind == "done":
                # Consolida com o texto final aparado (mantém o que já foi exibido).
                self.form_description = event[1] or self.form_description
                usage = event[2]
                yield
            elif kind == "error":
                self.ai_error = event[1]
                self.form_description = draft  # restaura o rascunho do usuário
                had_error = True
                yield

        # Fim do fluxo: encerra o carregando (corrige o spinner que não sumia).
        self.is_generating = False
        yield

        # Registra o consumo de tokens (entrada e saída contados separadamente).
        if not had_error and (usage.get("input") or usage.get("output")):
            with rx.session() as session:
                from sales_support_agent.models import TokenUsage
                session.add(
                    TokenUsage(
                        tenant_id=self.tenant_id,
                        agent_name="product_agent",
                        model=modelo_do_agente("product_agent"),
                        input_tokens=usage.get("input", 0),
                        output_tokens=usage.get("output", 0),
                    )
                )
                session.commit()


# ==========================================================================
# Prospecção automática de leads (agente com web_search).
# ==========================================================================
class ProductOptionUI(BaseModel):
    """Produto do tenant com o toggle de seleção para a pesquisa."""
    id: int
    name: str
    selected: bool


class SearchState(AppState):
    """Configuração e execução da pesquisa de prospecção automática de leads."""

    # Formulário
    available_products: List[ProductOptionUI] = []
    regiao: str = ""
    segmento: str = ""

    # Cota mensal por usuário (superadmin sem limite prático). É a mesma cota de
    # todas as etapas do funil — ver PLAN_CONSULTA_LIMITS.
    search_limit: int = CONSULTA_LIMIT_MENSAL
    searches_this_month: int = 0

    # Execução em andamento (background task)
    is_running: bool = False
    progress_message: str = ""
    search_error: str = ""

    # Confirmação antes de executar: incluir as empresas que a base já tem
    # (renovando as notícias delas) ou buscar só empresas inéditas. É pergunta,
    # e não preferência salva, porque a resposta certa muda a cada rodada.
    confirm_open: bool = False
    # Quantas empresas a base já tem, mostrado no diálogo para a escolha ser
    # informada (com a base vazia as duas opções dão no mesmo).
    empresas_na_base: int = 0

    # Último resultado (reidratado do banco a cada load)
    has_result: bool = False
    last_search_at: str = ""
    last_status: str = ""
    last_total_empresas: int = 0
    last_meta_atingida: bool = False
    last_resumo: str = ""
    last_avisos: List[str] = []
    last_result_json: str = ""

    def set_regiao(self, value: str):
        self.regiao = value

    def set_segmento(self, value: str):
        self.segmento = value

    def set_confirm_open(self, value: bool):
        self.confirm_open = value

    def abrir_confirmacao(self):
        """Botão "Executar pesquisa": valida o formulário ANTES de perguntar.

        Perguntar sobre reinclusão para depois recusar por falta de produto
        seria pedir uma decisão que não vai ser usada.
        """
        if not self.selected_product_ids:
            return toast_error("Selecione ao menos um produto.")
        if not self.regiao.strip():
            return toast_error("Informe a região de interesse.")
        if not self.segmento.strip():
            return toast_error("Informe o segmento estratégico.")
        if not self.is_superadmin and self.searches_this_month >= self.search_limit:
            return toast_error(f"Limite mensal de {self.search_limit} pesquisas atingido.")
        self.confirm_open = True

    def toggle_product(self, product_id: int):
        for i, p in enumerate(self.available_products):
            if p.id == product_id:
                self.available_products[i] = ProductOptionUI(id=p.id, name=p.name, selected=not p.selected)
                break

    @rx.var
    def selected_product_ids(self) -> List[int]:
        return [p.id for p in self.available_products if p.selected]

    @rx.var
    def quota_reached(self) -> bool:
        return not self.is_superadmin and self.searches_this_month >= self.search_limit

    def load_search_config(self):
        """on_load de /pesquisa: exige autenticação (mesmo padrão de
        ProductState.load_products), depois carrega produtos, config salva
        (sobrescrita — uma linha por tenant), cota mensal e o último resultado."""
        if not self.is_authenticated:
            return rx.redirect("/login")

        from sales_support_agent.models import Product, ProspectCompany, SearchConfig, SearchRun

        with rx.session() as session:
            self.search_limit = limite_consultas(self.is_superadmin)

            # Alimenta o diálogo de confirmação: sem saber o tamanho da base, a
            # escolha entre reincluir e não reincluir é feita no escuro.
            self.empresas_na_base = (
                session.query(ProspectCompany)
                .filter(ProspectCompany.tenant_id == self.tenant_id)
                .count()
            )

            # Cota é POR USUÁRIO (filtro em user_email, não em tenant_id):
            # dois usuários da Coester têm 20 consultas cada um. Execuções que
            # falharam não consomem cota (mesma regra do enriquecimento): o
            # usuário não perde crédito por um erro nosso.
            month_start = brt_now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            self.searches_this_month = (
                session.query(SearchRun)
                .filter(
                    SearchRun.user_email == self.user_email,
                    SearchRun.started_at >= month_start,
                    SearchRun.status != "error",
                )
                .count()
            )

            products = (
                session.query(Product)
                .filter(Product.tenant_id == self.tenant_id)
                .order_by(Product.created_at.desc())
                .all()
            )

            config = session.query(SearchConfig).filter(SearchConfig.tenant_id == self.tenant_id).first()
            selected_ids = set()
            if config:
                try:
                    selected_ids = set(json.loads(config.product_ids))
                except (ValueError, TypeError):
                    selected_ids = set()
                self.regiao = config.regiao
                self.segmento = config.segmento
            else:
                self.regiao = ""
                self.segmento = ""

            self.available_products = [
                ProductOptionUI(id=p.id, name=p.name, selected=(p.id in selected_ids))
                for p in products
            ]

            last_run = (
                session.query(SearchRun)
                .filter(SearchRun.tenant_id == self.tenant_id)
                .order_by(SearchRun.started_at.desc())
                .first()
            )
            if last_run:
                self.has_result = last_run.status != "running"
                self.last_status = last_run.status
                self.last_total_empresas = last_run.total_empresas
                self.last_meta_atingida = last_run.meta_atingida
                self.last_resumo = last_run.resumo
                self.last_result_json = last_run.result_json
                # erros_ou_avisos só existe dentro do JSON (não é coluna própria).
                self.last_avisos = []
                if last_run.result_json:
                    try:
                        self.last_avisos = json.loads(last_run.result_json).get("erros_ou_avisos", [])
                    except (ValueError, TypeError):
                        self.last_avisos = []
                self.last_search_at = (
                    last_run.finished_at.strftime("%d/%m/%Y %H:%M")
                    if last_run.finished_at
                    else last_run.started_at.strftime("%d/%m/%Y %H:%M")
                )
                self.search_error = last_run.erro if last_run.status == "error" else ""
                self.is_running = last_run.status == "running"
            else:
                self.has_result = False
                self.last_status = ""
                self.is_running = False

    @rx.event(background=True)
    async def start_search(self, incluir_conhecidas: bool = False):
        """Dispara a pesquisa (execução longa: 2-10 min). Roda em background
        (@rx.event(background=True)) para sobreviver a troca de página/queda do
        socket — o status fica persistido em SearchRun e é reidratado por
        load_search_config ao recarregar a página.

        `incluir_conhecidas` vem da confirmação da tela: com True, as empresas
        que a base já tem e que pertencem a esta linha de pesquisa voltam para a
        rodada, com as notícias buscadas de novo. Em ambos os casos a base
        conhecida vai para o agente como lista de exclusão, para o orçamento de
        buscas procurar empresa inédita (ver services/search_scope.py).
        """
        async with self:
            if self.is_running:
                yield toast_error("Já existe uma pesquisa em andamento.")
                return
            if not self.selected_product_ids:
                yield toast_error("Selecione ao menos um produto.")
                return
            if not self.regiao.strip():
                yield toast_error("Informe a região de interesse.")
                return
            if not self.segmento.strip():
                yield toast_error("Informe o segmento estratégico.")
                return
            if not self.is_superadmin and self.searches_this_month >= self.search_limit:
                yield toast_error(f"Limite mensal de {self.search_limit} pesquisas atingido.")
                return

            produtos_snapshot = [(p.id, p.name) for p in self.available_products if p.selected]
            regiao = self.regiao.strip()
            segmento = self.segmento.strip()
            tenant_id = self.tenant_id
            user_email = self.user_email

            self.confirm_open = False
            self.is_running = True
            self.search_error = ""
            self.progress_message = "Iniciando pesquisa..."
            self.has_result = False

        from sales_support_agent.models import SearchConfig, SearchRun, TokenUsage
        from sales_support_agent.services.prospect_agent import stream_prospect_search, ProdutoInput
        from sales_support_agent.services.search_scope import montar_escopo

        with rx.session() as session:
            # Sobrescreve a configuração (regra de negócio: sem histórico de config).
            config = session.query(SearchConfig).filter(SearchConfig.tenant_id == tenant_id).first()
            product_ids_json = json.dumps([pid for pid, _ in produtos_snapshot])
            if config:
                config.product_ids = product_ids_json
                config.regiao = regiao
                config.segmento = segmento
                config.updated_at = brt_now()
            else:
                config = SearchConfig(
                    tenant_id=tenant_id, product_ids=product_ids_json, regiao=regiao, segmento=segmento
                )
                session.add(config)

            # Execução versionada (histórico fica aqui, não em SearchConfig).
            # pesquisa_id é gerado já aqui (não é nullable) e reaproveitado no
            # resultado final, para a linha "running" e o JSON persistido
            # compartilharem o mesmo identificador de execução.
            pesquisa_id = str(uuid.uuid4())
            run = SearchRun(
                tenant_id=tenant_id, user_email=user_email,
                pesquisa_id=pesquisa_id, status="running",
                regiao=regiao, segmento=segmento,
            )
            session.add(run)
            session.commit()
            session.refresh(run)
            run_id = run.id

        produtos = [ProdutoInput(id=pid, nome=name) for pid, name in produtos_snapshot]

        # Escopo: o que a base já tem e o que volta para renovar notícias. Roda
        # antes da pesquisa porque as duas listas são entrada da fase 1.
        async with self:
            self.progress_message = "Verificando as empresas já encontradas..."
        escopo = await montar_escopo(tenant_id, regiao, segmento, incluir_conhecidas)

        # O classificador de escopo usa o modelo do agente de prospecção e roda
        # dentro desta pesquisa, então o consumo dele entra no mesmo TokenUsage:
        # separá-lo criaria uma linha de custo para uma etapa que o usuário não
        # enxerga como etapa.
        usage_final = {"input": 0, "output": 0}
        usage_escopo = escopo.usage
        avisos_escopo = list(escopo.avisos)
        had_error = False
        async for event in stream_prospect_search(
            produtos, regiao, segmento,
            conhecidas=escopo.conhecidas, reincluir=escopo.reincluir,
        ):
            kind = event[0]
            if kind == "progress":
                async with self:
                    self.progress_message = event[2]
            elif kind == "done":
                resultado = event[1]
                usage_final = event[2]
                # Mantém um único pesquisa_id por execução (o gerado na criação
                # da linha "running"), em vez do uuid que o agente gera à toa.
                resultado.pesquisa_id = pesquisa_id
                # Os avisos do escopo entram no mesmo lugar que os da pesquisa:
                # é ali que a tela lê, e para o usuário foi tudo uma execução só.
                resultado.erros_ou_avisos = avisos_escopo + list(resultado.erros_ou_avisos)
                with rx.session() as session:
                    run = session.get(SearchRun, run_id)
                    if run:
                        run.status = "done"
                        run.total_empresas = resultado.total_empresas_encontradas
                        run.meta_atingida = resultado.meta_atingida
                        run.resumo = resultado.resumo_da_pesquisa
                        run.result_json = resultado.model_dump_json()
                        run.finished_at = brt_now()
                        session.add(TokenUsage(
                            tenant_id=tenant_id,
                            agent_name="prospect_agent",
                            model=modelo_do_agente("prospect_agent"),
                            input_tokens=usage_final.get("input", 0) + usage_escopo.get("input", 0),
                            output_tokens=usage_final.get("output", 0) + usage_escopo.get("output", 0),
                        ))
                        session.add(ActivityLog(
                            tenant_id=tenant_id,
                            user_email=user_email,
                            action="PESQUISA_EXEC",
                            details=f"Executou pesquisa de prospecção ({regiao} / {segmento}): "
                                    f"{resultado.total_empresas_encontradas} empresas.",
                            timestamp=brt_now(),
                        ))
                        session.commit()
                    cfg = session.query(SearchConfig).filter(SearchConfig.tenant_id == tenant_id).first()
                    if cfg:
                        cfg.last_search_at = brt_now()
                        session.commit()
                async with self:
                    self.has_result = True
                    self.last_status = "done"
                    self.last_total_empresas = resultado.total_empresas_encontradas
                    self.last_meta_atingida = resultado.meta_atingida
                    self.last_resumo = resultado.resumo_da_pesquisa
                    self.last_avisos = resultado.erros_ou_avisos
                    self.last_result_json = resultado.model_dump_json()
                    self.last_search_at = brt_now().strftime("%d/%m/%Y %H:%M")
                    self.searches_this_month += 1
                    yield toast_success("Pesquisa concluída.")
            elif kind == "error":
                had_error = True
                msg = event[1]
                with rx.session() as session:
                    run = session.get(SearchRun, run_id)
                    if run:
                        run.status = "error"
                        run.erro = msg
                        run.finished_at = brt_now()
                        session.commit()
                async with self:
                    self.search_error = msg
                    self.last_status = "error"
                    yield toast_error(msg)

        # Sempre encerra o "carregando", mesmo em erro (mesma classe de bug já
        # corrigida no assistente de descrição de produtos).
        async with self:
            self.is_running = False
            self.progress_message = ""

    def advance_to_next_step(self):
        """Botão de avançar: leva à fase 2 do funil (enriquecimento KipFlow)."""
        return rx.redirect("/enriquecimento")


# ==========================================================================
# Fase 2 do funil: enriquecimento das empresas via API KipFlow.
# ==========================================================================
class CompanyUI(BaseModel):
    """Linha da tabela de empresas na tela de enriquecimento.

    Tudo já vem formatado como str do backend (padrão do projeto: o frontend
    não formata data, moeda nem percentual)."""
    id: int
    nome: str
    cnpj: str
    cidade_uf: str
    porte: str
    status_cadastral: str
    # Situação especial (recuperação judicial, falência...). String vazia quando
    # não há — é o caso da maioria. Critério de risco para a próxima fase.
    alerta_situacao: str
    telefone: str
    qtd_contatos: int
    # Resumo dos contatos já formatado (ex.: "2 (sócios)" / "1 (LinkedIn)"),
    # porque a origem muda a abordagem comercial: sócio se procura pelo
    # telefone da empresa; perfil de LinkedIn tem canal direto.
    contatos_label: str
    percentual: int
    status: str


def _rotulo_email(email: Optional[str], confianca: Optional[int]) -> str:
    """E-mail do contato com a confiança da Hunter, pronto para exibir.

    A confiança acompanha o endereço porque muda o que se pode fazer com ele: a
    Hunter devolve tanto e-mails confirmados em fonte pública quanto palpites de
    padrão do domínio, e os dois chegam no mesmo campo. Sem o número, um palpite
    de 30% parece um endereço verificado.
    """
    if not email:
        return ""
    if confianca is None:
        return email
    return f"{email} ({confianca}% de confiança)"


class ContatoUI(BaseModel):
    """Contato decisor no pop-up de detalhe."""
    nome: str
    cargo: str
    senioridade: str
    area: str
    # "Quadro societário" ou "LinkedIn": muda a abordagem comercial, então é
    # exibido em vez do jargão cru ("qsa").
    origem: str
    perfil_url: str
    # E-mail profissional (Hunter.io). Vazio quando não foi encontrado ou
    # quando a cota de créditos do ciclo acabou antes de chegar neste contato.
    email: str = ""
    # Rótulo pronto para a tela, com a confiança da Hunter: um e-mail de score
    # baixo é um palpite de padrão ("nome.sobrenome@"), não um endereço
    # confirmado, e disparar campanha nele queima domínio. Formatar aqui, e não
    # no componente, mantém a regra num lugar só.
    email_label: str = ""


class NoticiaUI(BaseModel):
    """Notícia da empresa (produzida na fase de pesquisa)."""
    titulo: str
    data: str
    resumo: str
    url: str


class CompanyDetailUI(BaseModel):
    """Tudo que foi coletado sobre uma empresa, para o pop-up de detalhe."""
    id: int = 0
    nome: str = ""
    razao_social: str = ""
    cnpj: str = ""
    # --- cadastrais ---
    status_cadastral: str = ""
    alerta_situacao: str = ""
    alerta_situacao_desde: str = ""
    endereco_completo: str = ""
    cidade_uf: str = ""
    cep: str = ""
    data_inicio_atividade: str = ""
    idade_empresa: str = ""
    # --- comerciais ---
    porte: str = ""
    faturamento_estimado: str = ""
    segmento: str = ""
    telefone: str = ""
    telefone_whatsapp: bool = False
    website: str = ""
    linkedin_url: str = ""
    # --- controle ---
    percentual: int = 0
    status_label: str = ""
    enriquecido_em: str = ""
    contatos: List[ContatoUI] = []
    noticias: List[NoticiaUI] = []


def _montar_company_detail(session, c) -> "CompanyDetailUI":
    """Constrói o pop-up de detalhe completo de uma empresa.

    Extraído de EnrichmentState.open_company_detail para ser reaproveitado
    também por PriorizacaoState.open_lead_detail (mesmo lead, mesmos dados de
    enriquecimento — só a seção de priorização/approach muda por cima).
    """
    from sales_support_agent.models import CompanyContact
    from sales_support_agent.services.enrichment import noticias_por_empresa
    from sales_support_agent.services.enrichment_report import status_em_pt
    from sales_support_agent.services.enrichment_rules import formatar_telefone
    from sales_support_agent.services.normalizers import formatar_cnpj

    contatos = (
        session.query(CompanyContact)
        .filter(CompanyContact.company_id == c.id)
        .order_by(CompanyContact.id)
        .all()
    )
    contatos_ui = [
        ContatoUI(
            nome=ct.nome,
            cargo=ct.cargo or "",
            senioridade=ct.senioridade or "",
            area=ct.area or "",
            origem="Quadro societário" if ct.origem == "qsa" else "LinkedIn",
            perfil_url=ct.perfil_url or "",
            email=ct.email or "",
            email_label=_rotulo_email(ct.email, ct.email_confianca),
        )
        for ct in contatos
    ]

    endereco = ", ".join([p for p in (c.endereco, c.bairro) if p])
    cidade_uf = " / ".join([p for p in (c.cidade, c.estado) if p])

    noticias_ui = [
        NoticiaUI(
            titulo=str(n.get("titulo") or ""),
            data=str(n.get("data_publicacao") or ""),
            resumo=str(n.get("resumo") or ""),
            url=str(n.get("url") or n.get("fonte") or ""),
        )
        for n in noticias_por_empresa(c.tenant_id, c.search_run_id, [c]).get(c.id, [])
    ]

    return CompanyDetailUI(
        id=c.id,
        nome=c.nome,
        razao_social=c.razao_social or "",
        cnpj=formatar_cnpj(c.cnpj) or "",
        status_cadastral=c.status_cadastral or "",
        alerta_situacao=c.alerta_situacao or "",
        alerta_situacao_desde=c.alerta_situacao_desde or "",
        endereco_completo=endereco,
        cidade_uf=cidade_uf or c.localizacao,
        cep=c.cep or "",
        data_inicio_atividade=c.data_inicio_atividade or "",
        idade_empresa=(
            f"{c.idade_empresa_anos} anos" if c.idade_empresa_anos is not None else ""
        ),
        porte=c.porte or "",
        faturamento_estimado=c.faturamento_estimado or "",
        segmento=c.segmento or c.segmento_identificado or "",
        telefone=formatar_telefone(c.telefone) or "",
        telefone_whatsapp=c.telefone_whatsapp,
        website=c.website_principal or c.website or "",
        linkedin_url=c.linkedin_url or "",
        percentual=c.enrichment_percentage,
        status_label=status_em_pt(c.enrichment_status),
        enriquecido_em=(
            c.enriched_at.strftime("%d/%m/%Y %H:%M") if c.enriched_at else ""
        ),
        contatos=contatos_ui,
        noticias=noticias_ui,
    )


class EnrichmentState(AppState):
    """Configuração e execução do enriquecimento de empresas (KipFlow)."""

    # Empresas da pesquisa de origem
    companies: List[CompanyUI] = []
    total_empresas: int = 0

    # Pesquisa de origem (fase anterior)
    search_run_id: int = 0
    origem_regiao: str = ""
    origem_segmento: str = ""

    # Limite de contatos por empresa (é o principal fator de custo da fase)
    contact_limit: int = CONTACT_LIMIT

    # Cota mensal de consultas — a MESMA da pesquisa (PLAN_CONSULTA_LIMITS).
    consulta_limit: int = CONSULTA_LIMIT_MENSAL
    enrichments_this_month: int = 0

    # Pop-up de detalhe da empresa (clique na linha da tabela)
    detail_open: bool = False
    detail: CompanyDetailUI = CompanyDetailUI()

    # Geração do relatório .xlsx
    is_exporting: bool = False

    # Confirmação de exclusão em lote das empresas sem enriquecimento (0%)
    bulk_delete_dialog_open: bool = False

    # Execução em andamento (background task)
    is_running: bool = False
    progress_message: str = ""
    progress_atual: int = 0
    progress_total: int = 0
    enrichment_error: str = ""

    # Último resultado (reidratado do banco a cada load)
    has_result: bool = False
    last_status: str = ""
    last_processadas: int = 0
    last_puladas: int = 0
    last_falhas: int = 0
    last_custo: str = "R$ 0,00"
    last_avisos: List[str] = []
    last_run_at: str = ""

    @rx.var
    def sem_empresas(self) -> bool:
        return self.total_empresas == 0

    @rx.var
    def quota_reached(self) -> bool:
        return not self.is_superadmin and self.enrichments_this_month >= self.consulta_limit

    def load_enrichment(self):
        """on_load de /enriquecimento: exige autenticação (mesmo padrão de
        SearchState.load_search_config), materializa as empresas da última
        pesquisa concluída e reidrata o estado da última execução."""
        if not self.is_authenticated:
            return rx.redirect("/login")

        from sales_support_agent.models import (
            CompanyContact,
            EnrichmentRun,
            ProspectCompany,
            SearchRun,
        )
        from sales_support_agent.services.enrichment import ensure_companies_materialized
        from sales_support_agent.services.enrichment_rules import formatar_telefone
        from sales_support_agent.services.normalizers import formatar_cnpj

        with rx.session() as session:
            # Limite de contatos por empresa: vale inclusive para o super admin,
            # porque cada contato é dinheiro pago à KipFlow.
            self.contact_limit = CONTACT_LIMIT

            # Cota mensal de consultas — a mesma da pesquisa, e POR USUÁRIO.
            # Execuções que falharam não contam (ex.: aborto por Receita fora
            # do ar, que sequer chega a gastar).
            self.consulta_limit = limite_consultas(self.is_superadmin)
            month_start = brt_now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            self.enrichments_this_month = (
                session.query(EnrichmentRun)
                .filter(
                    EnrichmentRun.user_email == self.user_email,
                    EnrichmentRun.started_at >= month_start,
                    EnrichmentRun.status != "error",
                )
                .count()
            )

            # A fase de enriquecimento opera sobre a última pesquisa CONCLUÍDA.
            last_run = (
                session.query(SearchRun)
                .filter(SearchRun.tenant_id == self.tenant_id, SearchRun.status == "done")
                .order_by(SearchRun.started_at.desc())
                .first()
            )
            if not last_run:
                self.search_run_id = 0
                self.companies = []
                self.total_empresas = 0
                self.origem_regiao = ""
                self.origem_segmento = ""
                self.has_result = False
                self.is_running = False
                return

            self.search_run_id = last_run.id
            self.origem_regiao = last_run.regiao
            self.origem_segmento = last_run.segmento

        # Materializa o JSON da pesquisa em linhas (idempotente).
        ensure_companies_materialized(self.tenant_id, self.search_run_id)

        with rx.session() as session:
            empresas = (
                session.query(ProspectCompany)
                .filter(
                    ProspectCompany.tenant_id == self.tenant_id,
                    ProspectCompany.search_run_id == self.search_run_id,
                )
                .order_by(ProspectCompany.icp_score.desc())
                .all()
            )
            linhas: List[CompanyUI] = []
            for c in empresas:
                contatos = (
                    session.query(CompanyContact)
                    .filter(CompanyContact.company_id == c.id)
                    .all()
                )
                qtd = len(contatos)
                origens = {ct.origem for ct in contatos}
                if not qtd:
                    contatos_label = "-"
                elif origens == {"qsa"}:
                    contatos_label = f"{qtd}"
                elif origens == {"linkedin"}:
                    contatos_label = f"{qtd}"
                else:
                    contatos_label = f"{qtd}"
                cidade_uf = " / ".join([p for p in (c.cidade, c.estado) if p])
                linhas.append(
                    CompanyUI(
                        id=c.id,
                        nome=c.razao_social or c.nome,
                        cnpj=formatar_cnpj(c.cnpj),
                        cidade_uf=cidade_uf or c.localizacao,
                        porte=c.porte or "-",
                        status_cadastral=c.status_cadastral or "-",
                        alerta_situacao=c.alerta_situacao or "",
                        # Formata na leitura também: linhas gravadas antes da
                        # padronização continuam aparecendo no formato certo.
                        telefone=formatar_telefone(c.telefone) or "-",
                        qtd_contatos=qtd,
                        contatos_label=contatos_label,
                        percentual=c.enrichment_percentage,
                        status=c.enrichment_status,
                    )
                )
            self.companies = linhas
            self.total_empresas = len(linhas)

            last_enrich = (
                session.query(EnrichmentRun)
                .filter(
                    EnrichmentRun.tenant_id == self.tenant_id,
                    EnrichmentRun.search_run_id == self.search_run_id,
                )
                .order_by(EnrichmentRun.started_at.desc())
                .first()
            )
            if last_enrich:
                self.has_result = last_enrich.status != "running"
                self.last_status = last_enrich.status
                self.last_processadas = last_enrich.processadas
                self.last_puladas = last_enrich.puladas
                self.last_falhas = last_enrich.falhas
                self.last_custo = _brl(last_enrich.custo_total)
                self.enrichment_error = last_enrich.erro if last_enrich.status == "error" else ""
                self.is_running = last_enrich.status == "running"
                self.progress_atual = last_enrich.processadas
                self.progress_total = last_enrich.total_empresas or len(linhas)
                try:
                    self.last_avisos = json.loads(last_enrich.avisos or "[]")
                except (ValueError, TypeError):
                    self.last_avisos = []
                self.last_run_at = (
                    last_enrich.finished_at.strftime("%d/%m/%Y %H:%M")
                    if last_enrich.finished_at
                    else last_enrich.started_at.strftime("%d/%m/%Y %H:%M")
                )
            else:
                self.has_result = False
                self.last_status = ""
                self.is_running = False
                self.progress_atual = 0
                self.progress_total = len(linhas)
                self.last_avisos = []

    @rx.event(background=True)
    async def start_enrichment(self):
        """Dispara o enriquecimento. Roda em background porque são dezenas de
        chamadas HTTP com throttle de rate limit — o mesmo motivo (e o mesmo
        esqueleto) de SearchState.start_search: snapshot do state no primeiro
        lock, sessões curtas fora do lock, e reset incondicional de is_running
        no bloco final."""
        async with self:
            if self.is_running:
                yield toast_error("Já existe um enriquecimento em andamento.")
                return
            if not self.search_run_id:
                yield toast_error("Nenhuma pesquisa concluída para enriquecer.")
                return
            if self.total_empresas == 0:
                yield toast_error("Nenhuma empresa encontrada na pesquisa anterior.")
                return
            if not self.is_superadmin and self.enrichments_this_month >= self.consulta_limit:
                yield toast_error(
                    f"Limite mensal de {self.consulta_limit} consultas atingido."
                )
                return

            tenant_id = self.tenant_id
            user_email = self.user_email
            search_run_id = self.search_run_id
            limite_contatos = self.contact_limit
            total_inicial = self.total_empresas

            self.is_running = True
            self.enrichment_error = ""
            self.progress_message = "Iniciando enriquecimento..."
            self.progress_atual = 0
            self.progress_total = total_inicial
            self.has_result = False

        from sales_support_agent.models import ActivityLog, EnrichmentRun
        from sales_support_agent.services.enrichment import stream_enrichment

        with rx.session() as session:
            run = EnrichmentRun(
                tenant_id=tenant_id,
                user_email=user_email,
                search_run_id=search_run_id,
                status="running",
                total_empresas=total_inicial,
            )
            session.add(run)
            session.commit()
            session.refresh(run)
            run_id = run.id

        async for event in stream_enrichment(tenant_id, search_run_id, limite_contatos):
            kind = event[0]

            if kind == "progress":
                _, atual, total, msg = event
                with rx.session() as session:
                    r = session.get(EnrichmentRun, run_id)
                    if r:
                        r.processadas = atual
                        r.total_empresas = total
                        session.commit()
                async with self:
                    self.progress_atual = atual
                    self.progress_total = total
                    self.progress_message = msg

            elif kind == "done":
                resumo = event[1]
                with rx.session() as session:
                    r = session.get(EnrichmentRun, run_id)
                    if r:
                        r.status = "done"
                        r.processadas = resumo["processadas"]
                        r.puladas = resumo["puladas"]
                        r.falhas = resumo["falhas"]
                        r.custo_total = resumo["custo_total"]
                        r.avisos = json.dumps(resumo["avisos"], ensure_ascii=False)
                        r.finished_at = brt_now()
                        session.add(
                            ActivityLog(
                                tenant_id=tenant_id,
                                user_email=user_email,
                                action="ENRIQUECIMENTO_EXEC",
                                details=(
                                    f"Enriqueceu {resumo['processadas']} empresa(s), "
                                    f"pulou {resumo['puladas']}, custo "
                                    f"{_brl(resumo['custo_total'])}."
                                ),
                                timestamp=brt_now(),
                            )
                        )
                        session.commit()
                async with self:
                    self.has_result = True
                    self.last_status = "done"
                    self.last_processadas = resumo["processadas"]
                    self.last_puladas = resumo["puladas"]
                    self.last_falhas = resumo["falhas"]
                    self.last_custo = _brl(resumo["custo_total"])
                    self.last_avisos = resumo["avisos"]
                    self.enrichments_this_month += 1
                    yield toast_success("Enriquecimento concluído.")

            elif kind == "error":
                msg = event[1]
                with rx.session() as session:
                    r = session.get(EnrichmentRun, run_id)
                    if r:
                        r.status = "error"
                        r.erro = msg
                        r.finished_at = brt_now()
                        session.commit()
                async with self:
                    self.enrichment_error = msg
                    self.last_status = "error"
                    self.has_result = True
                    yield toast_error(msg)

        # Sempre encerra o "carregando", mesmo em erro (mesma classe de bug já
        # corrigida em generate_description e start_search).
        async with self:
            self.is_running = False
            self.progress_message = ""
        yield EnrichmentState.load_enrichment

    def set_detail_open(self, value: bool):
        self.detail_open = value

    def open_company_detail(self, company_id: int):
        """Abre o pop-up com tudo que foi coletado sobre a empresa.

        As notícias vêm da FASE 1 (`SearchRun.result_json`), não de colunas —
        ver `enrichment.noticias_por_empresa`. Como é uma empresa só, a leitura
        do JSON acontece sob demanda, no clique, e não no load da página.
        """
        if not self.is_authenticated:
            return rx.redirect("/login")

        from sales_support_agent.models import ProspectCompany

        with rx.session() as session:
            c = session.get(ProspectCompany, company_id)
            # Checagem de posse: o id vem do cliente, não se confia nele.
            if not c or c.tenant_id != self.tenant_id:
                return toast_error("Empresa não encontrada.")
            self.detail = _montar_company_detail(session, c)
        self.detail_open = True

    def export_report(self):
        """Gera o .xlsx de TODAS as empresas da pesquisa e envia para download.

        Exporta inclusive as não enriquecidas: a ausência de dado é informação
        comercial (a empresa existe, o cadastro é que não foi encontrado), e
        omiti-las faria o relatório divergir da tela.
        """
        if not self.is_authenticated:
            return rx.redirect("/login")
        if not self.search_run_id:
            return toast_error("Nenhuma pesquisa concluída para exportar.")

        from sales_support_agent.models import CompanyContact, ProspectCompany
        from sales_support_agent.services.enrichment import noticias_por_empresa
        from sales_support_agent.services.enrichment_report import (
            montar_relatorio,
            nome_do_arquivo,
        )

        with rx.session() as session:
            empresas = (
                session.query(ProspectCompany)
                .filter(
                    ProspectCompany.tenant_id == self.tenant_id,
                    ProspectCompany.search_run_id == self.search_run_id,
                )
                .order_by(ProspectCompany.icp_score.desc())
                .all()
            )
            if not empresas:
                return toast_error("Nenhuma empresa para exportar.")

            contatos: dict = {}
            for ct in (
                session.query(CompanyContact)
                .filter(CompanyContact.tenant_id == self.tenant_id)
                .order_by(CompanyContact.id)
                .all()
            ):
                contatos.setdefault(ct.company_id, []).append(ct)

            noticias = noticias_por_empresa(self.tenant_id, self.search_run_id, empresas)

            conteudo = montar_relatorio(
                empresas,
                contatos,
                noticias,
                regiao=self.origem_regiao,
                segmento=self.origem_segmento,
            )
            self.log_activity(
                "RELATORIO_ENRIQUECIMENTO",
                f"Exportou {len(empresas)} empresa(s) em .xlsx.",
                session,
            )

        arquivo = nome_do_arquivo(self.origem_regiao, self.origem_segmento, brt_now())
        return [
            rx.download(data=conteudo, filename=arquivo),
            toast_success(f"Relatório com {len(empresas)} empresa(s) gerado."),
        ]

    def advance_to_next_phase(self):
        """Botão de avançar: leva à fase 3 do funil (priorização + approach)."""
        return rx.redirect("/priorizacao")

    def set_bulk_delete_dialog_open(self, value: bool):
        self.bulk_delete_dialog_open = value

    def confirm_bulk_delete_zeradas(self):
        """Exclui as empresas SEM nenhum dado de enriquecimento (0%).

        Reduz a base antes de rodar priorização/approach. Definição confirmada
        com o usuário: só as zeradas (`enrichment_percentage == 0`) — leads
        parciais permanecem, pois já têm algum dado aproveitável.
        """
        from sales_support_agent.models import CompanyContact, ProspectCompany

        with rx.session() as session:
            zeradas = (
                session.query(ProspectCompany)
                .filter(
                    ProspectCompany.tenant_id == self.tenant_id,
                    ProspectCompany.enrichment_percentage == 0,
                )
                .all()
            )
            if not zeradas:
                self.bulk_delete_dialog_open = False
                return toast_error("Nenhuma empresa sem enriquecimento para excluir.")

            ids = [c.id for c in zeradas]
            # CompanyContact depende de ProspectCompany (FK sem CASCADE) — o
            # filho sempre antes do pai, mesmo padrão de AdminState.delete_user.
            session.query(CompanyContact).filter(CompanyContact.company_id.in_(ids)).delete(
                synchronize_session=False
            )
            for c in zeradas:
                session.delete(c)
            self.log_activity(
                "ENRIQUECIMENTO_BULK_DELETE",
                f"Excluiu {len(ids)} empresa(s) sem enriquecimento.",
                session,
            )
            session.commit()

        self.bulk_delete_dialog_open = False
        return [
            toast_success(f"{len(ids)} empresa(s) sem enriquecimento excluída(s)."),
            EnrichmentState.load_enrichment,
        ]


# ==========================================================================
# Fase 3 do funil: priorização (score 0-100 por 7 critérios) + approach
# (dicas de primeiro contato), sobre os leads já enriquecidos.
# ==========================================================================
class CriterioUI(BaseModel):
    """Um dos 7 critérios no breakdown do pop-up de detalhe."""
    criterio: str
    peso: str  # ex.: "30%", já formatado
    pontos: int
    justificativa: str


class DicaApproachUI(BaseModel):
    tipo_label: str  # "Gancho de abertura" / "Canal recomendado" / ...
    dica: str


class LeadPriorizadoUI(BaseModel):
    """Linha da tabela de /priorizacao."""
    id: int
    nome: str
    empresa: str
    score_final: int
    classe: str  # "Alta" | "Média" | "Baixa" | "" (ainda não priorizado)
    classe_cor: str  # color_scheme do badge
    executado_em: str


class LeadPriorizadoDetailUI(BaseModel):
    """Pop-up de detalhe: dados completos do lead + breakdown + approach."""
    empresa: CompanyDetailUI = CompanyDetailUI()
    score_final: int = 0
    classe: str = ""
    criterios: List[CriterioUI] = []
    tem_approach: bool = False
    dicas_approach: List[DicaApproachUI] = []


_ROTULO_TIPO_DICA = {
    "gancho": "Gancho de abertura",
    "canal": "Canal recomendado",
    "dor": "Ponto de dor provável",
    "timing": "Timing sugerido",
}


class PriorizacaoState(AppState):
    """Configuração e execução da priorização + approach (fase 3 do funil)."""

    companies: List[LeadPriorizadoUI] = []
    total_leads: int = 0

    # Pesquisa de origem (mesmo escopo de EnrichmentState: última concluída)
    search_run_id: int = 0
    origem_regiao: str = ""
    origem_segmento: str = ""

    # Cota mensal — a MESMA de pesquisa/enriquecimento (limite_consultas)
    consulta_limit: int = CONSULTA_LIMIT_MENSAL
    priorizacoes_this_month: int = 0

    # Checkbox "incluir approach", marcado por padrão
    incluir_approach: bool = True

    # Pop-up de detalhe
    detail_open: bool = False
    detail: LeadPriorizadoDetailUI = LeadPriorizadoDetailUI()

    # Geração do relatório .pdf
    is_exporting: bool = False

    # Execução em andamento (background task)
    is_running: bool = False
    progress_message: str = ""
    progress_atual: int = 0
    progress_total: int = 0
    priorizacao_error: str = ""

    # Último resultado (reidratado do banco a cada load)
    has_result: bool = False
    last_status: str = ""
    last_processados: int = 0
    last_puladas: int = 0
    last_falhas: int = 0
    last_avisos: List[str] = []
    last_run_at: str = ""

    @rx.var
    def sem_leads(self) -> bool:
        return self.total_leads == 0

    @rx.var
    def quota_reached(self) -> bool:
        return not self.is_superadmin and self.priorizacoes_this_month >= self.consulta_limit

    def set_detail_open(self, value: bool):
        self.detail_open = value

    def set_incluir_approach(self, value: bool):
        self.incluir_approach = value

    def load_priorizacao(self):
        """on_load de /priorizacao: mesmo gate de autenticação de
        EnrichmentState.load_enrichment. Opera sobre a última pesquisa
        CONCLUÍDA (mesmo escopo do enriquecimento), só sobre leads já
        enriquecidos (`enrichment_status in completed/partial`)."""
        if not self.is_authenticated:
            return rx.redirect("/login")

        from sales_support_agent.models import PriorizacaoRun, ProspectCompany, SearchRun
        from sales_support_agent.services.enrichment_rules import STATUS_CONSIDERADOS_ENRIQUECIDOS
        from sales_support_agent.services.priorizacao_rules import cor_classe_prioridade

        with rx.session() as session:
            # Cota mensal por usuário, idêntica às demais etapas do funil.
            self.consulta_limit = limite_consultas(self.is_superadmin)
            month_start = brt_now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            self.priorizacoes_this_month = (
                session.query(PriorizacaoRun)
                .filter(
                    PriorizacaoRun.user_email == self.user_email,
                    PriorizacaoRun.started_at >= month_start,
                    PriorizacaoRun.status != "error",
                )
                .count()
            )

            last_run = (
                session.query(SearchRun)
                .filter(SearchRun.tenant_id == self.tenant_id, SearchRun.status == "done")
                .order_by(SearchRun.started_at.desc())
                .first()
            )
            if not last_run:
                self.search_run_id = 0
                self.companies = []
                self.total_leads = 0
                self.origem_regiao = ""
                self.origem_segmento = ""
                self.has_result = False
                self.is_running = False
                return

            self.search_run_id = last_run.id
            self.origem_regiao = last_run.regiao
            self.origem_segmento = last_run.segmento

            empresas = (
                session.query(ProspectCompany)
                .filter(
                    ProspectCompany.tenant_id == self.tenant_id,
                    ProspectCompany.search_run_id == self.search_run_id,
                    ProspectCompany.enrichment_status.in_(STATUS_CONSIDERADOS_ENRIQUECIDOS),
                )
                .order_by(ProspectCompany.icp_score.desc())
                .all()
            )
            linhas: List[LeadPriorizadoUI] = []
            for c in empresas:
                linhas.append(
                    LeadPriorizadoUI(
                        id=c.id,
                        nome=c.nome,
                        empresa=c.razao_social or c.nome,
                        score_final=c.priorizacao_score_final or 0,
                        classe=c.priorizacao_classe or "",
                        classe_cor=cor_classe_prioridade(c.priorizacao_classe or ""),
                        executado_em=(
                            c.priorizacao_executado_em.strftime("%d/%m/%Y %H:%M")
                            if c.priorizacao_executado_em else ""
                        ),
                    )
                )
            self.companies = linhas
            self.total_leads = len(linhas)

            last_pri = (
                session.query(PriorizacaoRun)
                .filter(
                    PriorizacaoRun.tenant_id == self.tenant_id,
                    PriorizacaoRun.search_run_id == self.search_run_id,
                )
                .order_by(PriorizacaoRun.started_at.desc())
                .first()
            )
            if last_pri:
                # Uma execução "running" há muito tempo travou (processo
                # morto/crash antes de gravar o resultado) — sem isso a tela
                # ficava mostrando "priorizando..." para sempre, mesmo depois
                # do bug que causava o travamento já ter sido corrigido,
                # porque o registro antigo no banco nunca era atualizado.
                LIMITE_TRAVADO_MINUTOS = 20
                travado = (
                    last_pri.status == "running"
                    and (brt_now() - last_pri.started_at).total_seconds() > LIMITE_TRAVADO_MINUTOS * 60
                )
                if travado:
                    last_pri.status = "error"
                    last_pri.erro = "Execução anterior não finalizou (processo interrompido). Tente priorizar novamente."
                    last_pri.finished_at = brt_now()
                    session.commit()

                self.has_result = last_pri.status != "running"
                self.last_status = last_pri.status
                self.last_processados = last_pri.processados
                self.last_puladas = last_pri.puladas
                self.last_falhas = last_pri.falhas
                self.priorizacao_error = last_pri.erro if last_pri.status == "error" else ""
                self.is_running = last_pri.status == "running"
                self.progress_atual = last_pri.processados
                self.progress_total = last_pri.total_leads or len(linhas)
                try:
                    self.last_avisos = json.loads(last_pri.avisos or "[]")
                except (ValueError, TypeError):
                    self.last_avisos = []
                self.last_run_at = (
                    last_pri.finished_at.strftime("%d/%m/%Y %H:%M")
                    if last_pri.finished_at else last_pri.started_at.strftime("%d/%m/%Y %H:%M")
                )
            else:
                self.has_result = False
                self.last_status = ""
                self.is_running = False
                self.progress_atual = 0
                self.progress_total = len(linhas)
                self.last_avisos = []

    @rx.event(background=True)
    async def start_priorizacao(self):
        """Prioriza (e opcionalmente aplica approach a) todos os leads
        elegíveis pendentes da última pesquisa. Mesmo esqueleto de lock de
        EnrichmentState.start_enrichment."""
        async with self:
            if self.is_running:
                yield toast_error("Já existe uma priorização em andamento.")
                return
            if not self.search_run_id:
                yield toast_error("Nenhuma pesquisa concluída para priorizar.")
                return
            if self.total_leads == 0:
                yield toast_error("Nenhum lead enriquecido para priorizar.")
                return
            if not self.is_superadmin and self.priorizacoes_this_month >= self.consulta_limit:
                yield toast_error(
                    f"Limite mensal de {self.consulta_limit} consultas atingido."
                )
                return

            tenant_id = self.tenant_id
            user_email = self.user_email
            search_run_id = self.search_run_id
            incluir_approach = self.incluir_approach
            total_inicial = self.total_leads

            self.is_running = True
            self.priorizacao_error = ""
            self.progress_message = "Iniciando priorização..."
            self.progress_atual = 0
            self.progress_total = total_inicial
            self.has_result = False

        try:
            async for event in self._executar_priorizacao(
                tenant_id, user_email, search_run_id, incluir_approach, None, total_inicial
            ):
                yield event
        except Exception as e:
            async with self:
                self.priorizacao_error = str(e)
                self.last_status = "error"
                self.has_result = True
                yield toast_error(f"Falha inesperada na priorização: {e}")
        finally:
            # Sempre encerra o "carregando", mesmo em erro inesperado (mesma
            # classe de bug já corrigida em EnrichmentState.start_enrichment
            # e ProductState.generate_description).
            async with self:
                self.is_running = False
                self.progress_message = ""
        yield PriorizacaoState.load_priorizacao

    @rx.event(background=True)
    async def start_priorizacao_individual(self, company_id: int):
        """Prioriza (e opcionalmente aplica approach a) um único lead."""
        async with self:
            if self.is_running:
                yield toast_error("Já existe uma priorização em andamento.")
                return
            tenant_id = self.tenant_id
            user_email = self.user_email
            search_run_id = self.search_run_id
            incluir_approach = self.incluir_approach
            self.is_running = True
            self.priorizacao_error = ""
            self.progress_message = "Priorizando lead..."
            self.progress_atual = 0
            self.progress_total = 1
            self.has_result = False

        from sales_support_agent.models import ProspectCompany

        with rx.session() as session:
            c = session.get(ProspectCompany, company_id)
            # Checagem de posse: o id vem do cliente, não se confia nele.
            if not c or c.tenant_id != tenant_id:
                async with self:
                    self.is_running = False
                    self.progress_message = ""
                    yield toast_error("Lead não encontrado.")
                return

        try:
            async for event in self._executar_priorizacao(
                tenant_id, user_email, search_run_id, incluir_approach, [company_id], 1
            ):
                yield event
        except Exception as e:
            async with self:
                self.priorizacao_error = str(e)
                self.last_status = "error"
                self.has_result = True
                yield toast_error(f"Falha inesperada na priorização: {e}")
        finally:
            async with self:
                self.is_running = False
                self.progress_message = ""
        yield PriorizacaoState.load_priorizacao

    async def _executar_priorizacao(
        self, tenant_id, user_email, search_run_id, incluir_approach, company_ids, total_inicial,
    ):
        """Corpo comum das duas execuções (lote/individual): cria o
        PriorizacaoRun, consome stream_priorizacao e persiste progresso/
        resultado. Não é um event handler — é chamado de dentro de um."""
        from sales_support_agent.models import ActivityLog, PriorizacaoRun, TokenUsage
        from sales_support_agent.services.priorizacao import stream_priorizacao

        with rx.session() as session:
            run = PriorizacaoRun(
                tenant_id=tenant_id,
                user_email=user_email,
                search_run_id=search_run_id,
                status="running",
                incluiu_approach=incluir_approach,
                total_leads=total_inicial,
            )
            session.add(run)
            session.commit()
            session.refresh(run)
            run_id = run.id

        async for event in stream_priorizacao(tenant_id, search_run_id, incluir_approach, company_ids):
            kind = event[0]

            if kind == "progress":
                _, atual, total, msg = event
                with rx.session() as session:
                    r = session.get(PriorizacaoRun, run_id)
                    if r:
                        r.processados = atual
                        r.total_leads = total
                        session.commit()
                async with self:
                    self.progress_atual = atual
                    self.progress_total = total
                    self.progress_message = msg

            elif kind == "done":
                resumo = event[1]
                up = resumo["usage_priorizacao"]
                ua = resumo["usage_approach"]
                with rx.session() as session:
                    r = session.get(PriorizacaoRun, run_id)
                    if r:
                        r.status = "done"
                        r.processados = resumo["processados"]
                        r.puladas = resumo["puladas"]
                        r.falhas = resumo["falhas"]
                        r.avisos = json.dumps(resumo["avisos"], ensure_ascii=False)
                        r.finished_at = brt_now()
                        if up["input"] or up["output"]:
                            session.add(TokenUsage(
                                tenant_id=tenant_id, agent_name="priorizacao_agent",
                                model=modelo_do_agente("priorizacao_agent"),
                                input_tokens=up["input"], output_tokens=up["output"],
                            ))
                        if ua["input"] or ua["output"]:
                            session.add(TokenUsage(
                                tenant_id=tenant_id, agent_name="approach_agent",
                                model=modelo_do_agente("approach_agent"),
                                input_tokens=ua["input"], output_tokens=ua["output"],
                            ))
                        session.add(ActivityLog(
                            tenant_id=tenant_id, user_email=user_email,
                            action="PRIORIZACAO_EXEC",
                            details=(
                                f"Priorizou {resumo['processados']} lead(s), "
                                f"pulou {resumo['puladas']}, {resumo['falhas']} falha(s)."
                            ),
                            timestamp=brt_now(),
                        ))
                        session.commit()
                async with self:
                    self.has_result = True
                    self.last_status = "done"
                    self.last_processados = resumo["processados"]
                    self.last_puladas = resumo["puladas"]
                    self.last_falhas = resumo["falhas"]
                    self.last_avisos = resumo["avisos"]
                    self.priorizacoes_this_month += 1
                    yield toast_success("Priorização concluída.")

            elif kind == "error":
                msg = event[1]
                with rx.session() as session:
                    r = session.get(PriorizacaoRun, run_id)
                    if r:
                        r.status = "error"
                        r.erro = msg
                        r.finished_at = brt_now()
                        session.commit()
                async with self:
                    self.priorizacao_error = msg
                    self.last_status = "error"
                    self.has_result = True
                    yield toast_error(msg)

    def open_lead_detail(self, company_id: int):
        """Abre o pop-up com os dados completos do lead + breakdown dos 7
        critérios + dicas de approach (quando existirem)."""
        if not self.is_authenticated:
            return rx.redirect("/login")

        from sales_support_agent.models import ProspectCompany
        from sales_support_agent.services.priorizacao_rules import PESOS_CRITERIOS

        with rx.session() as session:
            c = session.get(ProspectCompany, company_id)
            # Checagem de posse: o id vem do cliente, não se confia nele.
            if not c or c.tenant_id != self.tenant_id:
                return toast_error("Lead não encontrado.")

            empresa_detail = _montar_company_detail(session, c)

            criterios_ui: List[CriterioUI] = []
            if c.priorizacao_criterios:
                try:
                    criterios = json.loads(c.priorizacao_criterios)
                except (ValueError, TypeError):
                    criterios = []
                for item in criterios:
                    nome = item.get("criterio", "")
                    peso = PESOS_CRITERIOS.get(nome)
                    criterios_ui.append(CriterioUI(
                        criterio=nome,
                        peso=f"{peso:g}%" if peso is not None else "",
                        pontos=item.get("pontos", 0),
                        justificativa=item.get("justificativa", ""),
                    ))

            dicas_ui: List[DicaApproachUI] = []
            tem_approach = c.approach_status == "done" and bool(c.approach_dicas)
            if tem_approach:
                try:
                    dicas = json.loads(c.approach_dicas)
                except (ValueError, TypeError):
                    dicas = []
                for d in dicas:
                    tipo = d.get("tipo", "")
                    dicas_ui.append(DicaApproachUI(
                        tipo_label=_ROTULO_TIPO_DICA.get(tipo, tipo.title() or "Dica"),
                        dica=d.get("dica", ""),
                    ))

            self.detail = LeadPriorizadoDetailUI(
                empresa=empresa_detail,
                score_final=c.priorizacao_score_final or 0,
                classe=c.priorizacao_classe or "",
                criterios=criterios_ui,
                tem_approach=tem_approach,
                dicas_approach=dicas_ui,
            )
        self.detail_open = True

    def export_report(self):
        """Gera o .pdf de todos os leads priorizados e envia para download."""
        if not self.is_authenticated:
            return rx.redirect("/login")
        if not self.search_run_id:
            return toast_error("Nenhuma pesquisa concluída para exportar.")

        from sales_support_agent.models import ProspectCompany
        from sales_support_agent.services.priorizacao_report import montar_relatorio, nome_do_arquivo

        with rx.session() as session:
            leads = (
                session.query(ProspectCompany)
                .filter(
                    ProspectCompany.tenant_id == self.tenant_id,
                    ProspectCompany.search_run_id == self.search_run_id,
                    ProspectCompany.priorizacao_status == "done",
                )
                .order_by(ProspectCompany.priorizacao_score_final.desc())
                .all()
            )
            if not leads:
                return toast_error("Nenhum lead priorizado para exportar.")

            criterios_por_lead: dict = {}
            dicas_por_lead: dict = {}
            for lead in leads:
                try:
                    criterios_por_lead[lead.id] = json.loads(lead.priorizacao_criterios or "[]")
                except (ValueError, TypeError):
                    criterios_por_lead[lead.id] = []
                if lead.approach_status == "done" and lead.approach_dicas:
                    try:
                        dicas_por_lead[lead.id] = json.loads(lead.approach_dicas)
                    except (ValueError, TypeError):
                        dicas_por_lead[lead.id] = []

            conteudo = montar_relatorio(
                leads, criterios_por_lead, dicas_por_lead,
                regiao=self.origem_regiao, segmento=self.origem_segmento,
            )
            self.log_activity(
                "RELATORIO_PRIORIZACAO",
                f"Exportou {len(leads)} lead(s) priorizado(s) em .pdf.",
                session,
            )

        arquivo = nome_do_arquivo(self.origem_regiao, self.origem_segmento, brt_now())
        return [
            rx.download(data=conteudo, filename=arquivo),
            toast_success(f"Relatório com {len(leads)} lead(s) gerado."),
        ]


# ==========================================================================
# "Listas e Leads": repositório central de todos os leads já enriquecidos.
# Estado deliberadamente magro (sem is_running/cota/progresso, que não fazem
# sentido numa tela de navegação/exclusão).
# ==========================================================================
class LeadUI(BaseModel):
    """Linha da tabela de /leads."""
    id: int
    nome: str
    empresa: str
    cidade_uf: str
    enrichment_status_label: str
    priorizacao_classe: str


_ENRICHMENT_STATUS_PT = {
    "completed": "Completo",
    "partial": "Parcial",
    "failed": "Falhou",
    "pending": "Incompleto",
    "in_progress": "Em andamento",
}


class LeadsState(AppState):
    """Repositório central de todos os leads (empresas) do tenant."""

    leads: List[LeadUI] = []
    total_leads: int = 0
    produto_options: List[str] = ["Todos"]
    produto_filter: str = "Todos"

    # Filtro por quem coletou o lead. Os rótulos são nomes (o que o usuário
    # reconhece) e `_usuarios_por_nome` traduz de volta para o e-mail, que é o
    # que está gravado em ProspectCompany.user_email.
    usuario_options: List[str] = ["Todos"]
    usuario_filter: str = "Todos"
    _usuarios_por_nome: Dict[str, str] = {}

    detail_open: bool = False
    detail: CompanyDetailUI = CompanyDetailUI()

    delete_dialog_open: bool = False
    lead_to_delete: int = 0

    bulk_delete_contatos_dialog_open: bool = False

    @rx.var
    def sem_leads(self) -> bool:
        return self.total_leads == 0

    def set_detail_open(self, value: bool):
        self.detail_open = value

    def set_delete_dialog_open(self, value: bool):
        self.delete_dialog_open = value

    def set_bulk_delete_contatos_dialog_open(self, value: bool):
        self.bulk_delete_contatos_dialog_open = value

    def set_produto_filter(self, value: str):
        self.produto_filter = value
        self._carregar_leads()

    def set_usuario_filter(self, value: str):
        self.usuario_filter = value
        self._carregar_leads()

    def load_leads(self):
        """on_load de /leads: mesmo gate de autenticação das demais páginas do
        funil. Cross-run de propósito — é o repositório central, não uma
        etapa específica."""
        if not self.is_authenticated:
            return rx.redirect("/login")

        from sales_support_agent.services import dashboard_insights as di

        self.produto_options = ["Todos"] + di.produtos_pesquisados(self.tenant_id)
        if self.produto_filter not in self.produto_options:
            self.produto_filter = "Todos"

        # Todos os usuários da organização entram na lista, mesmo os que ainda
        # não coletaram nada — assim o filtro não muda de tamanho a cada
        # pesquisa nova e é possível confirmar que alguém está sem leads.
        with rx.session() as session:
            usuarios = (
                session.query(User)
                .filter(User.tenant_id == self.tenant_id)
                .order_by(User.name)
                .all()
            )
        self._usuarios_por_nome = {u.name: u.email for u in usuarios}
        self.usuario_options = ["Todos"] + list(self._usuarios_por_nome.keys())
        if self.usuario_filter not in self.usuario_options:
            self.usuario_filter = "Todos"

        self._carregar_leads()

    def _carregar_leads(self):
        """Recarrega a lista respeitando `produto_filter` ("Todos" -> sem
        filtro). Extraído de `load_leads` para poder ser chamado sozinho
        quando o filtro muda, sem reconsultar `produto_options`."""
        from sales_support_agent.services import dashboard_insights as di

        produto = None if self.produto_filter == "Todos" else self.produto_filter
        coletor = (
            None
            if self.usuario_filter == "Todos"
            else self._usuarios_por_nome.get(self.usuario_filter)
        )
        empresas = di.listar_empresas(self.tenant_id, produto=produto, user_email=coletor)

        # Maior potencial primeiro: score de priorização quando existir,
        # senão o icp_score da pesquisa (mesmo critério do Top leads do
        # dashboard) — ordenado em Python pelo mesmo motivo de portabilidade
        # sqlite/postgres já documentado em `_monthly_series`.
        empresas.sort(
            key=lambda c: c.priorizacao_score_final if c.priorizacao_score_final is not None else (c.icp_score or 0),
            reverse=True,
        )
        linhas = [
            LeadUI(
                id=c.id,
                nome=c.nome,
                empresa=c.razao_social or c.nome,
                cidade_uf=" / ".join([p for p in (c.cidade, c.estado) if p]) or c.localizacao,
                enrichment_status_label=_ENRICHMENT_STATUS_PT.get(
                    c.enrichment_status, c.enrichment_status
                ),
                priorizacao_classe=c.priorizacao_classe or "-",
            )
            for c in empresas
        ]
        self.leads = linhas
        self.total_leads = len(linhas)

    def open_lead_detail_view(self, lead_id: int):
        if not self.is_authenticated:
            return rx.redirect("/login")

        from sales_support_agent.models import ProspectCompany

        with rx.session() as session:
            c = session.get(ProspectCompany, lead_id)
            # Checagem de posse: o id vem do cliente, não se confia nele.
            if not c or c.tenant_id != self.tenant_id:
                return toast_error("Lead não encontrado.")
            self.detail = _montar_company_detail(session, c)
        self.detail_open = True

    def ask_delete_lead(self, lead_id: int):
        self.lead_to_delete = lead_id
        self.delete_dialog_open = True

    def confirm_delete_lead(self):
        """Exclui um lead individual (e seus contatos) após confirmação."""
        from sales_support_agent.models import CompanyContact, ProspectCompany

        with rx.session() as session:
            c = session.get(ProspectCompany, self.lead_to_delete)
            # Checagem de posse: o id vem do cliente, não se confia nele.
            if not c or c.tenant_id != self.tenant_id:
                self.delete_dialog_open = False
                return toast_error("Lead não encontrado.")
            nome = c.nome
            session.query(CompanyContact).filter(CompanyContact.company_id == c.id).delete(
                synchronize_session=False
            )
            session.delete(c)
            self.log_activity("LEAD_DELETE", f"Excluiu o lead '{nome}'.", session)
            session.commit()

        self.delete_dialog_open = False
        self.lead_to_delete = 0
        return [toast_success("Lead excluído."), LeadsState.load_leads]

    def confirm_bulk_delete_contatos(self):
        """Apaga TODOS os contatos decisores (CompanyContact) do tenant, em
        todas as empresas — as empresas em si permanecem. Recalcula o
        percentual/status de enriquecimento de cada empresa afetada (dois dos
        12 critérios dependem dos contatos: "tem >=1 contato" e "tem >=1
        e-mail de contato"; sem recalcular, o percentual persistido mentiria
        sobre dados que acabaram de ser apagados)."""
        from sales_support_agent.models import CompanyContact, ProspectCompany
        from sales_support_agent.services.enrichment_rules import calcular_percentual, definir_status

        with rx.session() as session:
            empresas = (
                session.query(ProspectCompany)
                .filter(ProspectCompany.tenant_id == self.tenant_id)
                .all()
            )
            total_contatos = 0
            for c in empresas:
                qtd = (
                    session.query(CompanyContact)
                    .filter(CompanyContact.company_id == c.id)
                    .count()
                )
                if qtd == 0:
                    continue
                total_contatos += qtd
                session.query(CompanyContact).filter(CompanyContact.company_id == c.id).delete(
                    synchronize_session=False
                )
                # Os contatos acabaram de ir embora, e o e-mail vivia neles.
                c.enrichment_percentage = calcular_percentual(c, qtd_contatos=0, qtd_emails=0)
                c.enrichment_status = definir_status(c.enrichment_percentage)
                session.add(c)

            self.log_activity(
                "CONTATOS_BULK_DELETE",
                f"Excluiu {total_contatos} contato(s) decisor(es) de {len(empresas)} empresa(s).",
                session,
            )
            session.commit()

        self.bulk_delete_contatos_dialog_open = False
        return [toast_success(f"{total_contatos} contato(s) excluído(s)."), LeadsState.load_leads]


# ==========================================================================
# Dashboard de monitoramento (usuário comum) — /dashboard.
# KPIs + gráficos cross-pesquisa, lidos de services/dashboard_insights.py
# (mesmo módulo reaproveitado pelas tools do agente de Insights IA).
# ==========================================================================
_PALETA_DONUT = ["#1e5a96", "#7c3aed", "#b45309", "#0f766e", "#be185d", "#4338ca", "#6b7280"]

# Todos os 27 UFs, para o mapa sempre desenhar o país inteiro mesmo quando um
# estado não tem nenhum lead ainda (fica cinza, ver `cor_por_intensidade`).
_TODAS_UFS = (
    "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS",
    "MT", "PA", "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC",
    "SE", "SP", "TO",
)


class TopLeadUI(BaseModel):
    """Linha da tabela Top 10 de maior potencial."""
    id: int
    nome: str
    segmento: str
    porte: str
    score: int
    score_label: str  # "Priorizado" | "Estimado (ICP)"
    classe: str
    classe_cor: str
    status_enriquecimento: str


def _donut(itens: List[Dict[str, object]]) -> List[Dict[str, object]]:
    """Recharts espera `data=` como `List[dict]` puro (mesmo padrão de
    `_monthly_series`) — por isso os gráficos donut usam dict, não um
    BaseModel, ao contrário das demais view-models do projeto."""
    return [
        {**item, "cor": _PALETA_DONUT[i % len(_PALETA_DONUT)]}
        for i, item in enumerate(itens)
    ]


class DashboardState(AppState):
    """KPIs e gráficos do dashboard de monitoramento (usuário comum)."""

    sem_dados: bool = True

    kpi_leads_encontrados: int = 0
    kpi_leads_enriquecidos: int = 0
    kpi_score_icp: int = 0

    chart_segmento: List[Dict[str, Any]] = []
    chart_faturamento: List[Dict[str, Any]] = []
    chart_porte: List[Dict[str, Any]] = []
    chart_situacao: List[Dict[str, Any]] = []
    chart_contatos_cargo: List[Dict[str, Any]] = []

    cores_por_estado: Dict[str, str] = {}
    contagem_por_estado: Dict[str, int] = {}

    top_leads: List[TopLeadUI] = []
    top_leads_limit: str = "10"  # "5" | "10" | "15" | "todos"
    produto_options: List[str] = ["Todos"]
    produto_filter: str = "Todos"

    detail_open: bool = False
    detail: CompanyDetailUI = CompanyDetailUI()

    def set_detail_open(self, value: bool):
        self.detail_open = value

    def set_top_leads_limit(self, value: str):
        """Refiltra a tabela Top leads na hora, sem recarregar o resto do
        dashboard (KPIs/gráficos/mapa continuam os mesmos)."""
        self.top_leads_limit = value
        self._carregar_top_leads()

    def set_produto_filter(self, value: str):
        self.produto_filter = value
        self._carregar_top_leads()

    def open_lead_detail(self, lead_id: int):
        from sales_support_agent.models import ProspectCompany

        with rx.session() as session:
            c = session.get(ProspectCompany, lead_id)
            # Checagem de posse: o id vem do cliente, não se confia nele.
            if not c or c.tenant_id != self.tenant_id:
                return toast_error("Lead não encontrado.")
            self.detail = _montar_company_detail(session, c)
        self.detail_open = True

    def load_dashboard_data(self):
        """on_load de /dashboard (junto com AppState.load_dashboard, que cuida
        do gate de pagamento) — carrega os KPIs/gráficos cross-pesquisa."""
        if not self.is_authenticated:
            return

        from sales_support_agent.services import dashboard_insights as di
        from sales_support_agent.services.enrichment_rules import formatar_telefone  # noqa: F401 (reservado p/ uso futuro no detalhe)
        from sales_support_agent.services.enrichment_report import status_em_pt
        from sales_support_agent.services.priorizacao_rules import cor_classe_prioridade

        kpis = di.carregar_kpis(self.tenant_id)
        self.kpi_leads_encontrados = kpis["leads_encontrados"]
        self.kpi_leads_enriquecidos = kpis["leads_enriquecidos"]
        self.kpi_score_icp = kpis["score_medio_icp"]
        self.sem_dados = self.kpi_leads_encontrados == 0

        self.chart_segmento = _donut(di.distribuicao_por_segmento(self.tenant_id))
        self.chart_faturamento = _donut(di.distribuicao_por_faturamento(self.tenant_id))
        self.chart_porte = _donut(di.distribuicao_por_porte(self.tenant_id))
        self.chart_situacao = _donut(di.distribuicao_por_situacao_cadastral(self.tenant_id))
        self.chart_contatos_cargo = _donut(di.distribuicao_por_cargo_contato(self.tenant_id))

        por_estado = di.leads_por_estado(self.tenant_id)
        maximo = max(por_estado.values()) if por_estado else 0
        self.cores_por_estado = {
            uf: di.cor_por_intensidade(por_estado.get(uf, 0), maximo) for uf in _TODAS_UFS
        }
        self.contagem_por_estado = {uf: por_estado.get(uf, 0) for uf in _TODAS_UFS}

        self.produto_options = ["Todos"] + di.produtos_pesquisados(self.tenant_id)
        if self.produto_filter not in self.produto_options:
            self.produto_filter = "Todos"

        self._carregar_top_leads()

    def _carregar_top_leads(self):
        """Recarrega só a tabela Top leads, respeitando `top_leads_limit`
        ("5"/"10"/"15"/"todos" -> None) e `produto_filter` ("Todos" -> sem
        filtro). Extraído de `load_dashboard_data` para poder ser chamado
        sozinho quando um dos dois filtros muda, sem refazer KPIs/gráficos/
        mapa."""
        from sales_support_agent.services import dashboard_insights as di
        from sales_support_agent.services.enrichment_report import status_em_pt
        from sales_support_agent.services.priorizacao_rules import cor_classe_prioridade

        limite = None if self.top_leads_limit == "todos" else int(self.top_leads_limit)
        produto = None if self.produto_filter == "Todos" else self.produto_filter

        linhas: List[TopLeadUI] = []
        for c in di.top_leads_por_potencial(self.tenant_id, limite=limite, produto=produto):
            priorizado = c.priorizacao_score_final is not None
            score = c.priorizacao_score_final if priorizado else (c.icp_score or 0)
            linhas.append(TopLeadUI(
                id=c.id,
                nome=c.razao_social or c.nome,
                segmento=c.segmento or c.segmento_identificado or "-",
                porte=c.porte or "-",
                score=score,
                score_label="Priorizado" if priorizado else "Estimado (ICP)",
                classe=c.priorizacao_classe or "-",
                classe_cor=cor_classe_prioridade(c.priorizacao_classe or ""),
                status_enriquecimento=status_em_pt(c.enrichment_status),
            ))
        self.top_leads = linhas


# ==========================================================================
# Insights IA (/insights-ia): chat sobre os dados já coletados.
# Sem cota mensal (não é extração/enriquecimento novo, só análise do que já
# existe) — uso registrado em TokenUsage normalmente, só para o God Mode.
# ==========================================================================
class ChatMessageUI(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class InsightsState(AppState):
    """Chat com o agente de Insights IA — histórico, envio, streaming e limpeza."""

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

    def load_insights(self):
        """on_load de /insights-ia: mesmo gate de autenticação das demais páginas."""
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
        streaming chega (ver `services.insights_agent.stream_resposta` sobre
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
        from sales_support_agent.services.insights_agent import stream_resposta

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
                if usage["input"] or usage["output"]:
                    with rx.session() as session:
                        session.add(TokenUsage(
                            tenant_id=tenant_id, agent_name="insights_agent",
                            model=modelo_do_agente("insights_agent"),
                            input_tokens=usage["input"], output_tokens=usage["output"],
                        ))
                        session.commit()
                async with self:
                    self.messages = self.messages[:-1] + [
                        ChatMessageUI(role="assistant", content=texto)
                    ]
                yield rx.scroll_to("chat-anchor", align_to_top=False)

            elif kind == "error":
                _, msg = event
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
    # Custo com a API KipFlow (enriquecimento).
    kipflow_cost_total: float = 0.0
    kipflow_cost_label: str = "R$ 0,00"
    kipflow_cost_month_label: str = "R$ 0,00"

    # Créditos da Hunter (busca de e-mail dos contatos). Aqui a unidade é
    # CRÉDITO, não dinheiro: o Hunter cobra um pacote por ciclo, e o que importa
    # operacionalmente é quanto do pacote já foi gasto no ciclo corrente.
    hunter_creditos_label: str = "0"
    hunter_creditos_mes_label: str = "0 de 50"
    hunter_taxa_acerto_label: str = "sem buscas ainda"
    # O ciclo segue o aniversário da assinatura no Hunter, não o mês civil.
    hunter_renovacao_label: str = ""
    monthly_hunter_creditos: List[dict] = []

    # Séries mensais para os gráficos (entrada e saída são indicadores separados).
    monthly_input_tokens: List[dict] = []
    monthly_output_tokens: List[dict] = []
    monthly_input_cost: List[dict] = []
    monthly_output_cost: List[dict] = []
    monthly_kipflow_cost: List[dict] = []

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

        from sales_support_agent.models import HunterUsage, KipflowUsage, TokenUsage
        from sales_support_agent.services import hunter_client
        from sales_support_agent.services.settings import (
            get_hunter_creditos_totais,
            get_hunter_dia_renovacao,
            slots_hunter_configurados,
        )
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

            # Custo da API KipFlow (enriquecimento): o valor vem do campo `cost`
            # de cada resposta da API — é despesa real com terceiro.
            kip_rows = session.query(KipflowUsage).all()
            self.kipflow_cost_total = round(sum(k.cost for k in kip_rows), 2)
            self.kipflow_cost_label = _brl(self.kipflow_cost_total)
            self.monthly_kipflow_cost = _monthly_series(kip_rows, lambda k: k.cost)

            month_start = brt_now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            self.kipflow_cost_month_label = _brl(
                round(sum(k.cost for k in kip_rows if k.created_at >= month_start), 2)
            )

            # Créditos da Hunter. O acumulado soma `creditos` (não linhas):
            # busca sem resultado não consome crédito, então contar tentativas
            # inflaria o número e não bateria com a fatura do Hunter.
            hunter_rows = session.query(HunterUsage).all()
            self.hunter_creditos_label = str(sum(h.creditos for h in hunter_rows))
            self.monthly_hunter_creditos = _monthly_series(hunter_rows, lambda h: h.creditos)

            # A janela é o CICLO do Hunter (aniversário da assinatura), não o
            # mês civil usado nos demais cards — é a mesma janela que o gate de
            # cota conta, senão o painel diria uma coisa e o bloqueio outra.
            #
            # O teto é a SOMA das contas configuradas (o Hunter vende créditos
            # por conta, e a plataforma balanceia as buscas entre elas), não o
            # limite de uma só.
            dia_renovacao = get_hunter_dia_renovacao()
            inicio_ciclo = hunter_client.inicio_do_ciclo(dia_renovacao)
            usados_ciclo = sum(h.creditos for h in hunter_rows if h.created_at >= inicio_ciclo)
            limite_ciclo = get_hunter_creditos_totais()
            qtd_contas = len(slots_hunter_configurados())
            self.hunter_creditos_mes_label = f"{usados_ciclo} de {limite_ciclo}"
            self.hunter_renovacao_label = (
                f"{qtd_contas} conta(s) · renova em "
                f"{hunter_client.proxima_renovacao(dia_renovacao):%d/%m/%Y}"
            )

            # Taxa de acerto: quantas tentativas devolveram e-mail. É o número
            # que diz se vale a pena manter a etapa ligada — uma taxa baixa
            # significa domínios que a Hunter não cobre, não erro da plataforma.
            if hunter_rows:
                achados = sum(1 for h in hunter_rows if h.encontrado)
                self.hunter_taxa_acerto_label = (
                    f"{achados} de {len(hunter_rows)} buscas encontraram e-mail"
                )
            else:
                self.hunter_taxa_acerto_label = "sem buscas ainda"

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
        """Zera os contadores de consumo: tokens de IA e custo da KipFlow.

        NÃO toca em usuários, leads nem no feed de auditoria — é só o histórico
        de consumo que alimenta os cards e gráficos de custo.

        `HunterUsage` fica DE FORA de propósito, embora também seja um contador
        de consumo: ele não é só um indicador, é o gate da cota do ciclo
        (`services/hunter_client` soma os créditos do ciclo POR CONTA para
        decidir em qual delas cabe a próxima busca). Apagá-lo faria a plataforma
        acreditar que todas as contas estão zeradas e continuar buscando e-mails
        até a própria Hunter recusar — o oposto do controle que o botão sugere."""
        if not self.is_superadmin:
            return
        from sales_support_agent.models import KipflowUsage, TokenUsage

        with rx.session() as session:
            tokens = session.query(TokenUsage).delete()
            kipflow = session.query(KipflowUsage).delete()
            session.commit()
            self.log_activity(
                "CONTADORES_LIMPOS",
                f"{tokens} registro(s) de tokens e {kipflow} de custo KipFlow removidos.",
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
    """Configuração de agentes de IA (modelo + reasoning effort) e integrações
    de conta (e-mail via Microsoft Graph e KipFlow) — superadmin-only.

    Classe própria (não incha AdminState), mesmo motivo de DashboardState/
    InsightsState serem separadas de AppState — mas sua UI é renderizada
    DENTRO da página /admin já existente (pedido explícito: sem rota nova), e
    seu `load_settings` entra na mesma lista de `on_load` da rota `/admin`
    junto com `AdminState.load_dashboard`.

    Campos de segredo (`*_input`) NUNCA são populados com o valor
    decriptografado no load — só o `*_configurado` (bool) é lido, para mostrar
    "configurado"/"não configurado". Salvar com o campo em branco mantém o
    valor atual (não apaga) — só grava um segredo novo quando o super admin
    efetivamente digitar algo.
    """

    # --- Agentes de IA (4 pares modelo/effort) ---
    product_model: str = ""
    product_effort: str = ""
    prospect_model: str = ""
    prospect_effort: str = ""
    priorizacao_model: str = ""
    priorizacao_effort: str = ""
    insights_model: str = ""
    insights_effort: str = ""

    # --- E-mail (Microsoft Graph API) ---
    # `graph_tenant_id` é o Directory (tenant) ID do Entra ID — não confundir
    # com o `tenant_id` da aplicação, herdado do AppState.
    graph_sender_email: str = ""
    graph_tenant_id: str = ""
    graph_client_id: str = ""
    graph_client_secret_input: str = ""
    graph_client_secret_configurado: bool = False
    graph_testando: bool = False

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

    # --- KipFlow ---
    kipflow_base_url: str = "https://api.kipflow.io"
    kipflow_api_key_input: str = ""
    kipflow_api_key_configurado: bool = False

    # --- Hunter.io (e-mail dos contatos decisores) ---
    # Até HUNTER_MAX_CONTAS contas, balanceadas na busca de e-mail. Os três
    # dicionários são indexados pelo slot COMO STRING porque o `foreach` da UI
    # renderiza um campo por slot e o Reflex serializa a chave do dicionário
    # para o browser — e já nascem com uma entrada por slot, senão o `rx.input`
    # receberia `undefined` no primeiro render (mesmo motivo do preço dos
    # tokens, logo acima).
    hunter_key_inputs: Dict[str, str] = {str(s): "" for s in HUNTER_SLOTS}
    hunter_slot_configurado: Dict[str, bool] = {str(s): False for s in HUNTER_SLOTS}
    # "12 de 50 créditos" por conta: com oito contas, o total sozinho não diz
    # qual delas está no fim, que é a informação de que o super admin precisa
    # para decidir onde criar a próxima.
    hunter_slot_uso: Dict[str, str] = {str(s): "" for s in HUNTER_SLOTS}
    # Texto, não int: é o que o `rx.input` devolve. A conversão e a validação
    # ficam no `salvar`, mesmo padrão do preço dos tokens. Este é o teto de
    # CADA conta; o orçamento do ciclo é ele vezes as contas configuradas.
    hunter_creditos_mensais: str = "50"
    # Dia do mês em que as contas do Hunter renovam (aniversário da assinatura).
    # Um só para todas: elas são criadas no mesmo dia justamente por isso.
    hunter_dia_renovacao: str = "1"
    hunter_total_label: str = "nenhuma conta configurada"

    # --- Banco de dados (só leitura — não configurável aqui, ver Context) ---
    db_url_display: str = ""

    # ------------------------------------------------------- setters (auto-setters desligados)
    def set_product_model(self, value: str):
        self.product_model = value

    def set_product_effort(self, value: str):
        self.product_effort = value

    def set_prospect_model(self, value: str):
        self.prospect_model = value

    def set_prospect_effort(self, value: str):
        self.prospect_effort = value

    def set_priorizacao_model(self, value: str):
        self.priorizacao_model = value

    def set_priorizacao_effort(self, value: str):
        self.priorizacao_effort = value

    def set_insights_model(self, value: str):
        self.insights_model = value

    def set_insights_effort(self, value: str):
        self.insights_effort = value

    def set_graph_sender_email(self, value: str):
        self.graph_sender_email = value

    def set_graph_tenant_id(self, value: str):
        self.graph_tenant_id = value

    def set_graph_client_id(self, value: str):
        self.graph_client_id = value

    def set_graph_client_secret_input(self, value: str):
        self.graph_client_secret_input = value

    # Um par de setters para os 3 modelos: o modelo vem como argumento, ligado
    # no `on_change` da UI (`lambda v: ...set_token_input_price(modelo, v)`).
    # Assim incluir um 4º modelo em MODELOS_DISPONIVEIS não exige mexer aqui.
    def set_token_input_price(self, modelo: str, value: str):
        self.token_input_prices[modelo] = value

    def set_token_output_price(self, modelo: str, value: str):
        self.token_output_prices[modelo] = value

    def set_kipflow_base_url(self, value: str):
        self.kipflow_base_url = value

    def set_kipflow_api_key_input(self, value: str):
        self.kipflow_api_key_input = value

    # Um setter só para as 8 contas, com o slot vindo por argumento (mesmo
    # padrão do preço por modelo): subir HUNTER_MAX_CONTAS não exige mexer aqui.
    def set_hunter_key_input(self, slot: str, value: str):
        self.hunter_key_inputs[str(slot)] = value

    def set_hunter_creditos_mensais(self, value: str):
        self.hunter_creditos_mensais = value

    def set_hunter_dia_renovacao(self, value: str):
        self.hunter_dia_renovacao = value

    # ------------------------------------------------------- load
    def load_settings(self):
        """Entra na mesma lista de on_load de /admin (ver prospect_agent.py),
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
                self.kipflow_base_url = linha.kipflow_base_url
                self.kipflow_api_key_configurado = bool(linha.kipflow_api_key_enc)
                self.hunter_creditos_mensais = str(linha.hunter_creditos_mensais)
                self.hunter_dia_renovacao = str(linha.hunter_dia_renovacao)

        # Campos de segredo: sempre em branco no load (nunca o valor real).
        self.graph_client_secret_input = ""
        self.kipflow_api_key_input = ""
        self._carregar_contas_hunter()

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

    def _carregar_contas_hunter(self):
        """Estado das contas da Hunter para a UI: quais slots estão preenchidos,
        quanto cada um já gastou no ciclo e o orçamento somado.

        Nenhuma CHAVE passa por aqui — o State é serializado para o browser, e
        o que a tela precisa saber é só "configurado" e o consumo.
        """
        from sales_support_agent.services import hunter_client
        from sales_support_agent.services.settings import (
            ensure_hunter_accounts,
            get_hunter_creditos_mensais,
            slots_hunter_configurados,
        )

        ensure_hunter_accounts()
        configurados = set(slots_hunter_configurados())
        limite = get_hunter_creditos_mensais()
        usados = hunter_client.creditos_usados_por_conta(self.tenant_id)

        self.hunter_key_inputs = {str(s): "" for s in HUNTER_SLOTS}
        self.hunter_slot_configurado = {str(s): (s in configurados) for s in HUNTER_SLOTS}
        self.hunter_slot_uso = {
            str(s): (f"{usados.get(s, 0)} de {limite} no ciclo" if s in configurados else "")
            for s in HUNTER_SLOTS
        }

        qtd = len(configurados)
        if qtd:
            self.hunter_total_label = (
                f"{qtd} conta(s) configurada(s): {qtd * limite} créditos por ciclo, "
                f"{sum(usados.get(s, 0) for s in configurados)} já usados."
            )
        else:
            self.hunter_total_label = (
                "Nenhuma conta configurada: a busca de e-mail dos contatos fica desligada."
            )

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

    def save_kipflow_settings(self):
        if not self.is_superadmin:
            return toast_error("Apenas super admin pode alterar configurações.")
        from sales_support_agent.services.settings import salvar_integration_settings

        campos = {"kipflow_base_url": self.kipflow_base_url.strip() or "https://api.kipflow.io"}
        if self.kipflow_api_key_input.strip():
            campos["kipflow_api_key"] = self.kipflow_api_key_input.strip()
        salvar_integration_settings(**campos)
        with rx.session() as session:
            self.log_activity("CONFIG_INTEGRACAO", "Atualizou a configuração da KipFlow.", session)
            session.commit()
        return [toast_success("Configuração da KipFlow salva com sucesso."), SettingsState.load_settings]

    def save_hunter_settings(self):
        """Salva as chaves das contas, o teto de créditos e o dia de renovação.

        Os números são validados aqui, e não no serviço: um valor inválido não
        pode virar 0 silenciosamente, porque 0 desliga a busca de e-mails
        inteira sem nenhuma pista para quem digitou errado.

        O teto e o dia valem para TODAS as contas: o teto porque as contas são
        do mesmo plano, e o dia porque elas são criadas no mesmo dia de
        propósito, para haver uma única janela de ciclo a acompanhar.
        """
        if not self.is_superadmin:
            return toast_error("Apenas super admin pode alterar configurações.")
        from sales_support_agent.services.settings import (
            salvar_hunter_account, salvar_integration_settings,
        )

        try:
            limite = int(self.hunter_creditos_mensais.strip())
        except ValueError:
            return toast_error("Limite de créditos: informe um número inteiro.")
        if limite < 0:
            return toast_error("Limite de créditos não pode ser negativo.")

        try:
            dia = int(self.hunter_dia_renovacao.strip())
        except ValueError:
            return toast_error("Dia da renovação: informe um número inteiro de 1 a 31.")
        if not 1 <= dia <= 31:
            return toast_error("Dia da renovação deve estar entre 1 e 31.")

        salvar_integration_settings(
            hunter_creditos_mensais=limite, hunter_dia_renovacao=dia,
        )

        # Campo em branco preserva a chave já gravada (mesma regra das demais
        # integrações — a UI nunca lê o segredo de volta para mostrar). Apagar
        # uma conta é ação própria, em `remover_hunter_account`.
        novas = []
        for slot in HUNTER_SLOTS:
            chave = self.hunter_key_inputs.get(str(slot), "").strip()
            if chave:
                salvar_hunter_account(slot, chave)
                novas.append(slot)

        detalhe = (
            f"Atualizou a configuração da Hunter ({limite} crédito(s) por ciclo "
            f"por conta, renovando todo dia {dia})."
        )
        if novas:
            detalhe += f" Chave gravada na(s) conta(s): {', '.join(str(s) for s in novas)}."
        with rx.session() as session:
            self.log_activity("CONFIG_INTEGRACAO", detalhe, session)
            session.commit()
        return [
            toast_success("Configuração da Hunter salva com sucesso."),
            AdminState.load_dashboard,
            SettingsState.load_settings,
        ]

    def remover_hunter_account(self, slot: int):
        """Apaga a credencial de uma conta da Hunter.

        Ação separada do "Salvar" porque um campo de segredo em branco significa
        "não mexa", nunca "apague": sem um caminho explícito, cancelar uma conta
        exigiria mexer no banco à mão. O consumo já registrado permanece, é
        histórico de custo.
        """
        if not self.is_superadmin:
            return toast_error("Apenas super admin pode alterar configurações.")
        from sales_support_agent.services.settings import remover_hunter_account

        remover_hunter_account(int(slot))
        with rx.session() as session:
            self.log_activity(
                "CONFIG_INTEGRACAO",
                f"Removeu a chave da conta {slot} da Hunter.",
                session,
            )
            session.commit()
        return [
            toast_success(f"Conta {slot} da Hunter removida."),
            AdminState.load_dashboard,
            SettingsState.load_settings,
        ]