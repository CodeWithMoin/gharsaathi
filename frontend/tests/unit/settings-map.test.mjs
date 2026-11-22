import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import test from "node:test";
import { MEMORY_SWITCH_LABEL, MEMORY_TERM, OVERVIEW_TITLE, SETTINGS_DRAWER_CLASS, SETTINGS_DRAWER_MAX, sectionHeading, settingsRows } from "../../app/settings-map.mjs";
import { drawerWidth, pushOffset } from "../../app/drawer-push.mjs";

const app = new URL("../../app/", import.meta.url);
const source = (name) => readFileSync(new URL(name, app), "utf8");

test("the drawer has Stores, What GharSaathi knows and Delivery line under the profile row", () => {
  assert.deepEqual(settingsRows().map((row) => row.title), ["Stores", "What GharSaathi knows", "Delivery line (Beta)"]);
  assert.deepEqual(settingsRows().map((row) => row.section), ["stores", "memory", "delivery"]);
});

test("Rider assist (Vobiz) appears only when the server reports it on", () => {
  assert.ok(!settingsRows({ riderAssist: false }).some((row) => row.id === "rider"));
  assert.equal(settingsRows({ riderAssist: true }).at(-1)?.title, "Rider assist (Beta)");
});

test("removed rows stay out of the drawer", () => {
  const titles = settingsRows({ riderAssist: true }).map((row) => row.title);
  for (const gone of ["Addresses", "Notifications", "Privacy & memory", "Preferences", "Help with an order"]) assert.ok(!titles.includes(gone), gone);
});

test("every section has one heading; unknown sections fall back to the overview", () => {
  assert.equal(sectionHeading("profile"), "Profile");
  assert.equal(sectionHeading("memory"), "What GharSaathi knows");
  assert.equal(sectionHeading("delivery"), "Delivery line");
  assert.equal(sectionHeading(null), OVERVIEW_TITLE);
  assert.equal(sectionHeading(/** @type {any} */ ("privacy")), OVERVIEW_TITLE);
});

test("one drawer width on every page, and the page is pushed by exactly that width", () => {
  const sheet = source("account-settings-sheet.tsx");
  assert.ok(sheet.includes("${SETTINGS_DRAWER_CLASS}"));
  assert.ok(!/w-\[min\(94vw,420px\)\]|max-w-\[360px\]/.test(sheet), "no per-section widths");
  assert.equal(SETTINGS_DRAWER_CLASS, "w-[85vw] max-w-[400px]");
  assert.equal(drawerWidth(390, "settings"), 331.5);
  assert.equal(drawerWidth(360, "settings"), 306);
  assert.equal(drawerWidth(1440, "settings"), SETTINGS_DRAWER_MAX);
  assert.equal(pushOffset("settings", 390), -331.5);
  assert.equal(pushOffset("chats", 390), 292.5);
});

test("only Stores gets the solid square drawer rail", () => {
  const sheet = source("account-settings-sheet.tsx");
  const page = source("page.tsx");
  assert.ok(sheet.includes('open && section === "stores"'));
  assert.ok(sheet.includes('section === "stores" ? "" : "rounded-l-[28px]"'));
  assert.ok(page.includes('className="fixed inset-0 z-40 bg-scrim/20"'));
});

test("nothing in Settings opens the legacy setup sheet or onboarding", () => {
  const page = source("page.tsx");
  const start = page.indexOf("<AccountSettingsSheet");
  const block = page.slice(start, page.indexOf("/>", page.indexOf("onSignOut=", start)));
  assert.ok(start > 0 && block.length > 0);
  assert.ok(!block.includes("setSetupOpen"), "Settings callbacks must not open the setup sheet");
  assert.ok(!block.includes("onOpenSetup") && !block.includes("openSetup"), "Settings must not reopen onboarding");
  const sheet = source("account-settings-sheet.tsx");
  for (const gone of ["onManagePreferences", "onManagePrivacy", "onManageAddresses", "More preferences", "Privacy & memory"]) assert.ok(!sheet.includes(gone), gone);
});

test("the Vobiz rider page is rendered only behind the server flag", () => {
  const sheet = source("account-settings-sheet.tsx");
  assert.ok(sheet.includes('section === "rider" && riderAssistAvailable && <RiderAssistSettings />'));
  assert.ok(!sheet.includes("Get a Vobiz account") && !sheet.includes("**61*"), "no Vobiz setup copy in the drawer itself");
});

test("one memory term: no 'saved preferences' left in the app", () => {
  assert.equal(MEMORY_TERM, "Memory");
  assert.equal(MEMORY_SWITCH_LABEL, "Use what I've told you");
  const files = readdirSync(app).filter((name) => /\.(tsx|ts|mjs)$/.test(name));
  const offenders = files.filter((name) => /saved preferences/i.test(source(name)));
  assert.deepEqual(offenders, []);
  assert.ok(source("memory-settings.tsx").includes("MEMORY_SWITCH_LABEL"));
});

test("Sign out is a text link, not a big button", () => {
  const sheet = source("account-settings-sheet.tsx");
  const signOut = sheet.slice(sheet.indexOf("onClick={onSignOut}") - 40, sheet.indexOf("Sign out\"}"));
  assert.ok(!signOut.includes("w-full"), "not full width");
  assert.ok(!signOut.includes("border"), "no button outline");
});
