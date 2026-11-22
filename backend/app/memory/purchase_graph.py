from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import hashlib
import json
import re
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import delete, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.commerce.swiggy.models import OrderList, OrderSummary
from app.database.models import (
    MemoryPermission,
    MemorySubject,
    PersonalRecommendationModel,
    ProductCategoryEdge,
    PurchaseCategoryNode,
    PurchaseHistorySyncState,
    PurchaseOccurrenceEdge,
    PurchaseOrderNode,
    PurchaseProductNode,
    ReorderSuggestionFeedback,
)
from app.memory.errors import MemoryNotFoundError, MemoryPermissionError
from app.memory.purchase_patterns import (
    PatternEvidence,
    PurchaseObservation,
    RelatedProduct,
    mine_purchase_patterns,
)
from app.memory.repository import MemoryRepository
from app.memory.recommender import (
    DEFAULT_BIAS,
    MODEL_VERSION,
    initial_weights,
    score_candidate,
    update_personal_model,
)


DATA_CATEGORY = "purchase_history"
PURPOSE = "proactive_reorder"
DELIVERED_STATUSES = {"delivered", "completed"}


class OrderHistoryReader(Protocol):
    async def orders(self, count: int, active_only: bool) -> OrderList: ...


class PurchaseImportResult(BaseModel):
    imported_orders: int = Field(ge=0)
    imported_products: int = Field(ge=0)
    imported_occurrences: int = Field(ge=0)
    pruned_orders: int = Field(ge=0)
    cached: bool = False


class ReorderSuggestion(BaseModel):
    product_id: UUID
    product_name: str
    provider: str
    provider_item_id: str | None
    purchase_count: int = Field(ge=2)
    total_quantity: int = Field(ge=2)
    last_ordered_at: datetime
    pattern_confidence: float = Field(ge=0, le=1)
    score: float = Field(ge=0, le=1)
    model_version: str
    patterns: list[PatternEvidence] = Field(min_length=1)
    related_products: list[RelatedProduct] = Field(default_factory=list)
    reason: str
    voice_prompt: str
    requires_approval: Literal[True] = True
    assumes_current_stock: Literal[False] = False
    ranking_features: dict[str, float] = Field(exclude=True, repr=False)


class RecalledPurchase(BaseModel):
    product_name: str
    provider: str
    provider_item_id: str | None = None
    last_purchased_at: datetime
    purchase_count: int = Field(ge=1)


