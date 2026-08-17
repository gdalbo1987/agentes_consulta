import reflex as rx
from sqlmodel import SQLModel, Field
from sqlalchemy import Column, Text, UniqueConstraint
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

class ActivityLog(SQLModel, table=True):
    """Rastreamento de tudo o que acontece (Audit Log)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    user_email: str
    action: str 
    details: str 
    
    # Aqui também trocamos pela nossa nova função
    timestamp: datetime = Field(default_factory=brt_now)

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
    """Linha única com a CREDENCIAL da Microsoft Graph, editável pelo super
    admin em `/admin`. Segredos ficam CRIPTOGRAFADOS (Fernet, ver
    services/crypto.py) — nunca em texto puro, e nunca lidos de volta para um
    campo de State (State é serializado para o browser). A conexão do banco
    (DATABASE_URL) fica de fora de propósito: não pode depender de uma
    configuração guardada no próprio banco que ainda não se sabe acessar.

    Só credencial e conexão moram aqui. A configuração OPERACIONAL do Agente 1
    (horários das rodadas, janela de urgência, mapa de pastas) fica em
    `ClassificacaoConfig` e `PastaClasse`: ela é editada pelo usuário PADRÃO, é
    por tenant, e a UI precisa lê-la de volta — três coisas que esta tabela não
    faz, já que ela não tem `tenant_id` e é toda serializada como booleano.
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

    # Pasta de ORIGEM da varredura: o nome bem-conhecido `inbox` resolve a Caixa
    # de Entrada em qualquer idioma do locatário, o que um `displayName` não faz
    # (numa caixa em português ela se chama "Caixa de Entrada"). Aceita também o
    # id de uma pasta específica, para quem varre outra coisa que não a entrada.
    # É nível de CONEXÃO, por isso mora aqui e não na configuração operacional.
    graph_pasta_origem: str = "inbox"

    updated_at: datetime = Field(default_factory=brt_now)


# ===========================================================================
# Agente de Suporte ao Comercial — e-mails classificados
# ===========================================================================


