import assert from "node:assert/strict";
import test from "node:test";
import {
  basketSummary,
  collapseBasketCard,
  emptyTimeline,
  formatRupees,
  isNearBottom,
  latestMessageId,
  MAX_SUMMARIES,
  moveBasketCard,
  placeTimelineItems,
  recordResults,
  restoreTimeline,
  summaryText,
  timelineSnapshot,
} from "../../app/chat-timeline.mjs";

const turns = (...ids) => ids.map((id) => ({ id }));
const layout = (items) => items.map((item) => {
  if (item.kind === "message") return item.message.id;
  if (item.kind === "results") return "results";
  return `${item.kind === "basket" ? "LIVE" : "summary"}:${item.card.provider}`;
});
const basket = (status, quantities, paise) => ({ status, lines: quantities.map((quantity) => ({ quantity })), item_total: { paise } });

/** Replays a session: each step appends messages or records an event at the newest message. */
function replay(steps) {
  let messages = [];
  let timeline = emptyTimeline();
  let resultsAfterMessageId = null;
  for (const step of steps) {
    if (step.say) messages = [...messages, ...turns(...step.say)];
    if (step.results) { resultsAfterMessageId = latestMessageId(messages); timeline = recordResults(timeline); }
    if (step.basket) timeline = moveBasketCard(timeline, { provider: step.basket, afterMessageId: latestMessageId(messages), previous: step.previous ?? null });
    if (step.hide) timeline = collapseBasketCard(timeline, step.hide, step.previous ?? null);
  }
  return { messages, timeline, resultsAfterMessageId, showResults: resultsAfterMessageId !== null };
}

test("later messages always appear below the basket", () => {
  const state = replay([{ say: ["u1", "a1"] }, { basket: "zepto" }, { say: ["u2", "a2"] }]);
  assert.deepEqual(layout(placeTimelineItems(state)), ["u1", "a1", "LIVE:zepto", "u2", "a2"]);
});

test("an update moves the live card to now and leaves a summary in its old place", () => {
  const previous = basketSummary(basket("draft", [1, 1], 22200), "updated");
  const state = replay([{ say: ["u1", "a1"] }, { basket: "zepto" }, { say: ["u2", "a2"] }, { basket: "zepto", previous }, { say: ["u3"] }]);
  assert.deepEqual(layout(placeTimelineItems(state)), ["u1", "a1", "summary:zepto", "u2", "a2", "LIVE:zepto", "u3"]);
  const summary = state.timeline.summaries[0];
  assert.equal(summaryText(summary), "Basket updated · 2 items · ₹222");
});

test("there is one live card per store, and stores move independently", () => {
  const state = replay([
    { say: ["u1"] }, { basket: "zepto" },
    { say: ["u2"] }, { basket: "swiggy_instamart" },
    { say: ["u3"] }, { basket: "zepto" },
  ]);
  const items = placeTimelineItems(state);
  assert.deepEqual(layout(items), ["u1", "summary:zepto", "u2", "LIVE:swiggy_instamart", "u3", "LIVE:zepto"]);
  for (const store of ["zepto", "swiggy_instamart"]) {
    assert.equal(items.filter((item) => item.kind === "basket" && item.card.provider === store).length, 1);
  }
});

test("a card that is already the newest item does not move or leave a summary", () => {
  const once = replay([{ say: ["u1"] }, { basket: "zepto" }]);
  const again = moveBasketCard(once.timeline, { provider: "zepto", afterMessageId: "u1", previous: null });
  assert.equal(again, once.timeline);
});

test("items at the same message keep the order they happened in", () => {
  const resultsFirst = replay([{ say: ["u1"] }, { results: true }, { basket: "zepto" }]);
  assert.deepEqual(layout(placeTimelineItems(resultsFirst)), ["u1", "results", "LIVE:zepto"]);
  const basketFirst = replay([{ say: ["a1"] }, { basket: "zepto" }, { results: true }]);
  assert.deepEqual(layout(placeTimelineItems(basketFirst)), ["a1", "LIVE:zepto", "results"]);
  // Another store's card added later at the same message is now newer, so the first card moves below it.
  const twoStores = replay([{ say: ["a1"] }, { basket: "zepto" }, { basket: "swiggy_instamart" }, { basket: "zepto" }]);
  assert.deepEqual(layout(placeTimelineItems(twoStores)), ["a1", "summary:zepto", "LIVE:swiggy_instamart", "LIVE:zepto"]);
});

test("results stay anchored in the timeline as the chat continues", () => {
  const state = replay([{ say: ["u1"] }, { results: true }, { say: ["a1", "u2"] }, { basket: "zepto" }, { say: ["a2"] }]);
  assert.deepEqual(layout(placeTimelineItems(state)), ["u1", "results", "a1", "u2", "LIVE:zepto", "a2"]);
});

