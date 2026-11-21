#!/usr/bin/env bash
# Check local Swiggy development prerequisites without contacting Swiggy.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_PATH="${ENV_FILE:-${REPO_ROOT}/.env}"
failures=0

pass() { printf '  ✓ %s\n' "$1"; }
fail() { printf '  ✗ %s\n' "$1" >&2; failures=1; }

printf '%s\n' "Swiggy local readiness (read-only)"

if [[ -f "${ENV_PATH}" ]]; then
  pass "environment file found (${ENV_PATH})"
else
  fail "missing ${ENV_PATH}; run make setup"
fi

if command -v uv >/dev/null 2>&1; then
  pass "uv is available"
else
  fail "uv is not installed"
fi

if command -v node >/dev/null 2>&1; then
  pass "Node.js is available"
else
  fail "Node.js is not installed"
fi

if curl -fsS --max-time 2 http://127.0.0.1:8000/health >/dev/null 2>&1; then
  pass "backend health endpoint responds"
else
  fail "backend is unavailable; run make dev-live"
fi

if [[ "${failures}" -ne 0 ]]; then
  printf '%s\n' "No Swiggy OAuth, MCP, search, cart, checkout, or order action was attempted." >&2
  exit 1
fi

printf '%s\n' "Ready to start Swiggy OAuth from the browser. No provider action was attempted."
