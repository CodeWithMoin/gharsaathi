"use client";

import Link from "next/link";
import Image from "next/image";
import { ArrowLeft, CheckCircle, Clock, Plus, Star } from "@phosphor-icons/react";
import { useState } from "react";

type OptionId = "summary" | "labels" | "grid";

const options: Array<{ id: OptionId; label: string; hint: string }> = [
  { id: "summary", label: "A", hint: "Decision first" },
  { id: "labels", label: "B", hint: "Store cards" },
  { id: "grid", label: "C", hint: "Full compare" },
];

const stores = [
  { name: "Swiggy", price: 699, mrp: 850, eta: 18, rating: "4.3", lower: false },
  { name: "Zepto", price: 675, mrp: 825, eta: 11, rating: "4.3", lower: true },
];

function AddButton({ store, quiet = false }: { store: string; quiet?: boolean }) {
  return (
    <button
      type="button"
      className={`flex min-h-10 shrink-0 items-center justify-center gap-1.5 rounded-full px-4 text-xs font-semibold transition active:scale-[.98] ${
        quiet
          ? "border border-[#d9c7bb] bg-white text-[#6c5042] hover:bg-[#fff8f2]"
          : "bg-[#c6531d] text-white hover:bg-[#b94a18]"
      }`}
      aria-label={`Add from ${store}`}
    >
      <Plus size={14} weight="bold" /> Add
    </button>
  );
}

function ProductHeading() {
  return (
    <div className="flex items-center gap-4">
      <div className="relative size-24 shrink-0 overflow-hidden rounded-2xl bg-[#f3ece5]">
        <Image src="/demo-products/basmati-rice.png" alt="Basmati rice pack" fill sizes="96px" className="object-contain p-2" />
      </div>
      <div className="min-w-0">
        <p className="text-[10px] font-semibold tracking-[.12em] text-[#c6531d]">INDIA GATE</p>
        <h2 className="mt-1 text-[17px] font-semibold leading-snug text-[#35241c]">Everyday Basmati Rice</h2>
        <p className="mt-0.5 text-xs text-[#806f65]">5 kg</p>
      </div>
    </div>
  );
}

function SummaryOption() {
  return (
    <article className="overflow-hidden rounded-[22px] border border-[#e3d3c7] bg-[#fffdf9] shadow-[0_18px_40px_-34px_rgba(74,43,27,.7)]">
      <div className="px-4 py-4"><ProductHeading /></div>

      <div className="mx-3 rounded-2xl bg-[#edf5ed] px-3.5 py-3 text-[#2f673a]">
        <p className="text-sm font-semibold">Save ₹24 on Zepto</p>
        <p className="mt-0.5 text-[11px] leading-5 text-[#52775a]">₹675 item price and about 7 min faster</p>
      </div>

      <div className="mt-3 divide-y divide-[#eee3da] border-t border-[#eee3da]">
        {stores.map((store) => (
          <div key={store.name} className={`grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 px-4 py-3.5 ${store.lower ? "bg-[#fff8f1]" : ""}`}>
            <div>
              <div className="flex items-center gap-2">
                <strong className="text-[13px] font-semibold text-[#49352b]">{store.name}</strong>
                {store.lower && <span className="text-[10px] font-semibold text-[#397144]">Lower item price</span>}
              </div>
              <div className="mt-1 flex items-baseline gap-2">
                <strong className="text-lg leading-none text-[#35241c]">₹{store.price}</strong>
                <s className="text-[11px] text-[#9d8c81]">₹{store.mrp}</s>
                <span className="flex items-center gap-1 text-[10px] text-[#75645a]"><Clock size={11} /> {store.eta} min</span>
              </div>
            </div>
            <AddButton store={store.name} quiet={!store.lower} />
          </div>
        ))}
      </div>
    </article>
  );
}

function LabelsOption() {
  return (
    <article className="rounded-[22px] border border-[#e3d3c7] bg-[#fffdf9] p-4 shadow-[0_18px_40px_-34px_rgba(74,43,27,.7)]">
      <ProductHeading />
      <div className="mt-4 grid grid-cols-2 gap-2.5">
        {stores.map((store) => (
          <section key={store.name} className={`rounded-2xl border p-3 ${store.lower ? "border-[#bad8c0] bg-[#f2f8f2]" : "border-[#e4d6cb] bg-white"}`}>
            <div className="flex min-h-9 items-start justify-between gap-2">
              <strong className="text-xs font-semibold text-[#49352b]">{store.name}</strong>
              {store.lower && <span className="text-right text-[9px] font-semibold leading-3 text-[#397144]">₹24 less<br />7 min faster</span>}
            </div>
            <p className="mt-2 text-2xl font-semibold tracking-[-.03em] text-[#35241c]">₹{store.price}</p>
            <p className="mt-1 flex items-center gap-1 text-[10px] text-[#75645a]"><Clock size={11} /> {store.eta} min</p>
            <div className="mt-3"><AddButton store={store.name} quiet={!store.lower} /></div>
          </section>
        ))}
      </div>
      <p className="mt-3 text-[10px] leading-4 text-[#8a776b]">Item prices compared. Delivery fees are checked with the final basket.</p>
    </article>
  );
}

