"""Saved chats keep a checked photo thumbnail on user turns; old chats still load."""

import base64
import io

from PIL import Image

from app.conversations.schemas import MAX_CHAT_THUMBNAILS, ConversationStateUpsert
from app.photos.images import THUMBNAIL_PREFIX


def thumbnail(size: tuple[int, int] = (64, 48)) -> str:
    out = io.BytesIO()
    Image.new("RGB", size, (220, 180, 40)).save(out, format="JPEG", quality=70)
    return THUMBNAIL_PREFIX + base64.b64encode(out.getvalue()).decode("ascii")


def message(index: int, role: str = "user", **extra) -> dict:
    return {"id": f"m{index}", "role": role, "content": "📷 Photo", "created_at": "2026-11-22T10:00:00Z", **extra}


def test_valid_thumbnail_is_kept_and_old_messages_load_without_one() -> None:
    state = ConversationStateUpsert.model_validate(
        {"messages": [message(1), message(2, photo_thumbnail=thumbnail())]}
    )
    assert state.messages[0].photo_thumbnail is None
    assert state.messages[1].photo_thumbnail == thumbnail()


def test_bad_thumbnails_are_dropped_without_rejecting_the_save() -> None:
    state = ConversationStateUpsert.model_validate(
        {
            "messages": [
                message(1, photo_thumbnail="javascript:alert(1)"),
                message(2, photo_thumbnail=thumbnail((900, 600))),
                message(3, role="assistant", photo_thumbnail=thumbnail()),
                message(4, photo_preview="blob:http://localhost/abc"),
            ]
        }
    )
    assert [m.photo_thumbnail for m in state.messages] == [None, None, None, None]
    assert "photo_preview" not in state.model_dump()["messages"][3]


def test_only_the_newest_thumbnails_are_kept() -> None:
    count = MAX_CHAT_THUMBNAILS + 5
    state = ConversationStateUpsert.model_validate(
        {"messages": [message(n, photo_thumbnail=thumbnail()) for n in range(count)]}
    )
    kept = [m.id for m in state.messages if m.photo_thumbnail]
    assert kept == [f"m{n}" for n in range(5, count)]
