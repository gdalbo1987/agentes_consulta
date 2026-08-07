import reflex as rx
from sqlmodel import SQLModel, Field
from sqlalchemy import Column, Text
from typing import Optional
from datetime import datetime, timedelta, timezone

def brt_now():
    """Gera a hora atual no fuso UTC-3 (Brasília) e remove os milissegundos."""
    fuso_brt = timezone(timedelta(hours=-3))    
    return datetime.now(fuso_brt).replace(tzinfo=None, microsecond=0)

class Tenant(SQLModel, table=True):
    """A organização dona da instalação. Nesta versão interna existe UMA única
    linha — "Coester" (id=1), criada pelo seed (scripts/seed.py).

    A coluna `tenant_id` continua nas tabelas operacionais e todas as queries
    continuam filtrando por ela: com um único tenant o filtro é inócuo, e mantê-lo
    evita reescrever centenas de queries sem ganho funcional. As colunas
    comerciais (stripe_customer_id, subscription_status, plan_name) foram
    removidas junto com o modelo de assinatura.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    created_at: datetime = Field(default_factory=brt_now)

class User(SQLModel, table=True):
    """Usuários do sistema. Só existem por convite de um super admin (não há
    cadastro público) — ver AdminState.create_user."""
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True)
    name: str
    avatar_url: Optional[str] = None

    # Senha (bcrypt). Fica nula entre o convite e o clique no link de definição
    # de senha — nesse intervalo o acesso é só pelo `reset_token` abaixo.
    hashed_password: Optional[str] = None

    # Token de redefinição de senha (link enviado por e-mail) e sua validade
    reset_token: Optional[str] = Field(default=None, index=True)
    reset_token_expires: Optional[datetime] = None

    tenant_id: Optional[int] = Field(default=None, foreign_key="tenant.id")
    is_superadmin: bool = Field(default=False)

class Lead(SQLModel, table=True):
    """Tabela operacional: Leads de um cliente específico."""
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    email: str
    score: int = Field(default=0)
    
    # A trava de segurança: Todo lead pertence a um Tenant
    tenant_id: int = Field(foreign_key="tenant.id", index=True)

class ActivityLog(SQLModel, table=True):
    """Rastreamento de tudo o que acontece (Audit Log)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    user_email: str
    action: str 
    details: str 
    
    # Aqui também trocamos pela nossa nova função
    timestamp: datetime = Field(default_factory=brt_now)