class ClassificacaoRun(SQLModel, table=True):
    """Cabeçalho de uma execução do Agente 1, manual ou agendada.

    Mesmo molde das antigas SearchRun/EnrichmentRun/PriorizacaoRun: cria-se a
    linha como `running` ANTES do trabalho, atualizam-se os contadores em
    sessões curtas a cada evento de progresso (é o que faz o progresso
    sobreviver a um reload de página) e fecha-se com `done` ou `error`.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: Optional[int] = Field(default=None, foreign_key="tenant.id", index=True)

    # Vazio quando a rodada é agendada: não há usuário por trás dela. Indexado
    # porque é a chave de qualquer contagem por usuário.
    user_email: str = Field(default="", index=True)
    origem: str = Field(default="manual", index=True)  # manual | agendado
    slot: str = Field(default="")  # "" | h1 | h2 — qual horário disparou

    status: str = Field(default="running", index=True)  # running | done | error

    total_emails: int = 0      # lidos do Graph na janela
    processados: int = 0       # efetivamente enviados ao modelo
    classificados: int = 0     # caíram numa das 4 classes
    ignorados: int = 0         # não se encaixaram em nenhuma
    urgentes: int = 0
    resumidos: int = 0
    puladas: int = 0           # já estavam no banco: idempotência, custo zero
    falhas: int = 0

    erro: str = Field(default="", sa_column=Column(Text))
    avisos: str = Field(default="[]", sa_column=Column(Text))  # lista JSON

    started_at: datetime = Field(default_factory=brt_now, index=True)
    finished_at: Optional[datetime] = None

    # MATERIALIZADO, embora derivável dos dois timestamps acima. Com a coluna, o
    # card de duração média do dashboard é um AVG() direto; sem ela, seria uma
    # subtração de timestamps anuláveis linha a linha, em Python, toda vez que
    # a página carrega.
    duracao_segundos: Optional[int] = Field(default=None, index=True)


class EmailClassificado(SQLModel, table=True):
    """Um e-mail visto pelo Agente 1, tenha ele se encaixado numa classe ou não.

    Duas decisões aqui definem o comportamento do produto e são fáceis de errar:

    1. A identidade é o `internet_message_id`, NÃO o id do Graph. O id de uma
       mensagem MUDA quando ela é movida de pasta, e mover é exatamente o que
       este agente faz toda rodada. Se a deduplicação dependesse do id, a
       segunda execução reclassificaria (e recobraria) tudo o que a primeira
       moveu. O `internetMessageId` (RFC 5322) é imutável.

    2. E-mail que não se encaixa em nenhuma das 4 classes TAMBÉM vira linha,
       com `status="ignorado"` e `classe=""`. Sem essa linha, a rodada seguinte
       mandaria o mesmo e-mail ao modelo de novo, pagando de novo. Ele não é
       marcado, não é movido, e não aparece na tabela do dashboard.
    """

    __table_args__ = (
        UniqueConstraint("tenant_id", "internet_message_id", name="uq_email_tenant_imid"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: Optional[int] = Field(default=None, foreign_key="tenant.id", index=True)

    internet_message_id: str = Field(index=True, max_length=998)
    # Último id conhecido no Graph, reescrito com o que o POST /move devolve.
    # Serve para o PATCH/move seguinte, nunca para deduplicar.
    graph_message_id: str = Field(default="", sa_column=Column(Text))
    graph_conversation_id: str = Field(default="", index=True)
    graph_web_link: str = Field(default="", sa_column=Column(Text))

    remetente_email: str = Field(default="", index=True)  # o "cliente" das consultas
    remetente_nome: str = Field(default="")
    assunto: str = Field(default="", sa_column=Column(Text))
    # Corpo em texto puro e truncado: limita o custo em tokens e a superfície de
    # injeção de prompt. É o insumo do Agente 2 e da busca do Agente 3.
    corpo_texto: str = Field(default="", sa_column=Column(Text))
    recebido_em: datetime = Field(index=True)

    classe: str = Field(default="", index=True)  # "" = nenhuma das 4
    # Duas faixas de prioridade, mutuamente exclusivas (ver
    # `classificacao_rules.calcular_prioridade`): urgente é "tem data e ela cai
    # dentro da janela"; importante é "tem data, mas além da janela". Ter data é
    # um compromisso assumido, e antes um pedido com entrega em duas semanas
    # ficava indistinguível de um e-mail sem data nenhuma.
    urgente: bool = Field(default=False, index=True)
    importante: bool = Field(default=False, index=True)
    # Prazo estimado pelo modelo, em horas, guardado SEPARADO dos booleanos. Com
    # ele na linha, mudar a janela de urgência de 24h para 8h re-marca todos os
    # e-mails já gravados com um UPDATE, a custo zero de token. Só com o
    # booleano, mudar a janela exigiria reprocessar tudo no modelo.
    urgencia_prazo_horas: Optional[int] = None
    # Persistido junto pelo mesmo motivo: sem ele, o recálculo não conseguia
    # distinguir "urgente porque a data cabe na janela" de "urgente porque o
    # texto diz que é", e precisava adivinhar pela ausência de prazo.
    urgente_semantico: bool = Field(default=False)
    # Quando alguém tirou o e-mail da FILA de urgências, sem apagá-lo.
    #
    # Coluna própria, e não `urgente = False`: a urgência é um fato calculado a
    # partir do prazo, e o recálculo (ao mudar a janela) a reescreveria,
    # trazendo de volta o que já tinha sido tratado. "Já cuidei disto" é
    # decisão de uma pessoa e precisa sobreviver ao recálculo.
    urgencia_tratada_em: Optional[datetime] = Field(default=None, index=True)
    confianca: int = Field(default=0)  # 0-100
    justificativa: str = Field(default="", sa_column=Column(Text))

    # pending | classificado | ignorado | falhou
    status: str = Field(default="pending", index=True)
    # Dois booleanos e não um: a rodada pode falhar ENTRE aplicar a categoria e
    # mover. Separados, a rodada seguinte termina o serviço sem reinvocar o
    # modelo; juntos, não haveria como saber onde parou.
    categoria_aplicada: bool = Field(default=False)
    movido: bool = Field(default=False)
    pasta_destino_id: str = Field(default="")
    erro: str = Field(default="", sa_column=Column(Text))

    run_id: Optional[int] = Field(default=None, foreign_key="classificacaorun.id", index=True)
    classificado_em: Optional[datetime] = None
    created_at: datetime = Field(default_factory=brt_now)


class ResumoEmail(SQLModel, table=True):
    """Resumo gerado pelo Agente 2 para um e-mail já classificado.

    `email_id` é UNIQUE: a relação é um para um, e o "já resumido, pula" do
    Agente 2 passa a ser uma checagem de existência garantida pelo banco, não
    pela disciplina do código.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: Optional[int] = Field(default=None, foreign_key="tenant.id", index=True)
    email_id: int = Field(foreign_key="emailclassificado.id", index=True, unique=True)

    resumo: str = Field(default="", sa_column=Column(Text))
    pontos_chave: str = Field(default="[]", sa_column=Column(Text))  # lista JSON
    acao_sugerida: str = Field(default="", sa_column=Column(Text))
    prazo_mencionado: str = Field(default="")

    status: str = Field(default="pending", index=True)  # pending | done | failed
    erro: str = Field(default="", sa_column=Column(Text))
    # Modelo que gerou, em snapshot: trocar o modelo em /admin depois não
    # reescreve a procedência do que já foi gerado.
    modelo: str = Field(default="")
    gerado_em: Optional[datetime] = None
    created_at: datetime = Field(default_factory=brt_now)


