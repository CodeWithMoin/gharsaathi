// Browser notification (Web Push) logic with no React or DOM: support checks, the
// permission/subscription state machine, the opt-in and opt-out flows (browser APIs
// injected), and the one-time order prompt. Unit-tested in
// tests/unit/push-notifications.test.mjs.

export const MESSAGES = {
  unsupported: "This browser can't show notifications. Try Chrome, Edge, Firefox or Safari.",
  insecure: "Notifications need a secure connection. Open GharSaathi on https:// or localhost, not a local network address like 192.168.x.x.",
  iosInstall: "On iPhone and iPad, add GharSaathi to your Home Screen first (Share, then Add to Home Screen). Open it from there and turn on notifications.",
  denied: "Notifications are blocked for this site. Allow them in your browser's site settings, then try again.",
  dismissed: "Notifications stay off. Tap the switch again when you want them.",
  failed: "Couldn't turn on notifications. Check your connection and try again.",
  offFailed: "Couldn't turn off notifications on this device. Try again.",
  setup: "Browser notifications aren't set up on this server yet.",
  setupHint: "Setup: run `make vapid-keys`, add the keys to .env and restart the backend.",
  on: "Notifications are on for this device.",
  off: "Turn on to get order updates even when GharSaathi is in the background or closed.",
};

/**
 * @param {{ isSecureContext?: boolean, hasServiceWorker?: boolean, hasPushManager?: boolean,
 *   hasNotification?: boolean, userAgent?: string, maxTouchPoints?: number, standalone?: boolean }} env
 * @returns {"supported" | "insecure" | "ios-install" | "unsupported"}
 */
export function detectSupport(env) {
  const userAgent = env.userAgent ?? "";
  const ios = /iPad|iPhone|iPod/.test(userAgent) || (/Macintosh/.test(userAgent) && (env.maxTouchPoints ?? 0) > 1);
  if (env.isSecureContext === false) return "insecure";
  // iOS and iPadOS deliver web push only to a web app added to the Home Screen.
  if (ios && !env.standalone) return "ios-install";
  if (!env.hasServiceWorker || !env.hasPushManager || !env.hasNotification) return "unsupported";
  return "supported";
}

/** Reads the browser once; safe to call only in the browser. */
export function browserEnv(win) {
  const nav = win.navigator;
  return {
    isSecureContext: Boolean(win.isSecureContext),
    hasServiceWorker: "serviceWorker" in nav,
    hasPushManager: "PushManager" in win,
    hasNotification: "Notification" in win,
    userAgent: nav.userAgent,
    maxTouchPoints: nav.maxTouchPoints ?? 0,
    standalone: Boolean(win.matchMedia?.("(display-mode: standalone)").matches || nav.standalone),
  };
}

/**
 * One view of the notifications setting.
 * @param {{ available: boolean | null, support: string, permission: string, subscribed: boolean,
 *   busy?: "on" | "off" | null, error?: string }} input
 */
export function notificationState({ available, support, permission, subscribed, busy = null, error = "" }) {
  if (available === null) return { state: "loading", message: "", checked: false, disabled: true, canTest: false };
  if (!available) return { state: "hidden", message: MESSAGES.setup, hint: MESSAGES.setupHint, checked: false, disabled: true, canTest: false };
  if (support === "insecure") return { state: "insecure", message: MESSAGES.insecure, checked: false, disabled: true, canTest: false };
  if (support === "ios-install") return { state: "ios-install", message: MESSAGES.iosInstall, checked: false, disabled: true, canTest: false };
  if (support !== "supported") return { state: "unsupported", message: MESSAGES.unsupported, checked: false, disabled: true, canTest: false };
  if (busy === "on") return { state: "turning-on", message: "Turning on…", checked: true, disabled: true, canTest: false };
  if (busy === "off") return { state: "turning-off", message: "Turning off…", checked: false, disabled: true, canTest: false };
  if (permission === "denied") return { state: "denied", message: MESSAGES.denied, checked: false, disabled: true, canTest: false };
  if (error) return { state: "error", message: error, checked: subscribed, disabled: false, canTest: subscribed };
  if (subscribed && permission === "granted") return { state: "on", message: MESSAGES.on, checked: true, disabled: false, canTest: true };
  return { state: "off", message: MESSAGES.off, checked: false, disabled: false, canTest: false };
}

/** The one-time "Get delivery updates as notifications?" after a placed order. */
export function shouldOfferOrderPrompt({ available, support, permission, subscribed, answered }) {
  return available === true && support === "supported" && permission === "default" && !subscribed && !answered;
}

/** VAPID public key (base64url) → the Uint8Array `applicationServerKey` wants. */
export function urlBase64ToUint8Array(value) {
  const padded = `${value}${"=".repeat((4 - (value.length % 4)) % 4)}`.replace(/-/g, "+").replace(/_/g, "/");
  const binary = atob(padded);
  return Uint8Array.from(binary, (char) => char.charCodeAt(0));
}

/**
 * Opt in. The permission prompt is the first thing called, straight from the tap
 * (Safari only shows it inside a user gesture), and never on page load.
 * @param {{ requestPermission: () => Promise<string>, register: () => Promise<any>,
 *   subscribe: (registration: any, key: Uint8Array) => Promise<{ toJSON: () => unknown }>,
 *   save: (subscription: unknown) => Promise<unknown>, publicKey: string }} deps
 * @returns {Promise<{ state: "on" | "off" | "denied" | "error", message?: string, permission: string }>}
 */
export async function turnOn({ requestPermission, register, subscribe, save, publicKey }) {
  let permission;
  try {
    permission = await requestPermission();
  } catch {
    return { state: "error", message: MESSAGES.failed, permission: "default" };
  }
  if (permission === "denied") return { state: "denied", message: MESSAGES.denied, permission };
  if (permission !== "granted") return { state: "off", message: MESSAGES.dismissed, permission };
  try {
    const registration = await register();
    const subscription = await subscribe(registration, urlBase64ToUint8Array(publicKey));
    await save(subscription.toJSON());
    return { state: "on", permission };
  } catch {
    return { state: "error", message: MESSAGES.failed, permission };
  }
}

/**
 * Opt out on this device: tell the server first (so no more pushes are sent), then
 * drop the browser subscription. A server failure keeps the browser subscribed.
 * @param {{ getSubscription: () => Promise<{ endpoint: string, unsubscribe: () => Promise<boolean> } | null>,
 *   remove: (endpoint: string) => Promise<unknown> }} deps
 */
export async function turnOff({ getSubscription, remove }) {
  try {
    const subscription = await getSubscription();
    if (subscription) {
      await remove(subscription.endpoint);
      await subscription.unsubscribe();
    }
    return { state: "off" };
  } catch {
    return { state: "error", message: MESSAGES.offFailed };
  }
}

/** A notification's target, only ever a same-origin path. */
export function safePath(url) {
  return typeof url === "string" && url.startsWith("/") && !url.startsWith("//") && !url.includes("\\") ? url : "/";
}

/** The `?order=` id a notification opened the app with, if any. */
export function orderFromUrl(url) {
  try {
    const value = new URL(safePath(url), "https://gharsaathi.local").searchParams.get("order");
    return value && value.length <= 160 ? value : null;
  } catch {
    return null;
  }
}
