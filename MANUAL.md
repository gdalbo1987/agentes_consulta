# Manual da Plataforma Coester de Prospecção

Duas partes independentes. A **Parte 1** é para quem instala e mantém a
plataforma no servidor. A **Parte 2** é para quem usa a plataforma no dia a dia
e não precisa saber nada de técnico.

---

# Parte 1: Deploy

## 1.1 O que é preciso ter antes

| Item | Observação |
|---|---|
| Python 3.12 | versão em que o projeto foi desenvolvido e testado |
| PostgreSQL | banco vazio e acessível pelo servidor |
| Acesso à internet no servidor | a primeira execução baixa as dependências do frontend |
| Chave da OpenAI | obrigatória: sem ela nenhum agente funciona |
| Chave da KipFlow | enriquecimento cadastral e contatos decisores |
| Chave da Hunter.io | e-mail dos contatos (até 8 contas, ver 1.7) |
| Registro de app no Entra ID | envio de convites e redefinição de senha |

As três últimas são opcionais para subir, mas cada uma desliga uma parte do
produto se faltar. Sem a KipFlow o enriquecimento nem inicia. Sem a Hunter o
enriquecimento roda e avisa que não buscou e-mails. Sem o Entra ID os convites
não chegam por e-mail (o usuário é criado, mas ninguém recebe o link).

### Registro no Entra ID

O envio de e-mail usa a Microsoft Graph API com autenticação de aplicação, não
de usuário. No portal do Entra ID:

1. Registre um aplicativo e anote o **Directory (tenant) ID** e o
   **Application (client) ID**.
2. Em "Certificados e segredos", crie um **client secret** e copie o valor na
   hora (ele não é exibido de novo).
3. Em "Permissões de API", adicione a permissão de **aplicação** (não delegada)
   `Mail.Send` e **conceda o consentimento do administrador**.
4. Escolha uma caixa de correio real do locatário para ser o remetente.

Sem o consentimento de administrador, ou com um remetente que não seja caixa
real, a API responde 403 e nenhum e-mail sai.

## 1.2 Instalação

```bash
git clone <url-do-repositorio>
cd prospect_agent

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## 1.3 Configuração

```bash
cp .env.example .env
```

Abra o `.env` e preencha. As quatro que impedem o app de subir se faltarem:

```ini
DATABASE_URL=postgresql://usuario:senha@host:5432/nome_do_banco
APP_BASE_URL=https://prospeccao.coester.com.br
SETTINGS_ENCRYPTION_KEY=<gerada no passo abaixo>
OPENAI_API_KEY=sk-...
```

Gere a chave de criptografia com:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

**Guarde essa chave.** Ela cifra os segredos das integrações dentro do banco.
Trocá-la depois não quebra o app, mas torna ilegíveis os segredos já gravados:
eles passam a se comportar como "não configurado" e precisam ser digitados de
novo em `/admin`.

`APP_BASE_URL` precisa ser o endereço público real, sem barra no final. É a base
dos links de convite e de redefinição de senha. Se ficar em `localhost`, o
convite chega com um link que só funciona na máquina de quem o gerou.

As chaves de KipFlow, Hunter e Graph podem ficar no `.env` ou ser preenchidas
depois em `/admin`. O que estiver no `.env` é gravado no banco já cifrado na
primeira execução do seed. `HUNTER_API_KEY` vira a **conta 1** da Hunter; as
demais contas são cadastradas em `/admin`.

## 1.4 Banco de dados e primeiro acesso

A ordem importa. `migrate` cria as tabelas em que o `seed` escreve, e
`SETTINGS_ENCRYPTION_KEY` precisa existir antes do `seed`, que já cifra as
chaves de API encontradas no `.env`.

```bash
python -m reflex db migrate

