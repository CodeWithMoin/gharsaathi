from app.commerce.relevance import refine_search_result
from app.commerce.swiggy.models import Price, Product, ProductSearchResult


def product(name: str, brand: str, index: int) -> Product:
    return Product(
        product_id=f"product-{index}",
        sku_id=f"sku-{index}",
        spin_id=f"spin-{index}",
        name=name,
        brand=brand,
        pack_size="150 ml",
        price=Price(mrp=250, offer_price=199),
        available=True,
    )


def test_exact_short_brand_query_removes_provider_fuzzy_misses() -> None:
    result = ProductSearchResult(
        products=[
            product("Denver Black Code Deodorant", "Denver", 1),
            product("Axe Dark Temptation Body Spray", "Axe", 2),
            product("Wild Stone Code Body Perfume", "Wild Stone", 3),
            product("Axe Pulse Deodorant", "Axe", 4),
        ]
    )

    refined = refine_search_result("axe", result)

    assert [item.brand for item in refined.products] == ["Axe", "Axe"]


def test_results_are_ranked_and_capped_without_dropping_fuzzy_only_matches() -> None:
    products = [product(f"Desk accessory {index}", "Generic", index) for index in range(10)]
    result = ProductSearchResult(products=products)

    refined = refine_search_result("work from home supplies", result)

    assert len(refined.products) == 6
    assert refined.products[0].product_id == "product-0"


def test_two_token_query_prefers_products_matching_both_tokens() -> None:
    result = ProductSearchResult(
        products=[
            product("Denver Deodorant", "Denver", 1),
            product("Axe Deodorant", "Axe", 2),
            product("Axe Shower Gel", "Axe", 3),
        ]
    )

    refined = refine_search_result("axe deodorant", result)

    assert [item.name for item in refined.products] == ["Axe Deodorant"]
