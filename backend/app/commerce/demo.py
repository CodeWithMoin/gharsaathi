"""Static commerce fixtures for safe local product-flow experimentation.

Nothing in this module performs network I/O. Demo mode exercises search,
baskets, deterministic fees and exact-bill approval without touching a user's
provider account or cart.
"""

from datetime import UTC, datetime

from app.commerce.swiggy.errors import SwiggyConfigurationError
from app.commerce.swiggy.models import (
    Address,
    AddressList,
    AddressResolution,
    ConnectionStatus,
    Pagination,
    Price,
    Product,
    ProductSearchResult,
    ToolDescription,
)
from app.commerce.zepto.errors import ZeptoConfigurationError
from app.commerce.zepto.models import (
    CapabilitySummary,
    ConnectionStatus as ZeptoConnectionStatus,
    ToolDescription as ZeptoToolDescription,
)
from app.shopping.basket import item_total_paise
from app.shopping.models import Basket, Charge, Quote


DEMO_ADDRESS_ID = "demo-home"

DEMO_PRODUCTS = [
    Product(product_id="demo-oil", sku_id="demo-sku-oil", spin_id="demo-spin-oil", name="Fortune Sunflower Oil", brand="Fortune", pack_size="1 L", price=Price(mrp=175, offer_price=155, unit_level_price="₹155/L"), available=True, rating_value="4.5"),
    Product(product_id="demo-atta", sku_id="demo-sku-atta", spin_id="demo-spin-atta", name="Aashirvaad Shudh Chakki Atta", brand="Aashirvaad", pack_size="5 kg", price=Price(mrp=310, offer_price=265, unit_level_price="₹53/kg"), available=True, rating_value="4.6"),
    Product(product_id="demo-tea", sku_id="demo-sku-tea", spin_id="demo-spin-tea", name="Tata Tea Gold", brand="Tata", pack_size="500 g", price=Price(mrp=299, offer_price=285), available=True, rating_value="4.4"),
    Product(product_id="demo-sugar", sku_id="demo-sku-sugar", spin_id="demo-spin-sugar", name="Madhur Pure Sugar", brand="Madhur", pack_size="1 kg", price=Price(mrp=55, offer_price=47), available=True, rating_value="4.3"),
    Product(product_id="demo-milk", sku_id="demo-sku-milk", spin_id="demo-spin-milk", name="Amul Taaza Homogenised Milk", brand="Amul", pack_size="1 L", price=Price(mrp=72, offer_price=69), available=True, rating_value="4.7"),
    Product(product_id="demo-rice", sku_id="demo-sku-rice", spin_id="demo-spin-rice", name="India Gate Everyday Basmati Rice", brand="India Gate", pack_size="5 kg", price=Price(mrp=850, offer_price=699), available=True, image_url="/demo-products/basmati-rice.png", rating_value="4.3"),
    Product(product_id="demo-detergent", sku_id="demo-sku-detergent", spin_id="demo-spin-detergent", name="Surf Excel Easy Wash Detergent", brand="Surf Excel", pack_size="1 kg", price=Price(mrp=245, offer_price=219), available=True, rating_value="4.5"),
    Product(product_id="demo-eggs", sku_id="demo-sku-eggs", spin_id="demo-spin-eggs", name="Fresh White Eggs", brand="Farm Fresh", pack_size="12 pieces", price=Price(mrp=110, offer_price=96), available=True, rating_value="4.2"),
]

_ALIASES = {
    "chai": "tea",
    "tel": "oil",
    "doodh": "milk",
    "chawal": "rice",
    "sabun": "detergent",
    "ande": "eggs",
}
_GENERIC_TERMS = {"monthly", "groceries", "grocery", "essentials", "breakfast", "regular"}

_DEMO_ZEPTO_OFFERS = {
    "demo-oil": (175, 151, 12),
    "demo-atta": (310, 272, 14),
    "demo-tea": (299, 285, 11),
    "demo-sugar": (55, 45, 13),
    "demo-milk": (72, 68, 10),
    "demo-rice": (825, 675, 11),
    "demo-detergent": (245, 225, 15),
    "demo-eggs": (110, 92, 12),
}


