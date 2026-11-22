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
    # Shipped but labelled "Beta" in the UI; results may be less reliable.
    beta: bool = False


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
            "POST /api/swiggy/oauth/authorization",
            "GET /api/swiggy/oauth/callback",
            "POST /api/swiggy/disconnect",
            "GET /api/zepto/connection",
            "GET /api/zepto/oauth/readiness",
            "GET /api/zepto/oauth/start",
            "POST /api/zepto/oauth/authorization",
            "GET /api/zepto/oauth/callback",
            "POST /api/zepto/disconnect",
        ),
        ui_files=("frontend/app/page.tsx", "frontend/app/account-settings-sheet.tsx"),
    ),
    Capability(
        id="select_address",
        title="List and select a saved delivery address",
        voice=S(
            W,
            "'Address change karo' lists the saved addresses per connected store "
            "(list_delivery_addresses, server-side snapshot, no provider call) and switches "
            "with select_delivery_address: validated against the user's own snapshot, saved "
            "like PUT .../addresses/selected, moved onto the store's basket (an open bill is "
            "superseded), used by later tools in the same session, and shown on screen "
            "(address.selected). Zepto's snapshot also refreshes automatically when stale "
            "(30 min at load/session start, 5 min at picker open, debounced); a selection "
            "deleted in Zepto is cleared, never replaced, and the user is asked to pick again.",
        ),
        typed_chat=S(W, "Same list/select tools as voice; the switched address is kept for later turns. " + TYPED_VIA_ASSISTANT),
        ui=S(W, "Radio list per store in the setup sheet; a voice/typed switch updates it without restarting voice."),
        backend=S(W, "Demo mode lists the static fixture and keeps the selection for the session only."),
        voice_tools=("list_delivery_addresses", "select_delivery_address"),
        routes=(
            "GET /api/swiggy/addresses",
            "PUT /api/swiggy/addresses/selected",
            "GET /api/zepto/addresses",
            "PUT /api/zepto/addresses/selected",
        ),
        ui_files=("frontend/app/page.tsx", "frontend/app/assistant-events.mjs"),
    ),
    Capability(
        id="add_address",
        title="Add a new delivery address",
        voice=S(
            W,
            "Zepto, live mode: opens a focused Add delivery address sheet prefilled only with "
            "words found in the user's own recent speech/chat (sample values, phone numbers "
            "and coordinates are dropped server-side). Follow-up details fill the open form's "
            "empty fields without overwriting what the user typed. The assistant says the form "
            "is open only after the browser reports form_visible (POST .../ui-ack). Swiggy is "
            "refused (swiggy_unsupported); demo mode returns demo_mode.",
        ),
        typed_chat=S(
            W,
            "Same open_address_form tool; the browser reports form_visible with the next turn "
            "(ui_acks). " + TYPED_VIA_ASSISTANT,
        ),
        ui=S(P, "Zepto only, live mode only (AddressFormSheet + ZeptoAddressForm)."),
        backend=S(P, "Zepto only; Swiggy add-address is unsupported (no route)."),
        voice_tools=("open_address_form",),
        routes=("POST /api/zepto/addresses", "POST /api/voice/sessions/{session_id}/ui-ack"),
        ui_files=(
            "frontend/app/zepto-address-form.tsx",
            "frontend/app/address-form-sheet.tsx",
            "frontend/app/address-form-merge.mjs",
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
        ui=S(W, "One options carousel per searched item, best pick first, backend ₹ strings."),
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
        id="shop_from_photo",
        title="Shop from a photo (product pack, shopping list, list screenshot, shelf/fridge)",
        voice=S(
            P,
            "The user attaches a photo with the button beside the voice pill and refers to it "
            "('photo dekho'); the backend model calls read_attached_photo, confirms the list in "
            "one line, then searches each item (at most 4 searches per request). GPT-Live is "
            "not told when a photo arrives, so the user must mention it.",
        ),
        typed_chat=S(
            W,
            "A message with attachment_id reads the photo before the model runs; one confirm "
            "line, then up to 5 searches per message while a photo list is open. "
            + TYPED_VIA_ASSISTANT,
        ),
        ui=S(
            W,
            "Composer photo button: Take photo (native camera input on phones, webcam dialog on "
            "desktops) or Choose from gallery; client-side downscale to 1600 px JPEG (drops "
            "EXIF); thumbnail chip with remove; send with or without text; the user's bubble "
            "shows the photo thumbnail (tap for a larger view) and the saved chat keeps only "
            "the 'Photo' placeholder plus a <=320 px, <=40 KB server-made thumbnail. The photo "
            "menu/camera dialog and the chip carry a 'Beta' tag; an option that looks like the "
            "photo shows 'Looks like your photo', one that does not says so.",
        ),
        backend=S(
            W,
            "Magic-byte type check, 8 MB cap, Pillow decode, all metadata stripped, re-encoded "
            "JPEG kept in memory per user for at most 15 min and deleted right after reading; "
            "the upload returns a metadata-free thumbnail data URL that saved chats re-validate. "
            "Strict-JSON vision extraction (PHOTO_EXTRACTION_MODEL) reads printed pack names "
            "verbatim and identifies packs by their look (brand visible -> brand query; no brand "
            "-> the most likely generic product plus up to 2 alternatives, never an invented "
            "brand), image text treated as data, prices stripped, at most 15 items. A product "
            "or shelf photo is held (read-once, same TTL) until its first 3 items' searches are "
            "compared with it: one bounded vision call (PHOTO_VISUAL_MATCH_MODEL, 6 s) labels "
            "the top 6 store images same_product / similar / different; code puts the same "
            "product first and different ones last, and falls back to the normal order on any "
            "error.",
        ),
        voice_tools=("read_attached_photo",),
        beta=True,
        routes=("POST /api/chat/attachments", "DELETE /api/chat/attachments/{attachment_id}"),
        ui_files=(
            "frontend/app/page.tsx",
            "frontend/app/photo-attachment-ui.tsx",
            "frontend/app/photo-attachment.mjs",
            "frontend/app/camera-dialog.tsx",
            "frontend/app/camera-capture.mjs",
        ),
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
        id="clear_basket",
        title="Empty the basket in one step",
        voice=S(
            W,
            "clear_basket empties one store or every store in one revision-checked edit; the open "
            "bill and order card are cancelled; refused while an order outcome is unknown.",
        ),
        typed_chat=S(W, TYPED_VIA_ASSISTANT),
        ui=S(
            W,
            "Basket card 'Clear basket' opens a ConfirmDialog (destructive) naming the item "
            "count; disabled with the reason shown while checkout is pending or the outcome is "
            "unknown.",
        ),
        backend=S(
            W,
            "BasketService.clear (one revision-checked edit, supersedes the open bill and order "
            "card); the route passes expected_revision = the basket the user saw (409 if it changed).",
        ),
        voice_tools=("clear_basket",),
        routes=("POST /api/baskets/{provider}/clear",),
        ui_files=("frontend/app/basket/BasketPanel.tsx",),
    ),
    Capability(
        id="saved_lists",
        title="Saved lists the user creates ('Weekly list') and adding one to the basket",
        voice=S(
            W,
            "add_saved_list(name): finds the user's own list by name and adds it; items not "
            "added are reported with the reason.",
        ),
        typed_chat=S(W, TYPED_VIA_ASSISTANT),
        ui=S(
            W,
            "Quick chip 'My lists' opens the lists popup: new list by name, save basket as "
            "list, edit items/quantities, delete, and 'Add list to basket' with a report and a "
            "'Get bill' offer.",
        ),
        backend=S(
            W,
            "User-scoped table (saved_shopping_lists, unique name per user, 20 lists x 40 "
            "items). Adding re-checks each item at its store (same variant, available, today's "
            "price) and adds per store in one revision-checked edit; never quotes or orders.",
        ),
        voice_tools=("add_saved_list",),
        routes=(
            "GET /api/lists",
            "POST /api/lists",
            "PATCH /api/lists/{list_id}",
            "POST /api/lists/{list_id}/add-basket",
            "DELETE /api/lists/{list_id}",
            "POST /api/lists/{list_id}/add-to-basket",
        ),
        ui_files=("frontend/app/quick-chips.tsx", "frontend/app/saved-lists-dialog.tsx", "frontend/app/quick-actions.mjs"),
    ),
    Capability(
        id="quick_actions",
        title="Quick chips: reorder last order, recent items, live order chip",
        voice=S(U, "Screen shortcuts; by voice the user just asks ('pichla order phir se')."),
        typed_chat=S(U, "Screen shortcuts; 'Mera order kahan hai?' sends a normal typed turn."),
        ui=S(
            W,
            "Chips on the home screen and above the composer (max 6, scrollable): Reorder last "
            "order, Mera order kahan hai? (only while an order is active), the last 3 distinct "
            "ordered products (toast + Undo), My lists. Header chip for an active order "
            "(arriving / out for delivery / running late) scrolls to its status card.",
        ),
        backend=S(
            W,
            "Reads only this household's real orders (ordered basket + confirmed attempt with a "
            "provider order id; synth- sample orders excluded); reorder and recent items re-check "
            "each item at its store and add it; nothing is quoted or ordered.",
        ),
        routes=(
            "GET /api/quick-actions",
            "POST /api/quick-actions/repeat-last",
            "POST /api/quick-actions/recent-item",
        ),
        ui_files=("frontend/app/quick-chips.tsx", "frontend/app/quick-actions.mjs", "frontend/app/page.tsx"),
    ),
    Capability(
        id="express_order",
        title="Ask → tap: order or add named items in one turn",
        voice=S(
            W,
            "express_order searches, picks a confident match (remembered, or the single product of "
            "the named brand and size), adds and shows the order card in one call; quick_add does "
            "the same for plain adds. Ambiguous items return needs_choice (one question).",
        ),
        typed_chat=S(W, TYPED_VIA_ASSISTANT),
        ui=S(W, "Same order card and basket card as prepare_order and add_to_basket."),
        backend=S(W, "app/voice/express_tools.py; never approves or orders."),
        voice_tools=("express_order", "quick_add"),
        ui_files=("frontend/app/page.tsx",),
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
            "idempotent COD checkout. An unknown outcome is settled only from read-only Zepto "
            "order history (app/shopping/reconciliation.py): after the tap, on 'Check with "
            "Zepto', on app load and from get_order_status; never by repeating the order call.",
        ),
        voice_tools=("prepare_order",),
        routes=(
            "POST /api/zepto/checkout/cod",
            "POST /api/orders/confirm",
            "POST /api/orders/cancel",
            "POST /api/orders/check",
            "POST /api/orders/reconcile-pending",
        ),
        ui_files=(
            "frontend/app/basket/BasketPanel.tsx",
            "frontend/app/basket/zepto-checkout-state.mjs",
            "frontend/app/order-confirmation-card.tsx",
            "frontend/app/card-lifecycle.mjs",
        ),
    ),
    Capability(
        id="order_zepto_prepaid",
        title="Pay for a Zepto order online (UPI, Zepto Cash, pay online)",
        voice=S(
            IP,
            "prepare_order takes an optional payment_method; the card offers only methods "
            "that are enabled and listed by Zepto. Off by default "
            "(ZEPTO_ONLINE_PAYMENTS_ENABLED) and until the response contract is verified.",
            branch="codex/online-payment",
        ),
        typed_chat=S(IP, "Same prepare_order card as voice.", branch="codex/online-payment"),
        ui=S(
            IP,
            "Order card payment-method chips, Pay ₹… button, Open payment and "
            "\"I've paid, check status\" (order-confirmation-card.tsx).",
            branch="codex/online-payment",
        ),
        backend=S(
            IP,
            "Same bound card, tap, approval and one-shot claim as COD; the card binds the "
            "method. Payment states awaiting_payment / payment_succeeded / payment_failed / "
            "paid_order_failed / payment_unknown; single check_payment_status reads "
            "(app/shopping/payments.py, app/commerce/zepto/payments.py). Response shapes "
            "unverified: VERIFIED_CONTRACT is None, so only COD is offered.",
            branch="codex/online-payment",
        ),
        voice_tools=("prepare_order",),
        routes=(
            "POST /api/orders/payment-method",
            "POST /api/orders/payment-status",
        ),
        ui_files=("frontend/app/order-confirmation-card.tsx",),
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
        ui=S(
            W,
            "PersonalizationOnboarding in the setup sheet: import, 'Re-import full history' "
            "(once per 24 h per store), opt-in weekly sync on app open.",
        ),
        backend=S(W),
        routes=(
            "GET /api/memory/permissions",
            "PUT /api/memory/permissions/{data_category}/{purpose}",
            "POST /api/memory/purchase-history/sync",
            "GET /api/memory/purchase-history/sync-state",
            "PUT /api/memory/purchase-history/auto-sync",
            "POST /api/memory/purchase-history/auto-sync/run",
            "DELETE /api/memory/purchase-history",
        ),
        ui_files=("frontend/app/personalization-onboarding.tsx",),
    ),
    Capability(
        id="preference_memory",
        title="Remember, update and forget household preferences and rules (brand, size, store, avoid)",
        voice=S(
            IP,
            "Tier 1: a habit stated in passing ('hum hamesha Amul lete hain') is saved at once "
            "with an acknowledgement and undo; 'yaad rakhna' reads back, saves on a clear yes. "
            "remember_preference / update_preference / forget_preference (forget-all needs a "
            "confirm token) / list_preferences / confirm_memory. Tier 2: repeated choices "
            "become a pending proposal offered at most once per session. Active avoid/prefer "
            "memories filter or annotate search_products results in code.",
            "codex/memory-write-manage",
        ),
        voice_tools=(
            "recall_household_preferences",
            "remember_preference",
            "update_preference",
            "forget_preference",
            "list_preferences",
            "confirm_memory",
        ),
        typed_chat=S(
            W,
            "Same tools and rules through /api/chat/turn, which shares the voice toolbox.",
        ),
        ui=S(
            W,
            "Settings 'Yaad rakhi baatein': grouped list, inline edit (correct), delete, undo "
            "for 'abhi seekha', pending suggestions confirm/reject, forget-all, permission toggle.",
        ),
        ui_files=(
            "frontend/app/memory-settings.tsx",
            "frontend/app/memory-api.ts",
            "frontend/app/account-settings-sheet.tsx",
        ),
        backend=S(
            W,
            "Structured kinds, sensitive-data policy in code, consent switch, supersede on "
            "update, undo, forget-all with a confirm token, repeated-choice proposals.",
        ),
        routes=(
            "GET /api/memory",
            "POST /api/memory/extract",
            "POST /api/memory/proposals",
            "POST /api/memory/retrieve",
            "POST /api/memory/forget-all/token",
            "POST /api/memory/forget-all",
            "POST /api/memory/{memory_id}/confirm",
            "POST /api/memory/{memory_id}/correct",
            "POST /api/memory/{memory_id}/undo",
            "POST /api/memory/{memory_id}/reject",
            "DELETE /api/memory/{memory_id}",
        ),
    ),
    Capability(
        id="memory_summary",
        title="What do you know about me? (saved memory by group, with sources)",
        voice=S(
            W,
            "summarize_my_memory: top 5 (food rules, nevers, usual buys, home, style, orders) "
            "with each source; 'baaki screen par'; opens the full list on screen (memory.summary).",
        ),
        typed_chat=S(W, "Same tool. " + TYPED_VIA_ASSISTANT),
        ui=S(
            W,
            "Settings → What GharSaathi knows: Home, Food, Usual buys, Avoid, Shopping style, "
            "From your orders; each with source and date, Edit / Delete, Delete everything.",
        ),
        backend=S(
            W,
            "Read-only summary of confirmed memories (visible even when memory is off), "
            "profile choices and consented order history. Household facts (diet, keep-outs, "
            "usual quantity, value preference, home, delivery notes) are written today only by "
            "the owner's manual seed (make memory-seed); diet/keep-outs/avoid filter search and "
            "usual quantities default adds in code.",
        ),
        voice_tools=("summarize_my_memory",),
        routes=("GET /api/memory/summary",),
        ui_files=(
            "frontend/app/memory-settings.tsx",
            "frontend/app/memory-groups.mjs",
            "frontend/app/assistant-events.mjs",
        ),
    ),
    Capability(
        id="order_history",
        title="See past orders",
        voice=S(
            W,
            "list_recent_orders: up to 5 recent Zepto orders (code, status, items, total) from "
            "Zepto's read-only list_order_history, for 'pichla order kya tha?'.",
        ),
        typed_chat=S(W, "Same tool. " + TYPED_VIA_ASSISTANT),
        ui=S(M, "No order-history screen."),
        backend=S(
            P,
            "Zepto only (one connected Zepto account per deployment). app/api/swiggy_orders.py "
            "exists but is not mounted in app.main, so /api/swiggy/orders does not exist.",
        ),
        voice_tools=("list_recent_orders",),
        routes=("GET /api/zepto/orders", "GET /api/zepto/orders/{order_id}"),
    ),
    Capability(
        id="order_tracking",
        title="Track a delivery",
        voice=S(
            W,
            "get_order_status('latest' or an order id/code) answers 'mera order kahan hai / "
            "kitna time aur / order aaya?' in one line from the household's own monitor (or "
            "Zepto history when none), with the store's ETA if given and when it was last "
            "updated if stale; never guessed.",
        ),
        typed_chat=S(W, "Same tool. " + TYPED_VIA_ASSISTANT),
        ui=S(
            W,
            "A placed Zepto order card becomes a live status card (placed → packed → out for "
            "delivery → delivered / cancelled), saved with the chat; in-app toast for out for "
            "delivery, delivered, delayed (ETA later by ≥10 min) and cancelled. Track order "
            "opens LiveOrderTracking in a popup; Settings → Help with an order → Track too.",
        ),
        backend=S(
            W,
            "Read-only provider status polling at the provider's interval. POST /api/orders/confirm "
            "starts a monitor for a provider-confirmed Zepto order (idempotent per order). Zepto "
            "status comes from get_order_status when the live catalogue has it, else the read-only "
            "get_order_detail (shipment status + Zepto's committed ETA). Updates show in-app, and "
            "as browser notifications when the user turned them on (see browser_notifications); "
            "no SMS or calls.",
        ),
        voice_tools=("get_order_status",),
        routes=(
            "POST /api/tracking/monitors",
            "GET /api/tracking/monitors",
            "GET /api/tracking/monitors/{monitor_id}",
            "POST /api/tracking/monitors/{monitor_id}/refresh",
            "DELETE /api/tracking/monitors/{monitor_id}",
        ),
        ui_files=(
            "frontend/app/order-tracking.tsx",
            "frontend/app/order-status-card.tsx",
            "frontend/app/order-status.mjs",
        ),
    ),
    Capability(
        id="browser_notifications",
        title="Browser notifications for order updates",
        voice=S(U, "Turned on and off in Settings → Notifications only; the browser must ask the user."),
        typed_chat=S(U, "Same as voice."),
        ui=S(
            W,
            "Opt-in: Settings → Notifications (a toggle; the browser permission is asked only after "
            "the tap), per-kind switches, Send a test, and a one-time 'Get delivery updates as "
            "notifications?' after the first placed order. Denied, unsupported, insecure (plain-http "
            "LAN IP) and iPhone-needs-Home-Screen states explain themselves. Hidden when the server "
            "has no VAPID keys. The service worker skips the system notification while a "
            "GharSaathi tab is visible and focused (the in-app toast shows instead).",
        ),
        backend=S(
            W,
            "Web Push (VAPID, pywebpush) to the user's own browsers: order placed (also via "
            "reconciliation), out for delivery, delivered (Zepto's time), running late, cancelled, "
            "order still unconfirmed after the checks, payment needs action/failed (online payments "
            "only) and an untapped order card about to expire. Deduped per event, rate-limited per "
            "user, subscriptions encrypted at rest and removed on 404/410. No replenishment or "
            "low-stock reminders.",
        ),
        routes=(
            "GET /api/notifications/vapid-public-key",
            "GET /api/notifications/settings",
            "PUT /api/notifications/settings",
            "POST /api/notifications/subscriptions",
            "DELETE /api/notifications/subscriptions",
            "POST /api/notifications/test",
        ),
        ui_files=(
            "frontend/app/notification-settings.tsx",
            "frontend/app/push-notifications.mjs",
            "frontend/public/sw.js",
        ),
    ),
    Capability(
        id="order_cancel",
        title="Cancel a placed order",
        voice=S(
            U,
            "Zepto's documented MCP tools have no cancel tool. She says it can't be done here and "
            "to cancel in the Zepto app quickly, before packing. get_order_status reports "
            "cancel_supported: true only if the live catalogue ever exposes an order-cancel tool; "
            "it is never called. Using one would need its own tap-confirmed card, like Place order.",
        ),
        typed_chat=S(U, "Same as voice."),
        ui=S(U, "The status card says to open the Zepto app to cancel."),
        backend=S(
            U,
            "No cancel call exists. app/tracking/cancellation.py only detects a cancel tool in "
            "the live catalogue.",
        ),
    ),
    Capability(
        id="payment_methods",
        title="Pay online / UPI / wallet",
        voice=S(
            U,
            "Only cash on delivery is supported here; she does not promise UPI at the door.",
        ),
        typed_chat=S(U, "Same as voice."),
        ui=S(U, "The order card shows Cash on delivery only."),
        backend=S(
            U,
            "Zepto's catalogue has online, UPI and wallet order tools; GharSaathi uses only the "
            "approval-bound COD path.",
        ),
    ),
    Capability(
        id="prepare_inbound_delivery_line",
        title="Prepare approved context for the inbound delivery line",
        voice=S(
            U,
            "Not part of the browser voice session. A separately deployed Sarvam inbound "
            "agent handles recognized Zepto/Swiggy callers.",
        ),
        typed_chat=S(U, "Sharing requires the explicit approval tick and tap in the UI."),
        ui=S(
            W,
            "LiveOrderTracking shows the selected store address read-only (Change address opens "
            "the picker), the profile name, saved per-address directions and handover "
            "instructions (edited in a popup), Share with delivery line, an opt-in auto-share "
            "switch, and Clear delivery line.",
        ),
        backend=S(
            W,
            "Reads the selected address, profile name and saved directions server-side and "
            "streams one record for the server-configured caller to Sarvam; clears it (a "
            "caller_authorized=no record) on address change, delivered or cancelled. Owner "
            "only (SARVAM_INBOUND_OWNER_USER_ID or ALLOWED_USER_IDS). Never initiates a call.",
        ),
        routes=(
            "GET /api/inbound-delivery/config",
            "GET /api/inbound-delivery/directions",
            "PUT /api/inbound-delivery/directions",
            "DELETE /api/inbound-delivery/directions",
            "GET /api/inbound-delivery/line",
            "PUT /api/inbound-delivery/line/settings",
            "DELETE /api/inbound-delivery/line",
            "POST /api/inbound-delivery/contexts",
            "GET /api/inbound-delivery/contexts/{cohort_id}",
        ),
        ui_files=("frontend/app/order-tracking.tsx", "frontend/app/delivery-line-panel.tsx"),
    ),
    Capability(
        id="inbound_rider_call_summary",
        title="Show what happened on a call to the inbound delivery line",
        voice=S(U, "Not a voice feature; calls appear as cards in the chat."),
        typed_chat=S(U, "Not a chat feature; calls appear as cards in the chat."),
        ui=S(
            P,
            "Chat card near the order (who called, time, outcome, one-line summary), "
            "'Rider has your directions' on the order status card, a 'rider_called' "
            "browser notification (user-toggleable). Not yet verified with a real call.",
        ),
        backend=S(
            P,
            "RiderCallMonitor reads Sarvam's documented List interactions endpoint, read-only, "
            "at most every 30 s and only while a user has an active order or a context "
            "prepared in the last 2 h; stores a redacted outcome summary; deletable. Tested "
            "with mocked Sarvam responses only.",
        ),
        routes=(
            "GET /api/inbound-delivery/calls",
            "DELETE /api/inbound-delivery/calls",
            "DELETE /api/inbound-delivery/calls/{call_id}",
        ),
        ui_files=("frontend/app/inbound-calls-ui.tsx", "frontend/app/order-status-card.tsx"),
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
    Capability(
        id="rider_assist",
        title="Rider assist: an AI answers the delivery partner's forwarded call",
        voice=S(
            U,
            "Not part of the user's voice session. A forwarded phone call reaches the owner's "
            "Vobiz number; its signed answer URL returns a bidirectional <Stream> to WSS "
            "/api/rider/stream/{call}/{token} (OpenAI Realtime, its own 4 tools: "
            "get_active_order, get_delivery_notes, notify_owner, end_call).",
        ),
        typed_chat=S(M, "Not a chat feature; rider calls appear as cards in the chat."),
        ui=S(
            P,
            "Settings → Rider assist (Beta): switch, delivery notes popup, setup checklist, "
            "recent calls with delete; new calls show as a toast and a chat card (polled every "
            "30 s) and as a 'rider_called' browser notification (user-toggleable). Not yet "
            "verified with a real Vobiz call.",
        ),
        backend=S(
            P,
            "Off by default (RIDER_ASSIST_ENABLED). Vobiz webhook signatures (fail closed), "
            "per-call signed stream path, single owner (RIDER_ASSIST_OWNER_USER_ID), μ-law "
            "passthrough to Realtime audio/pcmu (L16 resampled), 40 ms playAudio frames, "
            "barge-in clearAudio/truncate, 3 min cap, spoken apology via the after-stream "
            "redirect when OpenAI is down; answers only from the owner's notes and confirmed "
            "order, never OTPs, money or order changes; stores a short summary, never audio. "
            "Unverified against a live Vobiz call.",
        ),
        beta=True,
        routes=(
            "GET /api/rider/settings",
            "PUT /api/rider/settings",
            "GET /api/rider/calls",
            "POST /api/rider/calls/{call_id}/seen",
            "DELETE /api/rider/calls",
            "DELETE /api/rider/calls/{call_id}",
            "POST /api/rider/vobiz/answer",
            "POST /api/rider/vobiz/after-stream",
        ),
        ui_files=(
            "frontend/app/rider-assist-settings.tsx",
            "frontend/app/rider-assist.mjs",
            "frontend/app/rider-calls.tsx",
            "frontend/app/account-settings-sheet.tsx",
            "frontend/app/page.tsx",
        ),
    ),
)

