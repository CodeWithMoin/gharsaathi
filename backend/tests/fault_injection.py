"""Fault-injection fakes for the failure-mode catalogue (docs/FAILURE_MODES.md).

Every fake here stands in for an external boundary — the Swiggy/Zepto MCP
client, a product searcher, the quote provider, the Zepto COD gateway, the
OpenAI Responses API and the GPT-Live session endpoint — and can be told to
fail in a specific way: raise an exception, return a malformed payload, or
answer slowly. Every fake records its calls, so tests can assert that order
and payment calls are made at most once and that nothing retries them.

Nothing here can reach a real provider or OpenAI.
"""

from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import httpx

from app.commerce.provider_state import SavedAddressSummary
from app.commerce.swiggy.models import Price, Product, ProductSearchResult
from app.shopping.approval import ApprovalService
from app.shopping.basket import BasketService, item_total_paise
from app.shopping.checkout import CheckoutService
from app.shopping.memory import InMemoryApprovalRepository, InMemoryBasketRepository
from app.shopping.models import Basket, BasketLine, Charge, ProductRef, ProviderName, Quote
from app.shopping.order_confirmation import OrderConfirmations
from app.voice.toolbox import VoiceToolbox
from tests.shopping_fakes import Clock, InMemoryCheckoutAttempts

FIXTURES = Path(__file__).parent / "fixtures"
HOUSEHOLD = "user_failure_modes"
SWIGGY_ADDRESS = "swiggy-home"
ZEPTO_ADDRESS = "zepto-home"
FEE = 2500


# --------------------------------------------------------------------------
# Fault descriptions
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Raise:
    """Raise this exception (a fresh copy per call is not needed: tests read the type)."""

    error: BaseException


@dataclass(frozen=True)
class Slow:
    """Answer after ``seconds``; then behave like ``then`` (default: the normal answer)."""

    seconds: float
    then: object | None = None


@dataclass(frozen=True)
class Payload:
    """Return this exact (possibly malformed) payload."""

    value: object


Fault = Raise | Slow | Payload


def transport_error(kind: str) -> Exception:
    """Exceptions shaped like the ones the MCP/httpx stack raises, by name.

    The provider clients classify failures from exception text (``429``,
    ``401``, ``connection reset``), so these carry the same markers.
    """

    errors: dict[str, Exception] = {
        "connection_refused": ConnectionRefusedError("[Errno 61] Connection refused"),
        "connection_reset": RuntimeError("Connection reset by peer"),
        "stream_closed": RuntimeError("stream closed: EndOfStream"),
        "http_500": RuntimeError("HTTP 500 Internal Server Error"),
        "http_502": RuntimeError("HTTP 502 Bad Gateway"),
        "http_429": RuntimeError("HTTP 429 Too Many Requests"),
        "http_401": RuntimeError("HTTP 401 Unauthorized"),
        "timeout": TimeoutError("timed out"),
    }
    return errors[kind]


async def _apply(fault: object | None, normal: Callable[[], Any]) -> Any:
    if fault is None:
        return normal()
    if isinstance(fault, Raise):
        raise fault.error
    if isinstance(fault, Slow):
        await asyncio.sleep(fault.seconds)
        return await _apply(fault.then, normal)
    if isinstance(fault, Payload):
        return copy.deepcopy(fault.value)
    if isinstance(fault, BaseException):
        raise fault
    return copy.deepcopy(fault)


# --------------------------------------------------------------------------
# Provider boundary
# --------------------------------------------------------------------------


def product(name: str, pack: str, offer: float, *, prefix: str = "z", available: bool = True) -> Product:
    slug = f"{name}-{pack}".lower().replace(" ", "-")
    return Product(
        product_id=f"{prefix}-p-{slug}",
        sku_id=f"{prefix}-sku-{slug}",
        spin_id=f"{prefix}-spin-{slug}",
        name=name,
        brand=name.split()[0],
        pack_size=pack,
        price=Price(mrp=offer + 10, offer_price=offer),
        available=available,
    )


