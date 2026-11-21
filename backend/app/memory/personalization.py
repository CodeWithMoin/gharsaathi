from dataclasses import dataclass
from uuid import UUID

from app.commerce.swiggy.models import Product
from app.database.models import MemoryItem


@dataclass(frozen=True)
class SearchPersonalization:
    provider_query: str
    applied_memory_ids: list[UUID]
    avoid_terms: list[str]


def personalize_search_query(
    query: str, memories: list[MemoryItem]
) -> SearchPersonalization:
    query_folded = query.casefold()
    additions: list[str] = []
    applied: list[UUID] = []
    avoid_terms: list[str] = []

    for memory in memories:
        values = {
            str(key): str(value).strip()
            for key, value in memory.value.items()
            if isinstance(value, (str, int, float)) and str(value).strip()
        }
        category = values.get("product_category")
        category_matches = bool(category and category.casefold() in query_folded)
        applied_current = False

        if memory.memory_type == "brand_preference" and category_matches:
            applied_current = _append_hint(
                values.get("brand"), query_folded, additions
            )
        elif memory.memory_type == "pack_size_preference" and category_matches:
            applied_current = _append_hint(
                values.get("pack_size"), query_folded, additions
            )
        elif memory.memory_type == "product_avoidance":
            terms = [values.get("product"), values.get("brand")]
            new_terms = [term.casefold() for term in terms if term]
            if new_terms:
                avoid_terms.extend(term for term in new_terms if term not in avoid_terms)
                applied_current = True

        if applied_current:
            applied.append(memory.id)

    provider_query = " ".join([query, *additions]).strip()
    return SearchPersonalization(provider_query, applied, avoid_terms)


def excludes_product(product: Product, avoid_terms: list[str]) -> bool:
    haystack = " ".join(
        part for part in (product.name, product.brand or "") if part
    ).casefold()
    return any(term in haystack for term in avoid_terms)


def _append_hint(
    value: str | None, query_folded: str, additions: list[str]
) -> bool:
    if not value:
        return False
    value_folded = value.casefold()
    if value_folded in query_folded or any(
        value_folded == existing.casefold() for existing in additions
    ):
        return False
    additions.append(value)
    return True
