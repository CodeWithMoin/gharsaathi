import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";

// `./x` resolves to x.mjs before x.tsx, so a component file sharing a base name with a
// logic module silently imports the wrong one (happened with photo-attachment and inbound-calls).
test("no app/*.tsx shares its base name with an app/*.mjs module", () => {
  const dir = fileURLToPath(new URL("../../app/", import.meta.url));
  const files = readdirSync(dir);
  const mjs = new Set(files.filter((f) => f.endsWith(".mjs")).map((f) => f.slice(0, -4)));
  const clashes = files.filter((f) => f.endsWith(".tsx") && mjs.has(f.slice(0, -4)));
  assert.deepEqual(clashes, []);
});
