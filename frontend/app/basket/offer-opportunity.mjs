function rupees(paise) {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: paise % 100 === 0 ? 0 : 2,
  }).format(paise / 100);
}

function offerOpportunityCopy(offer) {
  if (offer.applied) {
    return {
      tone: "success",
      title: offer.title,
      detail: "Applied in the final bill above.",
    };
  }

  if (offer.kind === "free_delivery") {
    if (offer.unlocked) {
      return {
        tone: "success",
        title: "Free delivery unlocked",
        detail: "Zepto will confirm the final delivery charge in the bill.",
      };
    }
    if (offer.remaining_paise > 0 && offer.potential_saving_paise != null) {
      const net = offer.net_extra_paise == null
        ? Math.max(0, offer.remaining_paise - offer.potential_saving_paise)
        : offer.net_extra_paise;
      return {
        tone: "saving",
        title: `${rupees(offer.remaining_paise)} more can unlock free delivery`,
        detail: `Potential delivery saving ${rupees(offer.potential_saving_paise)} · net extra ${rupees(net)} only if you add something useful.`,
      };
    }
  }

  if (offer.remaining_paise > 0) {
    return {
      tone: "neutral",
      title: `${rupees(offer.remaining_paise)} more to qualify for ${offer.title}`,
      detail: offer.provider_confirmed
        ? "Eligibility is provider-reported; Zepto will confirm the actual saving in the refreshed bill."
        : "The saving is not confirmed yet. Don’t add an item only for this threshold.",
    };
  }

  return {
    tone: "neutral",
    title: offer.title,
    detail: "Zepto will confirm eligibility and the actual saving in a refreshed bill.",
  };
}

function visibleOfferOpportunities(offers) {
  const seen = new Set();
  return offers
    .filter((offer) => offer.applied || offer.kind === "free_delivery" || offer.remaining_paise > 0)
    .filter((offer) => {
      const key = `${offer.kind}:${offer.title}:${offer.minimum_order_paise}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .sort((a, b) => {
      if (a.applied !== b.applied) return a.applied ? -1 : 1;
      if ((a.kind === "free_delivery") !== (b.kind === "free_delivery")) return a.kind === "free_delivery" ? -1 : 1;
      return a.remaining_paise - b.remaining_paise;
    })
    .slice(0, 3);
}

export { offerOpportunityCopy, visibleOfferOpportunities };
