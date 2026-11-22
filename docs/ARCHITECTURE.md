# GharSaathi architecture

How a request flows through the system, and every decision taken before the
assistant answers. Status markers: **live** = merged in `main`;
**in progress** = being built on the named branch.

## Components

```mermaid
flowchart LR
  subgraph Browser["Browser — Next.js (localhost:3003)"]
    UI["Chat, basket, settings, onboarding<br/>Clerk sign-in → bearer token"]
    VP["Voice panel<br/>mic + speaker over WebRTC"]
  end

  subgraph OpenAI["OpenAI"]
    LIVE["GPT-Live front model<br/>gpt-live-1.1, voice nira<br/>listens + speaks"]
    BRAIN["Backend model<br/>gpt-6-luna via Responses<br/>decides tools"]
  end

  subgraph Backend["FastAPI backend (127.0.0.1:8000)"]
    API["REST API<br/>every route needs sign-in<br/>(except OAuth + health)"]
    SB["Voice sideband<br/>runs tool calls,<br/>posts results, continues"]
    TOOLS["Voice tools<br/>search · basket · bill ·<br/>order card · saved addresses ·<br/>address form · memory"]
    CORE["Shopping core<br/>per-store baskets, deterministic bill maths,<br/>approval bound to exact basket revision"]
    MEM["Memory<br/>confirmed preferences +<br/>consented purchase history + profile"]
    ADP["Provider adapters<br/>schema-validated MCP calls,<br/>Swiggy 429 cooldown"]
  end

  DB[("PostgreSQL<br/>conversations, baskets, approvals,<br/>memory, profile, consent")]
  SW["Swiggy Instamart MCP"]
  ZP["Zepto MCP"]

  UI -- "HTTPS + bearer" --> API
  VP -- "audio (WebRTC)" --> LIVE
  LIVE -- "hand-off (delegation)" --> BRAIN
  BRAIN -- "function_call" --> SB
  SB --> TOOLS
  TOOLS --> CORE
  TOOLS --> MEM
  TOOLS --> ADP
  API --> CORE
  API --> MEM
  CORE --> DB
  MEM --> DB
  ADP --> SW
  ADP --> ZP
  SB -- "tool + basket events (SSE, per-session token)" --> VP
```

## One voice turn: every decision before she answers

```mermaid
flowchart TD
  U(["User speaks"]) --> T["GPT-Live transcribes<br/>(may mishear: 'Bed cook' for 'Diet Coke')"]
  T --> D1{"D1 · Answer directly or hand off?<br/>(model, LIVE_INSTRUCTIONS)"}
  D1 -- "greeting, capability, repeat-back,<br/>Instamart order → not supported" --> SPEAK
  D1 -- "shopping need" --> B["Backend model receives the request<br/>+ BACKEND_INSTRUCTIONS<br/>+ session profile (name, address form)<br/>+ household memory summary ⟨in progress⟩"]

  B --> D2{"D2 · Is the request clear enough?<br/>(model)"}
  D2 -- "nonsense word / likely mishearing" --> ASK1["Ask ONE short confirm<br/>closest grocery item, e.g. 'Diet Coke?'<br/>⟨in progress: codex/voice-recall-offers⟩"]
  D2 -- "brand only ('Amul order karo')" --> ASK2["Ask ONE question:<br/>which product + size"]
  D2 -- "category without brand/size<br/>('deo chahiye')" --> D3
  D2 -- "product + size/variant given" --> SEARCH

  D3{"D3 · Memory<br/>(model calls recall_household_preferences)"}
  D3 -- "match found" --> ASK3["Ask ONE confirm:<br/>'Pichli baar AXE Dark 150 ml liya tha,<br/>wahi chahiye?'"]
  D3 -- "no memory / no consent" --> ASK4["Ask ONE question:<br/>brand + size"]
  ASK3 -- "haan" --> SEARCH
  ASK4 -- "user answers" --> SEARCH

  SEARCH["search_products → each ready store<br/>(code checks below)"] --> ANN["Annotate options the household bought before<br/>⟨in progress: deterministic memory⟩"]
  ANN --> D4{"D4 · Pick or ask?<br/>(model)"}
  D4 -- "one clear match, or user said<br/>'Dark' / 'pehla wala' / 'sabse chhota'<br/>with one variant left" --> ADD["add_to_basket(option_id)"]
  D4 -- "same item on both stores +<br/>'seedha add karo'" --> ADD2["add cheaper available store,<br/>say which + why in one line"]
  D4 -- "variants still open" --> ASK5["Ask ONLY the missing variant<br/>(max one question per turn)"]
  D4 -- "remembered option present" --> ASK6["Offer the remembered size<br/>⟨in progress⟩"]

  ADD --> RES
  ADD2 --> RES
  RES["Tool results → backend model → short reply"] --> D5{"D5 · Another tool?<br/>(loop; cap 6 rounds, 4 searches)"}
  D5 -- "yes" --> SEARCH
  D5 -- "no" --> SPEAK

  ASK1 --> SPEAK
  ASK2 --> SPEAK
  ASK5 --> SPEAK
  ASK6 --> SPEAK
  SPEAK(["GPT-Live speaks Roman-script Hinglish<br/>feminine self, user's address form, prices in words"])
```

