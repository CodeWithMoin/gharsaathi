export type ConversationSummary = { id: string; title: string; preview: string; updated_at: string; is_active: boolean; message_count?: number; items?: string[] };

export const DEFAULT_CHAT_TITLE: string;
export const MAX_TITLE_CHARS: number;
export function isEmptyConversation(chat: ConversationSummary): boolean;
export function visibleConversations<C extends ConversationSummary>(chats: C[]): C[];
export function conversationTitle(chat: ConversationSummary): string;
export function relativeChatDate(value: string, now?: Date): string;
export function searchConversations<C extends ConversationSummary>(chats: C[], search: string): C[];
