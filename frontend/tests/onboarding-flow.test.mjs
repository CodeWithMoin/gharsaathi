import assert from "node:assert/strict";
import test from "node:test";
import {
  addressFormExample,
  applicableSteps,
  byAddressForm,
  canSkip,
  isOnboardingComplete,
  nextOnboardingStep,
  normalizeProfile,
  progressPosition,
  readOnboardingProgress,
  resolveProfile,
  stepAfter,
  stepBefore,
  validateProfileDraft,
  withOnboardingProgress,
} from "../app/onboarding-flow.mjs";

const fresh = { decisions: {}, completedAt: null, legacyComplete: false };

test("a brand-new user starts on the profile step", () => {
  assert.equal(nextOnboardingStep({ profileComplete: false, consentAccepted: false, progress: fresh }), "profile");
});

test("resumes at the first incomplete step", () => {
  const base = { profileComplete: true, consentAccepted: true, stores: { swiggy: false, zepto: false } };
  assert.equal(nextOnboardingStep({ profileComplete: true, consentAccepted: false, progress: fresh }), "consent");
  assert.equal(nextOnboardingStep({ ...base, progress: fresh }), "stores");
  assert.equal(nextOnboardingStep({ ...base, stores: { zepto: true }, progress: { ...fresh, decisions: { stores: "done" } } }), "address");
  assert.equal(nextOnboardingStep({ ...base, stores: { zepto: true }, progress: { ...fresh, decisions: { stores: "done", address: "skipped" } } }), "personalize");
  assert.equal(nextOnboardingStep({ ...base, stores: { zepto: true }, progress: { ...fresh, decisions: { stores: "done", address: "done", personalize: "done" } } }), "done");
});

test("skipping stores also skips address and history import", () => {
  const snapshot = { profileComplete: true, consentAccepted: true, stores: {}, progress: { ...fresh, decisions: { stores: "skipped" } } };
  assert.equal(nextOnboardingStep(snapshot), "done");
  assert.deepEqual(applicableSteps(snapshot), ["profile", "stores", "done"]);
});

test("returning from a provider OAuth redirect lands back on the stores step", () => {
  const snapshot = { profileComplete: true, consentAccepted: true, stores: { swiggy: true }, oauthReturn: true, progress: { ...fresh, decisions: { stores: "skipped" } } };
  assert.equal(nextOnboardingStep(snapshot), "stores");
});

test("an OAuth return never bypasses profile or consent", () => {
  assert.equal(nextOnboardingStep({ profileComplete: false, consentAccepted: false, oauthReturn: true, progress: fresh }), "profile");
  assert.equal(nextOnboardingStep({ profileComplete: true, consentAccepted: false, oauthReturn: true, progress: fresh }), "consent");
});

test("completed users never see onboarding, except for a new terms version", () => {
  const done = { ...fresh, completedAt: "2026-11-22T00:00:00.000Z" };
  assert.equal(nextOnboardingStep({ profileComplete: true, consentAccepted: true, stores: {}, progress: done }), null);
  assert.equal(nextOnboardingStep({ profileComplete: true, consentAccepted: true, oauthReturn: true, progress: done }), null);
  assert.equal(nextOnboardingStep({ profileComplete: true, consentAccepted: false, progress: done }), "consent");
});

test("legacy completed profiles are treated as complete even without an address form", () => {
  const progress = readOnboardingProgress({ gharsaathiProfile: { complete: true, language: "hindi" } });
  assert.equal(progress.legacyComplete, true);
  assert.equal(isOnboardingComplete(progress), true);
  assert.equal(nextOnboardingStep({ profileComplete: false, consentAccepted: true, progress }), null);
});

test("an old half-finished profile does not count as complete", () => {
  const progress = readOnboardingProgress({ gharsaathiProfile: { complete: false, language: "hinglish" } });
  assert.equal(isOnboardingComplete(progress), false);
});

test("ignores malformed progress metadata", () => {
  const progress = readOnboardingProgress({ gharsaathiProfile: { onboarding: { decisions: { stores: "maybe", consent: "done", address: "skipped" }, completedAt: 5 } } });
  assert.deepEqual(progress, { decisions: { address: "skipped" }, completedAt: null, legacyComplete: false });
  assert.deepEqual(readOnboardingProgress(null), fresh);
});

test("records decisions without dropping unrelated metadata", () => {
  const start = { other: 1, gharsaathiProfile: { language: "english", addressForm: "feminine" } };
  const once = withOnboardingProgress(start, { step: "stores", decision: "skipped" });
  assert.equal(once.other, 1);
  assert.equal(once.gharsaathiProfile.language, "english");
  assert.deepEqual(readOnboardingProgress(once).decisions, { stores: "skipped" });
  const twice = withOnboardingProgress(once, { completedAt: "2026-11-22T10:00:00.000Z" });
  assert.equal(twice.gharsaathiProfile.complete, true);
  assert.equal(readOnboardingProgress(twice).completedAt, "2026-11-22T10:00:00.000Z");
  assert.deepEqual(readOnboardingProgress(twice).decisions, { stores: "skipped" });
  assert.equal(start.gharsaathiProfile.onboarding, undefined, "input must not be mutated");
});

