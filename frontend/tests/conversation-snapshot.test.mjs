import assert from "node:assert/strict";
import test from "node:test";
import { conversationSnapshot, MAX_CHAT_MESSAGE_CHARS, MAX_CHAT_MESSAGES, MAX_PRODUCTS, MAX_SUBMITTED_QUERY_CHARS } from "../app/conversation-snapshot.mjs";

const message = (index, content = `turn ${index}`) => ({ id: `voice-${index}`, role: index % 2 ? "assistant" : "user", content, modality: "voice", created_at: "2026-11-22T10:00:00Z" });

test("passes a normal voice conversation through unchanged", () => {
  const messages = [message(0, "Deodorant chahiye"), message(1, "Ek second, dekh rahi hoon.")];
  assert.deepEqual(conversationSnapshot({ submittedQuery: "deodorant", products: [], searched: true, messages, resultsAfterMessageId: "voice-1" }), {
    submitted_query: "deodorant",
    products: [],
    searched: true,
    messages,
    results_after_message_id: "voice-1",
  });
});

test("keeps every field within the backend limits so a save is never rejected", () => {
  const longTurn = "हाँ ".repeat(MAX_CHAT_MESSAGE_CHARS);
  const messages = [message(0, longTurn), message(1, "   "), ...Array.from({ length: MAX_CHAT_MESSAGES + 50 }, (_, index) => message(index + 2))];
  const snapshot = conversationSnapshot({ submittedQuery: "a".repeat(400), products: Array.from({ length: 150 }, (_, index) => index), searched: true, messages, resultsAfterMessageId: null });
  assert.equal(snapshot.submitted_query.length, MAX_SUBMITTED_QUERY_CHARS);
  assert.equal(snapshot.products.length, MAX_PRODUCTS);
  assert.equal(snapshot.messages.length, MAX_CHAT_MESSAGES);
  assert.ok(snapshot.messages.every((item) => item.content.trim() && item.content.length <= MAX_CHAT_MESSAGE_CHARS));
  assert.equal(snapshot.messages.at(-1).id, `voice-${MAX_CHAT_MESSAGES + 51}`);
});

test("drops blank turns and trims spoken text", () => {
  const snapshot = conversationSnapshot({ submittedQuery: "  ", products: [], searched: false, messages: [message(0, "  Zepto par bill  "), message(1, " ")], resultsAfterMessageId: null });
  assert.deepEqual(snapshot.messages.map((item) => item.content), ["Zepto par bill"]);
  assert.equal(snapshot.submitted_query, "");
});
