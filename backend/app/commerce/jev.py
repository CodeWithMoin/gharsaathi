"""Optional Jev second-stage product reranker.

The provider result is already parsed, filtered, and capped before it reaches
this module. Jev may only reorder those original Product objects. Any timeout,
transport error, or malformed answer returns the deterministic order unchanged.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from app.commerce.swiggy.models import Product, ProductSearchResult


logger = logging.getLogger(__name__)
MAX_CANDIDATES = 12
DEFAULT_CACHE_TTL_SECONDS = 60.0
DEFAULT_CIRCUIT_FAILURES = 3
DEFAULT_CIRCUIT_COOLDOWN_SECONDS = 30.0


@dataclass(frozen=True)
class JevRerankOutcome:
    result: ProductSearchResult
    applied: bool
    resolved_model: str | None = None


class _NoulAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["noul"]
    noul: float = Field(ge=0.0, le=1.0)


class _JevResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1)
    answers: dict[str, _NoulAnswer]
    usage: dict[str, int] | None = None


class JevProductReranker:
    """Score a bounded shortlist in one typed Jev request."""

    def __init__(
        self,
        *,
        api_key: SecretStr,
        base_url: str,
        model: str,
        timeout_seconds: float,
        http_client: httpx.AsyncClient | None = None,
        cache_ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
        circuit_failures: int = DEFAULT_CIRCUIT_FAILURES,
        circuit_cooldown_seconds: float = DEFAULT_CIRCUIT_COOLDOWN_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._api_key = api_key
        self._endpoint = _system_one_endpoint(base_url)
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._http_client = http_client
        self._cache_ttl_seconds = cache_ttl_seconds
        self._circuit_failures = circuit_failures
        self._circuit_cooldown_seconds = circuit_cooldown_seconds
        self._clock = clock
        self._cache: dict[str, tuple[float, JevRerankOutcome]] = {}
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0
        self._lock = asyncio.Lock()

    async def rerank(
        self, query: str, result: ProductSearchResult
    ) -> ProductSearchResult:
        return (await self.rerank_with_diagnostics(query, result)).result

    async def rerank_with_diagnostics(
        self, query: str, result: ProductSearchResult
    ) -> JevRerankOutcome:
        candidates = [
            *(('product', index, product) for index, product in enumerate(result.products)),
            *(
                ('similar', index, product)
                for index, product in enumerate(result.similar_products)
            ),
        ]
        if len(candidates) < 2 or len(candidates) > MAX_CANDIDATES:
            return JevRerankOutcome(result=result.model_copy(deep=True), applied=False)

        fingerprint = _fingerprint(query, candidates)
        async with self._lock:
            now = self._clock()
            cached = self._cache.get(fingerprint)
            if cached is not None and cached[0] > now:
                return _copy_outcome(cached[1])
            if self._circuit_open_until > now:
                return JevRerankOutcome(
                    result=result.model_copy(deep=True), applied=False
                )

            try:
                scores, resolved_model = await self._score(query, candidates)
                products = _reorder(result.products, scores, "product")
                similar = _reorder(result.similar_products, scores, "similar")
            except Exception as exc:
                self._consecutive_failures += 1
                if self._consecutive_failures >= self._circuit_failures:
                    self._circuit_open_until = now + self._circuit_cooldown_seconds
                logger.warning(
                    "Jev reranking failed; using deterministic order",
                    extra={"error_type": type(exc).__name__},
                )
                return JevRerankOutcome(
                    result=result.model_copy(deep=True), applied=False
                )

            self._consecutive_failures = 0
            self._circuit_open_until = 0.0
            outcome = JevRerankOutcome(
                result=result.model_copy(
                    deep=True,
                    update={"products": products, "similar_products": similar},
                ),
                applied=True,
                resolved_model=resolved_model,
            )
            self._cache[fingerprint] = (
                now + self._cache_ttl_seconds,
                _copy_outcome(outcome),
            )
            if len(self._cache) > 128:
                oldest = min(self._cache, key=lambda key: self._cache[key][0])
                self._cache.pop(oldest, None)
            return outcome

    async def _score(
        self,
        query: str,
        candidates: list[tuple[str, int, Product]],
    ) -> tuple[dict[str, float], str]:
        key = self._api_key.get_secret_value().strip()
        if not key:
            raise ValueError("Jev API key is not configured")

        state_candidates: list[dict[str, Any]] = []
        questions: dict[str, dict[str, Any]] = {}
        for group, index, product in candidates:
            candidate_id = f"{group}_{index}"
            state_candidates.append(
                {
                    "candidate_id": candidate_id,
                    "name": product.name,
                    "brand": product.brand,
                    "pack_size": product.pack_size,
                }
            )
            questions[candidate_id] = {
                "type": "noul",
                "instructions": {
                    "task": (
                        "Is this candidate an exact or strongly relevant shopping "
                        "match for the query?"
                    ),
                    "candidate_id": candidate_id,
                },
                "criteria": {
                    "true": (
                        "The candidate matches the requested product type and any "
                        "stated brand, variant, and pack size."
                    ),
                    "false": (
                        "The candidate is a different product, competitor when an exact "
                        "brand was requested, or conflicts with the requested pack."
                    ),
                },
            }

        payload = {
            "model": self._model,
            "state": {"shopping_query": query, "candidates": state_candidates},
            "questions": questions,
        }
        headers = {"Authorization": f"Bearer {key}"}
        if self._http_client is not None:
            response = await self._http_client.post(
                self._endpoint,
                json=payload,
                headers=headers,
                timeout=self._timeout_seconds,
            )
        else:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(
                    self._endpoint,
                    json=payload,
                    headers=headers,
                )
        response.raise_for_status()
        parsed = _JevResponse.model_validate(response.json())
        expected = set(questions)
        if set(parsed.answers) != expected:
            raise ValueError("Jev response did not match the candidate set")
        return (
            {name: answer.noul for name, answer in parsed.answers.items()},
            parsed.model,
        )


def _reorder(
    products: list[Product], scores: dict[str, float], group: str
) -> list[Product]:
    indexed = list(enumerate(products))
    indexed.sort(
        key=lambda entry: (
            not entry[1].available,
            -scores[f"{group}_{entry[0]}"],
            entry[0],
        )
    )
    return [product.model_copy(deep=True) for _, product in indexed]


def _system_one_endpoint(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    if normalized.endswith("/v1"):
        return f"{normalized}/systemone"
    return f"{normalized}/v1/systemone"


def _fingerprint(
    query: str, candidates: list[tuple[str, int, Product]]
) -> str:
    value = {
        "query": " ".join(query.casefold().split()),
        "candidates": [
            {
                "group": group,
                "index": index,
                "product": product.model_dump(mode="json"),
            }
            for group, index, product in candidates
        ],
    }
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _copy_outcome(outcome: JevRerankOutcome) -> JevRerankOutcome:
    return JevRerankOutcome(
        result=outcome.result.model_copy(deep=True),
        applied=outcome.applied,
        resolved_model=outcome.resolved_model,
    )