class ClassificacaoConfig(SQLModel, table=True):
    """Configuração OPERACIONAL do Agente 1, uma linha por organização.

    Editável pelo usuário padrão no `/dashboard`, ao contrário de
    `IntegrationSetting`, que é credencial e é do super admin.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: Optional[int] = Field(default=None, foreign_key="tenant.id", index=True, unique=True)

    # "HH:MM" em horário de Brasília. String e não `time` porque é byte a byte o
    # que o `rx.input(type="time")` emite e consome, e esta versão do Reflex não
    # gera setters automáticos: cada conversão a mais seria mais um setter
    # escrito à mão. Com zero à esquerda, a comparação lexicográfica já basta
    # para decidir qual horário está vencido.
    horario_1: str = Field(default="08:00")
    horario_2: str = Field(default="16:00")

    janela_urgencia_horas: int = Field(default=24)
    lookback_horas: int = Field(default=48)            # janela de leitura no Graph
    max_emails_por_execucao: int = Field(default=200)  # o teto de custo da rodada

    # Interruptor das execuções AUTOMÁTICAS, ligado pelo botão "Iniciar" do
    # `/dashboard`. Nasce DESLIGADO de propósito: configurar os horários não é
    # o mesmo que autorizar o agente a mexer na caixa, e uma instalação nova
    # não pode começar a arquivar e-mails sozinha só porque subiu. Não afeta o
    # botão "Classificar agora", que é ação deliberada de uma pessoa.
    ativo: bool = Field(default=False)
    # Marca do último disparo AGENDADO, para o mesmo horário não rodar duas
    # vezes no mesmo dia.
    ultima_execucao_agendada: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=brt_now)

    # Quem salvou por último, e como se chamava na hora. A configuração é da
    # ORGANIZAÇÃO e todo usuário padrão pode mexer nela, então a tela precisa
    # dizer de quem é a configuração que está no ar: sem isso, quem abre o
    # painel vê horários que não reconhece e não tem como saber se foi um
    # colega que mudou ou se é resquício de teste.
    #
    # O NOME é desnormalizado de propósito, e não uma chave estrangeira para
    # `user`. É um registro histórico ("quem configurou assim"), e apagar o
    # usuário depois não pode transformar a resposta em um id órfão nem
    # derrubar o painel.
    atualizado_por_nome: str = Field(default="")
    atualizado_por_email: str = Field(default="")

    # Contador de edições HUMANAS, usado para detectar que dois usuários
    # editaram a mesma configuração (ver `classificacao_config.salvar_config`).
    #
    # É um contador, e não o `updated_at`, porque `brt_now()` trunca em segundos
    # inteiros: duas gravações no mesmo segundo carimbam o MESMO horário, e uma
    # trava baseada nele passaria despercebida exatamente na corrida que ela
    # existe para pegar. O contador é exato e monotônico, sem depender da
    # resolução do relógio.
    versao: int = Field(default=0)


class PastaClasse(SQLModel, table=True):
    """Mapa classe -> pasta do Outlook. O usuário informa o NOME; o backend
    resolve o id pelo Graph e o guarda aqui.

    Uma linha por classe, e não quatro pares de colunas em `ClassificacaoConfig`:
    acrescentar uma quinta classe vira um INSERT, não uma migration, e a UI vira
    um `foreach`. Mesmo precedente das contas por slot que existiam antes.
    """

    __table_args__ = (
        UniqueConstraint("tenant_id", "classe", name="uq_pasta_tenant_classe"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: Optional[int] = Field(default=None, foreign_key="tenant.id", index=True)
    classe: str = Field(index=True)

    pasta_nome: str = Field(default="")     # o que o usuário digitou
    pasta_caminho: str = Field(default="")  # "Caixa de Entrada/Pedidos", para exibir
    pasta_id: str = Field(default="", sa_column=Column(Text))  # resolvido pelo Graph
    resolvido_em: Optional[datetime] = None
    erro_resolucao: str = Field(default="")
    updated_at: datetime = Field(default_factory=brt_now)
