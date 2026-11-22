import assert from "node:assert/strict";
import test from "node:test";
import {
  CAMERA_IDEAL_WIDTH,
  cameraConstraints,
  cameraErrorKind,
  cameraErrorMessage,
  cameraPath,
  cameraReducer,
  cameraStatusText,
  captureSize,
  idleCamera,
  mirrorPreview,
  nextCamera,
  videoInputs,
} from "../../app/camera-capture.mjs";

const MAC_CHROME = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36";
const MAC_SAFARI = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15";
const IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1";
const ANDROID = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36";
const WINDOWS = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36";

test("desktops and laptops use the webcam dialog", () => {
  assert.equal(cameraPath({ userAgent: MAC_CHROME, platform: "MacIntel", maxTouchPoints: 0, hasGetUserMedia: true }), "webcam");
  assert.equal(cameraPath({ userAgent: MAC_SAFARI, platform: "MacIntel", maxTouchPoints: 0, hasGetUserMedia: true }), "webcam");
  assert.equal(cameraPath({ userAgent: WINDOWS, platform: "Win32", maxTouchPoints: 0, hasGetUserMedia: true, mobileHint: false }), "webcam");
  // A touchscreen laptop whose main pointer is still a trackpad or mouse.
  assert.equal(cameraPath({ userAgent: WINDOWS, platform: "Win32", maxTouchPoints: 10, coarsePointer: false, hasGetUserMedia: true }), "webcam");
});

test("phones and tablets keep the native capture input", () => {
  assert.equal(cameraPath({ userAgent: IPHONE, maxTouchPoints: 5, coarsePointer: true, hasGetUserMedia: true }), "native");
  assert.equal(cameraPath({ userAgent: ANDROID, maxTouchPoints: 5, coarsePointer: true, hasGetUserMedia: true }), "native");
  // iPadOS reports a Mac user agent but has touch points.
  assert.equal(cameraPath({ userAgent: MAC_SAFARI, platform: "MacIntel", maxTouchPoints: 5, hasGetUserMedia: true }), "native");
  assert.equal(cameraPath({ userAgent: WINDOWS, mobileHint: true, hasGetUserMedia: true }), "native");
  assert.equal(cameraPath({ userAgent: WINDOWS, maxTouchPoints: 5, coarsePointer: true, hasGetUserMedia: true }), "native");
});

test("without getUserMedia (insecure page, old browser) the file input is the only option", () => {
  assert.equal(cameraPath({ userAgent: MAC_CHROME, platform: "MacIntel", hasGetUserMedia: false }), "native");
  assert.equal(cameraPath(), "native");
});

test("constraints ask for one camera at about 1600 px wide and never audio", () => {
  assert.deepEqual(cameraConstraints(), { audio: false, video: { facingMode: "user", width: { ideal: CAMERA_IDEAL_WIDTH } } });
  assert.deepEqual(cameraConstraints({ facingMode: "environment" }).video, { facingMode: "environment", width: { ideal: 1600 } });
  assert.deepEqual(cameraConstraints({ deviceId: "cam-2" }), { audio: false, video: { deviceId: { exact: "cam-2" }, width: { ideal: 1600 } } });
});

test("device list keeps video inputs only and labels unnamed ones", () => {
  const cameras = videoInputs([
    { kind: "audioinput", deviceId: "mic", label: "MacBook Microphone" },
    { kind: "videoinput", deviceId: "a", label: "FaceTime HD Camera" },
    { kind: "videoinput", deviceId: "b", label: "" },
    { kind: "videoinput", deviceId: "", label: "No id before permission" },
  ]);
  assert.deepEqual(cameras, [{ deviceId: "a", label: "FaceTime HD Camera" }, { deviceId: "b", label: "Camera 2" }]);
  assert.deepEqual(videoInputs(null), []);
});

