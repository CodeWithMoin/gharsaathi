/**
 * Groups normalised transcript events (see `mapRealtimeTranscriptEvent`) into
 * chat bubbles. The active turn is tracked per role so that user and assistant
 * speech arriving interleaved (barge-in) keeps appending to one bubble each.
 *
 * @typedef {"user" | "assistant"} TranscriptRole
 * @typedef {{ role: TranscriptRole, operation: "append" | "replace" | "finish", text: string, sourceId?: string }} TranscriptEvent
 * @typedef {{ id: string, role: TranscriptRole, content: string }} TranscriptTurn
 */

/**
 * @param {{ makeId?: (sequence: number) => string }} [options]
 */
export function createTranscriptTurnTracker(options = {}) {
  const makeId = options.makeId ?? ((sequence) => `voice-${Date.now().toString(36)}-${sequence}`);
  /** @type {Map<string, TranscriptTurn>} */
  const turns = new Map();
  /** @type {Map<string, string>} provider source key -> turn key */
  const sources = new Map();
  /** @type {Set<string>} turn keys already bound to a provider source id */
  const boundKeys = new Set();
  /** @type {Record<TranscriptRole, string | null>} */
  const active = { user: null, assistant: null };
  let localKeys = 0;
  let bubbles = 0;

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

  return {
    /**
     * Applies one event and returns the updated bubble, or null when nothing
     * should be rendered (finish markers, empty text).
     * @param {TranscriptEvent} event
     * @returns {TranscriptTurn | null}
     */
    apply(event) {
      const { role } = event;
      const key = resolveKey(role, event.sourceId);
      if (event.operation === "finish") {
        if (active[role] === key) active[role] = null;
        return null;
      }
      const previous = turns.get(key);
      const content = event.operation === "replace" ? event.text : `${previous?.content ?? ""}${event.text}`;
      // A completed transcript closes the turn; the next event for this role starts a new bubble.
      active[role] = event.operation === "replace" ? null : key;
      if (!content.trim()) return null;
      if (!previous) bubbles += 1;
      const turn = { id: previous?.id ?? makeId(bubbles), role, content };
      turns.set(key, turn);
      return { ...turn, content: content.trim() };
    },
    /** Forget in-progress turns, e.g. when a new voice session starts. */
    reset() {
      active.user = null;
      active.assistant = null;
    },
  };
}
