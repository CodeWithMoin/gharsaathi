"use client";

import Image from "next/image";
import { CheckCircle, CircleNotch, Clock, ImageSquare, Plus, Star } from "@phosphor-icons/react";
import { motion } from "framer-motion";
import { useEffect, useMemo, useRef, useState } from "react";
import { A11y, EffectCards, Mousewheel } from "swiper/modules";
import { Swiper, SwiperSlide } from "swiper/react";
import type { Swiper as SwiperInstance } from "swiper";

import "swiper/css";
import "swiper/css/effect-cards";

import type { ProviderName } from "./basket/BasketPanel";

export type Product = {
  provider?: ProviderName;
  provider_label?: string;
  product_id: string;
  sku_id: string;
  spin_id?: string;
  name: string;
  brand?: string;
  pack_size?: string;
  price: { mrp: number; offer_price: number };
  available: boolean;
  image_url?: string;
  rating_value?: string;
  delivery_value?: string;
  delivery_unit?: string;
};

type ProductComparisonProps = {
  products: Product[];
  commerceMode: "demo" | "live";
  addingSku: string | null;
  onAdd: (product: Product) => Promise<boolean>;
};

type ProductGroup = { key: string; product: Product; offers: Product[] };
const MAX_COMPARISON_GROUPS = 6;

function comparisonKey(product: Product) {
  const normalizedName = product.name.normalize("NFKD").toLocaleLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
  const normalizedPack = (product.pack_size ?? "").toLocaleLowerCase().replace(/\s+/g, "").trim();
  return `${normalizedName}|${normalizedPack}`;
}

export function groupProductsForComparison(products: Product[]): ProductGroup[] {
  const groups = new Map<string, ProductGroup>();
  for (const product of products) {
    const key = comparisonKey(product);
    const existing = groups.get(key);
    if (existing) existing.offers.push(product);
    else groups.set(key, { key, product, offers: [product] });
  }
  return Array.from(groups.values()).slice(0, MAX_COMPARISON_GROUPS);
}

function providerName(product: Product, commerceMode: "demo" | "live") {
  const label = product.provider === "zepto" ? "Zepto" : "Swiggy";
  return commerceMode === "demo" ? `${label} demo` : label;
}

function productImage(product: Product) {
  if (product.image_url) return product.image_url;
  if (product.name.toLocaleLowerCase().includes("rice")) return "/demo-products/basmati-rice.png";
  return null;
}

function deliveryMinutes(product: Product) {
  if (!product.delivery_value) return null;
  const value = Number.parseFloat(product.delivery_value);
  if (!Number.isFinite(value)) return null;
  return (product.delivery_unit ?? "min").toLocaleLowerCase().startsWith("hour") ? Math.round(value * 60) : Math.round(value);
}

function decisionCopy(offers: Product[], commerceMode: "demo" | "live") {
  const available = offers.filter((offer) => offer.available);
  if (!available.length) return { title: "Currently unavailable", detail: "Try another pack size or product." };
  if (available.length === 1 && offers.length > 1) return { title: `Available on ${providerName(available[0], commerceMode)}`, detail: "The other store is currently unavailable." };

  const sorted = available.slice().sort((a, b) => a.price.offer_price - b.price.offer_price);
  const difference = sorted.length > 1 ? sorted[1].price.offer_price - sorted[0].price.offer_price : 0;
  const fastest = available
    .map((offer) => ({ offer, minutes: deliveryMinutes(offer) }))
    .filter((entry): entry is { offer: Product; minutes: number } => entry.minutes !== null)
    .sort((a, b) => a.minutes - b.minutes);

  if (difference > 0) {
    const detail = fastest.length > 1 && fastest[1].minutes > fastest[0].minutes
      ? `${providerName(fastest[0].offer, commerceMode)} is about ${fastest[1].minutes - fastest[0].minutes} min faster.`
      : "Final fees are checked with the basket.";
    return { title: `Save ₹${difference} on ${providerName(sorted[0], commerceMode)}`, detail };
  }
  if (fastest.length > 1 && fastest[1].minutes > fastest[0].minutes) {
    return { title: "Same item price at both stores", detail: `${providerName(fastest[0].offer, commerceMode)} is about ${fastest[1].minutes - fastest[0].minutes} min faster.` };
  }
  return { title: "Same item price at both stores", detail: "Choose the store you prefer. Final fees come later." };
}

