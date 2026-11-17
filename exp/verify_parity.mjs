// verify_parity.mjs — Kiểm chứng Phase 2 (ngang chức năng iSH) trên bản web.
// Chạy bridge.py trên bản vendor trong site/pb3 (KHÔNG dùng cây PB3 gốc),
// bằng Pyodide npm, như verify_module_worker.mjs.
// Bao phủ: boot 20/20, Mod dry-run + ghi thật (fixture Unity), Batch tuần tự
// 2 job (manifest + checkpoint/resume), KB (thêm/tra cứu/xuất file/nhập lại
// file đã xuất/quên), support matrix từ dữ liệu thật, dịch offline/Google
// (không vỡ gt.js).
// Chạy: node verify_parity.mjs (từ thư mục exp/ của repo)
// Kết quả: parity_results.md + parity_results.json
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

// Phase 1 đã vendor sdk/batch.py, kb.py, support_matrix.py + services/ ?
// Nếu thiếu thì harness PHẢI dùng stand-in từ cây iSH và ghi rõ (không giấu).
const vendored = {};
for (const f of ["sdk/batch.py", "sdk/kb.py", "sdk/support_matrix.py"]) {
  vendored[f] = fs.existsSync(SITE + "/pb3/" + f);
}
const needStandin = Object.values(vendored).some((v) => !v);
console.log("vendored:", JSON.stringify(vendored), "needStandin:", needStandin);

const pyodide = await loadPyodide({ indexURL: SITE + "/pyodide/" });
console.log("pyodide=" + pyodide.version + " python=" + pyodide.runPython("import sys; sys.version.split()[0]"));
await pyodide.loadPackage("lz4");

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
  // Stand-in CHỈ khi Phase 1 chưa vendor (ghi rõ trong kết quả).
  for (const [rel, ok] of Object.entries(vendored)) {
    if (!ok) {
      const buf = fs.readFileSync(PB3 + "/" + rel);
      pyodide.FS.writeFile("/pb3web/" + rel, new Uint8Array(buf));
      console.log("STAND-IN từ cây iSH (chỉ trong FS ảo): " + rel);
    }
  }
  const b64 = fs.readFileSync(SITE + "/fonts/BeVietnamPro-Regular.ttf.b64", "utf8").replace(/\s+/g, "");
  const bin = Buffer.from(b64, "base64");
  pyodide.FS.mkdirTree("/pb3web/fonts");
  pyodide.FS.writeFile("/pb3web/fonts/BeVietnamPro-Regular.ttf", new Uint8Array(bin));
  rec("write_site_files", man.files.length >= 71 && !needStandin,
    `${man.files.length} file pb3/ ghi vào /pb3web; stand-in iSH: ${needStandin ? "CÓ (xem parity_results.md)" : "KHÔNG — chạy trên bản vendor thật"}`,
    performance.now() - t0);
}

const progEvents = [];
pyodide.globals.set("pb3_post_progress", (kind, done, total) => {
  progEvents.push({ kind, done, total });
});
pyodide.runPython("import sys; sys.path.insert(0, '/pb3web')\nimport bridge\nbridge.set_progress_cb(pb3_post_progress)");

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
function callAsync(fn, ...args) {
  const b = pyodide.globals.get("bridge");
  return b[fn](...args).then((s) => JSON.parse(s));
}
import { createHash } from "node:crypto";
function sha256hexSync(buf) { return createHash("sha256").update(buf).digest("hex"); }
function fsExists(p) {
  try { pyodide.FS.stat(p); return true; } catch (e) { return false; }
}

// ---- boot 20/20 ----
let boot;
{
  const t0 = performance.now();
  boot = call("boot");
  rec("boot_20_plugins", boot.ok === true && boot.plugins === 20,
    JSON.stringify(boot), performance.now() - t0);
}

// ---- bridge có đủ hàm mới ----
{
  const t0 = performance.now();
  const has = pyodide.runPython(
    "import json; json.dumps({n: hasattr(bridge, n) for n in " +
    "['mod_patch', 'batch_run', 'kb_add', 'kb_query', 'kb_list', 'kb_forget', 'kb_export', 'kb_import', 'support_matrix']})");
  const h = JSON.parse(has);
  rec("bridge_new_fns", Object.values(h).every(Boolean), JSON.stringify(h), performance.now() - t0);
}

