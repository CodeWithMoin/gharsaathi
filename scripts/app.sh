#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="${REPO_ROOT}/.run"
PID_FILE="${RUN_DIR}/app.pid"
LOG_FILE="${RUN_DIR}/app.log"

is_running() {
  [[ -f "${PID_FILE}" ]] || return 1
  local app_pid
  app_pid="$(cat "${PID_FILE}")"
  kill -0 "${app_pid}" 2>/dev/null
}

start_app() {
  if is_running; then
    echo "GharSaathi is already running (PID $(cat "${PID_FILE}"))."
    return 0
  fi
  mkdir -p "${RUN_DIR}"
  rm -f "${PID_FILE}"
  nohup "${REPO_ROOT}/scripts/dev.sh" >"${LOG_FILE}" 2>&1 &
  echo $! >"${PID_FILE}"
  sleep 2
  if is_running; then
    echo "GharSaathi started. App: http://localhost:3003"
    echo "Logs: make logs"
  else
    echo "GharSaathi did not start. Check ${LOG_FILE}." >&2
    tail -n 40 "${LOG_FILE}" >&2 || true
    return 1
  fi
}

stop_app() {
  if ! is_running; then
    echo "GharSaathi is not running from make up."
    rm -f "${PID_FILE}"
    return 0
  fi
  local app_pid
  app_pid="$(cat "${PID_FILE}")"
  kill "${app_pid}"
  for _ in {1..20}; do
    kill -0 "${app_pid}" 2>/dev/null || break
    sleep 0.25
  done
  rm -f "${PID_FILE}"
  echo "GharSaathi stopped."
}

show_status() {
  if is_running; then
    echo "Launcher: running (PID $(cat "${PID_FILE}"))"
  else
    echo "Launcher: stopped"
  fi
  if curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then
    echo "Backend:  healthy · http://127.0.0.1:8000"
  else
    echo "Backend:  unavailable"
  fi
  if nc -z 127.0.0.1 5432 >/dev/null 2>&1; then
    echo "Database: running · PostgreSQL on 127.0.0.1:5432"
  else
    echo "Database: unavailable"
  fi
  if curl -fsS http://127.0.0.1:3003/ >/dev/null 2>&1; then
    echo "Frontend: healthy · http://localhost:3003"
  else
    echo "Frontend: unavailable"
  fi
  if curl -fsS http://127.0.0.1/health | grep -q 'oauth-callback-bridge'; then
    echo "Zepto OAuth bridge: healthy · http://localhost/callback"
  else
    echo "Zepto OAuth bridge: unavailable · run make zepto-bridge"
  fi
}

case "${1:-help}" in
  up) start_app ;;
  stop) stop_app ;;
  restart) stop_app; start_app ;;
  status) show_status ;;
  logs) touch "${LOG_FILE}"; tail -f "${LOG_FILE}" ;;
  clean) stop_app; rm -rf "${RUN_DIR}" ;;
  *) echo "Usage: $0 {up|stop|restart|status|logs|clean}" >&2; exit 2 ;;
esac
