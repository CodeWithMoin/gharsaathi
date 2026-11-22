export type SettingsSection = "profile" | "stores" | "memory" | "delivery" | "rider" | null;
export type SettingsRowId = "stores" | "memory" | "delivery" | "rider";
export type SettingsRowSpec = { id: SettingsRowId; title: string; section: Exclude<SettingsSection, null> };

export declare const MEMORY_TERM: "Memory";
export declare const MEMORY_SWITCH_LABEL: string;
export declare const SECTION_TITLES: Readonly<Record<Exclude<SettingsSection, null>, string>>;
export declare const OVERVIEW_TITLE: string;
export declare const SETTINGS_DRAWER_CLASS: string;
export declare const SETTINGS_DRAWER_FRACTION: number;
export declare const SETTINGS_DRAWER_MAX: number;
export declare function sectionHeading(section: SettingsSection): string;
export declare function settingsRows(options?: { riderAssist?: boolean }): SettingsRowSpec[];
