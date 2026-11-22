# Production MCP access

GharSaathi can call both providers from its AWS backend. The MCP transports are
remote HTTPS services; they are not restricted to a developer laptop. OAuth is
the part that is environment-specific.

## Production callback URLs

Request these exact callback URLs before changing `COMMERCE_MODE` from `demo`
to `live`:

- Swiggy: `https://gharsaathi.app/api/swiggy/oauth/callback`
- Zepto: `https://gharsaathi.app/api/zepto/oauth/callback`

OAuth redirect URLs must match exactly. Do not use wildcards, HTTP, or the local
Zepto callback bridge in production.

## Swiggy

Swiggy's official MCP manifest currently says third-party application
development is not permitted while its security and compliance review is in
progress. Its published redirect allowlist includes localhost and known MCP
clients, but not `gharsaathi.app`. Do not enable hosted Swiggy access unless
Swiggy explicitly approves the application and the exact HTTPS callback. Apply
through Swiggy Builders Club and provide the callback, expected traffic,
architecture, and a demo video.

## Zepto

Zepto's public MCP is also a remote HTTPS service. Its published callback
allowlist (checked 2026-11-22) contains `claude://…`, `https://claude.ai/…`,
VS Code, Postman, `http://localhost`, `http://localhost/callback`,
`http://127.0.0.1` and `http://127.0.0.1/callback`; it does not contain
`gharsaathi.app`. GharSaathi registers its client with dynamic client
registration (`POST https://auth.zepto.co.in/register` with
`redirect_uris: [ZEPTO_OAUTH_REDIRECT_URI]`), so a hosted login fails until the
exact production URI is allow-listed.

The hosted flow itself needs no bridge: `POST /api/zepto/oauth/authorization`
(signed-in owner) creates PKCE state on EFS and returns Zepto's URL; Zepto
redirects the browser to `https://gharsaathi.app/api/zepto/oauth/callback`,
CloudFront forwards `/api/*` to the backend, which validates state, exchanges the
code and redirects to `https://gharsaathi.app/?zepto=connected`. The local
port-80 bridge exists only because Zepto allows callbacks without a port; it must
not be used for the hosted site.

Request to open in `zeptonow/mcp` (GitHub issue):

> Please allow-list the OAuth redirect URI
> `https://gharsaathi.app/api/zepto/oauth/callback` for dynamic client
> registration. GharSaathi is a private, single-owner household shopping
> assistant (FastAPI backend on AWS, Next.js web app). Scope `tools:read`
> today; tokens stay server-side, encrypted at rest; no order is placed without
> the owner's tap on an order card; cash on delivery only. Expected traffic: one
> user, a few sessions a day.

## Application boundary
## Application boundary

The browser talks only to the GharSaathi backend. The backend performs OAuth,
stores provider tokens encrypted, and opens MCP sessions. Provider access and
refresh tokens must never be returned to browser JavaScript, committed to Git,
or logged.

The public site should remain in `COMMERCE_MODE=demo` until each provider has
approved its callback independently. One provider can be enabled without
enabling the other.

## Internal production gates

Provider approval alone is not enough. Before enabling live commerce on AWS:

- keep Clerk JWT verification enabled and configure the production authorized
  party/domain and public verification key;
- **multi-user only:** replace the single-user encrypted token files with per-user
  encrypted provider connections in PostgreSQL or another durable store backed
  by KMS. A private deployment with `ALLOWED_USER_IDS` set to the owner alone may
  keep the single connection, because only the owner can reach any route that
  starts OAuth or uses the connection;
- persist support/tracking state outside container `/tmp` (done: EFS mount at
  `backend/.data`) and define retention and deletion policies;
- keep OAuth state, provider cooldowns, and tokens shared across tasks before
  scaling the backend above one ECS task (the template pins it to one);
- run the complete OAuth and read-only search acceptance suite against the
  approved production callbacks; and
- keep cart, payment, checkout, and order placement behind their existing
  approval and non-replay rules (tap-confirmed order card, COD only while
  `ZEPTO_ONLINE_PAYMENTS_ENABLED=false`).

The template takes `CommerceMode` (default `demo`); `scripts/deploy-aws.sh`
refuses `live` unless `CONFIRM_LIVE=zepto-callback-allowlisted` is also set.

## Current official references

- Swiggy MCP manifest and redirect allowlist:
  https://github.com/Swiggy/swiggy-mcp-server-manifest
- Zepto MCP redirect allowlist and real-order warning:
  https://github.com/zeptonow/mcp
