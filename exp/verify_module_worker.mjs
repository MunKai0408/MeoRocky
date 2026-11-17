// verify_module_worker.mjs — Kiểm chứng Pyodide MODULE WORKER trong site/pyodide.
// Khác verify_vendor.mjs: import loadPyodide từ CHÍNH file vendor site/pyodide/pyodide.mjs
// (đường nạp của module worker), thay vì gói npm "pyodide" — chứng minh file .mjs đã vendor
// nạp được và chạy được. indexURL trỏ vào site/pyodide/, chặn mọi fetch http(s) trong lúc chạy.
// Mô phỏng đúng cách worker.js khởi động: đọc file trong site/ (bản vendor),
// ghi vào FS ảo /pb3web, import bridge, chạy từng tính năng trên fixture thật.
// Chạy: node verify_module_worker.mjs (từ thư mục exp/ của repo)
// Kết quả: module_worker_results.md + module_worker_results.json
import { loadPyodide } from "../pyodide/pyodide.mjs";
import fs from "node:fs";

const ROOT = decodeURIComponent(new URL("..", import.meta.url).pathname).replace(/\/$/, "");
const SITE = ROOT;
const PB3 = SITE + "/pb3";
const EXP = ROOT + "/exp";
const sec = (ms) => Math.round(ms / 100) / 10;
const results = {};
let failures = 0;

function rec(name, ok, detail, ms) {
  results[name] = { ok, detail, seconds: sec(ms) };
  if (!ok) failures++;
  console.log(`\n### ${name}: ${ok ? "PASS" : "FAIL"} (${sec(ms)}s)\n${detail}`);
}

