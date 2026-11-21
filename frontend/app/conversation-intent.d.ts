export type ConversationIntent = "ambiguous_brand" | "greeting" | "capabilities" | "connection_status";
export function conversationIntent(value: string): ConversationIntent | null;
export function conversationReply(value: string, state: { swiggyConnected: boolean; zeptoConnected: boolean }): string | null;
