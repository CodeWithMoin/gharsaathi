/**
 * Normalize transcript events emitted by both the Live API and older Realtime
 * event names used by local development snapshots.
 *
 * GPT-Live transcript deltas carry `start_ms`/`end_ms` on the session timeline
 * but no item id and no done event, so the timing is preserved for the turn
 * tracker to find message boundaries.
 *
 * @param {unknown} raw
 * @returns {{ role: "user" | "assistant", operation: "append" | "replace" | "finish", text: string, sourceId?: string, startMs?: number, endMs?: number } | null}
 */
export function mapRealtimeTranscriptEvent(raw) {
  if (!raw || typeof raw !== "object") return null;
  const event = /** @type {Record<string, unknown>} */ (raw);
  const type = typeof event.type === "string" ? event.type : "";
  const inputDeltaTypes = new Set(["session.input_transcript.delta", "conversation.item.input_audio_transcription.delta"]);
  const inputDoneTypes = new Set(["session.input_transcript.done", "conversation.item.input_audio_transcription.completed"]);
  const outputDeltaTypes = new Set(["session.output_transcript.delta", "response.audio_transcript.delta", "response.output_audio_transcript.delta"]);
  const outputDoneTypes = new Set(["session.output_transcript.done", "response.audio_transcript.done", "response.output_audio_transcript.done"]);

  let role;
  let operation;
  if (inputDeltaTypes.has(type)) { role = "user"; operation = "append"; }
  else if (inputDoneTypes.has(type)) { role = "user"; operation = "replace"; }
  else if (outputDeltaTypes.has(type)) { role = "assistant"; operation = "append"; }
  else if (outputDoneTypes.has(type)) { role = "assistant"; operation = "replace"; }
  else return null;

  const delta = typeof event.delta === "string" ? event.delta : "";
  const transcript = typeof event.transcript === "string" ? event.transcript : "";
  const text = operation === "append" ? delta : transcript;
  if (operation === "replace" && !text) operation = "finish";
  const source = event.item_id ?? event.response_id;
  const startMs = finiteNumber(event.start_ms);
  const endMs = finiteNumber(event.end_ms);
  return {
    role,
    operation,
    text,
    ...(typeof source === "string" && source ? { sourceId: source } : {}),
    ...(startMs !== undefined ? { startMs } : {}),
    ...(endMs !== undefined ? { endMs } : {}),
  };
}

/** @param {unknown} value */
function finiteNumber(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

const USER_STARTS = new Set(["input_audio_buffer.speech_started"]);
const USER_ENDS = new Set(["input_audio_buffer.speech_stopped", "input_audio_buffer.committed"]);
const ASSISTANT_STARTS = new Set(["response.created"]);
const ASSISTANT_ENDS = new Set(["response.done", "response.output_audio.done", "response.audio.done"]);
const SESSION_ENDS = new Set(["session.paused", "session.closed"]);

/**
 * Maps top-level turn-boundary events. Realtime sends speech/response lifecycle
 * events; GPT-Live only pauses or closes the session. Nested `response.event`
 * payloads describe backend reasoning, not speech, and are ignored.
 *
 * @param {unknown} raw
 * @returns {{ role: "user" | "assistant" | "both", operation: "start" | "end" } | null}
 */
export function mapRealtimeBoundaryEvent(raw) {
  if (!raw || typeof raw !== "object") return null;
  const type = /** @type {Record<string, unknown>} */ (raw).type;
  if (typeof type !== "string") return null;
  if (USER_STARTS.has(type)) return { role: "user", operation: "start" };
  if (USER_ENDS.has(type)) return { role: "user", operation: "end" };
  if (ASSISTANT_STARTS.has(type)) return { role: "assistant", operation: "start" };
  if (ASSISTANT_ENDS.has(type)) return { role: "assistant", operation: "end" };
  if (SESSION_ENDS.has(type)) return { role: "both", operation: "end" };
  return null;
}
