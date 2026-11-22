"use client";

import { useAuth } from "@clerk/nextjs";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { ApiError, useApiRequest } from "../authenticated-api";
import {
  doctorCounts,
  groupMemory,
  inspectorAllowed,
  shapeBaskets,
  shapeTurns,
  type DevBasket,
  type DevMemory,
  type DoctorCheck,
  type TraceTurn,
} from "../dev-inspector.mjs";

type DevState = {
  baskets: DevBasket[];
  memory: DevMemory[];
  watches: Record<string, unknown>[];
  notifications: { kind: string; outcome: string; event: string; at: string }[];
  zepto_address: { selected: boolean; snapshot_age_min: number | null };
};
type Status = { enabled: boolean; trace: boolean; trace_keep: number };

const card = "rounded-2xl border border-border bg-surface p-4";
const button = "rounded-full border border-border-strong px-3 py-1 text-sm font-semibold hover:bg-canvas disabled:opacity-50";
const DOT = { green: "bg-green-600", amber: "bg-amber-500", red: "bg-red-600" } as const;

export function DevInspector() {
  const { isLoaded, isSignedIn } = useAuth();
  const request = useApiRequest();
  const [status, setStatus] = useState<Status | null | "hidden">(null);
  const [turns, setTurns] = useState<TraceTurn[]>([]);
  const [state, setState] = useState<DevState | null>(null);
  const [doctor, setDoctor] = useState<DoctorCheck[] | null>(null);
  const [reconcile, setReconcile] = useState<string[] | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const next = await request<Status>("/api/dev/status");
      setStatus(next);
      const [traces, current] = await Promise.all([
        request<{ turns: TraceTurn[] }>("/api/dev/traces?limit=20"),
        request<DevState>("/api/dev/state"),
      ]);
      setTurns(traces.turns);
      setState(current);
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) setStatus("hidden");
      else setMessage(error instanceof Error ? error.message : "Request failed.");
    }
  }, [request]);

  useEffect(() => {
    if (isLoaded && isSignedIn) void load();
  }, [isLoaded, isSignedIn, load]);

  async function act(name: string, run: () => Promise<void>) {
    setBusy(name);
    setMessage(null);
    try {
      await run();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Request failed.");
    } finally {
      setBusy(null);
    }
  }

  if (!isLoaded) return <Shell>Loading…</Shell>;
  if (!isSignedIn) return <Shell>Sign in first: <Link className="underline" href="/sign-in">/sign-in</Link></Shell>;
  if (status === "hidden" || (status !== null && !inspectorAllowed(process.env.NODE_ENV, status))) {
    return <Shell>Dev tools are off. Set <code>DEV_TOOLS_ENABLED=true</code> in .env, then <code>make restart</code> (localhost only).</Shell>;
  }
  if (status === null) return <Shell>Loading…</Shell>;

  const shaped = shapeTurns(turns);
  const baskets = shapeBaskets(state?.baskets);
  const counts = doctorCounts(doctor);

  return <Shell>
    {message && <p className="mb-3 rounded-xl bg-red-50 p-3 text-sm text-red-800">{message}</p>}
    <div className="mb-4 flex flex-wrap gap-2">
      <button className={button} disabled={busy !== null} onClick={() => act("refresh", load)}>Refresh</button>
      <button className={button} disabled={busy !== null} onClick={() => act("doctor", async () => {
        const result = await request<{ checks: DoctorCheck[] }>("/api/dev/doctor", { method: "POST" });
        setDoctor(result.checks);
      })}>{busy === "doctor" ? "Running doctor…" : "Re-run doctor"}</button>
      <button className={button} disabled={busy !== null} onClick={() => {
        if (!window.confirm("This reads Zepto order history (read-only, at most 5 calls, 1 s apart) if an order outcome is unknown. Continue?")) return;
        void act("reconcile", async () => {
          const result = await request<{ output: string[] }>("/api/dev/reconcile-dry-run", { method: "POST" });
          setReconcile(result.output);
        });
      }}>Reconcile dry-run (calls Zepto)</button>
    </div>

    {doctor && <section className={`${card} mb-4`}>
      <h2 className="mb-2 font-semibold">Doctor · {counts.green} green · {counts.amber} amber · {counts.red} red <span className="text-sm font-normal text-muted">(0 Swiggy/Zepto calls)</span></h2>
      <ul className="space-y-1 text-sm">{doctor.map((check) => <li key={check.name}>
        <span className={`mr-2 inline-block size-2 rounded-full ${DOT[check.status]}`} />
        <b>{check.name}</b> — {check.detail}{check.fix && check.status !== "green" && <div className="ml-4 font-mono text-xs text-muted">fix: {check.fix}</div>}
      </li>)}</ul>
    </section>}

    {reconcile && <section className={`${card} mb-4`}><h2 className="mb-2 font-semibold">Reconcile dry-run</h2>
      <pre className="whitespace-pre-wrap text-xs">{reconcile.join("\n")}</pre></section>}

    <section className={`${card} mb-4`}>
      <h2 className="mb-2 font-semibold">Last turns {status.trace ? "" : <span className="text-sm font-normal text-muted">· DEV_TRACE is off (set DEV_TRACE=true)</span>}</h2>
      {shaped.length === 0 && <p className="text-sm text-muted">No traces yet.</p>}
      <ul className="divide-y divide-border text-sm">{shaped.map((turn) => <li key={turn.id} className="py-2">
        <button className="w-full text-left" onClick={() => setOpen(open === turn.id ? null : turn.id)}>
          <span className="font-mono">{turn.id.slice(0, 8)}</span> · {turn.channel} · {turn.outcome} · <b>{turn.total}</b> · {turn.rounds} rounds ({turn.modelShare}% model) · {turn.toolNames.join(", ") || "reply only"}
          {turn.problems.length > 0 && <span className="ml-2 text-red-700">{turn.problems.join("; ")}</span>}
        </button>
        {open === turn.id && <div className="mt-2 space-y-2 pl-4 text-xs">
          <div className="text-muted">{turn.at} · {turn.model} · prompt {turn.promptVersion}{turn.slowestTool ? ` · slowest ${turn.slowestTool}` : ""}</div>
          {turn.detail.map((round) => <div key={round.round}>
            <div><b>Round {round.round}</b> · model {round.model}{round.tokens ? ` · tokens ${Object.entries(round.tokens).map(([k, v]) => `${k} ${v}`).join(" ")}` : ""}</div>
            {round.tools.map((tool, index) => <div key={index} className="ml-3 font-mono">
              {tool.name}({tool.args}) → {tool.status} · {tool.time}{tool.counts ? ` · ${Object.entries(tool.counts).map(([k, v]) => `${k}=${v}`).join(" ")}` : ""}{tool.stages.length ? ` [${tool.stages.join(", ")}]` : ""}
            </div>)}
          </div>)}
        </div>}
      </li>)}</ul>
    </section>

    <div className="grid gap-4 md:grid-cols-2">
      <section className={card}>
        <h2 className="mb-2 font-semibold">Baskets</h2>
        {baskets.length === 0 && <p className="text-sm text-muted">No open baskets.</p>}
        {baskets.map((basket) => <div key={basket.provider} className="mb-3 text-sm">
          <div className="flex items-center justify-between gap-2">
            <span><b>{basket.label}</b> · {basket.status} · {basket.summary}{basket.locked && <b className="ml-1 text-red-700">LOCKED</b>}</span>
            <button className={button} disabled={!basket.canClear || busy !== null} onClick={() => {
              if (!window.confirm(`Clear the ${basket.label} basket?`)) return;
              void act("clear", async () => {
                await request("/api/dev/basket/clear", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ provider: basket.provider }) });
                await load();
              });
            }}>Clear basket</button>
          </div>
          <ul className="ml-3 text-xs text-muted">{basket.lines.map((line, index) => <li key={index}>{line.quantity} × {line.name}{line.pack_size ? ` (${line.pack_size})` : ""} · {line.line_total}</li>)}</ul>
        </div>)}
        <p className="text-xs text-muted">Zepto address: {state?.zepto_address.selected ? `selected · ${state.zepto_address.snapshot_age_min ?? "?"} min old` : "none selected"}</p>
      </section>

      <section className={card}>
        <h2 className="mb-2 font-semibold">Memory ({state?.memory.length ?? 0})</h2>
        {groupMemory(state?.memory).map((group) => <div key={group.status} className="mb-2 text-sm">
          <div className="text-xs font-semibold uppercase text-muted">{group.status}</div>
          <ul>{group.items.map((item) => <li key={item.id}><span className="font-mono text-xs">{item.id.slice(0, 8)}</span> {item.text} <span className="text-xs text-muted">· {item.kind ?? "-"} · {item.origin}</span></li>)}</ul>
        </div>)}
      </section>

      <section className={card}>
        <h2 className="mb-2 font-semibold">Tracking watches</h2>
        {(state?.watches ?? []).length === 0 && <p className="text-sm text-muted">None.</p>}
        <ul className="text-xs">{(state?.watches ?? []).map((watch, index) => <li key={index} className="font-mono">{JSON.stringify(watch)}</li>)}</ul>
      </section>

      <section className={card}>
        <h2 className="mb-2 font-semibold">Notification log</h2>
        {(state?.notifications ?? []).length === 0 && <p className="text-sm text-muted">None.</p>}
        <ul className="text-xs">{(state?.notifications ?? []).map((entry, index) => <li key={index}>{entry.at} · {entry.kind} · {entry.event} · {entry.outcome}</li>)}</ul>
      </section>
    </div>
  </Shell>;
}

function Shell({ children }: { children: React.ReactNode }) {
  return <main className="min-h-[100dvh] bg-surface-muted px-4 py-6 text-ink">
    <div className="mx-auto max-w-5xl">
      <nav className="mb-4 flex items-center justify-between text-sm"><Link href="/">← GharSaathi</Link><span className="font-semibold">Dev inspector · local only</span></nav>
      {children}
    </div>
  </main>;
}
