/* GharSaathi service worker: browser notifications only.
 *
 * It handles `push` and `notificationclick` and nothing else. There is no fetch
 * handler and no cache, so no authenticated data is ever stored offline.
 *
 * Double notifications: while a GharSaathi tab is visible and focused, a push
 * marked `suppress_when_visible` shows nothing; that tab already shows the
 * update in-app (toast or status card).
 */

const ICON = "/icons/gharsaathi-192.png";

function safePath(url) {
  return typeof url === "string" && url.startsWith("/") && !url.startsWith("//") && !url.includes("\\") ? url : "/";
}

function text(value, max) {
  return typeof value === "string" ? value.replace(/\s+/g, " ").trim().slice(0, max) : "";
}

async function appIsInFront() {
  const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
  return windows.some((client) => client.visibilityState === "visible" && client.focused);
}

async function handlePush(event) {
  let data = null;
  try {
    data = event.data ? event.data.json() : null;
  } catch {
    data = null;
  }
  const title = text(data && data.title, 60);
  if (!title) return;
  if (data.suppress_when_visible !== false && (await appIsInFront())) return;
  const tag = text(data.tag, 80);
  await self.registration.showNotification(title, {
    body: text(data.body, 200),
    icon: ICON,
    badge: ICON,
    lang: "en-IN",
    ...(tag ? { tag, renotify: true } : {}),
    data: { url: safePath(data.url) },
  });
}

async function handleClick(event) {
  event.notification.close();
  const url = safePath(event.notification.data && event.notification.data.url);
  const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
  const existing = windows.find((client) => new URL(client.url).origin === self.location.origin);
  if (existing) {
    await existing.focus();
    existing.postMessage({ type: "gharsaathi:open", url });
    return;
  }
  await self.clients.openWindow(url);
}

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("push", (event) => event.waitUntil(handlePush(event)));
self.addEventListener("notificationclick", (event) => event.waitUntil(handleClick(event)));
