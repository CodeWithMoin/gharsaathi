import assert from "node:assert/strict";
import test from "node:test";
import { mapRealtimeTranscriptEvent } from "../app/voice-transcript.mjs";
import { createTranscriptTurnTracker } from "../app/voice-transcript-turns.mjs";

function replay(rawEvents, tracker = createTranscriptTurnTracker({ makeId: (n) => `turn-${n}` })) {
  const bubbles = [];
  for (const raw of rawEvents) {
    const event = mapRealtimeTranscriptEvent(raw);
    if (!event) continue;
    const turn = tracker.apply(event);
    if (!turn) continue;
    const index = bubbles.findIndex((item) => item.id === turn.id);
    if (index === -1) bubbles.push(turn);
    else bubbles[index] = turn;
  }
  return { bubbles, tracker };
}

const userDelta = (delta, extra = {}) => ({ type: "conversation.item.input_audio_transcription.delta", delta, ...extra });
const assistantDelta = (delta, extra = {}) => ({ type: "response.output_audio_transcript.delta", delta, ...extra });

test("interleaved deltas without ids stay in one bubble per role", () => {
  const { bubbles } = replay([
    userDelta("तो मैं तुम्हारा क्या करूँगा मेरे लिए फिर"),
    assistantDelta("मैं options confirm "),
    userDelta("? अगर हम मेरा "),
    assistantDelta("करके "),
    userDelta("काम नहीं करोगे तो"),
    assistantDelta("बता सकती हूँ,"),
  ]);
  assert.deepEqual(bubbles, [
    { id: "turn-1", role: "user", content: "तो मैं तुम्हारा क्या करूँगा मेरे लिए फिर? अगर हम मेरा काम नहीं करोगे तो" },
    { id: "turn-2", role: "assistant", content: "मैं options confirm करके बता सकती हूँ," },
  ]);
});

test("interleaved deltas with ids stay in one bubble per role and completion replaces text", () => {
  const { bubbles } = replay([
    userDelta("do kilo ", { item_id: "in-1" }),
    assistantDelta("Bilkul, ", { response_id: "resp-1" }),
    userDelta("atta", { item_id: "in-1" }),
    assistantDelta("dekh rahi hoon.", { response_id: "resp-1" }),
    { type: "conversation.item.input_audio_transcription.completed", item_id: "in-1", transcript: "Do kilo atta." },
    { type: "response.output_audio_transcript.done", response_id: "resp-1", transcript: "Bilkul, dekh rahi hoon." },
  ]);
  assert.deepEqual(bubbles, [
    { id: "turn-1", role: "user", content: "Do kilo atta." },
    { id: "turn-2", role: "assistant", content: "Bilkul, dekh rahi hoon." },
  ]);
});

test("a turn started without an id adopts the id of its completion event", () => {
  const { bubbles } = replay([
    userDelta("do kilo "),
    assistantDelta("Haan "),
    userDelta("atta"),
    { type: "conversation.item.input_audio_transcription.completed", item_id: "in-1", transcript: "Do kilo atta." },
  ]);
  assert.deepEqual(bubbles.map((item) => [item.role, item.content]), [["user", "Do kilo atta."], ["assistant", "Haan"]]);
});

test("new turns after finish or completion create new bubbles in order", () => {
  const { bubbles } = replay([
    userDelta("pehla sawaal"),
    { type: "conversation.item.input_audio_transcription.completed", transcript: "Pehla sawaal." },
    assistantDelta("Pehla jawab"),
    { type: "response.output_audio_transcript.done" },
    userDelta("doosra sawaal"),
    assistantDelta("Doosra jawab"),
    userDelta("…aur kuch"),
  ]);
  assert.deepEqual(bubbles.map((item) => [item.id, item.role, item.content]), [
    ["turn-1", "user", "Pehla sawaal."],
    ["turn-2", "assistant", "Pehla jawab"],
    ["turn-3", "user", "doosra sawaal…aur kuch"],
    ["turn-4", "assistant", "Doosra jawab"],
  ]);
});

test("a different id for the same role starts a new bubble", () => {
  const { bubbles } = replay([
    assistantDelta("first", { response_id: "resp-1" }),
    assistantDelta("second", { response_id: "resp-2" }),
    assistantDelta(" more", { response_id: "resp-1" }),
  ]);
  assert.deepEqual(bubbles.map((item) => item.content), ["first more", "second"]);
});

test("reset closes in-progress turns", () => {
  const tracker = createTranscriptTurnTracker({ makeId: (n) => `turn-${n}` });
  replay([userDelta("hello")], tracker);
  tracker.reset();
  const { bubbles } = replay([userDelta("again")], tracker);
  assert.deepEqual(bubbles, [{ id: "turn-2", role: "user", content: "again" }]);
});
