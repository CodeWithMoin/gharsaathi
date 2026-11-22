/**
 * The Settings drawer map: a profile header row, then Stores, What GharSaathi knows and
 * Delivery line (Beta), then a Sign out text link. Every row opens a subpage inside the
 * same drawer; nothing in Settings opens the old setup sheet. Addresses live in the
 * header address block, Help on the order card, Notifications inside Profile.
 */

/** One word for what GharSaathi remembers, used everywhere in Settings. */
export const MEMORY_TERM = "Memory";
/** Label of the on/off switch on the memory page. */
export const MEMORY_SWITCH_LABEL = "Use what I've told you";

export const SECTION_TITLES = Object.freeze({
  profile: "Profile",
  stores: "Stores",
  memory: "What GharSaathi knows",
  delivery: "Delivery line",
  rider: "Rider assist",
});

/** Overview heading when no subpage is open. */
export const OVERVIEW_TITLE = "Account";

/**
 * One width for the drawer on the overview and every subpage, so it never changes size:
 * 85% of the viewport, capped at 400px (`w-[85vw] max-w-[400px]`). Wide enough for memory
 * rows with their source badge and Edit / Delete; leaves about 55px of the pushed page to
 * tap at 360px.
 */
export const SETTINGS_DRAWER_CLASS = "w-[85vw] max-w-[400px]";
export const SETTINGS_DRAWER_FRACTION = 0.85;
export const SETTINGS_DRAWER_MAX = 400;

/** @param {import("./settings-map.d.mts").SettingsSection} section */
export function sectionHeading(section) {
  return section && Object.prototype.hasOwnProperty.call(SECTION_TITLES, section) ? SECTION_TITLES[section] : OVERVIEW_TITLE;
}

/**
 * Rows under the profile header row, in order.
 * @param {{ riderAssist?: boolean }} options
 */
export function settingsRows({ riderAssist = false } = {}) {
  /** @type {{ id: string, title: string, section: string }[]} */
  const rows = [
    { id: "stores", title: "Stores", section: "stores" },
    { id: "memory", title: "What GharSaathi knows", section: "memory" },
    { id: "delivery", title: "Delivery line (Beta)", section: "delivery" },
  ];
  // The parked Vobiz rider assist only appears when the server has RIDER_ASSIST_ENABLED on.
  if (riderAssist) rows.push({ id: "rider", title: "Rider assist (Beta)", section: "rider" });
  return rows;
}
