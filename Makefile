SHELL := /bin/bash

.PHONY: help setup dev dev-live up stop restart status logs open swiggy-doctor zepto-bridge zepto-doctor zepto-preview check check-full test clean-run

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
	@echo "  make swiggy-doctor Read-only Swiggy local readiness check"
	@echo "  make zepto-bridge  Run Zepto's port-80 OAuth bridge (asks for Mac password)"
	@echo "  make zepto-doctor  Read-only Zepto local setup and callback readiness check"
	@echo "  make zepto-preview Run a temporary-item, unconfirmed live preview (explicit opt-in)"
	@echo "  make check         Lint, type-check, and run backend tests"
	@echo "  make check-full    Tests, lint, type-check, and production frontend build"

setup:
	@./scripts/setup.sh

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

swiggy-doctor:
	@./scripts/swiggy-doctor.sh

zepto-bridge:
	@cd backend && sudo uv run --with-requirements requirements.txt uvicorn app.local_callback_bridge:app --host 127.0.0.1 --port 80 --no-access-log

zepto-doctor:
	@./scripts/zepto-doctor.sh

zepto-preview:
	@./scripts/zepto-preview.sh --seed-preview-cleanup

check:
	@./scripts/verify.sh

check-full:
	@./scripts/verify.sh --full

test:
	@cd backend && uv run --with-requirements requirements.txt pytest -q

clean-run:
	@./scripts/app.sh clean