class PurchaseGraphService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.memory_repository = MemoryRepository(session)

    async def sync(
        self,
        actor: MemorySubject,
        reader: OrderHistoryReader,
        provider: str = "swiggy",
        count: int = 15,
    ) -> PurchaseImportResult:
        permission = await self._permission(actor)
        sync_state = await self._sync_state(actor.id, provider)
        now = datetime.now(UTC)
        if (
            sync_state.last_success_at is not None
            and now - self._as_utc(sync_state.last_success_at) < timedelta(minutes=10)
        ):
            return PurchaseImportResult(
                imported_orders=0,
                imported_products=0,
                imported_occurrences=0,
                pruned_orders=0,
                cached=True,
            )
        sync_state.last_attempt_at = now
        try:
            orders = await reader.orders(count=count, active_only=False)
        except Exception as exc:
            sync_state.last_error_code = type(exc).__name__[:80]
            await self.session.commit()
            raise
        result = await self.import_orders(actor, orders, provider, permission)
        sync_state.last_success_at = datetime.now(UTC)
        sync_state.last_error_code = None
        sync_state.fetched_order_count = len(orders.orders)
        if orders.orders:
            sync_state.window_start = min(item.created_at for item in orders.orders)
            sync_state.window_end = max(item.created_at for item in orders.orders)
        await self.session.commit()
        return result

    async def import_orders(
        self,
        actor: MemorySubject,
        orders: OrderList,
        provider: str = "swiggy",
        permission: MemoryPermission | None = None,
    ) -> PurchaseImportResult:
        permission = permission or await self._permission(actor)
        now = datetime.now(UTC)
        retention_expires_at = now + timedelta(days=permission.retention_days or 0)
        imported_order_count = 0
        imported_products: set[UUID] = set()
        occurrence_count = 0

        for provider_order in orders.orders:
            order, order_created = await self._upsert_order(
                actor.id, provider, provider_order, retention_expires_at
            )
            imported_order_count += int(order_created)
            quantities: dict[str, int] = defaultdict(int)
            names: dict[str, tuple[str, str | None]] = {}
            current_product_ids: set[UUID] = set()
            for item in provider_order.items:
                identity = self._identity_key(item.item_id, item.name)
                quantities[identity] += item.quantity
                names[identity] = (item.name, item.item_id)

            for identity, quantity in quantities.items():
                name, item_id = names[identity]
                product, product_created = await self._upsert_product(
                    actor.id,
                    provider,
                    identity,
                    item_id,
                    name,
                    provider_order.created_at,
                )
                if product_created:
                    imported_products.add(product.id)
                current_product_ids.add(product.id)
                occurrence = await self._occurrence(order.id, product.id)
                if occurrence is None:
                    self.session.add(
                        PurchaseOccurrenceEdge(
                            subject_id=actor.id,
                            order_id=order.id,
                            product_id=product.id,
                            quantity=quantity,
                            observed_at=provider_order.created_at,
                            source="provider_order_history",
                        )
                    )
                    occurrence_count += 1
                else:
                    occurrence.quantity = quantity
                    occurrence.observed_at = provider_order.created_at
            await self.session.flush()
            stale_occurrences = delete(PurchaseOccurrenceEdge).where(
                PurchaseOccurrenceEdge.order_id == order.id,
                PurchaseOccurrenceEdge.subject_id == actor.id,
            )
            if current_product_ids:
                stale_occurrences = stale_occurrences.where(
                    PurchaseOccurrenceEdge.product_id.not_in(current_product_ids)
                )
            await self.session.execute(
                stale_occurrences.execution_options(synchronize_session=False)
            )

        await self.session.flush()
        pruned = await self._prune_expired(actor.id, now)
        await self.session.commit()
        return PurchaseImportResult(
            imported_orders=imported_order_count,
            imported_products=len(imported_products),
            imported_occurrences=occurrence_count,
            pruned_orders=pruned,
        )

    async def suggestions(
        self,
        actor: MemorySubject,
        limit: int = 5,
        now: datetime | None = None,
        context_offset_minutes: int = 0,
    ) -> list[ReorderSuggestion]:
        await self._permission(actor)
        now = now or datetime.now(UTC)
        await self._prune_expired(actor.id, now)
        await self.session.commit()
        rows = (
            await self.session.execute(
                select(PurchaseProductNode, PurchaseOccurrenceEdge, PurchaseOrderNode)
                .join(
                    PurchaseOccurrenceEdge,
                    PurchaseOccurrenceEdge.product_id == PurchaseProductNode.id,
                )
                .join(
                    PurchaseOrderNode,
                    PurchaseOrderNode.id == PurchaseOccurrenceEdge.order_id,
                )
                .where(
                    PurchaseProductNode.subject_id == actor.id,
                    PurchaseOccurrenceEdge.subject_id == actor.id,
                    PurchaseOrderNode.subject_id == actor.id,
                    PurchaseOrderNode.is_active.is_(False),
                    PurchaseOrderNode.retention_expires_at > now,
                )
            )
        ).all()
        feedback_rows = (
            await self.session.execute(
                select(ReorderSuggestionFeedback).where(
                    ReorderSuggestionFeedback.subject_id == actor.id,
                    ReorderSuggestionFeedback.action == "dismissed",
                )
            )
        ).scalars().all()
        latest_dismissal: dict[UUID, datetime] = {}
        for feedback in feedback_rows:
            created_at = self._as_utc(feedback.created_at)
            previous = latest_dismissal.get(feedback.product_id)
            if previous is None or created_at > previous:
                latest_dismissal[feedback.product_id] = created_at

        observations: list[PurchaseObservation] = []
        product_providers: dict[UUID, str] = {}
        completed_order_ids: set[UUID] = set()
        for product, edge, order in rows:
            if order.status.casefold() in DELIVERED_STATUSES:
                product_providers[product.id] = product.provider
                completed_order_ids.add(order.id)
                observations.append(
                    PurchaseObservation(
                        product_id=product.id,
                        product_name=product.name,
                        provider_item_id=product.provider_item_id,
                        order_id=order.id,
                        ordered_at=self._as_utc(edge.observed_at),
                        quantity=edge.quantity,
                        local_weekday=order.local_weekday,
                        local_hour=order.local_hour,
                    )
                )

        current_local = now + timedelta(minutes=context_offset_minutes)
        mined_patterns = mine_purchase_patterns(
            observations,
            completed_order_count=len(completed_order_ids),
            now=now,
            current_weekday=current_local.weekday(),
            current_hour=current_local.hour,
        )
        personal_model = await self._personal_model(actor.id, create=False)
        weights = personal_model.weights if personal_model else initial_weights()
        bias = personal_model.bias if personal_model else DEFAULT_BIAS

        suggestions: list[ReorderSuggestion] = []
        for pattern in mined_patterns:
            dismissed_at = latest_dismissal.get(pattern.product_id)
            if dismissed_at is not None and dismissed_at >= pattern.last_ordered_at:
                continue
            summary = " ".join(item.statement for item in pattern.evidence[:2])
            rank = score_candidate(pattern.features, weights, bias)
            suggestions.append(
                ReorderSuggestion(
                    product_id=pattern.product_id,
                    product_name=pattern.product_name,
                    provider=product_providers[pattern.product_id],
                    provider_item_id=pattern.provider_item_id,
                    purchase_count=pattern.purchase_count,
                    total_quantity=pattern.total_quantity,
                    last_ordered_at=pattern.last_ordered_at,
                    pattern_confidence=pattern.confidence,
                    score=rank.score,
                    model_version=rank.model_version,
                    patterns=list(pattern.evidence),
                    related_products=list(pattern.related_products),
                    reason=f"{summary} Want to review current options?",
                    voice_prompt=(
                        f"Aapke completed orders mein {pattern.product_name} ka "
                        f"repeat pattern mila, {pattern.purchase_count} purchases mein. "
                        "Current options check karun?"
                    ),
                    ranking_features=pattern.features,
                )
            )
        return sorted(
            suggestions,
            key=lambda item: (item.score, item.pattern_confidence, item.last_ordered_at),
            reverse=True,
        )[:limit]

    async def has_history_permission(self, actor: MemorySubject) -> bool:
        try:
            await self._permission(actor)
        except MemoryPermissionError:
            return False
        return True

    async def recent_purchases(
        self,
        actor: MemorySubject,
        matches: Callable[[str], bool],
        limit: int = 3,
        now: datetime | None = None,
    ) -> list[RecalledPurchase]:
        """Read-only: delivered, retained products of this person whose name matches."""
        await self._permission(actor)
        now = now or datetime.now(UTC)
        rows = (
            await self.session.execute(
                select(PurchaseProductNode, PurchaseOccurrenceEdge, PurchaseOrderNode)
                .join(
                    PurchaseOccurrenceEdge,
                    PurchaseOccurrenceEdge.product_id == PurchaseProductNode.id,
                )
                .join(
                    PurchaseOrderNode,
                    PurchaseOrderNode.id == PurchaseOccurrenceEdge.order_id,
                )
                .where(
                    PurchaseProductNode.subject_id == actor.id,
                    PurchaseOccurrenceEdge.subject_id == actor.id,
                    PurchaseOrderNode.subject_id == actor.id,
                    PurchaseOrderNode.is_active.is_(False),
                    PurchaseOrderNode.retention_expires_at > now,
                )
            )
        ).all()
        found: dict[UUID, RecalledPurchase] = {}
        for product, edge, order in rows:
            if order.status.casefold() not in DELIVERED_STATUSES or not matches(product.name):
                continue
            observed = self._as_utc(edge.observed_at)
            current = found.get(product.id)
            if current is None:
                found[product.id] = RecalledPurchase(
                    product_name=product.name,
                    provider=product.provider,
                    provider_item_id=product.provider_item_id,
                    last_purchased_at=observed,
                    purchase_count=1,
                )
            else:
                found[product.id] = RecalledPurchase(
                    product_name=current.product_name,
                    provider=current.provider,
                    provider_item_id=current.provider_item_id,
                    last_purchased_at=max(current.last_purchased_at, observed),
                    purchase_count=current.purchase_count + 1,
                )
        return sorted(
            found.values(), key=lambda item: item.last_purchased_at, reverse=True
        )[:limit]

    async def dismiss_suggestion(
        self,
        actor: MemorySubject,
        product_id: UUID,
        context_offset_minutes: int = 0,
    ) -> None:
        await self._record_feedback(
            actor, product_id, "dismissed", 0.0, context_offset_minutes
        )

    async def review_suggestion(
        self,
        actor: MemorySubject,
        product_id: UUID,
        context_offset_minutes: int = 0,
    ) -> None:
        await self._record_feedback(
            actor, product_id, "reviewed", 1.0, context_offset_minutes
        )

    async def purge(self, actor: MemorySubject) -> None:
        """Delete this person's purchase graph and revoke future use."""
        for model in (
            PurchaseOrderNode,
            PurchaseProductNode,
            PurchaseCategoryNode,
            PurchaseHistorySyncState,
            PersonalRecommendationModel,
        ):
            await self.session.execute(
                delete(model)
                .where(model.subject_id == actor.id)
                .execution_options(synchronize_session=False)
            )
        permission = await self.memory_repository.get_permission(
            actor.id, DATA_CATEGORY, PURPOSE
        )
        if permission is not None:
            permission.allowed = False
            permission.revoked_at = datetime.now(UTC)
        await self.session.commit()

    async def _record_feedback(
        self,
        actor: MemorySubject,
        product_id: UUID,
        action: str,
        label: float,
        context_offset_minutes: int,
    ) -> None:
        candidates = await self.suggestions(
            actor,
            limit=100,
            context_offset_minutes=context_offset_minutes,
        )
        suggestion = next(
            (item for item in candidates if item.product_id == product_id), None
        )
        if suggestion is None:
            raise MemoryNotFoundError("Recommendation was not found for this person.")
        model = await self._personal_model(actor.id, create=True)
        assert model is not None
        weights, bias = update_personal_model(
            suggestion.ranking_features,
            label,
            model.weights,
            model.bias,
            model.training_examples,
        )
        model.weights = weights
        model.bias = bias
        model.training_examples += 1
        model.model_version = MODEL_VERSION
        self.session.add(
            ReorderSuggestionFeedback(
                subject_id=actor.id,
                product_id=product_id,
                action=action,
                features=suggestion.ranking_features,
                model_score=suggestion.score,
                model_version=suggestion.model_version,
                created_at=datetime.now(UTC),
            )
        )
        await self.session.commit()

    async def _personal_model(
        self, subject_id: UUID, create: bool
    ) -> PersonalRecommendationModel | None:
        model = (
            await self.session.execute(
                select(PersonalRecommendationModel).where(
                    PersonalRecommendationModel.subject_id == subject_id
                )
            )
        ).scalar_one_or_none()
        if model is None and create:
            model = PersonalRecommendationModel(
                subject_id=subject_id,
                model_version=MODEL_VERSION,
                weights=initial_weights(),
                bias=DEFAULT_BIAS,
                training_examples=0,
            )
            self.session.add(model)
            await self.session.flush()
        return model

    async def _permission(self, actor: MemorySubject) -> MemoryPermission:
        permission = await self.memory_repository.get_permission(
            actor.id, DATA_CATEGORY, PURPOSE
        )
        if (
            permission is None
            or not permission.allowed
            or permission.revoked_at is not None
            or permission.retention_days is None
            or permission.retention_days <= 0
        ):
            raise MemoryPermissionError(
                "Purchase-history memory requires explicit proactive_reorder consent "
                "and a positive retention period."
            )
        return permission

    async def _sync_state(
        self, subject_id: UUID, provider: str
    ) -> PurchaseHistorySyncState:
        state = (
            await self.session.execute(
                select(PurchaseHistorySyncState).where(
                    PurchaseHistorySyncState.subject_id == subject_id,
                    PurchaseHistorySyncState.provider == provider,
                )
            )
        ).scalar_one_or_none()
        if state is None:
            state = PurchaseHistorySyncState(subject_id=subject_id, provider=provider)
            self.session.add(state)
            await self.session.flush()
        return state

    async def _upsert_order(
        self,
        subject_id: UUID,
        provider: str,
        source: OrderSummary,
        retention_expires_at: datetime,
    ) -> tuple[PurchaseOrderNode, bool]:
        order = (
            await self.session.execute(
                select(PurchaseOrderNode).where(
                    PurchaseOrderNode.subject_id == subject_id,
                    PurchaseOrderNode.provider == provider,
                    PurchaseOrderNode.provider_order_id == source.order_id,
                )
            )
        ).scalar_one_or_none()
        provenance_hash = hashlib.sha256(
            json.dumps(
                {
                    "order_id": source.order_id,
                    "status": source.status,
                    "created_at": source.created_at.isoformat(),
                    "updated_at": source.updated_at.isoformat(),
                    "local_weekday": source.created_at.weekday(),
                    "local_hour": source.created_at.hour,
                    "total_paise": source.total_paise,
                    "is_active": source.is_active,
                    "items": [
                        {
                            "item_id": item.item_id,
                            "name": item.name,
                            "quantity": item.quantity,
                        }
                        for item in source.items
                    ],
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        created = order is None
        if order is None:
            order = PurchaseOrderNode(
                subject_id=subject_id,
                provider=provider,
                provider_order_id=source.order_id,
                status=source.status,
                occurred_at=source.created_at,
                provider_updated_at=source.updated_at,
                local_weekday=source.created_at.weekday(),
                local_hour=source.created_at.hour,
                utc_offset_minutes=self._utc_offset_minutes(source.created_at),
                total_paise=source.total_paise,
                is_active=source.is_active,
                provenance_hash=provenance_hash,
                retention_expires_at=retention_expires_at,
            )
            self.session.add(order)
            await self.session.flush()
        else:
            order.status = source.status
            order.occurred_at = source.created_at
            order.provider_updated_at = source.updated_at
            order.local_weekday = source.created_at.weekday()
            order.local_hour = source.created_at.hour
            order.utc_offset_minutes = self._utc_offset_minutes(source.created_at)
            order.total_paise = source.total_paise
            order.is_active = source.is_active
            order.provenance_hash = provenance_hash
            order.retention_expires_at = min(
                self._as_utc(order.retention_expires_at), retention_expires_at
            )
        return order, created

    async def _upsert_product(
        self,
        subject_id: UUID,
        provider: str,
        identity: str,
        provider_item_id: str | None,
        name: str,
        observed_at: datetime,
    ) -> tuple[PurchaseProductNode, bool]:
        product = (
            await self.session.execute(
                select(PurchaseProductNode).where(
                    PurchaseProductNode.subject_id == subject_id,
                    PurchaseProductNode.provider == provider,
                    PurchaseProductNode.identity_key == identity,
                )
            )
        ).scalar_one_or_none()
        created = product is None
        if product is None:
            product = PurchaseProductNode(
                subject_id=subject_id,
                provider=provider,
                identity_key=identity,
                provider_item_id=provider_item_id,
                name=name,
                normalized_name=self._normalize_name(name),
                first_seen_at=observed_at,
                last_seen_at=observed_at,
            )
            self.session.add(product)
            await self.session.flush()
        else:
            product.name = name
            product.normalized_name = self._normalize_name(name)
            product.provider_item_id = provider_item_id or product.provider_item_id
            product.first_seen_at = min(
                self._as_utc(product.first_seen_at), self._as_utc(observed_at)
            )
            product.last_seen_at = max(
                self._as_utc(product.last_seen_at), self._as_utc(observed_at)
            )
        return product, created

    async def _occurrence(
        self, order_id: UUID, product_id: UUID
    ) -> PurchaseOccurrenceEdge | None:
        return (
            await self.session.execute(
                select(PurchaseOccurrenceEdge).where(
                    PurchaseOccurrenceEdge.order_id == order_id,
                    PurchaseOccurrenceEdge.product_id == product_id,
                )
            )
        ).scalar_one_or_none()

    async def _prune_expired(self, subject_id: UUID, now: datetime) -> int:
        result = await self.session.execute(
            delete(PurchaseOrderNode).where(
                PurchaseOrderNode.subject_id == subject_id,
                PurchaseOrderNode.retention_expires_at <= now,
            )
        )
        await self.session.execute(
            delete(PurchaseProductNode)
            .where(
                PurchaseProductNode.subject_id == subject_id,
                ~exists().where(
                    PurchaseOccurrenceEdge.product_id == PurchaseProductNode.id
                ),
            )
            .execution_options(synchronize_session=False)
        )
        await self.session.execute(
            delete(PurchaseCategoryNode)
            .where(
                PurchaseCategoryNode.subject_id == subject_id,
                ~exists().where(
                    ProductCategoryEdge.category_id == PurchaseCategoryNode.id
                ),
            )
            .execution_options(synchronize_session=False)
        )
        await self.session.execute(
            delete(PersonalRecommendationModel)
            .where(
                PersonalRecommendationModel.subject_id == subject_id,
                ~exists().where(PurchaseOrderNode.subject_id == subject_id),
            )
            .execution_options(synchronize_session=False)
        )
        return result.rowcount or 0  # type: ignore[attr-defined]

    @classmethod
    def _identity_key(cls, item_id: str | None, name: str) -> str:
        if item_id:
            return f"item:{item_id}"
        digest = hashlib.sha256(cls._normalize_name(name).encode()).hexdigest()[:32]
        return f"name:{digest}"

    @staticmethod
    def _normalize_name(name: str) -> str:
        return re.sub(r"\s+", " ", name.casefold()).strip()

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _utc_offset_minutes(value: datetime) -> int:
        offset = value.utcoffset()
        return round(offset.total_seconds() / 60) if offset is not None else 0
