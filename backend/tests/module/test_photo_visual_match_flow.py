"""Photo → search → visual re-rank, through the typed chat service and the voice toolbox.

No OpenAI or store calls: the extractor, the vision-match Responses client, the
image fetcher and the store searcher are fakes.
"""

import json
import re
from types import SimpleNamespace
from typing import Any

from app.commerce.swiggy.models import Price, Product
from app.photos.extraction import PhotoReader
from app.photos.store import PhotoAttachmentStore
from app.photos.visual_match import OpenAIVisualMatchModel, PhotoVisualMatcher
from tests.module.test_chat_turn import USER, FakeResponses, FakeSearcher, call, service
from tests.module.test_photo_to_basket import FakeExtractor
from tests.shopping_fakes import World


def pack(name: str, pack_size: str, offer: float, slug: str) -> Product:
    return Product(
        product_id=f"p-{slug}", sku_id=f"sku-{slug}", spin_id=f"spin-{slug}", name=name, brand=name.split()[0],
        pack_size=pack_size, price=Price(mrp=offer, offer_price=offer), available=True,
        image_url=f"https://instamart-media-assets.swiggy.com/swiggy/image/upload/{slug}.png",
    )


RESULTS = [
    pack("Amul Kool Kesar Flavoured Milk", "180 ml", 25, "kool"),
    pack("Amul Lassi", "200 ml", 20, "lassi"),
    pack("Amul Smoodh Chocolate Milk", "160 ml", 20, "smoodh"),
]
LASSI_PHOTO = {
    "kind": "product_photo",
    "items": [{
        "printed_name": "Amul Lassi", "product": "lassi", "brand": "Amul", "variant": None, "pack_size": "200 ml",
        "quantity": None, "search_query": "Amul Lassi 200 ml", "alternative_queries": [],
        "visual_description": "blue-white tetra pack, Amul logo, lassi glass picture", "identified_from": "both",
        "confidence": "high", "evidence": "Amul Lassi 200 ml",
    }],
    "notes": "",
}


class FakeFetcher:
    def __init__(self) -> None:
        self.urls: list[str] = []

    async def fetch(self, url: str, provider: str) -> bytes | None:
        self.urls.append(url)
        return b"\xff\xd8\xff" + url.encode()


class FakeVision:
    """The vision model: labels candidates by the store name shown next to each image."""

    def __init__(self, same: str | None = "Lassi", fail: bool = False) -> None:
        self.same = same
        self.fail = fail
        self.requests: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> SimpleNamespace:
        self.requests.append(kwargs)
        if self.fail:
            raise RuntimeError("vision down")
        matches = []
        for part in kwargs["input"][0]["content"]:
            found = re.match(r"Candidate (\d+), store name \(data\): (.*)", part.get("text", ""))
            if found:
                name = json.loads(found.group(2))
                label = "same_product" if self.same and self.same in name else "different"
                matches.append({"candidate": int(found.group(1)), "visual_match": label, "reason": "pack shape"})
        return SimpleNamespace(output_text=json.dumps({"matches": matches}))


def photo_chat(responses: FakeResponses, store: PhotoAttachmentStore, vision: FakeVision, fetcher: FakeFetcher):
    matcher = PhotoVisualMatcher(OpenAIVisualMatchModel("", "", "test-model", responses=vision), fetcher)
    searcher = FakeSearcher(RESULTS)
    chat = service(responses, World(), searcher)
    chat.voice.photo_reader = PhotoReader(store, FakeExtractor(LASSI_PHOTO), visual_matcher=matcher)
    return chat, searcher


async def run_lassi(vision: FakeVision):
    store = PhotoAttachmentStore()
    photo = store.put(USER, b"lassi-photo")
    fetcher = FakeFetcher()
    responses = FakeResponses([[call("search_products", "s1", query="Amul Lassi 200 ml")], "Done."])
    chat, searcher = photo_chat(responses, store, vision, fetcher)
    result = await chat.run_turn(USER, None, "buy this", swiggy_address_id="addr_home", attachment_id=photo.attachment_id)
    return store, photo, fetcher, responses, result, searcher


async def test_lassi_photo_puts_the_lassi_first_marked_same_product_then_discards_the_photo() -> None:
    vision = FakeVision()
    store, photo, fetcher, responses, result, searcher = await run_lassi(vision)

    assert searcher.queries == ["Amul Lassi 200 ml"]
    output = responses.outputs(1)[0]
    assert [(r["name"], r["position"], r.get("visual_match")) for r in output["results"]] == [
        ("Amul Lassi", 1, "same_product"),
        ("Amul Kool Kesar Flavoured Milk", 2, "different"),
        ("Amul Smoodh Chocolate Milk", 3, "different"),
    ]
    assert output["photo_match"]["status"] == "same_product_found"
    event = next(e for e in result.ui_events if e["type"] == "tool.results")
    assert [(p["name"], p["position"], p.get("visual_match")) for p in event["products"]] == [
        ("Amul Lassi", 1, "same_product"),
        ("Amul Kool Kesar Flavoured Milk", 2, "different"),
        ("Amul Smoodh Chocolate Milk", 3, "different"),
    ]
    # One vision call with the user's photo plus the three store images, then the photo is gone.
    assert len(vision.requests) == 1
    images = [p for p in vision.requests[0]["input"][0]["content"] if p["type"] == "input_image"]
    assert len(images) == 4 and vision.requests[0]["store"] is False
    assert len(fetcher.urls) == 3
    assert len(store) == 0
    assert store.get(USER, photo.attachment_id) is None


