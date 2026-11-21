SHELL := /bin/bash

.PHONY: help setup dev dev-live up stop restart status logs open zepto-bridge check test clean-run

help:
	@echo "GharSaathi local commands"
	@echo "  make setup         Install frontend dependencies and warm backend dependencies"
	@echo "  make dev           Run static demo (no commerce API/MCP calls)"
	@echo "  make dev-live      Run with live commerce providers enabled"
	@echo "  make up            Run frontend + backend in the background"
	@echo "  make stop          Stop a background launch"
	@echo "  make restart       Restart a background launch"
	@echo "  make status        Show process and health status"
	@echo "  make logs          Follow background logs"
	@echo "  make open          Open http://localhost:3003"
	@echo "  make zepto-bridge  Run Zepto's port-80 OAuth bridge (asks for Mac password)"
	@echo "  make check         Lint, type-check, and run backend tests"

setup:
	@cd frontend && npm install
	@cd backend && uv run --with-requirements requirements.txt python -c "import app.main; print('Backend dependencies ready')"

dev:
	@./scripts/dev.sh

dev-live:
	@COMMERCE_MODE=live ./scripts/dev.sh

up:
	@./scripts/app.sh up

stop:
	@./scripts/app.sh stop

restart:
	@./scripts/app.sh restart

status:
	@./scripts/app.sh status

logs:
	@./scripts/app.sh logs

open:
	@open http://localhost:3003

zepto-bridge:
	@cd backend && sudo uv run --with-requirements requirements.txt uvicorn app.local_callback_bridge:app --host 127.0.0.1 --port 80 --no-access-log

check: test
	@cd frontend && npm run lint
	@cd frontend && npx tsc --noEmit

test:
	@cd backend && uv run --with-requirements requirements.txt pytest -q

clean-run:
	@./scripts/app.sh clean
