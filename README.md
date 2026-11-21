# GharSaathi — Household Shopping Agent

The local app starts in a safe static-demo mode. It can exercise address selection,
product search, baskets, deterministic fees, and exact-bill approval without calling
Swiggy or Zepto. It cannot check out or place an order.

## Safe demo versus live commerce

```bash
make dev       # default: static fixtures, zero commerce API/MCP calls
make dev-live  # explicit opt-in: live provider connections and MCP tools
```

Demo mode is the right place to iterate on UI, voice, chat, basket, and confirmation
flows. It returns a local sample address and catalogue and calculates sample fees in
deterministic backend code. Both provider cards remain visible, but no provider OAuth,
search, cart, or catalogue-discovery request is made.

In live mode, ordinary reloads do not fetch either provider's addresses again. The
backend returns each provider's database snapshot and saved selection. Only the
explicit **Refresh from Swiggy** or **Refresh from Zepto** action invokes a provider
address tool. Searches and final-bill requests are live actions and are clearly
labelled in the UI.

## Verified Swiggy contract

The integration was checked against the official Swiggy Builders Club documentation on 21 November 2026:

- Instamart Streamable HTTP endpoint: `https://mcp.swiggy.com/im`
- Authentication: OAuth 2.1 authorization code flow with PKCE S256 and Dynamic Client Registration
- Client registration: `POST https://mcp.swiggy.com/auth/register`
- Authorization: `GET https://mcp.swiggy.com/auth/authorize`
- Token exchange/refresh: `POST https://mcp.swiggy.com/auth/token`
- Read-only tools: `get_addresses` and `search_products`
- Tool data: `structuredContent`, normally containing a `{ success, data }` envelope

References: [authentication](https://mcp.swiggy.com/builders/docs/start/authenticate/), [MCP protocol](https://mcp.swiggy.com/builders/docs/reference/protocol/), [get_addresses](https://mcp.swiggy.com/builders/docs/reference/instamart/get_addresses/), and [search_products](https://mcp.swiggy.com/builders/docs/reference/instamart/search_products/).

The backend discovers `tools/list` once per authenticated MCP session and validates
each tool call against the returned live JSON Schema. The same persistent session and
catalogue are reused for later reads instead of re-initializing MCP for every request.
Swiggy notes that a user's live catalogue may differ from the public reference.

## Swiggy rate limits

Swiggy does not publish fixed request caps or remaining-quota headers. Its gateway
can return HTTP `429` with `Retry-After`; at the MCP layer the corresponding error
code is `-32000`. The backend captures the raw header before the Python MCP SDK can
wrap the transport error, and also recognizes JSON-RPC and proxy-style 429 messages.

The exact provider delay becomes a shared local cooldown for the linked account. If
an MCP bridge hides the header, the configurable fallback is 30 seconds. During the
cooldown the backend returns `429` immediately without probing Swiggy, includes a
browser-visible `Retry-After` response header, and leaves Zepto unaffected. Every
provider rejection logs a generated correlation ID, operation name, delay, and delay
source without logging tokens, addresses, response bodies, or search text.

If a 429 remains after a genuine quiet period, stop all other clients using the same
Swiggy identity and make one controlled request. Escalate a continued failure to
`builders@swiggy.in` with the correlation ID, timestamp/timezone, MCP endpoint, HTTP
status, JSON-RPC code, and `Retry-After` value. Never include an access token or OTP.

Reference: [Swiggy rate-limit guidance](https://mcp.swiggy.com/builders/docs/operate/rate-limits/).

## Local setup

Requirements for demo mode: Python 3.11+, Node.js 20+, and PostgreSQL. Live mode also
requires provider accounts and a browser for the providers' phone/OTP pages.

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

5. Open [http://localhost:3003](http://localhost:3003), choose **Connect securely**, and complete phone/OTP authentication only on Swiggy's page. After Swiggy redirects back, choose a saved address and search for `Fortune cooking oil 1 litre`.

No manually issued client ID, client secret, Swiggy password, or pasted access token is required. Swiggy returns a public-client ID through DCR. OAuth state and the PKCE verifier remain in backend memory and expire after ten minutes.

## Zepto connection and product search

The app can also connect a local Zepto account through the official [Zepto MCP](https://github.com/zeptonow/mcp) OAuth flow. It uses Dynamic Client Registration, PKCE S256, and the official Streamable HTTP MCP endpoint. Zepto accepts the allowlisted `http://localhost/callback` registration, so start the callback bridge in a third terminal before connecting:

```bash
cd backend
sudo uvicorn app.local_callback_bridge:app --host 127.0.0.1 --port 80 --no-access-log
```

Then use `GET /api/zepto/oauth/start` (the UI's **Connect Zepto** action) and
complete the phone/OTP flow only on Zepto's site. The callback bridge forwards the
result to the backend, which validates OAuth state and keeps the token encrypted
locally. After connecting, use **Refresh from Zepto** once to fetch and save the
account's delivery addresses. Later reloads use that database snapshot without an MCP
call. Swiggy and Zepto can stay connected together; a shopping request searches every
provider whose saved address is ready.

The authenticated Zepto catalogue was inspected before enabling actions. This build
exposes only saved-address retrieval and product search. It keeps one serialized MCP
session, caches the live tool catalogue for that session, reuses the selected address
and past-order context, and caches an identical search briefly. A provider 429 starts
a local cooldown so repeated UI actions do not keep extending the block. Zepto cart,
payment, checkout, and order tools remain unavailable through this app. The scope is
fixed to `tools:read`.

## Local token storage

The single-user connection is encrypted with Fernet and stored at `backend/.data/swiggy_connection.enc` by default. The file is created with mode `0600`, excluded from Git, and never returned to the browser or logged. If `APP_ENCRYPTION_KEY` is not set, local development creates a private key once at `backend/.data/app_encryption.key` and reuses it across backend restarts. The application redacts OAuth callback query parameters from Uvicorn logs; the documented development command also disables access logs as defense in depth. Keep the configured encryption key stable across backend restarts. Changing or deleting it makes the existing encrypted connection unreadable; delete the local encrypted connection file and reconnect if you intentionally rotate the key.

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
- `GET /api/zepto/connection`
- `GET /api/zepto/oauth/start`
- `GET /api/zepto/oauth/callback`
- `POST /api/zepto/disconnect`
- `GET /api/zepto/addresses?refresh=false` — database snapshot by default; provider
  access only when `refresh=true`
- `PUT /api/zepto/addresses/selected`
- `POST /api/zepto/search` with
  `{"query":"milk and bread","address_id":"<saved-address-id>","page_number":0}`

## Person-scoped memory

The backend includes a PostgreSQL-ready memory layer for explicit user preferences,
corrections, and purpose-specific consent. Conversation state and durable memory are
kept separate. A memory must be confirmed before retrieval, and retrieval requires
both an allowed purpose on the memory and a matching active permission.

`make dev` starts the local Homebrew PostgreSQL instance when needed, creates the
`equal_ai` development database, and applies migrations before starting the API.
To configure another database or apply migrations manually:

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

## Verification

Backend:

```bash
cd backend
pytest -q
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
