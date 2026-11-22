/**
 * Display-side safety net for assistant voice captions.
 *
 * GPT-Live's `session.output_transcript.delta` is a transcript of the spoken
 * audio, not the model's text, and the Live session has no setting for its
 * script. Hindi words the assistant speaks inside a Roman Hinglish sentence can
 * therefore come back in Devanagari ("aur 3 ka pack डेढ़ sau rupaye"). This
 * rewrites those words in everyday Hinglish spelling (not IAST).
 *
 * Only for assistant text, only when the user did not choose Hindi, and only
 * when the text is mostly Roman: a reply that is mainly Devanagari (the user
 * asked for Hindi in this session) is left as it is.
 */

/** Text with at most this share of Devanagari characters counts as Roman Hinglish. */
export const MAX_DEVANAGARI_SHARE = 0.4;

const WORDS = new Map(Object.entries({
  "है": "hai", "हैं": "hain", "हूँ": "hoon", "हूं": "hoon", "हो": "ho", "था": "tha", "थी": "thi", "थे": "the",
  "ली": "li", "लिया": "liya", "लिए": "liye", "ले": "le", "दी": "di", "दिया": "diya", "दे": "de",
  "की": "ki", "का": "ka", "के": "ke", "को": "ko", "में": "mein", "मैं": "main", "मैंने": "maine", "से": "se",
  "पर": "par", "और": "aur", "या": "ya", "नहीं": "nahi", "नही": "nahi", "हाँ": "haan", "हां": "haan", "जी": "ji",
  "क्या": "kya", "कि": "ki", "भी": "bhi", "तो": "to", "ही": "hi", "यह": "yeh", "ये": "ye", "वह": "woh", "वो": "wo",
  "एक": "ek", "दो": "do", "तीन": "teen", "चार": "chaar", "पांच": "paanch", "पाँच": "paanch", "छह": "chhah",
  "सौ": "sau", "हज़ार": "hazaar", "हजार": "hazaar", "डेढ़": "dedh", "डेढ": "dedh", "ढाई": "dhai", "सवा": "sava",
  "साढ़े": "saadhe", "रुपये": "rupaye", "रुपए": "rupaye", "रुपया": "rupaya",
  "आप": "aap", "आपने": "aapne", "आपको": "aapko", "आपका": "aapka", "ठीक": "theek", "कर": "kar", "करूँ": "karun",
  "करूं": "karun", "करना": "karna", "करती": "karti", "चाहिए": "chahiye", "वाला": "wala", "वाली": "wali",
  "वाले": "wale", "पिछली": "pichhli", "पिछले": "pichhle", "बार": "baar", "लेकिन": "lekin", "अभी": "abhi",
  "रही": "rahi", "रहा": "raha", "सकती": "sakti", "कितने": "kitne", "कितना": "kitna", "वही": "wahi",
  "बिल": "bill", "ऑर्डर": "order", "स्क्रीन": "screen", "पैक": "pack", "कैन": "can", "सिंगल": "single",
}));

const CONSONANTS = new Map(Object.entries({
  "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n", "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "n",
  "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n", "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
  "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m", "य": "y", "र": "r", "ल": "l", "व": "v", "श": "sh",
  "ष": "sh", "स": "s", "ह": "h", "क़": "q", "ख़": "kh", "ग़": "gh", "ज़": "z", "ड़": "d", "ढ़": "dh", "फ़": "f",
}));
const NUKTA_FORMS = new Map(Object.entries({ "क": "क़", "ख": "ख़", "ग": "ग़", "ज": "ज़", "ड": "ड़", "ढ": "ढ़", "फ": "फ़" }));
const VOWELS = new Map(Object.entries({
  "अ": "a", "आ": "aa", "इ": "i", "ई": "i", "उ": "u", "ऊ": "oo", "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au", "ऋ": "ri", "ऑ": "o", "ऍ": "e",
}));
const SIGNS = new Map(Object.entries({
  "ा": "a", "ि": "i", "ी": "i", "ु": "u", "ू": "u", "े": "e", "ै": "ai", "ो": "o", "ौ": "au", "ृ": "ri", "ॉ": "o", "ॅ": "e",
}));
const NUKTA = "\u093C";
const VIRAMA = "\u094D";
const NASALS = new Set(["\u0901", "\u0902"]); // chandrabindu, anusvara
const VISARGA = "\u0903";
const DIGITS = "०१२३४५६७८९";
const DEVANAGARI_RUN = /[\u0900-\u097F]+/g;

