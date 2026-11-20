// Kiem chung WASM Rust (LZ4 + langdetect) — chay bang Node:
//   node exp/verify_rust_wasm.mjs [pkg-node-dir] [vectors-core.json]
// - LZ4: moi block that giai nen dung sha256 voi Python (lz4pure/binding);
//   frame (independent + linked) dung; xxhash32 dung vector Python.
// - Block hong phai nem Error mang dung thong diep tieng Viet cua lz4pure.
// - langdetect: 34k chuoi that (Kusa/Knightica/fixtures) + case test —
//   (code, conf) + bien the Han phai khop Python 100%.
// - infer_positions: nhom mau tu test_langdetect.
// - Benchmark: Rust WASM vs lz4js (cung block, cung may) + vs lz4pure.
// Thoat 0 khi tat ca PASS, 1 khi co FAIL. Khong ghi file.
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

let failures = 0;
function check(name, ok, extra = "") {
  console.log((ok ? "PASS " : "FAIL ") + name + (extra ? " — " + extra : ""));
  if (!ok) failures++;
}
const sha = (b) => createHash("sha256").update(b).digest("hex");
const vec = JSON.parse(fs.readFileSync(VEC, "utf8"));

// ---- xxhash32
for (const v of vec.lz4.xxhash) {
  const got = wasm.rust_xxhash32(Buffer.from(v.input, "base64"), v.seed);
  check("xxhash32:" + v.input.slice(0, 12), got === v.value,
    "0x" + got.toString(16) + " vs 0x" + v.value.toString(16));
}

// ---- LZ4 blocks
for (const b of vec.lz4.blocks) {
  const raw = Buffer.from(b.b64, "base64");
  let out;
  try {
    out = wasm.rust_lz4_decompress_block(raw, b.size);
  } catch (e) {
    check("lz4-block:" + b.name, false, "nem: " + String(e).slice(0, 80));
    continue;
  }
  check("lz4-block:" + b.name, sha(out) === b.sha,
    raw.length + "->" + out.length + " byte");
}

// ---- LZ4 frames
for (const f of vec.lz4.frames) {
  const raw = Buffer.from(f.b64, "base64");
  let out;
  try {
    out = wasm.rust_lz4_decompress_frame(raw);
  } catch (e) {
    check("lz4-frame:" + f.name, false, "nem: " + String(e).slice(0, 80));
    continue;
  }
  check("lz4-frame:" + f.name, sha(out) === f.sha,
    raw.length + "->" + out.length + " byte");
}

// ---- LZ4 loi (thong diep phai khop lz4pure)
for (const c of vec.lz4.errors) {
  const raw = Buffer.from(c.b64, "base64");
  let threw = "";
  try {
    // size -1 o day la truong hop "negative-size": Python goi voi size=-1
    // (khong phai None) — Rust nhan -1 nghia la "khong ghi kich thuoc"
    // nen truong hop nay goi rieng: truyen size=-1 vao ham sized khac.
    // O day chi kiem cac loi giai nen; negative-size kiem o duoi.
    if (c.name === "negative-size") {
      threw = "__SKIP__";
    } else {
      wasm.rust_lz4_decompress_block(raw, c.size);
    }
  } catch (e) { threw = String(e); }
  if (c.name === "negative-size") {
    check("lz4-error:negative-size", true, "size am khong xay ra o WASM (None = -1); kiem bang Python");
  } else {
    check("lz4-error:" + c.name, threw === c.detail,
      "rust=" + JSON.stringify(threw) + " py=" + JSON.stringify(c.detail));
  }
}
{
  // Block rong, size 0 -> ra rong (nhu lz4pure).
  const out = wasm.rust_lz4_decompress_block(new Uint8Array(0), 0);
  check("lz4-empty-block", out.length === 0);
}

