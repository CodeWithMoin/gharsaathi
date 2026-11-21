/**
 * Normalize transcript events emitted by both the Live API and older Realtime
 * event names used by local development snapshots.
 *
 * @param {unknown} raw
 * @returns {{ role: "user" | "assistant", operation: "append" | "replace" | "finish", text: string, sourceId?: string } | null}
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
  return { role, operation, text, ...(typeof source === "string" && source ? { sourceId: source } : {}) };
}