test("forward and back navigation skip steps that do not apply", () => {
  const connected = { consentAccepted: true, stores: { swiggy: true } };
  assert.equal(stepAfter("profile", connected), "stores");
  assert.equal(stepAfter("stores", connected), "address");
  assert.equal(stepAfter("personalize", connected), "done");
  assert.equal(stepAfter("stores", { consentAccepted: true, stores: {} }), "done");
  assert.equal(stepAfter("consent", { consentAccepted: true, stores: {} }), "stores");
  assert.equal(stepBefore("stores", connected), "profile");
  assert.equal(stepBefore("stores", { consentAccepted: false }), "consent");
  assert.equal(stepBefore("done", { consentAccepted: true, stores: {} }), "stores");
  assert.equal(stepBefore("profile", connected), null);
});

test("progress uses a fixed five-step bar and only setup choices are skippable", () => {
  assert.deepEqual(progressPosition("profile"), { current: 1, total: 5, complete: false });
  assert.deepEqual(progressPosition("personalize"), { current: 5, total: 5, complete: false });
  assert.equal(progressPosition("done").complete, true);
  assert.deepEqual(["profile", "consent", "stores", "address", "personalize"].map(canSkip), [false, false, true, true, true]);
});

test("validates and normalises the profile draft", () => {
  assert.deepEqual(validateProfileDraft({ firstName: "  Anita   Devi ", addressForm: "feminine", language: "hinglish" }).value, { first_name: "Anita Devi", address_form: "feminine", language: "hinglish" });
  assert.equal(validateProfileDraft({ firstName: "अनीता", addressForm: "neutral", language: "hindi" }).ok, true);
  assert.equal(validateProfileDraft({ firstName: "D'Souza-Rao", addressForm: "masculine", language: "english" }).ok, true);
  assert.ok(validateProfileDraft({ firstName: "   ", addressForm: "neutral", language: "hinglish" }).errors.firstName);
  assert.ok(validateProfileDraft({ firstName: "9876543210", addressForm: "neutral", language: "hinglish" }).errors.firstName);
  assert.ok(validateProfileDraft({ firstName: "me@example.com", addressForm: "neutral", language: "hinglish" }).errors.firstName);
  assert.ok(validateProfileDraft({ firstName: "A".repeat(41), addressForm: "neutral", language: "hinglish" }).errors.firstName);
  assert.ok(validateProfileDraft({ firstName: "Ravi", addressForm: null, language: "hinglish" }).errors.addressForm);
  assert.ok(validateProfileDraft({ firstName: "Ravi", addressForm: "neutral", language: "tamil" }).errors.language);
});

test("parses the /api/profile contract strictly", () => {
  assert.deepEqual(normalizeProfile({ first_name: " Ravi ", address_form: "masculine", language: "english", extra: true }), { first_name: "Ravi", address_form: "masculine", language: "english" });
  assert.equal(normalizeProfile({ first_name: "Ravi", address_form: "male", language: "english" }), null);
  assert.equal(normalizeProfile({ first_name: "", address_form: "neutral", language: "english" }), null);
  assert.equal(normalizeProfile("Ravi"), null);
});

test("prefers the server profile and falls back to Clerk metadata", () => {
  const server = { first_name: "Server", address_form: "neutral", language: "english" };
  const clerkMetadata = { gharsaathiProfile: { firstName: "Clerk", addressForm: "feminine", language: "hindi" } };
  assert.equal(resolveProfile({ server, clerkMetadata }).source, "server");
  const fallback = resolveProfile({ server: null, clerkMetadata, clerkFirstName: "Ignored" });
  assert.equal(fallback.source, "clerk");
  assert.deepEqual(fallback.profile, { first_name: "Clerk", address_form: "feminine", language: "hindi" });
});

test("an incomplete profile still prefills the draft", () => {
  const resolved = resolveProfile({ clerkMetadata: { gharsaathiProfile: { language: "hindi" } }, clerkFirstName: " Meena " });
  assert.equal(resolved.profile, null);
  assert.deepEqual(resolved.draft, { firstName: "Meena", addressForm: null, language: "hindi" });
  assert.deepEqual(resolveProfile().draft, { firstName: "", addressForm: null, language: "hinglish" });
});

test("the live example changes with address form and language", () => {
  assert.equal(addressFormExample("masculine", "hinglish", "Ravi").text, "Ravi, aap dekh rahe hain, Amul doodh dono stores par hai. Kaunsa mangwaun?");
  assert.match(addressFormExample("feminine", "hinglish", "").text, /^Aap dekh rahi hain,/);
  assert.match(addressFormExample("neutral", "hinglish", "").text, /^Yeh dekhiye,/);
  assert.equal(addressFormExample("neutral", "hinglish").gendered, false);
  assert.match(addressFormExample("feminine", "hindi", "").text, /देख रही हैं/);
  const english = addressFormExample("feminine", "english", "Asha");
  assert.equal(english.gendered, false);
  assert.match(english.text, /^Asha, here's/);
});

test("picks copy by address form with a neutral fallback", () => {
  const variants = { masculine: "gaye", feminine: "gayi", neutral: "ho gaya" };
  assert.equal(byAddressForm("feminine", variants), "gayi");
  assert.equal(byAddressForm(null, variants), "ho gaya");
});