### Who decides what

| # | Decision | Decided by | Enforced in code? |
|---|---|---|---|
| D1 | Answer directly vs hand off to the backend model | GPT-Live (instructions) | No — and no answer can order: the only order path is the user's tap on the order card |
| D2 | Is the request clear? Ask for more context? | Backend model (instructions) | No — rules: at most one question, never re-ask what was said |
| D3 | Check memory first | Backend model calls `recall_household_preferences` | **Today: no** (model may skip it — the Diet Coke miss). **In progress:** memory summary at session start + memory-annotated search results, so remembered items are always in front of the model |
| D4 | Add vs ask which variant/store | Backend model (instructions + few-shot examples) | Partly — store ambiguity on bills returns `needs_store` from code |
| D5 | Call another tool or answer | Backend model | Hard cap in code: voice 6 rounds / 4 searches per turn (`sideband.py`); typed chat 6 rounds / 2 searches per turn (`app/chat/service.py`) |
| D6 | Remember a preference stated in passing (tier 1) | Backend model calls `remember_preference` / `update_preference` with `mode: "stated"` | **Yes:** code saves only with habit/rule words (`hamesha`, `kabhi nahi`, `sirf`, `mat … karna`) and refuses one-off words (`is baar`); a stated update must match a remembered item. Saved active at once; reply acknowledges with undo; `forget_preference` within 24 h restores what it replaced |
| D7 | Remember on request ("yaad rakhna…") | Backend model: `remember_preference` `mode: "explicit"` → read-back → `confirm_memory` | **Yes:** stays a pending proposal until `confirm_memory(confirm)`; forget-all needs a token issued in the same session |
| D8 | Suggest a preference from repeated choices (tier 2) | Code: basket adds are recorded; after a conversation save/archive `choice_signals.propose_from_choices` creates a pending proposal (same brand+pack in ≥3 conversations, or the same store every time both offered it) | **Yes:** never active without a yes; offered at most once per session (attached to a matching `add_to_basket` result); rejected/deleted suggestions never return |
| D9 | Apply saved rules to search | Code (`memory/household_rules.py` in `search_products`) | **Yes:** `avoid_store` skips that store's search (and an earlier option from it cannot be added), `avoid_product`/`avoid_brand` hide options unless the query asks for them (then `avoided: true`), `prefer_*` adds `preferred: true` (separate from `remembered`) |
| D10 | Prepare an order card | Backend model calls `prepare_order` on an explicit order request | **Yes, for the order itself:** the tool only shows the card; only the user's tap on **Place order** approves and orders, and code re-checks the bound confirmation first. Calling it at the wrong time can only show a card |

### Deterministic checks (code, every call)

