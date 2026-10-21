# TestHub

Aplicação web server-rendered para gestão de testes manuais e relatórios, construída com
Django e PostgreSQL e executada localmente com Docker Compose.

## Requisitos

- Docker Engine
- Docker Compose v2

Para rodar a aplicação fora do Docker, é necessário também Python 3.14 — veja
[Desenvolvimento local](#desenvolvimento-local).

## Configuração local

1. Crie o arquivo de ambiente local a partir do exemplo (sem valores reais de segredo):

   ```sh
   cp .env.example .env
   ```

2. Suba o banco de dados, a aplicação e o worker de análise de IA. Os serviços `web` e
   `ai-worker` aguardam o healthcheck (`pg_isready`) do `db` antes de iniciar, e ambos usam a
   mesma imagem:

   ```sh
   docker compose up --build -d
   ```

3. Aplique as migrações e crie o primeiro usuário administrador:

   ```sh
   docker compose run --rm web python manage.py migrate
   docker compose run --rm web python manage.py createsuperuser
   ```

A aplicação fica disponível em <http://localhost:8000/>. O `/admin/` do Django
(<http://localhost:8000/admin/>) é mantido apenas como *bootstrap* de superusuário: nenhum
modelo de domínio é registrado nele, pois a administração do produto (usuários, projetos,
casos e execuções) é feita inteiramente pela própria interface da aplicação.

### Reconciliação de migrações e do banco local

O passo `migrate` acima deve ser repetido sempre que novas migrações forem adicionadas ao
código (por exemplo, após atualizar o repositório). Se o banco local foi inicializado antes de
uma migração existir, o banco fica desatualizado e `manage.py check`/`migrate` apontam a
diferença. Nesse caso, aplique as migrações pendentes ou recrie o banco a partir do zero:

```sh
docker compose run --rm web python manage.py migrate
# se preferir um banco limpo (destrutivo, apaga os dados locais):
docker compose down -v && docker compose up --build -d
docker compose run --rm web python manage.py migrate
```

## Desenvolvimento local

Para iterar no código, rode o backend e o worker **fora do Docker** e mantenha **apenas o
PostgreSQL no Docker**. Assim o `runserver` recarrega o código a cada edição — o container `web`
não faz isso, porque o código é copiado para a imagem no build (`COPY . .`) — e você deixa de
reconstruir a imagem a cada mudança. O banco continua sendo o serviço `db` do Compose.

O script `scripts/dev.sh` automatiza o fluxo: garante o serviço `db`, aplica as
migrações e sobe o `runserver` (com *autoreload*) e o worker (`python manage.py
process_ai_reports`) em primeiro plano, encerrando os dois no `Ctrl+C`. O `.env` é
lido pelo próprio `config/settings.py` (via `python-dotenv`), não é executado pelo script.

### Pré-requisitos

- **Python 3.14** (a mesma linha da imagem base em `Dockerfile`).
- **Docker Engine + Docker Compose v2**, usados aqui apenas para o `db`.
- Um `.env` local, criado a partir do exemplo:

  ```sh
  cp .env.example .env
  ```

- A porta do `runserver` livre (padrão `8000`). Como `web` e `ai-worker` também publicam a `8000`,
  pare-os — o `db` permanece de pé:

  ```sh
  docker compose stop web ai-worker
  ```

### Passo a passo

1. Crie o ambiente virtual e instale as dependências:

   ```sh
   python3.14 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```

2. Suba backend + worker. O script sobe o `db` no Docker (se ainda não estiver de pé), aplica as
   migrações e inicia os dois processos:

   ```sh
   ./scripts/dev.sh
   ```

   A aplicação fica em <http://127.0.0.1:8000/>. Opções (`./scripts/dev.sh --help`):
   `--addr HOST:PORT` (endereço do `runserver`), `--no-db` (não sobe o `db`) e `--no-migrate` (não
   aplica migrações). Defina `PYTHON` para usar outro interpretador.

3. Em outro terminal, crie o primeiro usuário administrador (`createsuperuser` cria um usuário com
   perfil **Administrador** e `is_superuser=True`). O `.env` local é carregado automaticamente, então
   não é preciso preparar o ambiente antes:

   ```sh
   .venv/bin/python manage.py createsuperuser
   ```

### Rodar comandos e testes localmente

O `config/settings.py` carrega o `.env` automaticamente (via `python-dotenv`, em
`config/env.py`), então **qualquer** `manage.py` executado fora do `scripts/dev.sh` funciona direto — `check`,
`test`, `shell`, `migrate`, `createsuperuser`, etc.:

```sh
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/python manage.py test
```

Variáveis **exportadas** no shell têm precedência sobre o `.env` (o carregador usa
`override=False` do `python-dotenv`); para apontar para outro banco pontualmente, basta prefixar a variável:

```sh
POSTGRES_HOST=outro-host .venv/bin/python manage.py migrate
```

Os resultados esperados são os mesmos da seção [Comandos de validação](#comandos-de-validação), mas
sem o prefixo `docker compose run --rm web`.

### Senha do banco

O PostgreSQL só aplica `POSTGRES_PASSWORD` na **primeira** inicialização do volume. Se a senha do
`.env` mudar depois, o banco fica com a senha antiga e a autenticação — local ou de um container
recriado — falha com `password authentication failed for user "testhub"`. Alinhe o banco ao `.env`
(o container `db` já recebe `POSTGRES_USER`/`POSTGRES_DB`/`POSTGRES_PASSWORD` do `.env`, então a senha
não precisa passar pelo shell do host):

```sh
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' <<'SQL'
\getenv db_user POSTGRES_USER
\getenv pw POSTGRES_PASSWORD
ALTER USER :"db_user" WITH PASSWORD :'pw';
SQL
```

### Observações

- **Arquivos estáticos**: com `DJANGO_DEBUG=true` o `staticfiles` serve CSS/JS automaticamente; não é
  preciso `collectstatic` para desenvolver.
- **Worker sem *autoreload***: o `process_ai_reports` é um laço próprio e **não** recarrega sozinho;
  ao editar o adaptador de IA ou os serviços do worker, reinicie-o (`Ctrl+C` e rode
  `./scripts/dev.sh` de novo).
- **Voltar ao fluxo 100% Docker**: `docker compose up -d --build`. Como o `.env` e o banco passam a
  usar a mesma senha, `web` e `ai-worker` reconectam normalmente.

## Variáveis de ambiente

Todas as variáveis são definidas em `.env` (ignorado pelo Git). Os placeholders estão em
`.env.example`. Em execuções locais o `.env` é carregado automaticamente (via `python-dotenv`, em
`config/env.py`, sem sobrescrever variáveis já exportadas); no Compose, as variáveis chegam por `env_file`:

| Variável | Descrição |
|---|---|
| `DJANGO_SECRET_KEY` | Chave secreta do Django. Obrigatória quando `DJANGO_DEBUG=false`. |
| `DJANGO_DEBUG` | Ativa o modo de depuração (`true`/`false`). |
| `DJANGO_ALLOWED_HOSTS` | Hosts permitidos, separados por vírgula. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Origens confiáveis de CSRF, separadas por vírgula. |
| `DJANGO_SESSION_COOKIE_SECURE` / `DJANGO_CSRF_COOKIE_SECURE` / `DJANGO_SECURE_SSL_REDIRECT` | Endurecimento para HTTPS. |
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | Credenciais do PostgreSQL. |
| `POSTGRES_HOST` / `POSTGRES_PORT` | Conexão do Django com o banco. Local: `localhost` (porta publicada pelo Compose); os serviços `web`/`ai-worker` sobrescrevem com `db`. |
| `POSTGRES_HOST_PORT` / `WEB_HOST_PORT` | Portas publicadas no host. |
| `AI_PROVIDER_ACTIVE` | Interruptor explícito de aprovação do provedor de IA (`true`/`false`, padrão `false`). Enquanto `false`, nenhuma chamada real é feita. |
| `AI_PROVIDER_ENDPOINT` / `AI_PROVIDER_MODEL` / `AI_PROVIDER_API_KEY` / `AI_PROVIDER_TIMEOUT_SECONDS` | Endpoint base, **modelo** e segredo do provedor e tempo limite. O modelo é obrigatório — a URL final é `{endpoint}/models/{model}:generateContent`. Só têm efeito com `AI_PROVIDER_ACTIVE=true`. |

> **Provedor de IA: Google Gemini (Google AI Studio, tier gratuito) — somente dados de
> demonstração.** A integração está implementada em `specs/002-integracao-ia-gemini/`, com a
> decisão registrada como **Aprovado (somente demonstração)** em `docs/ai-provider-decision.md`.
> O **provedor e o plano podem mudar**: o adaptador (`apps/executions/ai_adapter.py`) é o ponto
> único de troca e o prompt do sistema é um arquivo versionado em `apps/executions/prompts/`.
>
> ⚠️ **Privacidade:** o tier gratuito do Google AI Studio **pode usar as entradas para melhorar
> produtos do Google** e as **observações dos passos são texto livre, podendo conter dados
> pessoais**. Por isso a análise real deve ser habilitada **apenas com os dados de demonstração**
> do TCC — **nunca** com dados reais ou sensíveis. Enquanto `AI_PROVIDER_ACTIVE=false` (padrão),
> nada é enviado e o relatório nasce **Indisponível** (`UNAVAILABLE`), com as métricas
> determinísticas disponíveis imediatamente.

### Ativação do provedor de IA (somente demonstração)

1. Em um `.env` local (fora do versionamento), defina:

   ```sh
   AI_PROVIDER_ACTIVE=true
   AI_PROVIDER_ENDPOINT=https://generativelanguage.googleapis.com/v1beta
   AI_PROVIDER_MODEL=gemini-3.8-flash        # modelo flash do tier gratuito (exemplo atual)
   AI_PROVIDER_API_KEY=<sua-chave>          # nunca versionar
   AI_PROVIDER_TIMEOUT_SECONDS=15
   ```

2. Suba os serviços, carregue os dados de demonstração e processe a análise do worker:

   ```sh
   docker compose up --build -d
   docker compose run --rm web python manage.py migrate
   docker compose run --rm web python manage.py seed_demo_data
   docker compose run --rm web python manage.py process_ai_reports --once
   ```

Detalhes (contexto enviado, caminhos de falha e limites do tier gratuito):
`specs/002-integracao-ia-gemini/quickstart.md`.

## Comandos de validação

```sh
docker compose up --build -d
docker compose run --rm web python manage.py check
docker compose run --rm web python manage.py makemigrations --check --dry-run
docker compose run --rm web python manage.py test
```

Resultados esperados (verificados em 2026-10-17, Docker Compose + PostgreSQL):

| Comando | Resultado esperado |
|---|---|
| `docker compose up --build -d` | `db`, `web` e `ai-worker` sobem; `db` reporta `healthy` antes de `web`/`ai-worker` iniciarem. |
| `check` | `System check identified no issues (0 silenced).` |
| `makemigrations --check --dry-run` | `No changes detected` (nenhuma migração pendente). |
| `test` | `Ran 290 tests ... OK` (suíte completa contra PostgreSQL, sem acesso a provedores externos de IA). |

A suíte cobre regressões entre aplicações (segurança, integridade, estados, métricas e falhas de
IA) em `tests/`, além dos testes por aplicação em `apps/*/tests/`.

## Operação

```sh
docker compose ps
docker compose logs --tail=100 web ai-worker db
docker compose restart web ai-worker   # reivindica leases de IA expirados, sem duplicar relatórios
docker compose down                    # preserva o volume nomeado do PostgreSQL
docker compose down -v                 # destrutivo: remove o volume do banco
```

### Supervisão do worker

O serviço `ai-worker` usa a mesma imagem e o mesmo ambiente de `web`, com o comando
`python manage.py process_ai_reports`, que roda continuamente. Ele reivindica relatórios com
`select_for_update(skip_locked)` e um *lease* de curta duração por token; o adaptador de IA é
chamado fora de transações e apenas o *token* atual pode gravar o resultado. Não há Redis,
Celery ou outro serviço: o próprio `ai-worker` é o processo de supervisão. Quando o provedor não
está habilitado (`AI_PROVIDER_ACTIVE=false`, padrão — ver `docs/ai-provider-decision.md`), o worker
finaliza o relatório como `UNAVAILABLE` com a categoria segura `Configuration`, sem erro de
execução.

### Verificação operacional (T076, 2026-10-16)

- **Lease reivindicado após reinício**: com um relatório em `PROCESSING` de *lease* expirado,
  `docker compose restart web ai-worker` fez o worker reivindicar o trabalho
  (`UNAVAILABLE`, `lease_token=None`) mantendo **um único** relatório por execução — nenhuma
  duplicação.
- **Volume preservado**: `docker compose down` manteve o volume `2-new-testhub_postgres_data`;
  após `docker compose up -d`, os dados semeados continuaram acessíveis, confirmando a
  persistência entre reinicializações.

Os dados do PostgreSQL ficam no volume nomeado `postgres_data` e são preservados entre
`docker compose down` e novas inicializações. Use `down -v` somente quando quiser descartar
o banco local.

## Estrutura

```text
manage.py
config/            # configuração do projeto Django (settings, env, urls, wsgi, asgi)
apps/              # aplicações de domínio (accounts, projects, testcases, executions)
templates/         # templates HTML compartilhados e componentes
static/            # CSS e JavaScript sem frameworks
tests/             # testes de regressão transversais (segurança, apresentação, acessibilidade)
docs/              # decisões registradas (ex.: provedor de IA)
specs/             # especificação, plano, tarefas e guia de validação da feature
scripts/           # ferramentas de desenvolvimento (scripts/dev.sh)
Dockerfile         # imagem da aplicação (Python base fixada, usuário não-root)
compose.yaml       # serviços db, web e ai-worker, volume nomeado e healthcheck
requirements.txt   # dependências fixadas
```
