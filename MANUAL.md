# Manual do Agente de Suporte ao Comercial

Este documento tem duas partes independentes. A **Parte 1** é para quem instala
e mantém a aplicação. A **Parte 2** é para quem usa o sistema no dia a dia e não
precisa saber nada da Parte 1.

---

# Parte 1: Deploy

## 1.1 O que é preciso ter antes

- **Python 3.12**
- **PostgreSQL** com um banco vazio criado (o padrão do projeto é
  `sales_support_agent`)
- **Chave de API da OpenAI**
- **Um registro de aplicativo no Entra ID** com as permissões descritas abaixo
- **Uma caixa de correio corporativa** para o agente monitorar

### Registro no Entra ID

O agente fala com a Microsoft Graph pelo fluxo de **aplicação** (client
credentials), sem usuário. No portal do Entra ID:

1. **Registros de aplicativo** e "Novo registro". Dê um nome e registre.
2. Anote o **ID do aplicativo (cliente)** e o **ID do diretório (locatário)**.
3. Em **Certificados e segredos**, crie um **novo segredo do cliente** e copie o
   VALOR na hora: ele não é exibido de novo.
4. Em **Permissões de API**, adicione **permissões de aplicativo** (não
   delegadas) para o Microsoft Graph:
   - **`Mail.Send`**, para enviar os convites e as redefinições de senha.
   - **`Mail.ReadWrite`**, para ler a caixa, aplicar categorias e mover
     mensagens. **`Mail.Read` sozinha não basta**: ela não permite marcar nem
     mover.
   - Opcionalmente **`MailboxSettings.ReadWrite`**, só para que as categorias
     apareçam coloridas no Outlook. Sem ela tudo funciona, as categorias apenas
     saem sem cor.
5. Clique em **Conceder consentimento do administrador**. Sem esse passo o Graph
   responde 403 e nada funciona.

### Duas coisas que costumam dar errado aqui

**A caixa precisa ser real e do locatário.** O fluxo de aplicação só enxerga
caixas de dentro do próprio locatário. Uma conta pessoal (`hotmail.com`,
`outlook.com`, `gmail.com`) responde `ErrorInvalidUser` e **nenhuma credencial
resolve**. Se o teste de leitura falhar com essa mensagem, o problema é o
endereço, não o segredo.

**`Mail.ReadWrite` de aplicação dá acesso a TODAS as caixas do locatário.**
Restrinja o registro à caixa comercial com uma **ApplicationAccessPolicy** do
Exchange Online. É um comando único no Exchange Online PowerShell, e sem ele um
segredo vazado lê o e-mail da empresa inteira:

```powershell
New-ApplicationAccessPolicy -AppId <CLIENT_ID> `
  -PolicyScopeGroupId <grupo-ou-caixa@dominio> `
  -AccessRight RestrictAccess `
  -Description "Agente de Suporte ao Comercial: so a caixa do comercial"
```

## 1.2 Instalação

O código fica no GitHub, no repositório **`agentes_consulta`**.

```bash
git clone https://github.com/<organizacao>/agentes_consulta.git
cd agentes_consulta