MILK = [product("Amul Taaza Milk", "500 ml", 27)]


class FaultySearcher:
    """A ``ProductSearcher`` that applies one fault per call (the last one repeats)."""

    def __init__(self, products: list[Product] | None = None, *faults: object) -> None:
        self.products = MILK if products is None else products
        self.faults = list(faults)
        self.queries: list[str] = []

    async def search(self, query: str, address_id: str, offset: int) -> ProductSearchResult:
        self.queries.append(query)
        fault = None
        if self.faults:
            fault = self.faults.pop(0) if len(self.faults) > 1 else self.faults[0]
        result = await _apply(fault, lambda: ProductSearchResult(products=self.products))
        return result


ZEPTO_SEARCH_PAYLOAD = {
    "products": [
        {
            "id": "product-1",
            "productVariantId": "variant-1",
            "storeProductId": "store-product-1",
            "name": "Amul Taaza Milk",
            "packSize": "500 ml",
            "price": 2700,
            "availableQuantity": 3,
        }
    ]
}


class FaultyMcpClient:
    """Stands in for ``ZeptoMcpClient``: per-tool scripted answers and faults.

    ``script[tool]`` is a list consumed one entry per call (the last entry
    repeats). An entry is a ``Fault``, an exception, or a payload. Tools
    without a script answer from ``defaults``; anything else is a test error.
    """

    def __init__(self, script: dict[str, list[object]] | None = None) -> None:
        self.script = {name: list(answers) for name, answers in (script or {}).items()}
        self.calls: list[tuple[str, dict[str, Any], bool]] = []
        self.session_generation = 1
        self.resets = 0
        self.defaults: dict[str, object] = {
            "select_saved_address": {"success": True},
            "search_products": ZEPTO_SEARCH_PAYLOAD,
            "search_multiple_products": ZEPTO_SEARCH_PAYLOAD,
            "list_saved_addresses": {"addresses": [{"id": ZEPTO_ADDRESS, "addressLine": "Green Park"}]},
            "update_cart": {"success": True},
        }

    def names(self) -> list[str]:
        return [name for name, _, _ in self.calls]

    def count(self, name: str) -> int:
        return self.names().count(name)

    async def prepare_session(self) -> int:
        answers = self.script.get("prepare_session")
        if answers:
            fault = answers.pop(0) if len(answers) > 1 else answers[0]
            await _apply(fault, lambda: None)
        return self.session_generation

    async def reset_session(self) -> None:
        self.resets += 1
        self.session_generation += 1

    async def aclose(self) -> None:
        await self.reset_session()

    async def call_tool(
        self, name: str, arguments: dict[str, Any], *, retry_on_auth: bool = True
    ) -> dict[str, Any]:
        self.calls.append((name, dict(arguments), retry_on_auth))
        answers = self.script.get(name)
        if answers:
            fault = answers.pop(0) if len(answers) > 1 else answers[0]
            return await _apply(fault, lambda: copy.deepcopy(self.defaults.get(name)))
        if name in self.defaults:
            return copy.deepcopy(self.defaults[name])  # type: ignore[return-value]
        raise AssertionError(f"Unexpected provider call {name}")


def zepto_preview(basket: Basket, **overrides: Any) -> dict[str, Any]:
    """A valid unconfirmed Zepto COD preview for exactly ``basket`` (fixture shape)."""

    preview = json.loads((FIXTURES / "zepto_cod_preview.json").read_text())
    data = preview["data"]
    subtotal = item_total_paise(basket)
    data["deliveryAddress"]["id"] = basket.address_id
    data["items"] = [
        {
            "name": line.name,
            "price": line.unit_price_paise,
            "productVariantId": line.ref.variant_id,
            "storeProductId": line.ref.sku_id,
            "quantity": line.quantity,
        }
        for line in basket.lines
    ]
    data["totalItems"] = len(basket.lines)
    data["subTotal"] = subtotal
    data["deliveryFee"] = 3000
    data["deliveryFeePreDiscount"] = 3000
    data["deliveryFeeDiscount"] = 0
    data["packagingFee"] = 0
    data["discount"] = 0
    data["freeDelivery"] = None
    data["toPayAmount"] = subtotal + 3000
    data.update(overrides)
    return preview


