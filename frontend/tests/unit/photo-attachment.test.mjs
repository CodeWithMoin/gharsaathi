import assert from "node:assert/strict";
import test from "node:test";
import { applyAssistantEvent } from "../../app/assistant-events.mjs";
import {
  GALLERY_ACCEPT,
  PHOTO_PLACEHOLDER,
  attachmentIdFor,
  canSend,
  downscaleSize,
  idlePhoto,
  isHeif,
  photoMessageContent,
  photoReducer,
  uploadErrorMessage,
  validatePhotoFile,
} from "../../app/photo-attachment.mjs";

test("downscale keeps the aspect ratio, caps the long side at 1600 and never enlarges", () => {
  assert.deepEqual(downscaleSize(4032, 3024), { width: 1600, height: 1200 });
  assert.deepEqual(downscaleSize(3024, 4032), { width: 1200, height: 1600 });
  assert.deepEqual(downscaleSize(1000, 5000), { width: 320, height: 1600 });
  assert.deepEqual(downscaleSize(1600, 900), { width: 1600, height: 900 });
  assert.deepEqual(downscaleSize(640, 480), { width: 640, height: 480 });
  assert.deepEqual(downscaleSize(20000, 1), { width: 1600, height: 1 });
  assert.deepEqual(downscaleSize(0, 100), { width: 0, height: 0 });
});

test("file validation accepts photos (including HEIC with no type) and refuses others", () => {
  assert.equal(validatePhotoFile({ type: "image/jpeg", size: 3_000_000 }), null);
  assert.equal(validatePhotoFile({ type: "image/heic", size: 3_000_000 }), null);
  assert.equal(validatePhotoFile({ type: "", size: 3_000_000, name: "IMG_0001.HEIC" }), null);
  assert.equal(isHeif({ type: "", name: "IMG_0001.HEIC" }), true);
  assert.match(validatePhotoFile({ type: "application/pdf", size: 10 }), /JPEG, PNG, WebP or HEIC/);
  assert.match(validatePhotoFile({ type: "image/svg+xml", size: 10 }), /JPEG, PNG, WebP or HEIC/);
  assert.match(validatePhotoFile({ type: "image/jpeg", size: 50 * 1024 * 1024 }), /too large/);
  assert.match(validatePhotoFile(null), /No photo/);
  assert.equal(GALLERY_ACCEPT, "image/jpeg,image/png,image/webp,image/heic,image/heif");
});

test("upload errors map to plain messages", () => {
  assert.match(uploadErrorMessage(413), /too large/);
  assert.match(uploadErrorMessage(415), /JPEG/);
  assert.match(uploadErrorMessage(422), /couldn't be read/);
  assert.match(uploadErrorMessage(500), /Try again/);
});

test("attachment state: upload, ready, consumed; stale uploads cannot overwrite a newer photo", () => {
  let state = photoReducer(idlePhoto, { type: "start", token: 1, previewUrl: "blob:a" });
  assert.equal(state.status, "uploading");
  assert.equal(canSend(state, "atta"), false, "never send mid-upload");
  state = photoReducer(state, { type: "start", token: 2, previewUrl: "blob:b" });
  assert.equal(photoReducer(state, { type: "uploaded", token: 1, attachmentId: "ph_old" }), state);
  assert.equal(photoReducer(state, { type: "failed", token: 1, message: "x" }), state);
  state = photoReducer(state, { type: "uploaded", token: 2, attachmentId: "ph_new" });
  assert.equal(state.status, "ready");
  assert.equal(attachmentIdFor(state), "ph_new");
  assert.equal(canSend(state, ""), true, "a photo can be sent without text");
  assert.equal(photoReducer(state, { type: "consumed", attachmentId: "ph_other" }), state);
  assert.equal(photoReducer(state, { type: "consumed", attachmentId: "ph_new" }).status, "idle");
  assert.equal(photoReducer(state, { type: "clear" }).status, "idle");
});

test("rejected and failed photos show an error and do not enable sending", () => {
  const rejected = photoReducer(idlePhoto, { type: "rejected", token: 3, message: "Choose a JPEG" });
  assert.equal(rejected.status, "error");
  assert.equal(attachmentIdFor(rejected), null);
  assert.equal(canSend(rejected, ""), false);
  assert.equal(canSend(rejected, "doodh"), true);
  assert.equal(canSend(idlePhoto, "  "), false);
});

test("the saved user turn shows a Photo placeholder, never image data", () => {
  assert.equal(photoMessageContent(""), PHOTO_PLACEHOLDER);
  assert.equal(photoMessageContent("  is list se mangao "), `${PHOTO_PLACEHOLDER}\nis list se mangao`);
});

test("photo.read events clear the chip through onPhotoRead; malformed ones are ignored", () => {
  const seen = [];
  const outcome = applyAssistantEvent({ type: "photo.read", attachment_id: "ph_abc", item_count: 3 }, { onPhotoRead: (read) => seen.push(read) });
  assert.deepEqual(seen, [{ attachmentId: "ph_abc", itemCount: 3 }]);
  assert.equal(outcome.kind, "photo");
  assert.match(outcome.message, /3 items/);
  assert.equal(applyAssistantEvent({ type: "photo.read" }, { onPhotoRead: () => assert.fail() }), null);
  const none = applyAssistantEvent({ type: "photo.read", attachment_id: "ph_x", item_count: 0 }, {});
  assert.match(none.message, /No shopping items/);
});