python -m venv .venv
.venv/Scripts/activate            # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
```

**O repositório não traz nenhuma credencial, e isso é proposital.** O que ele
tem é o `.env.example`, que é a lista das variáveis com explicação de cada uma e
todos os valores em branco. Três coisas ficam de fora do clone e precisam ser
providenciadas em cada ambiente:

| O que falta | Onde vem |
|---|---|
| O arquivo `.env` | Você cria a partir do `.env.example`, na seção 1.3 |
| O banco de dados | PostgreSQL vazio, com o schema aplicado na seção 1.4 |
| O primeiro super admin | Criado pelo `seed`, também na seção 1.4 |

O `.env` está no `.gitignore` e **nunca** deve ser commitado. Se um dia for
preciso compartilhar configuração entre duas máquinas, compartilhe o
`.env.example` preenchido por fora do repositório, nunca pelo Git.

## 1.3 Configuração

Copie `.env.example` para `.env` e preencha. As variáveis obrigatórias:

| Variável | Para que serve |
|---|---|
| `DATABASE_URL` | Conexão com o PostgreSQL. É a **única** configuração que fica só no `.env`, pelo motivo óbvio: não dá para guardar no banco o endereço do banco. |
| `SETTINGS_ENCRYPTION_KEY` | Chave Fernet que cifra o segredo do Graph guardado no banco. Gere com o comando abaixo. Trocá-la invalida todos os segredos já gravados. |
| `OPENAI_API_KEY` | Chave da OpenAI. |
| `APP_BASE_URL` | Endereço público da aplicação. É a partir dele que os links de convite e de redefinição de senha são montados. |
| `SUPER_ADMIN_EMAIL` / `SUPER_ADMIN_NOME` / `SUPER_ADMIN_SENHA` | Identidade do primeiro super admin. Só usadas na primeira execução do `seed`. |

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

As credenciais do Graph (`GRAPH_SENDER_EMAIL`, `GRAPH_TENANT_ID`,
`GRAPH_CLIENT_ID`, `GRAPH_CLIENT_SECRET`) podem entrar no `.env` para o `seed`
já as gravar cifradas, ou serem preenchidas depois em `/admin`. Depois da
primeira execução, o banco é a fonte da verdade e o `.env` deixa de ser
consultado para elas.

O bloco `TESTER_GRAPH_*` é opcional e existe só para os testes funcionais.
**Ele precisa apontar para uma caixa DIFERENTE da de produção**: a suíte se
recusa a rodar se as duas forem iguais.

## 1.4 Banco de dados e primeiro acesso

```bash
python -m reflex db migrate
python scripts/seed.py
```

O `seed` cria a organização, o primeiro super admin, as linhas de configuração
dos agentes e as quatro linhas de pasta (com nome sugerido e sem identificador
ainda). Ele é idempotente: rodar de novo não duplica nada nem sobrescreve uma
senha já trocada.

**O primeiro super admin sai de três variáveis do `.env`**, e nenhuma delas tem
valor padrão no código:

| Variável | Obrigatória |
|---|---|
| `SUPER_ADMIN_EMAIL` | Sim. Sem ela o `seed` para e explica o que fazer. |
| `SUPER_ADMIN_NOME` | Não. Faltando, o próprio endereço vira o rótulo até ser editado em `/profile`. |
| `SUPER_ADMIN_SENHA` | Sim, e **só na primeira execução**, quando há usuário a criar. |

Não há padrão de propósito. Um e-mail embutido no código publicaria o endereço
de uma pessoa no repositório e, pior, criaria em silêncio um super admin com a
identidade de alguém de outra empresa numa instalação em que a variável ficasse
por preencher. Uma senha embutida seria uma credencial publicada, e uma senha
genérica ("admin", "changeme") viraria a senha de produção de quem esquecesse de
trocá-la.

Só o hash bcrypt vai para o banco; a senha em texto puro não é gravada em lugar
nenhum. Se preferir não deixá-la em arquivo, passe na hora:

```bash
SUPER_ADMIN_SENHA="uma-senha-forte" python scripts/seed.py
```

**Troque a senha no primeiro acesso, em `/profile`, e apague a
`SUPER_ADMIN_SENHA` do `.env` depois.** Ela não é mais lida: da segunda execução
em diante o `seed` encontra o usuário e nunca reescreve a senha dele.

Rodar o `seed` de novo também é o caminho de **resgate** quando a organização
fica sem super admin: a permissão é restaurada sem tocar na senha.

Se estiver migrando da versão anterior da plataforma, rode também:

```bash
python scripts/migrar_agentes.py
```

## 1.5 Subir a aplicação

```bash
python -m reflex run --env prod --single-port
```

## 1.6 Verificação pós-deploy

1. Abra `/` e confira que a landing carrega.
2. Entre com o super admin. Você deve cair em `/admin`.
3. Em `/admin`, preencha a seção de Integrações com as credenciais do Graph e
   clique em **Salvar**.
4. Clique em **"Enviar e-mail de teste"**. Ele confere a permissão `Mail.Send`.
5. Clique em **"Testar leitura da caixa"**. Ele confere a `Mail.ReadWrite`, e
   deve responder com a quantidade de pastas encontradas.

   Os dois botões existem porque são permissões diferentes e falham de formas
   diferentes. Um envio bem-sucedido não diz nada sobre a leitura, e sem o
   segundo teste o primeiro sinal de um consentimento faltando seria a execução
   automática das 08:00 falhando, num horário em que ninguém está olhando.
6. Vá em `/dashboard`, vincule as quatro pastas e rode o `--dry-run` da seção
   1.8 antes de deixar o agente mexer na caixa.

## 1.7 Configurações que só existem em `/admin`

- **Modelo e esforço de cada um dos três agentes.** Trocar aqui vale na próxima
  execução, sem reiniciar o servidor.
- **Preço do token por modelo**, em dólares por 1 milhão de tokens (o formato da
  tabela da OpenAI). O banco guarda por token e a conversão é feita nas duas
  pontas.
- **Credenciais da Microsoft Graph** e a pasta de origem da varredura. Deixe
  `inbox` para a Caixa de Entrada: o nome bem-conhecido funciona em qualquer
  idioma do locatário, o que um nome exibido não faz.
- **Convite e gestão de usuários.**
- **Limpar contadores**, que apaga o histórico de consumo de tokens. Não toca em
  e-mails, execuções nem logs.

## 1.8 A linha de comando

`scripts/classificar.py` roda a mesma execução da aplicação, por fora dela.

```bash
# Confere o que o agente FARIA, sem escrever nada (nem no Outlook, nem no banco)
python scripts/classificar.py --manual --dry-run

