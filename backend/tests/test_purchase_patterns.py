from datetime import UTC, datetime, timedelta
import uuid

from app.memory.purchase_patterns import PurchaseObservation, mine_purchase_patterns


def observation(
    product_id: uuid.UUID,
    name: str,
    order_id: uuid.UUID,
    when: datetime,
    quantity: int = 1,
) -> PurchaseObservation:
    return PurchaseObservation(
        product_id=product_id,
        product_name=name,
        provider_item_id=str(product_id),
        order_id=order_id,
        ordered_at=when,
        quantity=quantity,
        local_weekday=when.weekday(),
        local_hour=when.hour,
    )


def test_discovers_temporal_cadence_quantity_and_basket_patterns() -> None:
    milk_id = uuid.uuid4()
    bread_id = uuid.uuid4()
    monday_morning = datetime(2026, 10, 5, 8, tzinfo=UTC)
    observations: list[PurchaseObservation] = []
    for index in range(3):
        order_id = uuid.uuid4()
        when = monday_morning + timedelta(days=index * 7)
        observations.extend(
            [
                observation(milk_id, "Milk 1 L", order_id, when, quantity=2),
                observation(bread_id, "Brown bread", order_id, when),
            ]
        )

    patterns = mine_purchase_patterns(
        observations,
        completed_order_count=6,
        now=monday_morning + timedelta(days=22),
        current_weekday=0,
        current_hour=8,
    )

    milk = next(item for item in patterns if item.product_id == milk_id)
    kinds = {item.kind for item in milk.evidence}
    assert kinds >= {
        "repeat_frequency",
        "repeat_cadence",
        "weekday_routine",
        "daypart_routine",
        "quantity_routine",
        "basket_affinity",
    }
    assert any(item.matches_current_context for item in milk.evidence)
    assert milk.related_products[0].product_id == bread_id
    assert milk.features["basket_affinity"] > 0


def test_two_repeated_orders_do_not_become_a_fake_usual() -> None:
    product_id = uuid.uuid4()
    observations = [
        observation(
            product_id,
            "Occasional snack",
            uuid.uuid4(),
            datetime(2026, 10, 1 + index, 14, tzinfo=UTC),
        )
        for index in range(2)
    ]

    assert (
        mine_purchase_patterns(
            observations,
            completed_order_count=5,
            now=datetime(2026, 10, 10, tzinfo=UTC),
            current_weekday=5,
            current_hour=14,
        )
        == []
    )
