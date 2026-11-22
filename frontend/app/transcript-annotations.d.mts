export function stripNonSpeechAnnotations(text: string): string;
export function voiceTurnDisplayText(turn: { role: "user" | "assistant"; content: string; captionScript?: "roman" | "native" }): string;
