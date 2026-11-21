from urllib.parse import urlsplit, urlunsplit

import httpx


_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


async def local_callback_is_ready(
    redirect_uri: str,
    *,
    expected_service: str | None = None,
) -> bool:
    """Probe local OAuth callback infrastructure before creating OAuth state."""

    parsed = urlsplit(redirect_uri)
    if parsed.hostname not in _LOOPBACK_HOSTS:
        return True
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    host = "127.0.0.1"
    health_url = urlunsplit((parsed.scheme, f"{host}:{port}", "/health", "", ""))
    try:
        async with httpx.AsyncClient(timeout=1.0, trust_env=False) as client:
            response = await client.get(health_url)
        if response.status_code != 200:
            return False
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return False
    return payload.get("status") == "ok" and (
        expected_service is None or payload.get("service") == expected_service
    )
