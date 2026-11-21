/**
 * The final Zepto order action is intentionally narrower than bill approval.
 * Keeping this predicate outside the component makes the safety boundary easy
 * to exercise without rendering the UI.
 */
export function canConfirmZeptoCodOrder({ provider, commerceMode, approvalStatus }) {
  return provider === "zepto" && commerceMode === "live" && approvalStatus === "approved";
}

/** @param {{ status: string, provider_order_id?: string | null, message?: string | null }} result */
export function checkoutStatusCopy(result) {
  switch (result.status) {
    case "dispatching":
      return "Submitting your Zepto cash-on-delivery order. Please do not close this page or submit again.";
    case "confirmed":
      return result.provider_order_id
        ? `Zepto COD order placed. Order ID: ${result.provider_order_id}.`
        : "Zepto COD order placed. Check Zepto order history for the order details.";
    case "outcome_unknown":
      return "We could not confirm whether Zepto received this order. Do not retry; check Zepto or your order history.";
    case "rejected":
      return "Zepto did not accept this order. Nothing has been confirmed. Review the bill and start again if needed.";
    default:
      return result.message || "We could not confirm this Zepto order. Do not retry; check Zepto or your order history.";
  }
}
