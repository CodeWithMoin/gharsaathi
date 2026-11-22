"use client";

import Image from "next/image";
import { CaretLeft, CaretRight, Check, CheckCircle, CircleNotch, ClockCounterClockwise, Clock, Heart, ImageSquare, Minus, Plus, Sparkle } from "@phosphor-icons/react";
import { motion, useReducedMotion } from "framer-motion";
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";

import type { ProviderName, ShoppingBasket } from "./basket/BasketPanel";
import { activeIndexFromScroll, basketActionLabel, basketQuantity, optionPositionLabel, optionPrices, orderOptions, positionLabel, storeName, type BestPickBadge } from "./product-options.mjs";
import { groupBadge } from "./result-groups.mjs";

export type Product = {
  provider?: ProviderName;
  provider_label?: string;
  product_id: string;
  sku_id: string;
  spin_id?: string;
  name: string;
  brand?: string;
  pack_size?: string;
  /** Rupee numbers for basket lines; the ₹ strings to show come from the backend. */
  price: { mrp: number; offer_price: number; offer_display?: string; mrp_display?: string | null; saving_display?: string | null };
  available: boolean;
  image_url?: string;
  rating_value?: string;
  delivery_value?: string;
  delivery_unit?: string;
  /** Household annotations from the assistant's search: shown first, with a badge. */
  remembered?: string | null;
  preferred?: boolean | null;
  avoided?: boolean | null;
  /** Card number from the assistant's search; the model uses the same number. */
  position?: number | null;
};

type ProductOptionsProps = {
  label: string;
  products: Product[];
  commerceMode: "demo" | "live";
  addingSku: string | null;
  baskets: Partial<Record<ProviderName, ShoppingBasket>>;
  onAdd: (product: Product) => Promise<boolean>;
  onQuantity: (product: Product, quantity: number) => Promise<boolean>;
};

const MAX_QUANTITY = 50;
const STORE_DOT: Record<ProviderName, string> = { swiggy_instamart: "bg-accent", zepto: "bg-zepto" };
const BADGE_ICON: Record<BestPickBadge, typeof Sparkle> = { "You bought this before": ClockCounterClockwise, "Your usual": Heart, "Best match": Sparkle };
const focusRing = "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent";

function productImage(product: Product) {
  if (product.image_url) return product.image_url;
  if (product.name.toLocaleLowerCase().includes("rice")) return "/demo-products/basmati-rice.png";
  return null;
}

/** Native scroll-snap; this only tracks which card is current and scrolls to one. */
function useSnapCarousel(count: number) {
  const ref = useRef<HTMLUListElement>(null);
  const reduceMotion = useReducedMotion();
  const [active, setActive] = useState(0);
  const [overflowing, setOverflowing] = useState(false);
  const [atStart, setAtStart] = useState(true);
  const [atEnd, setAtEnd] = useState(true);

  useEffect(() => {
    const list = ref.current;
    if (!list) return;
    let frame = 0;
    const measure = () => {
      frame = 0;
      const maxScroll = list.scrollWidth - list.clientWidth;
      const offsets = Array.from(list.children, (child) => (child as HTMLElement).offsetLeft);
      setActive(activeIndexFromScroll(offsets, list.scrollLeft, maxScroll));
      setOverflowing(maxScroll > 1);
      setAtStart(list.scrollLeft <= 1);
      setAtEnd(list.scrollLeft >= maxScroll - 1);
    };
    const schedule = () => { if (!frame) frame = requestAnimationFrame(measure); };
    measure();
    list.addEventListener("scroll", schedule, { passive: true });
    const observer = new ResizeObserver(schedule);
    observer.observe(list);
    return () => {
      list.removeEventListener("scroll", schedule);
      observer.disconnect();
      if (frame) cancelAnimationFrame(frame);
    };
  }, [count]);

  const scrollToIndex = useCallback((index: number, smooth: boolean) => {
    const list = ref.current;
    if (!list || !list.children.length) return;
    const target = list.children[Math.max(0, Math.min(index, list.children.length - 1))] as HTMLElement;
    const first = list.children[0] as HTMLElement;
    list.scrollTo({ left: target.offsetLeft - first.offsetLeft, behavior: smooth && !reduceMotion ? "smooth" : "auto" });
  }, [reduceMotion]);

  return { ref, active, overflowing, atStart, atEnd, scrollToIndex };
}