def zepto_confirmed(order_id: str, payable_paise: int, **overrides: Any) -> dict[str, Any]:
    """A confirmed COD order in the shape ``parse_confirmed_cod_order`` accepts."""

    data: dict[str, Any] = {
        "isPreview": False,
        "paymentMethod": "COD",
        "orderId": order_id,
        "toPayAmount": payable_paise,
        "etaInMinutes": 12,
    }
    data.update(overrides)
    return {"data": data}


class FaultyQuotes:
    """A ``QuoteProvider`` with scripted faults; otherwise an exact quote (+ ``extra``)."""

    def __init__(self, *faults: object) -> None:
        self.faults = list(faults)
        self.calls = 0
        self.extra = 0
        self.unavailable: list[ProductRef] = []

    async def quote(self, basket: Basket) -> Quote:
        self.calls += 1
        fault = None
        if self.faults:
            fault = self.faults.pop(0) if len(self.faults) > 1 else self.faults[0]

        def normal() -> Quote:
            items = item_total_paise(basket)
            return Quote(
                provider=basket.provider,
                basket_id=basket.id,
                basket_revision=basket.revision,
                item_total_paise=items,
                charges=[Charge(label="Delivery fee", amount_paise=FEE)],
                payable_paise=items + FEE + self.extra,
                unavailable_refs=list(self.unavailable),
                quoted_at=datetime.now(UTC),
            )

        return await _apply(fault, normal)


@dataclass
class Placed:
    order_id: str
    payable_paise: int
    status: str = "placed"


class FaultyGateway:
    """Stands in for ``ZeptoCodCheckoutGateway``; records every would-be order."""

    def __init__(self, quotes: FaultyQuotes, *faults: object) -> None:
        self.quotes = quotes
        self.faults = list(faults)
        self.calls: list[str] = []

    async def confirm_cod(self, basket: Basket) -> object:
        self.calls.append(basket.id)
        fault = None
        if self.faults:
            fault = self.faults.pop(0) if len(self.faults) > 1 else self.faults[0]
        payable = item_total_paise(basket) + FEE + self.quotes.extra
        return await _apply(fault, lambda: Placed(order_id=f"ZEP-{len(self.calls)}", payable_paise=payable))


class Addresses:
    async def describe(self, external_user_id: str, provider: str, address_id: str) -> SavedAddressSummary:
        return SavedAddressSummary(label="Home", short_address="Green Park")


@dataclass
class FaultWorld:
    """In-memory baskets, approvals, cards and checkout with fault-injectable edges."""

    quotes: FaultyQuotes = field(default_factory=FaultyQuotes)
    clock: Clock = field(default_factory=Clock)

    def __post_init__(self) -> None:
        self.basket_repo = InMemoryBasketRepository()
        self.approval_repo = InMemoryApprovalRepository()
        self.baskets = BasketService(self.basket_repo, self.approval_repo, self.clock)
        self.approvals = ApprovalService(
            self.basket_repo, self.approval_repo, lambda _: self.quotes, self.clock
        )
        self.confirmations = OrderConfirmations(self.basket_repo, self.approvals, self.clock)
        self.gateway = FaultyGateway(self.quotes)
        self.attempts = InMemoryCheckoutAttempts(self.basket_repo, self.approval_repo)
        self.checkout = CheckoutService(
            self.basket_repo,
            self.approval_repo,
            self.attempts,
            self.quotes,
            self.gateway,
            self.clock,
        )

    def toolbox(
        self,
        *,
        swiggy: Any = None,
        zepto: Any = None,
        swiggy_address: str | None = SWIGGY_ADDRESS,
        zepto_address: str | None = ZEPTO_ADDRESS,
        **kwargs: Any,
    ) -> VoiceToolbox:
        return VoiceToolbox(
            swiggy if swiggy is not None else FaultySearcher([product("Amul Taaza Milk", "500 ml", 28, prefix="s")]),
            self.baskets,
            self.approvals,
            swiggy_address,
            zepto if zepto is not None else FaultySearcher(),
            zepto_address,
            HOUSEHOLD,
            addresses=Addresses(),
            order_confirmations=self.confirmations,
            **kwargs,
        )

    async def fill(self, provider: ProviderName = "zepto", quantity: int = 2) -> Basket:
        await self.baskets.add_line(
            HOUSEHOLD,
            provider,
            BasketLine(
                ref=ProductRef(
                    provider=provider, product_id="oil", variant_id=f"{provider}-oil", sku_id=f"{provider}-oil-sku"
                ),
                name="Fortune Sunflower Oil",
                pack_size="1 L",
                unit_price_paise=9850,
                quantity=quantity,
            ),
        )
        return await self.baskets.set_address(
            HOUSEHOLD, provider, ZEPTO_ADDRESS if provider == "zepto" else SWIGGY_ADDRESS
        )

    async def snapshot(self, provider: ProviderName = "zepto") -> tuple[int, list[tuple[str, int]], str]:
        """Basket revision, lines and status: equal snapshots mean nothing was changed."""

        basket = await self.baskets.active(HOUSEHOLD, provider)
        return basket.revision, [(line.ref.variant_id, line.quantity) for line in basket.lines], basket.status.value


