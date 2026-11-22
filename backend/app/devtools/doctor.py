"""``make doctor``: one read-only table of local readiness, green / amber / red.

Every check is read-only: HTTP GETs to the local app and helper, one cheap
OpenAI ``GET /models``, a Jev reachability ping, a SELECT on the local
database, and decrypting the local provider connection files to read flags and
times only.

Store accounts are never touched: Swiggy and Zepto readiness comes only from
our own stored state (token expiry, recorded rate-limit cooldown, address
snapshot age), so a doctor run makes zero Swiggy/Zepto calls and cannot cause
a rate limit. OpenAI and Jev get at most one call each per run (a
``ProviderCallBudget`` of 1); ``--offline`` skips them too. No cart, order or
payment tool exists here. Values from ``.env`` are never printed: details only say
whether something is set, valid or reachable. A red line carries the exact
command that fixes it; the process exits 1 when any line is red.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from app.core.config import REPOSITORY_ROOT, Settings
from app.devtools.budget import ProviderBudgetExceeded, ProviderCallBudget

Status = Literal["green", "amber", "red"]
BACKEND_URL = "http://127.0.0.1:8000/health"
FRONTEND_URL = "http://127.0.0.1:3003/"
BRIDGE_URL = "http://127.0.0.1/health"
PG_START = "brew services start postgresql@17   # or: make up (starts it)"
MIGRATE = "cd backend && uv run --with-requirements requirements.txt alembic upgrade head"


@dataclass
class Check:
    name: str
    status: Status
    detail: str
    fix: str | None = None


@dataclass
class HttpResult:
    status: int
    text: str
    ms: float


@dataclass
class DbSnapshot:
    revisions: list[str]
    # One row per person with a Zepto address state: (selected, refreshed_at).
    zepto_addresses: list[tuple[bool, datetime | None]] = field(default_factory=list)
    locked_baskets: int = 0


HttpGet = Callable[[str, dict[str, str], float], Awaitable[HttpResult]]
DbRead = Callable[[str], Awaitable[DbSnapshot]]
Runner = Callable[[list[str]], str | None]


@dataclass
class DoctorEnv:
    settings: Settings
    http_get: HttpGet
    db_read: DbRead
    alembic_heads: Callable[[Path], set[str]]
    run: Runner
    repo_root: Path = REPOSITORY_ROOT
    database_url: str = ""
    frontend_env: dict[str, str] = field(default_factory=dict)
    now: datetime = field(default_factory=lambda: datetime.now(UTC))
    # Store files dev.sh may point at instead of this checkout's (see scripts/dev.sh).
    swiggy_store_fallback: Path | None = None
    # --offline: no network call outside this machine (OpenAI and Jev are skipped).
    offline: bool = False
    # One call per external service per run.
    budgets: dict[str, ProviderCallBudget] = field(
        default_factory=lambda: {name: ProviderCallBudget(max_calls=1) for name in ("openai", "jev")}
    )

    # The checkout the running app was started from (else this one); its .data,
    # .run/launch.json and frontend/.env.local are what the app actually uses.
    app_root: Path | None = None

    def app_path(self, path: Path) -> Path:
        root = self.app_root
        if root is None or root.resolve() == self.repo_root.resolve():
            return path
        try:
            return root / path.relative_to(self.repo_root)
        except ValueError:
            return path

    async def external_get(self, service: str, url: str, headers: dict[str, str], timeout: float) -> HttpResult:
        await self.budgets[service].spend(service, "GET " + url.rsplit("/", 1)[-1])
        return await self.http_get(url, headers, timeout)


# Probes ------------------------------------------------------------------------
async def http_get(url: str, headers: dict[str, str], timeout: float) -> HttpResult:
    import httpx

    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        response = await client.get(url, headers=headers)
    return HttpResult(response.status_code, response.text[:2000], round((time.perf_counter() - started) * 1000, 1))


async def db_read(url: str) -> DbSnapshot:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(url, connect_args={"timeout": 3} if url.startswith("postgresql+asyncpg") else {})
    try:
        async with engine.connect() as connection:
            revisions = [row[0] for row in await connection.execute(text("SELECT version_num FROM alembic_version"))]
            try:
                addresses = [
                    (row[0] is not None, row[1])
                    for row in await connection.execute(
                        text("SELECT selected_address_id, refreshed_at FROM provider_address_states WHERE provider = 'zepto'")
                    )
                ]
                locked = (
                    await connection.execute(
                        text("SELECT count(*) FROM shopping_baskets WHERE status = 'checkout_pending'")
                    )
                ).scalar_one()
            except Exception:  # noqa: BLE001 - an older schema still reports its revision
                addresses, locked = [], 0
            return DbSnapshot(revisions, addresses, int(locked or 0))
    finally:
        await engine.dispose()


def alembic_heads(repo_root: Path = REPOSITORY_ROOT) -> set[str]:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(repo_root / "backend" / "alembic.ini"))
    config.set_main_option("script_location", str(repo_root / "backend" / "alembic"))
    return set(ScriptDirectory.from_config(config).get_heads())


def running_app_root(run: Runner | None = None) -> Path | None:
    """The checkout the app on :8000 was started from, if one is running."""

    probe = DoctorEnv(
        settings=Settings(_env_file=None),
        http_get=http_get,
        db_read=db_read,
        alembic_heads=alembic_heads,
        run=run or run_command,
    )
    return running_checkout(probe)


def run_command(argv: list[str]) -> str | None:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=value lines; never executed. Values are only tested for presence."""

    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip().removeprefix("export ").strip()] = value.strip().strip("'\"")
    return values


