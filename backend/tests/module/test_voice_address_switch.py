"""Voice address switching, address-form availability and the screen acknowledgement."""

import asyncio
import json
from typing import Any

import pytest

from app.commerce.demo import DemoAddressBook
from app.commerce.swiggy.models import Address, AddressList, AddressResolution, Pagination, ProductSearchResult
from app.commerce.zepto.models import ConnectionStatus
from app.shopping.models import BasketStatus
from app.voice.address_book_tools import (
    ADDRESS_BOOK_TOOL_DEFINITIONS,
    LIST_DELIVERY_ADDRESSES,
    SELECT_DELIVERY_ADDRESS,
)
from app.voice.address_prefill import RecentUserWords
from app.voice.address_tools import OPEN_ADDRESS_FORM, TEXT_LIMITS
from app.voice.sideband import SessionChannel, SidebandRunner
from app.voice.toolbox import VOICE_TOOL_DEFINITIONS, VoiceToolbox
from app.voice.ui_ack import UiAck
from tests.shopping_fakes import HOUSEHOLD, World, line
from tests.test_voice_tools import FakeConnection, FakeSearcher, nested, product


def _list(*entries: tuple[str, str], selected: str | None = None) -> AddressList:
    return AddressList(
        addresses=[
            Address(id=address_id, tag=tag, category="HOME", address_line=f"{tag} street, Bengaluru")
            for address_id, tag in entries
        ],
        pagination=Pagination(page=1, page_size=10, total=len(entries), total_pages=1),
        resolution=AddressResolution(default_address_id=selected, resolution_source="saved_selection"),
    )


class FakeBook:
    """Per (household, store) saved lists; selections only for ids in that list."""

    def __init__(self) -> None:
        self.lists: dict[tuple[str, str], AddressList] = {
            (HOUSEHOLD, "swiggy_instamart"): _list(("sw-home", "Home"), ("sw-office", "Office"), selected="sw-home"),
            (HOUSEHOLD, "zepto"): _list(("zp-home", "Home"), ("zp-office", "Office"), selected="zp-home"),
            ("someone_else", "zepto"): _list(("other-flat", "Flat")),
        }
        self.selected: list[tuple[str, str, str]] = []

    async def saved(self, household_id: str, provider: str) -> AddressList | None:
        return self.lists.get((household_id, provider))

    async def select(self, household_id: str, provider: str, address_id: str) -> AddressList | None:
        addresses = self.lists.get((household_id, provider))
        if addresses is None or address_id not in {item.id for item in addresses.addresses}:
            return None
        self.selected.append((household_id, provider, address_id))
        addresses.resolution.default_address_id = address_id
        return addresses


def box(world: World, book: Any = None, *, zepto: bool = True, swiggy_searcher: Any = None) -> VoiceToolbox:
    return VoiceToolbox(
        swiggy_searcher or FakeSearcher(ProductSearchResult(products=[product("Sunlite Oil", 154, 180)])),
        world.baskets,
        world.approvals,
        "sw-home",
        FakeSearcher(ProductSearchResult(products=[])) if zepto else None,
        "zp-home" if zepto else None,
        HOUSEHOLD,
        address_book=book if book is not None else FakeBook(),
    )


async def run(toolbox: VoiceToolbox, name: str, **arguments: Any) -> Any:
    return await toolbox.execute(name, json.dumps(arguments))


def test_tools_are_strict_and_registered() -> None:
    for definition in ADDRESS_BOOK_TOOL_DEFINITIONS:
        assert definition in VOICE_TOOL_DEFINITIONS
        assert definition["strict"] is True
        parameters = definition["parameters"]
        assert parameters["additionalProperties"] is False
        assert set(parameters["required"]) == set(parameters["properties"])


async def test_list_shows_only_this_households_saved_addresses_with_selection() -> None:
    outcome = await run(box(World()), LIST_DELIVERY_ADDRESSES, store=None)
    stores = {store["store_id"]: store for store in outcome.model_output["stores"]}
    assert set(stores) == {"swiggy_instamart", "zepto"}
    zepto = stores["zepto"]["addresses"]
    assert [(item["label"], item["selected"]) for item in zepto] == [("Home", True), ("Office", False)]
    assert "other-flat" not in json.dumps(outcome.model_output)
    assert outcome.ui_event is None


async def test_list_store_not_connected_and_empty_snapshot() -> None:
    outcome = await run(box(World(), zepto=False), LIST_DELIVERY_ADDRESSES, store="zepto")
    assert outcome.model_output["status"] == "not_connected"

    book = FakeBook()
    del book.lists[(HOUSEHOLD, "zepto")]
    outcome = await run(box(World(), book), LIST_DELIVERY_ADDRESSES, store="zepto")
    store = outcome.model_output["stores"][0]
    assert store["status"] == "no_saved_addresses"
    assert "Refresh" in store["message"]