async def call(toolbox: VoiceToolbox, name: str, **arguments: Any) -> tuple[dict[str, Any], dict[str, Any] | None]:
    outcome = await toolbox.execute(name, json.dumps(arguments))
    return outcome.model_output, outcome.ui_event


# --------------------------------------------------------------------------
# OpenAI boundary
# --------------------------------------------------------------------------


def function_call(name: str, call_id: str, **arguments: Any) -> SimpleNamespace:
    return SimpleNamespace(type="function_call", name=name, arguments=json.dumps(arguments), call_id=call_id)


class FaultyResponses:
    """Scripted ``AsyncOpenAI().responses``.

    Each script entry is a reply string, a list of function calls, an
    exception (raised), or ``Slow``. ``""`` models empty output and a
    refusal (the SDK's ``output_text`` is empty for a refusal item).
    """

    def __init__(self, script: list[object]) -> None:
        self.script = list(script)
        self.requests: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> SimpleNamespace:
        self.requests.append(kwargs)
        step: object = self.script.pop(0) if self.script else "Theek hai."
        while isinstance(step, Slow):
            await asyncio.sleep(step.seconds)
            step = step.then if step.then is not None else "Theek hai."
        if isinstance(step, Raise):
            raise step.error
        if isinstance(step, BaseException):
            raise step
        response_id = f"resp_{len(self.requests)}"
        if isinstance(step, str):
            output_type = "message" if step else "refusal"
            return SimpleNamespace(id=response_id, output=[SimpleNamespace(type=output_type)], output_text=step)
        return SimpleNamespace(id=response_id, output=step, output_text="")


def openai_error(kind: str) -> Exception:
    """OpenAI SDK errors without a network call."""

    import openai

    request = httpx.Request("POST", "https://api.openai.test/v1/responses")
    if kind == "rate_limit":
        return openai.RateLimitError(
            "Rate limit reached", response=httpx.Response(429, request=request), body=None
        )
    if kind == "timeout":
        return openai.APITimeoutError(request=request)
    if kind == "connection":
        return openai.APIConnectionError(request=request)
    if kind == "server":
        return openai.InternalServerError(
            "Server error", response=httpx.Response(500, request=request), body=None
        )
    raise KeyError(kind)


def live_sessions_transport(answer: object) -> httpx.MockTransport:
    """A GPT-Live ``/live/sessions`` endpoint that answers with a fault.

    ``answer`` is an ``httpx.Response``, an exception to raise, or a
    JSON-able payload returned with HTTP 200.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(answer, BaseException):
            raise answer
        if isinstance(answer, httpx.Response):
            return answer
        return httpx.Response(200, json=answer)

    return httpx.MockTransport(handler)
