"""Content-free diagnostics and tolerated variations for the Zepto COD preview."""

import copy
import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

import pytest

from app.commerce.zepto.checkout import (
    ZeptoPreviewCheckFailed,
    ZeptoPreviewRefused,
    quote_from_cod_preview,
)
from app.commerce.zepto.diagnostics import PREVIEW_FAILURE_EVENT, payload_shape
from app.commerce.zepto.errors import ZeptoSchemaError
from app.commerce.zepto.quote_provider import ZeptoQuoteProvider
from app.shopping.errors import ApprovalRefused, ProviderUnavailable
from app.shopping.models import Basket, BasketLine, ProductRef


NOW = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)

PII = [
    "Priya Sharma",
    "+91 98765 43210",
    "9876543210",
    "Flat 4B, Sunshine Apartments, Koramangala",
    "560034",
    "priya@example.com",
    "Axe Dark Temptation Deo",
    "Get 10% off with Example Bank",
    "Leave at the door, call Priya",
]
# Distinctive amounts so any leaked price is visible in the log text.
PRICES = ["17237", "2913", "4411", "1913", "19150", "24561", "73"]


def pii_basket() -> Basket:
    return Basket(
        id="basket_pii",
        household_id="household_pii",
        provider="zepto",
        address_id="addr_priya_home_9876543210",
        revision=2,
        created_at=NOW,
        updated_at=NOW,
        lines=[
            BasketLine(
                ref=ProductRef(
                    provider="zepto",
                    product_id="prod_axe",
                    variant_id="pvid_axe_secret",
                    sku_id="spid_axe_secret",
                ),
                name="Axe Dark Temptation Deo",
                unit_price_paise=17237,
                quantity=1,
            )
        ],
    )


def pii_preview() -> dict[str, Any]:
    """A reconciled preview (17237 + 2913 + 4411 = 24561) full of PII-like text."""

    return {
        "data": {
            "deliverable": True,
            "isPreview": True,
            "requiresConfirmation": True,
            "paymentMethod": "COD",
            "customerName": "Priya Sharma",
            "customerPhone": "+91 98765 43210",
            "email": "priya@example.com",
            "deliveryInstructions": "Leave at the door, call Priya",
            "deliveryAddress": {
                "id": "addr_priya_home_9876543210",
                "label": "Priya Sharma",
                "addressLine": "Flat 4B, Sunshine Apartments, Koramangala",
                "pincode": "560034",
                "phone": "9876543210",
            },
            "items": [
                {
                    "name": "Axe Dark Temptation Deo",
                    "price": 17237,
                    "mrp": 24561,
                    "productVariantId": "pvid_axe_secret",
                    "storeProductId": "spid_axe_secret",
                    "quantity": 1,
                }
            ],
            # A dynamic key derived from user data must be masked in the shape.
            "slotsByAddress": {"Flat 4B, Sunshine Apartments, Koramangala": "9876543210"},
            "subTotal": 17237,
            "deliveryFee": 2913,
            "deliveryFeeDiscount": 0,
            "deliveryFeePreDiscount": 2913,
            "packagingFee": 4411,
            "discount": 0,
            "taxes": None,
            "otherFees": [],
            "toPayAmount": 24561,
            "totalItems": 1,
            "etaInMinutes": 73,
            "freeDelivery": {
                "isUnlocked": False,
                "message": "Get 10% off with Example Bank",
                "minimumOrderValue": 19150,
                "remainingAmountToUnlock": 1913,
            },
        }
    }


class FakeClient:
    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses

    async def call_tool(
        self, name: str, arguments: dict[str, Any], *, retry_on_auth: bool = True
    ) -> dict[str, Any]:
        value = self.responses[name]
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)


def provider_for(preview: object, **overrides: Any) -> ZeptoQuoteProvider:
    responses = {
        "select_saved_address": {"success": True},
        "update_cart": {"success": True},
        "create_order": preview,
        **overrides,
    }
    return ZeptoQuoteProvider(FakeClient(responses), clock=lambda: NOW)


