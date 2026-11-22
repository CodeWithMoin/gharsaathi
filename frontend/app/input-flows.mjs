// Pure helpers behind the input popups (address picker, profile, preference
// edit, store disconnect). No React, so they can be unit-tested.

export const REPLY_LANGUAGES = ["hinglish", "hindi", "english"];
export const ADDRESS_FORMS = ["masculine", "feminine", "neutral"];

/** Short name of a saved address, as shown in lists. */
export function addressLabel(address) {
  return address?.tag || address?.category || "Saved address";
}

/** The saved address with this id, or null when it isn't in the list. */
export function selectedAddress(addresses, selectedId) {
  if (!selectedId || !Array.isArray(addresses)) return null;
  return addresses.find((address) => address.id === selectedId) ?? null;
}

/** What the setup sheet says about a store's delivery address. */
export function addressSummary(addresses, selectedId) {
  const chosen = selectedAddress(addresses, selectedId);
  if (chosen) return { title: addressLabel(chosen), detail: chosen.address_line ?? "", chosen: true };
  const count = Array.isArray(addresses) ? addresses.length : 0;
  if (count === 0) return { title: "No saved address yet", detail: "", chosen: false };
  return { title: "No address chosen", detail: `${count} saved ${count === 1 ? "address" : "addresses"}`, chosen: false };
}

/** The editable values of a preference, keyed by field. */
export function initialEditValues(fields) {
  return Object.fromEntries((fields ?? []).map((field) => [field.key, field.value ?? ""]));
}

/**
 * Starting values of the Edit profile popup. The chat language comes from the
 * account (what the app shows), falling back to the saved profile.
 */
export function profileDraft(profile, languageLabel) {
  const fromLabel = String(languageLabel ?? "").trim().toLowerCase();
  const language = REPLY_LANGUAGES.includes(fromLabel) ? fromLabel : REPLY_LANGUAGES.includes(profile?.language) ? profile.language : "hinglish";
  const addressForm = ADDRESS_FORMS.includes(profile?.address_form) ? profile.address_form : "neutral";
  return { first_name: profile?.first_name ?? "", address_form: addressForm, language };
}

/** Only the fields that changed, ready for PUT /api/profile; null when nothing changed. */
export function profileChanges(initial, draft) {
  const changes = {};
  const before = String(initial?.first_name ?? "").trim();
  const after = String(draft?.first_name ?? "").trim();
  if (before !== after) changes.first_name = after || null;
  if (draft?.address_form && draft.address_form !== initial?.address_form) changes.address_form = draft.address_form;
  if (draft?.language && draft.language !== initial?.language) changes.language = draft.language;
  return Object.keys(changes).length ? changes : null;
}

const STORE_NAMES = { swiggy: "Swiggy Instamart", zepto: "Zepto" };

/** Text of the Disconnect confirmation for a store. */
export function disconnectCopy(store) {
  const name = STORE_NAMES[store] ?? "this store";
  return {
    title: `Disconnect ${name}?`,
    body: `GharSaathi will stop searching ${name} until you connect it again. Your ${name} account and orders are not changed.`,
    confirmLabel: "Disconnect",
    busyLabel: "Disconnecting…",
  };
}