| Check | Location | Prevents |
|---|---|---|
| Strict JSON schema on every tool argument | `backend/app/voice/*_tools.py`, `tools.py` | Malformed or extra arguments |
| Not-a-product guard | `backend/app/commerce/query_guard.py` | Searching chit-chat |
| Store connected + delivery address selected | `tools.py`, `basket_tools.py` | Search/add without a store (`not_connected`) |
| Opaque `option_id` → stored search result | search → basket | Invented products or prices |
| Which store for the bill | `basket_tools.py` | Guessing a store (`needs_store`) |
| Consent + household scoping | `backend/app/memory/recall.py` | Using history without consent or another user's data |
| Preference memory policy: structured kinds, length caps, sensitive categories (health, religion/caste, finances, gender/sexuality, age, family details) refused | `backend/app/memory/household.py`, `household_service.py` | Storing free-text instructions or sensitive traits, in every tier and from the Settings edit route |
| Preference consent (Settings switch) before any write or suggestion | `backend/app/memory/household_service.py` | Remembering when memory is off |
| Swiggy 429 cooldown | `backend/app/commerce/swiggy/client.py` | Hammering a rate-limited account |
| Bill arithmetic in paise | `backend/app/shopping/basket.py` | Model-generated maths |
| Approval bound to basket revision, conditional DB writes | `backend/app/shopping/approval.py`, `checkout.py` | Approving/ordering a basket the user didn't see |
| Accepted current terms for consequential actions | `backend/app/legal_consent.py` dependency | Acting before consent |
| No voice/typed tool can approve or place orders; `prepare_order` only shows the order card | `backend/app/voice/order_tools.py` | Spoken or typed "yes" becoming an order |
| Order card tap: confirmation belongs to this user, is open (single-use, not cancelled/replaced), unexpired (5 min, capped by the bill), and the basket revision, address, approval, approval fingerprint, quote digest and payable are unchanged; Zepto only; accepted terms | `backend/app/shopping/order_confirmation.py`, `POST /api/orders/confirm` | Ordering a stale, edited, replayed, cancelled or someone else's bill |
| Tap runs the existing approve + COD checkout: idempotency key `order-confirmation:<id>`, durable one-shot claim per approval, fresh re-quote must match, outcome-unknown never retried | `backend/app/shopping/checkout.py` | Double orders, price drift, blind retries |

## Placing an order (one tap)

```mermaid
sequenceDiagram
  participant U as User
  participant A as Assistant (voice or typed)
  participant B as Backend
  participant Z as Zepto
  U->>A: "order kar do"
  A->>B: prepare_order(store)
  B->>B: current bill? else request_approval path (quote)
  B->>B: bind confirmation (user, basket rev, approval, quote digest, payable, address; 5 min, single use)
  B-->>U: order card: items, total, Cash on delivery, Deliver to, Expires in, Place order · ₹…, Cancel
  A-->>U: "Zepto, do items, … rupaye, cash on delivery, Home par. Screen par 'Place order' dabaiye."
  U->>B: tap Place order → POST /api/orders/confirm
  B->>B: re-check every binding, consume the confirmation
  B->>B: approve (fingerprint) → COD checkout claim
  B->>Z: confirm COD (once)
  Z-->>B: order id
  B-->>U: card: "Order placed · #id"
  B-->>A: voice: session.commentary.append · typed: server note on the next turn
  A-->>U: "Order ho gaya" (only now)
```

Cancel (`POST /api/orders/cancel`) invalidates the confirmation at once. A newer card
for the same store replaces the older one. Confirmations live in backend memory; a
restart drops them and the user asks again. The order stays one-shot across processes
because the checkout claim is unique per approval in PostgreSQL. Swiggy Instamart has
no checkout, so `prepare_order` returns `unsupported` there.

## Delivery addresses (switch, add)

**Switch to a saved address.** "Address change karo" calls `list_delivery_addresses`
(saved addresses per connected store from the server-side snapshot, or the demo
fixture; no provider call), the model names them in one line, then
`select_delivery_address(store, address_id)`:

1. The id must be in that user's own snapshot for that store (`SavedAddressBook.select`,
   the same repository write as `PUT /api/{store}/addresses/selected`).
2. The store's basket gets the new address (`BasketService.set_address`), which
   supersedes any open bill; refused once checkout started.
3. The session's toolbox uses it for later searches, baskets, bills and order cards;
   options found for the old address can no longer be added.
4. `address.selected` updates the on-screen selection. The voice panel does not
   restart for a switch the assistant made; typed chat keeps its session.

**Add a new (Zepto) address.**