SUPER_ADMIN_EMAIL="fulano@coester.com.br" \
SUPER_ADMIN_NOME="Fulano de Tal" \
SUPER_ADMIN_SENHA="uma-senha-forte" \
python scripts/seed.py
```

O seed cria três coisas: a organização Coester, o primeiro super admin e as
linhas de configuração de agentes e integrações.

`SUPER_ADMIN_SENHA` não tem valor padrão de propósito. Sem ela o script para e
explica o que fazer. Só o hash bcrypt vai para o banco, a senha em texto puro
não é gravada em lugar nenhum. Troque a senha no primeiro acesso, em "Meu
Perfil", e remova a variável do ambiente depois.

O seed é idempotente: rodar de novo não duplica nada e não sobrescreve uma senha
já trocada.

## 1.5 Subir a aplicação

```bash
python -m reflex run --env prod --single-port
```

`--single-port` serve o frontend e o backend na mesma porta (3000), que é o que
simplifica o proxy reverso. Sem essa opção, o backend sobe separado na 8000.

A primeira execução baixa e compila as dependências do frontend e demora vários
minutos. As seguintes são rápidas.

Aponte o proxy reverso (Nginx, IIS, Caddy) para a porta 3000 com HTTPS. O
`APP_BASE_URL` deve bater exatamente com o endereço que o proxy publica.

## 1.6 Verificação pós-deploy

Nesta ordem, porque cada passo depende do anterior:

1. Abra `/` e confirme que a landing carrega com vídeo e logo.
2. Faça login em `/login` com o super admin criado no seed.
3. Você deve cair em `/dashboard`. Abra `/admin` pelo menu lateral.
4. Em `/admin`, preencha as integrações que ficaram de fora do `.env`.
5. Ainda em `/admin`, clique em **"Enviar e-mail de teste"** no card do
   Microsoft Graph. Se o e-mail não chegar, resolva isso antes de convidar
   alguém: um convite que não chega deixa a pessoa sem acesso e sem aviso.
6. Preencha o **preço dos tokens** por modelo e o **limite de créditos da
   Hunter** com o dia de renovação correto (ver 1.7).
7. Convide um usuário de teste e confirme que o link recebido define a senha.

## 1.7 Configurações que só existem em `/admin`

Estas não estão no `.env` e precisam de atenção depois do deploy.

**Preço dos tokens da OpenAI.** Uma linha por modelo, em dólares por 1 milhão de
tokens (o formato da tabela de preços publicada pela OpenAI). Enquanto
estiverem zerados, os cards de custo mostram zero. O preço vale para o consumo
registrado com aquele modelo, então trocar o modelo de um agente não reescreve
o custo já apurado.

**Contas e créditos da Hunter.** O card tem oito campos de chave, um por conta,
mais dois números que valem para todas elas.

O Hunter vende créditos **por conta**, então cadastrar mais de uma multiplica a
cota sem plano pago: com quatro contas gratuitas o orçamento do ciclo é 200
créditos, não 50. A plataforma distribui as buscas entre as contas cadastradas,
mandando cada uma para a que tem mais crédito sobrando, e se uma delas recusar
(cota esgotada ou chave errada) a busca segue nas outras, com aviso no fim da
rodada. O rótulo verde ao lado de cada conta mostra quanto ela já gastou no
ciclo. O ícone de lixeira apaga a chave daquela conta, e é o único jeito de
remover uma: deixar o campo em branco preserva a chave gravada, como em todos os
campos de segredo do painel.

O **limite por ciclo** (50 no plano gratuito) é o teto de **cada** conta. O **dia
da renovação** é o dia do mês em que as contas na Hunter foram criadas, não o dia
1º, e define a janela em que os créditos são contados. Crie todas as contas no
mesmo dia: o campo é um só, e um dia errado faz a plataforma liberar ou bloquear
buscas na hora errada.

**Modelo e esforço de cada agente.** Quatro agentes configuráveis: produto,
pesquisa, priorização (compartilhado com approach) e insights.

## 1.8 Atualizações

```bash
git pull
pip install -r requirements.txt
python -m reflex db migrate
python -m reflex run --env prod --single-port
```

Se a atualização trouxer mudança de schema, o `migrate` aplica. Faça backup do
banco antes de atualizar em produção.

## 1.9 Problemas comuns

| Sintoma | Causa provável |
|---|---|
| `DATABASE_URL não configurada` ao rodar o seed | `.env` não foi lido ou a variável está vazia |
| `SUPER_ADMIN_SENHA não configurada` | primeira execução do seed sem a variável (esperado) |
| Convite não chega | Graph sem consentimento de administrador, ou remetente que não é caixa real |
| Link do convite aponta para o endereço errado | `APP_BASE_URL` incorreto |
| Integrações aparecem como "não configurado" depois de funcionar | `SETTINGS_ENCRYPTION_KEY` foi trocada |
| `EBUSY` ao subir o servidor | sobrou uma instância anterior rodando, segurando os arquivos do frontend |
| Enriquecimento interrompe avisando sobre a Receita | a consulta pública caiu. É proposital: sem ela cada empresa sairia muito mais cara na KipFlow |

---

# Parte 2: Manual de uso

## 2.1 Como entrar

O acesso é só por convite. Não existe cadastro público nem login com Google.

Um super admin cria o seu usuário informando nome, e-mail e classe. Você recebe
um e-mail com um link válido por **24 horas** e **escolhe a sua própria senha**.
Ninguém define senha por você, e nenhuma senha provisória circula por e-mail.

Se o link expirar, use "Esqueci minha senha" na tela de login, ou peça um novo
convite.

## 2.2 O caminho completo, em ordem

O funil tem cinco etapas e elas dependem umas das outras. Pular etapa não
funciona: o enriquecimento só enxerga empresas que a pesquisa encontrou, e a
priorização só enxerga empresas já enriquecidas.

```
Produtos  ->  Pesquisa  ->  Enriquecimento  ->  Priorização  ->  Insights
```

### Passo 1: Produtos

Menu **Produtos**. Cadastre o que você vende, com nome e descrição.

A descrição não é enfeite: é a partir dela que o agente de pesquisa entende que
tipo de empresa precisa do seu produto. Descrição vaga ("equipamentos
industriais") traz lead genérico. Descrição específica, dizendo qual problema o
produto resolve e em que tipo de operação ele entra, traz lead melhor.

Há um assistente de IA que ajuda a escrever a descrição.

Os produtos são compartilhados: todos os usuários veem e usam o mesmo catálogo,
e não há limite de quantos cadastrar.

### Passo 2: Pesquisa

Menu **Pesquisa**. Selecione um ou mais produtos e preencha:

- **Região de interesse**: aceita do país ao município. "Brasil", "Sul", "Rio
  Grande do Sul" ou "Caxias do Sul".
- **Segmento estratégico**: o setor que você quer atingir.

Clique em **Executar pesquisa**. Antes de começar, a plataforma pergunta o que
fazer com as empresas que a base já tem:

- **Apenas empresas novas**: a busca usa todo o esforço procurando empresas que
  ainda não estão na base. As notícias das empresas antigas continuam sendo as
  da última pesquisa que as encontrou.
- **Incluir as empresas já encontradas**: além das inéditas, as empresas que a
  base já tem nessa mesma linha de pesquisa voltam ao resultado com as notícias
  buscadas de novo. Use quando for abordar uma lista já levantada e quiser o
  gancho atualizado. A rodada demora mais, proporcionalmente a quantas forem.

"Mesma linha de pesquisa" não exige repetir as palavras da vez anterior: a
plataforma compara região e segmento por significado, então "RS" reencontra o
que foi buscado como "Rio Grande do Sul".

Se você escolher incluir e nenhuma empresa da base pertencer a essa linha de
pesquisa (você mudou de região ou de setor, por exemplo), a pesquisa roda
normalmente só com empresas novas e um aviso no resultado diz isso, citando o
recorte pedido. Um resultado sem nada reincluído nunca é silencioso.

Em qualquer das duas opções, a busca não gasta esforço reencontrando empresas
que já estão na base. O agente devolve no mínimo 30 empresas com potencial de
encaixe no seu perfil de cliente ideal, cada uma com uma nota de 0 a 100 e uma
justificativa, mais as notícias recentes que servem de gancho de abordagem.

A pesquisa demora vários minutos. Acompanhe pela barra de progresso e não feche
a página.

Ao terminar aparecem o resumo, os avisos e o botão **Avançar para próxima
etapa**.

Uma observação sobre contagem: quando uma pesquisa cobre dois produtos, os leads
encontrados contam para os dois. Por isso a soma de leads por produto pode ser
maior que o total da base. Não é erro.

### Passo 3: Enriquecimento

Menu **Enriquecimento**. Clique em **Iniciar Enriquecimento**.

A plataforma completa o cadastro de cada empresa e procura os decisores. Ela
trabalha na ordem do mais barato para o mais caro: primeiro a Receita Federal,
que é gratuita, e só depois as fontes pagas, para o que a Receita não tem.

O que é coletado:

- razão social, CNPJ, cidade e estado, endereço, idade da empresa
- porte, segmento, faixa de faturamento
- situação cadastral, mais um **alerta em vermelho** para recuperação judicial,
  falência e situações equivalentes
- telefone, WhatsApp, site e LinkedIn
- até **4 contatos decisores** por empresa, com nome e cargo
- o **e-mail profissional** de cada contato, com um percentual de confiança

Cada empresa recebe um percentual de 0 a 100, que é quanto dos 12 campos foi
preenchido. Verde é completo, âmbar é parcial.

Sobre o percentual de confiança do e-mail: acima de 80 o endereço foi
confirmado em fonte pública. Abaixo de 70 é um palpite baseado no padrão do
domínio da empresa. Vale conferir antes de disparar campanha, porque e-mail
errado queima a reputação do seu domínio.

O e-mail entra no cálculo do percentual. Uma empresa com tudo preenchido, mas
sem nenhum e-mail, fica em 92% e continua na fila. Isso é intencional: quando os
créditos renovam, ela ganha nova tentativa.

Se aparecer o aviso de **limite de uso da Hunter**, a cota de créditos do
período acabou em todas as contas cadastradas. O enriquecimento termina
normalmente, só sem os e-mails restantes, e a mensagem informa a data em que a
cota renova. Os contatos que ficaram de fora são tentados de novo depois, sem
repetir os que já foram resolvidos. Se o aviso for de **chave inválida** numa
conta, as buscas seguiram nas outras: peça ao super admin para revisar aquela
chave em `/admin`.

Ao final, **Baixar relatório (.xlsx)** gera uma planilha com três abas: uma
linha por empresa, uma por contato e uma por notícia. É o formato para filtrar e
importar no CRM.

### Passo 4: Priorização

Menu **Priorização**. Clique em **Iniciar priorização**.

Cada lead é avaliado em 7 critérios com pesos diferentes:

| Critério | Peso |
|---|---|
| Fit com o perfil de cliente ideal | 30% |
| Potencial financeiro | 20% |
| Facilidade de contato | 20% |
| Segmento estratégico | 10% |
| Sinais de investimento futuro | 10% |
| Maturidade da empresa | 5% |
| Região de localização | 5% |

O resultado é uma nota de 0 a 100 e uma classe: **Alta**, **Média** ou **Baixa**.
Clicando na linha você vê a justificativa de cada critério, e não só a nota.

A opção **incluir approach** vem marcada. Com ela, cada lead também recebe de 2
a 4 recomendações de primeiro contato: um gancho de abertura usando dados reais
da empresa, o canal recomendado, uma dor provável e o timing sugerido.

**Baixar relatório (.pdf)** gera um documento de apresentação, com a tabela
resumo e uma página por lead. É o formato para levar a uma reunião. Para
trabalhar em planilha, use o .xlsx do enriquecimento.

### Passo 5: Lista de Leads

Menu **Lista de Leads**. Todas as empresas já coletadas, de todas as pesquisas,
com filtro por produto e por usuário que coletou.

Clique em qualquer linha para abrir a ficha completa: identificação, canais de
contato, decisores com e-mail e situação do enriquecimento.

### Passo 6: Insights IA

Menu **Insights IA**. Um chat que responde perguntas sobre a sua base. Ele lê os
mesmos números do dashboard, então as respostas nunca divergem das telas.

Exemplos do que ele responde:

- "Quantos leads por produto? E qual produto rende os maiores scores?"
- "Quais leads grandes de metalurgia no RS já têm contato?"
- "Qual o faturamento da empresa X e por que ela ficou classe Alta?"
- "Me passa o site, o LinkedIn e os e-mails dos meus melhores leads."
- "Como abordar a empresa X?"
- "O que ainda falta enriquecer?"

Ele responde apenas com dados da base e diz quando não encontrou algo, em vez de
inventar. Perguntas fora do assunto comercial são recusadas.

A conversa fica salva entre sessões e é privada: cada usuário tem a sua. O botão
**Limpar conversa** apaga o histórico.

## 2.3 Dashboard

Menu **Dashboard**. Visão geral da base: total de leads, quantos foram
enriquecidos, notas médias, distribuição por segmento, porte, faturamento e
situação cadastral, um mapa do Brasil por concentração de leads e a lista dos
melhores leads. Clicar em um lead abre a mesma ficha de detalhe.

## 2.4 Seus limites

**20 consultas por mês, por usuário, em cada etapa.** Pesquisa, enriquecimento e
priorização contam separadamente: são 20 de cada, não 20 no total. A cota é
individual, o seu consumo não afeta o de outra pessoa, e ela renova no início do
mês. Execuções que falharam não consomem cota.

Quando você chega no limite, o botão é desabilitado e aparece um aviso
explicando o motivo. A barra de progresso mostra quanto você já usou.

**4 contatos por empresa** no enriquecimento. Esse limite vale para todo mundo,
inclusive super admin, porque cada contato tem custo com o fornecedor.

## 2.5 Meu Perfil

Menu **Meu Perfil**. Altere nome, foto e senha.

## 2.6 Para super admins

Menu **Painel Admin**, visível apenas para super admins.

**Usuários.** Convidar, editar, promover a super admin e revogar acesso. Ao
revogar, o usuário e a conversa privada dele de Insights são apagados, mas os
leads, pesquisas e enriquecimentos permanecem: são patrimônio da organização, e
a autoria fica preservada no filtro por usuário da Lista de Leads.

Cuidado com uma armadilha: um super admin pode rebaixar a si mesmo para
"Usuário". Sendo o único, isso tranca o painel para todos. A saída é técnica,
rodar o seed de novo no servidor, então prefira sempre ter dois super admins.

**Custos.** Três consumos medidos, cada um com acumulado e gráfico dos últimos 6
meses:

- **Tokens da OpenAI**, em dólares, multiplicando cada consumo pelo preço do
  modelo que o gerou.
- **KipFlow**, em reais, com o valor real cobrado pela API.
- **Hunter**, em créditos, mostrando quanto do pacote do ciclo já foi usado, o
  número de contas cadastradas e a data da próxima renovação. O total é a soma
  das contas: quatro contas gratuitas dão 200 créditos por ciclo.

**Integrações.** Chaves da KipFlow, as até oito contas da Hunter e as
credenciais do Microsoft Graph, mais o preço dos tokens e o modelo de cada
agente. As chaves aparecem apenas como "configurado" ou "não configurado", nunca
em texto legível. Deixar o campo em branco ao salvar preserva o valor atual.

**Limpar contadores.** Zera o histórico de tokens e de custo da KipFlow. Não
apaga usuários, leads nem o histórico de atividades. Os créditos da Hunter
ficam de fora de propósito: é a contagem deles que segura a cota, e zerá-la
faria a plataforma buscar e-mails além do limite contratado.

**Histórico de atividades.** Registro de quem fez o quê e quando.

## 2.7 Perguntas frequentes

**Posso rodar o enriquecimento duas vezes?** Pode. Empresas já completas são
puladas sem gasto, e as parciais reprocessam só o que falta. Contatos cujo
e-mail já foi procurado não são procurados de novo, mesmo que não tenham sido
encontrados.

**Por que uma empresa ficou em 92%?** Provavelmente falta o e-mail de contato,
que é um dos 12 campos. Ela volta à fila quando os créditos da Hunter renovarem.

**Por que a soma de leads por produto é maior que o total?** Porque uma pesquisa
que cobre dois produtos faz o mesmo lead contar nos dois.

**O que significa o alerta vermelho na empresa?** Recuperação judicial, falência
ou situação equivalente. Atenção: a Receita mantém como "ativa" uma empresa em
recuperação judicial, então esse alerta é a única forma de perceber. Não é
motivo automático de descarte, é informação para a decisão comercial.

**Um contato veio do quadro societário e outro do LinkedIn. Qual a diferença?**
O do quadro societário é o decisor de fato, mas normalmente sem canal direto:
aborda-se pelo telefone da empresa perguntando por ele. O do LinkedIn tem canal
direto, porém costuma ter menos senioridade.

**Quem vê os meus leads?** Todos os usuários veem a base inteira da organização.
O filtro por usuário na Lista de Leads serve para saber quem coletou cada um,
não para esconder.
