import assert from "node:assert/strict";
import test from "node:test";

import { conversationIntent, conversationReply } from "../app/conversation-intent.mjs";

test("capability questions never become product searches", () => {
  assert.equal(conversationIntent("kya kar sakte ho"), "capabilities");
  assert.equal(conversationIntent("What can you do?"), "capabilities");
  assert.match(conversationReply("kya kar sakte ho", { swiggyConnected: false, zeptoConnected: true }), /compare/);
});

test("provider status questions use local connection state", () => {
  assert.equal(conversationIntent("can you see zepto?"), "connection_status");
  assert.equal(
    conversationReply("Zepto connected hai?", { swiggyConnected: false, zeptoConnected: true }),
    "Zepto connected hai. Main selected address par products check kar sakti hoon.",
  );
});

test("real product requests continue to search", () => {
  assert.equal(conversationIntent("Amul Taaza milk 1 litre"), null);
  assert.equal(conversationIntent("Fortune oil milega?"), null);
  assert.equal(conversationIntent("milk"), null);
});

test("brand-only order requests ask for the missing product", () => {
  assert.equal(conversationIntent("can you order amul"), "ambiguous_brand");
  assert.equal(conversationIntent("Amul mangwa do"), "ambiguous_brand");
  assert.equal(
    conversationReply("can you order amul", { swiggyConnected: false, zeptoConnected: true }),
    "Amul ka kya chahiye—milk, butter, paneer, ya kuch aur? Pack size bhi bata dijiye.",
  );
  assert.equal(conversationIntent("Amul milk 1 litre"), null);
});
