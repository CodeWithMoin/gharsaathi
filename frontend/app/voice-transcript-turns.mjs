/**
 * Groups normalised transcript events (see `mapRealtimeTranscriptEvent`) into
 * chat bubbles. The active turn is tracked per role so that user and assistant
 * speech arriving interleaved (barge-in) keeps appending to one bubble each.
 *
 * GPT-Live transcript deltas have no item id or done event, so a role's turn
 * also closes when:
 * - that role is silent for `gapMs` on the session timeline (`start_ms`/`end_ms`),
 *   or for `arrivalGapMs` of wall-clock time when an event has no timing;
 * - the other role spoke for at least `interjectionMs` during that silence, so a
 *   short backchannel ("haan") never splits an ongoing sentence;
 * - a boundary event arrives (`boundary()`), or the other role's turn closes
 *   after this role had already gone quiet.
 *
 * @typedef {"user" | "assistant"} TranscriptRole
 * @typedef {{ role: TranscriptRole, operation: "append" | "replace" | "finish", text: string, sourceId?: string, startMs?: number, endMs?: number }} TranscriptEvent
 * @typedef {{ role: TranscriptRole | "both", operation: "start" | "end" }} TranscriptBoundary
 * @typedef {{ id: string, role: TranscriptRole, content: string }} TranscriptTurn
 * @typedef {{ start: number, end: number, clock: "timeline" | "arrival" }} TurnSpan
 */

export const TURN_GAP_MS = 1800;
export const ARRIVAL_GAP_MS = 2500;
export const INTERJECTION_MS = 900;
/** The assistant often pauses mid-reply while a tool runs; only the user speaking (or a boundary) ends her bubble. */
export const ASSISTANT_GAP_MS = 60000;
export const TURN_TAKE_MS = 700;

/**
 * @param {{ makeId?: (sequence: number) => string, now?: () => number, gapMs?: number, arrivalGapMs?: number, interjectionMs?: number, assistantGapMs?: number }} [options]
 */
export function createTranscriptTurnTracker(options = {}) {
  const makeId = options.makeId ?? ((sequence) => `voice-${Date.now().toString(36)}-${sequence}`);
  const now = options.now ?? (() => Date.now());
  const gapMs = options.gapMs ?? TURN_GAP_MS;
  const arrivalGapMs = options.arrivalGapMs ?? ARRIVAL_GAP_MS;
  const interjectionMs = options.interjectionMs ?? INTERJECTION_MS;
  const assistantGapMs = options.assistantGapMs ?? ASSISTANT_GAP_MS;
  /** @type {Map<string, TranscriptTurn>} */
  const turns = new Map();
  /** @type {Map<string, string>} provider source key -> turn key */
  const sources = new Map();
  /** @type {Set<string>} turn keys already bound to a provider source id */
  const boundKeys = new Set();
  /** @type {Record<TranscriptRole, string | null>} */
  const active = { user: null, assistant: null };
  /** @type {Record<TranscriptRole, TurnSpan | null>} timing of each role's latest turn, open or closed */
  const spans = { user: null, assistant: null };
  let localKeys = 0;
  let bubbles = 0;

  /** @param {TranscriptRole} role */
  const other = (role) => (role === "user" ? "assistant" : "user");

  /**
   * @param {TranscriptRole} role
   * @param {boolean} [cascade] also close the other role if it was quiet throughout this turn
   */
  function close(role, cascade = true) {
    if (!active[role]) return;
    active[role] = null;
    const span = spans[role];
    const otherRole = other(role);
    const otherSpan = spans[otherRole];
    if (!cascade || !span || !active[otherRole] || !otherSpan || otherSpan.clock !== span.clock) return;
    const quietThroughout = span.clock === "timeline" ? otherSpan.end <= span.start : otherSpan.end < span.start;
    if (quietThroughout) close(otherRole, false);
  }

  /** @param {TranscriptRole} role @param {number} at @param {TurnSpan["clock"]} clock */
  function shouldSplit(role, at, clock) {
    const span = spans[role];
    if (!active[role] || !span || span.clock !== clock) return false;
    const silenceLimit = role === "assistant" ? assistantGapMs : clock === "timeline" ? gapMs : arrivalGapMs;
    if (at - span.end >= silenceLimit) return true;
    const otherSpan = spans[other(role)];
    if (!otherSpan || otherSpan.clock !== clock || otherSpan.start < span.end) return false;
    if (otherSpan.end - otherSpan.start >= interjectionMs) return true;
    // A short reply that starts after the assistant went quiet is a real turn ("sabse chhota"),
    // unlike a backchannel spoken while she is still talking.
    return role === "assistant" && otherSpan.start - span.end >= TURN_TAKE_MS;
  }

  /** @param {TranscriptRole} role @param {string | undefined} sourceId */
  function resolveKey(role, sourceId) {
    if (sourceId) {
      const sourceKey = `${role}:${sourceId}`;
      const known = sources.get(sourceKey);
      if (known) return known;
      const current = active[role];
      // A turn that started without an id adopts the first id that shows up for it.
      const key = current && !boundKeys.has(current) ? current : sourceKey;
      sources.set(sourceKey, key);
      boundKeys.add(key);
      return key;
    }
    if (active[role]) return active[role];
    localKeys += 1;
    return `${role}:local-${localKeys}`;
  }

  /** @param {TranscriptRole} role @param {string} key @param {number} start @param {number} end @param {TurnSpan["clock"]} clock */
  function track(role, key, start, end, clock) {
    const span = spans[role];
    if (active[role] === key && span && span.clock === clock) span.end = Math.max(span.end, end);
    else spans[role] = { start, end, clock };
  }

  return {
    /**
     * Applies one event and returns the updated bubble, or null when nothing
     * should be rendered (finish markers, empty text).
     * @param {TranscriptEvent} event
     * @returns {TranscriptTurn | null}
     */
    apply(event) {
      const { role } = event;
      const clock = event.startMs !== undefined ? "timeline" : "arrival";
      const start = event.startMs ?? now();
      const end = Math.max(start, event.endMs ?? start);
      const current = active[role];
      // Provider ids are authoritative; timing heuristics apply only to unbound turns.
      if (!event.sourceId && current && !boundKeys.has(current) && shouldSplit(role, start, clock)) close(role);
      const key = resolveKey(role, event.sourceId);
      if (event.operation === "finish") {
        if (active[role] === key) close(role);
        return null;
      }
      const previous = turns.get(key);
      const content = event.operation === "replace" ? event.text : `${previous?.content ?? ""}${event.text}`;
      if (event.operation === "append" && event.text) track(role, key, start, end, clock);
      active[role] = key;
      // A completed transcript closes the turn; the next event for this role starts a new bubble.
      if (event.operation === "replace") close(role);
      if (!content.trim()) return null;
      if (!previous) bubbles += 1;
      const turn = { id: previous?.id ?? makeId(bubbles), role, content };
      turns.set(key, turn);
      return { ...turn, content: content.trim() };
    },
    /**
     * Applies a speech/response lifecycle boundary. `start` and `end` both
     * close the role's open bubble; the next fragment opens a new one.
     * @param {TranscriptBoundary} boundary
     */
    boundary(boundary) {
      if (boundary.role === "both") {
        close("user", false);
        close("assistant", false);
        return;
      }
      close(boundary.role);
    },
    /** Forget in-progress turns, e.g. when a new voice session starts. */
    reset() {
      active.user = null;
      active.assistant = null;
      spans.user = null;
      spans.assistant = null;
    },
  };
}
