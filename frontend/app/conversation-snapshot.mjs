/**
 * Builds the PUT /api/conversations/current body within the backend's
 * validation limits (backend/app/conversations/schemas.py). One oversized
 * field would otherwise reject the whole save, and every later save with it.
 */

export const MAX_SUBMITTED_QUERY_CHARS = 120;
export const MAX_PRODUCTS = 100;
export const MAX_CHAT_MESSAGES = 1000;
export const MAX_CHAT_MESSAGE_CHARS = 20000;

/**
 * @template {{ content: string }} M
 * @template P
 * @param {{ submittedQuery: string, products: P[], searched: boolean, messages: M[], resultsAfterMessageId: string | null }} state
 */
export function conversationSnapshot(state) {
  const messages = state.messages
    .map((message) => ({ ...message, content: message.content.trim().slice(0, MAX_CHAT_MESSAGE_CHARS) }))
    .filter((message) => message.content)
    .slice(-MAX_CHAT_MESSAGES);
  return {
    submitted_query: state.submittedQuery.trim().slice(0, MAX_SUBMITTED_QUERY_CHARS),
    products: state.products.slice(0, MAX_PRODUCTS),
    searched: state.searched,
    messages,
    results_after_message_id: state.resultsAfterMessageId,
  };
}