# Routes that are plumbing rather than a user-facing capability.
INFRASTRUCTURE_ROUTES: dict[str, str] = {
    "GET /health": "Liveness check.",
    "POST /api/voice/session": "Starts a Realtime voice session (carries every voice capability).",
    "POST /api/chat/turn": "Runs one typed chat turn through the voice backend model and tools (carries every typed-chat capability).",
    "GET /api/voice/sessions/{session_id}/events": "Server-sent voice tool events for the browser UI.",
    "POST /api/voice/prewarm": "Opens the OpenAI connection when the user reaches for the mic; no session, no user data.",
    "GET /api/swiggy/tools": "Diagnostic: live Swiggy MCP tool catalogue.",
    "GET /api/zepto/tools": "Diagnostic: live Zepto MCP tool catalogue.",
    "GET /api/zepto/capabilities": "Diagnostic: Zepto tool-policy summary.",
    # Local dev inspector (docs/DEV_TOOLS.md): 404 unless DEV_TOOLS_ENABLED, loopback, not private.
    "GET /api/dev/status": "Dev tools (local only): inspector availability.",
    "GET /api/dev/traces": "Dev tools (local only): recent DEV_TRACE turn records.",
    "GET /api/dev/state": "Dev tools (local only): the signed-in user's baskets, memory, watches, notification log.",
    "POST /api/dev/basket/clear": "Dev tools (local only): empty the owner's own unlocked basket.",
    "POST /api/dev/doctor": "Dev tools (local only): read-only doctor, no store calls.",
    "POST /api/dev/reconcile-dry-run": "Dev tools (local only): read-only Zepto history check, budgeted, refused under cooldown.",
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
        # Bill/order failure status (codex/voice-reliability).
        "bill_failed",
        "item_summary",
        "bill_breakdown",
        "fees_total",
        # summarize_my_memory and search_products household-fact fields.
        "more_on_screen",
        "nothing_saved",
        "ingredient_check",
        "usual_quantity",
        # express_order / quick_add / clear_basket result fields and statuses.
        "also_in_basket",
        "items_removed",
        "named_brand_and_size",
        "needs_choice",
        "picked_because",
        "several_matches",
        # read_attached_photo item fields and photo kinds.
        "printed_name",
        "search_query",
        "product_photo",
        "alternative_queries",
        "identified_from",
        # search_products photo visual-match fields and statuses.
        "visual_match",
        "photo_match",
        "same_product",
        "no_exact_match",
        # Order status tool fields and statuses (codex/order-tracking-status).
        "order_ref",
        "order_status",
        "order_status_label",
        "out_for_delivery",
        "live_tracking",
        "eta_minutes",
        "last_updated_minutes_ago",
        "no_order",
        # Store-reported order times (codex/tracking-times).
        "eta_passed",
        "eta_time_india",
        "delivered_time_india",
        "took_minutes",
        # Unknown-outcome reconciliation status (codex/order-confirm-reconcile).
        "not_confirmed",
        "cancel_supported",
        "cancel_here",
        "option_id",
        "item_id",
        "store_id",
        "not_connected",
        "needs_store",
        "no_memory",
        "purchase_history",
        "confirmed_preference",
        # Memory write tool fields and statuses (codex/memory-write-manage).
        "already_saved",
        "blocked_by_household_rule",
        "confirm_token",
        "memory_off",
        "memory_ref",
        "memory_suggestion",
        "needs_confirmation",
        "not_allowed",
        "not_saved_no_habit",
        "not_saved_one_off",
        "skipped_stores",
        "user_words",
        # Search statuses for a store that could not be reached (not "not found").
        "provider_error",
        "unreachable_stores",
        "address_invalid",
        # Household-rule annotations and the delivery ETA field (search options, bill).
        "avoid_store",
        "avoided_because",
        "delivery_eta",
        # Fields, statuses and enum values shown in the backend few-shot examples.
        "swiggy_instamart",
        "price_paise",
        "line_total_paise",
        "item_total_paise",
        "payable_paise",
        "amount_paise",
        "pack_size",
        "line_total",
        "item_total",
        "awaiting_user_approval",
        "form_opened",
        "prefilled_fields",
        "form_visible",
        "filled_fields",
        "kept_user_fields",
        "bill_cancelled",
        "address_id",
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
        "payment_method",
        "payment_methods",
        "payment_method_label",
        "payment_method_unavailable",
        "upi_reserve",
        # read_attached_photo result statuses and fields (codex/photo-to-basket).
        "no_items",
        "no_photo",
        "shopping_list",
    }
)


