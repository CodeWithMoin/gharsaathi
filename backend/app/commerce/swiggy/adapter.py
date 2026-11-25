import logging
from typing import Any
from urllib.parse import urlparse

from app.commerce.swiggy.client import SwiggyMcpClient
from app.commerce.swiggy.errors import SwiggySchemaError
from app.commerce.swiggy.models import (
    Address,
    AddressList,
    AddressResolution,
    Pagination,
    Price,
    Product,
    ProductSearchResult,
)

logger = logging.getLogger(__name__)


class SwiggyInstamartAdapter:
    def __init__(self, client: SwiggyMcpClient) -> None:
        self.client = client

    async def addresses(self, page: int = 1, page_size: int = 10) -> AddressList:
        payload = await self.client.call_tool(
            "get_addresses", {"page": page, "pageSize": page_size}
        )
        return parse_addresses(payload)

    async def search_products(
        self, query: str, address_id: str, offset: int = 0
    ) -> ProductSearchResult:
        payload = await self.client.call_tool(
            "search_products",
            {"query": query, "addressId": address_id, "offset": offset},
        )
        return parse_products(payload)


def _data(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        raise SwiggySchemaError("Swiggy returned an invalid data envelope.")
    return data


def parse_addresses(payload: dict[str, Any]) -> AddressList:
    data = _data(payload)
    raw_addresses = data.get("addresses", [])
    if not isinstance(raw_addresses, list):
        raise SwiggySchemaError("Swiggy returned an invalid addresses list.")
    addresses: list[Address] = []
    for item in raw_addresses:
        if not isinstance(item, dict):
            raise SwiggySchemaError("Swiggy returned an invalid address item.")
        identifier = item.get("id")
        address_line = item.get("addressLine")
        if not isinstance(identifier, str) or not isinstance(address_line, str):
            raise SwiggySchemaError("A Swiggy address is missing required fields.")
        addresses.append(
            Address(
                id=identifier,
                address_line=address_line,
                category=_optional_str(item.get("addressCategory")),
                tag=_optional_str(item.get("addressTag")),
            )
        )

    raw_pagination = data.get("pagination", {})
    raw_resolution = data.get("resolution", {})
    if not isinstance(raw_pagination, dict) or not isinstance(raw_resolution, dict):
        raise SwiggySchemaError("Swiggy returned invalid address metadata.")
    pagination = Pagination(
        page=_integer(raw_pagination.get("page"), 1),
        page_size=_integer(raw_pagination.get("pageSize"), 10),
        total=_integer(raw_pagination.get("total"), len(addresses)),
        total_pages=_integer(raw_pagination.get("totalPages"), 0),
        has_more=raw_pagination.get("hasMore") is True,
    )
    candidates = raw_resolution.get("candidateAddressIds", [])
    resolution = AddressResolution(
        default_address_id=_optional_str(raw_resolution.get("defaultAddressId")),
        resolution_source=_optional_str(raw_resolution.get("resolutionSource")),
        needs_user_clarification=raw_resolution.get("needsUserClarification") is True,
        candidate_address_ids=[item for item in candidates if isinstance(item, str)]
        if isinstance(candidates, list)
        else [],
    )
    return AddressList(
        addresses=addresses, pagination=pagination, resolution=resolution
    )


def parse_products(payload: dict[str, Any]) -> ProductSearchResult:
    data = _data(payload)
    products = _flatten_product_groups(data.get("products", []))
    similar = _flatten_product_groups(data.get("similarProducts", []))
    return ProductSearchResult(
        products=products,
        similar_products=similar,
        next_offset=_optional_str(data.get("nextOffset")),
    )


def _flatten_product_groups(raw_groups: object) -> list[Product]:
    if not isinstance(raw_groups, list):
        raise SwiggySchemaError("Swiggy returned an invalid products list.")
    products: list[Product] = []
    first_error: SwiggySchemaError | None = None
    for group in raw_groups:
        # One malformed product must not hide the rest of the results; only a
        # response where nothing parses is a schema error.
        try:
            products.extend(_group_products(group))
        except SwiggySchemaError as exc:
            first_error = first_error or exc
    if first_error is not None:
        if not products:
            raise first_error
        logger.warning("event=swiggy_product_skipped reason=%s", first_error)
    return products


def _group_products(group: object) -> list[Product]:
    if not isinstance(group, dict):
        raise SwiggySchemaError("Swiggy returned an invalid product item.")
    product_id = _required_str(group, "productId")
    parent_product_id = _optional_str(group.get("parentProductId"))
    variations = group.get("variations", [])
    if not isinstance(variations, list):
        raise SwiggySchemaError("A Swiggy product has invalid variations.")
    products: list[Product] = []
    for variation in variations:
        if not isinstance(variation, dict):
            raise SwiggySchemaError("Swiggy returned an invalid product variation.")
        price = variation.get("price")
        if not isinstance(price, dict):
            raise SwiggySchemaError("A Swiggy product is missing its price.")
        rating = variation.get("rating", {})
        sla = variation.get("sla", {})
        products.append(
            Product(
                product_id=product_id,
                parent_product_id=parent_product_id,
                sku_id=_required_str(variation, "skuId"),
                spin_id=_required_str(variation, "spinId"),
                name=_required_str(variation, "displayName"),
                brand=_optional_str(variation.get("brandName"))
                or _optional_str(group.get("brand")),
                pack_size=_optional_str(variation.get("quantityDescription")),
                price=Price(
                    mrp=_number(price, "mrp"),
                    offer_price=_number(price, "offerPrice"),
                    unit_level_price=_optional_str(price.get("unitLevelPrice")),
                ),
                available=variation.get("isInStockAndAvailable") is True,
                image_url=_optional_https_url(variation.get("imageUrl")),
                rating_value=_dict_str(rating, "value"),
                rating_count=_dict_str(rating, "count"),
                delivery_value=_dict_str(sla, "value"),
                delivery_unit=_dict_str(sla, "unit"),
            )
        )
    return products


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise SwiggySchemaError(f"Swiggy response is missing {key}.")
    return value


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_https_url(value: object) -> str | None:
    text = _optional_str(value)
    if text is None:
        return None
    parsed = urlparse(text)
    return text if parsed.scheme == "https" and bool(parsed.hostname) else None


def _number(payload: dict[str, Any], key: str) -> float:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SwiggySchemaError(f"Swiggy response is missing numeric {key}.")
    return float(value)


def _integer(value: object, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _dict_str(value: object, key: str) -> str | None:
    return _optional_str(value.get(key)) if isinstance(value, dict) else None
