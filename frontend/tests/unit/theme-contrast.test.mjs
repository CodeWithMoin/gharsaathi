import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import test from "node:test";
import { CLERK_COLORS } from "../../app/ui/clerk-colors.mjs";

const app = new URL("../../app/", import.meta.url);
const css = readFileSync(new URL("globals.css", app), "utf8");
const tokens = Object.fromEntries([...css.matchAll(/--color-([a-z-]+):\s*(\d+) (\d+) (\d+);/g)].map(([, name, r, g, b]) => [name, [r, g, b].map(Number)]));

function luminance([r, g, b]) {
  const [R, G, B] = [r, g, b].map((v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; });
  return 0.2126 * R + 0.7152 * G + 0.0722 * B;
}
function contrast(a, b) {
  const [hi, lo] = [luminance(tokens[a]), luminance(tokens[b])].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}
const hex = (rgb) => "#" + rgb.map((v) => v.toString(16).padStart(2, "0")).join("");

// [foreground, background] pairs that carry body-size text: WCAG AA 4.5:1.
const TEXT = [
  ...["text", "text-secondary", "text-muted", "text-subtle", "accent", "accent-strong", "danger", "success", "zepto"].flatMap((fg) => ["bg", "surface", "surface-muted"].map((bg) => [fg, bg])),
  ["text", "surface-strong"], ["text-secondary", "surface-strong"], ["text-muted", "surface-strong"],
  ["text", "accent-soft"], ["text-secondary", "accent-soft"], ["text-muted", "accent-soft"], ["text-subtle", "accent-soft"], ["accent", "accent-soft"], ["accent-strong", "accent-soft"],
  ["on-accent", "accent"], ["on-accent", "accent-hover"], ["on-accent", "accent-strong"], ["on-accent", "danger"], ["on-accent", "danger-hover"], ["on-accent", "success"], ["on-accent", "success-strong"], ["on-accent", "zepto-strong"], ["on-accent", "zepto-hover"],
  ["on-user-bubble", "user-bubble"], ["text-muted", "user-bubble"],
  ["on-inverse", "inverse"], ["on-inverse", "inverse-hover"],
  ["success", "success-soft"], ["success-strong", "success-soft"],
  ["warning", "warning-soft"], ["warning", "surface"],
  ["danger", "danger-soft"], ["danger-hover", "danger-soft"],
  ["zepto-strong", "zepto-soft"], ["zepto-ink", "zepto-soft"], ["zepto-muted", "zepto-soft"], ["zepto", "zepto-soft"],
];
// Large text, icons, focus rings and status marks: WCAG AA 3:1.
const UI = [
  ["focus", "surface"], ["focus", "bg"], ["accent", "surface"],
  ["meter-user", "surface"], ["meter-assistant", "surface"],
  ["accent", "accent-soft"], ["danger", "surface"], ["success", "surface"],
];

test("every token pair used for text meets WCAG AA 4.5:1", () => {
  const failing = TEXT.map(([fg, bg]) => [fg, bg, contrast(fg, bg)]).filter(([, , ratio]) => ratio < 4.5);
  assert.deepEqual(failing.map(([fg, bg, r]) => `${fg} on ${bg}: ${r.toFixed(2)}`), []);
});

test("UI marks and large text meet WCAG AA 3:1", () => {
  const failing = UI.map(([fg, bg]) => [fg, bg, contrast(fg, bg)]).filter(([, , ratio]) => ratio < 3);
  assert.deepEqual(failing.map(([fg, bg, r]) => `${fg} on ${bg}: ${r.toFixed(2)}`), []);
});

test("the two meter colours stay distinguishable from each other", () => {
  // Charcoal vs terracotta also differ in hue, and the pill names the speaker in text.
  assert.ok(contrast("meter-user", "meter-assistant") >= 2.5);
  assert.notDeepEqual(tokens["meter-user"], tokens["meter-assistant"]);
});

test("Clerk's literal colours match the tokens", () => {
  assert.equal(CLERK_COLORS.colorPrimary, hex(tokens.accent));
  assert.equal(CLERK_COLORS.colorText, hex(tokens.text));
  assert.equal(CLERK_COLORS.colorTextSecondary, hex(tokens["text-muted"]));
  assert.equal(CLERK_COLORS.colorBackground, hex(tokens.surface));
});

test("app code uses colour tokens instead of hard-coded hex values", () => {
  const offenders = [];
  (function walk(dir) {
    for (const name of readdirSync(dir)) {
      const url = new URL(name, dir);
      if (statSync(url).isDirectory()) walk(new URL(`${name}/`, dir));
      else if (/\.(tsx?|css)$/.test(name) && name !== "globals.css") {
        readFileSync(url, "utf8").split("\n").forEach((line, i) => { if (/#[0-9a-fA-F]{6}\b/.test(line)) offenders.push(`${url.pathname.split("/app/")[1]}:${i + 1}`); });
      }
    }
  })(app);
  assert.deepEqual(offenders, []);
});