function GridOption() {
  return (
    <article className="overflow-hidden rounded-[22px] border border-[#e3d3c7] bg-[#fffdf9] shadow-[0_18px_40px_-34px_rgba(74,43,27,.7)]">
      <div className="px-4 py-4"><ProductHeading /></div>
      <div className="grid grid-cols-[minmax(78px,.8fr)_1fr_1fr] border-t border-[#eaded5] text-xs">
        <div className="bg-[#f7eee7] px-3 py-3" />
        {stores.map((store) => <strong key={store.name} className={`${store.lower ? "bg-[#edf5ed] text-[#2f673a]" : "bg-[#f7eee7] text-[#49352b]"} px-3 py-3 text-center`}>{store.name}</strong>)}

        <span className="px-3 py-3 text-[#806f65]">Item price</span>
        {stores.map((store) => <span key={`${store.name}-price`} className={`${store.lower ? "bg-[#f7fbf7] font-semibold text-[#2f673a]" : ""} px-3 py-3 text-center text-sm`}>₹{store.price}{store.lower && <small className="block text-[9px] font-semibold">₹24 less</small>}</span>)}

        <span className="bg-[#fcf8f4] px-3 py-3 text-[#806f65]">Delivery</span>
        {stores.map((store) => <span key={`${store.name}-eta`} className={`${store.lower ? "bg-[#f1f7f1] font-semibold text-[#2f673a]" : "bg-[#fcf8f4]"} px-3 py-3 text-center`}>{store.eta} min{store.lower && <small className="block text-[9px] font-semibold">7 min faster</small>}</span>)}

        <span className="px-3 py-3 text-[#806f65]">Available</span>
        {stores.map((store) => <span key={`${store.name}-stock`} className="flex items-center justify-center gap-1 px-3 py-3 text-[#397144]"><CheckCircle size={13} weight="fill" /> Yes</span>)}

        <span className="bg-[#fcf8f4] px-3 py-3 text-[#806f65]">Rating</span>
        {stores.map((store) => <span key={`${store.name}-rating`} className="flex items-center justify-center gap-1 bg-[#fcf8f4] px-3 py-3"><Star size={11} weight="fill" /> {store.rating}</span>)}
      </div>
      <div className="grid grid-cols-2 gap-2.5 p-3">
        {stores.map((store) => <AddButton key={store.name} store={store.name} quiet={!store.lower} />)}
      </div>
    </article>
  );
}

export default function ComparisonOptionsPage() {
  const [selected, setSelected] = useState<OptionId>("summary");

  return (
    <main className="grain min-h-[100dvh] bg-[#fbf5ed] px-4 pb-12 pt-5 text-[#35241c]">
      <div className="mx-auto w-full max-w-[430px]">
        <Link href="/" className="inline-flex items-center gap-2 rounded-full px-1 py-2 text-xs font-semibold text-[#70584b] transition hover:text-[#35241c]">
          <ArrowLeft size={15} /> Back to chat
        </Link>

        <header className="mt-5">
          <h1 className="font-display text-3xl font-semibold tracking-[-.04em]">How should we show the difference?</h1>
          <p className="mt-2 max-w-[36ch] text-sm leading-6 text-[#806f65]">Same product and stores. Switch between the three comparison treatments.</p>
        </header>

        <div className="mt-6 grid grid-cols-3 gap-2 rounded-[18px] border border-[#e5d5ca] bg-[#f4e8de] p-1.5" role="tablist" aria-label="Comparison design options">
          {options.map((option) => (
            <button
              key={option.id}
              type="button"
              role="tab"
              aria-selected={selected === option.id}
              onClick={() => setSelected(option.id)}
              className={`min-w-0 rounded-[13px] px-2 py-2.5 text-left transition active:scale-[.98] ${selected === option.id ? "bg-[#fffdf9] text-[#35241c] shadow-[0_8px_20px_-16px_rgba(65,39,26,.7)]" : "text-[#806f65] hover:bg-white/50"}`}
            >
              <span className="block text-[10px] font-bold">{option.label}</span>
              <span className="mt-0.5 block truncate text-[10px] font-medium">{option.hint}</span>
            </button>
          ))}
        </div>

        <section className="mt-4" role="tabpanel">
          {selected === "summary" && <SummaryOption />}
          {selected === "labels" && <LabelsOption />}
          {selected === "grid" && <GridOption />}
        </section>

        <aside className="mt-4 rounded-2xl bg-[#f1e5dc] px-4 py-3 text-xs leading-5 text-[#765e51]">
          <strong className="text-[#49352b]">What changes:</strong> only the comparison treatment. Product image, basket rules, and approval flow stay the same.
        </aside>
      </div>
    </main>
  );
}
