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

TYPED_VIA_ASSISTANT = (
    "POST /api/chat/turn runs the voice backend model with the same instructions, tools "
    "and VoiceToolbox checks; page.tsx applies its ui_events through assistant-events.mjs. "
    "Not yet verified in a real browser session."
)
TYPED_NO_TOOL = "No assistant tool (same as voice); typed chat can only answer in text."

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
        voice=S(
            M,
            "Voice uses whichever address is selected on screen when the session starts; "
            "basket tools report it (label + short line from the saved snapshot). No selection tool.",
        ),
        typed_chat=S(
            M,
            "Typed turns use the addresses selected on screen (sent with each turn); "
            "there is no selection tool.",
        ),
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
        typed_chat=S(W, "Same open_address_form tool and limits as voice. " + TYPED_VIA_ASSISTANT),
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
            W,
            "At most 2 searches per message. If the chat endpoint fails, 'Seedha search "
            "karein' runs the old direct search. " + TYPED_VIA_ASSISTANT,
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
        typed_chat=S(
            P,
            "The model searches each item separately, but at most 2 searches per message; "
            "further items are asked about in the reply.",
        ),
        ui=S(M, "One search box, one query."),
        backend=S(W, "Via the voice model's parallel search_products calls."),
        voice_tools=("search_products",),
    ),
    Capability(
        id="add_to_basket",
        title="Add a product to the basket",
        voice=S(W, "Swiggy and Zepto: each option_id adds to its own store's basket."),
        typed_chat=S(W, TYPED_VIA_ASSISTANT),
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
        typed_chat=S(W, TYPED_VIA_ASSISTANT),
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
        typed_chat=S(W, TYPED_VIA_ASSISTANT),
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
        typed_chat=S(W, TYPED_VIA_ASSISTANT),
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
        typed_chat=S(
            W,
            "A typed 'bill bana do' returns basket.approval_requested; page.tsx hands its request "
            "to BasketPanel like voice. " + TYPED_VIA_ASSISTANT,
        ),
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
        voice=S(
            W,
            "Only through the order card: prepare_order shows it and the user's tap on "
            "'Place order' approves this exact bill and places the order. A spoken 'yes' is "
            "not approval.",
        ),
        typed_chat=S(W, "Same as voice: prepare_order shows the order card; the tap approves."),
        ui=S(W, "BasketPanel approve/reject, or the order card's 'Place order' tap."),
        backend=S(W, "Fingerprint-bound approval; the tap re-checks the bound confirmation first."),
        voice_tools=("prepare_order",),
        routes=(
            "POST /api/basket/approval/{approval_id}/approve",
            "POST /api/basket/approval/{approval_id}/reject",
        ),
        ui_files=("frontend/app/basket/BasketPanel.tsx",),
    ),
    Capability(
        id="order_zepto_cod",
        title="Place a Zepto cash-on-delivery order",
        voice=S(
            W,
            "On an explicit order request prepare_order shows the order card (store, items, "
            "payable, COD, address, expiry); only the user's tap places it. The result is spoken "
            "via session.commentary.append.",
        ),
        typed_chat=S(
            W,
            "Same prepare_order card; the tap result reaches the next typed turn as a server "
            "note. " + TYPED_VIA_ASSISTANT,
        ),
        ui=S(
            W,
            "Order card in the chat timeline (order-confirmation-card.tsx) with Place order / "
            "Cancel and a countdown; BasketPanel's COD button after approval (live mode only).",
        ),
        backend=S(
            W,
            "Single-use, 5-minute confirmation bound to user, basket revision, approval, quote "
            "and address (app/shopping/order_confirmation.py), then the existing approve + "
            "idempotent COD checkout.",
        ),
        voice_tools=("prepare_order",),
        routes=(
            "POST /api/zepto/checkout/cod",
            "POST /api/orders/confirm",
            "POST /api/orders/cancel",
        ),
        ui_files=(
            "frontend/app/basket/BasketPanel.tsx",
            "frontend/app/basket/zepto-checkout-state.mjs",
            "frontend/app/order-confirmation-card.tsx",
        ),
    ),
    Capability(
        id="order_swiggy",
        title="Place a Swiggy Instamart order",
        voice=S(U, "prepare_order returns unsupported for Instamart; only the bill can be reviewed."),
        typed_chat=S(U, "Same as voice."),
        ui=S(M, "A Swiggy bill can be approved but there is no order action."),
        backend=S(M, "No Swiggy checkout; only Zepto COD exists (app/shopping/checkout.py)."),
    ),
    Capability(
        id="offers",
        title="Free-delivery / offer threshold suggestions",
        voice=S(
            P,
            "Read-only: request_approval and view_basket report the store-confirmed offers "
            "on the current bill; no promo-code entry and no threshold suggestions by voice.",
        ),
        voice_tools=("request_approval", "view_basket"),
        typed_chat=S(P, "Same as voice: store-confirmed offers on the bill only, read-only."),
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
            "On request only: recall_household_preferences and search_products results "
            "marked 'remembered' use consented past purchases of the item the user asked "
            "for; a short consented summary is in the session context. No proactive "
            "suggestions by voice.",
        ),
        voice_tools=("recall_household_preferences",),
        typed_chat=S(P, "Same as voice: recall tool, remembered annotations and consented summary; read-only."),
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
        typed_chat=S(M, TYPED_NO_TOOL),
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
            "Recall only: recall_household_preferences and 'remembered' search annotations "
            "read confirmed preferences. Saving or correcting a preference by voice is not "
            "supported.",
        ),
        voice_tools=("recall_household_preferences",),
        typed_chat=S(P, "Same as voice: recall tool, remembered annotations and consented summary; read-only."),
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
        typed_chat=S(M, TYPED_NO_TOOL),
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
        typed_chat=S(M, TYPED_NO_TOOL),
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
        typed_chat=S(M, TYPED_NO_TOOL),
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
        id="user_profile",
        title="How to address the user (first name, address form, reply language)",
        voice=S(
            P,
            "Read at voice-session start and added to the session instructions; it can only "
            "be changed on screen, never by voice or inference.",
        ),
        typed_chat=S(P, "Read on every typed turn and added to the same backend instructions; changed on screen only."),
        ui=S(
            P,
            "Account settings > Preferences (address form, first name; language is mirrored). "
            "First-run onboarding does not call it yet.",
        ),
        backend=S(W, "user_profiles table, keyed by the verified Clerk user id."),
        voice_session_behavior=True,
        routes=("GET /api/profile", "PUT /api/profile"),
        ui_files=("frontend/app/account-settings-sheet.tsx",),
    ),
    Capability(
        id="voice_turn_taking",
        title="Voice turn-taking and barge-in (interrupting the assistant)",
        voice=S(
            IP,
            "GPT-Live has no turn_detection/VAD/commit controls; VOICE_TURN_TAKING selects native "
            "(default, unchanged), tuned (option A: interruption + Hinglish patience policy appended "
            "to the Live instructions) or smart_turn (option B: tuned + browser Hinglish end-of-turn "
            "model that holds playback mid-thought and stops it on barge-in). Measured in "
            "docs/voice-turn-taking.md; not yet verified in a real browser session.",
            "codex/smart-turn-options",
        ),
        typed_chat=S(U, "Not applicable to typed chat."),
        ui=S(
            IP,
            "voice-panel.tsx starts SmartTurnController (smart-turn.ts) only when the session reports "
            "smart_turn; the model is lazy-loaded, SHA-256 verified, and falls back to option A.",
            "codex/smart-turn-options",
        ),
        backend=S(
            IP,
            "Setting voice_turn_taking; voice/turn_taking.py builds the instructions and the "
            "session response's turn_taking field.",
            "codex/smart-turn-options",
        ),
        voice_session_behavior=True,
        ui_files=("frontend/app/voice-panel.tsx", "frontend/app/smart-turn.ts"),
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
        typed_chat=S(M, TYPED_NO_TOOL),
        ui=S(M, "No frontend caller."),
        backend=S(W, "Read-only."),
        routes=("GET /api/zepto/cart",),
    ),
)