def default_env(settings: Settings, database_url: str | None = None, *, offline: bool = False) -> DoctorEnv:
    fallback = Path.home() / "Downloads/equal-ai/backend/.data/swiggy_connection.enc"
    return DoctorEnv(
        settings=settings,
        http_get=http_get,
        db_read=db_read,
        alembic_heads=alembic_heads,
        run=run_command,
        database_url=database_url or os.environ.get("DATABASE_URL") or settings.database_url,
        frontend_env=read_env_file(REPOSITORY_ROOT / "frontend" / ".env.local"),
        swiggy_store_fallback=fallback,
        offline=offline,
    )


# Helpers -----------------------------------------------------------------------
def _secret(value: Any) -> str:
    getter = getattr(value, "get_secret_value", None)
    return (getter() if callable(getter) else str(value or "")).strip()


def _duration(seconds: float) -> str:
    seconds = abs(int(seconds))
    if seconds < 90:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 90:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h {minutes % 60}m"
    return f"{hours // 24}d"


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _encryption_key(env: DoctorEnv | Settings) -> str | None:
    settings = env.settings if isinstance(env, DoctorEnv) else env
    if settings.app_encryption_key.strip():
        return settings.app_encryption_key.strip()
    path = env.app_path(settings.local_encryption_key_path) if isinstance(env, DoctorEnv) else settings.local_encryption_key_path
    try:
        # Read only: unlike the app, the doctor never creates a key file.
        return path.read_text(encoding="ascii").strip() or None
    except OSError:
        return None


def read_connection(path: Path, key: str | None) -> dict[str, Any] | None | Literal["unreadable"]:
    """Flags and times from an encrypted provider connection; token values are dropped at once."""

    if not path.exists():
        return None
    if not key:
        return "unreadable"
    from cryptography.fernet import Fernet, InvalidToken

    try:
        payload = json.loads(Fernet(key.encode("ascii")).decrypt(path.read_bytes()))
    except (InvalidToken, ValueError, OSError, UnicodeEncodeError):
        return "unreadable"
    if not isinstance(payload, dict):
        return "unreadable"

    def when(name: str) -> datetime | None:
        raw = payload.get(name)
        try:
            return _aware(datetime.fromisoformat(raw)) if isinstance(raw, str) else None
        except ValueError:
            return None

    return {
        "connected": bool(payload.get("access_token")),
        "refreshable": bool(payload.get("refresh_token")),
        "expires_at": when("expires_at"),
        "rate_limited_until": when("rate_limited_until"),
    }