/**
 * @typedef {{ text: string, vowel: string, inherent: boolean, consonant: boolean }} Akshara
 */

/** @param {string} word */
function aksharas(word) {
  /** @type {Akshara[]} */
  const parts = [];
  const chars = [...word];
  for (let index = 0; index < chars.length; index += 1) {
    let char = chars[index];
    if (chars[index + 1] === NUKTA && NUKTA_FORMS.has(char)) {
      char = /** @type {string} */ (NUKTA_FORMS.get(char));
      index += 1;
    }
    const last = parts[parts.length - 1];
    if (CONSONANTS.has(char)) {
      parts.push({ text: /** @type {string} */ (CONSONANTS.get(char)), vowel: "a", inherent: true, consonant: true });
    } else if (VOWELS.has(char)) {
      parts.push({ text: "", vowel: /** @type {string} */ (VOWELS.get(char)), inherent: false, consonant: false });
    } else if (SIGNS.has(char) && last?.consonant) {
      last.vowel = /** @type {string} */ (SIGNS.get(char));
      last.inherent = false;
    } else if (char === VIRAMA && last?.consonant) {
      last.vowel = "";
      last.inherent = false;
    } else if (NASALS.has(char) && last) {
      last.vowel += "n";
      last.inherent = false;
    } else if (char === VISARGA && last) {
      last.vowel += "h";
    } else if (DIGITS.includes(char)) {
      parts.push({ text: String(DIGITS.indexOf(char)), vowel: "", inherent: false, consonant: false });
    } else if (char === "\u0964" || char === "\u0965") {
      parts.push({ text: ".", vowel: "", inherent: false, consonant: false });
    }
  }
  return parts;
}

/**
 * Hinglish-style spelling of one Devanagari word, with Hindi schwa deletion.
 * @param {string} word
 */
export function romanizeDevanagariWord(word) {
  const known = WORDS.get(word);
  if (known) return known;
  const parts = aksharas(word);
  const letters = parts.filter((part) => part.consonant || part.vowel);
  if (letters.length > 1) {
    const final = parts[parts.length - 1];
    if (final.consonant && final.inherent) final.vowel = "";
  }
  for (let index = 1; index < parts.length - 1; index += 1) {
    const [before, current, after] = [parts[index - 1], parts[index], parts[index + 1]];
    if (current.consonant && current.inherent && before.vowel && after.consonant && after.vowel) {
      current.vowel = "";
      index += 1;
    }
  }
  return parts.map((part) => part.text + part.vowel).join("");
}

/** @param {string} text */
export function devanagariShare(text) {
  const devanagari = (text.match(/[\u0900-\u097F]/g) ?? []).length;
  const latin = (text.match(/[A-Za-z]/g) ?? []).length;
  return devanagari + latin ? devanagari / (devanagari + latin) : 0;
}

/**
 * Rewrites Devanagari words in a mostly-Roman Hinglish assistant caption.
 * @param {string} text
 * @param {{ script?: "roman" | "native" }} [options] `native` (Hindi chosen in the profile) disables it.
 */
export function romanizeAssistantCaption(text, options = {}) {
  if (options.script === "native" || !text || !/[\u0900-\u097F]/.test(text)) return text;
  if (devanagariShare(text) > MAX_DEVANAGARI_SHARE) return text;
  return text.replace(DEVANAGARI_RUN, (run) => {
    const danda = /[\u0964\u0965]$/.test(run);
    const word = run.replace(/[\u0964\u0965]+$/, "");
    return (word ? romanizeDevanagariWord(word) : "") + (danda ? "." : "");
  });
}
