"""Single source of truth for what a user can do, and from which surface.

``docs/CAPABILITIES.md`` is generated from this module and
``tests/test_capabilities.py`` checks it against the real FastAPI routes, voice
tool definitions and voice prompts. When you add, remove or wire up a feature:

1. Edit ``CAPABILITIES`` (or ``INFRASTRUCTURE_ROUTES``) below.
2. Regenerate the doc from ``backend/``::

       uv run --with-requirements requirements.txt python -m app.capabilities --write

3. Run ``pytest tests/test_capabilities.py``.

This module is plain data on purpose: it must not import the app.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class Status(str, Enum):
    WORKS = "works"
    PARTIAL = "partial"
    IN_PROGRESS = "in_progress"
    MISSING = "missing"
    UNSUPPORTED = "unsupported"


STATUS_LABEL = {
    Status.WORKS: "✅ works",
    Status.PARTIAL: "🟡 partial",
    Status.IN_PROGRESS: "🚧 in progress",
    Status.MISSING: "❌ missing",
    Status.UNSUPPORTED: "⛔ unsupported",
}


@dataclass(frozen=True)
class Surface:
    status: Status
    note: str = ""
    # Branch carrying in-flight work for this surface, if any.
    branch: str | None = None


@dataclass(frozen=True)
class Capability:
    id: str
    title: str
    voice: Surface
    typed_chat: Surface
    ui: Surface
    backend: Surface
    # Voice tool names (from the Realtime/Responses tool definitions) that
    # deliver this capability. Required when ``voice`` works or is partial.
    voice_tools: tuple[str, ...] = ()
    # ``"METHOD /path"`` exactly as FastAPI registers it.
    routes: tuple[str, ...] = ()
    # Frontend files (relative to the repo root) implementing the UI surface.
    ui_files: tuple[str, ...] = ()
    # True for behaviour of the voice session itself (turn-taking, transcripts)
    # rather than something the model does through a tool.
    voice_session_behavior: bool = False


def S(status: Status, note: str = "", branch: str | None = None) -> Surface:
    return Surface(status, note, branch)


W, P, IP, M, U = (
    Status.WORKS,
    Status.PARTIAL,
    Status.IN_PROGRESS,
    Status.MISSING,
    Status.UNSUPPORTED,
)

TYPED_NO_ASSISTANT = (
    "Typed text never reaches the assistant: page.tsx runSearch only answers a few "
    "local intents (conversation-intent.mjs) or calls the search endpoints."
)

CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        id="connect_store",
        title="Connect / disconnect a store account (Swiggy, Zepto)",
        voice=S(U, "OAuth must run on the provider's page; never collect credentials in conversation."),
        typed_chat=S(P, "Answers 'is Swiggy/Zepto connected?' locally; cannot connect."),
        ui=S(W, "Store setup sheet and account settings."),
        backend=S(W, "OAuth 2.1 + PKCE with callback readiness check."),
        routes=(
            "GET /api/swiggy/connection",
            "GET /api/swiggy/oauth/readiness",
            "GET /api/swiggy/oauth/start",
            "GET /api/swiggy/oauth/callback",
            "POST /api/swiggy/disconnect",
            "GET /api/zepto/connection",
            "GET /api/zepto/oauth/readiness",
            "GET /api/zepto/oauth/start",
            "GET /api/zepto/oauth/callback",
            "POST /api/zepto/disconnect",
        ),
        ui_files=("frontend/app/page.tsx", "frontend/app/account-settings-sheet.tsx"),
    ),
    Capability(
        id="select_address",
        title="List and select a saved delivery address",
        voice=S(M, "Voice uses whichever address is selected on screen when the session starts; no tool."),
        typed_chat=S(M, TYPED_NO_ASSISTANT),
        ui=S(W, "Radio list per store in the setup sheet."),
        backend=S(W),
        routes=(
            "GET /api/swiggy/addresses",
            "PUT /api/swiggy/addresses/selected",
            "GET /api/zepto/addresses",
            "PUT /api/zepto/addresses/selected",
        ),
        ui_files=("frontend/app/page.tsx",),
    ),
    Capability(
        id="add_address",
        title="Add a new delivery address",
        voice=S(
            W,
            "Zepto, live mode: opens the address form prefilled with what the user said; "
            "the user checks, sets location and saves on screen. Swiggy is refused "
            "(swiggy_unsupported). Known gap: a second voice prefill remounts the form "
            "(new key in page.tsx) and overwrites fields the user is editing.",
        ),
        typed_chat=S(M, TYPED_NO_ASSISTANT),
        ui=S(P, "Zepto only, live mode only (ZeptoAddressForm)."),
        backend=S(P, "Zepto only; Swiggy add-address is unsupported (no route)."),
        voice_tools=("open_address_form",),
        routes=("POST /api/zepto/addresses",),
        ui_files=(
            "frontend/app/zepto-address-form.tsx",
            "frontend/app/voice-address-prefill.mjs",
        ),
    ),
    Capability(
        id="product_search",
        title="Search and compare products across connected stores",
        voice=S(W, "Searches every store with a selected address (Swiggy Instamart + Zepto)."),
        typed_chat=S(
            P,
            "Searches both stores directly via the search endpoints, bypassing the "
            "assistant (no clarification, no Hinglish rewriting beyond local intents).",
        ),
        ui=S(W, "ProductComparison cards grouped across stores."),
        backend=S(W),
        voice_tools=("search_products",),
        routes=(
            "POST /api/swiggy/search",
            "POST /api/swiggy/search/personalized",
            "POST /api/zepto/search",
        ),
        ui_files=("frontend/app/product-comparison.tsx",),
    ),
    Capability(
        id="multi_item_request",
        title="Ask for several items in one message ('atta, dal aur doodh')",
        voice=S(W, "Backend prompt tells the model to search each item separately."),
        typed_chat=S(M, "The whole typed string is sent as a single search query."),
        ui=S(M, "One search box, one query."),
        backend=S(W, "Via the voice model's parallel search_products calls."),
        voice_tools=("search_products",),
    ),
    Capability(
        id="add_to_basket",
        title="Add a product to the basket",
        voice=S(W, "Swiggy and Zepto: each option_id adds to its own store's basket."),
        typed_chat=S(M, TYPED_NO_ASSISTANT),
        ui=S(W, "Add button on Swiggy and Zepto result cards."),
        backend=S(W, "Provider-aware local basket."),
        voice_tools=("add_to_basket",),
        routes=("POST /api/basket/lines",),
        ui_files=("frontend/app/page.tsx", "frontend/app/basket/BasketPanel.tsx"),
    ),
    Capability(
        id="edit_basket",
        title="Change quantity or remove a basket item",
        voice=S(W, "Swiggy and Zepto (store parameter; asks when ambiguous)."),
        typed_chat=S(M, TYPED_NO_ASSISTANT),
        ui=S(W),
        backend=S(W),
        voice_tools=("set_basket_quantity",),
        routes=(
            "PATCH /api/basket/lines/{variant_id}",
            "DELETE /api/basket/lines/{variant_id}",
        ),
        ui_files=("frontend/app/basket/BasketPanel.tsx",),
    ),
    Capability(
        id="view_basket",
        title="Hear or see the basket and item total",
        voice=S(W, "One basket per store; store null lists every store."),
        typed_chat=S(M, TYPED_NO_ASSISTANT),
        ui=S(W),
        backend=S(W),
        voice_tools=("view_basket",),
        routes=("GET /api/basket",),
        ui_files=("frontend/app/basket/BasketPanel.tsx",),
    ),
    Capability(
        id="request_bill",
        title="Request the exact provider bill for review",
        voice=S(W, "Swiggy or Zepto (store parameter); see voice_bill_handoff for the screen gap."),
        typed_chat=S(M, TYPED_NO_ASSISTANT),
        ui=S(W),
        backend=S(W, "Deterministic fee/charge arithmetic from the provider quote."),
        voice_tools=("request_approval",),
        routes=("POST /api/basket/address", "POST /api/basket/approval"),
        ui_files=("frontend/app/basket/BasketPanel.tsx",),
    ),
    Capability(
        id="voice_bill_handoff",
        title="See on screen the bill that voice requested",
        voice=S(W, "request_approval emits basket.approval_requested with the approval and quote."),
        typed_chat=S(M, TYPED_NO_ASSISTANT),
        ui=S(
            M,
            "Known gap: voice-panel.tsx passes only the provider to onBasketTouched, and "
            "page.tsx never sets BasketPanel's incomingRequest, so the basket opens without "
            "the requested bill and the user must request it again on screen.",
        ),
        backend=S(W, "Same approval as POST /api/basket/approval."),
        voice_tools=("request_approval",),
        ui_files=("frontend/app/voice-panel.tsx", "frontend/app/basket/BasketPanel.tsx"),
    ),
    Capability(
        id="approve_bill",
        title="Approve or reject the exact bill",
        voice=S(U, "Approval is bound to the on-screen bill; a spoken 'yes' is not approval."),
        typed_chat=S(U, "Same as voice: approval happens only on the bill review."),
        ui=S(W),
        backend=S(W, "Fingerprint-bound approval."),
        routes=(
            "POST /api/basket/approval/{approval_id}/approve",
            "POST /api/basket/approval/{approval_id}/reject",
        ),
        ui_files=("frontend/app/basket/BasketPanel.tsx",),
    ),
    Capability(
        id="order_zepto_cod",
        title="Place a Zepto cash-on-delivery order",
        voice=S(U, "The final order action is on-screen only, after bill approval."),
        typed_chat=S(U, "Final order action is on-screen only."),
        ui=S(W, "Live mode only, after approval (zepto-checkout-state.mjs)."),
        backend=S(W, "Idempotency-key, approval- and fingerprint-bound."),
        routes=("POST /api/zepto/checkout/cod",),
        ui_files=("frontend/app/basket/BasketPanel.tsx", "frontend/app/basket/zepto-checkout-state.mjs"),
    ),
    Capability(
        id="order_swiggy",
        title="Place a Swiggy Instamart order",
        voice=S(U, "The final order action is on-screen only."),
        typed_chat=S(U, "Final order action is on-screen only."),
        ui=S(M, "A Swiggy bill can be approved but there is no order action."),
        backend=S(M, "No Swiggy checkout; only Zepto COD exists (app/shopping/checkout.py)."),
    ),
    Capability(
        id="offers",
        title="Free-delivery / offer threshold suggestions",
        voice=S(M, "No voice tool."),
        typed_chat=S(M, TYPED_NO_ASSISTANT),
        ui=S(P, "Zepto basket only (BasketPanel), using consented usual items."),
        backend=S(P, "Zepto only."),
        routes=("POST /api/zepto/offer-suggestions",),
        ui_files=("frontend/app/basket/BasketPanel.tsx", "frontend/app/basket/offer-opportunity.mjs"),
    ),
    Capability(
        id="usual_items",
        title="Suggest the household's usual items from purchase history",
        voice=S(
            P,
            "Only on request: recall_household_preferences returns consented past purchases "
            "of the item the user asked for; no proactive suggestions by voice.",
        ),
        voice_tools=("recall_household_preferences",),
        typed_chat=S(M, TYPED_NO_ASSISTANT),
        ui=S(P, "Listed inside Zepto offer suggestions; dismiss/review routes have no frontend caller."),
        backend=S(W),
        routes=(
            "GET /api/memory/usual-suggestions",
            "POST /api/memory/usual-suggestions/{product_id}/dismiss",
            "POST /api/memory/usual-suggestions/{product_id}/review",
        ),
        ui_files=("frontend/app/basket/BasketPanel.tsx",),
    ),
    Capability(
        id="purchase_history_personalization",
        title="Import purchase history and personalise search (consented)",
        voice=S(M, "Permissions and import are on-screen only."),
        typed_chat=S(M, TYPED_NO_ASSISTANT),
        ui=S(W, "PersonalizationOnboarding in the setup sheet."),
        backend=S(W),
        routes=(
            "GET /api/memory/permissions",
            "PUT /api/memory/permissions/{data_category}/{purpose}",
            "POST /api/memory/purchase-history/sync",
            "DELETE /api/memory/purchase-history",
        ),
        ui_files=("frontend/app/personalization-onboarding.tsx",),
    ),
    Capability(
        id="preference_memory",
        title="Remember explicit household preferences (brand, size, corrections)",
        voice=S(
            P,
            "Recall only: recall_household_preferences reads confirmed preferences before a "
            "broad item search. Saving or correcting a preference by voice is not supported.",
        ),
        voice_tools=("recall_household_preferences",),
        typed_chat=S(M, TYPED_NO_ASSISTANT),
        ui=S(M, "No frontend file calls these memory routes."),
        backend=S(W, "Extract/propose, confirm/correct/reject, retrieve, delete."),
        routes=(
            "GET /api/memory",
            "POST /api/memory/extract",
            "POST /api/memory/proposals",
            "POST /api/memory/retrieve",
            "POST /api/memory/{memory_id}/confirm",
            "POST /api/memory/{memory_id}/correct",
            "POST /api/memory/{memory_id}/reject",
            "DELETE /api/memory/{memory_id}",
        ),
    ),
    Capability(
        id="order_history",
        title="See past orders",
        voice=S(M, "No voice tool."),
        typed_chat=S(M, TYPED_NO_ASSISTANT),
        ui=S(M, "No frontend caller."),
        backend=S(
            P,
            "Zepto only. app/api/swiggy_orders.py exists but is not mounted in app.main, "
            "so /api/swiggy/orders does not exist.",
        ),
        routes=("GET /api/zepto/orders", "GET /api/zepto/orders/{order_id}"),
    ),
    Capability(
        id="order_tracking",
        title="Track a delivery",
        voice=S(M, "No voice tool."),
        typed_chat=S(M, TYPED_NO_ASSISTANT),
        ui=S(W, "LiveOrderTracking panel (also reachable from support)."),
        backend=S(W, "Read-only provider status polling."),
        routes=(
            "POST /api/tracking/monitors",
            "GET /api/tracking/monitors",
            "GET /api/tracking/monitors/{monitor_id}",
            "POST /api/tracking/monitors/{monitor_id}/refresh",
            "DELETE /api/tracking/monitors/{monitor_id}",
        ),
        ui_files=("frontend/app/order-tracking.tsx",),
    ),
    Capability(
        id="support_complaint",
        title="Prepare a support complaint with evidence",
        voice=S(
            M,
            "Support agent tools exist (app/support/agent_tools.py, /api/support/agent/*) "
            "but are not wired into the voice session.",
        ),
        typed_chat=S(M, TYPED_NO_ASSISTANT),
        ui=S(W, "SupportPanel: create, attach evidence, prepare, approve."),
        backend=S(W),
        routes=(
            "POST /api/support/complaints",
            "GET /api/support/complaints/{complaint_id}",
            "POST /api/support/complaints/{complaint_id}/evidence",
            "POST /api/support/complaints/{complaint_id}/prepare",
            "POST /api/support/complaints/{complaint_id}/approve",
            "GET /api/support/agent/tools",
            "POST /api/support/agent/execute",
        ),
        ui_files=("frontend/app/support-panel.tsx",),
    ),
    Capability(
        id="support_submit",
        title="Submit a complaint to the provider",
        voice=S(U, "No officially supported provider support channel."),
        typed_chat=S(U, "No officially supported provider support channel."),
        ui=S(U, "No officially supported provider support channel."),
        backend=S(U, "Route exists but always refuses (SupportChannelUnavailable)."),
        routes=("POST /api/support/complaints/{complaint_id}/submit",),
    ),
    Capability(
        id="conversation_history",
        title="Reopen, search or start a new conversation",
        voice=S(U, "Navigating history is an on-screen task; voice turns join the open conversation."),
        typed_chat=S(U, "Navigating history is an on-screen task."),
        ui=S(W, "Recent-chat drawer."),
        backend=S(W, "User-scoped, server-backed history."),
        routes=(
            "GET /api/conversations",
            "GET /api/conversations/current",
            "PUT /api/conversations/current",
            "POST /api/conversations/new",
            "POST /api/conversations/{conversation_id}/open",
        ),
        ui_files=("frontend/app/page.tsx",),
    ),
    Capability(
        id="legal_consent",
        title="Accept terms and privacy consent",
        voice=S(U, "Consent must be an explicit on-screen action."),
        typed_chat=S(U, "Consent must be an explicit on-screen action."),
        ui=S(W, "FirstRunOnboarding."),
        backend=S(W, "Durable consent record."),
        routes=("GET /api/legal-consent", "POST /api/legal-consent"),
        ui_files=("frontend/app/first-run-onboarding.tsx",),
    ),
    Capability(
        id="voice_turn_taking",
        title="Voice turn-taking and barge-in (interrupting the assistant)",
        voice=S(
            P,
            "Relies on the OpenAI Live session defaults: build_session_config (voice/service.py) "
            "sets no turn detection; the semantic_vad config in voice/client.py is unused. "
            "No Hinglish smart-turn model in main; only an uncommitted frontend/app/smart-turn.ts "
            "in the smart-turn-hinglish worktree.",
            "codex/smart-turn-hinglish (uncommitted)",
        ),
        typed_chat=S(U, "Not applicable to typed chat."),
        ui=S(M, "No client-side end-of-turn or interruption handling in voice-panel.tsx."),
        backend=S(P, "Session config only; no turn-detection settings sent."),
        voice_session_behavior=True,
        ui_files=("frontend/app/voice-panel.tsx",),
    ),
    Capability(
        id="voice_transcript",
        title="Show the live voice conversation as chat bubbles",
        voice=S(P, "Transcripts stream into the chat and are saved with the conversation."),
        typed_chat=S(U, "Not applicable: typed messages are added directly."),
        ui=S(
            P,
            "Known gap: voice-panel.tsx keeps one shared activeTranscriptRef for both roles, so "
            "when user and assistant speech overlap (events without a source id) bubbles "
            "fragment into several pieces.",
        ),
        backend=S(W, "Persisted through PUT /api/conversations/current."),
        voice_session_behavior=True,
        ui_files=("frontend/app/voice-panel.tsx", "frontend/app/voice-transcript.mjs"),
    ),
    Capability(
        id="provider_cart_view",
        title="Read the Zepto provider-side cart",
        voice=S(M, "No voice tool."),
        typed_chat=S(M, TYPED_NO_ASSISTANT),
        ui=S(M, "No frontend caller."),
        backend=S(W, "Read-only."),
        routes=("GET /api/zepto/cart",),
    ),
)

# Routes that are plumbing rather than a user-facing capability.
INFRASTRUCTURE_ROUTES: dict[str, str] = {
    "GET /health": "Liveness check.",
    "POST /api/voice/session": "Starts a Realtime voice session (carries every voice capability).",
    "GET /api/voice/sessions/{session_id}/events": "Server-sent voice tool events for the browser UI.",
    "GET /api/swiggy/tools": "Diagnostic: live Swiggy MCP tool catalogue.",
    "GET /api/zepto/tools": "Diagnostic: live Zepto MCP tool catalogue.",
    "GET /api/zepto/capabilities": "Diagnostic: Zepto tool-policy summary.",
}

# API modules whose ``router`` is intentionally not mounted in ``app.main``.
UNMOUNTED_ROUTERS: dict[str, str] = {
    "app.api.swiggy_orders": "Swiggy order history router exists but is not included in app.main.",
}

# snake_case identifiers that appear in voice prompts but are not tool names
# (tool result fields and statuses). Anything else snake_case in a prompt must
# be a defined voice tool.
PROMPT_NON_TOOL_IDENTIFIERS: frozenset[str] = frozenset(
    {
        "option_id",
        "item_id",
        "store_id",
        "not_connected",
        "needs_store",
        "no_memory",
        "purchase_history",
        "confirmed_preference",
    }
)


SURFACES = (
    ("voice", "Voice"),
    ("typed_chat", "Typed chat"),
    ("ui", "On-screen UI"),
    ("backend", "Backend"),
)

BEGIN_MARKER = "<!-- BEGIN GENERATED: python -m app.capabilities --write -->"
END_MARKER = "<!-- END GENERATED -->"
DOC_PATH = Path(__file__).resolve().parents[2] / "docs" / "CAPABILITIES.md"


def _cell(surface: Surface) -> str:
    parts = [f"**{STATUS_LABEL[surface.status]}**"]
    if surface.note:
        parts.append(surface.note)
    if surface.branch:
        parts.append(f"Branch: `{surface.branch}`.")
    return " ".join(parts).replace("|", "\\|")


def render_markdown() -> str:
    lines = [
        BEGIN_MARKER,
        "",
        "| Capability | " + " | ".join(label for _, label in SURFACES) + " | Voice tools |",
        "|---|" + "---|" * len(SURFACES) + "---|",
    ]
    for capability in CAPABILITIES:
        tools = ", ".join(f"`{name}`" for name in capability.voice_tools) or "—"
        cells = [_cell(getattr(capability, key)) for key, _ in SURFACES]
        lines.append(f"| {capability.title} (`{capability.id}`) | " + " | ".join(cells) + f" | {tools} |")
    lines += ["", "### Backend routes by capability", ""]
    for capability in CAPABILITIES:
        if capability.routes:
            lines.append(f"- `{capability.id}`: " + ", ".join(f"`{route}`" for route in capability.routes))
    lines += ["", "### Infrastructure routes", ""]
    lines += [f"- `{route}`: {reason}" for route, reason in INFRASTRUCTURE_ROUTES.items()]
    if UNMOUNTED_ROUTERS:
        lines += ["", "### Routers defined but not mounted", ""]
        lines += [f"- `{module}`: {reason}" for module, reason in UNMOUNTED_ROUTERS.items()]
    lines += ["", END_MARKER]
    return "\n".join(lines)


def write_doc(path: Path = DOC_PATH) -> None:
    text = path.read_text(encoding="utf-8")
    start = text.index(BEGIN_MARKER)
    end = text.index(END_MARKER) + len(END_MARKER)
    path.write_text(text[:start] + render_markdown() + text[end:], encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help=f"Update {DOC_PATH}")
    args = parser.parse_args()
    if args.write:
        write_doc()
    else:
        print(render_markdown())
