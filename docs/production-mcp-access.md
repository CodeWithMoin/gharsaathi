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

Swiggy supports hosted web integrations, but production access is reviewed.
Apply through Swiggy Builders Club and provide the HTTPS callback, expected
traffic, architecture, and a demo video. Swiggy's documentation says localhost
is allowed for development; production redirects require HTTPS and explicit
allowlisting.

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
