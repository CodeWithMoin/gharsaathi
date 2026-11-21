// Pure first-run onboarding logic: step order, resumability, and profile validation.
// Kept framework-free so it can be tested with `node --test`.

export const ONBOARDING_VERSION = 2;
export const SETUP_STEPS = /** @type {const} */ (["profile", "consent", "stores", "address", "personalize"]);
export const ADDRESS_FORMS = /** @type {const} */ (["masculine", "feminine", "neutral"]);
export const LANGUAGES = /** @type {const} */ (["hinglish", "english", "hindi"]);
export const FIRST_NAME_MAX_LENGTH = 40;

const DECISIONS = new Set(["done", "skipped"]);
const DECISION_STEPS = new Set(["stores", "address", "personalize"]);

export function normalizeFirstName(raw) {
  return typeof raw === "string" ? raw.normalize("NFC").replace(/\s+/g, " ").trim() : "";
}

function isAddressForm(value) {
  return ADDRESS_FORMS.includes(value);
}

function isLanguage(value) {
  return LANGUAGES.includes(value);
}

/** Validates the name step. Errors are user-facing Roman Hinglish strings. */
export function validateProfileDraft(draft) {
  const firstName = normalizeFirstName(draft?.firstName);
  const errors = {};
  if (!firstName) errors.firstName = "Apna naam likhiye. Main isi naam se bulaungi.";
  else if (firstName.length > FIRST_NAME_MAX_LENGTH) errors.firstName = `Naam ${FIRST_NAME_MAX_LENGTH} akshar se chhota rakhiye.`;
  else if (/[\d@/\\:]/.test(firstName)) errors.firstName = "Sirf naam likhiye. Number ya email ki zaroorat nahi.";
  else if (!/^[\p{L}\p{M}][\p{L}\p{M} .'-]*$/u.test(firstName)) errors.firstName = "Naam mein sirf letters rakhiye.";
  if (!isAddressForm(draft?.addressForm)) errors.addressForm = "Bataiye main aapse kaise baat karun.";
  if (!isLanguage(draft?.language)) errors.language = "Reply language chuniye.";
  const ok = Object.keys(errors).length === 0;
  return {
    ok,
    errors,
    value: ok ? { first_name: firstName, address_form: draft.addressForm, language: draft.language } : null,
  };
}

/** Accepts the `/api/profile` JSON contract; returns null for anything incomplete or malformed. */
export function normalizeProfile(raw) {
  if (!raw || typeof raw !== "object") return null;
  const firstName = normalizeFirstName(raw.first_name);
  if (!firstName || firstName.length > FIRST_NAME_MAX_LENGTH) return null;
  if (!isAddressForm(raw.address_form) || !isLanguage(raw.language)) return null;
  return { first_name: firstName, address_form: raw.address_form, language: raw.language };
}

/**
 * Chooses the profile to resume from. The server profile wins; the Clerk
 * metadata copy is a fallback until `/api/profile` is available everywhere.
 */
export function resolveProfile({ server, clerkMetadata, clerkFirstName } = {}) {
  const fromServer = normalizeProfile(server);
  if (fromServer) return { profile: fromServer, source: "server", draft: draftFrom(fromServer) };
  const saved = clerkMetadata && typeof clerkMetadata === "object" ? clerkMetadata.gharsaathiProfile : undefined;
  const fromClerk = normalizeProfile({
    first_name: saved?.firstName ?? clerkFirstName,
    address_form: saved?.addressForm,
    language: saved?.language,
  });
  if (fromClerk) return { profile: fromClerk, source: "clerk", draft: draftFrom(fromClerk) };
  return {
    profile: null,
    source: null,
    draft: {
      firstName: normalizeFirstName(saved?.firstName ?? clerkFirstName),
      addressForm: isAddressForm(saved?.addressForm) ? saved.addressForm : null,
      language: isLanguage(saved?.language) ? saved.language : "hinglish",
    },
  };
}

function draftFrom(profile) {
  return { firstName: profile.first_name, addressForm: profile.address_form, language: profile.language };
}

/** Reads onboarding progress from Clerk `unsafeMetadata`. Unknown shapes are ignored. */
export function readOnboardingProgress(metadata) {
  const saved = metadata && typeof metadata === "object" ? metadata.gharsaathiProfile : undefined;
  const onboarding = saved && typeof saved.onboarding === "object" && saved.onboarding !== null ? saved.onboarding : null;
  const decisions = {};
  for (const step of DECISION_STEPS) {
    const value = onboarding?.decisions?.[step];
    if (DECISIONS.has(value)) decisions[step] = value;
  }
  const completedAt = typeof onboarding?.completedAt === "string" ? onboarding.completedAt : null;
  // Users who finished the original two-screen onboarding are not sent through it again.
  const legacyComplete = !onboarding && saved?.complete === true;
  return { decisions, completedAt, legacyComplete };
}

export function isOnboardingComplete(progress) {
  return Boolean(progress?.completedAt) || progress?.legacyComplete === true;
}

/** Returns a new metadata object with one decision (or completion) recorded. */
export function withOnboardingProgress(metadata, update) {
  const base = metadata && typeof metadata === "object" ? metadata : {};
  const saved = base.gharsaathiProfile && typeof base.gharsaathiProfile === "object" ? base.gharsaathiProfile : {};
  const current = readOnboardingProgress(base);
  const decisions = { ...current.decisions };
  if (update?.step && DECISION_STEPS.has(update.step) && DECISIONS.has(update.decision)) decisions[update.step] = update.decision;
  const completedAt = update?.completedAt ?? current.completedAt ?? undefined;
  return {
    ...base,
    gharsaathiProfile: {
      ...saved,
      ...(completedAt ? { complete: true } : {}),
      onboarding: { version: ONBOARDING_VERSION, decisions, ...(completedAt ? { completedAt } : {}) },
    },
  };
}

function anyStoreConnected(snapshot) {
  return Boolean(snapshot?.stores?.swiggy || snapshot?.stores?.zepto);
}

const STEP_ORDER = [...SETUP_STEPS, "done"];

/**
 * Steps that apply to this user right now, in order, ending with "done".
 * Consent drops out once recorded; address and history need a connected store.
 */
export function applicableSteps(snapshot) {
  const steps = ["profile"];
  if (!snapshot?.consentAccepted) steps.push("consent");
  steps.push("stores");
  if (anyStoreConnected(snapshot)) steps.push("address", "personalize");
  steps.push("done");
  return steps;
}

/**
 * The first incomplete step for a signed-in user, or null when the app should show.
 * `oauthReturn` is true when the URL carries a provider's `?{provider}=connected` callback marker.
 */
export function nextOnboardingStep(snapshot) {
  const progress = snapshot?.progress ?? { decisions: {}, completedAt: null, legacyComplete: false };
  const completed = isOnboardingComplete(progress);
  if (!completed && !snapshot?.profileComplete) return "profile";
  if (!snapshot?.consentAccepted) return "consent";
  if (completed) return null;
  if (snapshot?.oauthReturn) return "stores";
  const decisions = progress.decisions ?? {};
  if (!decisions.stores) return "stores";
  if (anyStoreConnected(snapshot)) {
    if (!decisions.address) return "address";
    if (!decisions.personalize) return "personalize";
  }
  return "done";
}

export function stepAfter(step, snapshot) {
  const steps = new Set(applicableSteps(snapshot));
  const index = STEP_ORDER.indexOf(step);
  if (index === -1) return "profile";
  return STEP_ORDER.slice(index + 1).find((item) => steps.has(item)) ?? "done";
}

/** Previous step for the back button; consent cannot be revisited once recorded. */
export function stepBefore(step, snapshot) {
  const steps = new Set(applicableSteps(snapshot));
  const index = STEP_ORDER.indexOf(step);
  if (index <= 0) return null;
  return STEP_ORDER.slice(0, index).reverse().find((item) => steps.has(item)) ?? null;
}

/** 1-based position on the fixed five-segment progress bar. */
export function progressPosition(step) {
  if (step === "done") return { current: SETUP_STEPS.length, total: SETUP_STEPS.length, complete: true };
  const index = SETUP_STEPS.indexOf(step);
  return { current: index === -1 ? 1 : index + 1, total: SETUP_STEPS.length, complete: false };
}

export function canSkip(step) {
  return step === "stores" || step === "address" || step === "personalize";
}

const EXAMPLE_VERB = {
  masculine: { hinglish: "aap dekh rahe hain", hindi: "आप देख रहे हैं" },
  feminine: { hinglish: "aap dekh rahi hain", hindi: "आप देख रही हैं" },
  neutral: { hinglish: "yeh dekhiye", hindi: "यह देखिए" },
};

/** The live preview sentence on the profile step. */
export function addressFormExample(addressForm, language, firstName) {
  const form = isAddressForm(addressForm) ? addressForm : "neutral";
  const name = normalizeFirstName(firstName);
  if (language === "english") {
    return { text: `${name ? `${name}, here` : "Here"}'s Amul milk on both stores. Which one should I order?`, gendered: false };
  }
  if (language === "hindi") {
    const verb = EXAMPLE_VERB[form].hindi;
    return { text: `${name ? `${name}, ` : ""}${verb}, अमूल दूध दोनों स्टोर पर है। कौन सा मँगवाऊँ?`, gendered: form !== "neutral" };
  }
  const verb = EXAMPLE_VERB[form].hinglish;
  const opener = name ? `${name}, ${verb}` : verb.charAt(0).toUpperCase() + verb.slice(1);
  return { text: `${opener}, Amul doodh dono stores par hai. Kaunsa mangwaun?`, gendered: form !== "neutral" };
}

/** Picks copy that agrees with how the user asked to be addressed. */
export function byAddressForm(addressForm, variants) {
  return variants[isAddressForm(addressForm) ? addressForm : "neutral"];
}