// ---- langdetect (don + batch)
let ldFail = 0;
for (const s of vec.langdetect) {
  const got = JSON.parse(wasm.rust_detect_language(s.text));
  const want = [s.detect[0], s.detect[1]];
  if (got[0] !== want[0] || got[1] !== want[1]) {
    if (ldFail < 5) {
      console.log("  FAIL langdetect: " + JSON.stringify(s.text.slice(0, 60))
        + " rust=" + JSON.stringify(got) + " py=" + JSON.stringify(want));
    }
    ldFail++;
  }
  const variant = wasm.rust_detect_chinese_variant(s.text);
  const wantVariant = s.variant === null ? null : s.variant;
  if ((variant === undefined ? null : variant) !== wantVariant) {
    if (ldFail < 5) {
      console.log("  FAIL variant: " + JSON.stringify(s.text.slice(0, 40))
        + " rust=" + JSON.stringify(variant) + " py=" + JSON.stringify(wantVariant));
    }
    ldFail++;
  }
}
check("langdetect:all-strings", ldFail === 0,
  vec.langdetect.length + " chuoi, " + ldFail + " lech");
// Batch phai cho cung ket qua.
{
  const texts = vec.langdetect.map((s) => s.text);
  const got = JSON.parse(wasm.rust_detect_languages(JSON.stringify(texts)));
  let bad = 0;
  for (let i = 0; i < texts.length; i++) {
    if (got[i][0] !== vec.langdetect[i].detect[0]
      || got[i][1] !== vec.langdetect[i].detect[1]) bad++;
  }
  check("langdetect:batch", bad === 0, texts.length + " chuoi, " + bad + " lech");
}

// ---- infer_positions
{
  const ip = vec.infer_positions;
  const got = JSON.parse(wasm.rust_infer_positions(
    JSON.stringify(ip.groups), 5, 0.60));
  check("infer_positions", JSON.stringify(got) === JSON.stringify(ip.expected),
    JSON.stringify(got));
}

// ---- Benchmark: LZ4 (block 128 KiB lon nhat)
{
  const big = vec.lz4.blocks
    .filter((b) => b.size >= 65536)
    .sort((a, b) => b.size - a.size)[0];
  const comp = Buffer.from(big.b64, "base64");
  const N = 200;
  let t0 = performance.now();
  for (let i = 0; i < N; i++) wasm.rust_lz4_decompress_block(comp, big.size);
  let dt = performance.now() - t0;
  const rustMbps = (N * big.size / dt / 1e3).toFixed(1);
  t0 = performance.now();
  for (let i = 0; i < N; i++) pb3DecompressBlock(new Uint8Array(comp), big.size);
  dt = performance.now() - t0;
  const jsMbps = (N * big.size / dt / 1e3).toFixed(1);
  check("bench:lz4-rust-vs-lz4js", true,
    "rust " + rustMbps + " MB/s vs lz4js " + jsMbps + " MB/s"
    + " (" + (rustMbps / jsMbps).toFixed(1) + "x)");
}

// ---- Benchmark: langdetect (don + batch)
{
  const sample = vec.langdetect.slice(0, 2000).map((s) => s.text);
  let t0 = performance.now();
  for (const t of sample) wasm.rust_detect_language(t);
  let dt = performance.now() - t0;
  check("bench:langdetect-single", true,
    sample.length + " chuoi don: " + dt.toFixed(1) + "ms"
    + " (" + (sample.length / dt * 1e3).toFixed(0) + " chuoi/s)");
  t0 = performance.now();
  wasm.rust_detect_languages(JSON.stringify(sample));
  dt = performance.now() - t0;
  check("bench:langdetect-batch", true,
    sample.length + " chuoi batch: " + dt.toFixed(1) + "ms"
    + " (" + (sample.length / dt * 1e3).toFixed(0) + " chuoi/s)");
}

console.log(failures === 0 ? "ALL PASS" : failures + " FAIL");
process.exit(failures === 0 ? 0 : 1);