// ---- MOD dry-run trên resources.assets (pid 1001) ----
const assetsPath = putInput(PB3 + "/tests/fixtures/unity/resources.assets", "resources.assets");
const assetsShaBefore = sha256hexSync(fs.readFileSync(PB3 + "/tests/fixtures/unity/resources.assets"));
let modDry;
{
  progEvents.length = 0;
  const t0 = performance.now();
  modDry = call("mod_patch", assetsPath,
    JSON.stringify([{ object: 1001, old: "Xin chao", new: "Chào" }]), false);
  const e = (modDry.edits || [])[0] || {};
  const noOutDir = !fsExists("/tmp/mod_out");
  const inputUnchanged = sha256hexSync(pyodide.FS.readFile(assetsPath)) === assetsShaBefore;
  const progOk = progEvents.some((ev) => ev.kind === "mod" && ev.done === 1 && ev.total === 1);
  rec("mod_dryrun", modDry.ok === true && modDry.dry_run === true && modDry.patched === 1 &&
    e.verified === true && e.occurrences === 1 && e.write_mode === "PATCH_INPLACE" &&
    modDry.dest === null && modDry.zip_path === null && noOutDir && inputUnchanged && progOk,
    `verified=${e.verified} occ=${e.occurrences} mode=${e.write_mode} script ${e.old_script_len}->${e.new_script_len}B unit ${e.size_before}->${e.size_after}B dest=${modDry.dest} no_mod_out=${noOutDir} input_unchanged=${inputUnchanged} progress_mod=${progOk}`,
    performance.now() - t0);
}

// ---- MOD ghi thật (allow_write=true) ----
let modWrite;
{
  progEvents.length = 0;
  const t0 = performance.now();
  modWrite = call("mod_patch", assetsPath,
    JSON.stringify([{ object: 1001, old: "Xin chao", new: "Chào" }]), true);
  const e = (modWrite.edits || [])[0] || {};
  const destExists = modWrite.dest ? fsExists(modWrite.dest) : false;
  let patchedScript = null;
  if (destExists) {
    // runPython trả về giá trị của biểu thức cuối (print trả None).
    patchedScript = pyodide.runPython(
      "from sdk import unity as u\n" +
      "sf = u.parse_serialized(open('" + modWrite.dest + "', 'rb').read(), bridge._app.limits, where='patched')\n" +
      "[s for _p, _n, s in sf.text_assets() if _p == 1001][0]");
  }
  const inputUnchanged = sha256hexSync(pyodide.FS.readFile(assetsPath)) === assetsShaBefore;
  rec("mod_write", modWrite.ok === true && modWrite.dry_run === false && e.verified === true &&
    destExists && patchedScript === "Chào 9 Kings!\x00\x00\x00" && inputUnchanged,
    `dest=${modWrite.dest} exists=${destExists} patched_script=${JSON.stringify(patchedScript)} input_unchanged=${inputUnchanged}`,
    performance.now() - t0);
}

// ---- MOD trên bundle sample.unity3d (entry trong bundle) ----
{
  const t0 = performance.now();
  const bp = putInput(PB3 + "/tests/fixtures/unity/sample.unity3d", "sample.unity3d");
  const insp = call("unity_inspect", bp);
  let detail = "";
  let ok = false;
  let skipped = false;
  const files = insp.files || [];
  let target = null;
  for (const f of files) {
    for (const ta of f.text_assets || []) {
      const head = ta.head || "";
      if (/^[\x20-\x7e]{4,}/.test(head) && head.slice(0, 4) !== "ZZZZ") {
        target = { entry: f.entry, pid: ta.path_id, old: head.slice(0, 4) };
        break;
      }
    }
    if (target) break;
  }
  if (!target) {
    skipped = true;
    detail = "SKIP — fixture sample.unity3d không có TextAsset ASCII dùng được (không tính FAIL)";
  } else {
    const r = call("mod_patch", bp,
      JSON.stringify([{ object: target.pid, old: target.old, new: "ZZZZ", entry: target.entry }]), false);
    const e = (r.edits || [])[0] || {};
    ok = r.ok === true && e.verified === true && e.occurrences >= 1;
    detail = `entry=${target.entry} pid=${target.pid} old=${JSON.stringify(target.old)} verified=${e.verified} occ=${e.occurrences} mode=${e.write_mode}`;
  }
  results["mod_bundle"] = { ok: ok || skipped, skipped, detail, seconds: sec(performance.now() - t0) };
  if (!ok && !skipped) failures++;
  console.log(`\n### mod_bundle: ${ok ? "PASS" : skipped ? "SKIP" : "FAIL"} (${sec(performance.now() - t0)}s)\n${detail}`);
}

