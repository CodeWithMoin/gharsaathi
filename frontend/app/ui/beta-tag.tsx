import { PHOTO_BETA_LABEL } from "../photo-attachment.mjs";

/** Small "Beta" tag: shop from a photo is a beta feature. */
export function BetaTag() {
  return <span className="inline-flex h-5 shrink-0 items-center rounded-full bg-accent-soft px-2 text-[10px] font-semibold uppercase tracking-wide text-accent-ink">{PHOTO_BETA_LABEL}</span>;
}