def launch_info(checkout: Path) -> dict[str, Any]:
    try:
        data = json.loads((checkout / ".run" / "launch.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def commerce_mode(env: DoctorEnv) -> str:
    """The running app's mode from its launch record; unknown means check as live."""

    mode = launch_info(env.app_root or env.repo_root).get("commerce_mode")
    return mode if mode in {"demo", "live"} else "unknown"


# Checks ------------------------------------------------------------------------
async def check_backend(env: DoctorEnv) -> Check:
    try:
        result = await env.http_get(BACKEND_URL, {}, 2.0)
    except Exception:  # noqa: BLE001
        return Check("Backend :8000", "red", "not answering", "make up   # or: make start")
    if result.status == 200 and '"ok"' in result.text:
        return Check("Backend :8000", "green", f"healthy ({result.ms:.0f} ms)")
    return Check("Backend :8000", "red", f"/health answered {result.status}", "make restart")


async def check_frontend(env: DoctorEnv) -> Check:
    try:
        result = await env.http_get(FRONTEND_URL, {}, 8.0)
    except Exception:  # noqa: BLE001
        return Check("Frontend :3003", "red", "not answering", "make up   # or: make start")
    if result.status < 500:
        return Check("Frontend :3003", "green", f"up ({result.ms:.0f} ms)")
    return Check("Frontend :3003", "red", f"answered {result.status}", "make restart   # then: make logs")


async def check_database(env: DoctorEnv, snapshot: DbSnapshot | Exception | None) -> Check:
    target = env.database_url.rsplit("@", 1)[-1].split("?", 1)[0] or "?"
    if isinstance(snapshot, Exception) or snapshot is None:
        reason = type(snapshot).__name__ if snapshot is not None else "no answer"
        return Check("Database", "red", f"{target} unreachable ({reason})", PG_START)
    try:
        # The migrations of the checkout the app runs from, not this one.
        heads = env.alembic_heads(env.app_root or env.repo_root)
    except Exception:  # noqa: BLE001
        return Check("Database", "amber", f"{target} reachable; migration heads unreadable")
    if set(snapshot.revisions) == heads:
        return Check("Database", "green", f"{target} · alembic at head ({', '.join(sorted(heads))[:12]})")
    return Check(
        "Database",
        "red",
        f"{target} · alembic {','.join(snapshot.revisions) or 'empty'} ≠ head {','.join(sorted(heads))}",
        MIGRATE,
    )


async def check_zepto_auth(env: DoctorEnv) -> Check:
    if commerce_mode(env) == "demo":
        return Check("Zepto login", "amber", "demo mode · stores not used", "make start   # live stores")
    connection = read_connection(env.app_path(env.settings.zepto_token_store_path), _encryption_key(env))
    fix = "make zepto-bridge, then open http://localhost:3003 → Settings → Zepto → Connect"
    if connection is None:
        return Check("Zepto login", "red", "not connected", fix)
    if connection == "unreadable":
        return Check("Zepto login", "red", "connection file unreadable with the configured key", fix)
    if not connection["connected"]:
        return Check("Zepto login", "red", "signed out (no session)", fix)
    until = connection["rate_limited_until"]
    if until is not None and until > env.now:
        left = (until - env.now).total_seconds()
        return Check("Zepto login", "amber", f"connected · rate-limit cooldown {_duration(left)} left (no Zepto calls until then)")
    expires = connection["expires_at"]
    if expires is None:
        return Check("Zepto login", "green", "connected · no expiry reported")
    left = (expires - env.now).total_seconds()
    if left > 3600:
        return Check("Zepto login", "green", f"connected · token expires in {_duration(left)}")
    if connection["refreshable"]:
        state = "expired" if left <= 0 else "expires soon"
        return Check("Zepto login", "amber", f"connected · token {state} ({_duration(left)}) · refresh token present")
    return Check("Zepto login", "red", f"token {'expired' if left <= 0 else 'expires'} in {_duration(left)}, no refresh", fix)


async def check_zepto_address(env: DoctorEnv, snapshot: DbSnapshot | Exception | None) -> Check:
    fix = "open http://localhost:3003 → Settings → Delivery address → choose a Zepto address"
    if commerce_mode(env) == "demo":
        return Check("Zepto address", "amber", "demo mode · stores not used")
    if not isinstance(snapshot, DbSnapshot):
        return Check("Zepto address", "amber", "database unavailable; not checked")
    selected = [refreshed for chosen, refreshed in snapshot.zepto_addresses if chosen]
    if not selected:
        return Check("Zepto address", "red", "no Zepto delivery address selected", fix)
    newest = max((_aware(value) for value in selected if value is not None), default=None)
    if newest is None:
        return Check("Zepto address", "green", "selected")
    age = (env.now - newest).total_seconds()
    limit = env.settings.zepto_address_refresh_max_age_min * 60
    status: Status = "green" if age <= limit else "amber"
    note = "" if status == "green" else " (refreshes at next app load)"
    return Check("Zepto address", status, f"selected · snapshot {_duration(age)} old{note}")


async def check_swiggy(env: DoctorEnv) -> Check:
    if commerce_mode(env) == "demo":
        return Check("Swiggy", "amber", "demo mode · stores not used")
    path = env.app_path(env.settings.swiggy_token_store_path)
    if not path.exists() and env.swiggy_store_fallback is not None and env.swiggy_store_fallback.exists():
        path = env.swiggy_store_fallback
    connection = read_connection(path, _encryption_key(env))
    if connection is None or connection == "unreadable" or not connection["connected"]:
        return Check("Swiggy", "amber", "not connected (optional)", "open http://localhost:3003 → Settings → Swiggy → Connect")
    until = connection["rate_limited_until"]
    if until is not None and until > env.now:
        left = (until - env.now).total_seconds()
        return Check("Swiggy", "amber", f"connected · rate-limit cooldown {_duration(left)} left")
    return Check("Swiggy", "green", "connected · no cooldown")


async def check_openai(env: DoctorEnv) -> Check:
    key = _secret(env.settings.openai_api_key)
    fix = "set OPENAI_API_KEY in .env, then make restart"
    if not key:
        return Check("OpenAI", "red", "OPENAI_API_KEY not set", fix)
    if env.offline:
        return Check("OpenAI", "amber", "key set · not called (--offline)")
    url = env.settings.openai_base_url.rstrip("/") + "/models"
    try:
        result = await env.external_get("openai", url, {"Authorization": f"Bearer {key}"}, 6.0)
    except ProviderBudgetExceeded:
        return Check("OpenAI", "amber", "not called again (one call per run)")
    except Exception as exc:  # noqa: BLE001
        return Check("OpenAI", "red", f"unreachable ({type(exc).__name__})", "check your network, then make doctor")
    if result.status == 200:
        return Check("OpenAI", "green", f"key works · models listed ({result.ms:.0f} ms)")
    if result.status in {401, 403}:
        return Check("OpenAI", "red", f"key rejected ({result.status})", fix)
    return Check("OpenAI", "amber", f"models list answered {result.status} ({result.ms:.0f} ms)")


async def check_jev(env: DoctorEnv) -> Check:
    key = _secret(env.settings.jev_api_key)
    enabled = env.settings.jev_reranking_enabled
    if not key:
        detail = "not configured (optional reranker)"
        return Check("Jev", "red" if enabled else "amber", detail, "set JEV_API_KEY in .env" if enabled else None)
    if not enabled:
        return Check("Jev", "amber", "key set · reranking off (JEV_RERANKING_ENABLED=false)")
    if env.offline:
        return Check("Jev", "amber", "enabled · not pinged (--offline)")
    try:
        result = await env.external_get("jev", env.settings.jev_base_url.rstrip("/"), {"Authorization": f"Bearer {key}"}, 3.0)
    except ProviderBudgetExceeded:
        return Check("Jev", "amber", "not pinged again (one call per run)")
    except Exception as exc:  # noqa: BLE001
        return Check("Jev", "red", f"enabled but unreachable ({type(exc).__name__})", "set JEV_RERANKING_ENABLED=false in .env, then make restart")
    budget = env.settings.jev_timeout_seconds * 1000
    status: Status = "green" if result.ms <= budget else "amber"
    return Check("Jev", status, f"enabled · ping {result.ms:.0f} ms (timeout {budget:.0f} ms)")


async def check_vapid(env: DoctorEnv) -> Check:
    from app.notifications.vapid import public_key_for

    public = env.settings.web_push_vapid_public_key.strip()
    private = _secret(env.settings.web_push_vapid_private_key)
    subject = env.settings.web_push_subject.strip()
    fix = "make vapid-keys   # paste the three lines into .env, then make restart"
    if not (public or private or subject):
        return Check("Web Push (VAPID)", "amber", "off · browser notifications hidden", fix)
    missing = [name for name, value in (("PUBLIC", public), ("PRIVATE", private), ("SUBJECT", subject)) if not value]
    if missing:
        return Check("Web Push (VAPID)", "red", f"incomplete · missing WEB_PUSH_VAPID_{'/'.join(missing)}", fix)
    if public_key_for(private) != public:
        return Check("Web Push (VAPID)", "red", "public and private keys are not a pair", fix)
    return Check("Web Push (VAPID)", "green", "valid key pair")


async def check_clerk(env: DoctorEnv) -> Check:
    frontend_env = env.frontend_env
    if env.app_root is not None and not frontend_env.get("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY"):
        frontend_env = read_env_file(env.app_root / "frontend" / ".env.local")
    backend_secret = _secret(env.settings.clerk_secret_key) or frontend_env.get("CLERK_SECRET_KEY", "")
    missing = []
    if not frontend_env.get("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY"):
        missing.append("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY (frontend/.env.local)")
    if not backend_secret:
        missing.append("CLERK_SECRET_KEY")
    if missing:
        return Check("Clerk", "red", "missing " + ", ".join(missing), "cp frontend/.env.local.example frontend/.env.local   # add the Clerk keys")
    return Check("Clerk", "green", "publishable + secret keys present")


async def check_rider(env: DoctorEnv) -> Check:
    s = env.settings
    if not s.rider_assist_enabled:
        return Check("Rider assist", "green", "off (beta, RIDER_ASSIST_ENABLED=false)")
    required = {
        "RIDER_ASSIST_OWNER_USER_ID": s.rider_assist_owner_user_id.strip(),
        "RIDER_ASSIST_PUBLIC_URL": s.rider_assist_public_url.strip(),
        "VOBIZ_AUTH_ID": s.vobiz_auth_id.strip(),
        "VOBIZ_AUTH_TOKEN": _secret(s.vobiz_auth_token),
    }
    missing = [name for name, value in required.items() if not value]
    if len(_secret(s.rider_assist_stream_secret)) < 32:
        missing.append("RIDER_ASSIST_STREAM_SECRET (≥32 chars)")
    if missing:
        return Check("Rider assist", "red", "enabled but missing " + ", ".join(missing), "add them to .env (docs/RIDER_ASSIST.md), then make restart")
    return Check("Rider assist", "green", "enabled · config complete · make vobiz-check to verify numbers")


async def check_bridge(env: DoctorEnv) -> Check:
    try:
        result = await env.http_get(BRIDGE_URL, {}, 2.0)
    except Exception:  # noqa: BLE001
        result = None
    if result is not None and "oauth-callback-bridge" in result.text:
        return Check("Zepto bridge :80", "green", "callback bridge up")
    return Check("Zepto bridge :80", "amber", "not running (needed only to log in to Zepto)", "make zepto-bridge")


async def check_data_dirs(env: DoctorEnv) -> Check:
    s = env.settings
    paths = [
        env.app_path(path)
        for path in (s.swiggy_token_store_path.parent, s.support_data_path, s.tracking_data_path, env.repo_root / ".run")
    ]
    problems: list[str] = []
    pending: list[str] = []
    for path in dict.fromkeys(paths):
        if path.exists():
            if not os.access(path, os.W_OK):
                problems.append(str(path))
        else:
            parent = next((p for p in path.parents if p.exists()), None)
            if parent is None or not os.access(parent, os.W_OK):
                problems.append(str(path))
            else:
                pending.append(path.name)
    if problems:
        return Check("Data dirs", "red", "not writable: " + ", ".join(problems), "chmod -R u+w " + " ".join(problems))
    if pending:
        return Check("Data dirs", "green", "writable (created on first use: " + ", ".join(pending) + ")")
    return Check("Data dirs", "green", "backend/.data, support, tracking, .run writable")


def running_checkout(env: DoctorEnv) -> Path | None:
    pids = env.run(["lsof", "-nP", "-tiTCP:8000", "-sTCP:LISTEN"])
    for pid in (pids or "").split():
        listing = env.run(["lsof", "-a", "-p", pid, "-d", "cwd", "-Fn"])
        for line in (listing or "").splitlines():
            if line.startswith("n/"):
                top = env.run(["git", "-C", line[1:], "rev-parse", "--show-toplevel"])
                if top:
                    return Path(top)
    return None


async def check_git(env: DoctorEnv) -> Check:
    checkout = env.app_root
    if checkout is None:
        return Check("Running checkout", "amber", "no app on :8000 to inspect", "make up")
    info = launch_info(checkout)
    branch = info.get("branch") or env.run(["git", "-C", str(checkout), "rev-parse", "--abbrev-ref", "HEAD"]) or "?"
    started = info.get("commit")
    head = env.run(["git", "-C", str(checkout), "rev-parse", "--short", "HEAD"]) or "?"
    commit = started or head
    where = checkout.name
    detail = f"{where} · {branch} @ {commit}"
    if branch != "main":
        return Check("Running checkout", "amber", detail + " (not main)", f"make stop && cd <main checkout> && make up   # running from {checkout}")
    if started and head != "?" and not head.startswith(started[: len(head)]) and not started.startswith(head):
        return Check("Running checkout", "amber", detail + f" · checkout now at {head}", "make restart")
    if checkout.resolve() != env.repo_root.resolve():
        return Check("Running checkout", "green", detail + f" · stores/keys read from {checkout}")
    return Check("Running checkout", "green", detail)


async def _snapshot(env: DoctorEnv) -> DbSnapshot | Exception:
    try:
        return await asyncio.wait_for(env.db_read(env.database_url), timeout=6)
    except Exception as exc:  # noqa: BLE001
        return exc


async def run_checks(env: DoctorEnv) -> list[Check]:
    if env.app_root is None:
        env.app_root = running_checkout(env)
    snapshot = await _snapshot(env)
    independent = await asyncio.gather(
        check_backend(env),
        check_frontend(env),
        check_openai(env),
        check_jev(env),
        check_bridge(env),
    )
    backend, frontend, openai, jev, bridge = independent
    return [
        backend,
        frontend,
        await check_database(env, snapshot),
        await check_zepto_auth(env),
        await check_zepto_address(env, snapshot),
        await check_swiggy(env),
        openai,
        jev,
        await check_vapid(env),
        await check_clerk(env),
        await check_rider(env),
        bridge,
        await check_data_dirs(env),
        await check_git(env),
    ]


# Output ------------------------------------------------------------------------
MARKS = {"green": "✓", "amber": "!", "red": "✗"}
COLORS = {"green": "\033[32m", "amber": "\033[33m", "red": "\033[31m"}
RESET = "\033[0m"


def render(checks: Iterable[Check], *, color: bool = False) -> str:
    checks = list(checks)
    width = max((len(check.name) for check in checks), default=10) + 2
    lines = ["GharSaathi doctor (read-only · 0 Swiggy/Zepto calls)", ""]
    for check in checks:
        mark = MARKS[check.status]
        label = f"{mark} {check.status.upper():5}"
        if color:
            label = f"{COLORS[check.status]}{label}{RESET}"
        lines.append(f"  {label}  {check.name.ljust(width)}{check.detail}")
        if check.fix and check.status != "green":
            lines.append(f"  {'':7}  {'':{width}}fix: {check.fix}")
    counts = {status: sum(1 for c in checks if c.status == status) for status in ("green", "amber", "red")}
    lines += ["", f"{counts['green']} green · {counts['amber']} amber · {counts['red']} red"]
    return "\n".join(lines)


def as_json(checks: Iterable[Check]) -> list[dict[str, Any]]:
    return [asdict(check) for check in checks]


def exit_code(checks: Iterable[Check]) -> int:
    return 1 if any(check.status == "red" for check in checks) else 0