test("hiding a card collapses it in place", () => {
  const state = replay([{ say: ["u1"] }, { basket: "zepto" }, { say: ["a1"] }, { hide: "zepto", previous: basketSummary(basket("awaiting_approval", [1], 5000), "bill_ready") }]);
  const items = placeTimelineItems(state);
  assert.deepEqual(layout(items), ["u1", "summary:zepto", "a1"]);
  assert.equal(summaryText(items[1].card), "Bill ready · 1 item · ₹50");
});

test("cards without a known anchor come before the first message", () => {
  const state = replay([{ basket: "zepto" }, { say: ["u1"] }]);
  assert.deepEqual(layout(placeTimelineItems(state)), ["LIVE:zepto", "u1"]);
  const trimmed = { ...state, timeline: { ...state.timeline, live: [{ provider: "zepto", afterMessageId: "gone", seq: 1 }] } };
  assert.deepEqual(layout(placeTimelineItems(trimmed)), ["LIVE:zepto", "u1"]);
  assert.deepEqual(layout(placeTimelineItems({ ...state, showResults: true })), ["results", "LIVE:zepto", "u1"]);
});

test("the timeline survives a save and reload unchanged", () => {
  const previous = basketSummary(basket("approved", [2], 34400), "bill_approved");
  const state = replay([{ say: ["u1"] }, { results: true }, { basket: "zepto" }, { say: ["a1"] }, { basket: "zepto", previous }, { say: ["u2"] }, { basket: "swiggy_instamart" }]);
  const restored = restoreTimeline(JSON.parse(JSON.stringify(timelineSnapshot(state.timeline))));
  assert.deepEqual(restored, state.timeline);
  assert.deepEqual(layout(placeTimelineItems({ ...state, timeline: restored })), layout(placeTimelineItems(state)));
  // New events after a reload are still newer than everything restored.
  const next = moveBasketCard(restored, { provider: "zepto", afterMessageId: "u2", previous: null });
  assert.deepEqual(layout(placeTimelineItems({ ...state, timeline: next })), ["u1", "results", "summary:zepto", "a1", "summary:zepto", "u2", "LIVE:swiggy_instamart", "LIVE:zepto"]);
});

test("restoring drops malformed entries and duplicate live stores", () => {
  const restored = restoreTimeline({
    seq: 4,
    results_seq: 1,
    live: [{ provider: "zepto", after_message_id: "a1", seq: 2 }, { provider: "zepto", after_message_id: "a2", seq: 3 }, { provider: "bigbasket", after_message_id: "a1", seq: 4 }],
    summaries: [{ provider: "zepto", after_message_id: "u1", seq: 1, stage: "shipped" }, { provider: "zepto", after_message_id: "u1", seq: 1, stage: "updated", item_count: 2, item_total_paise: -5 }],
  });
  assert.deepEqual(restored.live, [{ provider: "zepto", afterMessageId: "a1", seq: 2 }]);
  assert.deepEqual(restored.summaries, [{ provider: "zepto", afterMessageId: "u1", seq: 1, stage: "updated", itemCount: 2, itemTotalPaise: null }]);
  assert.deepEqual(restoreTimeline(undefined), emptyTimeline());
  assert.deepEqual(restoreTimeline("nope"), emptyTimeline());
});

test("summaries are capped", () => {
  let timeline = emptyTimeline();
  for (let index = 0; index < MAX_SUMMARIES + 10; index += 1) timeline = moveBasketCard(timeline, { provider: "zepto", afterMessageId: `m${index}`, previous: null });
  assert.equal(timeline.summaries.length, MAX_SUMMARIES);
  assert.equal(timelineSnapshot(timeline).summaries.length, MAX_SUMMARIES);
});

test("summaries describe the bill step the card had reached", () => {
  assert.deepEqual(basketSummary(basket("awaiting_approval", [1, 2], 22200), null), { stage: "bill_ready", itemCount: 3, itemTotalPaise: 22200 });
  assert.equal(basketSummary(basket("approved", [1], 100), "bill_approved").stage, "bill_approved");
  assert.equal(basketSummary(basket("draft", [1], 100), "bill_ready").stage, "updated", "an edit after the bill drops back to an updated basket");
  assert.equal(basketSummary(basket("ordered", [1], 100), null).stage, "ordered");
  assert.equal(basketSummary(null, null), null);
  assert.equal(summaryText({ stage: "updated", itemCount: null, itemTotalPaise: null }), "Basket updated");
  assert.equal(formatRupees(22250), "₹222.50");
  assert.equal(formatRupees(150000), "₹1,500");
});

test("follows new items only while the reader is near the bottom", () => {
  assert.equal(isNearBottom({ scrollTop: 1200, viewportHeight: 800, scrollHeight: 2000 }), true);
  assert.equal(isNearBottom({ scrollTop: 1100, viewportHeight: 800, scrollHeight: 2000 }), true);
  assert.equal(isNearBottom({ scrollTop: 600, viewportHeight: 800, scrollHeight: 2000 }), false);
  assert.equal(isNearBottom({ scrollTop: 0, viewportHeight: 800, scrollHeight: 700 }), true);
});
