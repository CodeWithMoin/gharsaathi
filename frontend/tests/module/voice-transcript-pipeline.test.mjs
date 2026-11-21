import assert from "node:assert/strict";
import test from "node:test";

import { createTranscriptTurnTracker } from "../../app/voice-transcript-turns.mjs";
import { mapRealtimeTranscriptEvent } from "../../app/voice-transcript.mjs";

// Raw provider events -> mapRealtimeTranscriptEvent -> turn tracker -> rendered bubbles.
function render(rawEvents) {
  const tracker = createTranscriptTurnTracker({ makeId: (n) => `turn-${n}` });
  const bubbles = [];
  for (const raw of rawEvents) {
    const event = mapRealtimeTranscriptEvent(raw);
    const turn = event && tracker.apply(event);
    if (!turn) continue;
    const index = bubbles.findIndex((item) => item.id === turn.id);
    if (index === -1) bubbles.push(turn);
    else bubbles[index] = turn;
  }
  return bubbles.map(({ id, role, content }) => [id, role, content]);
}

test("noise, malformed events and whitespace never create bubbles or skip ids", () => {
  assert.deepEqual(
    render([
      null,
      "session.input_transcript.delta",
      { type: "response.created" },
      { type: "session.input_transcript.delta", delta: 42 },
      { type: "session.input_transcript.delta", delta: "   " },
      { type: "session.output_transcript.done" },
      { type: "session.input_transcript.delta", delta: "chai patti" },
    ]),
    [["turn-1", "user", "chai patti"]],
  );
});

test("legacy and Live event names mix into the same conversation", () => {
  assert.deepEqual(
    render([
      { type: "session.input_transcript.delta", delta: "Tata " },
      { type: "conversation.item.input_audio_transcription.delta", delta: "Tea Gold" },
      { type: "session.input_transcript.done", transcript: "Tata Tea Gold." },
      { type: "response.audio_transcript.delta", delta: "Tata Tea Gold " },
      { type: "response.output_audio_transcript.delta", delta: "₹285 ka hai." },
    ]),
    [
      ["turn-1", "user", "Tata Tea Gold."],
      ["turn-2", "assistant", "Tata Tea Gold ₹285 ka hai."],
    ],
  );
});

test("barge-in: an interrupted reply stays whole and the next reply is a new bubble", () => {
  assert.deepEqual(
    render([
      { type: "response.output_audio_transcript.delta", response_id: "r1", delta: "Main teen options " },
      { type: "conversation.item.input_audio_transcription.delta", item_id: "u2", delta: "ruko, " },
      { type: "response.output_audio_transcript.delta", response_id: "r1", delta: "dikha" },
      { type: "response.output_audio_transcript.done", response_id: "r1" },
      { type: "conversation.item.input_audio_transcription.completed", item_id: "u2", transcript: "Ruko, sirf Amul." },
      { type: "response.output_audio_transcript.delta", response_id: "r3", delta: "Theek hai, Amul." },
    ]),
    [
      ["turn-1", "assistant", "Main teen options dikha"],
      ["turn-2", "user", "Ruko, sirf Amul."],
      ["turn-3", "assistant", "Theek hai, Amul."],
    ],
  );
});

test("a late completion for an earlier item updates that bubble, not the current one", () => {
  assert.deepEqual(
    render([
      { type: "conversation.item.input_audio_transcription.delta", item_id: "u1", delta: "atta" },
      { type: "conversation.item.input_audio_transcription.completed", item_id: "u1", transcript: "Atta." },
      { type: "conversation.item.input_audio_transcription.delta", item_id: "u2", delta: "aur dal" },
      { type: "conversation.item.input_audio_transcription.completed", item_id: "u1", transcript: "Do kilo atta." },
    ]),
    [
      ["turn-1", "user", "Do kilo atta."],
      ["turn-2", "user", "aur dal"],
    ],
  );
});
