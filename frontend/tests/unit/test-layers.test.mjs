import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import test from "node:test";

const root = new URL("../../", import.meta.url);
const scripts = JSON.parse(readFileSync(new URL("package.json", root), "utf8")).scripts;

test("every top-level test file belongs to exactly one layer script", () => {
  const legacy = readdirSync(new URL("tests/", root)).filter((name) => name.endsWith(".test.mjs"));
  const layered = legacy.map((name) => [
    name,
    ["test:unit", "test:module"].filter((script) => scripts[script].split(" ").includes(`tests/${name}`)),
  ]);
  const wrong = layered.filter(([, layers]) => layers.length !== 1);
  assert.deepEqual(wrong, [], "add new files to tests/unit or tests/module, or list them in exactly one script");
});

test("npm test runs the top-level files and both layer directories", () => {
  for (const glob of ["tests/*.test.mjs", "tests/unit/*.test.mjs", "tests/module/*.test.mjs"]) {
    assert.ok(scripts.test.split(" ").includes(glob), glob);
  }
});
