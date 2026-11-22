import assert from "node:assert/strict";
import test from "node:test";
import {
  newRiderCalls,
  notesDraft,
  notesError,
  notesPayload,
  placeRiderCalls,
  riderCallCardText,
  riderCallTime,
  riderCardsToShow,
  riderOutcomeText,
  riderStatus,
} from "../../app/rider-assist.mjs";

const call = (overrides = {}) => ({ id: "c1", seen: false, started_at: "2026-11-22T02:54:00Z", outcome: "answered", answered_from: ["notes"], rider_said: "Gate pe hoon, flat number?", urgency: "normal", ...overrides });

test("the chat card reads like 'Rider called · 8:24 am · quote — answered from your notes'", () => {
  assert.equal(riderCallTime("2026-11-22T02:54:00Z"), "8:24 am");
  assert.equal(riderCallCardText(call()), "Rider called · 8:24 am · 'Gate pe hoon, flat number?' — answered from your notes");
  assert.equal(riderCallCardText(call({ rider_said: null, outcome: "assistant_unavailable", answered_from: [] })), "Rider called · 8:24 am — the assistant couldn't answer");
  assert.equal(riderOutcomeText(call({ answered_from: ["notes", "order"] })), "answered from your notes and order");
  assert.equal(riderOutcomeText(call({ outcome: "not_delivery", answered_from: [] })), "not a delivery call, message taken");
});

test("only unseen, not-yet-toasted calls toast, oldest first", () => {
  const calls = [call({ id: "b", started_at: "2026-11-22T03:00:00Z" }), call({ id: "a" }), call({ id: "s", seen: true })];
  assert.deepEqual(newRiderCalls(calls, new Set()).map((c) => c.id), ["a", "b"]);
  assert.deepEqual(newRiderCalls(calls, new Set(["a"])).map((c) => c.id), ["b"]);
});

test("cards skip dismissed and old calls", () => {
  const now = Date.parse("2026-11-22T10:00:00Z");
  const calls = [call({ id: "old", started_at: "2026-11-21T08:00:00Z" }), call({ id: "x" }), call({ id: "gone" })];
  assert.deepEqual(riderCardsToShow(calls, now, new Set(["gone"])).map((c) => c.id), ["x"]);
});

test("cards are placed after their anchor message and its attachments", () => {
  const items = [{ kind: "message", message: { id: "m1" } }, { kind: "results" }, { kind: "message", message: { id: "m2" } }];
  const placed = placeRiderCalls(items, [{ card: "A", afterMessageId: "m1" }, { card: "B", afterMessageId: null }, { card: "C", afterMessageId: "missing" }]);
  assert.deepEqual(placed.map((item) => item.kind === "rider-call" ? item.card : item.kind), ["message", "results", "A", "message", "B", "C"]);
});

test("delivery notes mirror the backend rules", () => {
  assert.equal(notesError({ landmark: "Near Shiv mandir", directions: "Gate 2, flat 402" }), null);
  assert.match(notesError({ landmark: "", directions: "OTP is 1234" }), /OTP/);
  assert.match(notesError({ landmark: "x".repeat(161), directions: "" }), /landmark/);
  assert.match(notesError({ landmark: "", directions: "see https://maps" }), /links/);
  assert.deepEqual(notesPayload({ landmark: "  Near   park ", directions: " ", handoff: "leave_at_door", preferred_language: "english" }), { landmark: "Near park", directions: null, handoff: "leave_at_door", preferred_language: "english" });
  assert.deepEqual(notesDraft({ handoff: "bogus" }), { landmark: "", directions: "", handoff: "call_again", preferred_language: "hinglish" });
});

test("the switch reflects the server flag, owner and setup", () => {
  const base = { feature_enabled: true, owner_configured: true, stream_auth_configured: true, enabled: true };
  assert.equal(riderStatus(base).label, "On");
  assert.equal(riderStatus({ ...base, enabled: false }).label, "Off");
  assert.equal(riderStatus({ ...base, feature_enabled: false }).label, "Turned off on this server");
  assert.equal(riderStatus({ ...base, owner_configured: false }).active, false);
  assert.equal(riderStatus({ ...base, stream_auth_configured: false }).label, "Server setup incomplete");
});
