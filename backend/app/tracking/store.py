from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

from app.tracking.errors import TrackingNotFound
from app.tracking.models import OrderWatch, TrackingProvider


class OrderTrackingStore:
    """Small encrypted-filesystem-friendly local persistence used by the app today.

    Files use opaque hashes rather than provider order IDs, and permissions are
    private.  Replacing this with the project database is intentionally a
    repository concern, not a polling-loop concern.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.watches_path = root / "watches"

    @staticmethod
    def _key(user_id: str, provider: TrackingProvider, order_id: str) -> str:
        material = f"{user_id}\x00{provider.value}\x00{order_id}".encode()
        return hashlib.sha256(material).hexdigest()

    def _path(
        self, user_id: str, provider: TrackingProvider, order_id: str
    ) -> Path:
        return self.watches_path / f"{self._key(user_id, provider, order_id)}.json"

    def save(self, watch: OrderWatch) -> OrderWatch:
        self.watches_path.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self._path(watch.user_id, watch.provider, watch.order_id)
        temporary = path.with_suffix(f".{uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(watch.model_dump(mode="json"), indent=2), encoding="utf-8"
        )
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        return watch

    def get(
        self, user_id: str, provider: TrackingProvider, order_id: str
    ) -> OrderWatch:
        path = self._path(user_id, provider, order_id)
        try:
            return OrderWatch.model_validate_json(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise TrackingNotFound("This order is not being tracked.") from exc

    def get_by_id(self, user_id: str, monitor_id: str) -> OrderWatch:
        """Resolve an opaque monitor ID without ever placing an order ID in a URL."""

        try:
            UUID(monitor_id)
        except ValueError as exc:
            raise TrackingNotFound("This tracking monitor was not found.") from exc
        for watch in self.list(user_id):
            if watch.id == monitor_id:
                return watch
        raise TrackingNotFound("This tracking monitor was not found.")

    def list(self, user_id: str, *, active_only: bool = False) -> list[OrderWatch]:
        if not self.watches_path.exists():
            return []
        watches: list[OrderWatch] = []
        for path in self.watches_path.glob("*.json"):
            try:
                watch = OrderWatch.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                # A corrupt local cache must not break all other user watches.
                continue
            if watch.user_id == user_id and (watch.active or not active_only):
                watches.append(watch)
        return sorted(watches, key=lambda item: item.updated_at, reverse=True)

    def list_users(self) -> list[str]:
        if not self.watches_path.exists():
            return []
        users: set[str] = set()
        for path in self.watches_path.glob("*.json"):
            try:
                watch = OrderWatch.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            users.add(watch.user_id)
        return sorted(users)
