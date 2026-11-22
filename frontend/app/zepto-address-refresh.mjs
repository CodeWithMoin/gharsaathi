/**
 * What the screen does with a Zepto saved-address list from the backend.
 * A removed selection is never replaced automatically, even when only one
 * address is left: the user picks again with one tap.
 *
 * @param {{ addresses: { id: string }[], resolution: { default_address_id?: string | null }, selection_removed?: boolean }} data
 * @param {string} current The address id the screen is using now.
 * @returns {{ selectedId: string, selectionRemoved: boolean }}
 */
export function zeptoAddressSelection(data, current) {
  const ids = new Set(data.addresses.map((item) => item.id));
  if (data.selection_removed) return { selectedId: "", selectionRemoved: true };
  const preferred = data.resolution.default_address_id;
  if (preferred && ids.has(preferred)) return { selectedId: preferred, selectionRemoved: false };
  if (current && ids.has(current)) return { selectedId: current, selectionRemoved: false };
  if (data.addresses.length === 1 && !current) return { selectedId: data.addresses[0].id, selectionRemoved: false };
  // The id in use is gone from the refreshed list.
  return { selectedId: "", selectionRemoved: Boolean(current) };
}
