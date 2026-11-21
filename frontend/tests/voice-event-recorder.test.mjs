import assert from "node:assert/strict";
import test from "node:test";
import { createVoiceEventRecorder, describeVoiceEvent, voiceEventLogEnabled, VOICE_EVENT_LOG_STORAGE_KEY } from "../app/voice-event-recorder.mjs";

const SECRET_TEXT = "मुझे AXE dark chahiye 9876543210";

test("describes events without any transcript or payload text", () => {
  const record = describeVoiceEvent({ type: "session.input_transcript.delta", event_id: "evt_1", delta: SECRET_TEXT, start_ms: 1000, end_ms: 1400 }, "datachannel");
  assert.deepEqual(record, { source: "datachannel", type: "session.input_transcript.delta", ids: ["event_id"], timing: { start_ms: 1000, end_ms: 1400 }, chars: { delta: SECRET_TEXT.length } });
  assert.ok(!JSON.stringify(record).includes("AXE"));
  assert.ok(!JSON.stringify(record).includes("evt_1"));
});

test("records nested Responses event types and delegation targets, not their payloads", () => {
  const nested = describeVoiceEvent({ type: "response.event", event_id: "e", delegation_id: "del", event: { type: "response.output_item.done", item: { arguments: SECRET_TEXT } } }, "sideband");
  assert.deepEqual(nested, { source: "sideband", type: "response.event", ids: ["event_id", "delegation_id"], inner: "response.output_item.done" });
  const delegation = describeVoiceEvent({ type: "session.delegation.created", event_id: "e", offset_ms: 3600, delegation: { id: "del", target: "responses" } }, "datachannel");
  assert.deepEqual(delegation, { source: "datachannel", type: "session.delegation.created", ids: ["event_id"], timing: { offset_ms: 3600 }, delegation_target: "responses" });
});

test("is off unless the env flag or the dev localStorage key enables it", () => {
  const storage = (value) => ({ getItem: (key) => (key === VOICE_EVENT_LOG_STORAGE_KEY ? value : null) });
  assert.equal(voiceEventLogEnabled({}), false);
  assert.equal(voiceEventLogEnabled({ envFlag: "1" }), true);
  assert.equal(voiceEventLogEnabled({ envFlag: "1", production: true }), true);
  assert.equal(voiceEventLogEnabled({ storage: storage("1") }), true);
  assert.equal(voiceEventLogEnabled({ storage: storage("1"), production: true }), false);
  assert.equal(voiceEventLogEnabled({ storage: storage(null) }), false);
  assert.equal(voiceEventLogEnabled({ storage: { getItem() { throw new Error("blocked"); } } }), false);
});

test("disabled recorder keeps nothing; enabled recorder is bounded", () => {
  const off = createVoiceEventRecorder({ enabled: false });
  off.record({ type: "session.started" }, "datachannel");
  assert.equal(off.records.length, 0);
  const on = createVoiceEventRecorder({ enabled: true, now: () => 5 });
  for (let index = 0; index < 1100; index += 1) on.record({ type: "session.output_transcript.delta", delta: "x" }, "datachannel");
  assert.equal(on.records.length, 1000);
  assert.deepEqual(on.records[0], { at: 5, source: "datachannel", type: "session.output_transcript.delta", ids: [], chars: { delta: 1 } });
});
