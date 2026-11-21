"""Normalisation for the read-only Zepto order-history tool response.

The MCP catalogue describes ``list_order_history`` as accepting ``limit`` and
``pageNumber``.  Its response has no cursor, so pagination is derived from the
response's total ``count`` and the exact request parameters used for the call.
All monetary values remain integer paise at this provider boundary.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.commerce.zepto.errors import ZeptoSchemaError


class ZeptoOrderedProduct(BaseModel):
    """One product summary returned with a historical order."""

    name: str
    count: int


class ZeptoOrderHistoryOrder(BaseModel):
    """A read-only, provider-normalised Zepto order summary."""

    id: str
    code: str
    formatted_status: str
    grand_total_paise: int
    item_quantity_count: int
    products: list[ZeptoOrderedProduct]
    total_arrival_time_minutes: int | None
    total_refund_paise: int


class ZeptoOrderHistoryPagination(BaseModel):
    page_number: int
    limit: int
    total: int
    total_pages: int
    has_more: bool


class ZeptoOrderHistory(BaseModel):
    orders: list[ZeptoOrderHistoryOrder]
    pagination: ZeptoOrderHistoryPagination


class ZeptoOrderProduct(BaseModel):
    model_config = ConfigDict(strict=True, populate_by_name=True)

    id: str
    product_name: str = Field(alias="productName")
    brand: str
    pack_size: str = Field(alias="packsize")
    image_path: str = Field(alias="imagePath")
    status: str
    quantity_ordered: int = Field(alias="quantityOrdered", ge=0)
    quantity_fulfilled: int = Field(alias="quantityFulfilled", ge=0)
    quantity_rejected: int = Field(alias="quantityRejected", ge=0)
    unit_mrp_paise: int = Field(alias="unitMRP", ge=0)
    unit_selling_price_paise: int = Field(alias="unitSellingPrice", ge=0)
    total_mrp_paise: int = Field(alias="totalMRP", ge=0)
    total_final_selling_price_paise: int = Field(
        alias="totalFinalSellingPrice", ge=0
    )
    total_refund_paise: int = Field(alias="totalRefundAmount", ge=0)


class ZeptoOrderShipment(BaseModel):
    model_config = ConfigDict(strict=True, populate_by_name=True)

    shipment_id: str = Field(alias="shipmentId")
    shipment_code: str = Field(alias="shipmentCode")
    status: str
    formatted_status: str = Field(alias="formattedStatus")
    placed_time: str = Field(alias="placedTime")
    delivery_time: str | None = Field(alias="deliveryTime")
    arrived_time: str | None = Field(alias="arrivedTime")
    committed_eta_minutes: int = Field(alias="committedEtaInMins", ge=0)
    total_arrival_time_minutes: int | None = Field(
        alias="totalArrivalTimeInMinutes", ge=0
    )
    shipment_weight_grams: int = Field(alias="shipmentWeightInGms", ge=0)
    products: list[ZeptoOrderProduct]


class ZeptoBillSummary(BaseModel):
    model_config = ConfigDict(strict=True, populate_by_name=True)

    item_total_paise: int = Field(alias="itemTotal", ge=0)
    total_bill_paise: int = Field(alias="totalBill", ge=0)
    total_saved_paise: int = Field(alias="totalSaved", ge=0)


class ZeptoOrderDetail(BaseModel):
    """Provider-confirmed read-only detail; all money remains integer paise."""

    model_config = ConfigDict(strict=True, populate_by_name=True)

    id: str
    order_id: str = Field(alias="orderId")
    code: str
    formatted_status: str = Field(alias="formattedStatus")
    order_type: str = Field(alias="orderType")
    placed_time: str = Field(alias="placedTime")
    user_address_formatted: str = Field(alias="userAddressFormatted")
    item_quantity_count: int = Field(alias="itemQuantityCount", ge=0)
    grand_total_paise: int = Field(alias="grandTotalAmount", ge=0)
    grand_total_pre_discount_paise: int = Field(
        alias="grandTotalAmountPreDiscount", ge=0
    )
    total_refund_paise: int = Field(alias="totalRefundAmount", ge=0)
    wallet_balance_used_paise: int = Field(alias="walletBalanceUsed", ge=0)
    is_invoice_available: bool = Field(alias="isInvoiceAvailable")
    invoice_url: str | None = Field(alias="invoiceUrl")
    is_eligible_for_rating: bool = Field(alias="isEligibleForRating")
    bill_summary: ZeptoBillSummary = Field(alias="billSummary")
    shipments: list[ZeptoOrderShipment]


def _is_int(value: object) -> bool:
    """Treat booleans as invalid even though Python considers them integers."""

    return type(value) is int


def _positive_int(value: object, field: str) -> int:
    if not _is_int(value) or value < 1:
        raise ZeptoSchemaError(f"Zepto returned an invalid {field}.")
    return value


def _non_negative_int(value: object, field: str) -> int:
    if not _is_int(value) or value < 0:
        raise ZeptoSchemaError(f"Zepto returned an invalid {field}.")
    return value


def _string(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ZeptoSchemaError(f"Zepto returned an invalid {field}.")
    return value


def _parse_product(value: object) -> ZeptoOrderedProduct:
    if not isinstance(value, dict):
        raise ZeptoSchemaError("Zepto returned an invalid order product.")
    return ZeptoOrderedProduct(
        name=_string(value.get("name"), "order product name"),
        count=_non_negative_int(value.get("count"), "order product count"),
    )


def _parse_order(value: object) -> ZeptoOrderHistoryOrder:
    if not isinstance(value, dict):
        raise ZeptoSchemaError("Zepto returned an invalid order-history order.")

    raw_products = value.get("productsNamesAndCounts")
    if not isinstance(raw_products, list):
        raise ZeptoSchemaError("Zepto returned an invalid order product list.")

    raw_arrival_time = value.get("totalArrivalTimeInMinutes")
    if raw_arrival_time is not None and not _is_int(raw_arrival_time):
        raise ZeptoSchemaError("Zepto returned an invalid order arrival time.")

    return ZeptoOrderHistoryOrder(
        id=_string(value.get("id"), "order id"),
        code=_string(value.get("code"), "order code"),
        formatted_status=_string(value.get("formattedStatus"), "order status"),
        grand_total_paise=_non_negative_int(
            value.get("grandTotalAmount"), "order grand total"
        ),
        item_quantity_count=_non_negative_int(
            value.get("itemQuantityCount"), "order item quantity count"
        ),
        products=[_parse_product(product) for product in raw_products],
        total_arrival_time_minutes=raw_arrival_time,
        total_refund_paise=_non_negative_int(
            value.get("totalRefundAmount"), "order refund total"
        ),
    )


def parse_order_history(
    value: dict[str, Any], *, limit: int = 10, page_number: int = 1
) -> ZeptoOrderHistory:
    """Parse the exact live ``list_order_history`` response shape.

    A schema mismatch invalidates the complete response.  In particular, this
    parser never skips malformed orders: presenting a partial purchase history
    could imply that it is complete when it is not.
    """

    request_limit = _positive_int(limit, "order-history limit")
    request_page_number = _positive_int(page_number, "order-history page number")
    if not isinstance(value, dict):
        raise ZeptoSchemaError("Zepto returned an invalid order-history response.")

    total = _non_negative_int(value.get("count"), "order-history count")
    raw_orders = value.get("orders")
    if not isinstance(raw_orders, list):
        raise ZeptoSchemaError("Zepto returned an invalid order-history order list.")
    if len(raw_orders) > request_limit or len(raw_orders) > total:
        raise ZeptoSchemaError("Zepto returned inconsistent order-history pagination.")

    orders = [_parse_order(order) for order in raw_orders]
    total_pages = (total + request_limit - 1) // request_limit
    return ZeptoOrderHistory(
        orders=orders,
        pagination=ZeptoOrderHistoryPagination(
            page_number=request_page_number,
            limit=request_limit,
            total=total,
            total_pages=total_pages,
            has_more=request_page_number < total_pages,
        ),
    )


def parse_order_detail(value: dict[str, Any]) -> ZeptoOrderDetail:
    """Parse the read-only ``get_order_detail`` response verified live."""

    try:
        return ZeptoOrderDetail.model_validate(value)
    except ValidationError as exc:
        raise ZeptoSchemaError("Zepto returned an invalid order detail.") from exc
