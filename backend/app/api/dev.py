"""Local dev inspector API (``/api/dev/*``, docs/DEV_TOOLS.md).

Every route answers 404 unless ``DEV_TOOLS_ENABLED=true``, the deployment is
not private (no ``ALLOWED_USER_IDS``), the request comes straight from the
loopback interface (no proxy headers) and it carries a verified signed-in user.
Data is always the signed-in user's own. The only writes are the owner's own
basket clear; the doctor and reconcile dry-run are read-only.
"""

from __future__ import annotations

import io
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.routing import APIRoute
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth import PrivateDeploymentForbidden, UserIdDependency
from app.core.config import Settings, get_settings
from app.database.session import get_db_session
from app.devtools.budget import ProviderBudgetExceeded, ProviderCallBudget
from app.devtools.data import DevData, DevDataError
from app.devtools.trace import read_traces

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
PROXY_HEADERS = ("x-forwarded-for", "x-forwarded-host", "forwarded", "x-real-ip", "cf-connecting-ip")
# Identity failures inside /api/dev look exactly like a missing route.
HIDDEN_STATUS = frozenset({401, 403, 503})


def dev_tools_open(request: Request, settings: Settings) -> bool:
    if not settings.dev_tools_enabled or settings.private_deployment:
        return False
    host = request.client.host if request.client else ""
    if host not in LOOPBACK_HOSTS:
        return False
    return not any(request.headers.get(header) for header in PROXY_HEADERS)


def require_dev_tools(request: Request, settings: Annotated[Settings, Depends(get_settings)]) -> None:
    if not dev_tools_open(request, settings):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")


class DevRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Awaitable[Any]]:
        handler = super().get_route_handler()

        async def hidden(request: Request) -> Any:
            try:
                return await handler(request)
            except PrivateDeploymentForbidden as exc:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found") from exc
            except HTTPException as exc:
                if exc.status_code in HIDDEN_STATUS:
                    raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found") from exc
                raise

        return hidden


router = APIRouter(route_class=DevRoute, dependencies=[Depends(require_dev_tools)])

DoctorRunner = Callable[[], Awaitable[list[Any]]]
ReconcileRunner = Callable[[io.StringIO], Awaitable[Any]]


async def get_dev_data(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> DevData:
    # The request session's engine, so tests stay on their own database.
    return DevData(async_sessionmaker(session.bind, expire_on_commit=False), settings.tracking_data_path)


def get_dev_doctor(settings: Annotated[Settings, Depends(get_settings)]) -> DoctorRunner:
    from app.devtools import doctor

    async def run() -> list[Any]:
        # Never a Swiggy/Zepto call (stored state only); OpenAI/Jev once each.
        return await doctor.run_checks(doctor.default_env(settings))

    return run


def get_dev_reconciler(settings: Annotated[Settings, Depends(get_settings)]) -> ReconcileRunner:
    async def run(out: io.StringIO) -> Any:
        from app.devtools.reconcile import reconcile_dry_run

        # Refused under a recorded cooldown; ≤5 read-only Zepto calls, ≥1 s apart.
        return await reconcile_dry_run(settings, out, budget=ProviderCallBudget())

    return run


DevDataDependency = Annotated[DevData, Depends(get_dev_data)]


class ClearBasketIn(BaseModel):
    provider: Literal["swiggy_instamart", "zepto"]


@router.get("/status")
async def dev_status(user_id: UserIdDependency, settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, Any]:
    return {"enabled": True, "trace": settings.dev_trace, "trace_keep": settings.dev_trace_keep}


@router.get("/traces")
async def dev_traces(
    user_id: UserIdDependency,
    settings: Annotated[Settings, Depends(get_settings)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    records = read_traces(settings.dev_trace_dir, limit)
    return {"trace_enabled": settings.dev_trace, "turns": list(reversed(records))}


@router.get("/state")
async def dev_state(user_id: UserIdDependency, data: DevDataDependency) -> dict[str, Any]:
    try:
        actor = await data.person(user_id)
        memories = await data.memories(actor)
        address = await data.zepto_address(actor)
    except DevDataError:
        memories, address = [], {"selected": False, "snapshot_age_min": None}
    return {
        "baskets": await data.baskets(user_id),
        "memory": memories,
        "watches": data.watches(user_id),
        "notifications": await data.notifications(user_id),
        "zepto_address": address,
    }


@router.post("/basket/clear")
async def dev_clear_basket(body: ClearBasketIn, user_id: UserIdDependency, data: DevDataDependency) -> dict[str, Any]:
    try:
        return await data.clear_basket(user_id, body.provider)
    except DevDataError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.post("/doctor")
async def dev_doctor(user_id: UserIdDependency, doctor: Annotated[DoctorRunner, Depends(get_dev_doctor)]) -> dict[str, Any]:
    from app.devtools.doctor import as_json, exit_code

    checks = await doctor()
    return {"checks": as_json(checks), "exit_code": exit_code(checks)}


@router.post("/reconcile-dry-run")
async def dev_reconcile(
    user_id: UserIdDependency, reconcile: Annotated[ReconcileRunner, Depends(get_dev_reconciler)]
) -> dict[str, Any]:
    out = io.StringIO()
    try:
        counts = await reconcile(out)
    except ProviderBudgetExceeded as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return {"output": out.getvalue().splitlines(), "counts": counts if isinstance(counts, dict) else None}
