#!/usr/bin/env bash
# Run the repository's provider-free automated verification in one command.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FULL=false

usage() {
  cat <<'EOF'
Usage: ./scripts/verify.sh [--full]

Runs every backend test layer (unit, module, integration) plus frontend tests,
lint, and TypeScript checks. Integration tests use a throwaway PostgreSQL
database and skip if PostgreSQL is unreachable (see docs/TESTING.md).
--full also creates a production frontend build. No commerce provider is called.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --full) FULL=true ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

printf '%s\n' "Running backend unit, module and integration tests (mocked providers only)"
(cd "${REPO_ROOT}/backend" && PYTHONPATH=. uv run \
  --with-requirements requirements.txt pytest -q)

printf '%s\n' "Running frontend tests, lint, and type checks"
(cd "${REPO_ROOT}/frontend" && npm test && npm run lint && npx tsc --noEmit)

if [[ "${FULL}" == true ]]; then
  printf '%s\n' "Creating the production frontend build"
  (cd "${REPO_ROOT}/frontend" && npm run build)
fi

printf '%s\n' "Verification passed. No live cart, checkout, payment, or order call was made."
