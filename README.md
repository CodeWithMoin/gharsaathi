# GharSaathi — Household Shopping Agent

This milestone connects one local test user to Swiggy and supports only two read-only Instamart actions: list saved delivery addresses and search products at the selected address. It cannot mutate a cart, check out, or place an order.

## Verified Swiggy contract

The integration was checked against the official Swiggy Builders Club documentation on 21 November 2026:

- Instamart Streamable HTTP endpoint: `https://mcp.swiggy.com/im`
- Authentication: OAuth 2.1 authorization code flow with PKCE S256 and Dynamic Client Registration
- Client registration: `POST https://mcp.swiggy.com/auth/register`
- Authorization: `GET https://mcp.swiggy.com/auth/authorize`
- Token exchange/refresh: `POST https://mcp.swiggy.com/auth/token`
- Read-only tools used here: `get_addresses`, `search_products`, and `get_orders`
- Tool data: `structuredContent`, normally containing a `{ success, data }` envelope

References: [authentication](https://mcp.swiggy.com/builders/docs/start/authenticate/), [MCP protocol](https://mcp.swiggy.com/builders/docs/reference/protocol/), [get_addresses](https://mcp.swiggy.com/builders/docs/reference/instamart/get_addresses/), [search_products](https://mcp.swiggy.com/builders/docs/reference/instamart/search_products/), and [get_orders](https://mcp.swiggy.com/builders/docs/reference/instamart/get_orders/).

The backend still calls `tools/list` for the authenticated user and validates each tool call against the returned live JSON Schema. Swiggy notes that a user's live catalogue may differ from the public reference.

## Swiggy rate limits

Swiggy does not publish fixed request caps or remaining-quota headers. Its gateway
can return HTTP `429` with `Retry-After`; at the MCP layer the corresponding error
code is `-32000`. See Swiggy's official [rate-limit guidance](https://mcp.swiggy.com/builders/docs/operate/rate-limits/).

The backend captures `Retry-After` before the MCP SDK can wrap the transport error,
also recognizes JSON-RPC and proxy-style 429 messages, and applies one shared
cooldown for the local user. A retry is attempted only when the provider's delay
fits inside Swiggy's recommended 30-second user-facing retry budget. Otherwise the
API immediately returns `429` with its own `Retry-After` header and a clear message.
When an MCP bridge hides the header, the safe fallback is 30 seconds. Read calls
are serialized, and the live tool schema is cached for five minutes to avoid an
extra `tools/list` request on every search. Automatic 429 retries are allowlisted
to `get_addresses` and `search_products`; future consequential tools do not inherit
blind retries.

Each MCP attempt also logs a generated correlation ID, operation name, outcome,
duration, and rate-limit delay/source. Tokens, response bodies, address data, and
search text are not logged. Use the correlation ID and timestamp when escalating a
persistent provider-side 429 to Swiggy.

The fallback, retry budget, and schema-cache duration can be adjusted with
`SWIGGY_RATE_LIMIT_FALLBACK_SECONDS`, `SWIGGY_RETRY_BUDGET_SECONDS`, and
`SWIGGY_TOOL_SCHEMA_CACHE_SECONDS`. Do not lower the fallback merely to probe the
provider more frequently; repeated calls can prolong a gateway cooldown.

## Local setup

Requirements: Python 3.11+, Node.js 20+, a Swiggy account with a saved delivery address, and a browser for Swiggy's phone/OTP page.

1. Create the root environment file and encryption key:

   ```bash
   cp .env.example .env
   python -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
   ```

   Paste the generated value into `APP_ENCRYPTION_KEY` in `.env`. Do not commit `.env` or reuse this local key in production.

2. Confirm this exact callback value in `.env`:

   ```dotenv
   SWIGGY_OAUTH_REDIRECT_URI=http://localhost:8000/api/swiggy/oauth/callback
   ```

   Swiggy documents exact-match redirect allowlisting. Its public manifest lists localhost callbacks, but if this path is rejected, ask `builders@swiggy.in` to allowlist the exact URI above. Production redirects require HTTPS and Swiggy onboarding.

3. Install and run the backend:

   ```bash
   cd backend
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   uvicorn app.main:app --reload --port 8000 --no-access-log
   ```

4. In a second terminal, install and run the frontend:

   ```bash
   cd frontend
   cp .env.local.example .env.local
   npm install
   npm run dev
   ```

5. Open [http://localhost:3000](http://localhost:3000), choose **Connect securely**, and complete phone/OTP authentication only on Swiggy's page. After Swiggy redirects back, choose a saved address and search for `Fortune cooking oil 1 litre`.

No manually issued client ID, client secret, Swiggy password, or pasted access token is required. Swiggy returns a public-client ID through DCR. OAuth state and the PKCE verifier remain in backend memory and expire after ten minutes.

## Local token storage

The single-user connection is encrypted with Fernet and stored at `backend/.data/swiggy_connection.enc` by default. The file is created with mode `0600`, excluded from Git, and never returned to the browser or logged. The application redacts OAuth callback query parameters from Uvicorn logs; the documented development command also disables access logs as defense in depth. Keep the configured encryption key stable across backend restarts. Changing it makes the existing encrypted connection unreadable; delete the local encrypted file and reconnect if you intentionally rotate the key during development.

For a multi-user deployment, replace this file store with per-user encrypted database or vault storage and complete Swiggy's delegated-auth/enterprise onboarding.

## API surface

- `GET /health`
- `GET /api/swiggy/connection`
- `GET /api/swiggy/oauth/start`
- `GET /api/swiggy/oauth/callback`
- `POST /api/swiggy/disconnect`
- `GET /api/swiggy/tools` — authenticated live catalogue, for diagnostics
- `GET /api/swiggy/addresses?page=1&pageSize=10`
- `POST /api/swiggy/search` with `{"query":"Fortune cooking oil 1 litre","address_id":"<saved-address-id>","offset":0}`
- `POST /api/swiggy/search/personalized` — applies only confirmed, permitted,
  category-relevant memories and returns which memories were used
- `POST /api/voice/session` — creates a 60-second OpenAI Realtime client secret
  with the versioned voice prompt; requires the local-development `X-User-Id`
  header and never returns the server API key
- `GET /api/swiggy/orders?count=10&activeOnly=false` — read-only recent Instamart
  history normalized without delivery address text or phone number
- `POST /api/memory/purchase-history/sync` — explicitly fetch and import recent
  read-only history after purchase-history consent; this is never run automatically
- `GET /api/memory/reorder-suggestions` — evidence-backed recommendations from the
  caller's retained graph; this does not call Swiggy or mutate a cart
- `POST /api/memory/reorder-suggestions/{product_id}/review` — records that the
  person chose to inspect current options and updates only their ranking model
- `POST /api/memory/reorder-suggestions/{product_id}/dismiss` — suppresses a
  suggestion until newer completed-order evidence exists and updates only that
  person's ranking model
- `DELETE /api/memory/purchase-history` — deletes the person's purchase graph and
  revokes its reorder-suggestion permission

## Voice prompt and Realtime sessions

The backend-owned prompt in `backend/app/voice/prompts.py` defines a feminine,
Hinglish-native shopping persona. Hindi/Hinglish self-reference must use feminine
agreement such as `karti hoon`, `karungi`, and `dekh rahi hoon`; user gender is
never guessed. It also keeps spoken turns short, makes language switching depend
on substantive speech rather than accent, and enforces the product's search-only,
approval, authentication, truthfulness, and prompt-injection boundaries.
Its compact pronunciation reference makes Romanised grocery words use their Hindi
sound—for example, `atta` is voiced as `आटा` (`aa-taa`), not English
`at-ta`—without putting phonetic notation into normal spoken responses. Few-shot
catalogue examples teach a general classification pattern: preserve provider and
brand names, apply Hindi phonology to Hindi product terms, retain Indian-English
descriptors, and expand units naturally. The policy applies to unseen Swiggy
Instamart and Zepto catalogue titles rather than acting as a fixed word dictionary.

`POST /api/voice/session` calls OpenAI's GA
`POST /v1/realtime/client_secrets` endpoint from the backend. The browser receives
only the short-lived `ek_...` credential. Configure `OPENAI_API_KEY` and the
`VOICE_*` values from `.env.example`; do not put the API key in a frontend env
file. The frontend WebRTC transport is intentionally outside this prompt-focused
change.

Run the optional API-backed behavioral evaluation with:

```bash
cd backend
python -m evaluation.run_voice_prompt
```

It uses `VOICE_EVALUATION_MODEL`, spends API credits only when `OPENAI_API_KEY` is
configured, and otherwise reports a clean skip. This text-model smoke evaluation
checks grammar and policy behavior.

Run the real OpenAI Realtime audio and variance evaluation with:

```bash
cd backend
python -m evaluation.run_realtime_voice
```

This creates three independent takes for each of 12 realistic Instamart/Zepto
catalogue titles with the configured Realtime model and voice. It rejects clear
masculine self-reference, missing product targets, excessive questions, invalid
audio, and overly long turns; it also reports response/opening diversity and
within-item similarity. Each turn has a 45-second timeout and one retry. The 24 kHz
mono MP3 files and manifest are written to `frontend/public/voice-qa-openai/`.

For local mock-audio QA on macOS, generate the curated Instamart/Zepto fixture set:

```bash
cd backend
python -m evaluation.run_voice_diversity --generate-audio
```

This uses the installed Hindi `Lekha` system voice, validates 24 kHz mono MP3
output, and computes lexical/opening diversity. The internal
[voice QA page](http://localhost:3000/voice-qa) displays the real OpenAI Realtime
corpus first and the local mock corpus separately, so reviewers can compare actual
pronunciation, feminine grammar, pacing, and repetition without confusing the two.

## Person-scoped memory

The backend includes a PostgreSQL-ready memory layer for explicit user preferences,
corrections, and purpose-specific consent. Conversation state and durable memory are
kept separate. A memory must be confirmed before retrieval, and retrieval requires
both an allowed purpose on the memory and a matching active permission.

Configure the database and run migrations:

```bash
cd backend
alembic upgrade head
```

Memory endpoints currently require `X-User-Id`. This header is only a local-development
identity boundary; replace it with the authenticated application's user ID before any
multi-user deployment.

- `POST /api/memory/proposals`
- `POST /api/memory/extract` — extracts evidence-backed proposals from one user message
- `GET /api/memory`
- `POST /api/memory/{memory_id}/confirm`
- `POST /api/memory/{memory_id}/reject`
- `POST /api/memory/{memory_id}/correct`
- `DELETE /api/memory/{memory_id}`
- `PUT /api/memory/permissions/{data_category}/{purpose}`
- `GET /api/memory/permissions`
- `POST /api/memory/retrieve`

The initial policy blocks memory purposes related to differential pricing, credit,
eligibility, employment, housing, insurance, and pressure targeting. Corrections
create a new confirmed record and retain the superseded record and audit events.

Memory extraction uses the versioned structured-output prompt in
`backend/app/memory/prompts.py`. If `OPENAI_API_KEY` is configured, the backend uses
the Responses API with a strict Pydantic output schema and the model selected by
`MEMORY_EXTRACTION_MODEL`. Without a key, it falls back to a high-precision local
English/Hinglish ruleset. Model output never becomes confirmed memory automatically:
the evidence must occur verbatim in the user's message, and the UI asks the user to
**Confirm & use** or reject each proposal.

The first extraction version recognizes brand, pack-size, quantity, dietary, and
product-avoidance preferences. It deliberately excludes demographics, precise
location, financial profiling, health conditions, relationships, and business data.

### Purchase-history graph

Purchase history is represented as a person-scoped property graph in PostgreSQL:
person → order → occurrence → product, with optional product/category edges. Composite
foreign keys prevent an edge from connecting nodes owned by different people. Imports
are idempotent and store only normalized order/product facts, a provenance hash, and a
retention deadline. Raw provider responses, delivery addresses, phone numbers, and
payment methods are not stored in the graph.

Before history can be imported or queried, the person must explicitly enable the
`purchase_history` / `proactive_reorder` permission with a positive retention period.
The UI currently enables this memory separately and does not trigger a provider sync.
Sync remains an explicit button action. Swiggy currently documents `get_orders` as a
15-day window, so repeated explicit syncs can accumulate a longer retained history;
the app does not poll the provider in the background. Recommendations use a hybrid pipeline: supported
frequency, cadence, weekday/daypart, quantity, and item-to-item basket-affinity
patterns generate candidates; a person-scoped online logistic ranker then orders those
candidates. The ranker updates from explicit **Check options** and **Not now** actions,
so two people can learn different priorities without sharing raw histories. Minimum
evidence gates prevent two coincidental repeats from being presented as a routine.
Population-level collaborative filtering is intentionally not enabled while the app
has sparse single-user data; it can be evaluated later with enough consented users.

Every card states its historical evidence, never claims the item is running low, and
only offers to review current options. Recommendations cannot add to cart or place an
order; any future purchase must still use exact-basket approval. A dismissed product
stays suppressed until a newer completed purchase provides fresh evidence. Revoking
purchase-history memory deletes the graph, feedback, and the person's learned model.

#### Recommender upgrade path

The first production baseline stays intentionally small-data friendly. The
[Recommenders project](https://github.com/recommenders-team/recommenders) is used as a
reference for temporal splits, ranking metrics, and future challenger models rather
than added as a runtime dependency. SAR or LightFM becomes useful after there are
enough consented people and item interactions for collaborative signal. SASRec should
only be trialled after users have sufficiently long sequences; it is not a sensible
cold-start model for a household with a few orders. Challenger promotion requires a
time-based offline comparison on Recall@K/NDCG@K plus product safety checks, followed
by an online experiment. Raw histories must not cross household boundaries merely to
make model training easier.

## Verification

Backend:

```bash
cd backend
pytest -q
python -m evaluation.run_recommender
python -m evaluation.run_memory_extraction
```

Frontend:

```bash
cd frontend
npm run lint
npx tsc --noEmit
npm run build
```

Automated tests mock OAuth and MCP results. They never call cart, checkout, payment, or order tools. A real end-to-end address/search verification requires completing Swiggy OAuth in a browser with a test account.
