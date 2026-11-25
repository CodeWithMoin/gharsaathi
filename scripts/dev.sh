#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/local-env.sh
source "${REPO_ROOT}/scripts/local-env.sh"
RUN_DIR="${REPO_ROOT}/.run"

ensure_local_postgres() {
  if ! nc -z 127.0.0.1 5432 >/dev/null 2>&1; then
    local pg_data="${PGDATA:-}"
    if [[ -z "${pg_data}" ]]; then
      for candidate in \
        /opt/homebrew/var/postgresql@17 \
        /usr/local/var/postgresql@17 \
        /usr/local/var/postgres; do
        if [[ -f "${candidate}/PG_VERSION" ]]; then
          pg_data="${candidate}"
          break
        fi
      done
    fi
    if [[ -z "${pg_data}" || ! -f "${pg_data}/PG_VERSION" ]]; then
      printf '%s\n' "PostgreSQL is not running and no local data directory was found." >&2
      printf '%s\n' "Install PostgreSQL 17 or set PGDATA, then run make dev again." >&2
      return 1
    fi
    mkdir -p "${RUN_DIR}"
    echo "Starting local PostgreSQL"
    pg_ctl -D "${pg_data}" -l "${RUN_DIR}/postgres.log" start >/dev/null
  fi

  # Only the built-in default database is created here; a DATABASE_URL from
  # the shell or .env points at a database the user created themselves.
  if [[ "$(local_database_url)" == */equal_ai ]] && ! psql -h 127.0.0.1 -d postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname = 'equal_ai'" | grep -q 1; then
    echo "Creating local equal_ai database"
    createdb -h 127.0.0.1 equal_ai
  fi
}

resolve_env_file() {
  if [[ -n "${ENV_FILE:-}" && -f "${ENV_FILE}" ]]; then
    printf '%s\n' "${ENV_FILE}"
  elif [[ -f "${REPO_ROOT}/.env" ]]; then
    printf '%s\n' "${REPO_ROOT}/.env"
  elif [[ -f "${HOME}/Downloads/equal-ai/.env" ]]; then
    printf '%s\n' "${HOME}/Downloads/equal-ai/.env"
  else
    printf '%s\n' "Missing .env. Copy .env.example to .env and add your keys." >&2
    return 1
  fi
}

ENV_PATH="$(resolve_env_file)"
ensure_local_postgres
export_local_database_url
export COMMERCE_MODE="${COMMERCE_MODE:-demo}"

# Clerk's Next.js setup keeps the development instance keys in the frontend
# env file. Reuse only the server secret for the local FastAPI process when the
# root env has not defined it; never source or execute the env file.
if [[ -z "${CLERK_SECRET_KEY:-}" && -f "${REPO_ROOT}/frontend/.env.local" ]]; then
  while IFS='=' read -r env_key env_value; do
    if [[ "${env_key}" == "CLERK_SECRET_KEY" ]]; then
      export CLERK_SECRET_KEY="${env_value%$'\r'}"
      break
    fi
  done < "${REPO_ROOT}/frontend/.env.local"
fi
export CLERK_AUTHORIZED_PARTIES_CSV="${CLERK_AUTHORIZED_PARTIES_CSV:-http://localhost:3003,http://127.0.0.1:3003}"

SWIGGY_STORE="${SWIGGY_TOKEN_STORE_PATH:-}"
if [[ -z "${SWIGGY_STORE}" && -f "${HOME}/Downloads/equal-ai/backend/.data/swiggy_connection.enc" ]]; then
  SWIGGY_STORE="${HOME}/Downloads/equal-ai/backend/.data/swiggy_connection.enc"
fi

cleanup() {
  trap - INT TERM EXIT
  [[ -n "${BACKEND_PID:-}" ]] && kill "${BACKEND_PID}" 2>/dev/null || true
  [[ -n "${FRONTEND_PID:-}" ]] && kill "${FRONTEND_PID}" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

# Which checkout/branch/commit this launch runs (read by make doctor). No secrets.
mkdir -p "${RUN_DIR}"
printf '{"checkout":"%s","branch":"%s","commit":"%s","commerce_mode":"%s","started_at":"%s"}\n' \
  "${REPO_ROOT}" \
  "$(git -C "${REPO_ROOT}" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)" \
  "$(git -C "${REPO_ROOT}" rev-parse --short HEAD 2>/dev/null || echo unknown)" \
  "${COMMERCE_MODE}" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"${RUN_DIR}/launch.json"

echo "Starting backend on http://127.0.0.1:8000 (${COMMERCE_MODE} commerce mode)"
(
  cd "${REPO_ROOT}/backend"
  if [[ -n "${SWIGGY_STORE}" ]]; then
    export SWIGGY_TOKEN_STORE_PATH="${SWIGGY_STORE}"
  fi
  echo "Applying database migrations"
  uv run --with-requirements requirements.txt alembic upgrade head
  exec uv run --with-requirements requirements.txt uvicorn app.main:app \
    --host 127.0.0.1 --port 8000 --env-file "${ENV_PATH}" --no-access-log
) &
BACKEND_PID=$!

echo "Starting frontend on http://localhost:3003"
(
  cd "${REPO_ROOT}/frontend"
  exec npm run dev
) &
FRONTEND_PID=$!

echo "GharSaathi is starting. Press Ctrl-C to stop both services."
while kill -0 "${BACKEND_PID}" 2>/dev/null && kill -0 "${FRONTEND_PID}" 2>/dev/null; do
  sleep 1
done

echo "One service stopped unexpectedly; shutting down the other." >&2
exit 1
