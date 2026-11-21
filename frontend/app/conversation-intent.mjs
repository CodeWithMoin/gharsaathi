function normalize(value) {
  return value
    .normalize("NFKC")
    .toLocaleLowerCase()
    .replace(/[^\p{L}\p{N}\s]/gu, " ")
    .replace(/\s+/g, " ")
    .trim();
}

const broadBrands = new Set([
  "aashirvaad", "amul", "britannia", "dabur", "fortune", "haldiram",
  "maggi", "nestle", "parle", "patanjali", "surf excel", "tata",
]);

function requestedSubject(query) {
  let value = query;
  const wrappers = [
    /^(?:please\s+)?(?:can\s+you\s+|could\s+you\s+|would\s+you\s+)?(?:order|buy|get|find|show|search)(?:\s+me)?\s+/,
    /^(?:please\s+)?(?:mujhe\s+)?(?:mangwa\s+do|mangao|manga\s+do|dila\s+do)\s+/,
    /^(?:mujhe\s+)?/,
  ];
  for (const wrapper of wrappers) value = value.replace(wrapper, "").trim();
  return value.replace(/\s+(?:please|chahiye|milega|milega kya|mangwa do|manga do|mangao|order karo|order kar do|le aao)$/, "").trim();
}

export function conversationIntent(value) {
  const query = normalize(value);
  if (["hi", "hello", "hey", "namaste", "thanks", "thank you", "shukriya"].includes(query)) return "greeting";
  if (/\b(?:what can you do|how can you help|who are you|kya kar sakte ho|kya kar sakti ho|(?:tum|aap) kya kar sakt(?:e|i) ho|(?:tum|aap) kaun ho)\b/.test(query)) return "capabilities";
  if (/\b(?:swiggy|zepto)\b/.test(query) && /\b(?:connected|connect|linked|link|access|see|dekh|dikh)\b/.test(query)) return "connection_status";
  if (broadBrands.has(requestedSubject(query))) return "ambiguous_brand";
  return null;
}

export function conversationReply(value, { swiggyConnected, zeptoConnected }) {
  const intent = conversationIntent(value);
  if (intent === "greeting") return "Namaste! Ghar ke liye kya mangwana hai? Product, brand aur pack size bol dijiye.";
  if (intent === "capabilities") return "Main Swiggy aur Zepto par groceries dhoondh aur compare kar sakti hoon, selected items ka basket prepare kar sakti hoon, aur har purchase se pehle aapki approval leti hoon.";
  if (intent === "connection_status") {
    const connected = [swiggyConnected ? "Swiggy" : null, zeptoConnected ? "Zepto" : null].filter(Boolean);
    return connected.length ? `${connected.join(" aur ")} connected ${connected.length > 1 ? "hain" : "hai"}. Main selected address par products check kar sakti hoon.` : "Abhi koi store connected nahi hai. Manage stores se Swiggy ya Zepto connect kijiye.";
  }
  if (intent === "ambiguous_brand") {
    const brand = requestedSubject(normalize(value));
    if (brand === "amul") return "Amul ka kya chahiye—milk, butter, paneer, ya kuch aur? Pack size bhi bata dijiye.";
    const displayBrand = brand.split(" ").map((part) => part.charAt(0).toLocaleUpperCase() + part.slice(1)).join(" ");
    return `${displayBrand} ka kaunsa product chahiye? Product aur pack size bata dijiye.`;
  }
  return null;
}