class Product(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id")
    name: str
    description: str
    created_at: datetime = Field(default_factory=brt_now)

class TokenUsage(SQLModel, table=True):
    """Consumo de tokens dos modelos (entrada e saída são contados separadamente,
    pois têm cobrança diferente). Soma de todos os agentes por tenant."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    agent_name: str
    # Modelo que de fato gerou este consumo, gravado no instante do uso.
    # Sem esta coluna o custo teria de ser atribuído pelo modelo que o agente
    # usa HOJE — e trocar o modelo em `/admin` reescreveria retroativamente
    # todo o histórico de gasto. Linhas gravadas antes desta coluna existir
    # ficam com "" e caem no fallback descrito em `AdminState.load_dashboard`.
    model: str = Field(default="", index=True)
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    created_at: datetime = Field(default_factory=brt_now)


class TokenPricing(SQLModel, table=True):
    """Preço do token por MODELO OpenAI — uma linha para cada item de
    `services/settings.MODELOS_DISPONIVEIS`, editável pelo super admin em
    `/admin`. Multiplica os contadores de `TokenUsage` para virar custo.

    Guardado POR TOKEN (não por milhão) porque é assim que o cálculo é feito;
    a UI é que pede o valor por 1M de tokens, que é o formato da tabela de
    preços publicada pela OpenAI, e converte na hora de salvar.

    Preço por modelo, e não único: os modelos da mesma família diferem em uma
    ordem de grandeza no preço, então um valor global tornaria o total do painel
    inutilizável assim que dois agentes rodassem em modelos diferentes. O
    cruzamento com o consumo é exato porque `TokenUsage.model` guarda o modelo
    usado em cada chamada.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    model: str = Field(index=True, unique=True)
    input_cost_per_token: float = Field(default=0.0)   # USD
    output_cost_per_token: float = Field(default=0.0)  # USD
    updated_at: datetime = Field(default_factory=brt_now)


class SearchConfig(SQLModel, table=True):
    """Parâmetros da pesquisa de prospecção por tenant (uma linha por tenant).
    Uma nova pesquisa SOBRESCREVE estes campos — não é histórico versionado
    (o histórico das execuções fica em SearchRun)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True, unique=True)
    product_ids: str = Field(default="[]")  # lista JSON de Product.id selecionados
    regiao: str = ""
    segmento: str = ""
    last_search_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=brt_now)


class SearchRun(SQLModel, table=True):
    """Execução (versionada) da pesquisa de prospecção: status + JSON de resultado."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    # Quem disparou a execução. É por esta coluna que a cota mensal de 20
    # consultas é contada (o limite é POR USUÁRIO, não pela organização) e é
    # dela que sai o "coletor" propagado para ProspectCompany.user_email.
    user_email: str = Field(default="", index=True)
    pesquisa_id: str = Field(index=True)  # uuid4
    status: str = "running"  # running | done | error
    regiao: str = ""  # snapshot da entrada no momento da execução
    segmento: str = ""
    total_empresas: int = Field(default=0)
    meta_atingida: bool = Field(default=False)
    resumo: str = ""
    erro: str = ""
    result_json: str = Field(default="", sa_column=Column(Text))
    started_at: datetime = Field(default_factory=brt_now)
    finished_at: Optional[datetime] = None


class ProspectCompany(SQLModel, table=True):
    """Empresa da fase de pesquisa MATERIALIZADA como registro + campos da fase
    de enriquecimento (KipFlow).

    A fase de pesquisa grava as empresas apenas dentro de SearchRun.result_json
    (um blob de texto). Esta tabela é a materialização desse JSON: sem ela não
    há PK por empresa, índice por CNPJ nem onde guardar enrichment_status.
    O preenchimento é feito por services/enrichment.ensure_companies_materialized,
    que é idempotente (rodar de novo não duplica)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    search_run_id: int = Field(foreign_key="searchrun.id", index=True)
    # Usuário que coletou a empresa — copiado do SearchRun que a originou, em
    # ensure_companies_materialized. Denormalizado de propósito: o filtro por
    # usuário em /leads é uma consulta direta, sem join com searchrun.
    user_email: str = Field(default="", index=True)

    # --- origem: copiado de EmpresaICP (services/prospect_agent.py) ---
    nome: str
    website: Optional[str] = None
    localizacao: str = ""
    segmento_identificado: str = ""
    icp_score: int = Field(default=0)
    justificativa_match: str = ""

    # --- chaves canônicas (dedupe + idempotência) ---
    cnpj: Optional[str] = Field(default=None, index=True)  # 14 dígitos (zfill)
    dominio: Optional[str] = Field(default=None, index=True)

    # --- os 12 campos do enriquecimento (2 = cnpj acima; 11 e 12 = CompanyContact) ---
    razao_social: Optional[str] = None                # 1
    cidade: Optional[str] = None                      # 3
    estado: Optional[str] = None                      # 3
    endereco: Optional[str] = None                    # vem de graça no mesmo dataset
    bairro: Optional[str] = None
    cep: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    data_inicio_atividade: Optional[str] = None       # 4 (origem do cálculo)
    idade_empresa_anos: Optional[int] = None          # 4 (calculado em Python)
    porte: Optional[str] = None                       # 5 Pequena | Média | Grande
    porte_original: Optional[str] = None              # valor cru da API (ex.: DEMAIS)
    segmento: Optional[str] = None                    # 6
    faturamento_estimado: Optional[str] = None        # 7 é uma FAIXA, não valor exato
    status_cadastral: Optional[str] = None            # 8 Ativa | Inativa | Baixada
    status_cadastral_original: Optional[str] = None   # valor cru da Receita
    # Situação especial (recuperação judicial, falência, liquidação...).
    # NÃO é redundante com status_cadastral: a Receita mantém a empresa como
    # "ATIVA" mesmo em recuperação judicial — foi o caso da Real Auto Ônibus.
    # Vem do campo `situacao_especial` da Receita (gratuito) e serve como
    # critério de priorização/risco para a próxima fase do funil.
    alerta_situacao: Optional[str] = Field(default=None, index=True)
    alerta_situacao_desde: Optional[str] = None
    website_principal: Optional[str] = None           # 9
    telefone: Optional[str] = None                    # 10
    telefone_whatsapp: bool = Field(default=False)
    linkedin_url: Optional[str] = None                # insumo para buscar decisores

    # --- controle do enriquecimento ---
    # pending | in_progress | completed | partial | failed
    enrichment_status: str = Field(default="pending", index=True)
    enrichment_percentage: int = Field(default=0)
    enriched_at: Optional[datetime] = None
    enrichment_errors: str = Field(default="[]", sa_column=Column(Text))  # lista JSON
    # Payload bruto da KipFlow: permite reprocessar/remapear sem gastar crédito de novo.
    kipflow_raw_response: str = Field(default="", sa_column=Column(Text))
    kipflow_cost: float = Field(default=0.0)
    created_at: datetime = Field(default_factory=brt_now)

    # --- priorização (fase 3): pontuação calculada sobre o lead já enriquecido ---
    # pending | done | failed
    priorizacao_status: str = Field(default="pending", index=True)
    priorizacao_score_final: Optional[int] = None
    priorizacao_classe: Optional[str] = None  # Alta | Média | Baixa
    # JSON: lista de 7 objetos {"criterio","pontos","justificativa"}. Não são
    # 14 colunas separadas pelo mesmo motivo de enrichment_errors/
    # kipflow_raw_response: nada no projeto filtra por critério individual, só
    # por priorizacao_score_final/priorizacao_classe (esses sim são colunas).
    priorizacao_criterios: str = Field(default="", sa_column=Column(Text))
    priorizacao_executado_em: Optional[datetime] = None
    priorizacao_erro: str = ""

    # --- approach (fase 3, opcional por lead): dicas de primeiro contato ---
    approach_status: str = Field(default="pending", index=True)  # pending | done | failed
    approach_dicas: str = Field(default="", sa_column=Column(Text))  # JSON: 2-4 dicas
    approach_executado_em: Optional[datetime] = None
    approach_erro: str = ""


class CompanyContact(SQLModel, table=True):
    """Contato decisor de uma empresa (1:N com ProspectCompany)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    company_id: int = Field(foreign_key="prospectcompany.id", index=True)
    nome: str
    cargo: str = ""
    senioridade: str = ""
    area: str = ""
    perfil_url: str = ""
    perfil_public_id: Optional[str] = None  # chave de dedupe entre execuções
    # De onde veio o contato — muda COMO abordar, não é só metadado:
    #   "qsa"      -> sócio/administrador do quadro societário (Receita Federal,
    #                 gratuito). É o decisor de fato, mas sem canal direto:
    #                 aborda-se pelo telefone/e-mail da empresa pedindo por ele.
    #   "linkedin" -> perfil do LinkedIn (KipFlow, pago). Tem canal direto
    #                 (perfil_url), porém costuma ser de senioridade menor.
    origem: str = Field(default="linkedin")

    # --- e-mail profissional (Hunter.io, ver services/hunter_client.py) ---
    email: str = Field(default="")
    # Confiança do Hunter (0-100). Abaixo de ~70 o e-mail é um palpite de
    # padrão ("nome.sobrenome@dominio") sem fonte pública confirmada, então o
    # score é exibido junto: quem for disparar e-mail precisa saber a diferença.
    email_confianca: Optional[int] = None
    # Marca a TENTATIVA, não o sucesso. É o que impede gastar um crédito duas
    # vezes no mesmo contato quando o enriquecimento roda de novo — sem ela,
    # cada reprocessamento queimaria a cota do ciclo inteira de novo.
    email_buscado_em: Optional[datetime] = None

    created_at: datetime = Field(default_factory=brt_now)


class EnrichmentRun(SQLModel, table=True):
    """Execução do processo de enriquecimento (alimenta o painel de status)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    user_email: str = Field(default="", index=True)  # cota mensal por usuário
    search_run_id: int = Field(foreign_key="searchrun.id", index=True)
    status: str = "running"  # running | done | error
    total_empresas: int = Field(default=0)
    processadas: int = Field(default=0)
    puladas: int = Field(default=0)  # idempotência: já estavam completed
    falhas: int = Field(default=0)
    custo_total: float = Field(default=0.0)
    erro: str = ""
    avisos: str = Field(default="[]", sa_column=Column(Text))  # lista JSON
    started_at: datetime = Field(default_factory=brt_now)
    finished_at: Optional[datetime] = None


class PriorizacaoRun(SQLModel, table=True):
    """Execução do processo de priorização (+ approach opcional), mesmo
    template de EnrichmentRun — alimenta o painel de status de /priorizacao."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    user_email: str = Field(default="", index=True)  # cota mensal por usuário
    search_run_id: int = Field(foreign_key="searchrun.id", index=True)
    status: str = "running"  # running | done | error
    incluiu_approach: bool = Field(default=False)
    total_leads: int = Field(default=0)
    processados: int = Field(default=0)
    puladas: int = Field(default=0)  # já tinham priorizacao_status == "done" (idempotência)
    falhas: int = Field(default=0)
    erro: str = ""
    avisos: str = Field(default="[]", sa_column=Column(Text))  # lista JSON
    started_at: datetime = Field(default_factory=brt_now)
    finished_at: Optional[datetime] = None


class KipflowUsage(SQLModel, table=True):
    """Custo cobrado pela API da KipFlow, por requisição (fonte do indicador de
    custo no God Mode — mesmo papel que TokenUsage cumpre para os modelos de IA).
    A KipFlow cobra por dataset/registro retornado e devolve o valor em `cost`."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    endpoint: str  # ex.: "companies/batch/cnpj", "social/personas"
    cost: float = Field(default=0.0)  # em BRL, como veio no campo `cost` da resposta
    created_at: datetime = Field(default_factory=brt_now)

class HunterAccount(SQLModel, table=True):
    """Uma das contas da Hunter.io entre as quais a busca de e-mail é balanceada.

    O Hunter vende créditos por CONTA, e o plano gratuito dá poucos por ciclo.
    Para ampliar a cota sem plano pago, a plataforma aceita até
    `HUNTER_MAX_CONTAS` contas e distribui as buscas entre elas
    (`services/hunter_client.Balanceador`): o orçamento do ciclo passa a ser
    créditos por conta x contas configuradas.

    Tabela de configuração, como `IntegrationSetting` e `AgentModelSetting`:
    não tem `tenant_id` porque é a credencial da organização inteira, não de um
    cliente. `slot` (1..8) é a identidade estável da conta — é ele que
    `HunterUsage.account_slot` referencia, e por isso apagar a linha de um slot
    NÃO apaga o histórico de consumo dela.

    A chave fica CIFRADA (Fernet, services/crypto.py) e nunca é lida de volta
    para um campo de State: a UI vê apenas se o slot está configurado.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    slot: int = Field(index=True, unique=True)  # 1..HUNTER_MAX_CONTAS
    api_key_enc: str = Field(default="", sa_column=Column(Text))
    created_at: datetime = Field(default_factory=brt_now)
    updated_at: datetime = Field(default_factory=brt_now)


class HunterUsage(SQLModel, table=True):
    """Consumo da API Hunter.io na busca de e-mail dos contatos decisores.

    Mesmo papel que `KipflowUsage` cumpre para a KipFlow, com uma diferença que
    é do próprio fornecedor: o Hunter NÃO cobra em dinheiro por chamada, cobra
    em CRÉDITOS de um pacote mensal, e a documentação é explícita em que uma
    busca sem resultado não consome crédito. Por isso há duas colunas:

    - toda tentativa vira uma linha (com `encontrado` True/False), para o painel
      mostrar a taxa de acerto real;
    - `creditos` é 1 só quando veio e-mail, e é ele que alimenta o card, o
      gráfico e — principalmente — o gate da cota do ciclo.

    Somar linhas em vez de créditos superestimaria o consumo e faria a
    plataforma se auto-bloquear antes da hora.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    # Nulo quando o contato foi apagado depois; o consumo permanece no histórico.
    contact_id: Optional[int] = Field(default=None, index=True)
    dominio: str = Field(default="")
    encontrado: bool = Field(default=False)
    creditos: int = Field(default=0)
    # Slot da conta que pagou o crédito (HunterAccount.slot). Sem ele o
    # balanceamento seria cego: o teto é POR CONTA, então a soma global não diz
    # se ainda cabe uma busca na conta escolhida. Não é FK de propósito — a
    # linha do slot pode ser removida quando a conta é cancelada, e o consumo
    # já feito continua contando no ciclo.
    account_slot: int = Field(default=1, index=True)
    created_at: datetime = Field(default_factory=brt_now, index=True)


class ChatMessage(SQLModel, table=True):
    """Memória da conversa do agente de Insights IA (`/insights-ia`).

    Persiste a interface `Session` do OpenAI Agents SDK (get_items/add_items/
    pop_item/clear_session em services/insights_agent.py) — o próprio SDK lê e
    grava aqui automaticamente a cada turno via `Runner.run(..., session=...)`,
    não precisa de escrita manual no state. Escopo por (tenant_id, user_email):
    cada usuário tem sua própria conversa, mesmo dentro do mesmo tenant.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    user_email: str = Field(index=True)
    role: str  # "user" | "assistant" | "system" | "tool" (o que o SDK gravar)
    content: str = Field(default="", sa_column=Column(Text))  # texto extraído, p/ exibição na UI
    raw_json: str = Field(default="", sa_column=Column(Text))  # item bruto do SDK (reconstrução exata)
    created_at: datetime = Field(default_factory=brt_now)


class AgentModelSetting(SQLModel, table=True):
    """Modelo + reasoning effort de um agente de IA — editável pelo super admin em
    `/admin`: semeado do `.env` uma única vez (ver services/settings.py) e, a
    partir daí, o banco é a única fonte — o `.env` deixa de ser consultado para
    esses valores.

    `agent_key` agrupa os pontos de configuração de modelo que o `.env` já
    impõe hoje (não inventa granularidade nova): "product" (assistente de
    descrição + seu guardrail), "prospect" (os dois agentes de /pesquisa, que
    já compartilham OPENAI_SEARCH_MODEL), "priorizacao" (priorização + approach,
    que já compartilham OPENAI_PRIORIZACAO_MODEL) e "insights" (chat + guardrail).
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    agent_key: str = Field(index=True, unique=True)  # product|prospect|priorizacao|insights
    model: str
    effort: str  # minimal|low|medium|high
    updated_at: datetime = Field(default_factory=brt_now)


class IntegrationSetting(SQLModel, table=True):
    """Linha única com as integrações de conta (e-mail e KipFlow), editável pelo
    super admin em `/admin`. Segredos ficam CRIPTOGRAFADOS (Fernet, ver
    services/crypto.py) — nunca em texto puro, e nunca lidos de volta para um
    campo de State (State é serializado para o browser). A conexão do banco
    (DATABASE_URL) fica de fora de propósito: não pode depender de uma
    configuração guardada no próprio banco que ainda não se sabe acessar.
    """
    id: Optional[int] = Field(default=None, primary_key=True)

    # --- E-mail: Microsoft Graph API (substituiu o SMTP) ---
    # Caixa remetente: precisa ser um usuário/mailbox real do locatário, porque
    # o envio é feito em POST /v1.0/users/{graph_sender_email}/sendMail.
    graph_sender_email: str = ""
    # ATENÇÃO ao nome: este é o "Directory (tenant) ID" do Entra ID, e NÃO tem
    # relação com o `tenant_id` da aplicação (a organização Coester). O prefixo
    # `graph_` existe justamente para os dois nunca serem confundidos.
    graph_tenant_id: str = ""
    graph_client_id: str = ""
    graph_client_secret_enc: str = Field(default="", sa_column=Column(Text))

    kipflow_api_key_enc: str = Field(default="", sa_column=Column(Text))
    kipflow_base_url: str = "https://api.kipflow.io"

    # --- Hunter.io: e-mail dos contatos decisores ---
    # As CHAVES não ficam aqui: são várias, uma por conta, em `HunterAccount`.
    # O que sobra nesta linha é o que vale para todas elas.
    #
    # Teto de créditos por ciclo DE CADA CONTA. 50 é o plano gratuito do
    # Hunter; fica configurável porque a conta paga muda esse número, e o gate
    # da aplicação não pode exigir deploy para acompanhar. O orçamento total do
    # ciclo é este valor multiplicado pelas contas configuradas.
    hunter_creditos_mensais: int = Field(default=50)
    # Dia do mês em que o Hunter renova os créditos: é o aniversário da
    # ASSINATURA, não o dia 1º. Contar por mês civil daria uma janela deslocada
    # do ciclo real e a plataforma bloquearia (ou liberaria) na hora errada.
    # Dia maior que o mês comporta (31 em fevereiro) cai no último dia dele,
    # tratado em `hunter_client.inicio_do_ciclo`. Vale para TODAS as contas: o
    # operador cria as contas no mesmo dia justamente para não ter oito janelas
    # diferentes para acompanhar.
    hunter_dia_renovacao: int = Field(default=1)

    updated_at: datetime = Field(default_factory=brt_now)
