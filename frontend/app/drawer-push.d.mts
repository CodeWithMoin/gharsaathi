export type DrawerName = "chats" | "settings";

export declare const DRAWER_SPRING: Readonly<{ type: "spring"; stiffness: number; damping: number }>;
export declare const DRAWER_PANEL_TRANSITION: Readonly<{ type: "spring"; stiffness: number; damping: number; opacity: { duration: number } }>;
export declare function drawerWidth(viewportWidth: number): number;
export declare function pushOffset(drawer: DrawerName | null, viewportWidth: number, reducedMotion?: boolean): number;
export declare function drawerState(drawer: DrawerName | null): { chatsOpen: boolean; settingsOpen: boolean };
export declare function restoreScrollTarget(input: { savedScrollY: number; follow: boolean; scrollHeight: number; viewportHeight: number }): number;
export declare function viewportInsets(input: { layoutHeight: number; visualHeight: number; visualOffsetTop: number; scale?: number }): { top: number; bottom: number };
