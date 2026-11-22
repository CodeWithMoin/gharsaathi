import pytest

from app.shopping.query_relax import relaxed_query


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("Paper Boat Swing Coconut Water Drink 270", "Paper Boat Swing"),
        ("Paper Boat Swing Coconut Water Drink 270 ml", "Paper Boat Swing"),
        ("Amul Sweet Lassi 1 pack (200 ml)", "Amul Sweet Lassi"),
        ("Diet Coke Can 330 ml", "Diet Coke Can"),
        ("milk", None),
        ("Amul Taaza", None),
        ("500 ml", None),
    ],
)
def test_relaxed_query(query: str, expected: str | None) -> None:
    assert relaxed_query(query) == expected
