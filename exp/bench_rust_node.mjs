// Benchmark tap trung Rust WASM vs lz4js vs lz4pure (de thong ke vung):
//   node exp/bench_rust_node.mjs [pkg-node-dir] [vectors-core.json]
// - (a) Giai nen TOAN BO 106 block Kusa (13,9 MB payload) bang Rust va
//   lz4js, 5 lan, lay median.
// - (b) Block 128 KiB lon nhat, warmup 50 lan roi 1000 lan, median 3 lan.
// In ra MB/s + ti le. Khong ghi file.
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { pb3DecompressBlock } from "../lz4js/index.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PKG = path.resolve(process.argv[2] || path.join(HERE, "..", "rust", "pkg-node"));
const VEC = process.argv[3]
  || "/home/hatch/workspace/rust-hotspots/bench/rust_vectors_core.json";
const wasm = await import(path.join(PKG, "meorocky_wasm.js"));
const vec = JSON.parse(fs.readFileSync(VEC, "utf8"));

function median(a) {
  const s = [...a].sort((x, y) => x - y);
  return s[Math.floor(s.length / 2)];
}
const sha = (b) => createHash("sha256").update(b).digest("hex");

const blocks = vec.lz4.blocks
  .filter((b) => b.name.startsWith("6d3223da"))
  .map((b) => ({ comp: Buffer.from(b.b64, "base64"), size: b.size, sha: b.sha }));
const totalUnc = blocks.reduce((a, b) => a + b.size, 0);
console.log("blocks Kusa: " + blocks.length + ", payload " + totalUnc + " B");

// (a) Toan bo bundle.
function runAll(decode) {
  const t0 = performance.now();
  for (const b of blocks) {
    const out = decode(b.comp, b.size);
    if (sha(out) !== b.sha) throw new Error("sha lech");
  }
  return performance.now() - t0;
}
const rustAll = [];
const jsAll = [];
for (let i = 0; i < 5; i++) {
  rustAll.push(runAll((c, s) => wasm.rust_lz4_decompress_block(c, s)));
  jsAll.push(runAll((c, s) => pb3DecompressBlock(new Uint8Array(c), s)));
}
const rustAllMed = median(rustAll);
const jsAllMed = median(jsAll);
console.log("ALL-BLOCKS rust: " + rustAll.map((x) => x.toFixed(1)).join(", ")
  + " ms -> median " + rustAllMed.toFixed(1) + "ms ("
  + (totalUnc / rustAllMed / 1e3).toFixed(1) + " MB/s)");
console.log("ALL-BLOCKS lz4js: " + jsAll.map((x) => x.toFixed(1)).join(", ")
  + " ms -> median " + jsAllMed.toFixed(1) + "ms ("
  + (totalUnc / jsAllMed / 1e3).toFixed(1) + " MB/s)");
console.log("ALL-BLOCKS ratio rust/lz4js: " + (jsAllMed / rustAllMed).toFixed(2) + "x");

// (b) Block lon nhat, warmup + 1000 lan, median 3 lan.
const big = blocks.reduce((a, b) => (b.size > a.size ? b : a), blocks[0]);
function runBig(decode, n) {
  for (let i = 0; i < 50; i++) decode(big.comp, big.size);
  const t0 = performance.now();
  for (let i = 0; i < n; i++) decode(big.comp, big.size);
  return performance.now() - t0;
}
const rustBig = [];
const jsBig = [];
for (let i = 0; i < 3; i++) {
  rustBig.push(runBig((c, s) => wasm.rust_lz4_decompress_block(c, s), 1000));
  jsBig.push(runBig((c, s) => pb3DecompressBlock(new Uint8Array(c), s), 1000));
}
const rustBigMed = median(rustBig);
const jsBigMed = median(jsBig);
console.log("BIG-BLOCK rust: " + rustBig.map((x) => x.toFixed(1)).join(", ")
  + " ms/1000 -> median " + (1000 * big.size / rustBigMed / 1e3).toFixed(1) + " MB/s");
console.log("BIG-BLOCK lz4js: " + jsBig.map((x) => x.toFixed(1)).join(", ")
  + " ms/1000 -> median " + (1000 * big.size / jsBigMed / 1e3).toFixed(1) + " MB/s");
console.log("BIG-BLOCK ratio rust/lz4js: " + (jsBigMed / rustBigMed).toFixed(2) + "x");
