/**
 * Recent-chats drawer: which saved chats to list, their titles and dates.
 */

import { stripNonSpeechAnnotations } from "./transcript-annotations.mjs";

/** The server's title when a chat has no user message (api/conversations.py). */
export const DEFAULT_CHAT_TITLE = "Shopping chat";
export const MAX_TITLE_CHARS = 42;

/**
 * @typedef {{ id: string, title: string, preview: string, updated_at: string, is_active: boolean, message_count?: number, items?: string[] }} ConversationSummary
 */

/** @param {unknown} value */
const clean = (value) => (typeof value === "string" ? stripNonSpeechAnnotations(value).replace(/\s+/g, " ").trim() : "");

/**
 * A chat with no messages. Uses `message_count` when the server sends it;
 * otherwise a chat whose title and preview are both the server default never
 * had a message.
 *
 * @param {ConversationSummary} chat
 */
export function isEmptyConversation(chat) {
  if (typeof chat.message_count === "number") return chat.message_count === 0;
  const title = clean(chat.title);
  const preview = clean(chat.preview);
  return (!title || title === DEFAULT_CHAT_TITLE) && (!preview || preview === DEFAULT_CHAT_TITLE);
}

/**
 * @template {ConversationSummary} C
 * @param {C[]} chats
 * @returns {C[]}
 */
export function visibleConversations(chats) {
  return chats.filter((chat) => !isEmptyConversation(chat));
}

/** @param {string} value */
function shorten(value) {
  if (value.length <= MAX_TITLE_CHARS) return value;
  const cut = value.slice(0, MAX_TITLE_CHARS);
  const space = cut.lastIndexOf(" ");
  return `${(space > MAX_TITLE_CHARS / 2 ? cut.slice(0, space) : cut).replace(/[\s,.;:!?–—-]+$/u, "")}…`;
}

/**
 * The items discussed when the server lists them ("Atta, Dahi"), else the
 * first user request, else the latest message, trimmed to one line.
 *
 * @param {ConversationSummary} chat
 */
export function conversationTitle(chat) {
  const items = (Array.isArray(chat.items) ? chat.items : []).map(clean).filter(Boolean);
  if (items.length) return shorten(items.join(", "));
  const title = clean(chat.title);
  const source = title && title !== DEFAULT_CHAT_TITLE ? title : clean(chat.preview) || DEFAULT_CHAT_TITLE;
  const titled = source.charAt(0).toLocaleUpperCase() + source.slice(1);
  return shorten(titled);
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** @param {Date} date */
const dayStart = (date) => new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();

/**
 * "Today, 5:28" · "Yesterday" · "21 Nov" · "21 Nov 2025", in local time.
 *
 * @param {string} value
 * @param {Date} [now]
 */
export function relativeChatDate(value, now = new Date()) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const days = Math.round((dayStart(now) - dayStart(date)) / 86_400_000);
  if (days <= 0) {
    const hours = date.getHours() % 12 || 12;
    return `${hours}:${String(date.getMinutes()).padStart(2, "0")} ${date.getHours() < 12 ? "am" : "pm"}`;
  }
  if (days === 1) return "Yesterday";
  const dayMonth = `${date.getDate()} ${MONTHS[date.getMonth()]}`;
  return date.getFullYear() === now.getFullYear() ? dayMonth : `${dayMonth} ${date.getFullYear()}`;
}

/**
 * @template {ConversationSummary} C
 * @param {C[]} chats
 * @param {string} search
 * @returns {C[]}
 */
export function searchConversations(chats, search) {
  const needle = search.trim().toLocaleLowerCase();
  if (!needle) return chats;
  return chats.filter((chat) => [conversationTitle(chat), chat.title, chat.preview, ...(chat.items ?? [])].some((field) => typeof field === "string" && field.toLocaleLowerCase().includes(needle)));
}
