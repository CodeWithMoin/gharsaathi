SHELL := /bin/bash

.PHONY: start start-demo help setup dev dev-live up stop restart status logs open swiggy-doctor zepto-bridge zepto-doctor zepto-preview seed-sample-household unseed-sample-household reconcile-unknown vapid-keys vobiz-check check check-full test test-unit test-module test-integration test-failures clean-run live-smoke live-smoke-cart

start:
	@./scripts/start.sh live

start-demo:
	@./scripts/start.sh demo

help:
	@echo "GharSaathi local commands"
	@echo "  make start         One command: checks setup, starts everything (live stores), opens the app"
	@echo "  make start-demo    Same, with the static demo catalogue (no store calls)"
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
	@echo "  make vobiz-check  Read-only Vobiz check: lists your numbers (masked); rider assist beta"
	@echo "  make zepto-doctor  Read-only Zepto local setup and callback readiness check"
	@echo "  make zepto-preview Run a temporary-item, unconfirmed live preview (explicit opt-in)"
	@echo "  make seed-sample-household   Demo only: load fictional sample orders + memories for your user"
	@echo "  make unseed-sample-household Remove only that seeded sample data"
	@echo "  make reconcile-unknown ARGS=--dry-run  Check unknown Zepto orders against order history (--apply settles single matches)"
	@echo "  make vapid-keys    Generate browser-notification (Web Push) keys; prints them here only"
	@echo "  make test          Run every test layer (backend + frontend)"
	@echo "  make test-unit     Pure-logic tests (backend + frontend)"
	@echo "  make test-module   Module tests with in-memory fakes (backend + frontend)"
	@echo "  make test-integration  Real app + throwaway PostgreSQL database (backend)"
	@echo "  make test-failures Only the failure-mode tests named in docs/FAILURE_MODES.md"
	@echo "  make check         All tests, lint, and type-check"
	@echo "  make check-full    Tests, lint, type-check, and production frontend build"
	@echo "  make live-smoke    Opt-in READ-ONLY real-provider smoke (owner consent; docs/LIVE_SMOKE.md)"
	@echo "  make live-smoke-cart Opt-in CART-MUTATING Zepto preview smoke (never confirms an order)"

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

vobiz-check:
	@./scripts/backend-cli.sh app.rider.vobiz_check

zepto-doctor:
	@./scripts/zepto-doctor.sh

zepto-preview:
	@./scripts/zepto-preview.sh --seed-preview-cleanup

seed-sample-household:
	@./scripts/backend-cli.sh app.memory.sample_seed $(if $(USER_ID),--user-id $(USER_ID)) $(if $(HOUSEHOLD),--household $(HOUSEHOLD))

unseed-sample-household:
	@./scripts/backend-cli.sh app.memory.sample_seed --remove $(if $(USER_ID),--user-id $(USER_ID))

# The same database as `make start` (scripts/local-env.sh). ARGS is required: --dry-run or --apply.
reconcile-unknown:
	@test -n "$(ARGS)" || { echo "Usage: make reconcile-unknown ARGS=--dry-run  (or ARGS=--apply)" >&2; exit 2; }
	@./scripts/backend-cli.sh app.shopping.reconcile_unknown $(ARGS)

# Prints the private key to this terminal only; paste the lines into .env.
vapid-keys:
	@cd backend && PYTHONPATH=. uv run --with-requirements requirements.txt python -m app.notifications.vapid

check:
	@./scripts/verify.sh

check-full:
	@./scripts/verify.sh --full

PYTEST := cd backend && PYTHONPATH=. uv run --with-requirements requirements.txt pytest -q

test:
	@$(PYTEST)
	@cd frontend && npm test

test-unit:
	@$(PYTEST) -m unit
	@cd frontend && npm run test:unit

test-module:
	@$(PYTEST) -m module
	@cd frontend && npm run test:module

test-integration:
	@$(PYTEST) -m integration

test-failures:
	@$(PYTEST) -m failure_mode -rxX
	@cd frontend && node --test tests/unit/failure-modes.test.mjs

live-smoke:
	@./scripts/live-smoke.sh

live-smoke-cart:
	@./scripts/live-smoke.sh --cart

clean-run:
	@./scripts/app.sh clean
