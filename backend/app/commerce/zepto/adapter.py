from typing import Any

from app.commerce.swiggy.models import (
    Address,
    AddressList,
    AddressResolution,
    Pagination,
    Price,
    Product,
    ProductSearchResult,
)
from app.commerce.zepto.errors import ZeptoSchemaError, ZeptoUnrecognisedResponse


def _payload(value: dict[str, Any]) -> dict[str, Any]:
    data = value.get("data")
    return data if isinstance(data, dict) else value


def parse_addresses(value: dict[str, Any]) -> AddressList:
    payload = _payload(value)
    raw_addresses = payload.get("addresses")
    if not isinstance(raw_addresses, list):
        raise ZeptoSchemaError("Zepto returned an invalid saved-address response.")
    addresses: list[Address] = []
    for raw in raw_addresses:
        if not isinstance(raw, dict):
            continue
        address_id = raw.get("id")
        address_line = raw.get("addressLine")
        if not isinstance(address_id, str) or not isinstance(address_line, str):
            continue
        label = raw.get("label")
        addresses.append(
            Address(
                id=address_id,
                address_line=address_line,
                tag=label if isinstance(label, str) else None,
                category=label.upper() if isinstance(label, str) else None,
            )
        )
    default = addresses[0].id if len(addresses) == 1 else None
    return AddressList(
        addresses=addresses,
        pagination=Pagination(
            page=1,
            page_size=max(len(addresses), 1),
            total=len(addresses),
            total_pages=1 if addresses else 0,
        ),
        resolution=AddressResolution(
            default_address_id=default,
            resolution_source="zepto_saved_addresses",
            needs_user_clarification=len(addresses) != 1,
            candidate_address_ids=[address.id for address in addresses],
        ),
    )


def _product_nodes(value: object) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if isinstance(value.get("productVariantId"), str):
            found.append(value)
        else:
            for child in value.values():
                found.extend(_product_nodes(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_product_nodes(child))
    return found


def _is_empty_result(payload: dict[str, Any]) -> bool:
    """A well-formed answer with no products: ``products: []`` or groups of them."""

    products = payload.get("products")
    if isinstance(products, list):
        return not products
    groups = payload.get("groups")
    if isinstance(groups, list):
        return all(
            isinstance(group, dict)
            and isinstance(group.get("products"), list)
            and not group["products"]
            for group in groups
        )
    return False


def parse_products(value: dict[str, Any]) -> ProductSearchResult:
    """Parse a Zepto search answer.

    Raises ``ZeptoUnrecognisedResponse`` when no product could be read and the
    answer is not a well-formed empty result (a text envelope, unknown keys,
    items without the expected ids or prices).
    """

    payload = _payload(value)
    products: list[Product] = []
    seen: set[tuple[str, str]] = set()
    for raw in _product_nodes(payload):
        product_variant_id = raw.get("productVariantId")
        store_product_id = raw.get("storeProductId")
        name = raw.get("name")
        price = raw.get("price")
        if not (
            isinstance(product_variant_id, str)
            and isinstance(store_product_id, str)
            and isinstance(name, str)
            and isinstance(price, (int, float))
        ):
            continue
        key = (product_variant_id, store_product_id)
        if key in seen:
            continue
        seen.add(key)
        mrp = raw.get("mrp")
        available_quantity = raw.get("availableQuantity")
        product_id = raw.get("id")
        products.append(
            Product(
                product_id=product_id if isinstance(product_id, str) else product_variant_id,
                sku_id=store_product_id,
                spin_id=product_variant_id,
                name=name,
                pack_size=raw.get("packSize") if isinstance(raw.get("packSize"), str) else None,
                price=Price(
                    mrp=(mrp if isinstance(mrp, (int, float)) else price) / 100,
                    offer_price=price / 100,
                ),
                available=not isinstance(available_quantity, (int, float)) or available_quantity > 0,
                image_url=raw.get("imageUrl") if isinstance(raw.get("imageUrl"), str) else None,
            )
        )
    if not products and not _is_empty_result(payload):
        raise ZeptoUnrecognisedResponse("Zepto returned a search answer GharSaathi could not read.")
    return ProductSearchResult(products=products)
