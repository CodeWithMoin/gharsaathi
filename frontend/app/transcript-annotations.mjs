/**
 * Removes non-speech annotations that speech models write into transcripts,
 * such as "[sniff]", "[clears throat]" or "(inaudible)".
 *
 * Only standalone tokens are removed. Square brackets must hold one to three
 * letter-only words that are lowercase or a known annotation; parentheses must
 * hold a known annotation. Product text like "[2 x 100g]", "Dettol [Original]"
 * or "(Pack of 2)" is kept.
 */

const KNOWN = new Set([
  "applause", "breath", "breathing", "breathes", "chuckle", "chuckles", "clears throat", "cough", "coughs",
  "crosstalk", "gasp", "gasps", "inaudible", "inhales", "exhales", "laugh", "laughs", "laughter", "music",
  "noise", "pause", "silence", "sigh", "sighs", "sneeze", "sniff", "sniffs", "static", "unintelligible",
]);

const SQUARE = /(^|\s)\[([\p{L}'-]+(?: [\p{L}'-]+){0,2})\](?=$|[\s.,!?;:…।])/gu;
const ROUND = /(^|\s)\(([\p{L}'-]+(?: [\p{L}'-]+){0,2})\)(?=$|[\s.,!?;:…।])/gu;

/** @param {string} words */
function known(words) {
  return KNOWN.has(words.toLocaleLowerCase());
}

/**
 * @param {string} text
 * @returns {string}
 */
export function stripNonSpeechAnnotations(text) {
  if (!text.includes("[") && !text.includes("(")) return text;
  const stripped = text
    .replace(SQUARE, (match, lead, words) => (words === words.toLocaleLowerCase() || known(words) ? lead : match))
    .replace(ROUND, (match, lead, words) => (known(words) ? lead : match));
  if (stripped === text) return text;
  return stripped
    .replace(/[ \t]{2,}/g, " ")
    .replace(/ +([.,!?;:…।])/g, "$1")
    .replace(/^[ \t]*[.,;:]+[ \t]*/, "")
    .trim();
}
