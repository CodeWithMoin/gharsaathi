import assert from "node:assert/strict";
import test from "node:test";
import {
  CALL_OUTCOMES,
  INBOUND_CARD_MAX_AGE_MS,
  inboundCallCardText,
  inboundCallUrgent,
  inboundCardsToShow,
  inboundHeadline,
  inboundOutcomeText,
  placeInboundCalls,
  riderDirectionsLine,
} from "../../app/inbound-calls.mjs";

const AT = "2026-11-22T08:44:00Z"; // 2:14 pm in India

test("the card reads who called, when, the outcome and the summary", () => {
  assert.equal(
    inboundCallCardText({ started_at: AT, provider: "zepto", outcome: "route_confirmed", summary: "Guided to the gate." }),
    "Zepto rider called · 2:14 pm · Route confirmed — Guided to the gate.",
  );
  assert.equal(inboundCallCardText({ started_at: AT, provider: null, outcome: "no_decision", summary: null }), "A delivery partner called · 2:14 pm · No outcome recorded");
});

test("every outcome has its own plain-English copy", () => {
  const expected = {
    route_confirmed: "Route confirmed",
    owner_transfer_requested: "Caller asked for you",
    provider_contact_needed: "Rider needed help — check the Zepto app",
    unauthorized_caller: "Unverified caller — no address shared",
    general_request_handled: "Question answered",
    out_of_scope: "Not about a delivery",
    no_decision: "No outcome recorded",
  };
  assert.deepEqual(Object.keys(expected).sort(), [...CALL_OUTCOMES].sort());
  for (const outcome of CALL_OUTCOMES) assert.equal(inboundOutcomeText(outcome, "zepto"), expected[/** @type {keyof typeof expected} */ (outcome)]);
  assert.equal(inboundOutcomeText("provider_contact_needed", "swiggy"), "Rider needed help — check the Swiggy app");
  assert.equal(inboundOutcomeText("something_new", "zepto"), "No outcome recorded");
  assert.equal(inboundHeadline("zepto", "unauthorized_caller"), "Someone called the delivery line");
  assert.equal(inboundHeadline("zepto", "out_of_scope"), "Someone called the delivery line");
  assert.equal(inboundHeadline("swiggy", "route_confirmed"), "Swiggy rider called");
  assert.ok(inboundCallUrgent({ outcome: "owner_transfer_requested" }) && inboundCallUrgent({ outcome: "provider_contact_needed" }));
  assert.ok(!inboundCallUrgent({ outcome: "route_confirmed" }));
});

test("the order card says the rider has directions only after a confirmed route for that order", () => {
  const order = { store: "Zepto", placedAt: "2026-11-22T08:30:00Z", status: "out_for_delivery" };
  const call = { started_at: AT, provider: "zepto", outcome: "route_confirmed" };
  assert.equal(riderDirectionsLine([call], order), "Rider has your directions");
  assert.equal(riderDirectionsLine([{ ...call, outcome: "provider_contact_needed" }], order), null);
  assert.equal(riderDirectionsLine([{ ...call, started_at: "2026-11-22T08:00:00Z" }], order), null);
  assert.equal(riderDirectionsLine([{ ...call, provider: "swiggy" }], order), null);
  assert.equal(riderDirectionsLine([call], { ...order, status: "delivered" }), null);
});

test("recent, undismissed calls show oldest first", () => {
  const now = Date.parse("2026-11-22T09:00:00Z");
  const calls = [
    { id: "b", started_at: "2026-11-22T08:50:00Z" },
    { id: "a", started_at: "2026-11-22T08:40:00Z" },
    { id: "old", started_at: new Date(now - INBOUND_CARD_MAX_AGE_MS - 1000).toISOString() },
    { id: "hidden", started_at: "2026-11-22T08:45:00Z" },
  ];
  assert.deepEqual(inboundCardsToShow(calls, now, new Set(["hidden"])).map((call) => call.id), ["a", "b"]);
});

test("call cards sit right after the order status card, else at the end", () => {
  const items = [{ kind: "message" }, { kind: "order-status" }, { kind: "message" }];
  assert.deepEqual(placeInboundCalls(items, ["c1"]).map((item) => item.kind), ["message", "order-status", "inbound-call", "message"]);
  assert.deepEqual(placeInboundCalls([{ kind: "message" }], ["c1"]).map((item) => item.kind), ["message", "inbound-call"]);
  assert.equal(placeInboundCalls(items, []), items);
});
