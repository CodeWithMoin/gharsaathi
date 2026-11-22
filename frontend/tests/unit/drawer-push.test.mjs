import assert from "node:assert/strict";
import test from "node:test";
import { DRAWER_PANEL_TRANSITION, DRAWER_SPRING, drawerState, drawerWidth, pushOffset, restoreScrollTarget, viewportInsets } from "../../app/drawer-push.mjs";

test("drawer width is 75% of the viewport, capped at 360px", () => {
  assert.equal(drawerWidth(390), 292.5);
  assert.equal(drawerWidth(1440), 360);
  assert.equal(drawerWidth(0), 0);
  assert.equal(drawerWidth(Number.NaN), 0);
});

test("chats push the page right and settings mirror it to the left", () => {
  assert.equal(pushOffset("chats", 390), 292.5);
  assert.equal(pushOffset("settings", 390), -292.5);
  assert.equal(pushOffset("settings", 1440), -360);
  assert.equal(pushOffset(null, 390), 0);
});

test("reduced motion never pushes the page", () => {
  assert.equal(pushOffset("chats", 390, true), 0);
  assert.equal(pushOffset("settings", 390, true), 0);
});

test("only one drawer is open at a time", () => {
  assert.deepEqual(drawerState("chats"), { chatsOpen: true, settingsOpen: false });
  assert.deepEqual(drawerState("settings"), { chatsOpen: false, settingsOpen: true });
  assert.deepEqual(drawerState(null), { chatsOpen: false, settingsOpen: false });
});

test("both drawers share one spring and panels slide with it, fading fast", () => {
  assert.deepEqual({ ...DRAWER_SPRING }, { type: "spring", stiffness: 420, damping: 40 });
  assert.equal(DRAWER_PANEL_TRANSITION.stiffness, DRAWER_SPRING.stiffness);
  assert.equal(DRAWER_PANEL_TRANSITION.damping, DRAWER_SPRING.damping);
  assert.ok(DRAWER_PANEL_TRANSITION.opacity.duration <= 0.15);
});

test("closing a drawer returns the reader to their scroll position, clamped", () => {
  assert.equal(restoreScrollTarget({ savedScrollY: 1500, follow: false, scrollHeight: 4000, viewportHeight: 844 }), 1500);
  assert.equal(restoreScrollTarget({ savedScrollY: 3900, follow: false, scrollHeight: 2000, viewportHeight: 844 }), 1156);
  assert.equal(restoreScrollTarget({ savedScrollY: -20, follow: false, scrollHeight: 2000, viewportHeight: 844 }), 0);
});

test("following the latest message scrolls to the bottom after a drawer closes", () => {
  assert.equal(restoreScrollTarget({ savedScrollY: 200, follow: true, scrollHeight: 4000, viewportHeight: 844 }), 3156);
  assert.equal(restoreScrollTarget({ savedScrollY: 200, follow: true, scrollHeight: 500, viewportHeight: 844 }), 0);
});

test("viewport insets are zero without a keyboard", () => {
  assert.deepEqual(viewportInsets({ layoutHeight: 844, visualHeight: 844, visualOffsetTop: 0 }), { top: 0, bottom: 0 });
});

test("a keyboard that shrinks the visual viewport lifts the composer above it", () => {
  assert.deepEqual(viewportInsets({ layoutHeight: 844, visualHeight: 508, visualOffsetTop: 0 }), { top: 0, bottom: 336 });
});

test("an iOS keyboard that also scrolls the visual viewport keeps the header and composer visible", () => {
  assert.deepEqual(viewportInsets({ layoutHeight: 844, visualHeight: 508.4, visualOffsetTop: 120.2 }), { top: 120, bottom: 215 });
});

test("pinch-zoom and bad input leave the bars alone", () => {
  assert.deepEqual(viewportInsets({ layoutHeight: 844, visualHeight: 400, visualOffsetTop: 100, scale: 2 }), { top: 0, bottom: 0 });
  assert.deepEqual(viewportInsets({ layoutHeight: Number.NaN, visualHeight: 400, visualOffsetTop: 0 }), { top: 0, bottom: 0 });
  assert.deepEqual(viewportInsets({ layoutHeight: 844, visualHeight: 900, visualOffsetTop: 0 }), { top: 0, bottom: 0 });
});
