/**
 * Builds the PUT /api/conversations/current body within the backend's
 * validation limits (backend/app/conversations/schemas.py). One oversized
 * field would otherwise reject the whole save, and every later save with it.
 */

import { emptyTimeline, timelineSnapshot } from "./chat-timeline.mjs";
import { resultGroupsSnapshot } from "./result-groups.mjs";
import { stripNonSpeechAnnotations } from "./transcript-annotations.mjs";

export const MAX_SUBMITTED_QUERY_CHARS = 120;
export const MAX_PRODUCTS = 100;
export const MAX_CHAT_MESSAGES = 1000;
export const MAX_CHAT_MESSAGE_CHARS = 20000;

/**
 * Spoken turns are saved without non-speech annotations like "[sniff]".
 *
 * Result groups are saved in `result_groups`. When there are groups, the
 * older single-list fields (`products`, `searched`, `results_after_message_id`)
 * are filled from them too, so a server or client that predates groups still
 * shows the results, as one list.
 *
 * @template {{ content: string, modality?: string }} M
 * @template P
 * @param {{ submittedQuery: string, products: P[], searched: boolean, messages: M[], resultsAfterMessageId: string | null, timeline?: import("./chat-timeline.mjs").ChatTimeline }} state
 */
export function conversationSnapshot(state) {
  const messages = state.messages
    .map((message) => ({ ...message, content: (message.modality === "voice" ? stripNonSpeechAnnotations(message.content) : message.content).trim().slice(0, MAX_CHAT_MESSAGE_CHARS) }))
    .filter((message) => message.content)
    .slice(-MAX_CHAT_MESSAGES);
  const timeline = state.timeline ?? emptyTimeline();
  const groups = resultGroupsSnapshot(timeline.groups ?? []);
  return {
    submitted_query: state.submittedQuery.trim().slice(0, MAX_SUBMITTED_QUERY_CHARS),
    products: (groups.length ? groups.flatMap((group) => group.products) : state.products).slice(0, MAX_PRODUCTS),
    searched: groups.length ? true : state.searched,
    messages,
    results_after_message_id: groups.length ? groups[groups.length - 1].after_message_id : state.resultsAfterMessageId,
    timeline: timelineSnapshot(timeline),
    result_groups: groups,
  };
}
