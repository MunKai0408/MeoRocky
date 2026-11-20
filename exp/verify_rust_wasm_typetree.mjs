// Kiem chung WASM Rust (typetree) — chay bang Node, doc streaming:
//   node exp/verify_rust_wasm_typetree.mjs [pkg-node-dir] [vectors.jsonl]
// - Moi MB object that (Kusa + Knightica + fixture .assets): value JSON
//   + end phai khop Python (read_typetree_value) 100%, ke ca thu tu
//   field, so am, bytes ($bytes base64).
// - Benchmark: object lon nhat (LocalizationTextData) + tong 1805 object,
//   so voi Python (do rieng bang bench_typetree_py.py).
// Thoat 0 khi tat ca PASS, 1 khi co FAIL. Khong ghi file.
import fs from "node:fs";
import path from "node:path";
import readline from "node:readline";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PKG = path.resolve(process.argv[2] || path.join(HERE, "..", "rust", "pkg-node"));
const VEC = process.argv[3]
  || "/home/hatch/workspace/rust-hotspots/bench/rust_vectors_typetree.jsonl";
const wasm = await import(path.join(PKG, "meorocky_wasm.js"));

let failures = 0;
let checked = 0;
let biggest = null;
let biggestMs = 0;
let totalMs = 0;

// So sanh sau (deep) co thu tu key — JSON object giu thu tu chen.
function deepEqual(a, b) {
  if (a === b) return true;
  if (a === null || b === null) return a === b;
  if (typeof a !== typeof b) return false;
  if (Array.isArray(a) || Array.isArray(b)) {
    if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return false;
    for (let i = 0; i < a.length; i++) if (!deepEqual(a[i], b[i])) return false;
    return true;
  }
  if (typeof a === "object") {
    const ka = Object.keys(a);
    const kb = Object.keys(b);
    if (ka.length !== kb.length) return false;
    for (let i = 0; i < ka.length; i++) {
      if (ka[i] !== kb[i]) return false;
      if (!deepEqual(a[ka[i]], b[kb[i]])) return false;
    }
    return true;
  }
  return false;
}

function firstDiff(a, b, path) {
  if (a === b) return null;
  if (a === null || b === null || typeof a !== typeof b) {
    return path + ": rust=" + JSON.stringify(a)?.slice(0, 80)
      + " py=" + JSON.stringify(b)?.slice(0, 80);
  }
  if (Array.isArray(a) || Array.isArray(b)) {
    if (!Array.isArray(a) || !Array.isArray(b)) return path + ": kieu lech";
    if (a.length !== b.length) return path + ": do dai " + a.length + " vs " + b.length;
    for (let i = 0; i < a.length; i++) {
      const d = firstDiff(a[i], b[i], path + "[" + i + "]");
      if (d) return d;
    }
    return null;
  }
  if (typeof a === "object") {
    const ka = Object.keys(a);
    const kb = Object.keys(b);
    if (ka.length !== kb.length) return path + ": so key " + ka.length + " vs " + kb.length;
    for (let i = 0; i < ka.length; i++) {
      if (ka[i] !== kb[i]) return path + ": key thu " + i + " " + ka[i] + " vs " + kb[i];
      const d = firstDiff(a[ka[i]], b[kb[i]], path + "." + ka[i]);
      if (d) return d;
    }
    return null;
  }
  return path + ": " + JSON.stringify(a) + " vs " + JSON.stringify(b);
}

const rl = readline.createInterface({
  input: fs.createReadStream(VEC, { encoding: "utf8" }),
  crlfDelay: Infinity,
});
for await (const line of rl) {
  if (!line.trim()) continue;
  const o = JSON.parse(line);
  const raw = Buffer.from(o.b64, "base64");
  const nodeJson = JSON.stringify(o.node);
  const t0 = performance.now();
  let outJson;
  try {
    outJson = wasm.rust_parse_typetree_value(nodeJson, raw, 0, o.little);
  } catch (e) {
    failures++;
    console.log("FAIL typetree:" + o.name + " — nem: " + String(e).slice(0, 120));
    continue;
  }
  const dt = performance.now() - t0;
  totalMs += dt;
  if (raw.length > (biggest?.rawLen || 0)) {
    biggest = { name: o.name, rawLen: raw.length };
    biggestMs = dt;
  }
  const got = JSON.parse(outJson);
  checked++;
  if (got.end !== o.end) {
    failures++;
    if (failures < 6) {
      console.log("FAIL typetree:" + o.name + " — end " + got.end + " vs " + o.end);
    }
    continue;
  }
  if (!deepEqual(got.value, o.value)) {
    failures++;
    if (failures < 6) {
      console.log("FAIL typetree:" + o.name + " — "
        + (firstDiff(got.value, o.value, "$") || "lech khong ro"));
    }
  }
}

console.log("typetree: " + checked + " object, " + failures + " FAIL");
console.log("bench:typetree-total " + totalMs.toFixed(1) + "ms / " + checked
  + " object (" + (checked / totalMs * 1e3).toFixed(0) + " obj/s)");
if (biggest) {
  console.log("bench:typetree-biggest " + biggest.name + " raw=" + biggest.rawLen
    + "B: " + biggestMs.toFixed(1) + "ms");
}
console.log(failures === 0 ? "ALL PASS" : failures + " FAIL");
process.exit(failures === 0 ? 0 : 1);
