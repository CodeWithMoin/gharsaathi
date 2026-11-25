#!/usr/bin/env bash
# Sourced by scripts/dev.sh (the app) and scripts/backend-cli.sh (maintenance
# commands) so both use the same local database. Prints nothing.
#
# The app's DATABASE_URL comes from the shell first, then the repository's
# .env, otherwise it is the local PostgreSQL as the current macOS user.

_LOCAL_ENV_FILE="${LOCAL_ENV_FILE:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.env}"

local_database_url() {
  local from_env=""
  if [[ -f "${_LOCAL_ENV_FILE}" ]]; then
    from_env="$(sed -n 's/^[[:space:]]*DATABASE_URL[[:space:]]*=[[:space:]]*//p' "${_LOCAL_ENV_FILE}" | tail -n 1)"
    from_env="${from_env%$'\r'}"
    from_env="${from_env%\"}"; from_env="${from_env#\"}"
    from_env="${from_env%\'}"; from_env="${from_env#\'}"
  fi
  printf '%s\n' "${DATABASE_URL:-${from_env:-postgresql+asyncpg://${USER}@127.0.0.1:5432/equal_ai}}"
}

export_local_database_url() {
  DATABASE_URL="$(local_database_url)"
  export DATABASE_URL
}
