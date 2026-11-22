"use client";

import { CaretDown, List, MapPin, ShoppingCartSimple } from "@phosphor-icons/react";
import { useEffect, useState } from "react";
import { AccountMenuButton } from "./account-settings-sheet";
import type { HeaderAddress } from "./home-header.mjs";
import type { HeaderChip } from "./quick-actions.mjs";
import { HeaderOrderChip } from "./quick-chips";

const iconButton = "grid size-11 shrink-0 place-items-center rounded-full text-ink-secondary outline-none transition hover:bg-accent-soft focus-visible:ring-2 focus-visible:ring-focus/50 active:scale-[.96] motion-reduce:active:scale-100";

/**
 * Swiggy-style home header: ☰ (recent chats) · the delivery address block (pin, bold
 * label ▾, one muted line of the address; tap → address picker) · basket with its badge ·
 * avatar (opens the right drawer). A moving order's chip sits on its own row below, so
 * the address never has to share the 360px row with it.
 */
export function HomeHeader({ address, basketCount, orderChip, userName, userImageUrl, accountOpen, top = 0, onOpenChats, onOpenAddress, onOpenBasket, onOpenAccount, onOpenOrder }: {
  address: HeaderAddress;
  basketCount: number;
  orderChip: HeaderChip | null;
  userName: string;
  userImageUrl?: string;
  accountOpen: boolean;
  top?: number;
  onOpenChats: () => void;
  onOpenAddress: () => void;
  onOpenBasket: () => void;
  onOpenAccount: () => void;
  onOpenOrder: (orderId: string) => void;
}) {
  // The header blends into the page; a hairline and soft shadow appear only once content scrolls under it.
  const scrolled = useScrolled();
  return <header style={{ top }} className={`fixed inset-x-0 top-0 z-30 border-b bg-surface-muted/85 pt-[env(safe-area-inset-top)] backdrop-blur-xl backdrop-saturate-150 transition-[border-color,box-shadow] duration-200 motion-reduce:transition-none ${scrolled ? "border-border shadow-[0_12px_30px_-26px_rgb(var(--shadow)/.55)]" : "border-transparent shadow-none"}`}>
    <div className="mx-auto flex h-[68px] max-w-4xl items-center gap-2 pl-3 pr-4 sm:px-6">
      <button type="button" onClick={onOpenChats} aria-label="Open shopping chats" className={iconButton}><List size={22} weight="bold" /></button>
      <button type="button" onClick={onOpenAddress} disabled={address.state === "checking"} aria-label={address.accessibleLabel} data-testid="header-address" className="flex min-h-11 min-w-0 flex-1 items-center gap-1.5 rounded-2xl px-1.5 py-1 text-left outline-none transition-colors hover:bg-surface-muted focus-visible:ring-2 focus-visible:ring-focus/50 disabled:cursor-default">
        <MapPin size={20} weight="fill" className="shrink-0 text-accent" aria-hidden="true" />
        <span className="min-w-0 flex-1">
          <span className="flex min-w-0 items-center gap-0.5"><span className="truncate text-[15px] font-bold leading-5 text-ink">{address.label}</span>{address.state !== "checking" && <CaretDown size={13} weight="bold" className="shrink-0 text-ink-secondary" aria-hidden="true" />}</span>
          {address.line && <span className="block truncate text-xs leading-4 text-muted">{address.line}</span>}
        </span>
      </button>
      <nav className="flex shrink-0 items-center gap-2" aria-label="Chat actions">
        {basketCount > 0 && <button type="button" onClick={onOpenBasket} aria-label={`Open basket with ${basketCount} items`} className={`relative ${iconButton}`}><ShoppingCartSimple size={22} weight="bold" /><span className="absolute right-0 top-0.5 grid h-[18px] min-w-[18px] place-items-center rounded-full bg-accent px-1 text-[10px] font-bold leading-none text-on-accent ring-2 ring-surface-muted">{basketCount}</span></button>}
        <AccountMenuButton name={userName} imageUrl={userImageUrl} active={accountOpen} onClick={onOpenAccount} />
      </nav>
    </div>
    {orderChip && <div className="mx-auto flex max-w-4xl justify-center px-3 pb-2 sm:justify-start sm:px-6"><HeaderOrderChip chip={orderChip} onOpen={onOpenOrder} /></div>}
  </header>;
}

function useScrolled(threshold = 4): boolean {
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const update = () => setScrolled(window.scrollY > threshold);
    update();
    window.addEventListener("scroll", update, { passive: true });
    return () => window.removeEventListener("scroll", update);
  }, [threshold]);
  return scrolled;
}