// CHẶN MỌI FETCH HTTP(S): boot vendor đúng nghĩa thì chỉ đọc file local qua fs.
// Nếu code nào còn chạm CDN (jsDelivr hay bất kỳ http nào) thì guard này ném lỗi ngay.
let httpFetchCount = 0;
const _fetch = globalThis.fetch;
globalThis.fetch = async (input, init) => {
  const url = String(typeof input === "string" ? input : input.url);
  if (/^https?:\/\//.test(url)) { httpFetchCount++; throw new Error("BLOCKED http fetch: " + url); }
  return _fetch(input, init);
};
const pyodide = await loadPyodide({ indexURL: SITE + "/pyodide/" });
console.log("pyodide=" + pyodide.version + " python=" + pyodide.runPython("import sys; sys.version.split()[0]"));
rec("no_http_fetch_during_load", httpFetchCount === 0, `http fetch trong lúc loadPyodide: ${httpFetchCount}`, 0);

// Kiểm tra tĩnh đường module worker (đúng file sẽ chạy trên Safari):
// worker.js import từ pyodide.mjs, không còn importScripts; app.js tạo worker dạng module.
{
  const t0 = performance.now();
  const w = fs.readFileSync(SITE + "/worker.js", "utf8");
  const a = fs.readFileSync(SITE + "/app.js", "utf8");
  const idx = fs.readFileSync(SITE + "/index.html", "utf8");
  const hasMjsImport = /import\s*\{\s*loadPyodide\s*\}\s*from\s*["']\.\/pyodide\/pyodide\.mjs["']/.test(w);
  const noImportScripts = !/importScripts/.test(w);
  const moduleWorker = /new Worker\("worker\.js",\s*\{\s*type:\s*"module"\s*\}\)/.test(a);
  const noCrossOrigin = !/cdn\.jsdelivr\.net|https:\/\//.test(w + a + idx);
  const mjsVendored = fs.existsSync(SITE + "/pyodide/pyodide.mjs") &&
    fs.statSync(SITE + "/pyodide/pyodide.mjs").size === 17758;
  rec("module_worker_static", hasMjsImport && noImportScripts && moduleWorker && noCrossOrigin && mjsVendored,
    `worker import .mjs=${hasMjsImport} no importScripts=${noImportScripts} app type:module=${moduleWorker} no cross-origin=${noCrossOrigin} pyodide.mjs vendored 17758B=${mjsVendored}`,
    performance.now() - t0);
}

// loadPackage lz4 (như worker)
{
  const t0 = performance.now();
  try {
    await pyodide.loadPackage("lz4");
    pyodide.runPython("import lz4; print('lz4', lz4.__version__ if hasattr(lz4, '__version__') else 'ok')");
    rec("load_lz4", true, "loadPackage lz4 OK", performance.now() - t0);
  } catch (e) {
    rec("load_lz4", false, String(e?.message ?? e), performance.now() - t0);
  }
}

// Ghi file site vào FS ảo /pb3web (như worker fetch từng file)
{
  const t0 = performance.now();
  const man = JSON.parse(fs.readFileSync(SITE + "/pb3/files.json", "utf8"));
  for (const rel of man.files) {
    const buf = fs.readFileSync(SITE + "/" + rel);
    const target = "/pb3web/" + rel.slice(4);
    pyodide.FS.mkdirTree(target.slice(0, target.lastIndexOf("/")));
    pyodide.FS.writeFile(target, new Uint8Array(buf));
  }
  const b64 = fs.readFileSync(SITE + "/fonts/BeVietnamPro-Regular.ttf.b64", "utf8").replace(/\s+/g, "");
  const bin = Buffer.from(b64, "base64");
  pyodide.FS.mkdirTree("/pb3web/fonts");
  pyodide.FS.writeFile("/pb3web/fonts/BeVietnamPro-Regular.ttf", new Uint8Array(bin));
  rec("write_site_files", man.files.length === 95, `${man.files.length} file pb3/ + font b64 (${bin.length} byte) ghi vào /pb3web`, performance.now() - t0); // 95 = 94 + 1 (P0-2 thêm sdk/gate.py, 2026-11-17)
}

// progress callback: ghi lại event để chứng minh tiến độ chảy thật
const progEvents = [];
pyodide.globals.set("pb3_post_progress", (kind, done, total) => {
  progEvents.push({ kind, done, total });
});
pyodide.runPython("import sys; sys.path.insert(0, '/pb3web')\nimport bridge\nbridge.set_progress_cb(pb3_post_progress)");

// helper: ghi fixture host -> /tmp/input/ trong FS ảo
function putInput(hostPath, name) {
  const buf = fs.readFileSync(hostPath);
  pyodide.FS.mkdirTree("/tmp/input");
  pyodide.FS.writeFile("/tmp/input/" + name, new Uint8Array(buf));
  return "/tmp/input/" + name;
}
function call(fn, ...args) {
  const b = pyodide.globals.get("bridge");
  return JSON.parse(b[fn](...args));
}

// ---- boot ----
let boot;
{
  const t0 = performance.now();
  boot = call("boot");
  rec("boot", boot.ok === true && boot.plugins === 20,
    JSON.stringify(boot), performance.now() - t0);
}

// ---- font donor 142/142 (134 chữ cái + 8 dấu) ----
{
  const t0 = performance.now();
  const d = call("font_donor_info", "/pb3web/fonts/BeVietnamPro-Regular.ttf");
  rec("font_donor_142", d.ok === true && d.covered === 142 && d.total === 142,
    JSON.stringify(d), performance.now() - t0);
}

// ---- list_entries game1.zip (inventory: 11 theo chẩn đoán giai đoạn 1) ----
{
  const t0 = performance.now();
  const p = putInput(PB3 + "/tests/fixtures/extract/game1.zip", "game1.zip");
  const r = call("list_entries", p);
  rec("list_entries", r.ok === true && r.total === 11,
    `kind=${r.kind} total=${r.total} sample=${JSON.stringify((r.entries || []).slice(0, 3))}`,
    performance.now() - t0);
}

// ---- unity_inspect resources.assets ----
{
  const t0 = performance.now();
  const p = putInput(PB3 + "/tests/fixtures/unity/resources.assets", "resources.assets");
  const r = call("unity_inspect", p);
  const f0 = (r.files || [])[0] || {};
  const ta = (f0.text_assets || []).find((t) => t.path_id === 1001);
  rec("unity_inspect_assets", r.ok === true && f0.version === 17 &&
    f0.unity_version === "2019.4.40f1" && ta && ta.head.includes("Xin chao 9 Kings!"),
    JSON.stringify(r).slice(0, 600), performance.now() - t0);
}

// ---- unity_inspect sample.unity3d (bundle) ----
{
  const t0 = performance.now();
  const p = putInput(PB3 + "/tests/fixtures/unity/sample.unity3d", "sample.unity3d");
  const r = call("unity_inspect", p);
  rec("unity_inspect_bundle", r.ok === true && r.bundle === true && (r.files || []).length >= 1,
    JSON.stringify(r).slice(0, 500), performance.now() - t0);
}

// ---- extract game1.zip (21 entry, 6 file) ----
let extractZipBytes = 0;
{
  progEvents.length = 0;
  const t0 = performance.now();
  const p = putInput(PB3 + "/tests/fixtures/extract/game1.zip", "game1.zip");
  const r = call("extract", p);
  const phases = [...new Set(progEvents.map((e) => e.kind))];
  const zipData = pyodide.FS.readFile("/tmp/extract.zip");
  extractZipBytes = zipData.length;
  rec("extract", r.ok === true && r.total_entries === 21 && r.files.length === 6 && extractZipBytes > 0,
    `decision=${r.decision} total=${r.total_entries} files=${r.files.length} zip=${extractZipBytes}B phases=${phases.join(",")} progress_events=${progEvents.length}`,
    performance.now() - t0);
}

// ---- translate offline ----
{
  progEvents.length = 0;
  const t0 = performance.now();
  const r = call("translate_json", JSON.stringify({ play: "play", quit: "Quit" }), "vi", false);
  rec("translate_offline", r.ok === true && r.translated.play === "Chơi",
    `translated=${JSON.stringify(r.translated)} untranslated=${r.untranslated} progress_events=${progEvents.length}`,
    performance.now() - t0);
}

// ---- translate + credit ----
{
  const t0 = performance.now();
  const r = call("translate_json", JSON.stringify({ title: "9 Kings", play: "play" }), "vi", true);
  rec("translate_credit", r.ok === true && r.translated.play === "Chơi" && typeof r.credit_key === "string",
    `credit_key=${r.credit_key} title=${JSON.stringify(r.translated.title)}`,
    performance.now() - t0);
}

// ---- compress + decompress sample.ipa ----
{
  progEvents.length = 0;
  const t0 = performance.now();
  const p = putInput(PB3 + "/tests/fixtures/zip/sample.ipa", "sample.ipa");
  const c = call("compress_file", p, 6);
  const d = call("decompress_file", "/tmp/out.tar.xz");
  rec("compress_roundtrip", c.ok === true && d.ok === true && d.byte_identical === true,
    `orig=${c.original_size} comp=${c.compressed_size} ratio=${c.ratio} restored_sha=${(d.restored_sha256 || "").slice(0, 16)} byte_identical=${d.byte_identical} progress_events=${progEvents.length}`,
    performance.now() - t0);
}

// ---- font_scan donor + font_merge (donor làm target: added=0, chạy được đường merge) ----
{
  const t0 = performance.now();
  const donor = "/pb3web/fonts/BeVietnamPro-Regular.ttf";
  const s = call("font_scan", donor);
  const m = call("font_merge", donor, donor);
  const merged = pyodide.FS.readFile("/tmp/merged.ttf");
  rec("font_scan_merge", s.ok === true && s.covered === 142 && m.ok === true &&
    m.added === 0 && merged.length > 0,
    `scan=${s.summary} merge_added=${m.added} already=${m.already} glyphs_copied=${m.glyphs_copied} merged_bytes=${merged.length}`,
    performance.now() - t0);
}

// ---- font_donor_file ----
{
  const t0 = performance.now();
  const r = call("font_donor_file");
  const data = pyodide.FS.readFile("/tmp/BeVietnamPro-Regular.ttf");
  rec("font_donor_file", r.ok === true && data.length === 132948,
    `dest=${r.dest} bytes=${data.length}`, performance.now() - t0);
}

// ---- diagnose sample.ipa ----
{
  const t0 = performance.now();
  const p = putInput(PB3 + "/tests/fixtures/zip/sample.ipa", "sample.ipa");
  const r = call("diagnose", p);
  rec("diagnose", r.ok === true && r.entries.length === 3 && r.hex.length > 0 && r.strings.length > 0,
    `kind=${r.kind} entries=${r.entries.length} hex_lines=${r.hex.length} strings=${r.strings.length} first_str=${JSON.stringify((r.strings[0] || {}).text)}`,
    performance.now() - t0);
}

// ---- file_sha256 ----
{
  const t0 = performance.now();
  const r = call("file_sha256", "/tmp/input/sample.ipa");
  rec("file_sha256", r.ok === true && r.sha256.startsWith("ef27b9cf"),
    JSON.stringify(r), performance.now() - t0);
}

results._failures = failures;
fs.writeFileSync(EXP + "/module_worker_results.json", JSON.stringify(results, null, 2));
const lines = ["# Kiểm chứng bản web PB3 (Pyodide cục bộ, Node)", "",
  `Ngày: 2026-11-16. Pyodide ${pyodide.version}. Chạy bridge.py trên bản vendor trong site/ (không dùng cây PB3 gốc).`, "",
  "| Mục | Kết quả | Giây | Chi tiết |", "|---|---|---|---|"];
for (const [k, v] of Object.entries(results)) {
  if (k.startsWith("_")) continue;
  lines.push(`| ${k} | ${v.ok ? "PASS" : "FAIL"} | ${v.seconds} | ${String(v.detail).replace(/\|/g, "/").slice(0, 220)} |`);
}
lines.push("", `Thất bại: ${failures}`);
fs.writeFileSync(EXP + "/module_worker_results.md", lines.join("\n"));
console.log(`\n=== XONG: ${Object.keys(results).length - 1} mục, ${failures} FAIL — xem module_worker_results.md ===`);
process.exit(failures ? 1 : 0);
