# Testing

GharSaathi has three test layers. Every backend test carries exactly one pytest
marker (`unit`, `module`, `integration`); `--strict-markers` is on, and an
unclassified test file stops collection.

| Layer | Scope | Boundaries | Where |
| --- | --- | --- | --- |
| **unit** | One function or class | No I/O, database or network | `backend/tests/unit/`, `frontend/tests/unit/` |
| **module** | One module/package through its public interface | In-memory fakes, SQLite, or the app with fakes at its edges | `backend/tests/module/`, `frontend/tests/module/` |
| **integration** | The real FastAPI app end to end | A throwaway PostgreSQL database; only external providers are fakes (or `COMMERCE_MODE=demo`) | `backend/tests/integration/` |

Older top-level files (`backend/tests/test_*.py`, `frontend/tests/*.test.mjs`)
keep their names. Their layer is listed in `backend/tests/layers.py` and in the
frontend `test:unit` / `test:module` scripts. Put new tests in a layer
directory instead. The directory sets the marker.

## Running

```bash
make test               # every layer, backend + frontend
make test-unit          # backend -m unit + npm run test:unit
make test-module        # backend -m module + npm run test:module
make test-integration   # backend -m integration (needs PostgreSQL)
make check              # all tests + frontend lint + tsc (scripts/verify.sh)
```

Directly:

```bash
cd backend && PYTHONPATH=. uv run --with-requirements requirements.txt pytest -q -m integration
cd frontend && npm run test:unit
```

## Integration database

`backend/tests/integration/conftest.py`:

- connects to `postgresql://$USER@127.0.0.1:5432/postgres`, or to `TEST_DATABASE_ADMIN_URL` if you set it;
- creates `equal_ai_test_<random>`, runs `alembic upgrade head` in a subprocess with `DATABASE_URL` pointing at that database, and drops it with `WITH (FORCE)` when the session ends;
- truncates every table before each test;
- skips every integration test, with the reason shown, when PostgreSQL is unreachable. Set `REQUIRE_INTEGRATION_DB=1` (e.g. in CI) to fail instead of skipping.

The live `equal_ai` database is never used. The fixture asserts the test
database prefix before any app code runs.

### Fixtures

- `integration_settings`: sets `COMMERCE_MODE=demo`, clears OpenAI/JEV/Clerk keys, points support, tracking and token stores at `tmp_path`, disables the tracking poller, clears every `lru_cache`d service, and swaps in a `NullPool` engine. A `NullPool` engine works on both TestClient's loop and pytest-asyncio's loop.
- `api`: `TestClient(app)` with the lifespan running, so the tracking service is on `app.state`.
- `override(dependency, replacement)`: a `dependency_overrides` entry that is removed after the test.
- `session_factory`: the app's session factory on the test database, for service-level concurrency tests.
- `tests/integration/helpers.as_user(id)`: the `X-Test-User-Id` header read by the Clerk override in `tests/conftest.py`.
- Legal consent: `tests/conftest.py` treats every test identity as having accepted the current terms (`has_accepted_consent` is overridden; identity is still verified). Request the `enforce_legal_consent` fixture to use the real database check, as `tests/integration/test_consent_enforcement.py` does.

Route-table helpers (`walk`, `operations`, `requires_user`, `PUBLIC_ROUTES`)
are in `backend/tests/routes.py`. `walk` handles FastAPI's lazily included
routers.

## Rules

- **No live providers.** No Swiggy/Zepto OAuth, MCP, cart, checkout or order calls, and no OpenAI calls. Use the demo services, or a fake installed with `override`. The Zepto COD gateway in integration tests is a recording fake. Tests assert on its call list.
- **No real orders, ever.** A test that needs the order step uses `RecordingZeptoGateway` (see `test_shopping_checkout.py`).
- Integration tests must not touch ports 3003/8000/80 or the `equal_ai` database.
- Known bugs get a test marked `xfail(strict=True)` that states the reason. When the bug is fixed, the test starts passing, the strict xfail fails, and the marker must be removed.
- A public route (no Clerk session) must be listed with a reason in `tests/routes.py::PUBLIC_ROUTES`. `tests/module/test_route_table.py` and `tests/integration/test_auth_sweep.py` enforce this. The route-table test also fails if a parameterised route captures a later literal route.
