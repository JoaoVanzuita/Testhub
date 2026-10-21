#!/usr/bin/env bash
#
# scripts/dev.sh — desenvolvimento local do TestHub (aplicação fora do Docker).
#
# Roda o backend (manage.py runserver, com autoreload) e o worker de IA
# (manage.py process_ai_reports) diretamente na máquina, reutilizando o
# PostgreSQL que permanece no Docker Compose (serviço `db`).
#
# Motivo: o código da imagem é copiado no build (`COPY . .`), então edições no
# host não chegam ao container. Rodando local, o `runserver` recarrega sozinho.
#
# Pré-requisitos:
#   - .venv/ com as dependências (Django + psycopg + python-dotenv).
#   - Docker/Compose disponível para o serviço `db`.
#   - Porta do runserver livre: pare os containers da app para não disputar a
#     8000 -> `docker compose stop web ai-worker` (o `db` continua de pé).
#   - `POSTGRES_PASSWORD` do `.env` igual à senha real do banco.
#
# Uso:
#   scripts/dev.sh [--addr HOST:PORT] [--no-db] [--no-migrate]
#
# Variáveis de ambiente:
#   PYTHON               interpretador (padrão: .venv/bin/python)
#   POSTGRES_HOST_LOCAL  host local do banco (padrão: localhost)
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

usage() {
  cat <<'EOF'
scripts/dev.sh — desenvolvimento local do TestHub (aplicação fora do Docker).

Rodar o backend (runserver, com autoreload) e o worker de IA
(process_ai_reports) na máquina, usando o PostgreSQL que fica no Docker
Compose (serviço `db`).

Uso:
  scripts/dev.sh [--addr HOST:PORT] [--no-db] [--no-migrate]

Opções:
  --addr HOST:PORT   endereço do runserver (padrão: 127.0.0.1:8000)
  --no-db            não sobe o serviço `db` do Compose
  --no-migrate       não aplica migrações antes de iniciar
  -h, --help         mostra esta ajuda

Variáveis:
  PYTHON               interpretador (padrão: .venv/bin/python)
  POSTGRES_HOST_LOCAL  host local do banco (padrão: localhost)
EOF
}

ADDR="${RUNSERVER_ADDR:-127.0.0.1:8000}"
RUN_DB=1
RUN_MIGRATE=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --addr)
      ADDR="${2:?--addr exige HOST:PORT}"
      shift 2
      ;;
    --no-db) RUN_DB=0; shift ;;
    --no-migrate) RUN_MIGRATE=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "Opção desconhecida: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

# --- Ambiente ---------------------------------------------------------------
# O `.env` NÃO é executado como shell (`source`): o próprio `config/settings.py`
# o carrega via python-dotenv, sem sobrescrever variáveis já exportadas, e o
# Compose o interpola por conta própria. Aqui só derivamos o host/porta locais do
# banco, lendo `POSTGRES_HOST_PORT` do `.env` sem executá-lo.
PY="${PYTHON:-.venv/bin/python}"
if [[ ! -x "$PY" ]]; then
  echo "Interpretador não encontrado: '$PY'. Crie o .venv ou defina PYTHON." >&2
  exit 1
fi

# Lê uma chave do `.env` sem executá-lo como shell e sem interpolar `$`.
read_env_value() {
  "$PY" - "$1" <<'PY' 2>/dev/null || true
import sys

from dotenv import dotenv_values

value = dotenv_values(".env", interpolate=False).get(sys.argv[1])
if value:
    print(value)
PY
}

if [[ ! -f .env ]]; then
  echo "Aviso: .env não encontrado; usando os padrões do settings." >&2
fi

# Fora do Compose o banco não é alcançável pelo nome `db`: usar o host publicado.
export POSTGRES_HOST="${POSTGRES_HOST_LOCAL:-localhost}"
export POSTGRES_PORT="${POSTGRES_HOST_PORT:-$(read_env_value POSTGRES_HOST_PORT)}"
export POSTGRES_PORT="${POSTGRES_PORT:-5432}"
export DJANGO_DEBUG="${DJANGO_DEBUG:-true}"

# --- Banco de dados (mantido no Docker Compose) -----------------------------
if [[ "$RUN_DB" -eq 1 ]]; then
  if command -v docker >/dev/null 2>&1; then
    echo "Garantindo o serviço 'db' do Compose..."
    docker compose up -d db
  else
    echo "Aviso: 'docker' não encontrado; presumindo PostgreSQL em ${POSTGRES_HOST}:${POSTGRES_PORT}." >&2
  fi
fi

if [[ "$RUN_MIGRATE" -eq 1 ]]; then
  echo "Aplicando migrações..."
  "$PY" manage.py migrate --noinput
fi

# Aviso antecipado se a porta já estiver ocupada (normalmente pelo container `web`).
probe_host="${ADDR%:*}"
port="${ADDR##*:}"
[[ -z "$probe_host" || "$probe_host" == "0.0.0.0" ]] && probe_host="127.0.0.1"
if (exec 3<>"/dev/tcp/${probe_host}/${port}") 2>/dev/null; then
  exec 3>&- 3<&- 2>/dev/null || true
  echo "Aviso: ${ADDR} já está em uso. Se for o container da app, pare-o:" >&2
  echo "        docker compose stop web ai-worker" >&2
fi

# --- Processos: backend + worker --------------------------------------------
pids=()
cleanup() {
  for pid in "${pids[@]:-}"; do
    [[ -n "$pid" ]] || continue
    # Cada processo é líder do próprio grupo (setsid), então o sinal também
    # encerra o filho do autoreload do runserver.
    kill -- "-${pid}" 2>/dev/null || kill "${pid}" 2>/dev/null || true
  done
}
trap cleanup EXIT INT TERM

setsid "$PY" manage.py runserver "$ADDR" & pids+=("$!")
setsid "$PY" manage.py process_ai_reports & pids+=("$!")

echo
echo "Backend: http://${ADDR}  (autoreload ativo)"
echo "Worker:  manage.py process_ai_reports"
echo "Encerre com Ctrl+C."
wait -n
