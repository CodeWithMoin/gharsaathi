/**
 * Extracts the basket-panel approval request from a voice
 * `basket.approval_requested` tool event, or null when it is missing or
 * malformed (older backends sent only display strings).
 *
 * @param {unknown} raw
 * @returns {{ provider: "swiggy_instamart" | "zepto", request: any } | null}
 */
export function voiceApprovalFromToolEvent(raw) {
  if (!raw || typeof raw !== "object") return null;
  const event = /** @type {Record<string, any>} */ (raw);
  if (event.type !== "basket.approval_requested") return null;
  if (event.provider !== "swiggy_instamart" && event.provider !== "zepto") return null;
  const request = event.request;
  if (!request || typeof request !== "object") return null;
  const { approval, quote, basket } = request;
  const money = (value) => Boolean(value) && Number.isInteger(value.paise) && typeof value.display === "string";
  if (!approval || typeof approval.id !== "string" || typeof approval.fingerprint !== "string" || typeof approval.expires_at !== "string" || !money(approval.payable)) return null;
  if (!quote || !money(quote.payable) || !money(quote.item_total) || !Array.isArray(quote.charges)) return null;
  if (!basket || basket.provider !== event.provider || !Array.isArray(basket.lines)) return null;
  return { provider: event.provider, request };
}
