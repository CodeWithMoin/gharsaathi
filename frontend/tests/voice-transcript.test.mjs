import assert from "node:assert/strict";
import test from "node:test";
import { mapRealtimeBoundaryEvent, mapRealtimeTranscriptEvent } from "../app/voice-transcript.mjs";

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

test("keeps GPT-Live session timeline timing", () => {
  assert.deepEqual(
    mapRealtimeTranscriptEvent({ type: "session.input_transcript.delta", event_id: "e1", delta: "What is", start_ms: 1000, end_ms: 1200 }),
    { role: "user", operation: "append", text: "What is", startMs: 1000, endMs: 1200 },
  );
});

test("maps speech and response lifecycle events to turn boundaries", () => {
  assert.deepEqual(mapRealtimeBoundaryEvent({ type: "input_audio_buffer.speech_started" }), { role: "user", operation: "start" });
  assert.deepEqual(mapRealtimeBoundaryEvent({ type: "input_audio_buffer.speech_stopped" }), { role: "user", operation: "end" });
  assert.deepEqual(mapRealtimeBoundaryEvent({ type: "input_audio_buffer.committed" }), { role: "user", operation: "end" });
  assert.deepEqual(mapRealtimeBoundaryEvent({ type: "response.created" }), { role: "assistant", operation: "start" });
  assert.deepEqual(mapRealtimeBoundaryEvent({ type: "response.done" }), { role: "assistant", operation: "end" });
  assert.deepEqual(mapRealtimeBoundaryEvent({ type: "response.output_audio.done" }), { role: "assistant", operation: "end" });
  assert.deepEqual(mapRealtimeBoundaryEvent({ type: "session.paused" }), { role: "both", operation: "end" });
  assert.equal(mapRealtimeBoundaryEvent({ type: "response.event", event: { type: "response.completed" } }), null);
  assert.equal(mapRealtimeBoundaryEvent({ type: "session.input_transcript.delta" }), null);
  assert.equal(mapRealtimeBoundaryEvent(null), null);
});
