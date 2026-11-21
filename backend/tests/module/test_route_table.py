"""Static guards over the mounted route table (no requests, no database)."""

from starlette.routing import compile_path

from app.main import app
from tests.routes import PUBLIC_ROUTES, concrete_path, operations, requires_user


def test_route_table_is_discovered_through_lazy_routers() -> None:
    keys = {f"{method} {path}" for method, path, _ in operations(app)}
    assert len(keys) >= 60
    assert "POST /api/zepto/checkout/cod" in keys
    assert "DELETE /api/memory/purchase-history" in keys


def test_every_route_requires_a_signed_in_user_unless_explicitly_public() -> None:
    missing = sorted(
        f"{method} {path}"
        for method, path, route in operations(app)
        if f"{method} {path}" not in PUBLIC_ROUTES and not requires_user(route)
    )
    assert not missing, (
        "These routes run without a verified Clerk user. Add `UserIdDependency` "
        f"(or a dependency that uses it) or justify them in tests/routes.py: {missing}"
    )


def test_public_allowlist_has_no_stale_entries() -> None:
    mounted = {f"{method} {path}" for method, path, _ in operations(app)}
    assert set(PUBLIC_ROUTES) <= mounted, sorted(set(PUBLIC_ROUTES) - mounted)


def test_no_route_is_shadowed_by_an_earlier_parameterised_route() -> None:
    """Starlette stops at the first match, so `/{id}` mounted first hides `/literal`."""

    table = operations(app)
    shadowed = []
    for index, (method, path, _) in enumerate(table):
        target = concrete_path(path)
        for earlier_method, earlier_path, _ in table[:index]:
            if earlier_method != method or earlier_path == path:
                continue
            regex, _, _ = compile_path(earlier_path)
            if regex.match(target):
                shadowed.append(f"{method} {path} is captured by {earlier_path}")
                break
    assert not shadowed, shadowed
