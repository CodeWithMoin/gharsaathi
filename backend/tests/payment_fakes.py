"""Fakes for prepaid (online) Zepto orders. Nothing here talks to a provider.

``TEST_CONTRACT`` uses deliberately fake field names and values: the real
Zepto response shapes are unknown until the owner's live smoke runs record them
(docs/LIVE_SMOKE.md). Tests exercise the parsing and state machine through this
contract; production keeps ``VERIFIED_CONTRACT = None``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.commerce.zepto.payments import (
    MethodContract,
    MethodsContract,
    StatusContract,
    ZeptoPaymentContract,
    ZeptoPaymentGateway,
)
from app.shopping.basket import item_total_paise
from app.shopping.models import Basket, Charge, Quote


TEST_CONTRACT = ZeptoPaymentContract(
    methods=MethodsContract(
        list_path="fakeMethods",
        id_key="fakeId",
        enabled_key="fakeEnabled",
        identifiers={
            "online": frozenset({"FAKE_ONLINE"}),
            "upi_reserve": frozenset({"FAKE_UPI"}),
            "wallet": frozenset({"FAKE_WALLET"}),
        },
    ),
    per_method={
        "online": MethodContract(
            preview_payment_method="FAKE_ONLINE",
            order_id_path="fakeOrder.ref",
            payable_path="fakeOrder.amount",
            payment_url_path="fakeOrder.payLink",
        ),
        "upi_reserve": MethodContract(
            preview_payment_method="FAKE_UPI",
            order_id_path="fakeOrder.ref",
            payable_path="fakeOrder.amount",
        ),
        "wallet": MethodContract(
            preview_payment_method="FAKE_WALLET",
            order_id_path="fakeOrder.ref",
            payable_path="fakeOrder.amount",
            placed_path="fakeOrder.state",
            placed_values=frozenset({"FAKE_PAID"}),
        ),
    },
    status=StatusContract(
        payment_status_path="fakePayment",
        succeeded=frozenset({"FAKE_OK"}),
        failed=frozenset({"FAKE_FAIL"}),
        pending=frozenset({"FAKE_WAIT"}),
        order_status_path="fakeOrderState",
        order_confirmed=frozenset({"FAKE_PLACED"}),
        order_failed=frozenset({"FAKE_ORDER_FAIL"}),
        poll_instruction_path="fakePollNext",
    ),
)

PAY_LINK = "https://pay.example.test/session/abc"
FEE = 2500


def methods_payload(*names: str) -> dict[str, Any]:
    return {"fakeMethods": [{"fakeId": name, "fakeEnabled": True} for name in names]}


def start_payload(amount: int, *, ref: str = "ZP-1", link: str | None = PAY_LINK, state: str | None = None) -> dict[str, Any]:
    order: dict[str, Any] = {"ref": ref, "amount": amount}
    if link is not None:
        order["payLink"] = link
    if state is not None:
        order["state"] = state
    return {"fakeOrder": order}


def status_payload(payment: str, order: str | None = None, *, poll_next: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {"fakePayment": payment}
    if order is not None:
        payload["fakeOrderState"] = order
    if poll_next:
        payload["fakePollNext"] = True
    return payload


class ScriptedZeptoClient:
    """Records every tool call; answers from per-tool scripts (last answer repeats)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.scripts: dict[str, list[object]] = {}

    def script(self, tool: str, *answers: object) -> None:
        self.scripts[tool] = list(answers)

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    async def call_tool(
        self, name: str, arguments: dict[str, Any], *, retry_on_auth: bool = True
    ) -> dict[str, Any]:
        self.calls.append((name, dict(arguments)))
        answers = self.scripts.get(name)
        if not answers:
            raise AssertionError(f"Unexpected provider call {name}")
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        if isinstance(answer, BaseException):
            raise answer
        return answer  # type: ignore[return-value]


class FakePreviewQuotes:
    """Stands in for ZeptoQuoteProvider.quote_unlocked with a prepaid preview tool."""

    def __init__(self) -> None:
        self.previews: list[tuple[str, str]] = []
        self.extra = 0

    async def quote_unlocked(
        self, basket: Basket, *, preview_tool: str = "create_order", payment_method_value: str = "COD"
    ) -> Quote:
        self.previews.append((preview_tool, payment_method_value))
        items = item_total_paise(basket)
        return Quote(
            provider=basket.provider,
            basket_id=basket.id,
            basket_revision=basket.revision,
            item_total_paise=items,
            charges=[Charge(label="Delivery fee", amount_paise=FEE)],
            payable_paise=items + FEE + self.extra,
            quoted_at=datetime.now(UTC),
        )


def payment_gateway(
    client: ScriptedZeptoClient,
    quotes: FakePreviewQuotes | None = None,
    *,
    enabled: bool = True,
    allowed: frozenset = frozenset({"online", "upi_reserve", "wallet"}),
    contract: ZeptoPaymentContract | None = TEST_CONTRACT,
) -> ZeptoPaymentGateway:
    return ZeptoPaymentGateway(
        client=client,  # type: ignore[arg-type]
        quotes=quotes or FakePreviewQuotes(),  # type: ignore[arg-type]
        enabled=enabled,
        allowed=allowed,
        contract=contract,
    )
