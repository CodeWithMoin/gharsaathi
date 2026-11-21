/**
 * The chat is one chronological timeline. Product results and basket/bill
 * cards are items inserted after the message that was newest when they
 * happened, ordered by a running sequence number, so the newest message is
 * always at the bottom.
 *
 * Each store has at most one live (interactive) basket card. When its basket
 * or bill changes, the live card moves to the newest point in the timeline and
 * the card it leaves behind becomes a one-line, non-interactive summary.
 */

export const STORES = /** @type {const} */ (["swiggy_instamart", "zepto"]);
export const SUMMARY_STAGES = /** @type {const} */ (["updated", "bill_ready", "bill_approved", "ordered"]);
export const MAX_SUMMARIES = 50;

/** @typedef {"swiggy_instamart" | "zepto"} Store */
/** @typedef {"updated" | "bill_ready" | "bill_approved" | "ordered"} SummaryStage */
/** @typedef {{ provider: Store, afterMessageId: string | null, seq: number }} BasketCard */
/** @typedef {{ stage: SummaryStage, itemCount: number | null, itemTotalPaise: number | null }} BasketSummary */
/** @typedef {BasketCard & BasketSummary} SummaryCard */
/** @typedef {{ seq: number, resultsSeq: number, live: BasketCard[], summaries: SummaryCard[] }} ChatTimeline */

/** @returns {ChatTimeline} */
export function emptyTimeline() {
  return { seq: 0, resultsSeq: 0, live: [], summaries: [] };
}

/**
 * @param {{ id: string }[]} messages
 * @returns {string | null}
 */
export function latestMessageId(messages) {
  return messages.at(-1)?.id ?? null;
}

/**
 * New product results were placed after `resultsAfterMessageId`.
 *
 * @param {ChatTimeline} timeline
 * @returns {ChatTimeline}
 */
export function recordResults(timeline) {
  const seq = timeline.seq + 1;
  return { ...timeline, seq, resultsSeq: seq };
}

/**
 * @param {ChatTimeline} timeline
 * @param {Store} provider
 */
export function liveCard(timeline, provider) {
  return timeline.live.find((card) => card.provider === provider) ?? null;
}

/**
 * @param {SummaryCard[]} summaries
 * @param {BasketCard} card
 * @param {BasketSummary | null} summary
 */
function withSummary(summaries, card, summary) {
  const entry = { ...card, ...(summary ?? { stage: /** @type {SummaryStage} */ ("updated"), itemCount: null, itemTotalPaise: null }) };
  return [...summaries, entry].slice(-MAX_SUMMARIES);
}

/**
 * The store's basket or bill changed: its live card becomes the newest item.
 * `previous` describes what the old card showed, for its collapsed summary.
 * A card that is already the newest item stays where it is.
 *
 * @param {ChatTimeline} timeline
 * @param {{ provider: Store, afterMessageId: string | null, previous: BasketSummary | null }} change
 * @returns {ChatTimeline}
 */
export function moveBasketCard(timeline, { provider, afterMessageId, previous }) {
  const current = liveCard(timeline, provider);
  if (current && current.afterMessageId === afterMessageId && current.seq === timeline.seq) return timeline;
  const seq = timeline.seq + 1;
  return {
    ...timeline,
    seq,
    live: [...timeline.live.filter((card) => card.provider !== provider), { provider, afterMessageId, seq }],
    summaries: current ? withSummary(timeline.summaries, current, previous) : timeline.summaries,
  };
}

/**
 * Hiding a live card leaves its summary in place.
 *
 * @param {ChatTimeline} timeline
 * @param {Store} provider
 * @param {BasketSummary | null} summary
 * @returns {ChatTimeline}
 */
export function collapseBasketCard(timeline, provider, summary) {
  const current = liveCard(timeline, provider);
  if (!current) return timeline;
  return {
    ...timeline,
    live: timeline.live.filter((card) => card.provider !== provider),
    summaries: withSummary(timeline.summaries, current, summary),
  };
}

/**
 * @template {{ id: string }} M
 * @typedef {{ kind: "message", message: M } | { kind: "results" } | { kind: "basket", card: BasketCard } | { kind: "basket-summary", card: SummaryCard }} TimelineItem
 */

/**
 * Lays the chat out in order. Items whose anchor is missing (none yet, or
 * trimmed from the saved chat) come before the first message; within one
 * anchor, items follow their sequence numbers.
 *
 * @template {{ id: string }} M
 * @param {{ messages: M[], resultsAfterMessageId: string | null, timeline: ChatTimeline, showResults: boolean }} state
 * @returns {TimelineItem<M>[]}
 */
export function placeTimelineItems({ messages, resultsAfterMessageId, timeline, showResults }) {
  const ids = new Set(messages.map((message) => message.id));
  /** @type {{ anchor: string | null, seq: number, order: number, item: TimelineItem<M> }[]} */
  const inserted = [
    ...(showResults ? [{ anchor: resultsAfterMessageId, seq: timeline.resultsSeq, order: 0, item: /** @type {TimelineItem<M>} */ ({ kind: "results" }) }] : []),
    ...timeline.summaries.map((card, index) => ({ anchor: card.afterMessageId, seq: card.seq, order: 1 + index, item: /** @type {TimelineItem<M>} */ ({ kind: "basket-summary", card }) })),
    ...timeline.live.map((card) => ({ anchor: card.afterMessageId, seq: card.seq, order: 1 + MAX_SUMMARIES, item: /** @type {TimelineItem<M>} */ ({ kind: "basket", card }) })),
  ];
  /** @type {Map<string | null, typeof inserted>} */
  const groups = new Map();
  for (const entry of inserted) {
    const key = entry.anchor !== null && ids.has(entry.anchor) ? entry.anchor : null;
    groups.set(key, [...(groups.get(key) ?? []), entry]);
  }
  /** @param {string | null} key */
  const group = (key) => (groups.get(key) ?? []).sort((a, b) => a.seq - b.seq || a.order - b.order).map((entry) => entry.item);
  /** @type {TimelineItem<M>[]} */
  const items = [...group(null)];
  for (const message of messages) items.push({ kind: "message", message }, ...group(message.id));
  return items;
}

