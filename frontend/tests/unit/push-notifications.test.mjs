import assert from "node:assert/strict";
import test from "node:test";
import {
  MESSAGES,
  detectSupport,
  notificationState,
  orderFromUrl,
  safePath,
  shouldOfferOrderPrompt,
  turnOff,
  turnOn,
  urlBase64ToUint8Array,
} from "../../app/push-notifications.mjs";

const CHROME = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/131.0 Safari/537.36";
const IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1";
const IPAD_DESKTOP_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/18.0 Safari/605.1.15";
const full = { isSecureContext: true, hasServiceWorker: true, hasPushManager: true, hasNotification: true };

test("support: desktop browsers on https or localhost are supported", () => {
  assert.equal(detectSupport({ ...full, userAgent: CHROME }), "supported");
  assert.equal(detectSupport({ ...full, userAgent: IPAD_DESKTOP_UA, maxTouchPoints: 0 }), "supported"); // macOS Safari
});

test("support: plain-http LAN address is insecure, missing APIs are unsupported", () => {
  assert.equal(detectSupport({ ...full, isSecureContext: false, userAgent: CHROME }), "insecure");
  assert.equal(detectSupport({ ...full, hasPushManager: false, userAgent: CHROME }), "unsupported");
  assert.equal(detectSupport({ ...full, hasServiceWorker: false, userAgent: CHROME }), "unsupported");
});

test("support: iPhone and iPad need the Home Screen web app", () => {
  assert.equal(detectSupport({ ...full, hasPushManager: false, userAgent: IPHONE }), "ios-install");
  assert.equal(detectSupport({ ...full, userAgent: IPAD_DESKTOP_UA, maxTouchPoints: 5 }), "ios-install");
  assert.equal(detectSupport({ ...full, userAgent: IPHONE, standalone: true }), "supported");
});

test("state machine: every state has one clear message and the right switch", () => {
  const base = { available: true, support: "supported", permission: "default", subscribed: false };
  assert.equal(notificationState({ ...base, available: null }).state, "loading");
  const hidden = notificationState({ ...base, available: false });
  assert.equal(hidden.state, "hidden");
  assert.match(hidden.hint, /make vapid-keys/);
  assert.deepEqual(
    ["insecure", "ios-install", "unsupported"].map((support) => notificationState({ ...base, support }).state),
    ["insecure", "ios-install", "unsupported"],
  );
  const off = notificationState(base);
  assert.deepEqual([off.state, off.checked, off.disabled, off.canTest], ["off", false, false, false]);
  const on = notificationState({ ...base, permission: "granted", subscribed: true });
  assert.deepEqual([on.state, on.checked, on.canTest], ["on", true, true]);
  // Granted earlier but not subscribed on this device: still off, the switch turns it on.
  assert.equal(notificationState({ ...base, permission: "granted" }).state, "off");
  const denied = notificationState({ ...base, permission: "denied" });
  assert.deepEqual([denied.state, denied.disabled, denied.message], ["denied", true, MESSAGES.denied]);
  assert.equal(notificationState({ ...base, busy: "on" }).state, "turning-on");
  assert.equal(notificationState({ ...base, busy: "off", subscribed: true, permission: "granted" }).state, "turning-off");
  assert.equal(notificationState({ ...base, error: MESSAGES.failed }).message, MESSAGES.failed);
});

test("order prompt: offered once, only when it can actually work", () => {
  const base = { available: true, support: "supported", permission: "default", subscribed: false, answered: false };
  assert.equal(shouldOfferOrderPrompt(base), true);
  assert.equal(shouldOfferOrderPrompt({ ...base, answered: true }), false);
  assert.equal(shouldOfferOrderPrompt({ ...base, permission: "denied" }), false);
  assert.equal(shouldOfferOrderPrompt({ ...base, permission: "granted" }), false);
  assert.equal(shouldOfferOrderPrompt({ ...base, subscribed: true }), false);
  assert.equal(shouldOfferOrderPrompt({ ...base, available: false }), false);
  assert.equal(shouldOfferOrderPrompt({ ...base, available: null }), false);
  assert.equal(shouldOfferOrderPrompt({ ...base, support: "ios-install" }), false);
});