function ProductDecisionCard({ group, commerceMode, addingSku, onAdd }: {
  group: ProductGroup;
  commerceMode: "demo" | "live";
  addingSku: string | null;
  onAdd: (product: Product) => Promise<boolean>;
}) {
  const { product, offers } = group;
  const image = productImage(offers.find((offer) => offer.image_url) ?? product);
  const decision = decisionCopy(offers, commerceMode);
  const availablePrices = offers.filter((offer) => offer.available).map((offer) => offer.price.offer_price);
  const bestPrice = availablePrices.length ? Math.min(...availablePrices) : Number.POSITIVE_INFINITY;
  const hasPriceDifference = new Set(availablePrices).size > 1;
  const sortedOffers = offers.slice().sort((a, b) => Number(b.available) - Number(a.available) || a.price.offer_price - b.price.offer_price);

  return (
    <article className="overflow-hidden rounded-[20px] border border-[#e3d3c7] bg-[#fffdf9] shadow-[0_16px_36px_-32px_rgba(74,43,27,.7)]">
      <header className="flex items-center gap-3 px-3.5 py-3">
        <div className="relative size-[76px] shrink-0 overflow-hidden rounded-[15px] bg-[#f3ece5]">
          {image ? <Image src={image} alt={`${product.name} pack`} fill sizes="76px" unoptimized={image.startsWith("http")} className="object-contain p-1.5" /> : <span className="grid size-full place-items-center text-[#b49d90]" aria-label="Product image unavailable"><ImageSquare size={23} /></span>}
        </div>
        <div className="min-w-0 flex-1">
          {product.brand && <p className="text-[9px] font-semibold tracking-[.1em] text-[#c6531d]">{product.brand.toUpperCase()}</p>}
          <h3 className="mt-0.5 text-[15px] font-semibold leading-snug text-[#35241c]">{product.name}</h3>
          <p className="mt-0.5 text-[10px] text-[#806f65]">{product.pack_size || "Pack size unavailable"}</p>
        </div>
      </header>

      {offers.length > 1 && <div className="mx-2.5 rounded-[14px] bg-[#edf5ed] px-3 py-2.5 text-[#2f673a]"><p className="text-[13px] font-semibold">{decision.title}</p><p className="mt-0.5 text-[10px] leading-4 text-[#52775a]">{decision.detail}</p></div>}

      <div className={`${offers.length > 1 ? "mt-3" : ""} divide-y divide-[#eee3da] border-t border-[#eee3da]`}>
        {sortedOffers.map((offer) => {
          const isLower = offer.available && hasPriceDifference && offer.price.offer_price === bestPrice;
          const canAdd = offer.available && Boolean(offer.spin_id);
          const minutes = deliveryMinutes(offer);
          return (
            <div key={`${offer.provider ?? "swiggy_instamart"}-${offer.product_id}-${offer.sku_id}`} className={`grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2.5 px-3.5 py-2.5 ${isLower ? "bg-[#fff8f1]" : ""}`}>
              <div className="min-w-0">
                <div className="flex min-w-0 items-center gap-2"><strong className="truncate text-[13px] font-semibold text-[#49352b]">{providerName(offer, commerceMode)}</strong>{isLower && <span className="shrink-0 text-[9px] font-semibold text-[#397144]">Lower item price</span>}</div>
                <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1">
                  <strong className="text-[17px] leading-none text-[#35241c]">₹{offer.price.offer_price}</strong>
                  {offer.price.mrp > offer.price.offer_price && <s className="text-[11px] text-[#9d8c81]">₹{offer.price.mrp}</s>}
                  {minutes !== null && <span className="flex items-center gap-1 text-[10px] text-[#75645a]"><Clock size={11} /> {minutes} min</span>}
                  {offer.rating_value && <span className="flex items-center gap-1 text-[10px] text-[#75645a]"><Star size={10} weight="fill" /> {offer.rating_value}</span>}
                </div>
                <span className={`mt-1 flex items-center gap-1 text-[9px] font-medium ${offer.available ? "text-[#397144]" : "text-[#8b7770]"}`}>{offer.available && <CheckCircle size={11} weight="fill" />}{offer.available ? "Available" : "Unavailable"}</span>
              </div>
              <button type="button" onClick={() => void onAdd(offer)} disabled={!canAdd || addingSku !== null} aria-label={`Add ${offer.name} from ${providerName(offer, commerceMode)}`} className={`flex min-h-9 min-w-[76px] shrink-0 items-center justify-center gap-1.5 rounded-full px-3.5 text-xs font-semibold transition active:scale-[.98] disabled:cursor-not-allowed disabled:bg-[#cbb9ad] ${isLower ? "bg-[#c6531d] text-white hover:bg-[#b94a18]" : "border border-[#d9c7bb] bg-white text-[#6c5042] hover:bg-[#fff8f2]"}`}>
                {addingSku === offer.sku_id ? <CircleNotch size={15} className="animate-spin" aria-label="Adding" /> : <><Plus size={14} weight="bold" />{canAdd ? "Add" : "Unavailable"}</>}
              </button>
            </div>
          );
        })}
      </div>
      {offers.length > 1 && <p className="border-t border-[#eee3da] px-3.5 py-2 text-[9px] leading-4 text-[#8a776b]">Item prices only. Exact fees come with the basket.</p>}
    </article>
  );
}

