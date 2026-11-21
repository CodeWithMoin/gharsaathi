from datetime import UTC, datetime, timedelta
import uuid

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.commerce.swiggy.models import OrderItem, OrderList, OrderSummary
from app.database.base import Base
from app.database.models import (
    MemoryPermission,
    MemorySubject,
    PersonalRecommendationModel,
    PurchaseOccurrenceEdge,
    PurchaseOrderNode,
    PurchaseProductNode,
    ReorderSuggestionFeedback,
    SubjectType,
)
from app.memory.errors import MemoryPermissionError
from app.memory.purchase_graph import PurchaseGraphService


@pytest.fixture
def graph_db() -> Session:
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection: object, _: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def order(subject_id: uuid.UUID, order_id: str) -> PurchaseOrderNode:
    now = datetime.now(UTC)
    return PurchaseOrderNode(
        subject_id=subject_id,
        provider="swiggy",
        provider_order_id=order_id,
        status="delivered",
        occurred_at=now,
        provider_updated_at=now,
        local_weekday=now.weekday(),
        local_hour=now.hour,
        utc_offset_minutes=0,
        total_paise=12500,
        is_active=False,
        provenance_hash="a" * 64,
        retention_expires_at=now + timedelta(days=365),
    )


def product(subject_id: uuid.UUID, identity: str) -> PurchaseProductNode:
    now = datetime.now(UTC)
    return PurchaseProductNode(
        subject_id=subject_id,
        provider="swiggy",
        identity_key=identity,
        provider_item_id=identity,
        name="Example atta 5 kg",
        normalized_name="example atta 5 kg",
        first_seen_at=now,
        last_seen_at=now,
    )


def test_purchase_edges_cannot_cross_people(graph_db: Session) -> None:
    alice = MemorySubject(subject_type=SubjectType.PERSON, external_id="alice")
    bob = MemorySubject(subject_type=SubjectType.PERSON, external_id="bob")
    graph_db.add_all([alice, bob])
    graph_db.flush()

    alice_order = order(alice.id, "order-a")
    bob_product = product(bob.id, "product-b")
    graph_db.add_all([alice_order, bob_product])
    graph_db.flush()

    graph_db.add(
        PurchaseOccurrenceEdge(
            subject_id=alice.id,
            order_id=alice_order.id,
            product_id=bob_product.id,
            quantity=1,
            observed_at=datetime.now(UTC),
            source="provider_order_history",
        )
    )
    with pytest.raises(IntegrityError):
        graph_db.commit()


def test_purchase_graph_does_not_store_sensitive_provider_fields() -> None:
    column_names = {
        column.name
        for table in (
            PurchaseOrderNode.__table__,
            PurchaseProductNode.__table__,
            PurchaseOccurrenceEdge.__table__,
        )
        for column in table.columns
    }
    assert not {
        "address",
        "address_line",
        "phone",
        "payment_method",
        "raw_payload",
        "store_name",
    } & column_names


