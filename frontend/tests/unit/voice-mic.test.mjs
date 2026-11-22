import assert from "node:assert/strict";
import test from "node:test";
import { VOICE_START_TIMEOUT_MS, startDeadline, voiceErrorKind, voiceFailureView, voiceStartError, voiceStartTimeoutError, voiceSupport } from "../../app/voice-mic.mjs";

const mediaDevices = { getUserMedia() {} };
function RTCPeerConnection() {}

test("voice starts only with a secure context, getUserMedia and WebRTC", () => {
  assert.equal(voiceSupport({ isSecureContext: true, navigator: { mediaDevices }, RTCPeerConnection }), "ok");
  assert.equal(voiceSupport({ isSecureContext: false, navigator: { mediaDevices }, RTCPeerConnection }), "insecure_context");
  assert.equal(voiceSupport(undefined), "insecure_context");
});

test("an insecure context (phone on plain http) explains https and offers typed chat, not Try again", () => {
  const support = voiceSupport({ isSecureContext: false, navigator: {}, RTCPeerConnection });
  assert.equal(support, "insecure_context");
  const view = voiceFailureView({ support });
  assert.equal(view.message, "Voice needs a secure connection (https) or localhost. Open the app on https, or use typed chat.");
  assert.equal(view.retry, false);
});

test("missing navigator.mediaDevices never reaches getUserMedia", () => {
  assert.equal(voiceSupport({ isSecureContext: true, navigator: {}, RTCPeerConnection }), "unsupported");
  assert.equal(voiceSupport({ isSecureContext: true, navigator: { mediaDevices: {} }, RTCPeerConnection }), "unsupported");
  assert.equal(voiceSupport({ navigator: {}, RTCPeerConnection }), "insecure_context");
  assert.equal(voiceSupport({ isSecureContext: true, navigator: { mediaDevices } }), "unsupported");
  const view = voiceFailureView({ support: "unsupported" });
  assert.match(view.message, /typed chat/);
  assert.equal(view.retry, false);
});

test("NotAllowedError asks for microphone access in the browser settings", () => {
  assert.equal(voiceErrorKind({ name: "NotAllowedError" }), "denied");
  assert.equal(voiceErrorKind({ name: "SecurityError" }), "denied");
  const view = voiceFailureView({ error: { name: "NotAllowedError", message: "Permission denied" } });
  assert.equal(view.kind, "denied");
  assert.match(view.message, /^Allow microphone access in your browser settings/);
  assert.equal(view.retry, true);
});

test("NotFoundError says no microphone was found", () => {
  assert.equal(voiceErrorKind({ name: "NotFoundError" }), "not_found");
  assert.equal(voiceErrorKind({ name: "OverconstrainedError" }), "not_found");
  assert.match(voiceFailureView({ error: { name: "NotFoundError" } }).message, /^No microphone was found/);
});

test("NotReadableError says the mic is in use by another app", () => {
  assert.equal(voiceErrorKind({ name: "NotReadableError" }), "in_use");
  assert.equal(voiceErrorKind({ name: "AbortError" }), "in_use");
  assert.match(voiceFailureView({ error: { name: "NotReadableError" } }).message, /being used by another app/);
});

test("a start that runs past the deadline becomes a retryable timeout error", () => {
  assert.equal(VOICE_START_TIMEOUT_MS, 15_000);
  let fire;
  let delay;
  const expired = [];
  startDeadline(VOICE_START_TIMEOUT_MS, (error) => expired.push(error), (callback, ms) => { fire = callback; delay = ms; }, () => undefined);
  assert.equal(delay, 15_000);
  fire();
  fire();
  assert.equal(expired.length, 1);
  const view = voiceFailureView({ error: expired[0] });
  assert.equal(view.kind, "timeout");
  assert.match(view.message, /took too long/);
  assert.equal(view.retry, true);
  assert.equal(voiceErrorKind(voiceStartTimeoutError()), "timeout");
});

test("a cleared deadline never fires", () => {
  let fire;
  let cleared = 0;
  const expired = [];
  const deadline = startDeadline(10, (error) => expired.push(error), (callback) => { fire = callback; return 7; }, (handle) => { assert.equal(handle, 7); cleared += 1; });
  deadline.clear();
  deadline.clear();
  fire();
  assert.equal(cleared, 1);
  assert.deepEqual(expired, []);
});

test("unknown errors never show raw browser text; our own messages are kept", () => {
  const raw = voiceFailureView({ error: new TypeError("Cannot read properties of undefined (reading 'getUserMedia')") });
  assert.equal(raw.kind, "unknown");
  assert.equal(raw.message, "Could not start voice. Tap Try again, or use typed chat.");
  assert.equal(voiceFailureView({ error: voiceStartError("The voice connection dropped. Please try again.") }).message, "The voice connection dropped. Please try again.");
  assert.equal(voiceFailureView({ error: undefined }).kind, "unknown");
});
