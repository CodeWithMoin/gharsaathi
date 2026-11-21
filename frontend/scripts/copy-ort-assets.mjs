import { copyFile, mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const source = resolve(root, "node_modules/onnxruntime-web/dist");
const destination = resolve(root, "public/ort");
const assets = [
  "ort-wasm-simd-threaded.mjs",
  "ort-wasm-simd-threaded.wasm",
];

await mkdir(destination, { recursive: true });
await Promise.all(
  assets.map((asset) => copyFile(resolve(source, asset), resolve(destination, asset))),
);

console.log(`Copied ${assets.length} ONNX Runtime browser assets to public/ort.`);
