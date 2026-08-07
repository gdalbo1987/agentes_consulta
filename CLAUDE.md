@AGENTS.md

# Agente de Suporte ao Comercial

Ferramenta **interna** do Grupo Coester. Lê a caixa de e-mails do comercial pela
Microsoft Graph API, classifica cada mensagem em uma de quatro classes, marca o
que é urgente, arquiva na pasta correspondente, resume o que classificou e
responde perguntas sobre tudo isso num chat.

Não é um SaaS. Nasceu como produto comercial multi-tenant (marca "Orion Prospect
Brain", planos, Stripe, cadastro público), virou plataforma interna de
prospecção de leads, e foi convertida de novo para este domínio. Se você
encontrar qualquer resquício de billing, planos, OAuth de terceiros, ou do funil
antigo (produtos, pesquisa ICP, enriquecimento, priorização, KipFlow, Hunter.io),
é sobra de conversão: não reintroduza.

## Invariantes do produto

Estas regras são decisões de negócio, não detalhes de implementação. Mudá-las
exige pedido explícito do usuário.

1. **Uma única organização.** Existe exatamente uma linha em `Tenant`
   ("Coester", id=1). A coluna `tenant_id` continua nas tabelas operacionais e
   as queries continuam filtrando por ela; com um tenant só o filtro é inócuo, e
   mantê-lo evitou reescrever centenas de queries. **Nunca** apague dados por
   `tenant_id`: isso destruiria a base inteira da empresa.

2. **Acesso somente por convite.** Não há cadastro público nem login social. Um
   super admin convida (`AdminState.create_user`) informando nome, e-mail e
   classe; **nenhuma senha é definida no convite**. O convidado escolhe a dele
   por um link de uso único de 24h (`User.reset_token`). Super admin pode
   promover outros super admins.

3. **Uma caixa de e-mails, compartilhada.** A organização monitora uma caixa só,
   configurada em `IntegrationSetting.graph_sender_email`. Horários de execução,
   janela de urgência e mapa de pastas são da ORGANIZAÇÃO, não de cada usuário,
   e todo usuário padrão vê todos os e-mails classificados.

4. **Quatro classes, e só quatro:** `pedido`, `proposta`, `revisao_pedido`,
   `revisao_proposta` (`services/classificacao_rules.CLASSES`). E-mail que não se
   encaixa em nenhuma delas **não recebe marcação e não é movido**: ele fica
   exatamente onde estava, na caixa de entrada.

5. **Duas execuções automáticas por dia**, nos horários que o usuário define no
   `/dashboard`, mais o botão de execução manual. As duas compartilham o mesmo
   código.

A cota `CONSULTA_LIMIT_MENSAL = 20` sobreviveu em `state.py` mas está **órfã e
sem uso**. Ela era 20 por usuário por etapa do funil antigo; numa classificação
que roda duas vezes por dia, o mesmo número estouraria no dia 10. A quê ela
passa a se aplicar (execução manual? perguntas ao agente de consulta?) é decisão
de produto ainda pendente. Não a religue sem essa decisão.

## Como rodar

```bash
pip install -r requirements.txt
python -m reflex db migrate      # aplica as migrations
python scripts/seed.py           # tenant Coester, primeiro super admin, config inicial
python scripts/migrar_agentes.py # só ao vir de uma instalação anterior
python -m reflex run --env prod --single-port
```

Migrations são geradas por `python -m reflex db makemigrations --message "..."`.
O wrapper do Reflex já liga a metadata do SQLModel; **não** edite
`alembic/env.py` à mão. Antes de qualquer alteração de schema, leia a skill
`reflex-docs`.

**Revise sempre a migration gerada.** O autogenerate não ordena `DROP TABLE` por
dependência de chave estrangeira: a migration da conversão precisou ter a ordem
corrigida à mão, porque ela emitiu `DROP searchrun` antes de `DROP
prospectcompany`, que o referenciava. O PostgreSQL recusou e a transação
reverteu inteira. Trocar a ordem é preferível a usar `CASCADE`, que derrubaria
em silêncio qualquer outro dependente.

Validação rápida sem subir o servidor: `python -m reflex compile --dry`.

### Ao terminar de verificar: derrube o server e limpe os dados de teste

Regra permanente, não precisa ser pedida a cada vez. Todo server que você subir
para conferir uma alteração deve ser encerrado ao fim da tarefa, e todo dado
criado para testar deve sair do banco. O estado que você deixa é o estado em que
o usuário encontra o projeto.

Derrubar de verdade exige atenção a dois detalhes deste ambiente:

- **O granian roda em processo filho.** Ao matar o pai, o filho herda o socket e
  continua servindo; `Get-NetTCPConnection` segue apontando o PID do pai morto, e
  o `taskkill` responde "processo não encontrado" enquanto a porta responde 200.
  Procure também por `ParentProcessId` e por linhas de comando
  `multiprocessing.spawn`, que não citam "reflex". A cadeia real tem três
  processos.
- **Confirme pela porta, não pela lista de processos:** `curl` em 3000/3001/3002
  até não haver resposta. Uma instância esquecida trava o `node_modules` e o
  próximo `reflex run` falha com `EBUSY`.

Sobre os dados: apague os `EmailClassificado`, `ResumoEmail` e
`ClassificacaoRun` que você criou, e **restaure o que alterou**. Nunca limpe por
`tenant_id`, ver invariante 1. E não confunda dado de teste com histórico
legítimo: `ActivityLog` registra ações reais do usuário no navegador.

## Arquitetura

| Camada | Onde | Observação |
|---|---|---|
| Modelos | `sales_support_agent/models.py` | SQLModel; `brt_now()` é o padrão de timestamp (UTC-3) |
| Estado e event handlers | `sales_support_agent/state.py` | arquivo grande e central |
| Regras e integrações | `sales_support_agent/services/` | funções puras e clientes HTTP |
| UI | `sales_support_agent/pages/`, `components/` | |
| Rotas | `sales_support_agent/sales_support_agent.py` | `/`, `/login` e `/admin` à mão; o resto via `@rx.page` |
| Migrations | `alembic/versions/` | |
| Testes | `tests/` | `pytest`; ver a seção de testes abaixo |

O pipeline é: varredura da caixa -> classificação (Agente 1) -> resumo
(Agente 2) -> consulta (Agente 3). Os dois primeiros rodam juntos, na mesma
execução; o terceiro é sob demanda, no chat.

## As duas armadilhas que mais custam aqui

### O `id` de uma mensagem no Graph MUDA quando ela é movida de pasta

E mover é exatamente o que o Agente 1 faz toda execução. A identidade de
`EmailClassificado` é o **`internet_message_id`** (RFC 5322), que é imutável, e
nunca o `id` do Graph. Se a deduplicação dependesse do `id`, a segunda execução
não reconheceria os e-mails que ela mesma moveu e reclassificaria tudo,
recobrando tudo. O `graph_message_id` é guardado só para o PATCH e o move
seguintes, e é reescrito com o id novo que o `POST /move` devolve.

Decorre disso a ordem obrigatória por e-mail: **marcar a categoria, depois
mover, depois gravar o id novo**. Depois do move, o id antigo não existe, e um
PATCH nele volta 404.

### `PATCH categories` substitui o array inteiro

Mandar só as categorias novas apaga as que o usuário marcou à mão no Outlook, e
ele não teria como saber que foi a plataforma. `graph_client.aplicar_categorias`
lê as atuais e manda a união.

## Convenções que economizam tempo

- **Esta versão do Reflex NÃO gera setters automáticos.** Todo campo ligado a um
  `on_change`/`on_open_change` precisa do seu `set_<campo>` declarado à mão no
  State. Esquecer quebra em runtime, não na compilação. Há um teste reflexivo
  disso em `tests/test_convencoes_reflex.py`.
- **Segredos nunca chegam ao State.** `IntegrationSetting` guarda o client
  secret cifrado com Fernet (`services/crypto.py`, chave em
  `SETTINGS_ENCRYPTION_KEY`). A UI lê apenas o booleano `*_configurado`, porque o
  State é serializado para o browser. Campo em branco ao salvar **preserva** o
  valor atual.
- **Toasts, não `window_alert`**: use `toast_success` / `toast_error` de
  `state.py` em toda ação de salvar/criar/atualizar.
- **Links de e-mail** saem de `base_url()` (`APP_BASE_URL` no `.env`). Nunca
  escreva `http://localhost:3000` no código.
- **Handlers em background** (`@rx.event(background=True)`) precisam tirar um
  snapshot de `self.*` dentro do `async with self:` antes de rodar o trabalho
  longo. Nada de ler o State fora do lock.
- `foreach` do Reflex não acessa dict aninhado tipado: achate os dados em campos
  de topo no serviço antes de mandar para a UI. É por isso que
  `services/emails_query.py` devolve dicionários planos.
- **Nenhuma sessão de banco atravessa um `await` de rede.** Abra, leia, feche,
  chame o modelo ou o Graph, reabra, grave. Uma sessão aberta durante a chamada
  do modelo segura uma conexão do pool por segundos.
- **Nenhum travessão (`—`) em texto que o usuário lê.** Vale para as strings da
  UI, e-mails, documentação e para o que os agentes escrevem: cada agente de
  texto livre injeta `REGRA_SEM_TRAVESSAO` (`services/prompt_rules.py`) nas
  instruções, porque boa parte do que aparece na tela é gerado em runtime.
  Comentário e docstring de código não contam. Onde o travessão era só marcador
  de valor ausente numa tabela, o substituto é `-`.

## Os três agentes

Todos seguem o mesmo molde: saída estruturada por `output_type` (nunca parse
manual de JSON), agente construído **a cada chamada** e nunca como singleton de
módulo (senão trocar o modelo em `/admin` só valeria depois de reiniciar), e
retorno em `(ok, resultado, erro_em_portugues, usage)`.

Modelo e esforço vêm de `AgentModelSetting`, com as chaves
`("classificacao", "resumo", "consulta")` em `services/settings.AGENT_KEYS`.

### Agente 1, classificação

`classe` é um `Literal` sobre a tupla de classes, o que faz o JSON Schema virar
um enum e força o rótulo a sair byte a byte igual. Isso já custou um bug real no
produto anterior: com `str` livre, o modelo devolvia rótulos ornamentados, a
validação por igualdade falhava em silêncio e o resultado parecia falta de dado.
`"nenhuma"` é valor do enum, e não ausência de resposta, para que "não se
encaixa" seja decisão explícita.

**A urgência não é decidida pelo modelo.** Ele estima `prazo_em_horas` e um
sinal semântico; a comparação com a janela configurada é feita em Python
(`classificacao_rules.calcular_urgencia`). Além de conta de data ser
determinística demais para delegar, é isso que permite mudar a janela de 24h
para 8h com um UPDATE, sem reprocessar a caixa no modelo. Por isso
`urgencia_prazo_horas` é persistido separado do booleano `urgente`.

**Contra injeção de prompt, a defesa principal é o `output_type`**, e não a
instrução. O modelo não consegue emitir nada além de um rótulo do enum, um
inteiro, dois booleanos e uma frase: uma injeção, no pior caso, classifica
errado; ela não executa ação, não chama ferramenta e não despeja texto livre no
pipeline. Quem transformar a saída em texto livre estará removendo a proteção
principal, e não apenas mudando o formato.

### Agente 2, resumo

Roda na mesma execução, logo depois de uma classificação bem-sucedida, e nunca
para e-mail ignorado. Falha dele **não desfaz** a classificação: o e-mail já foi
movido, e o resumo pode ser gerado depois.

### Agente 3, consulta

O único com guardrail de entrada e de saída, memória (`DBChatSession` sobre
`ChatMessage`) e tools. As tools são **closures sobre `tenant_id`**: o modelo
nunca recebe um parâmetro de tenant, então o isolamento é estrutural.

Duas coisas importam mais que o prompt:

- **A checagem determinística de fundamentação** (`verificar_fundamentacao`).
  Um guardrail de LLM só recebe o TEXTO da resposta, então não tem como saber se
  ela veio do banco. A checagem olha o registro da execução: se nenhuma
  ferramenta foi chamada e a resposta não é cortesia curta nem o próprio
  fallback, a resposta é substituída pelo fallback.
- **Nenhuma ferramenta de escrita.** O agente é somente leitura por construção.
  É a defesa estrutural contra injeção vinda do conteúdo dos e-mails, que é
  texto de terceiro e chega até ele pelos resumos.

O texto só é liberado para a tela depois do guardrail de saída E da checagem de
fundamentação, e por isso o streaming é simulado. Emitir os deltas brutos do
modelo tornaria as duas verificações decorativas.

## Testes

`pytest`. A regra absoluta: **nenhum teste toca a caixa de e-mails de
produção**. Três camadas independentes garantem isso, e qualquer uma sozinha já
barra o acidente:

1. **Trava de rede** em `tests/conftest.py` (autouse): qualquer tentativa de
   falar com `graph.microsoft.com` ou com o login do Entra ID faz o teste
   FALHAR. Cobre httpx e requests, este último porque o MSAL autentica por
   dentro dele.
2. **`addopts = -m "not graph_funcional"`** no `pytest.ini`: um `pytest` puro
   não consegue fazer chamada de rede.
3. **Fixture `graph_funcional`**: pula o teste se `TESTER_GRAPH_SENDER_EMAIL`
   estiver ausente ou for igual a `GRAPH_SENDER_EMAIL`.

A trava cede a vez para o teste que pede a fixture `respx_mock`, porque o respx
patcha `httpx.Client.send`, acima do transporte onde ela mora. A cessão não abre
buraco: o respx roda com `assert_all_mocked` e rota não mockada levanta exceção
nele. Há um teste só para provar isso.

O banco da suíte é um SQLite temporário, apontado antes de qualquer import do
Reflex. Funciona porque o `load_dotenv()` do `rxconfig.py` não usa
`override=True`; se alguém acrescentar isso, a suíte passa a escrever no banco
de trabalho.

## E-mail e Microsoft Graph

Dois clientes, separados de propósito:

- `services/graph_mailer.py`: envio transacional (convite, redefinição de
  senha). Síncrono, porque roda dentro de event handler comum. Falha de envio
  nunca propaga: um e-mail que não sai não pode derrubar um convite.
- `services/graph_client.py`: leitura, marcação e movimentação. Assíncrono
  (httpx), porque roda em background task e no agendador, onde bloquear travaria
  o event loop. Toda requisição passa por um `_request` único.

Os dois compartilham `services/graph_auth.py`: uma implementação de token só,
porque o cache do MSAL vale por processo.

No Entra ID o registro precisa de **duas permissões de APLICAÇÃO** com
consentimento de administrador: `Mail.Send` para o envio e **`Mail.ReadWrite`**
para ler, marcar e mover (`Mail.Read` sozinha não basta). Use os dois botões do
card em `/admin` antes de depender de uma execução real: eles testam coisas
diferentes e falham de formas diferentes.

`graph_sender_email` precisa ser uma caixa REAL do locatário. Conta pessoal
(hotmail.com, outlook.com) nunca funciona no fluxo de aplicação, mesmo com as
credenciais certas: o Graph responde `ErrorInvalidUser`.

Recomendação de segurança: permissão de aplicação `Mail.ReadWrite` dá acesso a
TODAS as caixas do locatário. Restrinja o client ID à caixa comercial com uma
ApplicationAccessPolicy do Exchange Online.

Atenção ao nome: `IntegrationSetting.graph_tenant_id` é o Directory ID do Entra,
**sem relação** com o `tenant_id` da aplicação.

## Agendamento

`services/agendador.py`, com APScheduler. Sobe pelo **lifespan do ASGI**
(`app.register_lifespan_task`), e não em tempo de import: a distinção é o que
impede um `reflex compile` ou um `db makemigrations` de levantar um agendador
sem querer, já que os dois importam o módulo da aplicação mas não servem
requisição.

Três defesas contra disparo duplicado, porque nenhuma sozinha basta:

1. `classificacao_config.slot_devido` consulta `ultima_execucao_agendada` e
   decide se o horário já rodou hoje. É função pura, testada sem mexer no
   relógio.
2. Verificação no startup, além do gatilho no horário. Dá recuperação de graça:
   máquina desligada às 08:00 e servidor subindo às 09:20 ainda roda o horário 1.
3. `pg_try_advisory_lock` na reivindicação da rodada, cobrindo a corrida entre o
   botão manual e o job agendado, e o cenário de múltiplos workers. É mais forte
   que a flag `is_running` do State, que vive por sessão de browser e é
   invisível para a CLI. O lock morre com a conexão, então não substitui a
   recuperação de rodada travada de 20 minutos.

`scripts/classificar.py` roda a mesma execução fora da aplicação. Serve para
conferir com `--dry-run` antes de deixar o agente mexer numa caixa de produção,
para backfill controlado com `--desde`, e como saída de emergência: se o
APScheduler der problema no ambiente, basta apontar o Agendador de Tarefas do
Windows para ele, sem reescrever código.

## Custo

Um consumo medido, exibido em `/admin`: **tokens OpenAI** (`TokenUsage`) vezes o
preço **por modelo** (`TokenPricing`, uma linha por item de
`MODELOS_DISPONIVEIS`). Cada consumo grava em `TokenUsage.model` o modelo que o
gerou, então o custo é multiplicação exata, não rateio, e trocar o modelo de um
agente em `/admin` não reescreve o histórico. O banco guarda **USD por token**; a
UI pede **USD por 1M de tokens** (formato da tabela da OpenAI) e converte nas
duas pontas.

`TokenUsage.model` é texto desnormalizado, e não uma chave estrangeira. É por
isso que apagar linhas de `AgentModelSetting` (como `scripts/migrar_agentes.py`
faz com as chaves aposentadas) não estraga o custo histórico.

Trocar `MODELOS_DISPONIVEIS` (em `services/settings.py`) exige um UPDATE nas
linhas de `AgentModelSetting`, que o `ensure_*` nunca sobrescreve. Os preços não
precisam disso: a linha do modelo aposentado fica no banco de propósito, para o
custo passado continuar fechando.

O teto de custo de cada execução é `ClassificacaoConfig.max_emails_por_execucao`
(200 por padrão) e a janela é `lookback_horas`. A trava que impede pagar duas
vezes pelo mesmo e-mail é o `UNIQUE (tenant_id, internet_message_id)`: um e-mail
já conhecido é pulado sem nenhuma chamada ao modelo, inclusive os `ignorado`,
que existem no banco justamente para isso.
