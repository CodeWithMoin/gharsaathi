# Capability matrix

What a user can do in GharSaathi, and from which surface. This is the single
source of truth for "can voice do X?". The table below is **generated** from
`backend/app/capabilities.py`; `backend/tests/test_capabilities.py` fails when it
drifts from the code.

Surfaces:

- **Voice** — a tool the Realtime voice session can call (`backend/app/voice/`).
- **Typed chat** — what happens when the user types in the composer. Today typed
  text never reaches the assistant: `frontend/app/page.tsx` `runSearch` answers a
  few local intents (`conversation-intent.mjs`) and otherwise calls
  `/api/swiggy/search/personalized` and `/api/zepto/search` directly.
- **On-screen UI** — a button, form or panel in `frontend/app/`.
- **Backend** — FastAPI routes in `backend/app/api/`.

Statuses: ✅ works · 🟡 partial (see note) · 🚧 in progress (branch named) ·
❌ missing · ⛔ intentionally unsupported (reason given).

## Updating this matrix

When you add, wire up or remove a feature:

1. Edit `CAPABILITIES` (or `INFRASTRUCTURE_ROUTES`) in `backend/app/capabilities.py`:
   status per surface, the voice tool names, and the `"METHOD /path"` routes.
2. From `backend/`, regenerate this file:
   `uv run --with-requirements requirements.txt python -m app.capabilities --write`
3. Run `uv run --with-requirements requirements.txt pytest tests/test_capabilities.py -q`.

The test fails when a mounted route or a voice tool is missing from the registry,
the registry names a route/tool that does not exist, a voice prompt mentions an
undefined tool name, or a capability claims voice works without a session tool.
Prompt wording, typed-chat and UI statuses are verified by review, not by the test.

## Matrix

<!-- BEGIN GENERATED: python -m app.capabilities --write -->