putInput(PB3 + "/tests/fixtures/extract/game1.zip", "game1.zip");
// ---- KB: thêm + tra cứu + liệt kê ----
let kbGameId;{
  const t0 = performance.now();
  const a = call("kb_add", "/tmp/input/game1.zip");
  kbGameId = a.game_id;
  const q = call("kb_query", "/tmp/input/game1.zip");
  const l = call("kb_list");
  rec("kb_add_query_list", a.ok === true && typeof a.game_id === "number" &&
    q.ok === true && q.found === true && (q.profile.files || []).length === 5 &&
    l.ok === true && l.games.length === 1 && l.session_only === true,
    `game_id=${a.game_id} engine=${a.engine} query_found=${q.found} files=${(q.profile.files || []).length} list_games=${l.games.length} size=${l.size_bytes}B`,
    performance.now() - t0);
}

// ---- KB: xuất file + kiểm chứng file xuất tự chứa đủ dữ liệu ----
{
  const t0 = performance.now();
  const ex = call("kb_export");
  const destExists = ex.dest ? fsExists(ex.dest) : false;
  let copyGames = -1;
  if (destExists) {
    const bytes = pyodide.FS.readFile(ex.dest);
    pyodide.FS.writeFile("/tmp/kb_copy_check.sqlite", bytes);
    copyGames = pyodide.runPython(
      "from sdk.kb import KB\nkb2 = KB('/tmp/kb_copy_check.sqlite')\nn = len(kb2.list_games())\nkb2.close()\nn");
  }
  rec("kb_export", ex.ok === true && destExists && ex.games === 1 &&
    ex.size > 0 && copyGames === 1,
    `dest=${ex.dest} size=${ex.size}B games=${ex.games} copy_reopen_games=${copyGames}`,
    performance.now() - t0);
}

// ---- KB: quên + liệt kê lại = 0 ----
{
  const t0 = performance.now();
  const f = call("kb_forget", String(kbGameId));
  const l = call("kb_list");
  rec("kb_forget", f.ok === true && f.forgotten === true && l.ok === true && l.games.length === 0,
    `forgotten=${f.forgotten} list_games_after=${l.games.length}`,
    performance.now() - t0);
}

