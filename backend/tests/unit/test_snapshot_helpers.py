"""Pure helpers behind address snapshots and conversation summaries."""

from datetime import UTC, datetime
from uuid import uuid4

from app.api.conversations import _summary
from app.commerce.provider_state import cached_address_list, is_demo_address_list
from app.commerce.swiggy.models import Address, AddressList, AddressResolution, Pagination
from app.database.models import Conversation, ProviderAddressState

NOW = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)


def _addresses(*ids: str, source: str | None = "provider", default: str | None = None) -> AddressList:
    return AddressList(
        addresses=[Address(id=item, address_line=f"{item} road") for item in ids],
        pagination=Pagination(),
        resolution=AddressResolution(default_address_id=default, resolution_source=source),
    )


def test_demo_addresses_are_recognised_by_source_or_id_prefix() -> None:
    assert is_demo_address_list(_addresses("home", source="demo_fixture"))
    assert is_demo_address_list(_addresses("home", "demo-office"))
    assert not is_demo_address_list(_addresses("home", "office"))
    assert not is_demo_address_list(_addresses())


def test_cached_list_reports_the_saved_selection_over_the_provider_default() -> None:
    state = ProviderAddressState(
        subject_id=uuid4(),
        provider="zepto",
        snapshot=_addresses("home", "office", default="home").model_dump(mode="json"),
        selected_address_id="office",
        refreshed_at=NOW,
    )
    cached = cached_address_list(state)
    assert cached.cached is True and cached.needs_refresh is False
    assert cached.refreshed_at == NOW
    assert cached.resolution.default_address_id == "office"
    assert cached.resolution.resolution_source == "saved_selection"

    state.selected_address_id = None
    unselected = cached_address_list(state)
    assert unselected.resolution.default_address_id == "home"
    assert unselected.resolution.resolution_source == "provider"


def _conversation(state: dict, archived: bool = False) -> Conversation:
    return Conversation(
        id=uuid4(),
        subject_id=uuid4(),
        state=state,
        updated_at=NOW,
        archived_at=NOW if archived else None,
    )


def test_summary_titles_from_first_user_message_and_previews_the_latest() -> None:
    summary = _summary(
        _conversation(
            {
                "submitted_query": "atta",
                "messages": [
                    {"role": "assistant", "content": "Namaste!"},
                    {"role": "user", "content": "  "},
                    {"role": "user", "content": "Do kilo atta chahiye"},
                    {"role": "assistant", "content": "Yeh options mile."},
                ],
            }
        )
    )
    assert summary.title == "Do kilo atta chahiye"
    assert summary.preview == "Yeh options mile."
    assert summary.is_active is True


def test_summary_falls_back_to_query_then_default_and_truncates() -> None:
    assert _summary(_conversation({"submitted_query": "doodh"}, archived=True)).title == "doodh"
    assert _summary(_conversation({"messages": []})).title == "Shopping chat"
    long = "a" * 300
    summary = _summary(_conversation({"messages": [{"role": "user", "content": long}]}))
    assert len(summary.title) == 80 and len(summary.preview) == 160
    assert _summary(_conversation({}, archived=True)).is_active is False
