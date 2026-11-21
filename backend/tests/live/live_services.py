"""Construct the real live services exactly as ``app.dependencies`` does.

The only difference is that each service is handed a ``GuardedToolClient``
instead of the raw MCP client, and no reranker is attached (the smoke suite
checks provider contracts, not the optional third-party reranker).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import pytest

from app.commerce.swiggy.adapter import SwiggyInstamartAdapter
from app.commerce.swiggy.client import SwiggyMcpClient
from app.commerce.swiggy.oauth import SwiggyOAuthClient
from app.commerce.swiggy.service import SwiggyService
from app.commerce.swiggy.store import EncryptedConnectionStore
from app.commerce.zepto.client import ZeptoMcpClient
from app.commerce.zepto.oauth import ZeptoOAuthClient
from app.commerce.zepto.service import ZeptoService
from app.commerce.zepto.store import EncryptedZeptoConnectionStore
from app.core.config import get_settings
from app.dependencies import get_provider_encryption_key
from tests.live.harness import GuardedToolClient, RateLimitTripwire, SmokeRecorder


@dataclass
class LiveProvider:
    name: str
    service: Any
    oauth: Any
    raw_client: Any
    client: GuardedToolClient
    tripwire: RateLimitTripwire
    recorder: SmokeRecorder


def _require_store(path: Any, provider: str) -> None:
    # Checked before touching the encryption key so a fresh checkout never
    # creates a local key just because someone ran the smoke suite.
    if not path.exists():
        pytest.skip(f"{provider} has no stored connection; link it in the app first.")


def build_live_swiggy(
    tripwire: RateLimitTripwire, recorder: SmokeRecorder
) -> LiveProvider:
    settings = get_settings()
    _require_store(settings.swiggy_token_store_path, "swiggy")
    store = EncryptedConnectionStore(
        settings.swiggy_token_store_path, get_provider_encryption_key()
    )
    oauth = SwiggyOAuthClient(settings, store)
    raw = SwiggyMcpClient(settings, oauth)
    client = GuardedToolClient(
        raw,
        provider="swiggy",
        tier="read",
        tripwire=tripwire,
        recorder=recorder,
        env=os.environ,
    )
    adapter = SwiggyInstamartAdapter(client)  # type: ignore[arg-type]
    service = SwiggyService(oauth, client, adapter, None)  # type: ignore[arg-type]
    return LiveProvider("swiggy", service, oauth, raw, client, tripwire, recorder)


def build_live_zepto(
    tripwire: RateLimitTripwire, recorder: SmokeRecorder
) -> LiveProvider:
    settings = get_settings()
    _require_store(settings.zepto_token_store_path, "zepto")
    store = EncryptedZeptoConnectionStore(
        settings.zepto_token_store_path, get_provider_encryption_key()
    )
    oauth = ZeptoOAuthClient(settings, store)
    raw = ZeptoMcpClient(settings, oauth)
    client = GuardedToolClient(
        raw,
        provider="zepto",
        tier="read",
        tripwire=tripwire,
        recorder=recorder,
        env=os.environ,
    )
    service = ZeptoService(oauth, client, None)  # type: ignore[arg-type]
    return LiveProvider("zepto", service, oauth, raw, client, tripwire, recorder)