def diagnostic_records(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if f"event={PREVIEW_FAILURE_EVENT}" in record.getMessage()
    ]


def field(record: str, name: str) -> str:
    match = re.search(rf"\b{name}=(\S+)", record)
    assert match, f"{name} missing from diagnostic"
    return match.group(1)


def assert_content_free(text: str) -> None:
    scrubbed = re.sub(r"correlation_id=\S+|Ref \w+", "", text)
    for value in [*PII, "pvid_axe_secret", "spid_axe_secret", "addr_priya_home"]:
        assert value not in scrubbed
    for amount in PRICES:
        assert not re.search(rf"(?<![\w.]){amount}(?![\w.])", scrubbed), amount


def test_pii_fixture_is_a_valid_reconciled_quote() -> None:
    quote = quote_from_cod_preview(pii_basket(), pii_preview(), NOW)

    assert quote.payable_paise == 24561


MUTATIONS = {
    "payable.reconciliation": lambda d: d.__setitem__("toPayAmount", 24561 + 4411),
    "freeDelivery.progress": lambda d: d["freeDelivery"].__setitem__("remainingAmountToUnlock", 17),
    "freeDelivery.isUnlocked": lambda d: d["freeDelivery"].__setitem__("isUnlocked", "Priya Sharma"),
    "isPreview": lambda d: d.pop("isPreview"),
    "items[].quantity": lambda d: d["items"][0].__setitem__("quantity", "9876543210"),
    "subTotal": lambda d: d.__setitem__("subTotal", "17237"),
    "otherFees[].label": lambda d: d.__setitem__("otherFees", [{"label": "", "amount": 73}]),
}


@pytest.mark.parametrize("step", sorted(MUTATIONS))
async def test_invalid_preview_logs_step_and_shape_but_never_values(
    step: str, caplog: pytest.LogCaptureFixture
) -> None:
    preview = pii_preview()
    MUTATIONS[step](preview["data"])
    caplog.set_level(logging.WARNING)

    with pytest.raises(ProviderUnavailable) as error:
        await provider_for(preview).quote(pii_basket())

    [record] = diagnostic_records(caplog)
    assert field(record, "stage") == "parse_preview"
    assert field(record, "step") == step
    assert field(record, "outcome") == "invalid"
    shape = json.loads(field(record, "shape"))
    assert shape["data.items"] == "array[1]"
    assert shape["data.deliveryAddress.addressLine"] == "str"
    assert shape["data.slotsByAddress.<key>"] in {"str", "str(numeric)"}
    assert_content_free(record)
    assert_content_free(str(error.value))
    assert "Nothing was ordered" in str(error.value)
    assert field(record, "correlation_id")[:8] in str(error.value)


async def test_reconciliation_failure_names_the_gap_field_without_amounts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    preview = pii_preview()
    preview["data"]["toPayAmount"] = 24561 - 4411
    caplog.set_level(logging.WARNING)

    with pytest.raises(ProviderUnavailable, match="charges did not add up"):
        await provider_for(preview).quote(pii_basket())

    [record] = diagnostic_records(caplog)
    facts = json.loads(field(record, "facts"))
    assert facts["computed_vs_payable"] == "higher"
    assert "packagingFee" in facts["gap_equals_fields"]
    assert_content_free(record)


async def test_item_mismatch_is_refused_and_logged_with_counts_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    preview = pii_preview()
    preview["data"]["items"][0]["storeProductId"] = "spid_other_9876543210"
    caplog.set_level(logging.WARNING)

    with pytest.raises(ApprovalRefused, match="does not exactly match"):
        await provider_for(preview).quote(pii_basket())

    [record] = diagnostic_records(caplog)
    assert field(record, "step") == "items.match"
    assert field(record, "outcome") == "refused"
    facts = json.loads(field(record, "facts"))
    assert facts["variant_only_matches"] == 1
    assert facts["matched_identifiers"] == 0
    assert "spid_other" not in record
    assert_content_free(record)