function BasketControl({ product, quantity, primary, adding, addDisabled, locked, onAdd, onQuantity }: {
  product: Product;
  quantity: number;
  primary: boolean;
  adding: boolean;
  addDisabled: boolean;
  locked: boolean;
  onAdd: (product: Product) => Promise<boolean>;
  onQuantity: (product: Product, quantity: number) => Promise<boolean>;
}) {
  const reduceMotion = useReducedMotion();
  const [busy, setBusy] = useState(false);
  const canAdd = product.available && Boolean(product.spin_id);
  const enter = reduceMotion ? { opacity: 0 } : { opacity: 0, scale: 0.97, filter: "blur(2px)" };
  const shown = reduceMotion ? { opacity: 1 } : { opacity: 1, scale: 1, filter: "blur(0px)" };
  const transition = { duration: 0.16, ease: [0.23, 1, 0.32, 1] as const };

  const change = async (next: number) => {
    setBusy(true);
    try { await onQuantity(product, next); } finally { setBusy(false); }
  };

  if (quantity > 0) {
    return (
      <motion.div key="added" initial={enter} animate={shown} transition={transition} className="flex h-11 items-center rounded-full border border-success-border bg-success-soft text-success-strong">
        <button type="button" onClick={() => void change(quantity - 1)} disabled={busy || locked} aria-label={basketActionLabel(product, quantity === 1 ? "remove" : "decrease")} className={`grid size-11 shrink-0 place-items-center rounded-full transition-[transform,background-color] duration-150 hover:bg-success-border active:scale-[.94] disabled:opacity-40 ${focusRing}`}><Minus size={15} weight="bold" /></button>
        <span className="flex min-w-0 flex-1 items-center justify-center gap-1 text-xs font-semibold" aria-live="polite">
          {busy ? <CircleNotch size={15} className="animate-spin" aria-label="Updating basket" /> : <><Check size={13} weight="bold" aria-hidden />Added<span className="tabular-nums" aria-hidden>· {quantity}</span><span className="sr-only">, {quantity} in basket</span></>}
        </span>
        <button type="button" onClick={() => void change(quantity + 1)} disabled={busy || locked || quantity >= MAX_QUANTITY || !product.available} aria-label={basketActionLabel(product, "increase")} className={`grid size-11 shrink-0 place-items-center rounded-full transition-[transform,background-color] duration-150 hover:bg-success-border active:scale-[.94] disabled:opacity-40 ${focusRing}`}><Plus size={15} weight="bold" /></button>
      </motion.div>
    );
  }

  return (
    <motion.button key="add" initial={false} animate={shown} transition={transition} type="button" onClick={() => void onAdd(product)} disabled={!canAdd || addDisabled} aria-label={canAdd ? basketActionLabel(product, "add") : `${product.name} is unavailable`} className={`flex h-11 w-full items-center justify-center gap-1.5 rounded-full text-sm font-semibold transition-[transform,background-color] duration-150 active:scale-[.97] disabled:cursor-not-allowed ${focusRing} ${!canAdd ? "bg-surface-strong text-subtle" : primary ? "bg-accent text-on-accent hover:bg-accent-hover disabled:bg-border-strong" : "border border-border-strong bg-white text-ink-secondary hover:bg-canvas disabled:opacity-60"}`}>
      {adding ? <CircleNotch size={16} className="animate-spin" aria-label="Adding" /> : canAdd ? <><Plus size={15} weight="bold" aria-hidden />Add</> : "Unavailable"}
    </motion.button>
  );
}

