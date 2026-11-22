import assert from "node:assert/strict";
import test from "node:test";
import { conversationTitle, isEmptyConversation, relativeChatDate, searchConversations, visibleConversations } from "../../app/chat-list.mjs";

const chat = (fields) => ({ id: "c1", title: "Shopping chat", preview: "Shopping chat", updated_at: "2026-11-22T10:00:00Z", is_active: false, ...fields });

test("chats with no messages are hidden", () => {
  const chats = [
    chat({ id: "empty" }),
    chat({ id: "counted-empty", title: "aata aur dahi", preview: "aata aur dahi", message_count: 0 }),
    chat({ id: "atta", title: "aata aur dahi", preview: "Atta ke 3 options mile." }),
    chat({ id: "assistant-only", preview: "Namaste! Kya mangwana hai?" }),
    chat({ id: "counted", message_count: 2 }),
  ];
  assert.deepEqual(visibleConversations(chats).map((item) => item.id), ["atta", "assistant-only", "counted"]);
  assert.equal(isEmptyConversation(chat({ title: "", preview: "" })), true);
});

test("titles are the first request, trimmed to one readable line", () => {
  assert.equal(conversationTitle(chat({ title: "aata aur dahi" })), "Aata aur dahi");
  assert.equal(conversationTitle(chat({ title: "  [sniff]  mujhe   Fortune oil chahiye " })), "Mujhe Fortune oil chahiye");
  const long = conversationTitle(chat({ title: "mujhe ghar ke liye Aashirvaad atta, Amul dahi, Fortune oil aur do packet Maggi chahiye" }));
  assert.ok(long.length <= 43, long);
  assert.ok(long.endsWith("…"));
  assert.equal(long, "Mujhe ghar ke liye Aashirvaad atta, Amul…");
});

test("titles prefer the items discussed, then fall back to the preview", () => {
  assert.equal(conversationTitle(chat({ title: "aata aur dahi", items: ["Atta", "Dahi"] })), "Atta, Dahi");
  assert.equal(conversationTitle(chat({ preview: "namaste! kya mangwana hai?" })), "Namaste! kya mangwana hai?");
  assert.equal(conversationTitle(chat({})), "Shopping chat");
});

test("dates read as Today, Yesterday, or a short date", () => {
  const now = new Date(2026, 10, 22, 20, 0);
  assert.equal(relativeChatDate(new Date(2026, 10, 22, 17, 28).toISOString(), now), "Today, 5:28");
  assert.equal(relativeChatDate(new Date(2026, 10, 22, 0, 5).toISOString(), now), "Today, 12:05");
  assert.equal(relativeChatDate(new Date(2026, 10, 21, 23, 59).toISOString(), now), "Yesterday");
  assert.equal(relativeChatDate(new Date(2026, 10, 20, 9, 0).toISOString(), now), "20 Nov");
  assert.equal(relativeChatDate(new Date(2025, 11, 31, 9, 0).toISOString(), now), "31 Dec 2025");
  assert.equal(relativeChatDate("not a date", now), "");
});

test("search matches the shown title, the server title and the preview", () => {
  const chats = [chat({ id: "a", title: "aata aur dahi", preview: "Atta ke options" }), chat({ id: "b", title: "shampoo", preview: "Dove 340ml", items: ["Shampoo"] })];
  assert.deepEqual(searchConversations(chats, "DAHI").map((item) => item.id), ["a"]);
  assert.deepEqual(searchConversations(chats, "dove").map((item) => item.id), ["b"]);
  assert.deepEqual(searchConversations(chats, "  ").map((item) => item.id), ["a", "b"]);
});
