import pytest

from app.commerce.query_guard import clarification_for_query, non_search_reason
from app.commerce.swiggy.models import ProductSearchResult
from app.commerce.swiggy.service import SwiggyService


@pytest.mark.parametrize(
    ("query", "reason"),
    [
        ("kya kar sakte ho", "capabilities"),
        ("What can you do?", "capabilities"),
        ("can you see Zepto?", "connection_status"),
        ("Zepto connected hai?", "connection_status"),
        ("Namaste", "greeting"),
        ("can you order amul", "ambiguous_brand"),
        ("Amul mangwa do", "ambiguous_brand"),
        ("Amul", "ambiguous_brand"),
        ("Amul milk 1 litre", None),
        ("milk", None),
        ("USB-C cable", None),
        ("dog treats", None),
        ("notebook and pens", None),
        ("Fortune oil milega?", None),
    ],
)
def test_non_search_reason(query: str, reason: str | None) -> None:
    assert non_search_reason(query) == reason


def test_brand_only_request_gets_a_specific_clarifying_question() -> None:
    assert clarification_for_query("can you order amul") == (
        "Amul ka kya chahiye—milk, butter, paneer, ya kuch aur? "
        "Pack size bhi bata dijiye."
    )
    assert clarification_for_query("Amul milk 1 litre") is None


@pytest.mark.asyncio
async def test_swiggy_service_rejects_conversation_before_adapter_call() -> None:
    class Adapter:
        async def search_products(self, query: str, address_id: str, offset: int) -> ProductSearchResult:
            raise AssertionError("provider adapter must not be called")

    service = SwiggyService(object(), object(), Adapter())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="conversational"):
        await service.search("kya kar sakte ho", "home", 0)
