# GharCart — Household Shopping Agent

This milestone connects one local test user to Swiggy and supports only two read-only Instamart actions: list saved delivery addresses and search products at the selected address. It cannot mutate a cart, check out, or place an order.

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

The backend still calls `tools/list` for the authenticated user and validates each tool call against the returned live JSON Schema. Swiggy notes that a user's live catalogue may differ from the public reference.

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

## Verification

Backend:

```bash
cd backend
pytest -q
```

Frontend:

```bash
cd frontend
npm run lint
npx tsc --noEmit
npm run build
```

Automated tests mock OAuth and MCP results. They never call cart, checkout, payment, or order tools. A real end-to-end address/search verification requires completing Swiggy OAuth in a browser with a test account.
