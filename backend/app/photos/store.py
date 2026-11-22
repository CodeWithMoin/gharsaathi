"""Short-lived, user-scoped photo attachments, kept in memory only.

The owner chose "discard after reading": an image is deleted as soon as its
items are extracted, and anything unread expires after ``TTL_SECONDS``.
Image bytes are never written to disk or the database. A backend restart
drops every pending photo; the user attaches it again.
"""

from __future__ import annotations

import hmac
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass


TTL_SECONDS = 15 * 60
MAX_PER_USER = 3
MAX_TOTAL = 200


@dataclass(frozen=True)
class StoredPhoto:
    attachment_id: str
    owner_id: str
    jpeg: bytes
    created_at: float
    expires_at: float


class PhotoAttachmentStore:
    def __init__(
        self,
        ttl_seconds: float = TTL_SECONDS,
        *,
        max_per_user: int = MAX_PER_USER,
        max_total: int = MAX_TOTAL,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_per_user = max_per_user
        self.max_total = max_total
        self._clock = clock
        self._photos: OrderedDict[str, StoredPhoto] = OrderedDict()
        self._lock = threading.Lock()

    def put(self, owner_id: str, jpeg: bytes) -> StoredPhoto:
        if not owner_id:
            raise ValueError("A verified owner is required for a photo attachment.")
        with self._lock:
            self._prune()
            mine = [key for key, photo in self._photos.items() if _same(photo.owner_id, owner_id)]
            # The oldest pending photo of this user makes room for the new one.
            for key in mine[: max(0, len(mine) - self.max_per_user + 1)]:
                self._photos.pop(key, None)
            while len(self._photos) >= self.max_total:
                self._photos.popitem(last=False)
            now = self._clock()
            photo = StoredPhoto(
                attachment_id=f"ph_{secrets.token_urlsafe(18)}",
                owner_id=owner_id,
                jpeg=jpeg,
                created_at=now,
                expires_at=now + self.ttl_seconds,
            )
            self._photos[photo.attachment_id] = photo
            return photo

    def get(self, owner_id: str, attachment_id: str) -> StoredPhoto | None:
        """The photo only for its owner and only before it expires."""

        with self._lock:
            self._prune()
            photo = self._photos.get(attachment_id)
            if photo is None or not _same(photo.owner_id, owner_id):
                return None
            return photo

    def newest(self, owner_id: str) -> StoredPhoto | None:
        with self._lock:
            self._prune()
            for photo in reversed(self._photos.values()):
                if _same(photo.owner_id, owner_id):
                    return photo
            return None

    def discard(self, owner_id: str, attachment_id: str) -> bool:
        with self._lock:
            photo = self._photos.get(attachment_id)
            if photo is None or not _same(photo.owner_id, owner_id):
                return False
            del self._photos[attachment_id]
            return True

    def discard_up_to(self, owner_id: str, photo: StoredPhoto) -> None:
        """Delete ``photo`` and every older pending photo of the same owner."""

        with self._lock:
            if photo.attachment_id not in self._photos:
                return
            # Insertion order is upload order.
            for key, other in list(self._photos.items()):
                if _same(other.owner_id, owner_id):
                    del self._photos[key]
                if key == photo.attachment_id:
                    break

    def __len__(self) -> int:
        with self._lock:
            self._prune()
            return len(self._photos)

    def _prune(self) -> None:
        now = self._clock()
        for key in [key for key, photo in self._photos.items() if photo.expires_at <= now]:
            self._photos.pop(key, None)


def _same(left: str, right: str) -> bool:
    return bool(left) and bool(right) and hmac.compare_digest(left.encode(), right.encode())
