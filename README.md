# Plataforma Coester de Prospecção

Ferramenta **interna** do Grupo Coester para prospecção, enriquecimento e
priorização de leads B2B. Não é um SaaS: o acesso é fechado, por convite, e
existe uma única organização.

O funil vai da definição do produto até a recomendação de abordagem:

```
Produtos → Pesquisa (ICP) → Enriquecimento → Priorização → Approach → Insights
```

| Etapa | O que faz | Fontes |
|---|---|---|
| **Pesquisa** | encontra empresas com fit de ICP a partir do produto, região e segmento | agente de IA com busca na web |
| **Enriquecimento** | completa o cadastro, acha decisores e o e-mail deles | Receita Federal (grátis), KipFlow, Hunter.io |
| **Priorização** | pontua 7 critérios e classifica em Alta/Média/Baixa | agente de IA |
| **Approach** | sugere gancho, canal, dor e timing por lead | agente de IA |
| **Insights** | chat que responde perguntas sobre a base | agente de IA com 19 tools |

Relatórios saem em `.xlsx` (enriquecimento) e `.pdf` (priorização).

## Requisitos

- Python 3.12
- PostgreSQL
- Chaves de API: OpenAI (obrigatória), KipFlow, Hunter.io e um registro de
  aplicativo no Entra ID para envio de e-mail

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
`SETTINGS_ENCRYPTION_KEY` precisa existir antes do `seed`, que já grava
cifradas as chaves de API que estiverem no `.env`.

Validação rápida sem subir o servidor: `python -m reflex compile --dry`.

## Acesso

Não há cadastro público nem login social. O `seed` cria o **primeiro** super
admin, o único usuário que nasce fora do convite, porque não haveria quem o
convidasse. Daí em diante toda admissão passa por `/admin` → "Convidar
usuário": o convidado recebe um link de uso único de 24h e **escolhe a própria
senha**, então nunca circula senha provisória.

Se a organização ficar sem super admin (um super admin pode se rebaixar sozinho
em `/admin`), rodar o `seed` de novo restaura a permissão sem tocar na senha.

## Limites

- **20 consultas por usuário por mês**, contadas separadamente em cada etapa
  (pesquisa, enriquecimento e priorização). Super admin é ilimitado.
- **4 contatos por empresa** no enriquecimento, inclusive para super admin:
  cada contato é cobrado pela KipFlow.
- **Créditos da Hunter** por ciclo (50 no plano gratuito) **por conta**,
  renovando no aniversário da assinatura. Até 8 contas podem ser cadastradas em
  `/admin`, e a busca de e-mail é distribuída entre elas, então o orçamento do
  ciclo é o limite vezes o número de contas. O limite, as contas e o dia da
  renovação são configuráveis em `/admin`.

## Custo

`/admin` mede três consumos: tokens da OpenAI (em US$, preço por modelo),
KipFlow (em BRL, valor real da resposta da API) e Hunter (em créditos). Cada um
tem card de acumulado e gráfico dos últimos 6 meses.

## Estrutura

| Camada | Onde |
|---|---|
| Modelos (SQLModel) | `prospect_agent/models.py` |
| Estado e event handlers | `prospect_agent/state.py` |
| Regras e integrações | `prospect_agent/services/` |
| UI | `prospect_agent/pages/`, `prospect_agent/components/` |
| Rotas | `prospect_agent/prospect_agent.py` |
| Migrations | `alembic/versions/` |

Documentação complementar: [CLAUDE.md](CLAUDE.md) (convenções e invariantes),
[CONTRATO_PESQUISA.md](CONTRATO_PESQUISA.md) e
[CONTRATO_ENRIQUECIMENTO.md](CONTRATO_ENRIQUECIMENTO.md) (contratos de dados
entre as etapas do funil).

## Migrations

```bash
python -m reflex db makemigrations --message "descrição"
python -m reflex db migrate
```

O wrapper do Reflex já liga a metadata do SQLModel, então não edite
`alembic/env.py` à mão. Revise sempre o arquivo gerado: o autogenerate erra ao adicionar coluna
`NOT NULL` em tabela com dados.
