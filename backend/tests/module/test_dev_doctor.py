"""``make doctor`` checks against fakes: each green/amber/red path, no secrets, no store calls."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from app.core.config import Settings
from app.devtools import doctor
from app.devtools.doctor import Check, DbSnapshot, DoctorEnv, HttpResult
from app.notifications.vapid import generate

NOW = datetime(2026, 11, 22, 10, tzinfo=UTC)
KEY = Fernet.generate_key().decode()
SECRETS = {
    "openai": "sk-test-OPENAI-SECRET-123",
    "jev": "jev-SECRET-456",
    "clerk": "sk_test_CLERK_SECRET_789",
    "access": "zepto-ACCESS-TOKEN-abc",
    "refresh": "zepto-REFRESH-TOKEN-def",
}


class Http:
    def __init__(self, answers: dict[str, HttpResult | Exception]) -> None:
        self.answers = answers
        self.urls: list[str] = []

    async def __call__(self, url: str, headers: dict[str, str], timeout: float) -> HttpResult:
        self.urls.append(url)
        answer = self.answers.get(url, ConnectionError("refused"))
        if isinstance(answer, Exception):
            raise answer
        return answer


def healthy_http() -> dict[str, HttpResult | Exception]:
    return {
        doctor.BACKEND_URL: HttpResult(200, '{"status":"ok"}', 4.0),
        doctor.FRONTEND_URL: HttpResult(200, "<html>", 30.0),
        doctor.BRIDGE_URL: HttpResult(200, '{"service":"oauth-callback-bridge"}', 2.0),
        "https://api.openai.com/v1/models": HttpResult(200, "{}", 120.0),
        "https://api.typesafe.ai/v1": HttpResult(404, "", 80.0),
    }


def write_connection(path: Path, **fields: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"client_id": "client", **{k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in fields.items()}}
    path.write_bytes(Fernet(KEY.encode()).encrypt(json.dumps(payload).encode()))


def make_env(tmp_path: Path, *, http: Http | None = None, snapshot: DbSnapshot | Exception | None = None, offline: bool = False, launch: dict | None = None, **settings: object) -> tuple[DoctorEnv, Http]:
    public, private = generate()
    base: dict[str, object] = {
        "_env_file": None,
        "openai_api_key": SecretStr(SECRETS["openai"]),
        "jev_api_key": SecretStr(SECRETS["jev"]),
        "jev_reranking_enabled": True,
        "clerk_secret_key": SecretStr(SECRETS["clerk"]),
        "app_encryption_key": KEY,
        "web_push_vapid_public_key": public,
        "web_push_vapid_private_key": SecretStr(private),
        "web_push_subject": "mailto:owner@example.test",
        "zepto_token_store_path": tmp_path / "backend/.data/zepto.enc",
        "swiggy_token_store_path": tmp_path / "backend/.data/swiggy.enc",
        "support_data_path": tmp_path / "backend/.data/support",
        "tracking_data_path": tmp_path / "backend/.data/tracking",
        "local_encryption_key_path": tmp_path / "backend/.data/key",
    }
    base.update(settings)
    http = http or Http(healthy_http())
    (tmp_path / ".run").mkdir(exist_ok=True)
    (tmp_path / ".run" / "launch.json").write_text(json.dumps(launch or {"branch": "main", "commit": "abc1234", "commerce_mode": "live"}))

    async def db_read(url: str) -> DbSnapshot:
        if isinstance(snapshot, Exception):
            raise snapshot
        return snapshot or DbSnapshot(["head1"], [(True, NOW - timedelta(minutes=5))])

    def run(argv: list[str]) -> str | None:
        if argv[:2] == ["lsof", "-nP"]:
            return "4242"
        if argv[:2] == ["lsof", "-a"]:
            return f"p4242\nfcwd\nn{tmp_path}/backend"
        if "--show-toplevel" in argv:
            return str(tmp_path)
        if "--short" in argv:
            return "abc1234"
        if "--abbrev-ref" in argv:
            return "main"
        return None

    env = DoctorEnv(
        settings=Settings(**base),
        http_get=http,
        db_read=db_read,
        alembic_heads=lambda root: {"head1"},
        run=run,
        repo_root=tmp_path,
        database_url="postgresql+asyncpg://someone:DBPASSWORD@127.0.0.1:5432/equal_ai",
        frontend_env={"NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY": "pk_test_PUBLISHABLE", "CLERK_SECRET_KEY": SECRETS["clerk"]},
        now=NOW,
        offline=offline,
    )
    write_connection(env.settings.zepto_token_store_path, access_token=SECRETS["access"], refresh_token=SECRETS["refresh"], expires_at=NOW + timedelta(days=3))
    write_connection(env.settings.swiggy_token_store_path, access_token=SECRETS["access"])
    return env, http


def by_name(checks: list[Check]) -> dict[str, Check]:
    return {check.name: check for check in checks}


async def test_all_green_and_zero_store_calls(tmp_path: Path) -> None:
    env, http = make_env(tmp_path)
    checks = await doctor.run_checks(env)
    assert [c.name for c in checks if c.status != "green"] == []
    assert doctor.exit_code(checks) == 0
    assert not any("swiggy" in url or "zepto" in url for url in http.urls)
    # One call each to OpenAI and Jev, never more.
    assert sum("openai" in url for url in http.urls) == 1
    assert sum("typesafe" in url for url in http.urls) == 1


async def test_offline_makes_no_external_call(tmp_path: Path) -> None:
    env, http = make_env(tmp_path, offline=True)
    checks = by_name(await doctor.run_checks(env))
    assert all(url.startswith("http://127.0.0.1") for url in http.urls)
    assert checks["OpenAI"].status == "amber" and "--offline" in checks["OpenAI"].detail
    assert checks["Jev"].status == "amber"


async def test_output_never_contains_env_values(tmp_path: Path) -> None:
    env, _ = make_env(tmp_path)
    checks = await doctor.run_checks(env)
    text = doctor.render(checks) + json.dumps(doctor.as_json(checks))
    private = env.settings.web_push_vapid_private_key.get_secret_value()
    for value in [*SECRETS.values(), private, env.settings.web_push_vapid_public_key, KEY, "DBPASSWORD", "pk_test_PUBLISHABLE"]:
        assert value not in text


async def test_red_lines_carry_fix_commands_and_exit_one(tmp_path: Path) -> None:
    http = Http({**healthy_http(), doctor.BACKEND_URL: ConnectionError(), "https://api.openai.com/v1/models": HttpResult(401, "", 50)})
    env, _ = make_env(tmp_path, http=http, snapshot=OSError("down"), openai_api_key=SecretStr("sk-x"))
    checks = by_name(await doctor.run_checks(env))
    assert checks["Backend :8000"].status == "red" and checks["Backend :8000"].fix
    assert checks["Database"].status == "red" and "postgresql" in checks["Database"].fix
    assert checks["Database"].detail.startswith("127.0.0.1:5432/equal_ai")
    assert checks["OpenAI"].status == "red" and ".env" in checks["OpenAI"].fix
    assert checks["Zepto address"].status == "amber"
    assert doctor.exit_code(list(checks.values())) == 1
    rendered = doctor.render(list(checks.values()))
    assert "fix: make up" in rendered


async def test_database_behind_head_is_red_with_migrate_command(tmp_path: Path) -> None:
    env, _ = make_env(tmp_path, snapshot=DbSnapshot(["old"], [(True, NOW)]))
    check = by_name(await doctor.run_checks(env))["Database"]
    assert check.status == "red" and "alembic upgrade head" in check.fix


async def test_zepto_auth_paths(tmp_path: Path) -> None:
    env, _ = make_env(tmp_path)
    path = env.settings.zepto_token_store_path
    write_connection(path, access_token="a", refresh_token="r", expires_at=NOW - timedelta(minutes=5))
    assert (await doctor.check_zepto_auth(env)).status == "amber"
    write_connection(path, access_token="a", expires_at=NOW - timedelta(minutes=5))
    assert (await doctor.check_zepto_auth(env)).status == "red"
    write_connection(path)
    assert (await doctor.check_zepto_auth(env)).detail == "signed out (no session)"
    write_connection(path, access_token="a", rate_limited_until=NOW + timedelta(seconds=90))
    cooling = await doctor.check_zepto_auth(env)
    assert cooling.status == "amber" and "cooldown" in cooling.detail
    path.write_bytes(b"garbage")
    assert "unreadable" in (await doctor.check_zepto_auth(env)).detail
    path.unlink()
    assert (await doctor.check_zepto_auth(env)).status == "red"


async def test_demo_mode_skips_store_checks(tmp_path: Path) -> None:
    env, _ = make_env(tmp_path, launch={"branch": "main", "commerce_mode": "demo"})
    checks = by_name(await doctor.run_checks(env))
    assert all("demo mode" in checks[name].detail for name in ("Zepto login", "Zepto address", "Swiggy"))


async def test_swiggy_cooldown_and_stale_address_are_amber(tmp_path: Path) -> None:
    env, _ = make_env(tmp_path, snapshot=DbSnapshot(["head1"], [(True, NOW - timedelta(hours=3))]))
    write_connection(env.settings.swiggy_token_store_path, access_token="a", rate_limited_until=NOW + timedelta(seconds=42))
    checks = by_name(await doctor.run_checks(env))
    assert checks["Swiggy"].status == "amber" and "42s" in checks["Swiggy"].detail
    assert checks["Zepto address"].status == "amber"


async def test_no_selected_address_is_red(tmp_path: Path) -> None:
    env, _ = make_env(tmp_path, snapshot=DbSnapshot(["head1"], [(False, NOW)]))
    assert by_name(await doctor.run_checks(env))["Zepto address"].status == "red"


@pytest.mark.parametrize(
    ("update", "status"),
    [
        ({"web_push_vapid_public_key": "", "web_push_vapid_private_key": SecretStr(""), "web_push_subject": ""}, "amber"),
        ({"web_push_subject": ""}, "red"),
        ({"web_push_vapid_public_key": "BWrongKey"}, "red"),
    ],
)
async def test_vapid_paths(tmp_path: Path, update: dict, status: str) -> None:
    env, _ = make_env(tmp_path, **update)
    assert (await doctor.check_vapid(env)).status == status


async def test_clerk_jev_rider_paths(tmp_path: Path) -> None:
    env, _ = make_env(tmp_path, clerk_secret_key=SecretStr(""), jev_reranking_enabled=False, rider_assist_enabled=True)
    env.frontend_env = {}
    checks = by_name(await doctor.run_checks(env))
    assert checks["Clerk"].status == "red"
    assert checks["Jev"].status == "amber"
    assert checks["Rider assist"].status == "red" and "VOBIZ_AUTH_ID" in checks["Rider assist"].detail


async def test_jev_enabled_but_unreachable_is_red(tmp_path: Path) -> None:
    http = Http({**healthy_http(), "https://api.typesafe.ai/v1": TimeoutError()})
    env, _ = make_env(tmp_path, http=http)
    assert (await doctor.check_jev(env)).status == "red"


async def test_bridge_down_is_amber_with_fix(tmp_path: Path) -> None:
    http = Http({**healthy_http(), doctor.BRIDGE_URL: ConnectionError()})
    env, _ = make_env(tmp_path, http=http)
    check = await doctor.check_bridge(env)
    assert check.status == "amber" and check.fix == "make zepto-bridge"


async def test_running_from_a_feature_branch_warns(tmp_path: Path) -> None:
    env, _ = make_env(tmp_path, launch={"branch": "codex/x", "commit": "abc1234", "commerce_mode": "live"})
    check = by_name(await doctor.run_checks(env))["Running checkout"]
    assert check.status == "amber" and "not main" in check.detail


async def test_unwritable_data_dir_is_red(tmp_path: Path) -> None:
    env, _ = make_env(tmp_path)
    support = env.settings.support_data_path
    support.mkdir(parents=True)
    support.chmod(0o500)
    try:
        check = await doctor.check_data_dirs(env)
    finally:
        support.chmod(0o700)
    assert check.status == "red" and "chmod" in check.fix


def test_render_colors_only_when_asked() -> None:
    checks = [Check("A", "green", "ok"), Check("B", "red", "bad", "do x")]
    plain = doctor.render(checks)
    assert "\033[" not in plain and "fix: do x" in plain and "1 green · 0 amber · 1 red" in plain
    assert "\033[31m" in doctor.render(checks, color=True)
