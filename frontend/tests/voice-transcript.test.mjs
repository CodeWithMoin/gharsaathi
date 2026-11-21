import assert from "node:assert/strict";
import test from "node:test";
import { mapRealtimeTranscriptEvent } from "../app/voice-transcript.mjs";

test("maps current session transcript deltas to chat roles", () => {
  assert.deepEqual(mapRealtimeTranscriptEvent({ type: "session.input_transcript.delta", delta: "do kilo " }), { role: "user", operation: "append", text: "do kilo " });
  assert.deepEqual(mapRealtimeTranscriptEvent({ type: "session.output_transcript.delta", delta: "Bilkul." }), { role: "assistant", operation: "append", text: "Bilkul." });
});

test("maps Live API transcript events and preserves stable item ids", () => {
  assert.deepEqual(mapRealtimeTranscriptEvent({ type: "conversation.item.input_audio_transcription.delta", item_id: "input-1", delta: "atta" }), { role: "user", operation: "append", text: "atta", sourceId: "input-1" });
  assert.deepEqual(mapRealtimeTranscriptEvent({ type: "response.output_audio_transcript.delta", response_id: "response-1", delta: "Options dekh rahi hoon" }), { role: "assistant", operation: "append", text: "Options dekh rahi hoon", sourceId: "response-1" });
});

test("completed events replace partial text with canonical transcript", () => {
  assert.deepEqual(mapRealtimeTranscriptEvent({ type: "conversation.item.input_audio_transcription.completed", item_id: "input-1", transcript: "Do kilo atta chahiye." }), { role: "user", operation: "replace", text: "Do kilo atta chahiye.", sourceId: "input-1" });
  assert.deepEqual(mapRealtimeTranscriptEvent({ type: "response.output_audio_transcript.done" }), { role: "assistant", operation: "finish", text: "" });
});

test("ignores unrelated and malformed events", () => {
  assert.equal(mapRealtimeTranscriptEvent({ type: "response.completed" }), null);
  assert.equal(mapRealtimeTranscriptEvent(null), null);
});