class DemoSwiggyService:
    async def connection_status(self) -> ConnectionStatus:
        return ConnectionStatus(connected=True, mode="demo")

    async def authorization_url(self) -> str:
        raise SwiggyConfigurationError("Swiggy OAuth is disabled while demo mode is active.")

    async def complete_authorization(self, code: str, state: str) -> None:
        del code, state
        raise SwiggyConfigurationError("Swiggy OAuth is disabled while demo mode is active.")

    async def reject_authorization(self, state: str) -> None:
        del state

    async def disconnect(self) -> None:
        return None

    async def list_tools(self) -> list[ToolDescription]:
        return [
            ToolDescription(
                name="search_products",
                description="Static local demo product search; performs no network call.",
                input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
            )
        ]

    async def addresses(self, page: int, page_size: int) -> AddressList:
        return AddressList(
            addresses=[
                Address(
                    id=DEMO_ADDRESS_ID,
                    tag="Home · Demo",
                    category="HOME",
                    address_line="12 Sample Street, HSR Layout, Bengaluru",
                )
            ],
            pagination=Pagination(page=page, page_size=page_size, total=1, total_pages=1),
            resolution=AddressResolution(
                default_address_id=DEMO_ADDRESS_ID,
                resolution_source="demo_fixture",
            ),
        )

    async def search(self, query: str, address_id: str, offset: int) -> ProductSearchResult:
        del address_id, offset
        words = {
            _ALIASES.get(word, word)
            for word in "".join(character.lower() if character.isalnum() else " " for character in query).split()
        }
        if not words or words & _GENERIC_TERMS:
            products = DEMO_PRODUCTS
        else:
            products = [
                product
                for product in DEMO_PRODUCTS
                if any(
                    word in f"{product.name} {product.brand or ''} {product.pack_size or ''}".lower()
                    for word in words
                )
            ]
        timed_products = [
            product.model_copy(update={"delivery_value": "18", "delivery_unit": "min"})
            for product in (products or DEMO_PRODUCTS[:4])
        ]
        return ProductSearchResult(products=timed_products)


class DemoQuoteProvider:
    name = "swiggy_instamart"

    async def quote(self, basket: Basket) -> Quote:
        item_total = item_total_paise(basket)
        charges = [Charge(label="Handling fee · demo", amount_paise=500)]
        if item_total < 49900:
            charges.append(Charge(label="Delivery fee · demo", amount_paise=2500))
        return Quote(
            provider=self.name,
            basket_id=basket.id,
            basket_revision=basket.revision,
            item_total_paise=item_total,
            charges=charges,
            payable_paise=item_total + sum(charge.amount_paise for charge in charges),
            eta_text="10–15 min · demo",
            quoted_at=datetime.now(UTC),
        )


class DemoZeptoQuoteProvider(DemoQuoteProvider):
    name = "zepto"


class DemoZeptoService:
    """A connection-only Zepto stand-in with no network-capable collaborators."""

    async def connection_status(self) -> ZeptoConnectionStatus:
        return ZeptoConnectionStatus(connected=True, mode="demo")

    async def authorization_url(self) -> str:
        raise ZeptoConfigurationError("Zepto OAuth is disabled while demo mode is active.")

    async def complete_authorization(
        self, code: str, state: str, issuer: str | None = None
    ) -> None:
        del code, state, issuer
        raise ZeptoConfigurationError("Zepto OAuth is disabled while demo mode is active.")

    async def reject_authorization(self, state: str) -> None:
        del state

    async def disconnect(self) -> None:
        return None

    async def list_tools(self) -> list[ZeptoToolDescription]:
        return []

    async def capabilities(self) -> CapabilitySummary:
        return CapabilitySummary(
            connected=True,
            catalogue_discovered=False,
            exposed_actions=["demo_connection"],
        )

    async def addresses(self) -> AddressList:
        return await DemoSwiggyService().addresses(1, 10)

    async def search(
        self, query: str, address_id: str, page_number: int = 0
    ) -> ProductSearchResult:
        result = await DemoSwiggyService().search(query, address_id, page_number)
        products = []
        for product in result.products:
            mrp, offer_price, eta = _DEMO_ZEPTO_OFFERS[product.product_id]
            products.append(
                product.model_copy(
                    update={
                        "sku_id": f"zepto-{product.sku_id}",
                        "spin_id": f"zepto-{product.spin_id}",
                        "price": Price(mrp=mrp, offer_price=offer_price),
                        "delivery_value": str(eta),
                        "delivery_unit": "min",
                    }
                )
            )
        return result.model_copy(update={"products": products})
