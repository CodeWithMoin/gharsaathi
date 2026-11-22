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
    TOOLS["Voice tools<br/>search · basket · bill ·<br/>address form · memory recall"]
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
  D1 -- "greeting, capability, repeat-back,<br/>order/approve request → refuse" --> SPEAK
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
  RES["Tool results → backend model → short reply"] --> D5{"D5 · Another tool?<br/>(loop; cap 6 rounds, 2 searches<br/>⟨in progress⟩)"}
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
| D1 | Answer directly vs hand off to the backend model | GPT-Live (instructions) | No — but voice has no order/approve tool, so it can't act wrongly |
| D2 | Is the request clear? Ask for more context? | Backend model (instructions) | No — rules: at most one question, never re-ask what was said |
| D3 | Check memory first | Backend model calls `recall_household_preferences` | **Today: no** (model may skip it — the Diet Coke miss). **In progress:** memory summary at session start + memory-annotated search results, so remembered items are always in front of the model |
| D4 | Add vs ask which variant/store | Backend model (instructions + few-shot examples) | Partly — store ambiguity on bills returns `needs_store` from code |
| D5 | Call another tool or answer | Backend model | Typed chat: yes, hard cap of 6 rounds / 2 searches per turn (`app/chat/service.py`). Voice: **in progress** |

### Deterministic checks (code, every call)

| Check | Location | Prevents |
|---|---|---|
| Strict JSON schema on every tool argument | `backend/app/voice/*_tools.py`, `tools.py` | Malformed or extra arguments |
| Not-a-product guard | `backend/app/commerce/query_guard.py` | Searching chit-chat |
| Store connected + delivery address selected | `tools.py`, `basket_tools.py` | Search/add without a store (`not_connected`) |
| Opaque `option_id` → stored search result | search → basket | Invented products or prices |
| Which store for the bill | `basket_tools.py` | Guessing a store (`needs_store`) |
| Consent + household scoping | `backend/app/memory/recall.py` | Using history without consent or another user's data |
| Swiggy 429 cooldown | `backend/app/commerce/swiggy/client.py` | Hammering a rate-limited account |
| Bill arithmetic in paise | `backend/app/shopping/basket.py` | Model-generated maths |
| Approval bound to basket revision, conditional DB writes | `backend/app/shopping/approval.py`, `checkout.py` | Approving/ordering a basket the user didn't see |
| Accepted current terms for consequential actions | `backend/app/legal_consent.py` dependency | Acting before consent |
| No voice tool can approve or place orders | tool list | Spoken "yes" becoming an order |

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

## Typed chat

Typed text goes through the same backend model as voice (branch
`codex/typed-chat-assistant`):

```mermaid
flowchart LR
  T(["User types"]) --> L{"Local intent?<br/>(conversation-intent.mjs:<br/>greeting, connection status)"}
  L -- yes --> R1["Local reply"]
  L -- no --> C["POST /api/chat/turn<br/>{conversation_id, message,<br/>selected address per store}"]
  C --> B["Backend model, same delegation config as voice:<br/>BACKEND_INSTRUCTIONS + session profile,<br/>same 7 tools, same VoiceToolbox"]
  B --> D5{"Tool loop<br/>cap 6 rounds · 2 searches · timeout"}
  D5 --> OUT["{reply, ui_events[]}"]
  OUT --> UI["Assistant bubble + the event handler voice uses<br/>(assistant-events.mjs): results, basket, bill, address form"]
  C -. "error" .-> FB["Hinglish error +<br/>'Seedha search karein' = old direct search"]
```

- **Same decisions.** D2–D5 above apply unchanged: the request uses
  `build_session_config(...)["delegation"]["responses"]`, so model, instructions,
  profile block, tool definitions and `tool_choice` are the voice ones. There is no
  typed-only prompt. D1 does not exist for typed text (there is no front model).
- **Context per turn.** The last 12 messages of the user's own conversation (at most
  about 6,000 characters), plus this server's last turns if the browser has not saved
  them yet, plus the last 2 search results of this chat as a developer message, so
  "Dark" or "haan wahi" can add an `option_id` the user already saw.
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
  voice transcripts.
- **Known gap.** The backend prompt is written for speech, so typed replies also
  spell prices in words ("ek sau ninyanve rupaye").

## Where to look

- Feature × surface matrix: [`CAPABILITIES.md`](CAPABILITIES.md)
- Test layers and how to run them: [`TESTING.md`](TESTING.md)
- Opt-in real-provider smoke tests: [`LIVE_SMOKE.md`](LIVE_SMOKE.md)
- Voice turn-taking modes: [`voice-turn-taking.md`](voice-turn-taking.md)
