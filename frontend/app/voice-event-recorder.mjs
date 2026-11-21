/**
 * Dev-only, content-free recorder for realtime voice events. It keeps the
 * event type, which id fields are present, timing values and text lengths —
 * never transcript text, audio, arguments or other payload values.
 *
 * Enable with `NEXT_PUBLIC_VOICE_EVENT_LOG=1` at build/dev time, or (outside
 * production builds) in the browser console:
 * `localStorage.setItem("gharsaathi:voice-event-log", "1")`, then start voice.
 * Read the log with `copy(JSON.stringify(window.__gharsaathiVoiceEvents))`.
 */

export const VOICE_EVENT_LOG_STORAGE_KEY = "gharsaathi:voice-event-log";
const MAX_RECORDS = 1000;
const ID_FIELDS = ["event_id", "client_event_id", "item_id", "previous_item_id", "response_id", "delegation_id", "call_id", "conversation_id", "output_index", "content_index"];
const TIMING_FIELDS = ["start_ms", "end_ms", "offset_ms", "audio_start_ms", "audio_end_ms"];
const TEXT_FIELDS = ["delta", "transcript", "text"];

/**
 * @param {unknown} raw
 * @param {string} source
 * @returns {{ source: string, type: string, ids: string[], timing?: Record<string, number>, chars?: Record<string, number>, inner?: string, delegation_target?: string } | null}
 */
export function describeVoiceEvent(raw, source) {
  if (!raw || typeof raw !== "object") return null;
  const event = /** @type {Record<string, unknown>} */ (raw);
  const type = typeof event.type === "string" ? event.type.slice(0, 120) : "(no type)";
  const ids = ID_FIELDS.filter((field) => event[field] !== undefined && event[field] !== null);
  /** @type {Record<string, number>} */
  const timing = {};
  for (const field of TIMING_FIELDS) {
    if (typeof event[field] === "number" && Number.isFinite(event[field])) timing[field] = /** @type {number} */ (event[field]);
  }
  /** @type {Record<string, number>} */
  const chars = {};
  for (const field of TEXT_FIELDS) {
    if (typeof event[field] === "string") chars[field] = /** @type {string} */ (event[field]).length;
  }
  const record = { source, type, ids };
  if (Object.keys(timing).length) Object.assign(record, { timing });
  if (Object.keys(chars).length) Object.assign(record, { chars });
  const inner = event.event && typeof event.event === "object" ? /** @type {Record<string, unknown>} */ (event.event).type : undefined;
  if (typeof inner === "string") Object.assign(record, { inner: inner.slice(0, 120) });
  const delegation = event.delegation && typeof event.delegation === "object" ? /** @type {Record<string, unknown>} */ (event.delegation).target : undefined;
  if (typeof delegation === "string") Object.assign(record, { delegation_target: delegation.slice(0, 40) });
  return record;
}

/**
 * The env flag always enables it; the localStorage switch works only outside production builds.
 * @param {{ envFlag?: string, production?: boolean, storage?: { getItem(key: string): string | null } | null }} [context]
 */
export function voiceEventLogEnabled(context = {}) {
  if (context.envFlag === "1" || context.envFlag === "true") return true;
  if (context.production) return false;
  try {
    return context.storage?.getItem(VOICE_EVENT_LOG_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

/**
 * @param {{ enabled: boolean, sink?: Array<unknown>, now?: () => number, log?: (record: unknown) => void }} options
 */
export function createVoiceEventRecorder(options) {
  const sink = options.sink ?? [];
  const now = options.now ?? (() => Date.now());
  return {
    records: sink,
    /** @param {unknown} raw @param {string} source */
    record(raw, source) {
      if (!options.enabled) return;
      const description = describeVoiceEvent(raw, source);
      if (!description) return;
      const entry = { at: now(), ...description };
      sink.push(entry);
      if (sink.length > MAX_RECORDS) sink.splice(0, sink.length - MAX_RECORDS);
      options.log?.(entry);
    },
  };
}
