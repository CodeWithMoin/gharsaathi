"use client";

import { CheckCircle, CircleNotch, Clock, Plus, Scales } from "@phosphor-icons/react";
import { motion, useReducedMotion } from "framer-motion";

import type { ProviderName, ShoppingBasket } from "./basket/BasketPanel";
import { comparisonTitle, unitPriceText, type Comparison } from "./comparison-card.mjs";
import type { Product } from "./product-comparison";
import { basketActionLabel, basketQuantity, optionPrices, storeName } from "./product-options.mjs";

const STORE_DOT: Record<ProviderName, string> = { swiggy_instamart: "bg-accent", zepto: "bg-zepto" };
const focusRing = "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent";

/** Side-by-side comparison from compare_options; every amount and badge is a backend string. */
export function ComparisonCard({ comparison, commerceMode, addingSku, baskets, onAdd, onHide }: {
  comparison: Comparison;
  commerceMode: "demo" | "live";
  addingSku: string | null;
  baskets: Partial<Record<ProviderName, ShoppingBasket>>;
  onAdd: (product: Product) => Promise<boolean>;
  onHide: () => void;
}) {
  const reduceMotion = useReducedMotion();
  const { columns } = comparison;
  const title = comparisonTitle(comparison);
  return (
    <motion.section
      initial={reduceMotion ? { opacity: 0 } : { opacity: 0, transform: "translateY(12px)" }}
      animate={reduceMotion ? { opacity: 1 } : { opacity: 1, transform: "translateY(0px)" }}
      transition={{ duration: 0.24, ease: [0.23, 1, 0.32, 1] }}
      aria-label={`${title}, ${columns.length} options`}
      className="mt-8 min-w-0 max-w-full"
    >
      <div className="mb-2 flex min-h-8 items-center gap-2 px-1">
        <Scales size={14} weight="bold" className="shrink-0 text-accent-strong" aria-hidden />
        <h3 className="min-w-0 truncate text-[11px] font-bold uppercase tracking-[.12em] text-accent-strong">{title}</h3>
      </div>
      {comparison.headline && <p role="status" className="mb-2 px-1 text-[13px] font-semibold leading-5 text-ink">{comparison.headline}</p>}
      <ul aria-label={`${title} options`} className={columns.length <= 2 ? "grid grid-cols-2 gap-2" : "flex snap-x snap-mandatory gap-2 overflow-x-auto overscroll-x-contain pb-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"}>
        {columns.map((column) => {
          const { product } = column;
          const prices = optionPrices(product);
          const quantity = basketQuantity(baskets, product);
          const locked = ["checkout_pending", "ordered"].includes(baskets[product.provider]?.status ?? "");
          const adding = addingSku === product.sku_id;
          const canAdd = product.available && Boolean(product.spin_id) && !locked;
          const eta = product.delivery_value ? `${product.delivery_value} ${product.delivery_unit ?? "min"}` : null;
          return (
            <li key={`${product.provider}-${product.sku_id}`} className={columns.length <= 2 ? "min-w-0" : "w-[44%] min-w-[9.5rem] shrink-0 snap-start sm:w-[calc((100%-1rem)/3)]"}>
              <article className={`flex h-full flex-col rounded-[20px] border bg-surface px-3 pb-3 pt-2.5 shadow-[0_16px_36px_-32px_rgb(var(--shadow)/.7)] ${column.badges.length ? "border-accent-border" : "border-border-strong"}`}>
                <div className="flex min-h-5 flex-wrap gap-1">
                  {column.badges.map((badge) => <span key={badge} className="rounded-full bg-accent px-2 py-0.5 text-[10px] font-semibold leading-4 text-on-accent">{badge}</span>)}
                </div>
                <span className="mt-1.5 flex w-fit items-center gap-1 rounded-full bg-surface-muted px-2 py-0.5 text-[10px] font-semibold text-ink-secondary"><i className={`size-1.5 rounded-full ${STORE_DOT[product.provider]}`} aria-hidden />{storeName(product, commerceMode)}</span>
                <h4 className="mt-1.5 line-clamp-2 min-h-[2.6em] text-[13px] font-semibold leading-[1.3] text-ink" title={product.name}>{product.name}</h4>
                <p className="mt-0.5 truncate text-[11px] text-muted">{product.pack_size || "\u00a0"}</p>
                <div className="mt-2 flex flex-wrap items-baseline gap-x-1.5 gap-y-1">
                  {prices.price ? <strong className="text-[17px] leading-none tabular-nums text-ink">{prices.price}</strong> : <span className="text-xs text-muted">Price shown in basket</span>}
                  {prices.mrp && <s className="text-[11px] tabular-nums text-subtle"><span className="sr-only">MRP </span>{prices.mrp}</s>}
                </div>
                <p className="mt-1 text-[11px] tabular-nums leading-4 text-ink-secondary">{unitPriceText(column)}</p>
                <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[10px]">
                  <span className={`flex items-center gap-0.5 font-medium ${product.available ? "text-success" : "text-muted"}`}>{product.available && <CheckCircle size={11} weight="fill" aria-hidden />}{product.available ? "In stock" : "Out of stock"}</span>
                  {eta && <span className="flex items-center gap-0.5 text-muted"><Clock size={11} aria-hidden />{eta}</span>}
                </div>
                <div className="mt-auto pt-3">
                  <button
                    type="button"
                    onClick={() => void onAdd(product)}
                    disabled={!canAdd || addingSku !== null}
                    aria-label={canAdd ? basketActionLabel(product, "add") : `${product.name} is unavailable`}
                    className={`flex h-10 w-full items-center justify-center gap-1.5 rounded-full text-[13px] font-semibold transition-[transform,background-color] duration-150 active:scale-[.97] disabled:cursor-not-allowed ${focusRing} ${!canAdd ? "bg-surface-strong text-subtle" : "bg-accent text-on-accent hover:bg-accent-hover disabled:bg-border-strong"}`}
                  >
                    {adding ? <CircleNotch size={15} className="animate-spin" /> : <Plus size={14} weight="bold" />}
                    {!product.available ? "Out of stock" : quantity ? `Add · ${quantity} in basket` : "Add"}
                  </button>
                </div>
              </article>
            </li>
          );
        })}
      </ul>
      <button type="button" onClick={onHide} className="mx-auto mt-3 block text-xs font-semibold text-muted transition hover:text-ink">Hide comparison</button>
    </motion.section>
  );
}
