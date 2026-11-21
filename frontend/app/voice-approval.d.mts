import type { ApprovalRequest, ProviderName } from "./basket/BasketPanel";

export function voiceApprovalFromToolEvent(raw: unknown): { provider: ProviderName; request: ApprovalRequest } | null;