function browser({ permission = "granted", subscribeFails = false, saveFails = false } = {}) {
  const calls = [];
  return {
    calls,
    deps: {
      publicKey: "BAQIDA",
      requestPermission: async () => { calls.push("permission"); return permission; },
      register: async () => { calls.push("register"); return { id: "reg" }; },
      subscribe: async (registration, key) => {
        calls.push(["subscribe", registration.id, key instanceof Uint8Array]);
        if (subscribeFails) throw new Error("AbortError");
        return { toJSON: () => ({ endpoint: "https://fcm.googleapis.com/x", keys: { p256dh: "p", auth: "a" } }) };
      },
      save: async (subscription) => { calls.push(["save", subscription.endpoint]); if (saveFails) throw new Error("500"); },
    },
  };
}

test("opt-in: permission first, then register, subscribe and save", async () => {
  const { calls, deps } = browser();
  assert.deepEqual(await turnOn(deps), { state: "on", permission: "granted" });
  assert.deepEqual(calls, ["permission", "register", ["subscribe", "reg", true], ["save", "https://fcm.googleapis.com/x"]]);
});

test("opt-in: denied or dismissed stops before any registration", async () => {
  for (const [permission, state, message] of [["denied", "denied", MESSAGES.denied], ["default", "off", MESSAGES.dismissed]]) {
    const { calls, deps } = browser({ permission });
    assert.deepEqual(await turnOn(deps), { state, message, permission });
    assert.deepEqual(calls, ["permission"]);
  }
});

test("opt-in: a failed subscribe or save is an error, not 'on'", async () => {
  for (const options of [{ subscribeFails: true }, { saveFails: true }]) {
    const { deps } = browser(options);
    assert.deepEqual(await turnOn(deps), { state: "error", message: MESSAGES.failed, permission: "granted" });
  }
});

test("opt-out: server first, then the browser; a server failure keeps it on", async () => {
  const calls = [];
  const subscription = { endpoint: "https://fcm.googleapis.com/x", unsubscribe: async () => { calls.push("unsubscribe"); return true; } };
  assert.deepEqual(await turnOff({ getSubscription: async () => subscription, remove: async (endpoint) => { calls.push(["remove", endpoint]); } }), { state: "off" });
  assert.deepEqual(calls, [["remove", "https://fcm.googleapis.com/x"], "unsubscribe"]);
  calls.length = 0;
  const failed = await turnOff({ getSubscription: async () => subscription, remove: async () => { throw new Error("offline"); } });
  assert.deepEqual(failed, { state: "error", message: MESSAGES.offFailed });
  assert.deepEqual(calls, []);
  assert.deepEqual(await turnOff({ getSubscription: async () => null, remove: async () => {} }), { state: "off" });
});

test("VAPID key decodes to the 65-byte applicationServerKey", () => {
  const key = "BMKLrUJRgu4wvpU0lFt8vij_r7RzcuR9ozp-pYkA8iGXf3unOEosvn64T0YgaMVibcYjXLIwDcDYg545wERtzQw";
  const bytes = urlBase64ToUint8Array(key);
  assert.equal(bytes.length, 65);
  assert.equal(bytes[0], 4);
});

test("notification targets are same-origin paths only", () => {
  assert.equal(safePath("/?order=1"), "/?order=1");
  for (const bad of ["https://evil.example", "//evil.example", "javascript:alert(1)", "/\\evil", null, 5]) assert.equal(safePath(bad), "/");
  assert.equal(orderFromUrl("/?order=ORD%2F1"), "ORD/1");
  assert.equal(orderFromUrl("/"), null);
  assert.equal(orderFromUrl("https://evil.example/?order=1"), null);
});

test("copy is plain English and never about stock or replenishment", () => {
  for (const message of Object.values(MESSAGES)) {
    assert.doesNotMatch(message, /stock|khatam|usually|running out|reorder/i);
  }
});
