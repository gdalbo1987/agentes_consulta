# Agente de Suporte ao Comercial

Ferramenta **interna** do Grupo Coester. Lê a caixa de e-mails do comercial pela
Microsoft Graph API e devolve para a equipe uma caixa triada: cada mensagem
classificada, o que é urgente marcado, tudo arquivado na pasta certa e resumido.
Não é um SaaS: o acesso é fechado, por convite, e existe uma única organização.

```
Caixa de entrada -> Classificação -> Resumo -> Consulta
```

| Agente | O que faz |
|---|---|
| **Classificação** | Separa cada e-mail em pedido, proposta, revisão de pedido ou revisão de proposta. Marca o urgente, aplica a categoria no Outlook e move para a pasta da classe. |
| **Resumo** | Escreve, para cada e-mail classificado, o que o cliente quer, os pontos principais e o próximo passo. |
| **Consulta** | Chat que responde perguntas sobre os e-mails classificados: o que está urgente, o que chegou de um cliente, quantas propostas estão em aberto. |

E-mail que não se encaixa em nenhuma das quatro classes **não é marcado nem
movido**: ele fica exatamente onde estava.

A classificação roda **duas vezes por dia**, nos horários que o time define, e
também sob demanda por um botão no painel.

## Requisitos

- Python 3.12
- PostgreSQL
- Chave de API da OpenAI
- Um registro de aplicativo no Entra ID com **duas permissões de aplicação**,
  ambas com consentimento de administrador: `Mail.Send` e `Mail.ReadWrite`
- Uma caixa de correio real do locatário. Conta pessoal (hotmail.com,
  outlook.com) não funciona no fluxo de aplicação da Graph.

## Como rodar

```bash
python -m venv .venv && .venv/Scripts/activate   # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env        # preencha DATABASE_URL, SETTINGS_ENCRYPTION_KEY e OPENAI_API_KEY

python -m reflex db migrate                        # cria o schema
SUPER_ADMIN_SENHA="uma-senha-forte" python scripts/seed.py
python -m reflex run --env prod --single-port
```

A ordem importa: `migrate` cria as tabelas em que o `seed` escreve, e
`SETTINGS_ENCRYPTION_KEY` precisa existir antes do `seed`, que já grava cifrada
a credencial que estiver no `.env`.

Validação rápida sem subir o servidor: `python -m reflex compile --dry`.

Ao vir de uma instalação da versão anterior (a plataforma de prospecção), rode
também `python scripts/migrar_agentes.py`, que reconcilia as chaves de
configuração dos agentes.

## Primeiros passos depois de subir

1. Entre como super admin e vá em `/admin`. Preencha as credenciais da Microsoft
   Graph e use os DOIS botões de teste: "Enviar e-mail de teste" prova a
   permissão de envio, "Testar leitura da caixa" prova a de leitura. Elas são
   permissões diferentes e falham de formas diferentes.
2. Vá em `/dashboard` e vincule as quatro pastas do Outlook, uma por classe.
   Você informa o NOME da pasta; a plataforma descobre o identificador. Enquanto
   houver classe sem pasta, a classificação não roda, e isso é proposital:
   começar e parar no meio deixaria parte dos e-mails arquivada e parte na caixa
   de entrada.
3. Antes da primeira execução de verdade, confira o que o agente FARIA:

   ```bash
   python scripts/classificar.py --manual --dry-run
   ```

   O `--dry-run` lê e classifica sem escrever nada, nem no Outlook nem no banco.
4. Ajuste os dois horários e a janela de urgência, e clique em "Classificar
   agora".

## Acesso

Não há cadastro público nem login social. O `seed` cria o **primeiro** super
admin, o único usuário que nasce fora do convite, porque não haveria quem o
convidasse. Daí em diante toda admissão passa por `/admin` e o botão "Convidar
usuário": o convidado recebe um link de uso único de 24h e **escolhe a própria
senha**, então nunca circula senha provisória.

Se a organização ficar sem super admin (um super admin pode se rebaixar sozinho
em `/admin`), rodar o `seed` de novo restaura a permissão sem tocar na senha.

## Limites e custo

O único consumo medido é o de tokens da OpenAI, exibido em `/admin` em dólares,
com preço por modelo e gráfico dos últimos 6 meses.

Cada execução tem dois limites, editáveis no `/dashboard`:

- **`max_emails_por_execucao`** (200 por padrão): o teto de e-mails processados
  numa execução.
- **`lookback_horas`** (48 por padrão): quanto tempo para trás a varredura olha.
  É o que impede a primeira execução numa caixa com anos de histórico de tentar
  classificar tudo.

E-mail já visto numa execução anterior é pulado sem nenhuma chamada ao modelo,
inclusive os que não se encaixaram em classe nenhuma. É a trava que impede pagar
duas vezes pela mesma mensagem.

## Testes

```bash
pytest                      # suíte completa, sem rede
pytest -m graph_funcional   # opcional: fala com a caixa TESTER de verdade
```

**Nenhum teste da suíte padrão toca a caixa de e-mails de produção.** Uma trava
em `tests/conftest.py` faz qualquer tentativa de falar com a Microsoft falhar o
teste, os testes funcionais ficam desligados por padrão, e a fixture deles se
recusa a rodar se a caixa de teste for a mesma da produção. Detalhes em
[CLAUDE.md](CLAUDE.md).

## Estrutura

| Camada | Onde |
|---|---|
| Modelos (SQLModel) | `sales_support_agent/models.py` |
| Estado e event handlers | `sales_support_agent/state.py` |
| Regras e integrações | `sales_support_agent/services/` |
| UI | `sales_support_agent/pages/`, `sales_support_agent/components/` |
| Rotas | `sales_support_agent/sales_support_agent.py` |
| Migrations | `alembic/versions/` |
| Testes | `tests/` |

Documentação complementar: [CLAUDE.md](CLAUDE.md), com as convenções, os
invariantes e as duas armadilhas do Graph que mais custam aqui; e
[MANUAL.md](MANUAL.md), com o passo a passo de deploy e o manual de uso.

## Migrations

```bash
python -m reflex db makemigrations --message "descrição"
python -m reflex db migrate
```

O wrapper do Reflex já liga a metadata do SQLModel, então não edite
`alembic/env.py` à mão.

**Revise sempre o arquivo gerado.** O autogenerate erra ao adicionar coluna
`NOT NULL` em tabela com dados, e não ordena os `DROP TABLE` por dependência de
chave estrangeira: a migration da conversão precisou ter a ordem corrigida à
mão.