@pytest.mark.parametrize("address_id", ["zp-nowhere", "other-flat", "sw-office"])
async def test_select_rejects_ids_outside_this_stores_saved_list(address_id: str) -> None:
    book = FakeBook()
    toolbox = box(World(), book)
    outcome = await run(toolbox, SELECT_DELIVERY_ADDRESS, store="zepto", address_id=address_id)
    assert outcome.model_output["status"] == "unknown_address"
    assert outcome.ui_event is None
    assert book.selected == []
    assert toolbox.store_address("zepto") == "zp-home"


async def test_select_store_not_connected() -> None:
    outcome = await run(box(World(), zepto=False), SELECT_DELIVERY_ADDRESS, store="zepto", address_id="zp-office")
    assert outcome.model_output["status"] == "not_connected"


async def test_select_switches_session_address_and_next_search_uses_it() -> None:
    world = World()
    swiggy = FakeSearcher(ProductSearchResult(products=[product("Sunlite Oil", 154, 180)]))
    book = FakeBook()
    toolbox = box(world, book, swiggy_searcher=swiggy)
    first = await run(toolbox, "search_products", query="sunflower oil")
    old_option = first.model_output["results"][0]["option_id"]

    outcome = await run(toolbox, SELECT_DELIVERY_ADDRESS, store="swiggy_instamart", address_id="sw-office")

    assert outcome.model_output["status"] == "selected"
    assert outcome.model_output["delivery_address"]["label"] == "Office"
    assert outcome.ui_event == {
        "type": "address.selected", "provider": "swiggy_instamart", "address_id": "sw-office",
        "label": "Office", "bill_cancelled": False,
    }
    assert book.selected == [(HOUSEHOLD, "swiggy_instamart", "sw-office")]
    # Options priced for the old address can no longer be added.
    stale = await run(toolbox, "add_to_basket", option_id=old_option, quantity=1)
    assert stale.model_output["status"] == "error"
    second = await run(toolbox, "search_products", query="sunflower oil")
    assert swiggy.calls[-1][1] == "sw-office"
    fresh = second.model_output["results"][0]["option_id"]
    assert fresh != old_option
    assert (await run(toolbox, "add_to_basket", option_id=fresh, quantity=1)).model_output["status"] == "added"
    assert (await world.baskets.active(HOUSEHOLD, "swiggy_instamart")).address_id == "sw-office"


async def test_select_cancels_the_open_bill_for_that_store() -> None:
    world = World()
    await world.ready_basket()  # swiggy basket at sw-home... the fixture's "addr_home"
    await world.baskets.set_address(HOUSEHOLD, "swiggy_instamart", "sw-home")
    request = await world.approvals.request((await world.baskets.active(HOUSEHOLD, "swiggy_instamart")).id)
    assert (await world.baskets.active(HOUSEHOLD, "swiggy_instamart")).status == BasketStatus.AWAITING_APPROVAL

    outcome = await run(box(world), SELECT_DELIVERY_ADDRESS, store="swiggy_instamart", address_id="sw-office")

    assert outcome.model_output["bill_cancelled"] is True
    assert outcome.ui_event["bill_cancelled"] is True
    basket = await world.baskets.active(HOUSEHOLD, "swiggy_instamart")
    assert basket.status == BasketStatus.DRAFT and basket.address_id == "sw-office"
    approvals = await world.approvals.approvals.for_basket(basket.id)
    assert all(item.status.value != "pending" for item in approvals if item.id == request.approval.id)


async def test_demo_address_book_lists_fixture_and_keeps_no_state() -> None:
    world = World()
    toolbox = box(world, DemoAddressBook())
    listed = await run(toolbox, LIST_DELIVERY_ADDRESSES, store="zepto")
    assert [item["address_id"] for item in listed.model_output["stores"][0]["addresses"]] == ["demo-home"]
    assert (await run(toolbox, SELECT_DELIVERY_ADDRESS, store="zepto", address_id="demo-home")).model_output["status"] == "selected"
    assert (await run(toolbox, SELECT_DELIVERY_ADDRESS, store="zepto", address_id="real-1")).model_output["status"] == "unknown_address"


# --- address form availability and acknowledgement ---------------------------------

NULL_FIELDS: dict[str, Any] = {"store": "unspecified", "address_type": None, **dict.fromkeys(TEXT_LIMITS)}


class Zepto:
    def __init__(self, status: ConnectionStatus) -> None:
        self.status = status

    async def connection_status(self) -> ConnectionStatus:
        return self.status


def form_box(status: ConnectionStatus | None, said: str = "") -> VoiceToolbox:
    world = World()
    words = RecentUserWords()
    if said:
        words.add(said)
    return VoiceToolbox(
        FakeSearcher(ProductSearchResult(products=[])), world.baskets, world.approvals, "sw-home",
        household_id=HOUSEHOLD, zepto_connection=Zepto(status) if status else None, user_words=words,
    )


