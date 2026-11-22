import assert from "node:assert/strict";
import test from "node:test";
import { emptyTimeline, finishResultGroup, dropResultGroup, latestMessageId, moveBasketCard, placeTimelineItems, recordResultGroup, restoreTimeline, withResultGroups } from "../../app/chat-timeline.mjs";
import { conversationSnapshot } from "../../app/conversation-snapshot.mjs";
import { groupBadge, MAX_RESULT_GROUPS, normalizeItemKey, restoreResultGroups, resultGroupIdentity, resultsHeadline } from "../../app/result-groups.mjs";

const product = (name) => ({ product_id: name, sku_id: `${name}-sku`, name, price: { mrp: 100, offer_price: 90 }, available: true });
const turn = (id, role = "user", content = id) => ({ id, role, content, modality: "voice", created_at: "2026-11-22T10:00:00Z" });
const layout = (items) => items.map((item) => (item.kind === "message" ? item.message.id : item.kind === "results" ? `results:${item.group.key}` : `${item.kind}:${item.card.provider}`));

/** One voice `tool.results` event, recorded the way page.tsx records it. */
function voiceResults(timeline, messages, { query, products, itemKey, itemLabel }) {
  const identity = resultGroupIdentity({ itemKey, itemLabel, query, products });
  return recordResultGroup(timeline, { ...identity, query, products, status: "done", afterMessageId: latestMessageId(messages) });
}

test("two searches in one request make two groups, each at the point it arrived", () => {
  const messages = [turn("u1", "user", "aata aur dahi")];
  let timeline = voiceResults(emptyTimeline(), messages, { query: "aashirvaad atta", itemKey: "atta", itemLabel: "Atta", products: [product("Aashirvaad Atta 5kg"), product("Pillsbury Atta 5kg"), product("Fortune Atta 5kg")] });
  messages.push(turn("a1", "assistant"));
  timeline = voiceResults(timeline, messages, { query: "dahi", itemKey: "dahi", itemLabel: "Dahi", products: [product("Desi Farms Malai Dahi")] });
  assert.deepEqual(timeline.groups.map((group) => [group.label, group.products.length]), [["Atta", 3], ["Dahi", 1]]);
  assert.deepEqual(layout(placeTimelineItems({ messages, timeline })), ["u1", "results:atta", "a1", "results:dahi"]);
});

test("two searches in a row before any new message stay in order", () => {
  const messages = [turn("u1")];
  let timeline = voiceResults(emptyTimeline(), messages, { query: "atta", products: [product("Atta")] });
  timeline = voiceResults(timeline, messages, { query: "dahi", products: [product("Dahi")] });
  assert.deepEqual(layout(placeTimelineItems({ messages, timeline })), ["u1", "results:atta", "results:dahi"]);
});

test("searching the same item again replaces its group and moves it to now", () => {
  const messages = [turn("u1")];
  let timeline = voiceResults(emptyTimeline(), messages, { query: "atta", itemKey: "atta", itemLabel: "Atta", products: [product("Aashirvaad Atta")] });
  timeline = voiceResults(timeline, messages, { query: "dahi", itemKey: "dahi", itemLabel: "Dahi", products: [product("Dahi")] });
  messages.push(turn("u2", "user", "atta 10 kilo wala dikhao"));
  timeline = voiceResults(timeline, messages, { query: "atta 10kg", itemKey: "atta", itemLabel: "Atta", products: [product("Aashirvaad Atta 10kg")] });
  assert.equal(timeline.groups.length, 2);
  assert.deepEqual(timeline.groups.find((group) => group.key === "atta").products.map((item) => item.name), ["Aashirvaad Atta 10kg"]);
  assert.deepEqual(layout(placeTimelineItems({ messages, timeline })), ["u1", "results:dahi", "u2", "results:atta"]);
});

test("without item_key, the key and label come from the query, then the first product", () => {
  assert.deepEqual(resultGroupIdentity({ query: "  Fortune cooking-oil 1 litre " }), { key: "fortune cooking oil 1 litre", label: "Fortune cooking-oil 1 litre" });
  assert.equal(resultGroupIdentity({ query: "dahi" }).key, resultGroupIdentity({ query: "Dahi!" }).key);
  assert.deepEqual(resultGroupIdentity({ query: "", products: [product("Amul Masti Dahi")] }), { key: "amul masti dahi", label: "Amul Masti Dahi" });
  assert.deepEqual(resultGroupIdentity({ query: "" }), { key: "results", label: "Results" });
  assert.deepEqual(resultGroupIdentity({ itemKey: "Atta", itemLabel: "atta", query: "aashirvaad atta 5kg" }), { key: "atta", label: "Atta" });
  assert.equal(normalizeItemKey("दही"), "दही");
});