```mermaid
sequenceDiagram
  participant M as Backend model
  participant T as open_address_form
  participant S as Sideband
  participant B as Browser
  M->>T: prefill from what the user said
  T->>T: Zepto live + connected? (else address_form.unavailable)<br/>drop fields not in the user's own recent words,<br/>sample values, phones, coordinates; merge with earlier prefill
  T-->>S: form_requested + ui event (ack_id)
  S->>B: address_form.open (SSE)
  B->>B: open the Add delivery address popup,<br/>fill only empty, untouched fields, focus first empty
  B->>S: POST /api/voice/sessions/{id}/ui-ack {form_visible, filled_fields}
  S-->>M: form_opened (form_visible true) or form_not_shown (reason; 6 s timeout)
```

- The user's own words come from GPT-Live input transcripts seen by the sideband,
  typed messages, and the saved chat's user turns — never from the model.
- Typed chat cannot confirm within its own turn: the reply says the form is
  opening, and the browser sends `ui_acks` with the next turn as a developer note.
- The popup is `AddressFormSheet` on the shared `ui/input-dialog.tsx`
  (`InputDialog`: modal, focus trap, Escape/backdrop close with an unsaved-changes
  guard, bottom sheet under 640 px). Voice, typed chat, the setup sheet's button and
  onboarding all open the same popup.
- Contact number and coordinates are never prefilled; the model says the user must
  type the number.

## Context size per turn (prompt 1.8.0, o200k tokens)

What each model sees before the conversation itself. Measured with
`tiktoken` `o200k_base`; re-measure after prompt changes.

| Part | Front model (GPT-Live) | Backend model (gpt-6-luna) |
|---|---|---|
| Instructions | **2,643** (10,382 chars) | **2,958** (11,225 chars) |
| …of which few-shot examples | 589 | 1,327 (45%) |
| Session profile block (name, address form) | ~60 | ~60 |
| Tool definitions (7 tools) | — | **1,450** (`open_address_form` alone 468) |
| Household memory summary | — | ⟨in progress⟩ capped at ~8 items |
| One search result | — | 320 (2 options) – 750 (6 options) |
| **Fixed overhead before conversation** | **~2.7k** | **~4.5k**, plus ~0.3–0.75k per search round |

Largest backend sections: examples 1,327 · household memory 325 · basket 267 ·
task 256 · decisive selection 227 · return the result 214. Largest live sections:
examples 589 · decisive replies 499 · delegation 336 · language 312.

## Chat context at session start

Each voice session (and, via the same builder, each typed chat turn) starts with
the verified user's own saved chats, read server-side by
`ConversationContextLoader.load(user_id)` in `backend/app/conversations/context.py`.
Nothing is taken from the browser.

| Part | Source | Cap (o200k) |
|---|---|---|
| Current chat summary | `conversations.summary`, written in the background | 110 |
| Last 6 turns, verbatim | saved messages | 170 |
| On screen: results per item; each store's basket; its latest pending/approved bill (item total, payable, store-confirmed offers, expiry), however it was opened | saved `result_groups` + basket/approval tables (`shopping_approvals.quote`) | 140 |
| Previous chats (≤3, last 30 days): summary or first request, items searched, furthest basket step per store | archived conversations | 110 |

