@AGENTS.md

# Plataforma Coester de Prospecção

Ferramenta **interna** do Grupo Coester para prospecção, enriquecimento e
priorização de leads B2B. Não é um SaaS: nasceu como produto comercial
multi-tenant (marca "Orion Prospect Brain", planos, Stripe, cadastro público) e
foi convertida para uso interno fechado. Se você encontrar qualquer resquício
de billing, planos ou OAuth de terceiros, é sobra da conversão — não reintroduza.

## Invariantes do produto

Estas cinco regras são decisões de negócio, não detalhes de implementação.
Mudá-las exige pedido explícito do usuário:

1. **Uma única organização.** Existe exatamente uma linha em `Tenant`
   ("Coester", id=1). A coluna `tenant_id` continua nas tabelas operacionais e
   as queries continuam filtrando por ela — com um tenant só o filtro é inócuo,
   e mantê-lo evitou reescrever centenas de queries. **Nunca** apague dados por
   `tenant_id`: isso destruiria a base inteira da empresa.
2. **Acesso somente por convite.** Não há cadastro público nem login social.
   Um super admin convida (`AdminState.create_user`) informando nome, e-mail e
   classe; **nenhuma senha é definida no convite** — o convidado escolhe a dele
   por um link de uso único de 24h (`User.reset_token`). Super admin pode
   promover outros super admins.
3. **20 consultas por usuário por mês**, em CADA etapa do funil (pesquisa,
   enriquecimento, priorização contam separadamente). Constante
   `CONSULTA_LIMIT_MENSAL` em `state.py`; a contagem filtra por
   `<Run>.user_email` e ignora `status == "error"`. Super admin é ilimitado.
4. **Produtos são compartilhados e sem limite.** O catálogo é da organização,
   visível para todos os usuários.
5. **Contatos por empresa no enriquecimento: `CONTACT_LIMIT = 4`**, inclusive
   para super admin — cada contato é ~R$ 0,49 pagos à KipFlow.

## Como rodar

```bash
pip install -r requirements.txt
python -m reflex db migrate      # aplica as migrations
python scripts/seed.py           # tenant Coester + primeiro super admin
python -m reflex run --env prod --single-port
```

Migrations são geradas por `python -m reflex db makemigrations --message "..."` —
o wrapper do Reflex já liga a metadata do SQLModel; **não** edite `alembic/env.py`
à mão. Antes de qualquer alteração de schema, leia a skill `reflex-docs`.

### Ao terminar de verificar: derrube o server e limpe os dados de teste

Regra permanente, não precisa ser pedida a cada vez. Todo server que você subir
para conferir uma alteração deve ser encerrado ao fim da tarefa, e todo dado
criado para testar deve sair do banco. O estado que você deixa é o estado em que
o usuário encontra o projeto.

Derrubar de verdade exige atenção a dois detalhes deste ambiente:

- **O granian roda em processo filho.** Ao matar o pai, o filho herda o socket e
  continua servindo — `Get-NetTCPConnection` segue apontando o PID do pai morto,
  e o `taskkill` responde "processo não encontrado" enquanto a porta responde
  200. Procure também por `ParentProcessId` e por linhas de comando
  `multiprocessing.spawn`, que não citam "reflex".
- **Confirme pela porta, não pela lista de processos:** `curl` em 3000/3001/3002
  até não haver resposta. Uma instância esquecida trava o `node_modules` e o
  próximo `reflex run` falha com `EBUSY`.

Sobre os dados: apague o que você inseriu e **restaure o que alterou** (um preço
de teste gravado em `TokenPricing` fica com cara de configuração real). Nunca
limpe por `tenant_id` — ver invariante 1. E não confunda dado de teste com
histórico legítimo: `ActivityLog` registra ações reais do usuário no navegador.

## Arquitetura

| Camada | Onde | Observação |
|---|---|---|
| Modelos | `prospect_agent/models.py` | SQLModel; `brt_now()` é o padrão de timestamp (UTC-3) |
| Estado + event handlers | `prospect_agent/state.py` | arquivo grande e central |
| Regras e integrações | `prospect_agent/services/` | funções puras e clientes HTTP |
| UI | `prospect_agent/pages/`, `components/` | |
| Rotas | `prospect_agent/prospect_agent.py` | `/`, `/login` e `/admin` à mão; o resto via `@rx.page` |

Pipeline preservado da versão SaaS e **fora de escopo de qualquer conversão**:
pesquisa ICP → enriquecimento KipFlow/Receita → priorização → approach →
insights. Ver `CONTRATO_PESQUISA.md` e `CONTRATO_ENRIQUECIMENTO.md`.

Toda pesquisa pergunta antes de rodar se as empresas que a base já tem devem
entrar de novo (para renovar as notícias) ou não. Nos dois casos a base
conhecida vai para o agente como lista de exclusão — o orçamento de buscas é o
recurso escasso da etapa. Quais empresas pertencem à "mesma linha de pesquisa" é
decidido por um classificador de linguagem em `services/search_scope.py`,
comparando região e segmento por significado e não por texto igual, porque os
dois campos são digitados à mão a cada rodada.

## Convenções que economizam tempo

- **Esta versão do Reflex NÃO gera setters automáticos.** Todo campo ligado a um
  `on_change`/`on_open_change` precisa do seu `set_<campo>` declarado à mão no
  State. Esquecer isso quebra em runtime, não na compilação.
