"""Voice/typed tool that prepares a one-tap order card. It never approves or orders.

``prepare_order`` gets the store's current final bill (requesting one when none
is open) and binds an ``OrderConfirmation`` to it. The browser shows that as an
order card with a "Place order" button; only the user's tap on it, handled by
``POST /api/orders/confirm``, approves the bill and places the Zepto
cash-on-delivery order.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, timedelta
from typing import Any

from pydantic import ValidationError

from app.shopping.approval import latest_open_approval
from app.shopping.money import format_rupees, money_fields
from app.shopping.errors import ShoppingError
from app.shopping.models import Approval, ApprovalStatus, Basket
from app.shopping.order_confirmation import (
    ORDERABLE_PROVIDERS,
    OrderConfirmations,
    OrderResult,
)
from app.voice.basket_tools import (
    _STORE_PARAMETER,
    BasketStore,
    BasketToolExecutor,
    StoreArguments,
    _ambiguous,
    _basket_ui,
    _not_connected,
    address_for_model,
)
from app.voice.tools import ToolOutcome


logger = logging.getLogger(__name__)

# Reuse an open bill only when the card can still be tapped for a while.
MIN_BILL_LIFETIME = timedelta(seconds=90)
MAX_PENDING_UPDATES = 5

ORDER_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "prepare_order",
        "description": (
            "Use only when the user explicitly asks to place, confirm or approve the order "
            "(for example 'order kar do', 'place karo', 'approve karo'). Gets that store's "
            "current final bill (requesting it if needed) and shows an order card with a "
            "'Place order' button and Cancel. This tool never approves or places an order: "
            "only the user's tap on 'Place order' does. Zepto cash on delivery only."
        ),
        "parameters": {
            "type": "object",
            "properties": {"store": _STORE_PARAMETER},
            "required": ["store"],
            "additionalProperties": False,
        },
        "strict": True,
    }
]

ORDER_TOOL_NAMES = frozenset(tool["name"] for tool in ORDER_TOOL_DEFINITIONS)

ORDER_NOTE = (
    "An order card with a 'Place order' button is on screen. Nothing is approved or ordered "
    "yet; only the user's tap places it. In one short line say the store, item count, "
    "the payable string exactly as given, cash on delivery and the delivery address label, then ask the user to "
    "tap 'Place order'. Never say the order is placed until an order result arrives."
)

OrderSender = Callable[[str], Awaitable[None]]


class OrderToolExecutor:
    def __init__(
        self,
        basket: BasketToolExecutor,
        confirmations: OrderConfirmations | None,
    ) -> None:
        self.basket = basket
        self.confirmations = confirmations
        self._sender: OrderSender | None = None
        self._pending: list[str] = []

    def attach_sender(self, sender: OrderSender | None) -> None:
        """Voice: deliver order results to the live session as they happen."""

        self._sender = sender

    def take_updates(self) -> list[str]:
        """Typed chat: order results that arrived since the last message."""

        updates, self._pending = self._pending, []
        return updates

    async def notify(self, result: OrderResult) -> None:
        note = result.assistant_note()
        if self._sender is not None:
            await self._sender(note)
            return
        self._pending = [*self._pending, note][-MAX_PENDING_UPDATES:]

    async def execute(self, name: str, raw_arguments: str) -> ToolOutcome:
        try:
            arguments = json.loads(raw_arguments or "{}")
            if not isinstance(arguments, dict):
                raise ValueError
        except (json.JSONDecodeError, ValueError):
            return _error("Invalid arguments.")
        if name != "prepare_order":
            return _error(f"Unknown tool {name}.")
        try:
            return await self._prepare(StoreArguments.model_validate(arguments))
        except ValidationError:
            return _error("Invalid arguments for this tool.")
        except ShoppingError as exc:
            return _error(str(exc))

    async def _prepare(self, arguments: StoreArguments) -> ToolOutcome:
        if self.confirmations is None:
            return _error("Ordering is not available right now. Nothing was ordered.")
        stores = self.basket.stores
        if arguments.store is not None:
            store = stores.get(arguments.store)
            if store is None:
                return _not_connected(arguments.store)
        else:
            filled = [s for s in stores.values() if (await self.basket._active(s)).lines]
            if not filled:
                return _error("The basket is empty.")
            if len(filled) > 1:
                return _ambiguous("Which store's order should be placed", filled)
            store = filled[0]
        if store.provider not in ORDERABLE_PROVIDERS:
            return ToolOutcome(
                model_output={
                    "status": "unsupported",
                    "store": store.label,
                    "store_id": store.provider,
                    "message": (
                        f"{store.label} orders cannot be placed from GharSaathi yet; only Zepto "
                        "cash on delivery. Its bill can still be reviewed on screen. Nothing was ordered."
                    ),
                }
            )
        if not store.address_id:
            return _error(
                f"No {store.label} delivery address is selected. Ask the user to choose one on screen."
            )
        household = self.basket.household_id
        basket = await self.basket.baskets.set_address(household, store.provider, store.address_id)
        if not basket.lines:
            return _error(f"The {store.label} basket is empty.")

        approval = await self._open_bill(basket)
        fresh_request = None
        if approval is None:
            fresh_request = await self.basket.approvals.request(basket.id)
            self.basket._quotes[store.provider] = fresh_request.quote
            approval, basket = fresh_request.approval, fresh_request.basket
        confirmation = await self.confirmations.prepare(
            household, basket, approval, store=store.label, notifier=self.notify
        )
        address = await self.basket._address(store)
        return self._outcome(store, basket, approval, confirmation, address, fresh_request)

    async def _open_bill(self, basket: Basket) -> Approval | None:
        approval = latest_open_approval(
            await self.basket.approvals.approvals.for_basket(basket.id), basket
        )
        if approval is None or approval.quote is None:
            return None
        if approval.status not in {ApprovalStatus.PENDING, ApprovalStatus.APPROVED}:
            return None
        expires_at = approval.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at - self.basket.approvals.clock() < MIN_BILL_LIFETIME:
            return None
        return approval

    def _outcome(
        self,
        store: BasketStore,
        basket: Basket,
        approval: Approval,
        confirmation: Any,
        address: Any,
        fresh_request: Any,
    ) -> ToolOutcome:
        items = [
            {"name": line.name, "pack_size": line.pack_size, "quantity": line.quantity}
            for line in basket.lines
        ]
        item_count = sum(line.quantity for line in basket.lines)
        payable = format_rupees(approval.payable_paise)
        address_block = address_for_model(address)
        created_at = confirmation.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        seconds = max(0, int((confirmation.expires_at - created_at).total_seconds()))
        ui_address: dict[str, str] = {}
        if address is not None:
            ui_address = {"address": address.short_address}
            if address.label:
                ui_address["label"] = address.label
        ui_event: dict[str, Any] = {
            "type": "order.confirmation_requested",
            "provider": store.provider,
            "store": store.label,
            "confirmation": {
                "id": confirmation.id,
                "expires_at": confirmation.expires_at.isoformat(),
                "payable": payable,
                "payable_paise": approval.payable_paise,
                "item_count": item_count,
                "items": items,
                "payment": "cash_on_delivery",
                **({"delivery_address": ui_address} if ui_address else {}),
            },
            "basket": _basket_ui(basket, store),
        }
        if fresh_request is not None:
            # Same shape as POST /api/basket/approval so the basket panel shows this bill.
            from app.api.basket import approval_request_out

            ui_event["request"] = approval_request_out(fresh_request).model_dump(mode="json")
        return ToolOutcome(
            model_output={
                "status": "awaiting_tap",
                "store": store.label,
                "store_id": store.provider,
                "item_count": item_count,
                "items": items,
                **money_fields("payable", approval.payable_paise),
                "payment": "cash_on_delivery",
                **address_block,
                "expires_in_seconds": seconds,
                "note": ORDER_NOTE,
            },
            ui_event=ui_event,
        )


def _error(message: str) -> ToolOutcome:
    return ToolOutcome(model_output={"status": "error", "message": message})