test("switching cycles through cameras and is unavailable with one camera", () => {
  const cameras = [{ deviceId: "a", label: "A" }, { deviceId: "b", label: "B" }, { deviceId: "c", label: "C" }];
  assert.equal(nextCamera(cameras, "a").deviceId, "b");
  assert.equal(nextCamera(cameras, "c").deviceId, "a");
  assert.equal(nextCamera(cameras, "unknown").deviceId, "a");
  assert.equal(nextCamera(cameras.slice(0, 1), "a"), null);
  assert.equal(nextCamera([], null), null);
});

test("front and laptop cameras are mirrored in the preview; back cameras are not", () => {
  assert.equal(mirrorPreview({ facingMode: "user" }), true);
  assert.equal(mirrorPreview({ facingMode: "environment", label: "FaceTime" }), false);
  assert.equal(mirrorPreview({ label: "FaceTime HD Camera" }), true);
  assert.equal(mirrorPreview({ label: "Back Camera" }), false);
  assert.equal(mirrorPreview({ label: "Logitech BRIO" }), true);
  assert.equal(mirrorPreview(), true);
});

test("capture uses the stream's own resolution", () => {
  assert.deepEqual(captureSize(1920, 1080), { width: 1920, height: 1080 });
  assert.deepEqual(captureSize(1279.6, 720.2), { width: 1280, height: 720 });
  assert.deepEqual(captureSize(0, 0, { width: 1600, height: 1200 }), { width: 1600, height: 1200 });
  assert.deepEqual(captureSize(0, 0, {}), { width: 0, height: 0 });
  assert.deepEqual(captureSize(Number.NaN, 10), { width: 0, height: 0 });
});

test("getUserMedia errors map to states the user can act on", () => {
  assert.equal(cameraErrorKind({ name: "NotAllowedError" }), "denied");
  assert.equal(cameraErrorKind({ name: "SecurityError" }), "denied");
  assert.equal(cameraErrorKind({ name: "NotFoundError" }), "not_found");
  assert.equal(cameraErrorKind({ name: "OverconstrainedError" }), "not_found");
  assert.equal(cameraErrorKind({ name: "NotReadableError" }), "in_use");
  assert.equal(cameraErrorKind({ name: "TypeError" }), "unknown");
  assert.equal(cameraErrorKind(null), "unknown");
  assert.equal(cameraErrorMessage("denied"), "Allow camera access in your browser settings, or choose a photo from your files.");
  for (const kind of ["denied", "not_found", "in_use", "unknown"]) assert.match(cameraErrorMessage(kind), /choose a photo from your files/);
});

test("dialog state moves start → live → captured and ignores late events", () => {
  let state = cameraReducer(idleCamera, { type: "start", prompt: true });
  assert.deepEqual(state, { status: "starting", prompt: true });
  assert.equal(cameraStatusText(state), "Allow camera access when your browser asks.");
  assert.deepEqual(cameraReducer(idleCamera, { type: "captured" }), idleCamera);
  state = cameraReducer(state, { type: "live" });
  assert.deepEqual(state, { status: "live" });
  state = cameraReducer(state, { type: "captured" });
  assert.deepEqual(state, { status: "captured" });
  // A late loadedmetadata or error after capture changes nothing.
  assert.deepEqual(cameraReducer(state, { type: "live" }), state);
  assert.deepEqual(cameraReducer(state, { type: "failed", kind: "unknown" }), state);
  assert.deepEqual(cameraReducer(state, { type: "start" }), { status: "starting", prompt: false });
  assert.deepEqual(cameraReducer(state, { type: "reset" }), idleCamera);
});

test("hiding the tab pauses a starting or live camera; errors explain what to do", () => {
  assert.deepEqual(cameraReducer({ status: "live" }, { type: "paused" }), { status: "paused" });
  assert.deepEqual(cameraReducer({ status: "captured" }, { type: "paused" }), { status: "captured" });
  const denied = cameraReducer({ status: "starting", prompt: true }, { type: "failed", kind: "denied" });
  assert.deepEqual(denied, { status: "error", kind: "denied" });
  assert.equal(cameraStatusText(denied), cameraErrorMessage("denied"));
  assert.equal(cameraStatusText(idleCamera), "");
});