// ---- KB: nhập lại file đã xuất ở trên (kb_import, phase 3) ----
// KB phiên hiện trống (đã quên ở mục trước). File /tmp/pb3_kb.sqlite do
// kb_export ghi vẫn còn 1 game — NHƯNG game đó là game1.zip, không có
// bundle id nên import_profile (lấy bundle id làm khóa) từ chối: kết quả
// thật phải là imported=0, skipped=1, kèm cảnh báo rõ lý do, file nguồn
// không bị sửa. Sau đó dựng một file KB nguồn có bundle id thật bằng
// sdk.kb.remember rồi nhập: phải đếm đúng 1 game, đúng bundle id.
{
  const t0 = performance.now();
  const before = Buffer.from(pyodide.FS.readFile("/tmp/pb3_kb.sqlite"));
  const im0 = call("kb_import", "/tmp/pb3_kb.sqlite");
  const after = Buffer.from(pyodide.FS.readFile("/tmp/pb3_kb.sqlite"));
  const l0 = call("kb_list");
  // File KB nguồn có bundle id thật (dựng trực tiếp bằng sdk.kb).
  pyodide.runPython(
    "from sdk.kb import KB\n" +
    "src = KB('/tmp/kb_src.sqlite')\n" +
    "fp = {'bundle_id': 'com.example.kbtest', 'version': '1.0.5', 'engine': 'unity', 'engine_conf': 0, 'fingerprint': 'kbtestfp0123456789abcdef', 'size': 1234}\n" +
    "src.remember(fp, [{'rel_path': 'data/en.json', 'size': 10, 'sha256': '" + "aa".repeat(32) + "', 'role': 'text', 'adapter_id': 'json_text', 'score': 1.0}], [], {})\n" +
    "src.close()\n");
  const im = call("kb_import", "/tmp/kb_src.sqlite");
  const l = call("kb_list");
  const g0 = (im.games || [])[0] || {};
  rec("kb_import", im0.ok === true && im0.imported === 0 && im0.skipped === 1 &&
    (im0.warnings || []).some((w) => /không có bundle id/.test(w)) &&
    l0.ok === true && l0.games.length === 0 && before.equals(after) &&
    im.ok === true && im.imported === 1 && im.skipped === 0 &&
    g0.bundle_id === "com.example.kbtest" && l.ok === true && l.games.length === 1 &&
    l.games[0].bundle_id === "com.example.kbtest" &&
    (im.warnings || []).some((w) => /chưa kiểm hash/.test(w)),
    `file_phase2: imported=${im0.imported} skipped=${im0.skipped} (game1.zip không có bundle id) list_after=${l0.games.length} source_unchanged=${before.equals(after)}; file_có_bundle: imported=${im.imported} bundle=${g0.bundle_id} list_after=${l.games.length} warnings=${(im.warnings || []).length}`,
    performance.now() - t0);
}

// ---- BATCH tuần tự 2 job (extract game1.zip + mod resources.assets, dry-run) ----
const manifest = JSON.stringify([
  { path: "game1.zip", mode: "extract" },
  { path: "resources.assets", mode: "mod", object: "1001", old: "Xin chao", new: "Chào" },
]);
let batch1, batch1Ckpt;
{
  progEvents.length = 0;
  const t0 = performance.now();
  batch1 = call("batch_run", manifest, true, null, false);
  const s = batch1.summary || {};
  const ck = batch1.checkpoint ? JSON.parse(batch1.checkpoint) : null;
  const ckGames = ck && ck.games ? Object.keys(ck.games).length : 0;
  const zipOk = batch1.zip_path ? fsExists(batch1.zip_path) && pyodide.FS.readFile(batch1.zip_path).length > 0 : false;
  const progOk = progEvents.some((ev) => ev.kind === "batch" && ev.done === 2 && ev.total === 2);
  const gameDirs = (s.games || []).map((g) => g.status + ":" + (g.game_dir || "").split("/").pop()).join(",");
  batch1Ckpt = batch1.checkpoint;
  rec("batch_sequential_2jobs", batch1.ok === true && batch1.jobs === 1 && batch1.sequential === true &&
    s.total === 2 && s.ok === 2 && s.failed === 0 && s.dry_run === true && ckGames === 2 && zipOk && progOk,
    `total=${s.total} ok=${s.ok} failed=${s.failed} jobs=${s.jobs} ckpt_games=${ckGames} zip=${batch1.zip_bytes}B progress_batch_2_2=${progOk} games=[${gameDirs}]`,
    performance.now() - t0);
}

// ---- BATCH resume: chạy lại với checkpoint -> cả 2 game bị bỏ qua ----
{
  const t0 = performance.now();
  const r2 = call("batch_run", manifest, true, batch1Ckpt, true);
  const s = r2.summary || {};
  rec("batch_resume_checkpoint", r2.ok === true && r2.resumed === true &&
    s.total === 2 && s.skipped === 2 && s.ok === 0 &&
    (s.games || []).every((g) => (g.note || "").includes("checkpoint")),
    `total=${s.total} skipped=${s.skipped} ok=${s.ok} notes=${JSON.stringify((s.games || []).map((g) => g.note))}`,
    performance.now() - t0);
}

