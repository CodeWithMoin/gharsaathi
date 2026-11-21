import assert from "node:assert/strict";
import test from "node:test";
import { devanagariShare, romanizeAssistantCaption, romanizeDevanagariWord } from "../../app/hinglish-romanize.mjs";

test("rewrites Devanagari words seen in real mixed assistant captions", () => {
  assert.equal(
    romanizeAssistantCaption("aur 3 ka pack डेढ़ sau rupaye ka hai"),
    "aur 3 ka pack dedh sau rupaye ka hai",
  );
  assert.equal(
    romanizeAssistantCaption("Aapne pichhli baar 330 ml ki single can ली थी"),
    "Aapne pichhli baar 330 ml ki single can li thi",
  );
  assert.equal(romanizeAssistantCaption("Zepto par ठीक है, Diet Coke add ki है।"), "Zepto par theek hai, Diet Coke add ki hai.");
});

test("falls back to Hinglish spelling with schwa deletion, not IAST", () => {
  assert.equal(romanizeDevanagariWord("करना"), "karna");
  assert.equal(romanizeDevanagariWord("समझ"), "samajh");
  assert.equal(romanizeDevanagariWord("सामान"), "saman");
  assert.equal(romanizeDevanagariWord("ज़रूर"), "zarur");
  assert.equal(romanizeDevanagariWord("२५०"), "250");
});

test("leaves a mainly Devanagari (explicit Hindi) reply and Roman text unchanged", () => {
  const hindi = "ठीक है, अब मैं हिंदी में बात करूँगी। Tata Salt 1 kg Instamart पर अट्ठाईस रुपये का है।";
  assert.ok(devanagariShare(hindi) > 0.4);
  assert.equal(romanizeAssistantCaption(hindi), hindi);
  assert.equal(romanizeAssistantCaption("Diet Coke 330 ml can add ho gaya."), "Diet Coke 330 ml can add ho gaya.");
});

test("is disabled when the profile chose Hindi", () => {
  assert.equal(romanizeAssistantCaption("can ली थी", { script: "native" }), "can ली थी");
});