function OptionCard({ product, number, total, badge, commerceMode, quantity, locked, addingSku, onAdd, onQuantity }: {
  product: Product;
  number: number;
  total: number;
  badge: BestPickBadge | null;
  commerceMode: "demo" | "live";
  quantity: number;
  locked: boolean;
  addingSku: string | null;
  onAdd: (product: Product) => Promise<boolean>;
  onQuantity: (product: Product, quantity: number) => Promise<boolean>;
}) {
  const image = productImage(product);
  const prices = optionPrices(product);
  const BadgeIcon = badge ? BADGE_ICON[badge] : null;
  const provider = product.provider ?? "swiggy_instamart";
  const eta = product.delivery_value ? `${product.delivery_value} ${product.delivery_unit ?? "min"}` : null;

  return (
    <article className={`flex h-full flex-col overflow-hidden rounded-[20px] border bg-surface shadow-[0_16px_36px_-32px_rgb(var(--shadow)/.7)] ${badge ? "border-accent-border" : "border-border-strong"}`}>
      <span className="sr-only">{optionPositionLabel(number, total)}{badge ? `, ${badge}` : ""}.</span>
      <div className="relative aspect-[16/11] w-full shrink-0 bg-surface-muted">
        {image ? <Image src={image} alt="" fill sizes="(min-width: 640px) 240px, 70vw" unoptimized={image.startsWith("http")} className="object-contain p-3" /> : <span className="grid size-full place-items-center text-subtle"><ImageSquare size={26} aria-hidden /></span>}
        <div className="absolute left-2.5 top-2.5 flex items-center gap-1.5" aria-hidden>
          <span className="grid size-6 place-items-center rounded-full bg-surface/95 text-[11px] font-bold tabular-nums text-ink shadow-[0_2px_8px_-4px_rgb(var(--shadow)/.5)]">{number}</span>
          {badge && BadgeIcon && <span className="flex h-6 items-center gap-1 rounded-full bg-accent px-2 text-[10px] font-semibold text-on-accent shadow-[0_2px_8px_-4px_rgb(var(--shadow)/.6)]"><BadgeIcon size={11} weight="fill" />{badge}</span>}
        </div>
      </div>

      <div className="flex flex-1 flex-col px-3 pb-3 pt-2.5">
        <div className="flex items-center gap-2 text-[10px]">
          <span className="flex items-center gap-1 rounded-full bg-surface-muted px-2 py-0.5 font-semibold text-ink-secondary"><i className={`size-1.5 rounded-full ${STORE_DOT[provider]}`} aria-hidden />{storeName(product, commerceMode)}</span>
          {eta && <span className="flex items-center gap-0.5 text-muted"><Clock size={11} aria-hidden />{eta}</span>}
          <span className={`ml-auto flex items-center gap-0.5 font-medium ${product.available ? "text-success" : "text-muted"}`}>{product.available && <CheckCircle size={11} weight="fill" aria-hidden />}{product.available ? "In stock" : "Out of stock"}</span>
        </div>

        <h4 className="mt-2 line-clamp-2 min-h-[2.6em] text-[13px] font-semibold leading-[1.3] text-ink" title={product.name}>{product.name}</h4>
        <p className="mt-0.5 truncate text-[11px] text-muted">{product.pack_size || "\u00a0"}</p>

        <div className="mt-2 flex flex-wrap items-baseline gap-x-1.5 gap-y-1">
          {prices.price ? <strong className="text-[17px] leading-none tabular-nums text-ink">{prices.price}</strong> : <span className="text-xs text-muted">Price shown in basket</span>}
          {prices.mrp && <s className="text-[11px] tabular-nums text-subtle"><span className="sr-only">MRP </span>{prices.mrp}</s>}
          {prices.saving && <span className="rounded-full bg-success-soft px-1.5 py-0.5 text-[10px] font-semibold tabular-nums text-success-strong">Save {prices.saving}</span>}
        </div>

        <div className="mt-auto pt-3">
          <BasketControl
            product={product}
            quantity={quantity}
            primary={number === 1}
            adding={addingSku === product.sku_id}
            addDisabled={addingSku !== null}
            locked={locked}
            onAdd={onAdd}
            onQuantity={onQuantity}
          />
        </div>
      </div>
    </article>
  );
}

