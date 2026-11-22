#!/usr/bin/env bash
# Run a backend maintenance module against the same database as the local app:
#   scripts/backend-cli.sh app.shopping.reconcile_unknown --dry-run
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/local-env.sh
source "${REPO_ROOT}/scripts/local-env.sh"

if [[ $# -lt 1 ]]; then
  printf '%s\n' "Usage: scripts/backend-cli.sh <python module> [args...]" >&2
  exit 2
fi

export_local_database_url
# Host and database name only; never credentials.
database="${DATABASE_URL##*@}"
printf 'database=%s\n' "${database%%\?*}" >&2

cd "${REPO_ROOT}/backend"
exec uv run --with-requirements requirements.txt python -m "$@"