The whole block is ≤ ~600 tokens (measured in `tests/unit/test_conversation_context.py`).
GPT-Live gets the turns as `session.input` startup history
([docs](https://developers.openai.com/api/docs/guides/live-conversations#seed-a-session-with-prior-conversation))
and the rest in its instructions. The backend model gets the whole block in its
instructions. All of it is quoted as untrusted data. Prices, baskets, bills and
orders come only from saved state. Voice `view_basket` also reads the persisted
bill (`Approval.quote`) when the session did not request it, e.g. after the
on-screen "Get final bill" button; it never re-quotes. "Order confirmed" appears only when a chat's
saved timeline reached the `ordered` stage.

Rolling summaries (`backend/app/conversations/summarizer.py`) run off the request
path. They start after a save once 6 new turns have built up, and when a chat is
archived (New chat or opening another chat). The model is set by
`CONVERSATION_SUMMARY_MODEL` (default `gpt-6-luna`), with strict JSON output and a
200-token output cap. A summary records only what the user wanted, preferences
they stated, and open items; prices are stripped. Chats without messages are
never summarised. When there is no summary yet, the deterministic description is
used.

## Typed chat

Typed text goes through the same backend model as voice (branch
`codex/typed-chat-assistant`):

```mermaid
flowchart LR
  T(["User types"]) --> L{"Local intent?<br/>(conversation-intent.mjs:<br/>greeting, connection status)"}
  L -- yes --> R1["Local reply"]
  L -- no --> C["POST /api/chat/turn<br/>{conversation_id, message,<br/>selected address per store}"]
  C --> B["Backend model, same delegation config as voice:<br/>BACKEND_INSTRUCTIONS + session profile<br/>+ household memory summary + typed channel note,<br/>same tools, same VoiceToolbox"]
  B --> D5{"Tool loop<br/>cap 6 rounds · 2 searches · timeout"}
  D5 --> OUT["{reply, ui_events[]}"]
  OUT --> UI["Assistant bubble in the chat timeline + the event handler voice uses<br/>(assistant-events.mjs): per-item result groups, basket card,<br/>bill shown at once, address form"]
  C -. "error" .-> FB["Error bubble +<br/>'Seedha search karein' = one direct search,<br/>shown as its own result group"]
```

- **Same decisions.** D2–D5 above apply unchanged: the request uses
  `build_session_config(...)["delegation"]["responses"]` with the same profile and
  household memory summary a voice session gets, so model, instructions, tool
  definitions and `tool_choice` are the voice ones. There is no typed-only prompt.
  D1 does not exist for typed text (there is no front model).
- **Reply language.** The profile block states the saved `language` (`hinglish`
  default, `english`, `hindi`) as an explicit rule for voice and typed chat alike.
  Typed replies are shown as the model wrote them; the caption romaniser runs only on
  voice transcripts and stays off for `hindi` (`assistant_transcript_script`).
- **Channel note.** `app/chat/service.py` appends a short "Channel: typed" block to
  the instructions: prices may be written as "₹172" and lists may be short bullet
  lines. Voice sessions get no such block, so spoken replies keep prices in words.
- **Context per turn.** The same `ConversationContextLoader` block a voice session
  gets (see "Chat context at session start"): current chat summary, last 6 turns,
  on-screen state and previous chats, in the instructions. The message being answered
  is the only `user` input; if the browser already saved it, the trailing copy is
  dropped from the quoted turns. For a chat that is not the user's current one, only
  baskets/bills and previous chats are kept. The last 2 search results of this chat
  are added as a developer message, so "Dark" or "haan wahi" can add an `option_id`
  the user already saw.
- **Per-conversation state.** `backend/app/chat/service.py` keeps one `VoiceToolbox`
  per (verified user, conversation) in memory (200 sessions, 1 h idle), with turns
  serialised by a lock. A different selected address rebuilds it. After a backend
  restart the earlier `option_id`s are gone and the model searches again.
- **Hard caps (code).** 6 tool rounds, then one forced text answer
  (`tool_choice: "none"`); a third `search_products` in one turn is refused without
  running; a tool name outside the definitions is refused; per-turn timeout
  `CHAT_TURN_TIMEOUT_SECONDS` (default 60 s).
- **Checks.** Clerk identity and accepted current terms on the route (like
  `POST /api/voice/session`). Every deterministic check in the table above runs,
  because the tools are the same. Logs carry the outcome, rounds, tool names and
  latency only, never message text.
- **Persistence.** The browser appends the typed message and the reply to the open
  conversation and saves it with `PUT /api/conversations/current`, exactly like
  voice transcripts. Saves are debounced 250 ms; the page flushes a pending save
  before `POST /api/chat/turn` and before `POST /api/voice/session`, so the
  server-side context includes the latest turn.

## Where to look

- Feature × surface matrix: [`CAPABILITIES.md`](CAPABILITIES.md)
- Test layers and how to run them: [`TESTING.md`](TESTING.md)
- Opt-in real-provider smoke tests: [`LIVE_SMOKE.md`](LIVE_SMOKE.md)
- Voice turn-taking modes: [`voice-turn-taking.md`](voice-turn-taking.md)