@pytest.mark.parametrize(
    ("status", "expected", "reason"),
    [
        (ConnectionStatus(connected=True, mode="demo"), "demo_mode", "demo_mode"),
        (ConnectionStatus(connected=False, mode="live"), "not_connected", "zepto_not_connected"),
        (None, "not_connected", "zepto_not_connected"),
        (ConnectionStatus(connected=True, mode="live"), "form_requested", None),
    ],
)
async def test_backend_decides_form_availability(status: ConnectionStatus | None, expected: str, reason: str | None) -> None:
    outcome = await form_box(status).execute(OPEN_ADDRESS_FORM, json.dumps(NULL_FIELDS))
    assert outcome.model_output["status"] == expected
    if reason:
        assert outcome.ui_event == {"type": "address_form.unavailable", "reason": reason}
        assert outcome.model_output["status"] != "form_opened"
    else:
        assert outcome.ui_event["type"] == "address_form.open" and outcome.ui_event["ack_id"]


async def test_prefill_keeps_only_what_the_user_said_and_accumulates() -> None:
    toolbox = form_box(ConnectionStatus(connected=True, mode="live"), "naya address add karo rmz the loft")
    first = await toolbox.execute(
        OPEN_ADDRESS_FORM,
        json.dumps({
            **NULL_FIELDS, "building_name": "RMZ The Loft", "flat_details": "Flat 4B",
            "area": "Indiranagar, Bengaluru, Karnataka", "landmark": "Near metro station",
        }),
    )
    assert first.ui_event["prefill"] == {"building_name": "RMZ The Loft"}
    assert first.model_output["not_prefilled"] == ["area", "flat_details", "landmark"]

    toolbox.user_words.add("lagado floor 3, Gachibowli. Number mera bhar dena")
    second = await toolbox.execute(OPEN_ADDRESS_FORM, json.dumps({**NULL_FIELDS, "floor": "3", "area": "Gachibowli"}))
    # The earlier building survives a follow-up call that omits it.
    assert second.ui_event["prefill"] == {"building_name": "RMZ The Loft", "floor": "3", "short_address": "Gachibowli"}
    assert "cannot know" in second.model_output["contact_number"]


class AckConnection(FakeConnection):
    """Stays open until the tool result was sent (the sideband may wait for an ack)."""

    async def _iterate(self):
        for event in self.events:
            yield json.dumps(event)
            await asyncio.sleep(0)
        for _ in range(200):
            if any(item.get("type") == "response.create" for item in self.sent):
                break
            await asyncio.sleep(0.005)
        yield json.dumps({"type": "session.closed"})


def _form_call(call_id: str = "call_form") -> list[dict[str, Any]]:
    return [
        nested("del_1", {"type": "response.output_item.done", "item": {
            "type": "function_call", "call_id": call_id, "name": OPEN_ADDRESS_FORM,
            "arguments": json.dumps({**NULL_FIELDS, "building_name": "RMZ The Loft"}),
        }}, "e1"),
        nested("del_1", {"type": "response.completed"}, "e2"),
    ]


async def _run_with_ack(ack: dict[str, Any] | None) -> tuple[dict[str, Any], SessionChannel]:
    channel = SessionChannel(owner_id=HOUSEHOLD)
    events: list[dict[str, Any]] = [{"type": "session.input_transcript.done", "transcript": "Naya address, RMZ The Loft"}]
    connection = AckConnection(events + _form_call())
    toolbox = form_box(ConnectionStatus(connected=True, mode="live"))

    def publish(event: dict[str, Any]) -> None:
        channel.publish(event)
        if event.get("type") == "address_form.open" and ack is not None:
            asyncio.get_running_loop().call_soon(channel.acks.resolve, UiAck(ack_id=event["ack_id"], **ack))

    runner = SidebandRunner(connection, toolbox, publish, acks=channel.acks, user_words=toolbox.user_words)  # type: ignore[arg-type]
    await runner.run()
    await asyncio.gather(*runner._continuations, return_exceptions=True)
    return json.loads(connection.sent[0]["item"]["output"]), channel


async def test_voice_result_says_open_only_after_the_screen_confirms() -> None:
    output, channel = await _run_with_ack({"form_visible": True, "reason": "shown", "filled_fields": ["building_name"]})
    assert output["status"] == "form_opened"
    assert output["form_visible"] is True
    assert output["filled_fields"] == ["building_name"]
    # The sideband fed the user's own transcript to the prefill check.
    assert channel.history[1]["prefill"] == {"building_name": "RMZ The Loft"}


async def test_voice_result_reports_form_not_shown() -> None:
    output, _ = await _run_with_ack({"form_visible": False, "reason": "render_failed"})
    assert output["status"] == "form_not_shown"
    assert output["form_visible"] is False
    assert output["reason"] == "render_failed"
    assert "Do not say it is open" in output["message"]


async def test_voice_result_without_any_ack_times_out_as_not_shown(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.voice.ui_ack as ui_ack

    monkeypatch.setattr(ui_ack, "ACK_TIMEOUT_SECONDS", 0.01)
    original = ui_ack.UiAckRegistry.wait

    async def quick(self: Any, ack_id: str, timeout: float = 0.01) -> Any:
        return await original(self, ack_id, 0.01)

    monkeypatch.setattr(ui_ack.UiAckRegistry, "wait", quick)
    output, _ = await _run_with_ack(None)
    assert output["status"] == "form_not_shown"
    assert output["reason"] == "no_screen_response"