/**
 * @param {{ status: string, lines: { quantity: number }[], item_total: { paise: number } } | null | undefined} basket
 * @param {SummaryStage | null | undefined} stage the latest bill step shown in the card
 * @returns {BasketSummary | null}
 */
export function basketSummary(basket, stage) {
  if (!basket) return stage ? { stage, itemCount: null, itemTotalPaise: null } : null;
  const fromStatus = basket.status === "ordered" ? "ordered" : basket.status === "approved" ? "bill_approved" : basket.status === "awaiting_approval" ? "bill_ready" : "updated";
  return {
    stage: basket.status === "draft" ? "updated" : stage ?? fromStatus,
    itemCount: basket.lines.reduce((total, line) => total + line.quantity, 0),
    itemTotalPaise: basket.item_total.paise,
  };
}

const STAGE_LABELS = { updated: "Basket updated", bill_ready: "Bill ready", bill_approved: "Bill approved", ordered: "Order placed" };

/** @param {number} paise */
export function formatRupees(paise) {
  const rupees = paise / 100;
  return `₹${rupees.toLocaleString("en-IN", { minimumFractionDigits: paise % 100 ? 2 : 0, maximumFractionDigits: 2 })}`;
}

/**
 * "Basket updated · 2 items · ₹222"
 *
 * @param {BasketSummary} summary
 */
export function summaryText(summary) {
  const parts = [STAGE_LABELS[summary.stage]];
  if (summary.itemCount !== null) parts.push(`${summary.itemCount} ${summary.itemCount === 1 ? "item" : "items"}`);
  if (summary.itemTotalPaise !== null) parts.push(formatRupees(summary.itemTotalPaise));
  return parts.join(" · ");
}

/**
 * @param {ChatTimeline} timeline
 */
export function timelineSnapshot(timeline) {
  /** @param {BasketCard} card */
  const anchor = (card) => ({ provider: card.provider, after_message_id: card.afterMessageId, seq: card.seq });
  return {
    seq: timeline.seq,
    results_seq: timeline.resultsSeq,
    live: timeline.live.map(anchor),
    summaries: timeline.summaries.slice(-MAX_SUMMARIES).map((card) => ({ ...anchor(card), stage: card.stage, item_count: card.itemCount, item_total_paise: card.itemTotalPaise })),
  };
}

/** @param {unknown} value */
const count = (value) => (typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null);

/**
 * @param {unknown} raw
 * @returns {BasketCard | null}
 */
function restoreCard(raw) {
  if (!raw || typeof raw !== "object") return null;
  const value = /** @type {Record<string, unknown>} */ (raw);
  const provider = STORES.find((store) => store === value.provider);
  const seq = count(value.seq);
  if (!provider || seq === null) return null;
  const anchor = value.after_message_id;
  return { provider, afterMessageId: typeof anchor === "string" && anchor ? anchor : null, seq };
}

/**
 * Reads the saved `timeline` field. Malformed entries are dropped; a missing
 * timeline is empty.
 *
 * @param {unknown} raw
 * @returns {ChatTimeline}
 */
export function restoreTimeline(raw) {
  if (!raw || typeof raw !== "object") return emptyTimeline();
  const value = /** @type {Record<string, unknown>} */ (raw);
  /** @type {BasketCard[]} */
  const live = [];
  for (const entry of Array.isArray(value.live) ? value.live : []) {
    const card = restoreCard(entry);
    if (card && !live.some((item) => item.provider === card.provider)) live.push(card);
  }
  /** @type {SummaryCard[]} */
  const summaries = [];
  for (const entry of Array.isArray(value.summaries) ? value.summaries : []) {
    const card = restoreCard(entry);
    const fields = /** @type {Record<string, unknown>} */ (entry);
    const stage = SUMMARY_STAGES.find((item) => item === fields.stage);
    if (card && stage) summaries.push({ ...card, stage, itemCount: count(fields.item_count), itemTotalPaise: count(fields.item_total_paise) });
  }
  const seqs = [count(value.seq) ?? 0, count(value.results_seq) ?? 0, ...live.map((card) => card.seq), ...summaries.map((card) => card.seq)];
  return { seq: Math.max(...seqs), resultsSeq: count(value.results_seq) ?? 0, live, summaries: summaries.slice(-MAX_SUMMARIES) };
}

export const NEAR_BOTTOM_PX = 160;

/**
 * Auto-scroll follows new items only while the reader is already at the end.
 *
 * @param {{ scrollTop: number, viewportHeight: number, scrollHeight: number }} metrics
 * @param {number} [threshold]
 */
export function isNearBottom({ scrollTop, viewportHeight, scrollHeight }, threshold = NEAR_BOTTOM_PX) {
  return scrollHeight - (scrollTop + viewportHeight) <= threshold;
}
