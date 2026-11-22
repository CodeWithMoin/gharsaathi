"use client";

import { Bell, CircleNotch } from "@phosphor-icons/react";
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

/**
 * Settings → Profile: one "Notifications" switch with its status and hint. Every kind of
 * update stays on (the server default); the browser asks for permission only after the tap.
 */
export function NotificationSwitch({ controller }: { controller: NotificationsController }) {
  const { view } = controller;
  const labelId = useId();
  const statusId = useId();
  const on = view.checked;
  const busy = view.state === "turning-on" || view.state === "turning-off";
  const showSwitch = view.state !== "hidden" && view.state !== "loading";
  return <div className="rounded-2xl border border-border bg-surface p-3.5">
    <div className="flex items-center gap-3">
      <div className="min-w-0 flex-1">
        <p id={labelId} className="text-sm font-semibold">Notifications</p>
        <p id={statusId} role="status" aria-live="polite" className={`mt-0.5 text-xs leading-5 ${view.state === "denied" || view.state === "error" ? "text-danger" : "text-muted"}`}>{view.state === "loading" ? "Loading…" : view.state === "hidden" ? "Not set up on this server." : view.message || "Order placed, out for delivery, delivered, late or cancelled."}</p>
      </div>
      {showSwitch && <button type="button" role="switch" aria-checked={on} aria-labelledby={labelId} aria-describedby={statusId} disabled={view.disabled} onClick={() => void (on ? controller.disable() : controller.enable())} className={`relative h-7 w-12 shrink-0 rounded-full transition-colors after:absolute after:-inset-x-1 after:-inset-y-2 after:content-[''] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-offset-2 disabled:opacity-50 ${on ? "bg-accent" : "bg-border-strong"}`}>
        <span className={`absolute left-0.5 top-0.5 grid size-6 place-items-center rounded-full bg-white shadow transition-transform duration-200 ease-[cubic-bezier(.23,1,.32,1)] motion-reduce:transition-none ${on ? "translate-x-5" : "translate-x-0"}`}>{busy && <CircleNotch size={12} className="animate-spin text-muted" />}</span>
      </button>}
    </div>
    {view.hint && <p className="mt-1.5 text-[11px] leading-4 text-subtle">{view.hint}</p>}
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
