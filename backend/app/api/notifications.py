"""Browser notification settings and subscriptions for the signed-in user only."""

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel

from app.auth import UserIdDependency
from app.dependencies import PushNotifierDependency
from app.notifications import messages
from app.notifications.models import (
    KIND_LABELS,
    PUSH_KINDS,
    KindSetting,
    NotificationSettings,
    NotificationSettingsUpdate,
    SubscriptionIn,
    UnsubscribeIn,
)
from app.notifications.service import PushNotifier
from app.notifications.store import InvalidSubscription, Preferences, PushStore

router = APIRouter()

SETUP_HINT = "Browser notifications are not set up on this server. Run `make vapid-keys` and add the keys to .env."


class PublicKeyOut(BaseModel):
    available: bool
    public_key: str | None = None


class SubscriptionOut(BaseModel):
    id: str


class TestOut(BaseModel):
    delivered: int
    removed: int


def _store(notifier: PushNotifier) -> PushStore:
    if not notifier.enabled or notifier.store is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, SETUP_HINT)
    return notifier.store


async def _settings_out(notifier: PushNotifier, store: PushStore, user_id: str, prefs: Preferences) -> NotificationSettings:
    return NotificationSettings(
        available=True,
        public_key=notifier.public_key,
        kinds=[KindSetting(id=kind, label=KIND_LABELS[kind], enabled=prefs.allows(kind)) for kind in PUSH_KINDS],
        devices=await store.device_count(user_id),
        order_prompt_answered=prefs.order_prompt_answered,
    )


@router.get("/vapid-public-key", response_model=PublicKeyOut)
async def vapid_public_key(notifier: PushNotifierDependency, _: UserIdDependency) -> PublicKeyOut:
    if not notifier.enabled:
        return PublicKeyOut(available=False)
    return PublicKeyOut(available=True, public_key=notifier.public_key)


@router.get("/settings", response_model=NotificationSettings)
async def get_settings(notifier: PushNotifierDependency, user_id: UserIdDependency) -> NotificationSettings:
    if not notifier.enabled:
        return NotificationSettings(available=False, kinds=[])
    store = _store(notifier)
    return await _settings_out(notifier, store, user_id, await store.preferences(user_id))


@router.put("/settings", response_model=NotificationSettings)
async def put_settings(
    body: NotificationSettingsUpdate, notifier: PushNotifierDependency, user_id: UserIdDependency
) -> NotificationSettings:
    store = _store(notifier)
    prefs = await store.update_preferences(
        user_id, kinds=body.kinds, order_prompt_answered=body.order_prompt_answered, at=notifier.clock()
    )
    return await _settings_out(notifier, store, user_id, prefs)


@router.post("/subscriptions", response_model=SubscriptionOut, status_code=status.HTTP_201_CREATED)
async def subscribe(
    body: SubscriptionIn, notifier: PushNotifierDependency, user_id: UserIdDependency
) -> SubscriptionOut:
    store = _store(notifier)
    try:
        return SubscriptionOut(id=await store.save_subscription(user_id, body))
    except InvalidSubscription as exc:
        raise HTTPException(422, str(exc)) from exc


@router.delete("/subscriptions", status_code=status.HTTP_204_NO_CONTENT)
async def unsubscribe(body: UnsubscribeIn, notifier: PushNotifierDependency, user_id: UserIdDependency) -> Response:
    # Idempotent; only this user's own subscription can be removed.
    await _store(notifier).delete_subscription(user_id, body.endpoint)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/test", response_model=TestOut)
async def send_test(notifier: PushNotifierDependency, user_id: UserIdDependency) -> TestOut:
    """Send a test notification to the caller's own browsers only."""

    _store(notifier)
    result = await notifier.send(user_id, messages.test_message())
    if result.outcome == "no_subscription":
        raise HTTPException(status.HTTP_409_CONFLICT, "Turn on notifications in this browser first.")
    if result.outcome == "rate_limited":
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many test notifications. Try again in a minute.")
    if result.outcome == "failed":
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "The browser's push service didn't accept the test. Try again, or turn notifications off and on.",
        )
    return TestOut(delivered=result.delivered, removed=result.removed)
