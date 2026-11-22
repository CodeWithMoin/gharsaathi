"use client";

import { ArrowSquareOut, CheckCircle, Copy, EnvelopeSimple, Paperclip, ShieldCheck, Truck, X } from "@phosphor-icons/react";
import { FormEvent, useEffect, useState } from "react";
import { useApiRequest } from "./authenticated-api";
import { InputDialog } from "./ui/input-dialog";

type Provider = "swiggy" | "zepto";
type Complaint = {
  id: string;
  provider: Provider;
  status: string;
  revision: number;
  prepared_summary: string | null;
  evidence: Array<{ id: string; file_name: string }>;
};

type SupportStatus = { dirty: boolean; busy: boolean };
type SupportPanelProps = {
  onClose: () => void;
  onTrackOrder?: () => void;
  /** Inside a popup that already supplies the title, description and close button. */
  embedded?: boolean;
  onStatusChange?: (status: SupportStatus) => void;
};

const categories = [
  ["missing_product", "Missing item"],
  ["damaged_product", "Damaged item"],
  ["spoiled_product", "Spoiled item"],
  ["incorrect_product", "Wrong item"],
  ["incorrect_quantity", "Wrong quantity"],
  ["delayed_delivery", "Delayed delivery"],
  ["other", "Other issue"],
] as const;

const providerHandoff = {
  swiggy: {
    href: "https://www.swiggy.com/support",
    fallback: "Open the Swiggy app, select this order, then tap Help.",
  },
  zepto: {
    href: "https://www.zepto.com/",
    fallback: "Open the Zepto app, select this order, then tap Get Help.",
  },
} as const;

