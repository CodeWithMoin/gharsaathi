import re
import unicodedata

from app.commerce.swiggy.models import Product, ProductSearchResult


DEFAULT_RESULT_LIMIT = 6
_QUERY_FILLER = {
    "a",
    "an",
    "and",
    "aur",
    "buy",
    "can",
    "find",
    "for",
    "get",
    "me",
    "mujhe",
    "of",
    "order",
    "please",
    "show",
    "the",
    "you",
}


def refine_search_result(
    query: str,
    result: ProductSearchResult,
    *,
    limit: int = DEFAULT_RESULT_LIMIT,
) -> ProductSearchResult:
    """Rank provider results and remove obvious fuzzy misses.

    Providers may return a fixed-size semantic batch even for an exact brand
    query. For a short query, exact token matches are authoritative when any
    exist (for example, ``Axe`` must not show Denver). For broader or translated
    queries we preserve fuzzy provider matches, but still rank and cap them.
    """

    return result.model_copy(
        update={
            "products": _rank_products(query, result.products, limit),
            "similar_products": _rank_products(
                query, result.similar_products, limit
            ),
        }
    )


def _rank_products(query: str, products: list[Product], limit: int) -> list[Product]:
    if not products or limit <= 0:
        return []

    query_tokens = _meaningful_tokens(query)
    scored = [(_score(product, query_tokens), index, product) for index, product in enumerate(products)]

    # A one- or two-token request is usually a brand or compact product term.
    # If the provider returned exact matches, discard unrelated semantic filler.
    exact = [entry for entry in scored if query_tokens and entry[0][0] == len(query_tokens)]
    candidates = exact if len(query_tokens) <= 2 and exact else scored
    candidates.sort(key=lambda entry: (-entry[0][1], -entry[0][0], entry[1]))
    return [entry[2] for entry in candidates[:limit]]


def _score(product: Product, query_tokens: list[str]) -> tuple[int, int]:
    name_tokens = set(_tokens(product.name))
    brand_tokens = set(_tokens(product.brand or ""))
    pack_tokens = set(_tokens(product.pack_size or ""))
    searchable = name_tokens | brand_tokens | pack_tokens
    matched = sum(token in searchable for token in query_tokens)
    weighted = sum(
        5 if token in brand_tokens else 3 if token in name_tokens else 1
        for token in query_tokens
        if token in searchable
    )
    return matched, weighted


def _meaningful_tokens(value: str) -> list[str]:
    return [token for token in _tokens(value) if token not in _QUERY_FILLER]


def _tokens(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.findall(r"[\w]+", normalized, flags=re.UNICODE)