| Capability | Voice | Typed chat | On-screen UI | Backend | Voice tools |
|---|---|---|---|---|---|
| Connect / disconnect a store account (Swiggy, Zepto) (`connect_store`) | **⛔ unsupported** OAuth must run on the provider's page; never collect credentials in conversation. | **🟡 partial** Answers 'is Swiggy/Zepto connected?' locally; cannot connect. | **✅ works** Store setup sheet and account settings. | **✅ works** OAuth 2.1 + PKCE with callback readiness check. | — |
| List and select a saved delivery address (`select_address`) | **❌ missing** Voice uses whichever address is selected on screen when the session starts; basket tools report it (label + short line from the saved snapshot). No selection tool. | **❌ missing** Typed text never reaches the assistant: page.tsx runSearch only answers a few local intents (conversation-intent.mjs) or calls the search endpoints. | **✅ works** Radio list per store in the setup sheet. | **✅ works** | — |
| Add a new delivery address (`add_address`) | **✅ works** Zepto, live mode: opens the address form prefilled with what the user said; the user checks, sets location and saves on screen. Swiggy is refused (swiggy_unsupported). Known gap: a second voice prefill remounts the form (new key in page.tsx) and overwrites fields the user is editing. | **❌ missing** Typed text never reaches the assistant: page.tsx runSearch only answers a few local intents (conversation-intent.mjs) or calls the search endpoints. | **🟡 partial** Zepto only, live mode only (ZeptoAddressForm). | **🟡 partial** Zepto only; Swiggy add-address is unsupported (no route). | `open_address_form` |
| Search and compare products across connected stores (`product_search`) | **✅ works** Searches every store with a selected address (Swiggy Instamart + Zepto). | **🟡 partial** Searches both stores directly via the search endpoints, bypassing the assistant (no clarification, no Hinglish rewriting beyond local intents). | **✅ works** ProductComparison cards grouped across stores. | **✅ works** | `search_products` |
| Ask for several items in one message ('atta, dal aur doodh') (`multi_item_request`) | **✅ works** Backend prompt tells the model to search each item separately. | **❌ missing** The whole typed string is sent as a single search query. | **❌ missing** One search box, one query. | **✅ works** Via the voice model's parallel search_products calls. | `search_products` |
| Add a product to the basket (`add_to_basket`) | **✅ works** Swiggy and Zepto: each option_id adds to its own store's basket. | **❌ missing** Typed text never reaches the assistant: page.tsx runSearch only answers a few local intents (conversation-intent.mjs) or calls the search endpoints. | **✅ works** Add button on Swiggy and Zepto result cards. | **✅ works** Provider-aware local basket. | `add_to_basket` |
| Change quantity or remove a basket item (`edit_basket`) | **✅ works** Swiggy and Zepto (store parameter; asks when ambiguous). | **❌ missing** Typed text never reaches the assistant: page.tsx runSearch only answers a few local intents (conversation-intent.mjs) or calls the search endpoints. | **✅ works** | **✅ works** | `set_basket_quantity` |
| Hear or see the basket and item total (`view_basket`) | **✅ works** One basket per store; store null lists every store. | **❌ missing** Typed text never reaches the assistant: page.tsx runSearch only answers a few local intents (conversation-intent.mjs) or calls the search endpoints. | **✅ works** | **✅ works** | `view_basket` |
| Request the exact provider bill for review (`request_bill`) | **✅ works** Swiggy or Zepto (store parameter); see voice_bill_handoff for the screen gap. | **❌ missing** Typed text never reaches the assistant: page.tsx runSearch only answers a few local intents (conversation-intent.mjs) or calls the search endpoints. | **✅ works** | **✅ works** Deterministic fee/charge arithmetic from the provider quote. | `request_approval` |
| See on screen the bill that voice requested (`voice_bill_handoff`) | **✅ works** request_approval emits basket.approval_requested with the approval and quote. | **❌ missing** Typed text never reaches the assistant: page.tsx runSearch only answers a few local intents (conversation-intent.mjs) or calls the search endpoints. | **❌ missing** Known gap: voice-panel.tsx passes only the provider to onBasketTouched, and page.tsx never sets BasketPanel's incomingRequest, so the basket opens without the requested bill and the user must request it again on screen. | **✅ works** Same approval as POST /api/basket/approval. | `request_approval` |
| Approve or reject the exact bill (`approve_bill`) | **⛔ unsupported** Approval is bound to the on-screen bill; a spoken 'yes' is not approval. | **⛔ unsupported** Same as voice: approval happens only on the bill review. | **✅ works** | **✅ works** Fingerprint-bound approval. | — |
| Place a Zepto cash-on-delivery order (`order_zepto_cod`) | **⛔ unsupported** The final order action is on-screen only, after bill approval. | **⛔ unsupported** Final order action is on-screen only. | **✅ works** Live mode only, after approval (zepto-checkout-state.mjs). | **✅ works** Idempotency-key, approval- and fingerprint-bound. | — |
| Place a Swiggy Instamart order (`order_swiggy`) | **⛔ unsupported** The final order action is on-screen only. | **⛔ unsupported** Final order action is on-screen only. | **❌ missing** A Swiggy bill can be approved but there is no order action. | **❌ missing** No Swiggy checkout; only Zepto COD exists (app/shopping/checkout.py). | — |
| Free-delivery / offer threshold suggestions (`offers`) | **❌ missing** No voice tool. | **❌ missing** Typed text never reaches the assistant: page.tsx runSearch only answers a few local intents (conversation-intent.mjs) or calls the search endpoints. | **🟡 partial** Zepto basket only (BasketPanel), using consented usual items. | **🟡 partial** Zepto only. | — |
| Suggest the household's usual items from purchase history (`usual_items`) | **❌ missing** No voice tool. | **❌ missing** Typed text never reaches the assistant: page.tsx runSearch only answers a few local intents (conversation-intent.mjs) or calls the search endpoints. | **🟡 partial** Listed inside Zepto offer suggestions; dismiss/review routes have no frontend caller. | **✅ works** | — |
| Import purchase history and personalise search (consented) (`purchase_history_personalization`) | **❌ missing** Permissions and import are on-screen only. | **❌ missing** Typed text never reaches the assistant: page.tsx runSearch only answers a few local intents (conversation-intent.mjs) or calls the search endpoints. | **✅ works** PersonalizationOnboarding in the setup sheet. | **✅ works** | — |
| Remember explicit household preferences (brand, size, corrections) (`preference_memory`) | **❌ missing** No voice tool. | **❌ missing** Typed text never reaches the assistant: page.tsx runSearch only answers a few local intents (conversation-intent.mjs) or calls the search endpoints. | **❌ missing** No frontend file calls these memory routes. | **✅ works** Extract/propose, confirm/correct/reject, retrieve, delete. | — |
| See past orders (`order_history`) | **❌ missing** No voice tool. | **❌ missing** Typed text never reaches the assistant: page.tsx runSearch only answers a few local intents (conversation-intent.mjs) or calls the search endpoints. | **❌ missing** No frontend caller. | **🟡 partial** Zepto only. app/api/swiggy_orders.py exists but is not mounted in app.main, so /api/swiggy/orders does not exist. | — |
| Track a delivery (`order_tracking`) | **❌ missing** No voice tool. | **❌ missing** Typed text never reaches the assistant: page.tsx runSearch only answers a few local intents (conversation-intent.mjs) or calls the search endpoints. | **✅ works** LiveOrderTracking panel (also reachable from support). | **✅ works** Read-only provider status polling. | — |
| Prepare a support complaint with evidence (`support_complaint`) | **❌ missing** Support agent tools exist (app/support/agent_tools.py, /api/support/agent/*) but are not wired into the voice session. | **❌ missing** Typed text never reaches the assistant: page.tsx runSearch only answers a few local intents (conversation-intent.mjs) or calls the search endpoints. | **✅ works** SupportPanel: create, attach evidence, prepare, approve. | **✅ works** | — |
| Submit a complaint to the provider (`support_submit`) | **⛔ unsupported** No officially supported provider support channel. | **⛔ unsupported** No officially supported provider support channel. | **⛔ unsupported** No officially supported provider support channel. | **⛔ unsupported** Route exists but always refuses (SupportChannelUnavailable). | — |
| Reopen, search or start a new conversation (`conversation_history`) | **⛔ unsupported** Navigating history is an on-screen task; voice turns join the open conversation. | **⛔ unsupported** Navigating history is an on-screen task. | **✅ works** Recent-chat drawer. | **✅ works** User-scoped, server-backed history. | — |
| Accept terms and privacy consent (`legal_consent`) | **⛔ unsupported** Consent must be an explicit on-screen action. | **⛔ unsupported** Consent must be an explicit on-screen action. | **✅ works** FirstRunOnboarding. | **✅ works** Durable consent record. | — |
| Voice turn-taking and barge-in (interrupting the assistant) (`voice_turn_taking`) | **🚧 in progress** GPT-Live has no turn_detection/VAD/commit controls; VOICE_TURN_TAKING selects native (default, unchanged), tuned (option A: interruption + Hinglish patience policy appended to the Live instructions) or smart_turn (option B: tuned + browser Hinglish end-of-turn model that holds playback mid-thought and stops it on barge-in). Measured in docs/voice-turn-taking.md; not yet verified in a real browser session. Branch: `codex/smart-turn-options`. | **⛔ unsupported** Not applicable to typed chat. | **🚧 in progress** voice-panel.tsx starts SmartTurnController (smart-turn.ts) only when the session reports smart_turn; the model is lazy-loaded, SHA-256 verified, and falls back to option A. Branch: `codex/smart-turn-options`. | **🚧 in progress** Setting voice_turn_taking; voice/turn_taking.py builds the instructions and the session response's turn_taking field. Branch: `codex/smart-turn-options`. | — |
| Show the live voice conversation as chat bubbles (`voice_transcript`) | **🟡 partial** Transcripts stream into the chat and are saved with the conversation. | **⛔ unsupported** Not applicable: typed messages are added directly. | **🟡 partial** Known gap: voice-panel.tsx keeps one shared activeTranscriptRef for both roles, so when user and assistant speech overlap (events without a source id) bubbles fragment into several pieces. | **✅ works** Persisted through PUT /api/conversations/current. | — |
| Read the Zepto provider-side cart (`provider_cart_view`) | **❌ missing** No voice tool. | **❌ missing** Typed text never reaches the assistant: page.tsx runSearch only answers a few local intents (conversation-intent.mjs) or calls the search endpoints. | **❌ missing** No frontend caller. | **✅ works** Read-only. | — |

### Backend routes by capability

- `connect_store`: `GET /api/swiggy/connection`, `GET /api/swiggy/oauth/readiness`, `GET /api/swiggy/oauth/start`, `GET /api/swiggy/oauth/callback`, `POST /api/swiggy/disconnect`, `GET /api/zepto/connection`, `GET /api/zepto/oauth/readiness`, `GET /api/zepto/oauth/start`, `GET /api/zepto/oauth/callback`, `POST /api/zepto/disconnect`
- `select_address`: `GET /api/swiggy/addresses`, `PUT /api/swiggy/addresses/selected`, `GET /api/zepto/addresses`, `PUT /api/zepto/addresses/selected`
- `add_address`: `POST /api/zepto/addresses`
- `product_search`: `POST /api/swiggy/search`, `POST /api/swiggy/search/personalized`, `POST /api/zepto/search`
- `add_to_basket`: `POST /api/basket/lines`
- `edit_basket`: `PATCH /api/basket/lines/{variant_id}`, `DELETE /api/basket/lines/{variant_id}`
- `view_basket`: `GET /api/basket`
- `request_bill`: `POST /api/basket/address`, `POST /api/basket/approval`
- `approve_bill`: `POST /api/basket/approval/{approval_id}/approve`, `POST /api/basket/approval/{approval_id}/reject`
- `order_zepto_cod`: `POST /api/zepto/checkout/cod`
- `offers`: `POST /api/zepto/offer-suggestions`
- `usual_items`: `GET /api/memory/usual-suggestions`, `POST /api/memory/usual-suggestions/{product_id}/dismiss`, `POST /api/memory/usual-suggestions/{product_id}/review`
- `purchase_history_personalization`: `GET /api/memory/permissions`, `PUT /api/memory/permissions/{data_category}/{purpose}`, `POST /api/memory/purchase-history/sync`, `DELETE /api/memory/purchase-history`
- `preference_memory`: `GET /api/memory`, `POST /api/memory/extract`, `POST /api/memory/proposals`, `POST /api/memory/retrieve`, `POST /api/memory/{memory_id}/confirm`, `POST /api/memory/{memory_id}/correct`, `POST /api/memory/{memory_id}/reject`, `DELETE /api/memory/{memory_id}`
- `order_history`: `GET /api/zepto/orders`, `GET /api/zepto/orders/{order_id}`
- `order_tracking`: `POST /api/tracking/monitors`, `GET /api/tracking/monitors`, `GET /api/tracking/monitors/{monitor_id}`, `POST /api/tracking/monitors/{monitor_id}/refresh`, `DELETE /api/tracking/monitors/{monitor_id}`
- `support_complaint`: `POST /api/support/complaints`, `GET /api/support/complaints/{complaint_id}`, `POST /api/support/complaints/{complaint_id}/evidence`, `POST /api/support/complaints/{complaint_id}/prepare`, `POST /api/support/complaints/{complaint_id}/approve`, `GET /api/support/agent/tools`, `POST /api/support/agent/execute`
- `support_submit`: `POST /api/support/complaints/{complaint_id}/submit`
- `conversation_history`: `GET /api/conversations`, `GET /api/conversations/current`, `PUT /api/conversations/current`, `POST /api/conversations/new`, `POST /api/conversations/{conversation_id}/open`
- `legal_consent`: `GET /api/legal-consent`, `POST /api/legal-consent`
- `provider_cart_view`: `GET /api/zepto/cart`

### Infrastructure routes

- `GET /health`: Liveness check.
- `POST /api/voice/session`: Starts a Realtime voice session (carries every voice capability).
- `GET /api/voice/sessions/{session_id}/events`: Server-sent voice tool events for the browser UI.
- `GET /api/swiggy/tools`: Diagnostic: live Swiggy MCP tool catalogue.
- `GET /api/zepto/tools`: Diagnostic: live Zepto MCP tool catalogue.
- `GET /api/zepto/capabilities`: Diagnostic: Zepto tool-policy summary.

### Routers defined but not mounted

- `app.api.swiggy_orders`: Swiggy order history router exists but is not included in app.main.

<!-- END GENERATED -->
