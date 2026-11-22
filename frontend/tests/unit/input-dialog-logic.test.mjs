import assert from "node:assert/strict";
import test from "node:test";
import { dialogPresentation, hasUnsavedChanges, nextFocusIndex, resolveCloseRequest } from "../../app/ui/input-dialog-logic.mjs";

test("bottom sheet under 640px, centered dialog above", () => {
  assert.equal(dialogPresentation(390), "sheet");
  assert.equal(dialogPresentation(639), "sheet");
  assert.equal(dialogPresentation(640), "centered");
  assert.equal(dialogPresentation(Number.NaN), "centered");
});

test("unsaved-changes guard asks before discarding", () => {
  let asked = 0;
  const confirm = (answer) => () => { asked += 1; return answer; };
  assert.equal(resolveCloseRequest({ dirty: false, source: "escape" }), "close");
  assert.equal(resolveCloseRequest({ dirty: true, source: "backdrop", confirmDiscard: confirm(false) }), "stay");
  assert.equal(resolveCloseRequest({ dirty: true, source: "cancel", confirmDiscard: confirm(true) }), "close");
  assert.equal(asked, 2);
  assert.equal(resolveCloseRequest({ dirty: true, source: "escape" }), "stay");
  assert.equal(resolveCloseRequest({ dirty: false, busy: true, source: "escape" }), "stay");
  assert.equal(resolveCloseRequest({ dirty: true, busy: true, source: "done" }), "close");
});

test("focus trap wraps in both directions", () => {
  assert.equal(nextFocusIndex(3, 2, false), 0);
  assert.equal(nextFocusIndex(3, 0, true), 2);
  assert.equal(nextFocusIndex(3, -1, false), 0);
  assert.equal(nextFocusIndex(3, -1, true), 2);
  assert.equal(nextFocusIndex(0, -1, false), -1);
});

test("unsaved changes compare trimmed values", () => {
  assert.equal(hasUnsavedChanges({ type: "HOME" }, { type: "HOME", name: " " }), false);
  assert.equal(hasUnsavedChanges({ type: "HOME" }, { type: "HOME", latitude: "12.1" }), true);
});