async def test_held_photo_is_never_read_twice_and_option_ids_still_add_the_right_pack() -> None:
    store = PhotoAttachmentStore()
    photo = store.put(USER, b"lassi-photo")
    responses = FakeResponses(["Photo mein Amul Lassi 200 ml hai—dhoondhun?", [call("search_products", "s1", query="Amul Lassi 200 ml")], "Done."])
    chat, _ = photo_chat(responses, store, FakeVision(), FakeFetcher())
    await chat.run_turn(USER, None, "", swiggy_address_id="addr_home", attachment_id=photo.attachment_id)
    # Held for the match: not readable again, but still in memory.
    assert store.get(USER, photo.attachment_id) is None and store.newest(USER) is None
    assert store.held(USER, photo.attachment_id) is not None
    await chat.run_turn(USER, None, "haan", swiggy_address_id="addr_home")
    first = responses.outputs(2)[0]["results"][0]
    assert first["name"] == "Amul Lassi" and first["position"] == 1
    session = chat.sessions.get(USER, None)
    assert session.toolbox.options[first["option_id"]].product.name == "Amul Lassi"
    assert len(store) == 0


async def test_when_nothing_matches_the_closest_options_stay_and_the_model_is_told_to_say_so() -> None:
    store, _, _, responses, result, _ = await run_lassi(FakeVision(same=None))
    output = responses.outputs(1)[0]
    assert [r["name"] for r in output["results"]] == [p.name for p in RESULTS]
    assert all(r["visual_match"] == "different" for r in output["results"])
    assert output["photo_match"]["status"] == "no_exact_match"
    assert "couldn't find that exact pack" in output["photo_match"]["message"]
    event = next(e for e in result.ui_events if e["type"] == "tool.results")
    assert len(event["products"]) == 3  # nothing dropped
    assert len(store) == 0


async def test_a_failed_match_call_keeps_the_original_order() -> None:
    store, _, _, responses, result, _ = await run_lassi(FakeVision(fail=True))
    output = responses.outputs(1)[0]
    assert [r["name"] for r in output["results"]] == [p.name for p in RESULTS]
    assert "photo_match" not in output and all("visual_match" not in r for r in output["results"])
    event = next(e for e in result.ui_events if e["type"] == "tool.results")
    assert all("visual_match" not in p for p in event["products"])
    assert len(store) == 0


async def test_an_unrelated_search_is_not_compared_and_lists_are_never_held() -> None:
    store = PhotoAttachmentStore()
    photo = store.put(USER, b"lassi-photo")
    vision = FakeVision()
    responses = FakeResponses([[call("search_products", "s1", query="Fortune sunflower oil 1 L")], "Done."])
    chat, _ = photo_chat(responses, store, vision, FakeFetcher())
    await chat.run_turn(USER, None, "aur oil bhi", swiggy_address_id="addr_home", attachment_id=photo.attachment_id)
    assert vision.requests == []
    assert store.held(USER, photo.attachment_id) is not None  # still waiting; the TTL applies

    listed = PhotoAttachmentStore()
    shot = listed.put(USER, b"list")
    reader = PhotoReader(listed, FakeExtractor(), visual_matcher=PhotoVisualMatcher(OpenAIVisualMatchModel("", "", "m", responses=vision), FakeFetcher()))
    result = await reader.read(USER, shot.attachment_id)
    assert result.status == "ok" and result.held is False and len(listed) == 0


async def test_voice_tool_read_holds_the_newest_photo_and_drops_older_ones() -> None:
    from app.voice.photo_tools import PhotoToolExecutor

    store = PhotoAttachmentStore()
    store.put(USER, b"old")
    newest = store.put(USER, b"new")
    reader = PhotoReader(store, FakeExtractor(LASSI_PHOTO), visual_matcher=PhotoVisualMatcher(OpenAIVisualMatchModel("", "", "m", responses=FakeVision()), FakeFetcher()))
    tool = PhotoToolExecutor(reader, USER)
    outcome = await tool.execute("read_attached_photo", "{}")
    assert outcome.model_output["items"][0]["identified_from"] == "both"
    assert len(store) == 1 and store.held(USER, newest.attachment_id) is not None
    assert tool.held is not None and tool.held.attachment_id == newest.attachment_id
