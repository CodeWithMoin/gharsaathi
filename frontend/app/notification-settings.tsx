"use client";

import { Bell, BellSlash, CircleNotch, PaperPlaneTilt } from "@phosphor-icons/react";
import { useCallback, useEffect, useId, useMemo, useState } from "react";
import { ApiError, useApiRequest } from "./authenticated-api";
import {
  browserEnv,
  detectSupport,
  notificationState,
  shouldOfferOrderPrompt,
  turnOff,
  turnOn,
  type NotificationView,
  type PushSupport,
} from "./push-notifications.mjs";

type KindSetting = { id: string; label: string; enabled: boolean };
type ServerSettings = { available: boolean; public_key: string | null; kinds: KindSetting[]; devices: number; order_prompt_answered: boolean };

const SETTINGS_PATH = "/api/notifications/settings";
const SUBSCRIPTIONS_PATH = "/api/notifications/subscriptions";

export type NotificationsController = {
  view: NotificationView;
  kinds: KindSetting[];
  offerOrderPrompt: boolean;
  testNotice: string;
  testing: boolean;
  enable: () => Promise<void>;
  disable: () => Promise<void>;
  setKind: (kind: string, enabled: boolean) => Promise<void>;
  answerOrderPrompt: () => Promise<void>;
  sendTest: () => Promise<void>;
};

async function registration(): Promise<ServiceWorkerRegistration> {
  const registered = await navigator.serviceWorker.register("/sw.js", { scope: "/" });
  return registered.active ? registered : navigator.serviceWorker.ready;
}

/**
 * One controller per page. Nothing asks for permission or registers the service
 * worker on load; that happens only after the user taps the switch or "Turn on".
 */
export function useNotifications(): NotificationsController {
  const request = useApiRequest();
  const [server, setServer] = useState<ServerSettings | null>(null);
  const [support, setSupport] = useState<PushSupport>("unsupported");
  const [permission, setPermission] = useState<string>("default");
  const [subscribed, setSubscribed] = useState(false);
  const [busy, setBusy] = useState<"on" | "off" | null>(null);
  const [error, setError] = useState("");
  const [testing, setTesting] = useState(false);
  const [testNotice, setTestNotice] = useState("");

  useEffect(() => {
    let active = true;
    const detected = detectSupport(browserEnv(window));
    setSupport(detected);
    if (detected === "supported") setPermission(Notification.permission);
    void request<ServerSettings>(SETTINGS_PATH)
      .then(async (loaded) => {
        if (!active) return;
        setServer(loaded);
        if (!loaded.available || detected !== "supported" || Notification.permission !== "granted") return;
        // Already on in this browser: keep the server copy in sync (and owned by this user).
        const existing = await navigator.serviceWorker.getRegistration("/");
        const subscription = await existing?.pushManager.getSubscription();
        if (!subscription || !active) return;
        await request(SUBSCRIPTIONS_PATH, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(subscription.toJSON()) });
        if (active) setSubscribed(true);
      })
      .catch(() => { if (active) setServer((current) => current ?? { available: false, public_key: null, kinds: [], devices: 0, order_prompt_answered: true }); });
    return () => { active = false; };
  }, [request]);

  const enable = useCallback(async () => {
    if (!server?.public_key) return;
    setError("");
    setBusy("on");
    const result = await turnOn({
      requestPermission: () => Notification.requestPermission(),
      register: registration,
      subscribe: (registered, key) => (registered as ServiceWorkerRegistration).pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key as BufferSource }),
      save: (subscription) => request(SUBSCRIPTIONS_PATH, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(subscription) }),
      publicKey: server.public_key,
    });
    setPermission(result.permission);
    setSubscribed(result.state === "on");
    if (result.state === "error" || result.state === "off") setError(result.message ?? "");
    setBusy(null);
  }, [request, server?.public_key]);

  const disable = useCallback(async () => {
    setError("");
    setBusy("off");
    const result = await turnOff({
      getSubscription: async () => (await (await navigator.serviceWorker.getRegistration("/"))?.pushManager.getSubscription()) ?? null,
      remove: (endpoint) => request(SUBSCRIPTIONS_PATH, { method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ endpoint }) }),
    });
    if (result.state === "off") setSubscribed(false);
    else setError(result.message ?? "");
    setBusy(null);
  }, [request]);

  const save = useCallback(async (body: object) => {
    const saved = await request<ServerSettings>(SETTINGS_PATH, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    setServer(saved);
  }, [request]);

  const setKind = useCallback(async (kind: string, enabled: boolean) => {
    setServer((current) => current && { ...current, kinds: current.kinds.map((item) => item.id === kind ? { ...item, enabled } : item) });
    try {
      await save({ kinds: { [kind]: enabled } });
    } catch {
      setServer((current) => current && { ...current, kinds: current.kinds.map((item) => item.id === kind ? { ...item, enabled: !enabled } : item) });
      setError("Couldn't save that change. Try again.");
    }
  }, [save]);

  const answerOrderPrompt = useCallback(async () => {
    setServer((current) => current && { ...current, order_prompt_answered: true });
    try { await save({ order_prompt_answered: true }); } catch { /* the prompt stays closed for this visit */ }
  }, [save]);

  const sendTest = useCallback(async () => {
    setTesting(true);
    setTestNotice("");
    try {
      await request(`/api/notifications/test`, { method: "POST" });
      setTestNotice("Test sent. It should appear in a few seconds.");
    } catch (cause) {
      setTestNotice(cause instanceof ApiError && cause.message !== "Request failed." ? cause.message : "Couldn't send a test. Try again.");
    } finally {
      setTesting(false);
    }
  }, [request]);

  const view = useMemo(() => notificationState({ available: server ? server.available : null, support, permission, subscribed, busy, error }), [busy, error, permission, server, subscribed, support]);
  const offerOrderPrompt = shouldOfferOrderPrompt({ available: server?.available ?? null, support, permission, subscribed, answered: server?.order_prompt_answered ?? true });
  return { view, kinds: server?.kinds ?? [], offerOrderPrompt, testNotice, testing, enable, disable, setKind, answerOrderPrompt, sendTest };
}

