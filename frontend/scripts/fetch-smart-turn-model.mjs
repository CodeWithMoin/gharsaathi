// Puts the Hinglish smart-turn model in public/models/ and verifies its SHA-256.
// The 31 MB binary is not committed (no git LFS); run `npm run fetch:smart-turn-model`.
//   SMART_TURN_MODEL_SOURCE=/path/to/model.onnx  copy a local file instead of downloading
import { createHash } from "node:crypto";
import { copyFile, mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { MODEL_SHA256 } from "../app/smart-turn-policy.mjs";

const COMMIT = "64eafecdc0dd45ba7f4319ee2571e3bb493db30f";
const SOURCE_URL = `https://raw.githubusercontent.com/CodeWithMoin/smart-turn-hinglish/${COMMIT}/runs/smart_turn_4s.onnx`;
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const MODEL_PATH = resolve(root, "public/models/smart_turn_hinglish_4s-39736bc2.onnx");

const sha256 = (bytes) => createHash("sha256").update(bytes).digest("hex");

async function existingIsValid() {
  try {
    return sha256(await readFile(MODEL_PATH)) === MODEL_SHA256;
  } catch {
    return false;
  }
}

async function main() {
  if (await existingIsValid()) {
    console.log(`Smart-turn model present and verified (${MODEL_SHA256.slice(0, 8)}).`);
    return;
  }
  await mkdir(dirname(MODEL_PATH), { recursive: true });
  const temporary = `${MODEL_PATH}.partial`;
  const local = process.env.SMART_TURN_MODEL_SOURCE;
  if (local) {
    await copyFile(local, temporary);
  } else {
    const response = await fetch(SOURCE_URL);
    if (!response.ok) throw new Error(`Download failed: HTTP ${response.status} from ${SOURCE_URL}`);
    await writeFile(temporary, Buffer.from(await response.arrayBuffer()));
  }
  const digest = sha256(await readFile(temporary));
  if (digest !== MODEL_SHA256) {
    await rm(temporary, { force: true });
    throw new Error(`Smart-turn model hash mismatch: got ${digest}, expected ${MODEL_SHA256}.`);
  }
  await rename(temporary, MODEL_PATH);
  console.log(`Smart-turn model saved to public/models and verified (${MODEL_SHA256.slice(0, 8)}).`);
}

await main();