export function SupportPanel({ onClose, onTrackOrder, embedded = false, onStatusChange }: SupportPanelProps) {
  const request = useApiRequest();
  const [provider, setProvider] = useState<Provider>("swiggy");
  const [orderId, setOrderId] = useState("");
  const [product, setProduct] = useState("");
  const [category, setCategory] = useState("missing_product");
  const [description, setDescription] = useState("");
  const [resolution, setResolution] = useState("refund");
  const [evidence, setEvidence] = useState<File | null>(null);
  const [complaint, setComplaint] = useState<Complaint | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");

  const prepare = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      let next = await request<Complaint>("/api/support/complaints", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          provider,
          order_id: orderId,
          affected_product: product,
          category,
          description,
          desired_resolution: resolution,
        }),
      });
      if (evidence) {
        const body = new FormData();
        body.set("evidence", evidence);
        next = await request<Complaint>(`/api/support/complaints/${next.id}/evidence`, {
          method: "POST",
          body,
        });
      }
      next = await request<Complaint>(`/api/support/complaints/${next.id}/prepare`, { method: "POST" });
      setComplaint(next);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not prepare support handoff.");
    } finally {
      setBusy(false);
    }
  };

  const approve = async () => {
    if (!complaint) return;
    setBusy(true);
    setError("");
    try {
      setComplaint(await request<Complaint>(`/api/support/complaints/${complaint.id}/approve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ expected_revision: complaint.revision }),
      }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Approval could not be recorded.");
    } finally {
      setBusy(false);
    }
  };

  const copySummary = async () => {
    if (!complaint?.prepared_summary) return;
    try {
      await navigator.clipboard.writeText(complaint.prepared_summary);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      setError("Could not copy the message. You can still select and copy it from the prepared text.");
    }
  };

  const approved = complaint?.status === "approved_pending_submission";
  const dirty = !approved && Boolean(orderId.trim() || product.trim() || description.trim() || evidence || complaint);
  useEffect(() => { onStatusChange?.({ dirty, busy }); }, [busy, dirty, onStatusChange]);
  const officialHandoff = providerHandoff[provider];
  const emailHandoff = complaint?.prepared_summary && provider === "swiggy"
    ? `mailto:support@swiggy.in?subject=${encodeURIComponent(`Order support request · ${orderId}`)}&body=${encodeURIComponent(complaint.prepared_summary)}`
    : null;

  const trackButton = onTrackOrder && <button type="button" onClick={onTrackOrder} className="flex shrink-0 items-center gap-1 rounded-full border border-border-strong bg-white px-2.5 py-1.5 text-[10px] font-semibold text-ink-secondary"><Truck size={13} />Track</button>;
  return (
    <section className={embedded ? "" : "rounded-[8px_22px_22px_22px] border border-border-strong bg-surface shadow-[0_18px_45px_-34px_rgb(var(--shadow)/.7)]"}>
      {embedded ? trackButton && <div className="mb-3 flex justify-end">{trackButton}</div> : <header className="flex items-start gap-3 border-b border-border px-4 py-3.5">
        <div className="min-w-0 flex-1">
          <h2 className="text-[15px] font-semibold text-ink">Order help</h2>
          <p className="mt-0.5 text-[11px] leading-4 text-muted">I’ll prepare everything. You approve before opening provider support.</p>
        </div>
        {trackButton}
        <button type="button" onClick={onClose} aria-label="Close order help" className="grid size-8 shrink-0 place-items-center rounded-full text-muted hover:bg-surface-strong"><X size={16} /></button>
      </header>}

      {!complaint ? (
        <form onSubmit={prepare} className={`space-y-3 ${embedded ? "" : "p-4"}`}>
          <div className="grid grid-cols-2 gap-2">
            {(["swiggy", "zepto"] as const).map((name) => <button key={name} type="button" onClick={() => setProvider(name)} className={`rounded-xl border px-3 py-2 text-xs font-semibold capitalize transition ${provider === name ? "border-accent bg-accent-soft text-accent-strong" : "border-border-strong text-ink-secondary"}`}>{name}</button>)}
          </div>
          <label className="block"><span className="mb-1.5 block text-[11px] font-semibold text-ink-secondary">Order ID</span><input value={orderId} onChange={(event) => setOrderId(event.target.value)} required minLength={2} placeholder="From your recent order" className="w-full rounded-xl border border-border-strong bg-white px-3 py-2.5 text-sm outline-none focus:border-accent" /></label>
          <label className="block"><span className="mb-1.5 block text-[11px] font-semibold text-ink-secondary">Affected item</span><input value={product} onChange={(event) => setProduct(event.target.value)} required minLength={2} placeholder="e.g. Amul milk 1 L" className="w-full rounded-xl border border-border-strong bg-white px-3 py-2.5 text-sm outline-none focus:border-accent" /></label>
          <label className="block"><span className="mb-1.5 block text-[11px] font-semibold text-ink-secondary">What happened?</span><select value={category} onChange={(event) => setCategory(event.target.value)} className="w-full rounded-xl border border-border-strong bg-white px-3 py-2.5 text-sm outline-none focus:border-accent">{categories.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          <label className="block"><span className="mb-1.5 block text-[11px] font-semibold text-ink-secondary">Describe it</span><textarea value={description} onChange={(event) => setDescription(event.target.value)} required minLength={5} rows={3} placeholder="Tell me what was wrong…" className="w-full resize-none rounded-xl border border-border-strong bg-white px-3 py-2.5 text-sm leading-5 outline-none focus:border-accent" /></label>
          <div className="grid grid-cols-2 gap-2"><button type="button" onClick={() => setResolution("refund")} className={`rounded-xl border px-3 py-2 text-xs font-semibold ${resolution === "refund" ? "border-accent bg-accent-soft text-accent-strong" : "border-border-strong text-ink-secondary"}`}>Refund</button><button type="button" onClick={() => setResolution("replacement")} className={`rounded-xl border px-3 py-2 text-xs font-semibold ${resolution === "replacement" ? "border-accent bg-accent-soft text-accent-strong" : "border-border-strong text-ink-secondary"}`}>Replacement</button></div>
          <label className="flex cursor-pointer items-center gap-2 rounded-xl border border-dashed border-accent-border px-3 py-2.5 text-xs text-ink-secondary"><Paperclip size={16} /><span className="min-w-0 flex-1 truncate">{evidence ? evidence.name : "Add photo evidence (optional)"}</span><input type="file" accept="image/jpeg,image/png,image/webp" onChange={(event) => setEvidence(event.target.files?.[0] ?? null)} className="sr-only" /></label>
          {error && <p role="alert" className="rounded-xl bg-danger-soft px-3 py-2 text-xs leading-5 text-danger">{error}</p>}
          <button disabled={busy} className="w-full rounded-full bg-accent px-4 py-3 text-sm font-semibold text-on-accent transition active:scale-[.98] disabled:opacity-50">{busy ? "Preparing…" : "Prepare support message"}</button>
        </form>
      ) : (
        <div className={embedded ? "" : "p-4"}>
          <div className="flex items-center gap-2 text-xs font-semibold text-success"><CheckCircle size={17} weight="fill" />Message prepared</div>
          <pre className="mt-3 whitespace-pre-wrap rounded-2xl bg-surface-muted p-3 font-sans text-[11px] leading-5 text-ink-secondary">{complaint.prepared_summary}</pre>
          {complaint.evidence.length > 0 && <p className="mt-2 text-[10px] text-muted">Evidence attached: {complaint.evidence.map((item) => item.file_name).join(", ")}</p>}
          {error && <p role="alert" className="mt-3 rounded-xl bg-danger-soft px-3 py-2 text-xs leading-5 text-danger">{error}</p>}
          {!approved ? <button type="button" onClick={() => void approve()} disabled={busy} className="mt-4 w-full rounded-full bg-accent px-4 py-3 text-sm font-semibold text-on-accent transition active:scale-[.98] disabled:opacity-50">{busy ? "Recording approval…" : "Approve this exact message"}</button> : <div className="mt-4 rounded-2xl border border-success-border bg-success-soft p-3"><div className="flex gap-2 text-xs font-semibold text-success-strong"><ShieldCheck size={17} />Approved—not sent</div><p className="mt-1 text-[10px] leading-4 text-success">Order details and the approved message will be copied—paste them into Swiggy’s chat.</p><div className="mt-3 grid grid-cols-2 gap-2"><button type="button" onClick={() => void copySummary()} className="flex items-center justify-center gap-1.5 rounded-full border border-success-border bg-white px-3 py-2 text-xs font-semibold text-success-strong"><Copy size={14} />{copied ? "Details copied" : "Copy details"}</button><a href={officialHandoff.href} target="_blank" rel="noreferrer" onPointerDown={() => void copySummary()} onClick={() => void copySummary()} className="flex items-center justify-center gap-1.5 rounded-full bg-success px-3 py-2 text-xs font-semibold text-on-accent"><ArrowSquareOut size={14} />Open Swiggy Help</a></div>{emailHandoff && <a href={emailHandoff} onClick={() => void copySummary()} className="mt-2 flex w-full items-center justify-center gap-1.5 rounded-full border border-success-border bg-white px-3 py-2 text-xs font-semibold text-success-strong"><EnvelopeSimple size={14} />Email support@swiggy.in</a>}<p className="mt-2 text-[10px] leading-4 text-success">If it stays in the browser: {officialHandoff.fallback}</p></div>}
          <button type="button" onClick={() => setComplaint(null)} className="mt-3 w-full text-center text-[11px] font-semibold text-accent-strong">Start over</button>
        </div>
      )}
    </section>
  );
}

/** "Order help" popup. `open` can be driven by the header-free entry points: the settings drawer row or a `support.open` UI event. */
export function SupportDialog({ open, onClose, onTrackOrder }: { open: boolean; onClose: () => void; onTrackOrder?: () => void }) {
  const [status, setStatus] = useState<SupportStatus>({ dirty: false, busy: false });
  return <InputDialog
    open={open}
    title="Order help"
    description="I’ll prepare everything. You approve before opening provider support."
    closeLabel="Close order help"
    widthClassName="sm:max-w-md"
    dirty={status.dirty}
    busy={status.busy}
    discardMessage="Discard this support request?"
    onClose={() => { setStatus({ dirty: false, busy: false }); onClose(); }}
  >
    <SupportPanel embedded onClose={onClose} onTrackOrder={onTrackOrder} onStatusChange={setStatus} />
  </InputDialog>;
}