export function notificationsSubtitle(view: NotificationView) {
  if (view.state === "on") return "On for this device";
  if (view.state === "hidden") return "Not set up";
  if (view.state === "loading") return "Loading…";
  if (view.state === "denied") return "Blocked in this browser";
  if (view.state === "unsupported" || view.state === "insecure" || view.state === "ios-install") return "Not available here";
  return "Off";
}

/** Settings → Notifications. */
export function NotificationsDetail({ controller }: { controller: NotificationsController }) {
  const { view, kinds } = controller;
  const switchId = useId();
  const on = view.checked;
  return <div className="rounded-2xl border border-border bg-surface p-4">
    <span className="grid size-11 place-items-center rounded-xl bg-accent-soft text-accent-hover">{on ? <Bell size={24} weight="duotone" /> : <BellSlash size={24} weight="duotone" />}</span>
    <h3 className="mt-4 text-base font-semibold tracking-[-.02em]">Order updates as notifications</h3>
    <p className="mt-2 text-sm leading-6 text-muted">Order placed, out for delivery, delivered, running late or cancelled, even when GharSaathi is in the background or closed. Only for orders you place.</p>
    {view.state !== "hidden" && view.state !== "loading" && <div className="mt-4 flex min-h-12 items-center gap-3 rounded-xl border border-border px-3">
      <label htmlFor={switchId} className="min-w-0 flex-1 text-sm font-semibold">Notifications on this device</label>
      <button id={switchId} type="button" role="switch" aria-checked={on} disabled={view.disabled} onClick={() => void (on ? controller.disable() : controller.enable())} className={`relative h-7 w-12 shrink-0 rounded-full transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus disabled:opacity-50 ${on ? "bg-accent" : "bg-border-strong"}`}>
        <span className={`absolute top-1 grid size-5 place-items-center rounded-full bg-surface transition-[left] ${on ? "left-6" : "left-1"}`}>{(view.state === "turning-on" || view.state === "turning-off") && <CircleNotch size={12} className="animate-spin text-muted" />}</span>
      </button>
    </div>}
    <p role="status" aria-live="polite" className={`mt-2 text-xs leading-5 ${view.state === "denied" || view.state === "error" ? "text-danger" : "text-muted"}`}>{view.message}</p>
    {view.hint && <p className="mt-1 text-[11px] leading-5 text-subtle">{view.hint}</p>}
    {view.state === "on" && kinds.length > 0 && <fieldset className="mt-4">
      <legend className="text-xs font-semibold text-ink-secondary">Tell me when</legend>
      <div className="mt-1.5 divide-y divide-border rounded-xl border border-border">
        {kinds.map((kind) => <label key={kind.id} className="flex min-h-11 items-center gap-3 px-3 text-sm">
          <span className="min-w-0 flex-1">{kind.label}</span>
          <input type="checkbox" checked={kind.enabled} onChange={(event) => void controller.setKind(kind.id, event.target.checked)} className="size-5 accent-[rgb(var(--color-accent))]" />
        </label>)}
      </div>
    </fieldset>}
    {view.canTest && <button type="button" onClick={() => void controller.sendTest()} disabled={controller.testing} className="mt-4 flex min-h-11 w-full items-center justify-center gap-2 rounded-xl border border-accent-border px-4 text-sm font-semibold text-ink-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus disabled:opacity-55">
      {controller.testing ? <CircleNotch size={16} className="animate-spin" /> : <PaperPlaneTilt size={16} weight="bold" />}Send a test notification
    </button>}
    {controller.testNotice && <p role="status" className="mt-2 text-xs text-muted">{controller.testNotice}</p>}
  </div>;
}

/** The one-time prompt after the first placed order. */
export function OrderNotificationPrompt({ controller, onDone }: { controller: NotificationsController; onDone: () => void }) {
  const close = () => { void controller.answerOrderPrompt(); onDone(); };
  return <div role="dialog" aria-label="Delivery updates" className="pointer-events-auto flex w-full max-w-sm items-center gap-3 rounded-[22px] border border-border bg-surface px-4 py-3 shadow-[0_18px_40px_-20px_rgb(var(--shadow)/.8)]">
    <Bell size={22} weight="duotone" className="shrink-0 text-accent-ink" />
    <p className="min-w-0 flex-1 text-sm font-medium text-ink">Get delivery updates as notifications?</p>
    <button type="button" onClick={close} className="shrink-0 rounded-full px-3 py-2 text-xs font-semibold text-ink-secondary hover:bg-surface-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus">Not now</button>
    <button type="button" onClick={() => { void controller.enable().finally(close); }} className="shrink-0 rounded-full bg-accent px-3 py-2 text-xs font-semibold text-on-accent hover:bg-accent-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus">Turn on</button>
  </div>;
}
