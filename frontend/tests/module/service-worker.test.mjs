// The real public/sw.js in a VM with a fake service-worker global. Checks the
// visible-tab rule (no double notifications), payload handling, click targets,
// and that it has no fetch handler (no offline cache of authenticated data).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const SOURCE = readFileSync(new URL("../../public/sw.js", import.meta.url), "utf8");

function worker({ windows = [] } = {}) {
  const listeners = {};
  const shown = [];
  const opened = [];
  const self = {
    location: { origin: "https://app.example" },
    addEventListener: (type, handler) => { listeners[type] = handler; },
    skipWaiting: () => {},
    clients: {
      matchAll: async () => windows,
      claim: async () => {},
      openWindow: async (url) => { opened.push(url); },
    },
    registration: { showNotification: async (title, options) => { shown.push({ title, ...options }); } },
  };
  vm.runInNewContext(SOURCE, { self, URL });
  async function dispatch(type, event) {
    let pending;
    listeners[type]({ ...event, waitUntil: (promise) => { pending = promise; } });
    await pending;
  }
  return {
    listeners,
    shown,
    opened,
    push: (data) => dispatch("push", { data: data === undefined ? null : { json: () => (typeof data === "string" ? JSON.parse(data) : data) } }),
    click: (url) => {
      const notification = { data: { url }, closed: false, close() { this.closed = true; } };
      return dispatch("notificationclick", { notification }).then(() => notification);
    },
  };
}

const UPDATE = { kind: "out_for_delivery", title: "Out for delivery", body: "Your Zepto order is on the way.", url: "/?order=ORD1", tag: "order:ORD1", suppress_when_visible: true };

test("only push and notificationclick (plus lifecycle) are handled; nothing is cached", () => {
  const { listeners } = worker();
  assert.deepEqual(Object.keys(listeners).sort(), ["activate", "install", "notificationclick", "push"]);
  assert.doesNotMatch(SOURCE, /caches\.|addEventListener\("fetch"/);
});

test("a hidden or closed app gets the system notification", async () => {
  const sw = worker({ windows: [{ visibilityState: "hidden", focused: false, url: "https://app.example/" }] });
  await sw.push(UPDATE);
  assert.equal(sw.shown.length, 1);
  assert.deepEqual(
    { title: sw.shown[0].title, body: sw.shown[0].body, tag: sw.shown[0].tag, renotify: sw.shown[0].renotify, url: sw.shown[0].data.url },
    { title: "Out for delivery", body: "Your Zepto order is on the way.", tag: "order:ORD1", renotify: true, url: "/?order=ORD1" },
  );
  const closed = worker();
  await closed.push(UPDATE);
  assert.equal(closed.shown.length, 1);
});

test("a visible, focused app gets no second notification (it shows the toast)", async () => {
  const sw = worker({ windows: [{ visibilityState: "visible", focused: true, url: "https://app.example/" }] });
  await sw.push(UPDATE);
  assert.equal(sw.shown.length, 0);
  // The Settings test notification always shows.
  await sw.push({ ...UPDATE, kind: "test", suppress_when_visible: false });
  assert.equal(sw.shown.length, 1);
});

test("unreadable payloads show nothing and unsafe URLs open the home page", async () => {
  const sw = worker();
  await sw.push(undefined);
  await sw.push({ body: "no title" });
  assert.equal(sw.shown.length, 0);
  await sw.push({ ...UPDATE, url: "https://evil.example/" });
  assert.equal(sw.shown[0].data.url, "/");
});

test("tapping focuses an open app and tells it which order to show", async () => {
  const messages = [];
  let focused = false;
  const client = { url: "https://app.example/", visibilityState: "hidden", focused: false, focus: async () => { focused = true; }, postMessage: (message) => messages.push(message) };
  const sw = worker({ windows: [client] });
  const notification = await sw.click("/?order=ORD1");
  assert.equal(notification.closed, true);
  assert.equal(focused, true);
  assert.deepEqual(JSON.parse(JSON.stringify(messages)), [{ type: "gharsaathi:open", url: "/?order=ORD1" }]);
  assert.deepEqual(sw.opened, []);
});

test("tapping with no open app opens it at the order", async () => {
  const sw = worker();
  await sw.click("/?order=ORD1");
  assert.deepEqual(sw.opened, ["/?order=ORD1"]);
  const unsafe = worker();
  await unsafe.click("//evil.example");
  assert.deepEqual(unsafe.opened, ["/"]);
});
