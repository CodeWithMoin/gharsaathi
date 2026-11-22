#!/usr/bin/env bash
# One command to run GharSaathi locally. Asks only for what is actually missing.
#   ./scripts/start.sh          live stores (default)
#   ./scripts/start.sh demo     static demo catalogue, no store calls
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:-live}"
APP_URL="http://localhost:3003"
problems=()

say() { printf '%s\n' "$*"; }
need() { command -v "$1" >/dev/null 2>&1 || problems+=("Install $1 ($2)"); }

case "${MODE}" in
  live|demo) ;;
  *) say "Usage: $0 [live|demo]" >&2; exit 2 ;;
esac

need uv "https://docs.astral.sh/uv/"
need node "https://nodejs.org"
need npm "https://nodejs.org"
need psql "brew install postgresql@17"

env_file="${REPO_ROOT}/.env"
if [[ ! -f "${env_file}" ]]; then
  problems+=("Create .env: cp .env.example .env, then add OPENAI_API_KEY")
elif ! grep -Eq '^OPENAI_API_KEY=.+' "${env_file}"; then
  problems+=("Add OPENAI_API_KEY to .env (needed for voice and chat)")
fi
if [[ ! -f "${REPO_ROOT}/frontend/.env.local" ]] || ! grep -Eq '^NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=.+' "${REPO_ROOT}/frontend/.env.local"; then
  problems+=("Add Clerk keys to frontend/.env.local (see frontend/.env.local.example)")
fi

if ((${#problems[@]})); then
  say "Before GharSaathi can start:"
  for item in "${problems[@]}"; do say "  - ${item}"; done
  exit 1
fi

if [[ ! -d "${REPO_ROOT}/frontend/node_modules" ]]; then
  say "Installing frontend packages (first run only)…"
  (cd "${REPO_ROOT}/frontend" && npm ci --no-audit --no-fund >/dev/null)
fi

mkdir -p "${REPO_ROOT}/.run"
bridge_ok() { curl -fsS --max-time 2 http://127.0.0.1/health 2>/dev/null | grep -q 'oauth-callback-bridge'; }
if [[ "${MODE}" == "live" ]] && ! bridge_ok; then
  say "Zepto login needs a small helper on port 80. macOS will ask for your password once."
  sudo -v
  (cd "${REPO_ROOT}/backend" && nohup sudo uv run --with-requirements requirements.txt \
    uvicorn app.local_callback_bridge:app --host 127.0.0.1 --port 80 --no-access-log \
    >"${REPO_ROOT}/.run/zepto-bridge.log" 2>&1 &)
  for _ in {1..20}; do bridge_ok && break; sleep 0.5; done
  bridge_ok || say "Zepto helper did not start (see .run/zepto-bridge.log). Swiggy and demo still work."
fi

COMMERCE_MODE="${MODE}" "${REPO_ROOT}/scripts/app.sh" restart >/dev/null

say "Starting GharSaathi (${MODE} stores)…"
for _ in {1..60}; do
  if curl -fsS --max-time 2 http://127.0.0.1:8000/health >/dev/null 2>&1 \
    && curl -fsS --max-time 5 "${APP_URL}" >/dev/null 2>&1; then
    say "Ready: ${APP_URL}"
    command -v open >/dev/null 2>&1 && open "${APP_URL}"
    exit 0
  fi
  sleep 1
done
say "GharSaathi did not become ready. Last log lines:" >&2
tail -n 30 "${REPO_ROOT}/.run/app.log" >&2 || true
exit 1