// ---- SUPPORT MATRIX từ dữ liệu thật ----
{
  const t0 = performance.now();
  const r = call("support_matrix");
  const d = r.data || {};
  const p0 = (d.plugins || [])[0] || {};
  rec("support_matrix", r.ok === true && (d.plugins || []).length === 20 &&
    (d.containers || []).length === 11 && (d.unity || []).length === 7 &&
    (d.real_game || []).length >= 1 && (d.unsupported || []).length >= 1 &&
    typeof r.markdown === "string" && r.markdown.includes("MA TRẬN HỖ TRỢ") &&
    p0.id && p0.maturity_effective,
    `plugins=${(d.plugins || []).length} containers=${(d.containers || []).length} unity=${(d.unity || []).length} real_game=${(d.real_game || []).length} unsupported=${(d.unsupported || []).length} first=${p0.id}/${p0.maturity_effective} md_lines=${(r.markdown || "").split("\n").length}`,
    performance.now() - t0);
}

// ---- Dịch: offline vẫn chạy + Google interface (gt.js) không vỡ ----
{
  const t0 = performance.now();
  const off = call("translate_json", JSON.stringify({ play: "play", quit: "Quit" }), "vi", false);
  const w = fs.readFileSync(SITE + "/worker.js", "utf8");
  const gt = fs.readFileSync(SITE + "/gt.js", "utf8");
  // worker.js mục 2: import translateOneWithFallbacks (proxy -> Google -> MyMemory),
  // vẫn expose globalThis.pb3_google_translate như trước (bridge gọi qua js.).
  const importOk = /import\s*\{[^}]*translateOneWithFallbacks[^}]*\}\s*from\s*["']\.\/gt\.js["']/.test(w);
  const exposeOk = /globalThis\.pb3_google_translate\s*=\s*translateOneWithFallbacks/.test(w);
  const gtExportOk = /export\s+(async\s+)?function\s+translateOneWithFallbacks|export\s*\{[^}]*translateOneWithFallbacks/.test(gt);
  // translate_json_google trong Node: không có js.pb3_google_translate -> rơi về offline, không crash.
  const g = await callAsync("translate_json_google", JSON.stringify({ play: "play" }), "vi", false);
  rec("translate_regression", off.ok === true && off.translated.play === "Chơi" &&
    importOk && exposeOk && gtExportOk &&
    g.ok === true && g.translated_by === "offline" && g.translated.play === "Chơi",
    `offline play->${off.translated.play}; worker import gt.js=${importOk} expose globalThis=${exposeOk} gt.js export=${gtExportOk}; google_fn fallback offline=${g.translated_by} play->${g.translated.play}`,
    performance.now() - t0);
}

results._failures = failures;
results._standin_used = needStandin;
fs.writeFileSync(EXP + "/parity_results.json", JSON.stringify(results, null, 2));
const lines = ["# Kiểm chứng Phase 2 — ngang chức năng iSH cho bản web PB3", "",
  `Ngày: 2026-11-16. Pyodide ${pyodide.version}. Chạy bridge.py trên bản vendor trong site/pb3 (KHÔNG dùng cây PB3 gốc).`,
  needStandin
    ? "CẢNH BÁO: một số file sdk (batch/kb/support_matrix) chưa có trong site/pb3 nên harness đã dùng stand-in từ cây iSH — xem write_site_files."
    : "Không dùng stand-in: sdk/batch.py, sdk/kb.py, sdk/support_matrix.py đều đã có trong site/pb3 (Phase 1 đã vendor, md5 khớp cây iSH).",
  "",
  "| Mục | Kết quả | Giây | Chi tiết |", "|---|---|---|---|"];
for (const [k, v] of Object.entries(results)) {
  if (k.startsWith("_")) continue;
  const mark = v.skipped ? "SKIP" : v.ok ? "PASS" : "FAIL";
  lines.push(`| ${k} | ${mark} | ${v.seconds} | ${String(v.detail).replace(/\|/g, "/").slice(0, 240)} |`);
}
lines.push("", `Thất bại: ${failures}`);
fs.writeFileSync(EXP + "/parity_results.md", lines.join("\n"));
console.log(`\n=== XONG: ${Object.keys(results).filter((k) => !k.startsWith("_")).length} mục, ${failures} FAIL, stand-in: ${needStandin} — xem parity_results.md ===`);
process.exit(failures ? 1 : 0);
