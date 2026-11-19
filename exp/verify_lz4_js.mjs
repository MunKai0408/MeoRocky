// Kiem chung lz4js vendored (muc C1, toc do) — chay bang Node, khong can
// trinh duyet, khong can fixture:
//   node exp/verify_lz4_js.mjs [block.bin block.json ...]
// - Khoi nho nhu dung san (block LZ4 that, base64) giai nen dung.
// - Moi cap (block.bin, block.json {unc, sha}) truyen them: giai nen
//   bang pb3DecompressBlock, so sha256 voi Python (binding/lz4pure).
// - Block hong (cat cut, offset 0, offset vuot, sai size) phai nem
//   Error LZ4_CORRUPT (decompressBlock goc khong nem — wrapper them).
// - Benchmark: 200 lan block 128 KiB (neu co file) hoac khoi nho x20000.
// Thoat 0 khi tat ca PASS, 1 khi co FAIL. Khong ghi file.
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { pb3DecompressBlock } from "../lz4js/index.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
let failures = 0;
function check(name, ok, extra = "") {
  console.log((ok ? "PASS " : "FAIL ") + name + (extra ? " — " + extra : ""));
  if (!ok) failures++;
}
const sha = (b) => createHash("sha256").update(b).digest("hex");

// 1) Khoi nho nhu dung san (giong test_lz4_js.py::_TINY_BLOCK_B64).
{
  const plain = Buffer.from("Xin chao BearTea - cau noi LZ4 thu nghiem 12345", "utf8");
  const comp = Buffer.from(
    "8CBYaW4gY2hhbyBCZWFyVGVhIC0gY2F1IG5vaSBMWjQgdGh1IG5naGllbSAxMjM0NQ==", "base64");
  const out = pb3DecompressBlock(new Uint8Array(comp), plain.length);
  check("tiny-block", sha(out) === sha(plain), "len=" + out.length);
}

// 2) Block truyen qua doi so (block that tu IPA / fixture).
const args = process.argv.slice(2);
const blocks = [];
for (let i = 0; i + 1 < args.length; i += 2) {
  const comp = fs.readFileSync(args[i]);
  const meta = JSON.parse(fs.readFileSync(args[i + 1], "utf8"));
  blocks.push({ name: path.basename(args[i]), comp, meta });
}
for (const b of blocks) {
  const out = pb3DecompressBlock(new Uint8Array(b.comp), b.meta.unc);
  check("real-block:" + b.name, sha(out) === b.meta.sha,
    b.meta.comp + "->" + b.meta.unc + " byte");
}

// 3) Block hong phai nem LZ4_CORRUPT.
const corruptCases = {
  "truncate": { data: new Uint8Array(blocks[0] ? blocks[0].comp.subarray(0, 100) : [0xf0, 1, 2]), size: 131072 },
  "bad-offset-0": { data: new Uint8Array([0x40, 65, 66, 67, 68, 0x00, 0x00, 0x00]), size: 8 },
  "bad-offset-huge": { data: new Uint8Array([0x40, 65, 66, 67, 68, 0x00, 0xff, 0xff]), size: 8 },
  "wrong-size": { data: new Uint8Array([0x40, 65, 66, 67, 68]), size: 999 },
  "empty-nonempty": { data: new Uint8Array([1, 2]), size: 0 },
};
for (const [name, c] of Object.entries(corruptCases)) {
  let threw = "";
  try { pb3DecompressBlock(c.data, c.size); } catch (e) { threw = String(e && e.message || e); }
  check("corrupt:" + name, threw.indexOf("LZ4_CORRUPT") === 0, threw.slice(0, 60));
}
{
  const out = pb3DecompressBlock(new Uint8Array(0), 0);
  check("empty-block", out.length === 0);
}

// 4) Benchmark.
{
  const bench = blocks.find((b) => b.meta.unc >= 65536) || null;
  if (bench) {
    const comp = new Uint8Array(bench.comp);
    const t0 = performance.now();
    const N = 200;
    for (let i = 0; i < N; i++) pb3DecompressBlock(comp, bench.meta.unc);
    const dt = performance.now() - t0;
    const mbps = (N * bench.meta.unc / dt / 1e3).toFixed(1);
    check("bench:" + bench.name, dt > 0, N + "x" + bench.meta.unc + "B " + dt.toFixed(1) + "ms -> " + mbps + " MB/s");
  } else {
    const comp = Buffer.from(
      "8CBYaW4gY2hhbyBCZWFyVGVhIC0gY2F1IG5vaSBMWjQgdGh1IG5naGllbSAxMjM0NQ==", "base64");
    const t0 = performance.now();
    const N = 20000;
    for (let i = 0; i < N; i++) pb3DecompressBlock(new Uint8Array(comp), 47);
    const dt = performance.now() - t0;
    check("bench:tiny", dt > 0, N + "x47B " + dt.toFixed(1) + "ms");
  }
}

console.log(failures === 0 ? "ALL PASS" : failures + " FAIL");
process.exit(failures === 0 ? 0 : 1);
