import assert from "node:assert/strict";
import test from "node:test";
import { mapRealtimeBoundaryEvent, mapRealtimeTranscriptEvent } from "../app/voice-transcript.mjs";
import { createTranscriptTurnTracker } from "../app/voice-transcript-turns.mjs";

function replay(rawEvents, tracker = createTranscriptTurnTracker({ makeId: (n) => `turn-${n}` })) {
  const bubbles = [];
  for (const raw of rawEvents) {
    if (typeof raw === "function") { raw(); continue; }
    const boundary = mapRealtimeBoundaryEvent(raw);
    if (boundary) { tracker.boundary(boundary); continue; }
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

// GPT-Live: no item ids, no transcript-done events, only session-timeline timing.
const liveUser = (delta, start_ms, end_ms) => ({ type: "session.input_transcript.delta", event_id: `evt-u-${start_ms}`, delta, start_ms, end_ms });
const liveAssistant = (delta, start_ms, end_ms) => ({ type: "session.output_transcript.delta", event_id: `evt-a-${start_ms}`, delta, start_ms, end_ms });
const roles = (bubbles) => bubbles.map((item) => [item.role, item.content]);

test("GPT-Live session without done events or ids splits into turns by timeline", () => {
  const { bubbles } = replay([
    liveUser("डियोडोरेंट ऑर्डर ", 500, 1200),
    liveUser("करना है", 1200, 2000),
    { type: "session.delegation.created", event_id: "evt-d1", offset_ms: 2100, delegation: { id: "del-1", type: "delegation", target: "responses" } },
    liveAssistant("Ek second, ", 2400, 3000),
    liveAssistant("dekh rahi hoon.", 3000, 3800),
    { type: "response.event", event_id: "evt-r1", delegation_id: "del-1", event: { type: "response.completed" } },
    liveAssistant(" Zepto par Nivea aur AXE hain. Kaunsa wala basket mein add karoon?", 3900, 8200),
    liveUser("मुझे चाहिए आक्स वाला डार्क", 8800, 10600),
    liveAssistant("Theek hai, AXE Dark dekh rahi hoon.", 10900, 12500),
    // Barge-in while the assistant is still speaking.
    liveUser("हाँ हाँ हाँ ये ही चाहिए", 12300, 13600),
    liveAssistant(" Kaunsa size chahiye aapko?", 12500, 14000),
    liveUser("सबसे छोटा", 16000, 16800),
    liveAssistant("Theek hai, sabse chhota add kar rahi hoon.", 17200, 19800),
    liveUser("Zepto पर अभी फाइनल बिल दिखा देना", 22000, 24500),
  ]);
  assert.deepEqual(roles(bubbles), [
    ["user", "डियोडोरेंट ऑर्डर करना है"],
    ["assistant", "Ek second, dekh rahi hoon. Zepto par Nivea aur AXE hain. Kaunsa wala basket mein add karoon?"],
    ["user", "मुझे चाहिए आक्स वाला डार्क"],
    ["assistant", "Theek hai, AXE Dark dekh rahi hoon. Kaunsa size chahiye aapko?"],
    ["user", "हाँ हाँ हाँ ये ही चाहिए"],
    ["user", "सबसे छोटा"],
    ["assistant", "Theek hai, sabse chhota add kar rahi hoon."],
    ["user", "Zepto पर अभी फाइनल बिल दिखा देना"],
  ]);
});

test("a long pause by the same speaker starts a new bubble", () => {
  const { bubbles } = replay([
    liveUser("रुको एक मिनट", 1000, 2000),
    liveUser("प्रोमो कुछ है क्या", 4500, 6000),
  ]);
  assert.deepEqual(roles(bubbles), [["user", "रुको एक मिनट"], ["user", "प्रोमो कुछ है क्या"]]);
});

test("a tool-call pause inside the assistant's reply keeps one bubble", () => {
  const { bubbles } = replay([
    liveUser("कितना देर लगेगा आने के लिए", 1000, 2500),
    liveAssistant("Ek second, main check karti hoon.", 3000, 4500),
    liveAssistant(" Bill mein abhi delivery ka exact time nahi dikh raha.", 9000, 11500),
    liveAssistant(" Agla kya karna chahenge?", 14500, 15500),
    liveUser("तो approximate क्या होगा", 17000, 18500),
  ]);
  assert.deepEqual(roles(bubbles), [
    ["user", "कितना देर लगेगा आने के लिए"],
    ["assistant", "Ek second, main check karti hoon. Bill mein abhi delivery ka exact time nahi dikh raha. Agla kya karna chahenge?"],
    ["user", "तो approximate क्या होगा"],
  ]);
});

test("Realtime boundary events split turns even without done events, ids or timing", () => {
  let clock = 0;
  const at = (ms) => () => { clock = ms; };
  const tracker = createTranscriptTurnTracker({ makeId: (n) => `turn-${n}`, now: () => clock });
  const { bubbles } = replay([
    { type: "input_audio_buffer.speech_started" },
    userDelta("deodorant order karna hai"),
    { type: "input_audio_buffer.speech_stopped" },
    { type: "input_audio_buffer.committed" },
    { type: "response.created" },
    assistantDelta("Ek second, dekh rahi hoon."),
    { type: "response.output_audio.done" },
    { type: "response.done" },
    at(100),
    { type: "input_audio_buffer.speech_started" },
    userDelta("AXE wala dark"),
    { type: "input_audio_buffer.speech_stopped" },
    { type: "response.created" },
    assistantDelta("Theek hai."),
    { type: "response.done" },
    at(200),
    { type: "response.created" },
    assistantDelta("Kaunsa size chahiye?"),
  ], tracker);
  assert.deepEqual(roles(bubbles), [
    ["user", "deodorant order karna hai"],
    ["assistant", "Ek second, dekh rahi hoon."],
    ["user", "AXE wala dark"],
    ["assistant", "Theek hai."],
    ["assistant", "Kaunsa size chahiye?"],
  ]);
});

test("when the other role finishes a turn, a role that went quiet before it closes too", () => {
  let clock = 0;
  const at = (ms) => () => { clock = ms; };
  const tracker = createTranscriptTurnTracker({ makeId: (n) => `turn-${n}`, now: () => clock });
  const { bubbles } = replay([
    userDelta("do kilo atta"),
    at(300),
    assistantDelta("Bilkul, Aashirvaad 5 kilo hai."),
    { type: "response.done" },
    at(600),
    userDelta("haan wahi"),
  ], tracker);
  assert.deepEqual(roles(bubbles), [["user", "do kilo atta"], ["assistant", "Bilkul, Aashirvaad 5 kilo hai."], ["user", "haan wahi"]]);
});

test("a short backchannel during overlapping speech does not split the speaker's bubble", () => {
  const { bubbles } = replay([
    liveUser("I'd like", 1000, 1200),
    liveAssistant("Haan", 1300, 1600),
    liveUser(" to change my booking", 1700, 2500),
    liveUser(" for Friday", 2500, 3100),
  ]);
  assert.deepEqual(roles(bubbles), [["user", "I'd like to change my booking for Friday"], ["assistant", "Haan"]]);
});

test("both speakers talking at once keep exactly one growing bubble each", () => {
  const events = [];
  for (let index = 0; index < 8; index += 1) {
    const start = 1000 + index * 300;
    events.push(liveUser(`u${index} `, start, start + 300));
    events.push(liveAssistant(`a${index} `, start + 50, start + 350));
  }
  const { bubbles } = replay(events);
  assert.deepEqual(roles(bubbles), [
    ["user", "u0 u1 u2 u3 u4 u5 u6 u7"],
    ["assistant", "a0 a1 a2 a3 a4 a5 a6 a7"],
  ]);
});

test("late fragments that arrive out of order still join their own turn", () => {
  const { bubbles } = replay([
    liveUser("Fortune ", 1000, 1400),
    liveAssistant("Ji", 1500, 1700),
    liveUser("oil ek litre", 1400, 2200),
  ]);
  assert.deepEqual(roles(bubbles), [["user", "Fortune oil ek litre"], ["assistant", "Ji"]]);
});

test("provider ids stay authoritative even across long timeline gaps", () => {
  const { bubbles } = replay([
    userDelta("do kilo ", { item_id: "in-1", start_ms: 1000, end_ms: 1500 }),
    userDelta("atta", { item_id: "in-1", start_ms: 6000, end_ms: 6500 }),
    userDelta("aur cheeni", { item_id: "in-2", start_ms: 6600, end_ms: 7000 }),
  ]);
  assert.deepEqual(roles(bubbles), [["user", "do kilo atta"], ["user", "aur cheeni"]]);
});

test("session pause closes both open turns", () => {
  const { bubbles } = replay([
    liveUser("ek minute", 1000, 1500),
    liveAssistant("Ji", 1600, 1800),
    { type: "session.paused", event_id: "evt-p" },
    liveUser("haan ab bolo", 1900, 2500),
    liveAssistant("Ji boliye", 2600, 3000),
  ]);
  assert.deepEqual(roles(bubbles), [["user", "ek minute"], ["assistant", "Ji"], ["user", "haan ab bolo"], ["assistant", "Ji boliye"]]);
});
