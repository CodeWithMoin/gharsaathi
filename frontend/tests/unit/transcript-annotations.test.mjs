import assert from "node:assert/strict";
import test from "node:test";
import { stripNonSpeechAnnotations } from "../../app/transcript-annotations.mjs";

test("removes standalone non-speech tags", () => {
  assert.equal(stripNonSpeechAnnotations("[sniff] Main to bas check kar rahi thi."), "Main to bas check kar rahi thi.");
  assert.equal(stripNonSpeechAnnotations("Haan [laughs], bilkul."), "Haan, bilkul.");
  assert.equal(stripNonSpeechAnnotations("Ek second [clears throat] dekhti hoon"), "Ek second dekhti hoon");
  assert.equal(stripNonSpeechAnnotations("Theek hai (inaudible) phir"), "Theek hai phir");
  assert.equal(stripNonSpeechAnnotations("[Laughs] Accha"), "Accha");
  assert.equal(stripNonSpeechAnnotations("[breath]"), "");
});

test("keeps brackets that belong to product text", () => {
  for (const text of [
    "Pack [2 x 100g] add karun?",
    "Dettol [Original] soap",
    "Maggi (Pack of 2) mil gaya",
    "Coke (500 ml) chahiye",
    "Tea[green] blend",
    "Amul (Salted) butter",
  ]) assert.equal(stripNonSpeechAnnotations(text), text);
});

test("leaves ordinary text untouched", () => {
  assert.equal(stripNonSpeechAnnotations("  Atta 5 kg  "), "  Atta 5 kg  ");
});