class AsyncSessionShim:
    """Small awaitable wrapper for exercising async services with stdlib SQLite."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, value: object) -> None:
        self.session.add(value)

    async def execute(self, statement: object) -> object:
        return self.session.execute(statement)  # type: ignore[arg-type]

    async def flush(self) -> None:
        self.session.flush()

    async def commit(self) -> None:
        self.session.commit()


def actor_with_history_permission(graph_db: Session, external_id: str) -> MemorySubject:
    actor = MemorySubject(subject_type=SubjectType.PERSON, external_id=external_id)
    graph_db.add(actor)
    graph_db.flush()
    graph_db.add(
        MemoryPermission(
            subject_id=actor.id,
            data_category="purchase_history",
            purpose="proactive_reorder",
            allowed=True,
            retention_days=365,
            sharing_scope="agent_and_user",
            user_control="view_edit_delete",
            confirmed_at=datetime.now(UTC),
        )
    )
    graph_db.commit()
    return actor


def history_order(
    order_id: str, when: datetime, status: str = "delivered"
) -> OrderSummary:
    return OrderSummary(
        order_id=order_id,
        status=status,
        current_status=status,
        history_status=status,
        created_at=when,
        updated_at=when,
        item_count=1,
        total_paise=12500,
        order_type="INSTAMART",
        is_active=False,
        store_name="Example Store",
        items=[OrderItem(name="Example atta 5 kg", quantity=1, item_id="atta-5")],
    )


@pytest.mark.asyncio
async def test_import_is_consent_gated_idempotent_and_generates_safe_suggestion(
    graph_db: Session,
) -> None:
    actor = actor_with_history_permission(graph_db, "person-1")

    now = datetime.now(UTC)
    history = OrderList(
        orders=[
            history_order("order-1", now - timedelta(days=55)),
            history_order("order-2", now - timedelta(days=30)),
            history_order("order-3", now - timedelta(days=5)),
            history_order("order-cancelled", now - timedelta(days=2), "cancelled"),
        ]
    )
    service = PurchaseGraphService(AsyncSessionShim(graph_db))  # type: ignore[arg-type]
    first = await service.import_orders(actor, history)
    second = await service.import_orders(actor, history)

    assert first.imported_orders == 4
    assert first.imported_products == 1
    assert first.imported_occurrences == 4
    assert second.imported_orders == 0
    assert second.imported_products == 0
    assert second.imported_occurrences == 0
    edge_count = graph_db.scalar(select(func.count(PurchaseOccurrenceEdge.id)))
    assert edge_count == 4

    suggestions = await service.suggestions(actor, now=now)
    assert len(suggestions) == 1
    suggestion = suggestions[0]
    assert suggestion.provider == "swiggy"
    assert suggestion.purchase_count == 3
    assert suggestion.model_version == "personal-hybrid-logistic-v1"
    assert {item.kind for item in suggestion.patterns} >= {
        "repeat_frequency",
        "repeat_cadence",
        "quantity_routine",
    }
    assert suggestion.requires_approval is True
    assert suggestion.assumes_current_stock is False
    assert "ranking_features" not in suggestion.model_dump()
    assert "running out" not in suggestion.reason.casefold()
    assert "Want to review current options?" in suggestion.reason
    assert "Current options check karun?" in suggestion.voice_prompt

    initial_score = suggestion.score
    await service.review_suggestion(actor, suggestion.product_id)
    learned = await service.suggestions(actor, now=now)
    assert learned[0].score > initial_score
    await service.dismiss_suggestion(actor, suggestion.product_id)
    assert await service.suggestions(actor, now=now) == []
    model = graph_db.scalar(
        select(PersonalRecommendationModel).where(
            PersonalRecommendationModel.subject_id == actor.id
        )
    )
    assert model is not None
    assert model.training_examples == 2

    for stored_order in graph_db.scalars(select(PurchaseOrderNode)).all():
        stored_order.retention_expires_at = now - timedelta(seconds=1)
    graph_db.commit()
    assert await service.suggestions(actor, now=now) == []
    assert graph_db.scalar(select(func.count(PurchaseOrderNode.id))) == 0
    assert graph_db.scalar(select(func.count(PurchaseProductNode.id))) == 0
    assert graph_db.scalar(select(func.count(ReorderSuggestionFeedback.id))) == 0
    assert graph_db.scalar(select(func.count(PersonalRecommendationModel.id))) == 0
    await service.purge(actor)
    permission = graph_db.scalar(
        select(MemoryPermission).where(MemoryPermission.subject_id == actor.id)
    )
    assert permission is not None
    assert permission.allowed is False
    assert permission.revoked_at is not None


@pytest.mark.asyncio
async def test_import_requires_explicit_purchase_history_permission(
    graph_db: Session,
) -> None:
    actor = MemorySubject(subject_type=SubjectType.PERSON, external_id="no-consent")
    graph_db.add(actor)
    graph_db.commit()
    service = PurchaseGraphService(AsyncSessionShim(graph_db))  # type: ignore[arg-type]

    with pytest.raises(MemoryPermissionError):
        await service.import_orders(actor, OrderList(orders=[]))


@pytest.mark.asyncio
async def test_reimport_reconciles_removed_order_items(graph_db: Session) -> None:
    actor = actor_with_history_permission(graph_db, "reconcile-person")
    service = PurchaseGraphService(AsyncSessionShim(graph_db))  # type: ignore[arg-type]
    when = datetime.now(UTC) - timedelta(days=3)
    original = history_order("changing-order", when)
    await service.import_orders(actor, OrderList(orders=[original]))

    corrected = history_order("changing-order", when)
    corrected.items = [
        OrderItem(name="Different rice 5 kg", quantity=1, item_id="rice-5")
    ]
    await service.import_orders(actor, OrderList(orders=[corrected]))

    edges = graph_db.scalars(select(PurchaseOccurrenceEdge)).all()
    products = graph_db.scalars(select(PurchaseProductNode)).all()
    assert len(edges) == 1
    assert [item.name for item in products] == ["Different rice 5 kg"]
