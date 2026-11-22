export const MESSAGES: Record<
  "unsupported" | "insecure" | "iosInstall" | "denied" | "dismissed" | "failed" | "offFailed" | "setup" | "setupHint" | "on" | "off",
  string
>;

export type PushSupport = "supported" | "insecure" | "ios-install" | "unsupported";
export type PushPermission = "default" | "granted" | "denied";

export type BrowserEnv = {
  isSecureContext?: boolean;
  hasServiceWorker?: boolean;
  hasPushManager?: boolean;
  hasNotification?: boolean;
  userAgent?: string;
  maxTouchPoints?: number;
  standalone?: boolean;
};

export function detectSupport(env: BrowserEnv): PushSupport;
export function browserEnv(win: Window): BrowserEnv;

export type NotificationStateName =
  | "loading" | "hidden" | "insecure" | "ios-install" | "unsupported"
  | "turning-on" | "turning-off" | "denied" | "error" | "on" | "off";

export type NotificationView = {
  state: NotificationStateName;
  message: string;
  hint?: string;
  checked: boolean;
  disabled: boolean;
  canTest: boolean;
};

export function notificationState(input: {
  available: boolean | null;
  support: PushSupport | string;
  permission: PushPermission | string;
  subscribed: boolean;
  busy?: "on" | "off" | null;
  error?: string;
}): NotificationView;

export function shouldOfferOrderPrompt(input: {
  available: boolean | null;
  support: PushSupport | string;
  permission: PushPermission | string;
  subscribed: boolean;
  answered: boolean;
}): boolean;

export function urlBase64ToUint8Array(value: string): Uint8Array;

export function turnOn(deps: {
  requestPermission: () => Promise<string>;
  register: () => Promise<unknown>;
  subscribe: (registration: unknown, key: Uint8Array) => Promise<{ toJSON: () => unknown }>;
  save: (subscription: unknown) => Promise<unknown>;
  publicKey: string;
}): Promise<{ state: "on" | "off" | "denied" | "error"; message?: string; permission: string }>;

export function turnOff(deps: {
  getSubscription: () => Promise<{ endpoint: string; unsubscribe: () => Promise<boolean> } | null>;
  remove: (endpoint: string) => Promise<unknown>;
}): Promise<{ state: "off" | "error"; message?: string }>;

export function safePath(url: unknown): string;
export function orderFromUrl(url: unknown): string | null;
