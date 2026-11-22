/**
 * Home screen composition: the Swiggy-style address block in the header, the
 * "Where should we deliver?" step before the greeting, and the links on a placed
 * order's card. Pure and unit-tested; page.tsx only renders what these return.
 */

/** Zepto first: it is the store that can place orders (cash on delivery). */
const STORE_ORDER = /** @type {const} */ (["zepto", "swiggy"]);
export const STORE_NAMES = Object.freeze({ zepto: "Zepto", swiggy: "Swiggy Instamart" });

/** @param {{ tag?: string, category?: string } | null | undefined} address */
export function addressLabel(address) {
  const label = (address?.tag || address?.category || "").trim();
  return label ? label.charAt(0).toUpperCase() + label.slice(1) : "Saved address";
}

/**
 * @typedef {{ connected: boolean | null, addresses: { id: string, address_line: string, tag?: string, category?: string }[], selectedId: string, loaded?: boolean }} StoreAddresses
 * @param {StoreAddresses} store
 */
function selectedFor(store) {
  if (store.connected !== true || !store.selectedId) return null;
  return store.addresses.find((address) => address.id === store.selectedId) ?? null;
}

/**
 * What the header address block shows. `state`:
 * "selected" (label + one line of the address), "choose" (a store is connected but no
 * valid address is chosen), "connect" (no store connected), "checking" (still loading).
 * `store` is the store whose picker a tap opens; null means open Settings → Stores.
 * @param {{ zepto: StoreAddresses, swiggy: StoreAddresses }} stores
 */
export function headerAddress(stores) {
  for (const id of STORE_ORDER) {
    const address = selectedFor(stores[id]);
    if (address) return { state: "selected", store: id, label: addressLabel(address), line: address.address_line.trim(), accessibleLabel: `Delivering to ${addressLabel(address)}, ${address.address_line.trim()}. Change delivery address` };
  }
  const connected = STORE_ORDER.find((id) => stores[id].connected === true);
  if (connected) return { state: "choose", store: connected, label: "Choose address", line: `For ${STORE_NAMES[connected]}`, accessibleLabel: "Choose a delivery address" };
  if (STORE_ORDER.some((id) => stores[id].connected === null)) return { state: "checking", store: null, label: "Checking…", line: "", accessibleLabel: "Checking your stores" };
  return { state: "connect", store: null, label: "Connect a store", line: "To choose where to deliver", accessibleLabel: "Connect a store to choose a delivery address" };
}

/**
 * "Where should we deliver?" before the greeting: only when a store is connected, every
 * connected store has finished loading its addresses, and none has a valid selection
 * (never selected, or the selected one is gone). Otherwise null, and nothing blocks.
 * @param {{ zepto: StoreAddresses, swiggy: StoreAddresses }} stores
 */
export function addressGate(stores) {
  const connected = STORE_ORDER.filter((id) => stores[id].connected === true);
  if (!connected.length) return null;
  if (connected.some((id) => selectedFor(stores[id]))) return null;
  if (connected.some((id) => !stores[id].loaded)) return null;
  const store = connected[0];
  return { store, storeName: STORE_NAMES[store], addresses: stores[store].addresses, canAdd: store === "zepto" };
}

/**
 * Links under a placed order's status card. The delivery line and directions matter
 * only while the order is moving; help stays (missing or damaged items show up later).
 * @param {{ final: boolean, deliveryLineReady: boolean }} options
 */
export function orderCardLinks({ final, deliveryLineReady }) {
  /** @type {{ id: "delivery-line" | "directions" | "help", label: string }[]} */
  const links = [];
  if (!final) {
    links.push({ id: "delivery-line", label: deliveryLineReady ? "Delivery line ready" : "Set up delivery line" });
    links.push({ id: "directions", label: "Directions" });
  }
  links.push({ id: "help", label: "Need help?" });
  return links;
}
