#!/usr/bin/env bash
# Sourced by scripts/dev.sh (the app) and scripts/backend-cli.sh (maintenance
# commands) so both use the same local database. Prints nothing.
#
# The app's DATABASE_URL comes from the shell first; otherwise it is the local
# PostgreSQL as the current macOS user. A DATABASE_URL in .env is not used for
# local runs, because the exported value below always takes precedence.

local_database_url() {
  printf '%s\n' "${DATABASE_URL:-postgresql+asyncpg://${USER}@127.0.0.1:5432/equal_ai}"
}

export_local_database_url() {
  DATABASE_URL="$(local_database_url)"
  export DATABASE_URL
}