export function ProductComparison({ products, commerceMode, addingSku, onAdd }: ProductComparisonProps) {
  const groups = useMemo(() => groupProductsForComparison(products), [products]);
  const [activeIndex, setActiveIndex] = useState(0);
  const swiperRef = useRef<SwiperInstance | null>(null);

  useEffect(() => {
    setActiveIndex(0);
    swiperRef.current?.slideTo(0, 0);
  }, [products]);

  const addAndAdvance = async (product: Product, index: number) => {
    const canAdvance = index < groups.length - 1;
    if (canAdvance) swiperRef.current?.slideNext();
    const added = await onAdd(product);
    if (!added && canAdvance) swiperRef.current?.slidePrev();
    return added;
  };

  if (!groups.length) return null;

  if (groups.length === 1) {
    return (
      <motion.div
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.2, ease: "easeOut" }}
        className="min-w-0 max-w-full"
        data-product-card
      >
        <ProductDecisionCard
          group={groups[0]}
          commerceMode={commerceMode}
          addingSku={addingSku}
          onAdd={onAdd}
        />
      </motion.div>
    );
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.24, ease: "easeOut" }}
      className="min-w-0 max-w-full overflow-hidden px-1 pb-1"
    >
      <style>{`
        .product-card-swiper {
          width: calc(100% - 14px);
          margin-inline: auto;
          overflow: visible !important;
        }
        .product-card-swiper .swiper-wrapper {
          align-items: flex-start;
        }
        .product-card-swiper .swiper-slide {
          height: auto;
          border-radius: 20px;
          background: #fffdf9;
        }
        .product-card-swiper .swiper-slide-shadow {
          border-radius: 20px;
          background: rgba(74, 43, 27, .08) !important;
        }
      `}</style>

      <div data-product-deck aria-live="polite" aria-label={`Product ${activeIndex + 1} of ${groups.length}`}>
        <Swiper
          onSwiper={(swiper) => { swiperRef.current = swiper; }}
          onSlideChange={(swiper) => setActiveIndex(swiper.activeIndex)}
          modules={[EffectCards, Mousewheel, A11y]}
          effect="cards"
          cardsEffect={{ perSlideOffset: 5, perSlideRotate: 1.25, rotate: true, slideShadows: true }}
          grabCursor={groups.length > 1 && addingSku === null}
          allowTouchMove={groups.length > 1 && addingSku === null}
          threshold={3}
          shortSwipes
          longSwipes
          longSwipesMs={140}
          longSwipesRatio={0.06}
          resistanceRatio={0.55}
          speed={220}
          loop={false}
          rewind={false}
          simulateTouch
          followFinger
          touchMoveStopPropagation
          preventClicks
          preventClicksPropagation
          mousewheel={{ forceToAxis: true, sensitivity: 1.2, releaseOnEdges: true, thresholdDelta: 6 }}
          watchOverflow
          autoHeight
          className="product-card-swiper touch-pan-y"
          data-product-card
        >
          {groups.map((group, index) => (
            <SwiperSlide key={group.key}>
              <ProductDecisionCard
                group={group}
                commerceMode={commerceMode}
                addingSku={addingSku}
                onAdd={(product) => addAndAdvance(product, index)}
              />
            </SwiperSlide>
          ))}
        </Swiper>
      </div>

      {groups.length > 1 && <div className="mt-3 flex justify-center gap-1.5" aria-label={`Product ${activeIndex + 1} of ${groups.length}`}>{groups.map((group, index) => <button key={group.key} type="button" onClick={() => swiperRef.current?.slideTo(index)} aria-label={`Show product ${index + 1}`} aria-current={index === activeIndex ? "true" : undefined} className={`h-1.5 rounded-full transition-[width,background-color] ${index === activeIndex ? "w-5 bg-[#c6531d]" : "w-1.5 bg-[#d9c7bb]"}`} />)}</div>}
    </motion.div>
  );
}