# Routes that are plumbing rather than a user-facing capability.
INFRASTRUCTURE_ROUTES: dict[str, str] = {
    "GET /health": "Liveness check.",
    "POST /api/voice/session": "Starts a Realtime voice session (carries every voice capability).",
    "POST /api/chat/turn": "Runs one typed chat turn through the voice backend model and tools (carries every typed-chat capability).",
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
        # Fields, statuses and enum values shown in the backend few-shot examples.
        "swiggy_instamart",
        "price_rupees",
        "pack_size",
        "line_total",
        "item_total",
        "awaiting_user_approval",
        "form_opened",
        "prefilled_fields",
        "address_type",
        "flat_details",
        "building_name",
        "formatted_address",
        "contact_name",
        # Bill offers, memory annotations and recall result fields.
        "bill_offers",
        "current_bill",
        "promo_code_entry",
        "not_available",
        "free_delivery",
        "bought_on",
        "product_name",
        "last_purchased_on",
        "purchase_count",
        "saved_preferences",
        # prepare_order result fields and statuses.
        "awaiting_tap",
        "cash_on_delivery",
        "item_count",
        "delivery_address",
    }
)


API_CONTRACTS: dict[str, tuple[str, ...]] = {
    "POST /api/chat/turn": (
        "Auth: Clerk bearer token and accepted current terms (403 legal_consent_required otherwise).",
        'Request: {"conversation_id": "<uuid>" | null, "message": "AXE Dark sabse chhota add karo", '
        '"swiggy_address_id": "<id>" | null, "zepto_address_id": "<id>" | null}',
        "conversation_id null means the current conversation; another user's id is 404. "
        "message: 1-2000 characters; unknown keys are 422.",
        'Response 200: {"conversation_id": "<uuid>" | null, "reply": "Roman-script Hinglish text", '
        '"ui_events": [{"type": "tool.results" | "basket.updated" | "basket.approval_requested" | '
        '"order.confirmation_requested" | "address_form.open" | "address_form.unavailable" | "tool.not_connected" | "tool.error", ...}]}',
        "ui_events use the voice sideband shapes. Caps per turn: 6 tool rounds, 2 searches. "
        "Errors: 503 no OpenAI key, 502 model failure, 504 turn timeout (CHAT_TURN_TIMEOUT_SECONDS).",
    ),
    "GET /api/profile": (
        "Auth: Clerk bearer token; the record belongs to the verified user only.",
        "Response 200 (defaults when nothing was saved):",
        '{"first_name": null, "address_form": "neutral", "language": "hinglish", "updated_at": null}',
    ),
    "PUT /api/profile": (
        "Auth: Clerk bearer token. Partial update: only the keys sent are changed; unknown keys are 422.",
        'Request: {"first_name": "Moin" | null, "address_form": "masculine" | "feminine" | "neutral", '
        '"language": "hinglish" | "hindi" | "english"}',
        "first_name: up to 40 letters, spaces, apostrophes, dots or hyphens; null or blank clears it.",
        'Response 200: {"first_name": "Moin", "address_form": "masculine", "language": "hinglish", '
        '"updated_at": "2026-11-22T10:00:00Z"}',
    ),
}


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
    lines += ["", "### API contracts", ""]
    for route, contract in API_CONTRACTS.items():
        lines.append(f"- `{route}`")
        lines += [f"  - `{line}`" if line.startswith("{") else f"  - {line}" for line in contract]
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
