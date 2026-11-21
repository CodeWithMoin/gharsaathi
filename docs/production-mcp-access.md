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
allowlist currently contains localhost, loopback addresses, and a few known MCP
clients; it does not contain `gharsaathi.app`. Open an issue in
`zeptonow/mcp` requesting the exact production callback before enabling Zepto
login on the hosted app.

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
- replace the single-user encrypted token files with per-user encrypted
  provider connections in PostgreSQL or another durable store backed by KMS;
- persist support/tracking state outside container `/tmp` and define retention
  and deletion policies;
- keep OAuth state, provider cooldowns, and tokens shared across tasks before
  scaling the backend above one ECS task;
- run the complete OAuth and read-only search acceptance suite against the
  approved production callbacks; and
- keep cart, payment, checkout, and order placement disabled until their
  separate approval and non-replay review is complete.

The current CloudFormation template deliberately hard-codes
`COMMERCE_MODE=demo`. Do not turn that into a parameter until these gates are
implemented and reviewed.

## Current official references

- Swiggy MCP manifest and redirect allowlist:
  https://github.com/Swiggy/swiggy-mcp-server-manifest
- Zepto MCP redirect allowlist and real-order warning:
  https://github.com/zeptonow/mcp
