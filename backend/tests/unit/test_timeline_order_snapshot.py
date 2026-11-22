"""The saved chat timeline keeps the 'Ordered' link and the finished-order summary fields."""

from app.conversations.schemas import ChatTimelineSnapshot


def test_ordered_summary_link_and_finished_order_fields_round_trip() -> None:
    raw = {
        "seq": 3,
        "summaries": [
            {"provider": "zepto", "after_message_id": "m1", "seq": 1, "stage": "ordered", "item_count": 1, "item_total_paise": 5500, "order_id": "ord-live"}
        ],
        "orders": [
            {
                "provider": "zepto",
                "order_id": "ord-live",
                "seq": 2,
                "status": "delivered",
                "item_names": ["Amul Sweet Lassi"],
                "finished_at": "2026-11-22T03:11:00Z",
                "active": False,
            }
        ],
    }
    saved = ChatTimelineSnapshot.model_validate(raw).model_dump(mode="json")
    assert saved["summaries"][0]["order_id"] == "ord-live"
    assert saved["orders"][0]["item_names"] == ["Amul Sweet Lassi"]
    assert saved["orders"][0]["finished_at"].startswith("2026-11-22T03:11:00")
    # Older chats without the new fields still load.
    old = ChatTimelineSnapshot.model_validate({"orders": [{"provider": "zepto", "order_id": "o", "seq": 0}]})
    assert old.orders[0].item_names == [] and old.orders[0].finished_at is None