# Execução manual de verdade
python scripts/classificar.py --manual

# Só roda se algum horário configurado estiver vencido hoje
python scripts/classificar.py --agendado

# Backfill controlado: amplia a janela só nesta execução
python scripts/classificar.py --manual --desde 2026-07-01
```

O `--dry-run` é o jeito seguro de conferir a primeira execução numa caixa de
produção. O `--agendado` é a saída de emergência: se o agendador interno der
problema no ambiente, basta criar uma tarefa no Agendador de Tarefas do Windows
apontando para ele a cada 15 minutos, com o caminho absoluto do
`.venv\Scripts\python.exe`, marcando "executar estando o usuário conectado ou
não".

## 1.9 Atualizações

```bash
git pull
pip install -r requirements.txt
python -m reflex db migrate
python -m reflex run --env prod --single-port
```

## 1.10 Problemas comuns

| Sintoma | Causa provável |
|---|---|
| "A caixa de e-mails configurada não existe neste locatário" | O endereço é de uma conta pessoal, ou não existe. O fluxo de aplicação só enxerga caixas do locatário. |
| "O registro de aplicativo precisa da permissão Mail.ReadWrite" | Falta a permissão, ou falta o consentimento do administrador. |
| "Credenciais da Microsoft Graph inválidas ou expiradas" | Segredo do cliente errado ou vencido. Segredos do Entra ID expiram. |
| A classificação não começa e reclama de pastas | Alguma das quatro classes está sem pasta vinculada. Vincule em `/dashboard`. |
| A execução automática não dispara | Confira se a configuração está ativa e se os horários estão em `HH:MM`. Um horário inválido é ignorado, e o outro continua funcionando. |
| `EBUSY` ao subir o servidor | Ficou uma instância antiga rodando. Confirme pela porta, não pela lista de processos, e procure o processo filho do granian. |

---

# Parte 2: Manual de uso

## 2.1 Como entrar

Você recebe um convite por e-mail com um link que vale **24 horas e só pode ser
usado uma vez**. Clicando nele, você escolhe a sua própria senha. Ninguém, nem
quem convidou, conhece essa senha.

Se o link expirar, peça um novo convite ou use "Esqueceu sua senha?" na tela de
login.

## 2.2 O que o sistema faz

Duas vezes por dia, nos horários que o time define, a plataforma lê a caixa de
e-mails do comercial e, para cada mensagem nova:

1. **Classifica** em uma de quatro categorias:
   - **Pedido**: o cliente está comprando, mandando ordem de compra.
   - **Proposta**: o cliente pede orçamento ou cotação de algo ainda não
     comprado.
   - **Revisão de pedido**: mexe num pedido que já existe (mudança de
     quantidade, de prazo, cancelamento, cobrança de andamento).
   - **Revisão de proposta**: mexe num orçamento já enviado (desconto, revisão
     de escopo, questionamento de preço).
2. **Marca como urgente** se o e-mail pede entrega ou resposta dentro da janela
   configurada.
3. **Aplica a categoria** no Outlook e **move** a mensagem para a pasta daquela
   classe.
4. **Resume** o e-mail: o que o cliente quer, os pontos principais e o próximo
   passo.

**E-mail que não se encaixa em nenhuma das quatro classes não é tocado.** Ele não
recebe marcação, não é movido e continua na caixa de entrada. Newsletter,
boleto, currículo e convite de reunião ficam onde estavam.

## 2.3 O dashboard

É a tela principal, em `/dashboard`.

### Os quatro indicadores do topo

- **Duração média das execuções** e **duração da última**. Enquanto não houver
  nenhuma execução concluída, os dois mostram `-`.
- **E-mails classificados**, no acumulado, e **na última execução**.

### Zerar contadores

O botão discreto abaixo dos indicadores apaga **todas** as execuções, os e-mails
classificados e os resumos, devolvendo o painel ao estado de instalação nova.
Serve para começar do zero depois de uma bateria de testes. Ele pede confirmação
antes, e a ação não pode ser desfeita.

Três coisas que ele **não** faz:

- **Não desfaz nada no Outlook.** O que já foi arquivado continua nas pastas,
  com as categorias que recebeu. Se quiser desfazer, mova à mão.
- **Não apaga o histórico de custo** em `/admin`. O gasto aconteceu de verdade.
- **Não mexe na configuração** nem nas pastas vinculadas.

E uma que ele faz, e que custa dinheiro: é o registro apagado aqui que impede
pagar duas vezes pelo mesmo e-mail. Depois de zerar, a próxima execução
reclassifica tudo o que estiver dentro da janela de varredura e cobra de novo
por isso. Zerar com a janela em 48h é diferente de zerar com ela em 480.

Vale para toda a equipe: a caixa é compartilhada e o painel é o mesmo para
todos.

### Configuração das execuções

- **Primeiro e segundo horário**: quando a classificação roda sozinha.
- **Janela de urgência (horas)**: um e-mail é marcado como urgente quando pede
  entrega ou resposta dentro desse prazo. Mudar esse número **revê a marcação
  dos e-mails já classificados na hora**, sem reprocessar nada.
- **Varrer as últimas (horas)**: quanto tempo para trás cada execução olha.

**Esta configuração é da organização, não sua.** A caixa é uma só, então os
horários e a janela valem para todo mundo: o que um usuário salvar, os demais
passam a ver ao abrir o painel. Por isso o cartão mostra, embaixo do botão de
salvar, quem alterou por último e quando. Se você encontrar horários que não
reconhece, é ali que está a resposta.

A linha de autoria só aparece depois que alguém salva. Numa instalação que vem
de antes desse registro não há o que mostrar, e o espaço fica vazio até a
primeira alteração.

O disparo automático **não** conta como alteração: ele escreve na mesma
configuração duas vezes por dia para marcar que já rodou, mas não vira "alterado
por" nem mexe na data exibida.

#### Quando duas pessoas mexem ao mesmo tempo

A tela **não** se atualiza sozinha quando um colega salva. É de propósito:
trocar os campos no meio da digitação apagaria o que você está escrevendo.

O que acontece é isto:

1. Aparece um aviso amarelo em cima dos campos, dizendo quem alterou, com o
   botão **"Recarregar configuração"**. Só ele troca o que está na tela, e só
   quando você clicar.
2. Se você salvar sem recarregar, a gravação é **recusada**, com uma mensagem
   nomeando quem alterou e quando.

A recusa é o ponto importante. Sem ela, quem estivesse com o painel aberto
desde antes gravaria os valores velhos que ainda estão na tela e desfaria, em
silêncio, o que o colega acabou de configurar. Pior, a linha de autoria passaria
a creditar a pessoa errada, e a única pista seria alguém reparar depois que o
horário voltou sozinho.

Ao ver o aviso, recarregue, confira se a configuração do colega serve, e só
então mude o que você precisava mudar.

Os botões **Iniciar** e **Parar** não são recusados nunca, mesmo com o aviso na
tela. Eles mexem num interruptor só e não têm como desfazer horário nenhum, e
travar um "Parar" por causa de uma tela velha impediria alguém de frear o agente
pelo motivo errado.

### Por que o automático não rodou

Abaixo dos botões o painel mostra sempre a **última verificação** do agendador,
que acontece a cada 10 minutos. É ali que está a resposta, e ela tem três
formas:

| O que aparece | O que significa |
|---|---|
| "Última verificação há 4 min. Nada a fazer: nenhum horário vencido." | Está tudo certo. O agente conferiu e não era hora de rodar. |
| Faixa amarela: "As execuções automáticas estão PARADAS." | Alguém apertou Parar. **Salvar os horários não liga o agente.** Use o botão Iniciar, que fica na própria faixa. |
| Faixa vermelha: "A última verificação foi há 50 min... o servidor precisa ser verificado." | O processo não está acordando. Nenhum botão desta tela resolve: chame quem cuida do servidor. |

Essa distinção existe porque os três casos eram o mesmo silêncio antes, e foi
por isso que a caixa passou dias sem ser classificada sem ninguém entender o
motivo.

Quando uma execução automática termina em erro (a OpenAI fora do ar, a internet
caindo), a mensagem do erro também aparece ali, com o horário.

### O prompt do classificador

O card **Instruções do classificador** mostra e permite editar o texto que
decide a classe de cada e-mail e, por consequência, a pasta para onde ele vai.
Ele nasce fechado; clique em "Ver e editar".

Qualquer usuário pode editar, não só o super admin. É proposital: quem conhece
o vocabulário da casa é quem convive com os e-mails.

Ao salvar:

- o texto passa a valer **na próxima execução**, sem reiniciar nada;
- o card passa a mostrar **"Versão N"** no lugar de "Padrão do sistema";
- fica registrado **quem salvou e quando**, embaixo do título;
- a ação entra no log de atividades, visível em `/admin`.

Os trechos entre chaves (`{janela_horas}`, `{_ABRE}`, `{_FECHA}`,
`{REGRA_SEM_TRAVESSAO}`) são substituídos automaticamente na hora de usar.
Apagá-los não quebra a execução, mas o agente perde a informação que eles
carregam, então mantenha-os salvo se souber o que está fazendo.

Três saídas quando algo der errado:

- **Descartar alterações** joga fora o que está na caixa e recarrega o que está
  gravado.
- **Restaurar padrão** volta ao texto que vem com o sistema. Ele não pode ser
  desfeito, então copie o texto atual antes se quiser guardá-lo.
- Se um colega salvar enquanto você edita, o seu Salvar é **recusado**, com o
  nome de quem salvou, pelo mesmo motivo da configuração: aqui o que se perderia
  não é um horário, é uma calibragem inteira.

O prompt não pode ficar vazio. Para voltar ao original, use Restaurar padrão.

### Pastas do Outlook

Uma linha por classe. Você digita o **nome** da pasta e clica em "Vincular"; o
sistema descobre o identificador dela sozinho. O botão "Listar pastas da caixa"
mostra o que existe no Outlook, para você não precisar adivinhar.

Se houver duas pastas com o mesmo nome em lugares diferentes, o sistema não
escolhe por você: ele mostra os dois caminhos e pede que você informe o caminho
completo. Escolher sozinho arquivaria e-mail na pasta errada em silêncio.

**Enquanto alguma classe estiver sem pasta, a classificação não roda.** Isso é
proposital: começar e parar no meio deixaria parte dos e-mails arquivada e parte
na caixa de entrada, que é o pior dos dois mundos.

### Iniciar e parar as execuções automáticas

Definir os horários **não** liga o agente. Configurar quando ele rodaria é uma
coisa; autorizá-lo a mexer na caixa é outra. Quem liga é o botão **"Iniciar
automático"**, e o painel mostra o estado atual com um selo: "Automático parado"
ou "Em execução automática", com a data e a hora do próximo disparo.

Enquanto estiver parado, nenhum horário dispara, nem quando o servidor é
reiniciado. Uma instalação nova nasce parada.

**"Parar automático"** desliga os dois horários. Ele não interrompe uma execução
em andamento: ela termina e nenhuma outra começa. Parar no meio deixaria parte
dos e-mails arquivada e parte na caixa de entrada.

Só é possível iniciar depois que as quatro pastas estiverem vinculadas e os dois
horários forem válidos. Ligar com pasta faltando agendaria duas falhas por dia,
nos horários em que ninguém está olhando o painel.

### Classificar agora

Roda a classificação na hora, sem esperar o horário. É exatamente a mesma
execução que roda sozinha.

**Este botão funciona mesmo com o automático parado**, e é de propósito: é assim
que se confere a configuração, uma vez, antes de soltar o agente para rodar
todo dia. A ordem recomendada é vincular as pastas, clicar em "Classificar
agora", conferir o resultado na caixa, e só então clicar em "Iniciar
automático".

### Acompanhar uma execução

Enquanto a classificação roda, aparece uma **barra de progresso** com o número
de e-mails já processados e o assunto do que está sendo classificado agora.
Vale para as duas origens: o botão "Classificar agora" e a execução automática.
No automático o painel diz "Execução automática em andamento", para você saber
que não foi ninguém que clicou.

Quando termina, um aviso verde mostra o resultado (quantos classificados,
ignorados e já conhecidos) e **a lista se atualiza sozinha**. Se a execução
falhar, o aviso é vermelho e traz o motivo.

O painel descobre isso consultando o banco de tempos em tempos, e não pela sua
sessão: por isso a execução automática aparece mesmo que você não tenha tocado
em nada.

### Atualizar a lista

O botão "Atualizar", acima da tabela, recarrega os indicadores e a lista na
hora. O painel já se atualiza sozinho ao fim de cada execução; este botão é
para quando você quiser conferir agora, sem esperar.

### Urgente e Importante

São duas faixas, e um e-mail nunca tem as duas:

- **Urgente**: o e-mail pede entrega ou resposta **dentro da janela**
  configurada, ou trata o assunto como urgente sem dar data ("estamos parados
  esperando").
- **Importante**: o e-mail tem uma data, mas ela está **além da janela**. Ter
  data é compromisso assumido, mesmo distante. Antes esse caso ficava
  indistinguível de um e-mail sem data nenhuma e só reaparecia quando já era
  tarde.

Apertar a janela **rebaixa** um urgente para importante; ela nunca apaga a
prioridade de um e-mail que tem data.

O painel de Urgências mostra só os urgentes, de propósito: ele é a fila do que
precisa ser atendido agora.

### As categorias no Outlook

Cada e-mail classificado recebe a categoria da classe (Pedido, Proposta,
Revisão de pedido, Revisão de proposta), mais **"IA"**, mais "Urgente" ou
"Importante" quando for o caso.

A marca "IA" existe para você distinguir, de olho, o que a plataforma arquivou
do que alguém arquivou à mão. É também o que permite achar tudo o que o agente
tocou, com uma busca por categoria, caso uma execução saia errada. E-mail que
não se encaixou em nenhuma classe **não** recebe essa marca: ele não foi tocado.

O nome é curto porque as categorias dividem a mesma coluna do Outlook: um
e-mail classificado leva sempre duas e às vezes três, e um rótulo longo
espremia as outras. Ela se chamava "Classificado por IA" antes. Para trocar o
nome nas mensagens já arquivadas:

```bash
python scripts/renomear_categoria_ia.py --dry-run   # mostra o que faria
python scripts/renomear_categoria_ia.py             # aplica
python scripts/renomear_categoria_ia.py --reverter  # volta ao nome antigo
```

Ele preserva todas as outras categorias de cada mensagem, inclusive as que você
marcou à mão, e não gasta token nenhum.

As categorias aparecem sem cor no Outlook enquanto a permissão
`MailboxSettings.ReadWrite` não for concedida no Entra ID. É opcional e não
afeta a classificação.

A plataforma **nunca remove** categoria: ela lê as que já existem e manda a
união. O que você marcou à mão no Outlook continua lá.

Se a marca "IA" for acrescentada depois que a caixa já tem e-mails arquivados,
eles não a recebem sozinhos: e-mail já conhecido é pulado sem nenhuma chamada
ao modelo. Para aplicá-la ao que já está classificado:

```bash
python scripts/aplicar_categoria_ia.py --dry-run   # mostra o que faria
python scripts/aplicar_categoria_ia.py             # aplica
```

Não gasta token nenhum e pode ser repetido sem duplicar.

### Tirar um e-mail da lista de urgências

Cada item do painel de Urgências tem um botão de visto. Ele diz "já tratei
disto": o e-mail sai da fila e **nada é apagado**. Ele continua no banco, na
tabela de classificados, nas respostas da Consulta IA e no Outlook, com a
categoria "Urgente" intacta.

A fila é uma lista de trabalho, e não um relatório: o que já foi resolvido sai
dela. Por isso tirar da fila é diferente de deixar de ser urgente, e sobrevive a
uma mudança da janela de urgência.

Clicou sem querer? Abra o e-mail na tabela e use "Voltar para urgências".

### Excluir um e-mail do banco

O ícone de lixeira, na última coluna da tabela (ou o botão dentro do detalhe),
apaga aquele e-mail e o resumo dele. Pede confirmação antes.

Três coisas para saber:

- **A mensagem NÃO é apagada do Outlook.** Ela continua na pasta em que foi
  arquivada, com as categorias que recebeu. Isto aqui é só o registro do painel.
- **Ela some da Consulta IA também.** O chat responde a partir do mesmo banco.
- **Atenção ao custo**: é este registro que impede pagar duas vezes pelo mesmo
  e-mail. Se a mensagem voltar para a Caixa de Entrada dentro da janela de
  varredura, ela será reclassificada e cobrada de novo.

Para limpar tudo de uma vez, use "Zerar contadores" em vez de apagar linha a
linha.

### Urgências

Logo abaixo dos indicadores, a lista dos e-mails urgentes, do mais recente para
o mais antigo, já com o resumo. Clique em qualquer um para ver o detalhe.

### Tabela de e-mails classificados

Filtre por intervalo de datas e pelo indicador de urgente. As datas são
inclusivas nas duas pontas: filtrar "até 07/08" traz também o que chegou às 15h
do dia 7.

Clique numa linha para abrir o resumo completo: o que o cliente quer, os pontos
principais, o próximo passo sugerido, o prazo mencionado, e um link para abrir a
mensagem original no Outlook.

## 2.4 Consulta IA

Em `/consulta` você conversa com um agente sobre os e-mails já classificados.
Perguntas que ele responde bem:

- "Quais e-mails estão urgentes?"
- "O que chegou esta semana?"
- "Quantas propostas em aberto?"
- "Teve algum e-mail da Metalúrgica Silva?"
- "Aquele e-mail que falava de prazo de entrega, o que dizia?"
- "Quando rodou a última classificação?"

Duas coisas que valem saber:

**Ele só responde com o que está na base.** Se a informação não existir, ele diz
que não encontrou, em vez de inventar. Isso não é só uma instrução: a plataforma
verifica se a resposta veio de uma consulta de verdade e a substitui pela
mensagem de "não encontrei" quando não veio.

**Ele não escreve nada.** Não marca, não move, não envia e-mail, não altera
configuração. É somente leitura. Isso protege contra um e-mail malicioso que
tente dar ordens ao assistente: mesmo que o texto peça, não existe função que
ele possa usar para obedecer. Se um e-mail tiver esse tipo de conteúdo, o
assistente vai RELATAR isso para você, e não obedecer.

Perguntas fora do assunto (conhecimento geral, outros sistemas, pedidos de
código) são recusadas. Saudação e agradecimento, não: ele responde e oferece o
que sabe fazer. O botão "Limpar conversa" apaga o histórico do seu chat, que é
individual.

Se a verificação de escopo estiver fora do ar, a pergunta passa em vez de ser
recusada. É a escolha certa aqui: o assistente não tem nenhuma função de
escrita, então uma pergunta a mais passando não causa dano, enquanto uma
recusa indevida deixa você sem a ferramenta.

## 2.5 Meu Perfil

Em `/profile` você atualiza a foto e troca a senha.

## 2.6 Para super admins

Além de tudo acima, você tem `/admin`:

- **Consumo de tokens** da OpenAI, em dólares, com o preço de cada modelo e o
  gráfico dos últimos 6 meses. Uma pergunta na Consulta IA custa mais do que
  parece: são quatro chamadas ao modelo por pergunta, porque a verificação de
  escopo roda na entrada e na saída. Pergunta recusada também custa, e também
  aparece aqui.
- **Configuração dos três agentes**: qual modelo e qual esforço de raciocínio
  cada um usa. Vale na próxima execução, sem reiniciar nada.
- **Preço do token por modelo**, em dólares por 1 milhão de tokens.
- **Integração com a Microsoft Graph**, com os dois botões de teste.
- **Usuários**: convidar, editar, promover a super admin, remover.
- **Feed de atividades**: o registro do que foi feito na plataforma.
- **Limpar contadores**: apaga o histórico de consumo de tokens. Não toca em
  e-mails, execuções nem usuários.

Ao convidar alguém, você informa nome, e-mail e classe. **Você não define
senha**: a pessoa escolhe a dela pelo link.

## 2.7 Perguntas frequentes

**Um e-mail foi para a pasta errada. O que faço?**
Mova à mão no Outlook. A plataforma não o classifica de novo, mesmo que ele
volte para a caixa de entrada: cada e-mail é processado uma vez só, e isso é o
que evita gastar duas vezes com a mesma mensagem.

**Mudei a janela de urgência. Preciso reclassificar?**
Não. A marcação dos e-mails já classificados é revista na hora em que você
salva.

**Um e-mail importante ficou na caixa de entrada sem marcação.**
Ele não se encaixou em nenhuma das quatro classes. Na dúvida entre classificar e
deixar em paz, o agente deixa em paz: classificar errado esconde o e-mail numa
pasta, deixar em paz apenas mantém tudo como estava.

**A execução das 08:00 não rodou porque o computador estava desligado.**
Ela roda quando o servidor subir, desde que ainda seja o mesmo dia. O sistema
guarda qual horário já foi executado hoje.

**Cliquei em "Classificar agora" e não aconteceu nada.**
Provavelmente já havia uma execução em andamento. Só uma roda por vez, para que
duas não briguem pelo mesmo e-mail.

**Meu chat da Consulta IA some quando outro usuário entra?**
Não. Cada usuário tem o próprio histórico de conversa.