export function ProductOptions({ label, products, commerceMode, addingSku, baskets, onAdd, onQuantity }: ProductOptionsProps) {
  const reduceMotion = useReducedMotion();
  const options = useMemo(() => orderOptions(products), [products]);
  const { ref, active, overflowing, atStart, atEnd, scrollToIndex } = useSnapCarousel(options.length);

  useEffect(() => { scrollToIndex(0, false); }, [products, scrollToIndex]);

  if (!options.length) {
    return <p className="w-fit max-w-full rounded-[18px] bg-surface px-4 py-3 text-[13px] leading-5 text-ink-secondary"><strong className="font-semibold">{label}:</strong> only options you asked to avoid were found. Try another brand or product.</p>;
  }

  const onKeyDown = (event: KeyboardEvent<HTMLUListElement>) => {
    if (event.target !== event.currentTarget) return;
    const next = event.key === "ArrowRight" ? active + 1 : event.key === "ArrowLeft" ? active - 1 : event.key === "Home" ? 0 : event.key === "End" ? options.length - 1 : null;
    if (next === null) return;
    event.preventDefault();
    // Keyboard steps are instant; animating them would feel slow.
    scrollToIndex(next, false);
  };

  const arrowClass = `grid size-8 place-items-center rounded-full border border-border-strong bg-surface text-ink-secondary transition-[transform,background-color,opacity] duration-150 hover:bg-white active:scale-[.94] disabled:cursor-default disabled:opacity-35 ${focusRing}`;

  return (
    <motion.section
      initial={reduceMotion ? { opacity: 0 } : { opacity: 0, transform: "translateY(12px)" }}
      animate={reduceMotion ? { opacity: 1 } : { opacity: 1, transform: "translateY(0px)" }}
      transition={{ duration: 0.24, ease: [0.23, 1, 0.32, 1] }}
      aria-roledescription="carousel"
      aria-label={`${label}, ${options.length} ${options.length === 1 ? "option" : "options"}`}
      className="min-w-0 max-w-full"
    >
      <div className="mb-2 flex min-h-8 items-center gap-2 px-1">
        <h3 className="min-w-0 truncate text-[11px] font-bold uppercase tracking-[.12em] text-accent-strong">{groupBadge(label, options.length)}</h3>
        {overflowing && <span className="ml-auto text-[11px] font-semibold tabular-nums text-muted" aria-hidden>{positionLabel(active, options.length)}</span>}
        {overflowing && (
          <div className="hidden items-center gap-1.5 sm:flex">
            <button type="button" onClick={() => scrollToIndex(active - 1, true)} disabled={atStart} aria-label={`Previous ${label} option`} className={arrowClass}><CaretLeft size={14} weight="bold" /></button>
            <button type="button" onClick={() => scrollToIndex(active + 1, true)} disabled={atEnd} aria-label={`Next ${label} option`} className={arrowClass}><CaretRight size={14} weight="bold" /></button>
          </div>
        )}
      </div>
      <div className="rounded-[22px] has-[ul:focus-visible]:ring-2 has-[ul:focus-visible]:ring-focus has-[ul:focus-visible]:ring-offset-2 has-[ul:focus-visible]:ring-offset-surface-muted">
        <ul
          ref={ref}
          tabIndex={overflowing ? 0 : -1}
          onKeyDown={onKeyDown}
          aria-label={`${label} options`}
          className="relative flex snap-x snap-mandatory gap-3 overflow-x-auto overscroll-x-contain pb-1 outline-none [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
        >
          {options.map(({ key, product, number, badge }) => (
            <li key={key} className="w-[min(78%,15.5rem)] shrink-0 snap-start sm:w-[calc((100%-1.5rem)/3)]">
              <OptionCard
                product={product}
                number={number}
                total={options.length}
                badge={badge}
                commerceMode={commerceMode}
                quantity={basketQuantity(baskets, product)}
                locked={["checkout_pending", "ordered"].includes(baskets[product.provider ?? "swiggy_instamart"]?.status ?? "")}
                addingSku={addingSku}
                onAdd={onAdd}
                onQuantity={onQuantity}
              />
            </li>
          ))}
        </ul>
      </div>
    </motion.section>
  );
}