- **Segredos nunca chegam ao State.** `IntegrationSetting` guarda segredos
  cifrados com Fernet (`services/crypto.py`, chave em `SETTINGS_ENCRYPTION_KEY`).
  A UI lê apenas o booleano `*_configurado` — o State é serializado para o
  browser. Campo em branco ao salvar **preserva** o valor atual.
- **Toasts, não `window_alert`**: use `toast_success` / `toast_error` de
  `state.py` em toda ação de salvar/criar/atualizar.
- **Links de e-mail** saem de `base_url()` (`APP_BASE_URL` no `.env`) — nunca
  escreva `http://localhost:3000` no código.
- **Handlers em background** (`@rx.event(background=True)`) precisam tirar um
  snapshot de `self.*` dentro do `async with self:` antes de rodar o trabalho
  longo; nada de ler o State fora do lock.
- `foreach` do Reflex não acessa dict aninhado tipado — achate os dados em
  campos de topo no serviço antes de mandar para a UI.
- **Nenhum travessão (`—`) em texto que o usuário lê.** Vale para as strings da
  UI, e-mails e relatórios, e também para o que os agentes escrevem: cada agente
  de texto livre injeta `REGRA_SEM_TRAVESSAO` (`services/prompt_rules.py`) nas
  instruções, porque boa parte do que aparece na tela é gerado em runtime.
  Comentário e docstring de código não contam. Onde o travessão era só
  marcador de valor ausente numa tabela, o substituto é `-`.
- Validação rápida sem subir o servidor: `python -m reflex compile --dry`.

## E-mail

Envio pela **Microsoft Graph API** (`services/graph_mailer.py`), configurado
pelo super admin em `/admin`. Requer no Entra ID a permissão de **aplicação**
`Mail.Send` com consentimento de administrador, e um `graph_sender_email` que
seja caixa real do locatário. Há um botão "Enviar e-mail de teste" no painel —
use-o antes de depender de um convite real.

Atenção ao nome: `IntegrationSetting.graph_tenant_id` é o Directory ID do Entra,
**sem relação** com o `tenant_id` da aplicação.

Falha de envio nunca propaga: um e-mail que não sai não pode derrubar um convite
ou uma redefinição de senha.

## Custo

Três consumos são medidos e exibidos em `/admin`:

- **Tokens OpenAI** (`TokenUsage`) × preço **por modelo** (`TokenPricing`, uma
  linha por item de `MODELOS_DISPONIVEIS`). Cada consumo grava em
  `TokenUsage.model` o modelo que o gerou, então o custo é multiplicação exata,
  não rateio — e trocar o modelo de um agente em `/admin` não reescreve o
  histórico. O banco guarda **USD por token**; a UI pede **USD por 1M de
  tokens** (formato da tabela da OpenAI) e converte nas duas pontas.
- **KipFlow** (`KipflowUsage`), em BRL, valor real vindo da própria resposta da
  API.
- **Hunter.io** (`HunterUsage`), em **créditos**, não em dinheiro: o Hunter
  vende um pacote mensal. Uma busca que não acha e-mail não consome crédito
  (por isso a tabela guarda a tentativa e o crédito em colunas separadas, e o
  card soma `creditos`, não linhas).

`HunterUsage` é o único contador que **não** é só indicador: a soma do ciclo é o
gate que autoriza a próxima chamada (`hunter_client.creditos_restantes`). Por
isso ele fica de fora do botão "Limpar contadores" — zerá-lo faria a plataforma
buscar e-mails além do pacote contratado.

O ciclo **não é o mês civil**: o Hunter renova no aniversário da assinatura.
`IntegrationSetting.hunter_dia_renovacao` guarda esse dia e
`hunter_client.inicio_do_ciclo` calcula a janela (dia que o mês não comporta,
como 31 em fevereiro, cai no último dia). Contar por mês civil deslocaria a
janela do ciclo real e bloquearia na hora errada.

O crédito do Hunter é vendido **por conta**, então a plataforma aceita até
`HUNTER_MAX_CONTAS` (8) contas em `HunterAccount` e distribui as buscas entre
elas (`hunter_client.Balanceador`): a busca vai sempre para a conta com mais
crédito sobrando no ciclo, e uma conta que devolve 429 (cota) ou 401 (chave
errada) sai da roda daquela rodada em vez de derrubar a etapa. Disso decorrem
duas coisas que é fácil errar:

- o teto de `IntegrationSetting.hunter_creditos_mensais` é **por conta** (50 =
  plano gratuito); o orçamento do ciclo é ele vezes as contas configuradas
  (`get_hunter_creditos_totais`);
- `HunterUsage.account_slot` diz qual conta pagou. Sem contar por conta, o
  saldo global mentiria: com duas contas de 50 e 50 créditos gastos numa delas,
  ainda cabem 50 buscas no ciclo, mas nenhuma naquela conta.

O dia da renovação é um só para todas as contas, porque elas são criadas no
mesmo dia de propósito. Tudo isso é editável em `/admin`: migrar de plano ou
acrescentar uma conta não pode exigir deploy.

A trava de custo dessa etapa é `CompanyContact.email_buscado_em`, gravada na
**tentativa** e não no sucesso: sem ela, reprocessar a mesma pesquisa gastaria a
cota inteira de novo nos mesmos contatos.

Trocar `MODELOS_DISPONIVEIS` (em `services/settings.py`) exige um UPDATE nas
linhas de `AgentModelSetting`, que o `ensure_*` nunca sobrescreve — ver
`scripts/migrar_modelos.py`. Os preços não precisam disso: a linha do modelo
aposentado fica no banco de propósito, para o custo passado continuar fechando.