async def test_text_instead_of_json_is_reported_as_text_envelope(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    text = {"content_text": "Order preview for Priya Sharma, 9876543210: ₹245.61"}

    with pytest.raises(ProviderUnavailable, match="as text instead of structured data"):
        await provider_for(text).quote(pii_basket())

    [record] = diagnostic_records(caplog)
    assert field(record, "step") == "envelope.text"
    assert json.loads(field(record, "shape")) == {"$": "object", "content_text": "str"}
    assert_content_free(record)


async def test_failure_before_the_preview_names_the_tool_stage(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    provider = provider_for(
        pii_preview(),
        update_cart=ZeptoSchemaError("Arguments do not match Zepto's live update_cart schema"),
    )

    with pytest.raises(ProviderUnavailable, match="update cart step failed"):
        await provider.quote(pii_basket())

    [record] = diagnostic_records(caplog)
    assert field(record, "stage") == "update_cart"
    assert json.loads(field(record, "shape")) == {}


def test_shape_masks_dynamic_keys_and_merges_array_types() -> None:
    shape = payload_shape(
        {"items": [{"a": 1}, {"a": None}, {"a": 2.0}], "9876543210": "x", "ok": " "}
    )

    assert shape == {
        "$": "object",
        "<key>": "str",
        "items": "array[3]",
        "items[]": "object",
        "items[].a": "float(integral)|int|null",
        "ok": "str(empty)",
    }


# --- tolerated variations -------------------------------------------------


def _data(preview: dict[str, Any]) -> dict[str, Any]:
    return preview["data"]


VARIATIONS = {
    "integral_floats": lambda d: (
        d.update(subTotal=17237.0, toPayAmount=24561.0, totalItems=1.0),
        d["items"][0].update(quantity=1.0, price=17237.0),
    ),
    "address_id_key_variant": lambda d: d.__setitem__(
        "deliveryAddress", {"addressId": "addr_priya_home_9876543210"}
    ),
    "same_id_under_both_keys": lambda d: d["deliveryAddress"].__setitem__(
        "addressId", "addr_priya_home_9876543210"
    ),
    "address_without_display_fields": lambda d: (
        d["deliveryAddress"].pop("label"),
        d["deliveryAddress"].__setitem__("addressLine", None),
    ),
    "item_without_name_or_price": lambda d: (
        d["items"][0].pop("name"),
        d["items"][0].__setitem__("price", None),
    ),
    "total_items_absent": lambda d: d.pop("totalItems"),
    "other_fees_absent": lambda d: d.pop("otherFees"),
    "eta_absent": lambda d: d.pop("etaInMinutes"),
    "unknown_extra_fields": lambda d: d.update(newField={"nested": [1, "x"]}, flags=[]),
    "delivery_fee_pre_discount_null": lambda d: d.__setitem__("deliveryFeePreDiscount", None),
}


@pytest.mark.parametrize("variation", sorted(VARIATIONS))
def test_benign_variations_still_produce_the_provider_total(variation: str) -> None:
    preview = pii_preview()
    VARIATIONS[variation](_data(preview))

    quote = quote_from_cod_preview(pii_basket(), preview, NOW)

    assert quote.payable_paise == 24561
    assert quote.item_total_paise == 17237


def test_null_optional_fees_count_as_zero_and_must_reconcile() -> None:
    preview = pii_preview()
    data = _data(preview)
    data.update(packagingFee=None, discount=None, deliveryFeeDiscount=None, toPayAmount=17237 + 2913)

    quote = quote_from_cod_preview(pii_basket(), preview, NOW)

    assert quote.payable_paise == 17237 + 2913
    data["toPayAmount"] = 24561
    with pytest.raises(ZeptoPreviewCheckFailed) as error:
        quote_from_cod_preview(pii_basket(), preview, NOW)
    assert error.value.step == "payable.reconciliation"


@pytest.mark.parametrize("value", [None, "absent"])
def test_missing_free_delivery_progress_omits_the_offer(value: object) -> None:
    preview = pii_preview()
    if value == "absent":
        _data(preview).pop("freeDelivery")
    else:
        _data(preview)["freeDelivery"] = None

    quote = quote_from_cod_preview(pii_basket(), preview, NOW)

    assert [offer.kind for offer in quote.offers] == []


def test_unlocked_free_delivery_without_message_uses_a_neutral_title() -> None:
    preview = pii_preview()
    data = _data(preview)
    data.update(deliveryFee=0, deliveryFeeDiscount=2913, toPayAmount=17237 + 4411)
    data["freeDelivery"] = {
        "isUnlocked": True,
        "minimumOrderValue": 9900,
        "remainingAmountToUnlock": 0,
    }

    quote = quote_from_cod_preview(pii_basket(), preview, NOW)

    assert quote.offers[0].title == "Free delivery"
    assert quote.offers[0].applied is True


# --- safety invariants still refuse ---------------------------------------


REFUSALS: dict[str, tuple[Any, type[Exception], str]] = {
    "numeric_string_money": (lambda d: d.__setitem__("toPayAmount", "24561"), ZeptoPreviewCheckFailed, "toPayAmount"),
    "fractional_money": (lambda d: d.__setitem__("deliveryFee", 2913.5), ZeptoPreviewCheckFailed, "deliveryFee"),
    "negative_money": (lambda d: d.__setitem__("packagingFee", -1), ZeptoPreviewCheckFailed, "packagingFee"),
    "rupees_not_paise": (
        lambda d: d.update(subTotal=172, deliveryFee=29, deliveryFeePreDiscount=29, packagingFee=44, toPayAmount=245, freeDelivery=None),
        ZeptoPreviewCheckFailed,
        "subTotal.scale",
    ),
    "missing_is_preview": (lambda d: d.pop("isPreview"), ZeptoPreviewCheckFailed, "isPreview"),
    "confirmed_not_preview": (lambda d: d.__setitem__("isPreview", False), ZeptoPreviewCheckFailed, "isPreview"),
    "missing_confirmation_boundary": (
        lambda d: d.pop("requiresConfirmation"), ZeptoPreviewCheckFailed, "requiresConfirmation"
    ),
    "integral_float_wrong_quantity": (
        lambda d: d["items"][0].__setitem__("quantity", 2.0), ZeptoPreviewRefused, "items.match"
    ),
    "string_quantity": (
        lambda d: d["items"][0].__setitem__("quantity", "1"), ZeptoPreviewCheckFailed, "items[].quantity"
    ),
    "conflicting_address_ids": (
        lambda d: d["deliveryAddress"].__setitem__("addressId", "addr_other"),
        ZeptoPreviewRefused,
        "deliveryAddress.id",
    ),
    "address_without_id": (
        lambda d: d.__setitem__("deliveryAddress", {"label": "Home"}), ZeptoPreviewRefused, "deliveryAddress.id"
    ),
    "present_total_items_mismatch": (
        lambda d: d.__setitem__("totalItems", 2), ZeptoPreviewRefused, "totalItems"
    ),
    "malformed_free_delivery": (
        lambda d: d.__setitem__("freeDelivery", "unlocked"), ZeptoPreviewCheckFailed, "freeDelivery"
    ),
    "malformed_other_fees": (
        lambda d: d.__setitem__("otherFees", {"label": "x"}), ZeptoPreviewCheckFailed, "otherFees"
    ),
    "inconsistent_delivery_fee": (
        lambda d: d.__setitem__("deliveryFeeDiscount", 1), ZeptoPreviewCheckFailed, "deliveryFee.consistency"
    ),
}


@pytest.mark.parametrize("case", sorted(REFUSALS))
def test_real_mismatches_still_refuse_with_a_step(case: str) -> None:
    mutate, error_type, step = REFUSALS[case]
    preview = pii_preview()
    mutate(_data(preview))

    with pytest.raises(error_type) as error:
        quote_from_cod_preview(pii_basket(), preview, NOW)

    assert error.value.step == step
