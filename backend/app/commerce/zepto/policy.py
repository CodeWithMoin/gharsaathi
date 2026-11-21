"""Safety policy for a discovered commerce-tool catalogue.

This module deliberately does not encode a Zepto MCP schema.  The remote
catalogue remains the source of truth for argument validation.  Until that
catalogue is inspected, names are classified conservatively to decide retry
and approval boundaries only.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json
import re
from typing import Any, Iterable, Mapping


class ToolCapability(StrEnum):
    READ = "read"
    CART = "cart"
    ORDER = "order"
    UNKNOWN = "unknown"


class OrderApprovalRequired(ValueError):
    """Raised when an order action is not bound to the exact reviewed bill."""


@dataclass(frozen=True)
class ToolSafety:
    name: str
    capability: ToolCapability

    @property
    def retry_on_transport_failure(self) -> bool:
        """Only idempotent reads may be retried automatically."""

        return self.capability is ToolCapability.READ

    @property
    def requires_exact_approval(self) -> bool:
        return self.capability is ToolCapability.ORDER


@dataclass(frozen=True)
class OrderAction:
    """The local facts an eventual order submission must carry.

    Provider arguments are opaque because the live Zepto input schema has not
    yet been verified.  Their canonical digest prevents approval of one
    provider payload from being reused for another.
    """

    tool_name: str
    basket_id: str
    basket_revision: int
    basket_fingerprint: str
    payable_paise: int
    arguments: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not self.tool_name.strip() or not self.basket_id.strip():
            raise ValueError("An order action needs a tool name and basket id.")
        if self.basket_revision < 0 or self.payable_paise < 0:
            raise ValueError("Order revisions and payable amounts cannot be negative.")
        if not self.basket_fingerprint.strip():
            raise ValueError("An order action needs the reviewed basket fingerprint.")

    @property
    def arguments_digest(self) -> str:
        return canonical_arguments_digest(self.arguments)


@dataclass(frozen=True)
class ExactOrderApproval:
    """A user decision scoped to one concrete order action and price."""

    approval_id: str
    tool_name: str
    basket_id: str
    basket_revision: int
    basket_fingerprint: str
    payable_paise: int
    arguments_digest: str

    @classmethod
    def for_action(cls, approval_id: str, action: OrderAction) -> "ExactOrderApproval":
        if not approval_id.strip():
            raise ValueError("An approval id is required.")
        return cls(
            approval_id=approval_id,
            tool_name=action.tool_name,
            basket_id=action.basket_id,
            basket_revision=action.basket_revision,
            basket_fingerprint=action.basket_fingerprint,
            payable_paise=action.payable_paise,
            arguments_digest=action.arguments_digest,
        )


_WORD_BOUNDARY = re.compile(r"[^A-Za-z0-9]+")
_READ_PREFIXES = ("get", "list", "search", "find", "fetch", "read", "browse")
_READ_MARKERS = ("status", "history", "details", "tracking", "track", "catalogue")
_CART_MARKERS = ("cart", "basket")
_ORDER_MARKERS = ("checkout", "order", "purchase", "payment", "pay")


def classify_tool(name: str) -> ToolSafety:
    """Classify from a tool name without trusting provider descriptions.

    Unknown tools stay unknown (and therefore non-replayable) until explicitly
    reviewed.  Order reads such as ``get_order_status`` are intentionally read
    operations; an order-shaped action is always treated as consequential.
    """

    normalized = " ".join(
        token for token in _WORD_BOUNDARY.sub(" ", _split_camel_case(name)).lower().split() if token
    )
    words = tuple(normalized.split())
    if not words:
        return ToolSafety(name=name, capability=ToolCapability.UNKNOWN)

    if any(word in _CART_MARKERS for word in words):
        return ToolSafety(name=name, capability=ToolCapability.CART)
    if any(word in _ORDER_MARKERS for word in words):
        if words[0] in _READ_PREFIXES or any(word in _READ_MARKERS for word in words):
            return ToolSafety(name=name, capability=ToolCapability.READ)
        return ToolSafety(name=name, capability=ToolCapability.ORDER)
    if words[0] in _READ_PREFIXES:
        return ToolSafety(name=name, capability=ToolCapability.READ)
    return ToolSafety(name=name, capability=ToolCapability.UNKNOWN)


def classify_catalogue(tool_names: Iterable[str]) -> dict[str, ToolSafety]:
    """Classify a discovered catalogue without implying any tool is callable."""

    return {name: classify_tool(name) for name in tool_names}


def require_exact_order_approval(
    action: OrderAction, approval: ExactOrderApproval | None
) -> None:
    """Reject anything but an approval for this exact tool, basket, bill and payload."""

    if approval is None:
        raise OrderApprovalRequired("Review and approve the exact bill before ordering.")
    expected = ExactOrderApproval.for_action(approval.approval_id, action)
    if approval != expected:
        raise OrderApprovalRequired(
            "This approval does not match the exact basket, price, or order action. "
            "Please review the bill again."
        )


def canonical_arguments_digest(arguments: Mapping[str, Any]) -> str:
    """Hash a provider payload deterministically without retaining its values."""

    encoded = json.dumps(
        arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _split_camel_case(value: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value)
