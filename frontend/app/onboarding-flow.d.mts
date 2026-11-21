export type AddressForm = "masculine" | "feminine" | "neutral";
export type ReplyLanguage = "hinglish" | "english" | "hindi";
export type SetupStep = "profile" | "consent" | "stores" | "address" | "personalize";
export type OnboardingStep = SetupStep | "done";
export type DecisionStep = "stores" | "address" | "personalize";
export type StepDecision = "done" | "skipped";

/** A saved profile: the `PUT /api/profile` body onboarding sends. */
export type HouseholdProfile = { first_name: string; address_form: AddressForm; language: ReplyLanguage };
/** `GET /api/profile` response; defaults are returned until the user saves. */
export type ServerProfile = { first_name: string | null; address_form: AddressForm; language: ReplyLanguage; updated_at: string | null };
export type ProfileDraft = { firstName: string; addressForm: AddressForm | null; language: ReplyLanguage };

export type OnboardingProgress = {
  decisions: Partial<Record<DecisionStep, StepDecision>>;
  completedAt: string | null;
  legacyComplete: boolean;
};

export type OnboardingSnapshot = {
  profileComplete?: boolean;
  consentAccepted?: boolean;
  stores?: { swiggy?: boolean; zepto?: boolean };
  progress?: OnboardingProgress;
  oauthReturn?: boolean;
};

export const ONBOARDING_VERSION: 2;
export const SETUP_STEPS: readonly SetupStep[];
export const ADDRESS_FORMS: readonly AddressForm[];
export const LANGUAGES: readonly ReplyLanguage[];
export const FIRST_NAME_MAX_LENGTH: number;

export function normalizeFirstName(raw: unknown): string;
export function validateProfileDraft(draft: Partial<ProfileDraft> | null | undefined): {
  ok: boolean;
  errors: Partial<Record<"firstName" | "addressForm" | "language", string>>;
  value: HouseholdProfile | null;
};
export function normalizeProfile(raw: unknown): HouseholdProfile | null;
export function resolveProfile(sources?: { server?: unknown; clerkMetadata?: unknown; clerkFirstName?: string | null }): {
  profile: HouseholdProfile | null;
  draft: ProfileDraft;
};
export const ADDRESS_FORM_OPTIONS: ReadonlyArray<{ value: AddressForm; label: string; example: string }>;
export function readOnboardingProgress(metadata: unknown): OnboardingProgress;
export function isOnboardingComplete(progress: OnboardingProgress | null | undefined): boolean;
export function withOnboardingProgress<T extends Record<string, unknown>>(
  metadata: T | null | undefined,
  update: { step?: DecisionStep; decision?: StepDecision; completedAt?: string },
): T & { gharsaathiProfile: Record<string, unknown> };
export function applicableSteps(snapshot: OnboardingSnapshot): OnboardingStep[];
export function nextOnboardingStep(snapshot: OnboardingSnapshot): OnboardingStep | null;
export function stepAfter(step: OnboardingStep, snapshot: OnboardingSnapshot): OnboardingStep;
export function stepBefore(step: OnboardingStep, snapshot: OnboardingSnapshot): OnboardingStep | null;
export function progressPosition(step: OnboardingStep): { current: number; total: number; complete: boolean };
export function canSkip(step: OnboardingStep): boolean;
export function addressFormExample(addressForm: AddressForm | null, language: ReplyLanguage, firstName?: string): { text: string; gendered: boolean };
export function byAddressForm<T>(addressForm: AddressForm | null | undefined, variants: Record<AddressForm, T>): T;