API_CONTRACTS: dict[str, tuple[str, ...]] = {
    "GET /api/memory": (
        "Auth: Clerk bearer token; only the verified person's memories. Query: status=proposed|confirmed|superseded|rejected (optional), include_deleted=false.",
        "Response 200: MemoryResponse[] newest first. Settings shows status confirmed (active) and proposed (pending suggestions).",
        "MemoryResponse adds derived read-only fields: origin (explicit|stated|inferred), learned_at (ISO; confirmed_at or created_at), "
        "kind (prefer_brand|prefer_pack_size|prefer_store|avoid_store|avoid_product|avoid_brand|rule|null), group (pasand|kabhi_nahi|niyam|null), item, display_value, user_words.",
        '{"id": "uuid", "memory_type": "brand_preference", "value": {"kind": "prefer_brand", "product_category": "milk", "brand": "Amul Taaza", "origin": "stated", "user_words": "hum hamesha Amul Taaza lete hain"}, '
        '"text": "milk: hamesha Amul Taaza", "source_type": "explicit_statement", "status": "confirmed", "origin": "stated", "learned_at": "2026-11-22T10:00:00Z", "supersedes_memory_id": null, "...": "other fields"}',
        "origin stated = learned in passing without a read-back (undo-able for 24 h); inferred = a repeated-choice suggestion (status proposed until confirmed); explicit = everything else.",
        "Store values are store ids (swiggy_instamart|zepto); rule values are prefer_cheapest, prefer_fastest_delivery, prefer_larger_packs, prefer_smaller_packs, ask_before_substitute.",
    ),
    "POST /api/memory/{memory_id}/confirm": (
        "Auth: Clerk bearer token. Pending (proposed) -> confirmed. If the proposal has supersedes_memory_id, that confirmed memory becomes superseded.",
        "Response 200: MemoryResponse. 404 not this person's; 409 not proposed.",
    ),
    "POST /api/memory/{memory_id}/reject": (
        'Auth: Clerk bearer token. Request: {"reason": null | "text <= 500"}. Pending -> rejected; a rejected suggestion is never re-proposed from repeated choices.',
        "Response 200: MemoryResponse. 409 not proposed.",
    ),
    "POST /api/memory/{memory_id}/correct": (
        "Auth: Clerk bearer token. Settings inline edit. Request: MemoryProposal fields + reason, normally source_type correction with the full edited value object.",
        "Creates the replacement (confirmed; supersedes_memory_id = old id; value.origin stated becomes explicit); the old one becomes superseded and is kept for audit.",
        "Response 201: MemoryResponse. 409 the memory is not confirmed; 422 the text or a value falls under the sensitive-data policy.",
    ),
    "POST /api/memory/{memory_id}/undo": (
        "Auth: Clerk bearer token. Reverts a memory learned in passing (origin stated, status confirmed, learned_at within 24 h): it is deleted, and the memory it superseded, if any, is restored to confirmed.",
        "Response 200: the restored MemoryResponse, or null. 404 memory not found for this person; 409 outside the undo window or not undo-able.",
    ),
    "GET /api/memory/summary": (
        "Auth: Clerk bearer token. Read-only. Response 200: {\"consent\": {\"saved_preferences\": \"on\"|\"off\", "
        "\"purchase_history\": \"on\"|\"off\"}, \"groups\": [{\"id\": \"home\"|\"food\"|\"usual_buys\"|\"avoid\"|"
        "\"shopping_style\"|\"from_orders\", \"title\", \"items\": [{\"id\", \"kind\", \"label\", \"source\": "
        "\"told\"|\"in_passing\"|\"repeated_choice\"|\"order_history\", \"source_label\", \"updated_at\", "
        "\"managed_by\": \"memory\"|\"profile\"|\"order\"}]}], \"suggestions\": [...], \"total\": n}.",
        "memory items are edited/deleted through /api/memory/{id}; profile items through PUT /api/profile; "
        "order items are read-only (DELETE /api/memory/purchase-history removes all).",
    ),
    "DELETE /api/memory/{memory_id}": (
        "Auth: Clerk bearer token. Forget one memory (status deleted; excluded from every list and from search). Response 204; idempotent.",
    ),
    "POST /api/memory/forget-all/token": (
        "Auth: Clerk bearer token. Step 1 of 'Sab bhool jao': issues a 5-minute, single-use token bound to this person. Nothing is deleted.",
        '{"confirm_token": "1764000000.abc...", "count": 4, "expires_at": "2026-11-22T10:05:00Z"}',
    ),
    "POST /api/memory/forget-all": (
        'Auth: Clerk bearer token. Step 2, after the user confirms. Request: {"confirm_token": "..."}. Deletes every memory and pending suggestion of this person; permissions are kept. '
        "Purchase history has its own DELETE /api/memory/purchase-history.",
        "Response 204. 400 invalid, expired, already used, or another person's token.",
    ),
    "POST /api/chat/turn": (
        "Auth: Clerk bearer token and accepted current terms (403 legal_consent_required otherwise).",
        'Request: {"conversation_id": "<uuid>" | null, "message": "AXE Dark sabse chhota add karo", '
        '"swiggy_address_id": "<id>" | null, "zepto_address_id": "<id>" | null, '
        '"ui_acks": [{"ack_id": "<from address_form.open>", "form_visible": true, "reason": null, '
        '"filled_fields": ["building_name"], "kept_user_fields": []}]}',
        "conversation_id null means the current conversation; another user's id is 404. "
        "message: 1-2000 characters (may be empty when attachment_id is set); unknown keys are 422. "
        'attachment_id: "ph_..." from POST /api/chat/attachments; the photo is read (and deleted) before the model runs.',
        'Response 200: {"conversation_id": "<uuid>" | null, "reply": "Roman-script Hinglish text", '
        '"ui_events": [{"type": "tool.results" | "basket.updated" | "basket.approval_requested" | '
        '"order.confirmation_requested" | "photo.read" | "address_form.open" | "address_form.unavailable" | "address.selected" | "tool.not_connected" | "tool.error" | '
        '"memory.changed", ...}], "incomplete": false}',
        "ui_events use the voice sideband shapes. Caps per turn: 6 tool rounds, 2 searches. "
        "Errors: 503 no OpenAI key, 502 model failure, 504 turn timeout (CHAT_TURN_TIMEOUT_SECONDS). "
        "A model failure or timeout after a tool saved a basket change is 200 with incomplete: true, the "
        "saved change's ui_events and a short fixed reply in the profile language; tools are never re-run.",
    ),
    "POST /api/chat/attachments": (
        "Auth: Clerk bearer token and accepted current terms. multipart/form-data with one `file` field.",
        "Accepted by magic bytes: JPEG, PNG, WebP, HEIC/HEIF; at most 8 MB (413), other types 415, unreadable 422.",
        "The image is decoded, EXIF orientation applied, every metadata block (EXIF/GPS/XMP/ICC) dropped, "
        "downscaled to 1600 px and re-encoded as JPEG; kept in memory for this user only, at most 15 minutes, "
        "and deleted as soon as its items are read. Never written to disk or the database.",
        'Response 201: {"attachment_id": "ph_...", "expires_in_seconds": 900, "width": 1200, "height": 1600, '
        '"thumbnail": "data:image/jpeg;base64,..." | null}',
        "thumbnail: a new <=320 px, <=40 KB JPEG made from the stripped pixels (no metadata). The browser "
        "saves it on the user's chat message as photo_thumbnail; PUT /api/conversations/current re-checks "
        "it (JPEG, size, dimensions, no metadata), drops invalid ones and keeps the newest 20 per chat.",
        "Send it with POST /api/chat/turn as attachment_id (message may then be empty).",
    ),
    "DELETE /api/chat/attachments/{attachment_id}": (
        "Auth: Clerk bearer token and accepted current terms. Removes a pending photo of this user. "
        "Response 204; idempotent, and another user's id is a no-op.",
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
        beta = " **Beta**" if capability.beta else ""
        lines.append(f"| {capability.title} (`{capability.id}`){beta} | " + " | ".join(cells) + f" | {tools} |")
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