test("a typed search shows as searching, then fills in place", () => {
  const messages = [turn("u1"), turn("a1", "assistant"), turn("u2")];
  let timeline = voiceResults(emptyTimeline(), messages.slice(0, 1), { query: "atta", products: [product("Atta")] });
  timeline = recordResultGroup(timeline, { ...resultGroupIdentity({ query: "dahi" }), query: "dahi", products: [], status: "searching", afterMessageId: "u2" });
  timeline = moveBasketCard(timeline, { provider: "zepto", afterMessageId: "u2", previous: null });
  timeline = finishResultGroup(timeline, "dahi", { products: [product("Dahi")], status: "done" });
  assert.equal(timeline.groups.at(-1).status, "done");
  assert.deepEqual(layout(placeTimelineItems({ messages, timeline })), ["u1", "results:atta", "a1", "u2", "results:dahi", "basket:zepto"]);
  assert.deepEqual(dropResultGroup(timeline, "dahi").groups.map((group) => group.key), ["atta"]);
});

test("header counts are per group", () => {
  assert.equal(resultsHeadline(6, 3), "6 options found—showing the top 3.");
  assert.equal(resultsHeadline(3, 3), "3 relevant options found.");
  assert.equal(resultsHeadline(1, 3), "1 relevant option found.");
  assert.equal(groupBadge("Atta", 3), "Atta · 3 options");
  assert.equal(groupBadge("Dahi", 1), "Dahi · 1 option");
});

test("groups survive a save and reload, and stay ahead of the restored sequence", () => {
  const messages = [turn("u1"), turn("a1", "assistant")];
  let timeline = voiceResults(emptyTimeline(), messages.slice(0, 1), { query: "atta", itemLabel: "Atta", itemKey: "atta", products: [product("Atta")] });
  timeline = voiceResults(timeline, messages, { query: "dahi", itemLabel: "Dahi", itemKey: "dahi", products: [product("Dahi")] });
  timeline = recordResultGroup(timeline, { ...resultGroupIdentity({ query: "paneer" }), query: "paneer", products: [], status: "searching", afterMessageId: "a1" });
  const saved = JSON.parse(JSON.stringify(conversationSnapshot({ submittedQuery: "dahi", products: [], searched: false, messages, resultsAfterMessageId: null, timeline })));
  assert.deepEqual(saved.result_groups.map((group) => group.item_label), ["Atta", "Dahi"], "a search still running is not saved");
  assert.deepEqual(saved.products.map((item) => item.name), ["Atta", "Dahi"], "older readers still get every product");
  assert.equal(saved.searched, true);
  assert.equal(saved.results_after_message_id, "a1");

  const restoredTimeline = restoreTimeline(saved.timeline);
  const groups = restoreResultGroups({ resultGroups: saved.result_groups, products: saved.products, searched: saved.searched, submittedQuery: saved.submitted_query, resultsAfterMessageId: saved.results_after_message_id, resultsSeq: restoredTimeline.resultsSeq });
  const restored = withResultGroups(restoredTimeline, groups);
  assert.deepEqual(layout(placeTimelineItems({ messages, timeline: restored })), ["u1", "results:atta", "a1", "results:dahi"]);
  const next = moveBasketCard(restored, { provider: "zepto", afterMessageId: "a1", previous: null });
  assert.deepEqual(layout(placeTimelineItems({ messages, timeline: next })), ["u1", "results:atta", "a1", "results:dahi", "basket:zepto"]);
});

test("a chat saved before groups loads as one group", () => {
  const groups = restoreResultGroups({ products: [product("Desi Farms Malai Dahi")], searched: true, submittedQuery: "dahi", resultsAfterMessageId: "u1", resultsSeq: 2 });
  assert.deepEqual(groups, [{ key: "dahi", label: "Dahi", query: "dahi", products: [product("Desi Farms Malai Dahi")], status: "done", afterMessageId: "u1", seq: 2 }]);
  assert.equal(restoreResultGroups({ products: [], searched: true, submittedQuery: "xyz" })[0].products.length, 0, "an empty search still shows its 'nothing found' card");
  assert.deepEqual(restoreResultGroups({ products: [], searched: false, submittedQuery: "hi" }), []);
  assert.deepEqual(restoreResultGroups({ resultGroups: [], products: [], searched: false }), []);
  const timeline = withResultGroups(restoreTimeline({ seq: 1, results_seq: 2 }), groups);
  assert.equal(timeline.seq, 2);
});

test("restoring drops malformed groups and duplicates, and caps the count", () => {
  const groups = restoreResultGroups({ resultGroups: [null, "x", { item_key: "atta", item_label: "Atta", query: "atta", products: [product("A")], after_message_id: "u1", seq: 1 }, { item_key: "atta", query: "atta", products: [product("B")], after_message_id: "", seq: -3 }] });
  assert.deepEqual(groups.map((group) => [group.key, group.products[0].name, group.afterMessageId, group.seq]), [["atta", "B", null, 0]]);
  let timeline = emptyTimeline();
  for (let index = 0; index < MAX_RESULT_GROUPS + 5; index += 1) timeline = voiceResults(timeline, [], { query: `item ${index}`, products: [product(`P${index}`)] });
  assert.equal(timeline.groups.length, MAX_RESULT_GROUPS);
  assert.equal(timeline.groups.at(-1).key, `item ${MAX_RESULT_GROUPS + 4}`);
});
