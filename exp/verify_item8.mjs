// verify_item8.mjs — Kiểm chứng MỤC 8: plan JSON + UI checkbox cho REBUILD_ALL.
// (repo MunKai0408/MeoRocky, nhánh main; KHÔNG đẩy lên GitHub.)
//
// Chạy: node verify_item8.mjs (từ thư mục exp/ của repo)
// Kết quả: item8_results.json + item8_results.md (báo cáo tay).
//
//  A. Tĩnh: node --check; py_compile; plan.py chỉ dùng stdlib + tương thích
//     Python 3.9 (không có cú pháp 3.10+); files.json 95, tests.json 166,
//     sw.js precache có sdk/plan.py + CACHE_VERSION v21-20261117
//     (đồng bộ 2026-11-17; lúc viết mục 8: 90/159/v9).
//  B. Unit test Python: tests.test_plan (19 test).
//  C. Node Pyodide + bridge thật (fixture item8_fixture.zip: bundle v7 LZ4,
//     3 TextAsset JSON en/menu/dialog):
//     C1 plan_build -> đúng model + đủ 3 entry + after_sha khớp.
//     C2 dry-run không đổi file, không ghi output.
//     C3 bỏ tick 1 entry rồi áp -> chỉ entry được tick đổi, entry kia giữ nguyên.
//     C4 undo khôi phục đúng before_sha (cả từ backup lẫn patch ngược).
//     C5 resume sau khi dừng giữa chừng (stop_after=1 + checkpoint).
//     C6 plan JSON xuất ra đọc lại được + build lại cho cùng plan id.
//     C7 op REBUILD_ALL bị từ chối rõ ràng (M4) — entry khác vẫn áp.
//     C8 rebuild_all=true áp toàn bộ, bỏ qua tick.
//  D. Trình duyệt THẬT (Playwright chromium headless shell, 390px, serve
//     site qua HTTP local): chọn fixture -> boot -> tab Mod -> Lập kế hoạch
//     hiện 3 entry có checkbox -> bỏ tick 1 entry -> dry-run hiện kết quả;
//     390px không tràn ngang, không pageerror.
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { execFileSync, spawnSync } from "node:child_process";

const ROOT = decodeURIComponent(new URL("..", import.meta.url).pathname).replace(/\/$/, "");
const SITE = ROOT;
const EXP = ROOT + "/exp";
const FX = EXP + "/fixtures/item8_fixture.zip";
const BUNDLE = "Payload/9Kings.app/Data/Raw/aa/iOS/localization-locales_assets_all.bundle";
const CAB = "CAB-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const VPATH = BUNDLE + "::" + CAB;
const VI = {
  hello: "Xin chào", play: "Chơi", quit: "Thoát",
  start: "Bắt đầu chơi", options: "Tùy chọn", exit: "Thoát game",
  greeting: "Chào mừng, anh hùng!", farewell: "Tạm biệt!",
};
const sec = (ms) => Math.round(ms / 100) / 10;
const results = {};
let failures = 0;
function rec(name, ok, detail, ms) {
  results[name] = { ok, detail, seconds: ms == null ? undefined : sec(ms) };
  if (!ok) failures++;
  console.log(`\n### ${name}: ${ok ? "PASS" : "FAIL"}${ms == null ? "" : " (" + sec(ms) + "s)"}\n${detail}`);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ================= A. Tĩnh =================
{ // A1. node --check + py_compile
  const t0 = performance.now();
  const bad = [];
  for (const f of ["app.js", "sw.js"]) {
    try { execFileSync("node", ["--check", SITE + "/" + f], { stdio: "pipe" }); }
    catch (e) { bad.push(f); }
  }
  try {
    execFileSync("python3", ["-m", "py_compile", SITE + "/pb3/bridge.py",
      SITE + "/pb3/sdk/plan.py", SITE + "/pb3/tests/test_plan.py"],
      { stdio: "pipe", cwd: SITE + "/pb3" });
  } catch (e) { bad.push("py_compile"); }
  rec("static_node_py_check", bad.length === 0,
    "node --check OK: app.js, sw.js; python3 -m py_compile OK: bridge.py, sdk/plan.py, tests/test_plan.py" +
    (bad.length ? " | LỖI: " + bad.join(", ") : ""), performance.now() - t0);
}

{ // A2. sdk/plan.py: stdlib-only + Python 3.9 + manifest/precache/version
  const t0 = performance.now();
  const r = spawnSync("python3", [EXP + "/check_item8_static.py"], { encoding: "utf8" });
  let detail = (r.stdout || "").trim();
  let ok = r.status === 0;
  try {
    const j = JSON.parse(r.stdout || "{}");
    if (j.fails && j.fails.length) { ok = false; detail = j.fails.join(" | "); }
    else detail = "stdlib-only + Python 3.9 + files.json=95 + tests.json=166 + sw.js precache có sdk/plan.py + CACHE_VERSION=v21-20261117";
  } catch (e) { ok = false; detail = "không đọc được JSON: " + (r.stdout || "") + (r.stderr || ""); }
  rec("static_item8_checks", ok, detail, performance.now() - t0);
}

{ // A3. Sinh lại fixture (tái tạo được)
  const t0 = performance.now();
  try {
    execFileSync("python3", [EXP + "/build_item8_fixture.py"], { stdio: "pipe" });
    const ok = fs.existsSync(FX) && fs.statSync(FX).size > 0;
    rec("fixture_regenerate", ok,
      "build_item8_fixture.py -> " + FX + " (" + (ok ? fs.statSync(FX).size : 0) + "B, bundle v7 LZ4 + resources.assets)",
      performance.now() - t0);
  } catch (e) {
    rec("fixture_regenerate", false, "sinh fixture lỗi: " + String(e && e.message ? e.message : e), performance.now() - t0);
  }
}

// ================= B. Unit test Python =================
{ // B1. tests.test_plan
  const t0 = performance.now();
  const r = spawnSync("python3", ["-m", "unittest", "tests.test_plan"],
    { cwd: SITE + "/pb3", encoding: "utf8" });
  const out = (r.stdout || "") + (r.stderr || "");
  const m = out.match(/Ran (\d+) tests?/);
  const okRun = out.match(/OK\s*$/m);
  rec("python_test_plan", r.status === 0 && !!okRun,
    "python3 -m unittest tests.test_plan exit=" + r.status + "; " +
    (m ? "Ran " + m[1] + " tests, " + (okRun ? "OK" : "FAILED") : "không đọc được số test; đuôi: " + out.slice(-300)),
    performance.now() - t0);
}

// ================= C. Node Pyodide + bridge thật =================
const { loadPyodide } = await import("../pyodide/pyodide.mjs");
async function bootPyodide() {
  const pyodide = await loadPyodide({ indexURL: SITE + "/pyodide/" });
  await pyodide.loadPackage("lz4");
  const man = JSON.parse(fs.readFileSync(SITE + "/pb3/files.json", "utf8"));
  for (const rel of man.files) {
    const buf = fs.readFileSync(SITE + "/" + rel);
    const target = "/pb3web/" + rel.slice(4);
    pyodide.FS.mkdirTree(target.slice(0, target.lastIndexOf("/")));
    pyodide.FS.writeFile(target, new Uint8Array(buf));
  }
  pyodide.runPython("import sys; sys.path.insert(0, '/pb3web')");
  pyodide.globals.set("pb3_post_progress", () => {});
  pyodide.runPython("import bridge\nbridge.set_progress_cb(pb3_post_progress)\nbridge.__boot_result__ = bridge.boot()\n");
  const bridge = pyodide.globals.get("bridge");
  const bootOk = JSON.parse(pyodide.globals.get("bridge").__boot_result__).ok;
  if (!bootOk) throw new Error("Pyodide bridge boot failed");
  return { pyodide, bridge, call: (fn, ...args) => JSON.parse(bridge[fn](...args)) };
}
const { pyodide, bridge, call } = await bootPyodide();
pyodide.FS.mkdirTree("/tmp/input");
pyodide.FS.writeFile("/tmp/input/item8_fixture.zip", new Uint8Array(fs.readFileSync(FX)));
const INPUT = "/tmp/input/item8_fixture.zip";
const fsExists = (p) => { try { pyodide.FS.stat(p); return true; } catch { return false; } };
const fsRead = (p) => pyodide.FS.readFile(p);

let plan = null;
let planApplied = null; // plan đúng như đã áp ở C3 (dialog bỏ tick)
{ // C1. plan_build: đúng model + đủ 3 entry
  const t0 = performance.now();
  const r = call("plan_build", INPUT, JSON.stringify(VI), VPATH, "REBUILD_SECTION", false, null);
  const okShape = r.ok === true && r.plan && r.plan.version === 1 &&
    typeof r.plan.id === "string" && r.plan.id.length === 12 &&
    r.plan.source_bundle === "item8_fixture.zip" &&
    r.plan.unit_kind === "bundle" && r.plan.rebuild_all === false &&
    Array.isArray(r.plan.entries) && r.plan.entries.length === 3;
  let okFields = false, okSha = false, detailFields = "";
  if (okShape) {
    plan = r.plan;
    okFields = r.plan.entries.every((e) =>
      e.asset_type === "TextAsset" && e.op === "REBUILD_SECTION" &&
      typeof e.object === "number" && Array.isArray(e.changes) && e.changes.length > 0 &&
      e.preview_before && e.preview_after && e.before_sha && e.after_sha &&
      e.before_sha.length === 64 && e.after_sha.length === 64 && e.selected === true);
    // before_sha phải khớp sha script gốc (đọc lại bằng parser web).
    const origScripts = JSON.parse(pyodide.runPython(`
import json, zipfile
from sdk import unity as u
from core.limits import Limits
_be = u.UnityBackend("NATIVE")
_data = open("/tmp/input/item8_fixture.zip", "rb").read()
_z = zipfile.ZipFile("/tmp/input/item8_fixture.zip")
_bdata = _z.read("${BUNDLE}")
_b = u.parse_bundle(_bdata, _be, Limits())
_sf = u.parse_serialized(_b.get_entry("${CAB}"), Limits(), where="${CAB}")
json.dumps({n: s for _p, n, s in _sf.text_assets()}, ensure_ascii=False)`));
    const { createHash } = await import("node:crypto");
    okSha = r.plan.entries.every((e) => {
      const name = e.asset_path.split("::").pop();
      return createHash("sha256").update(origScripts[name], "utf8").digest("hex") === e.before_sha;
    });
    detailFields = "entries=" + r.plan.entries.map((e) =>
      e.asset_path.split("::").pop() + ":" + e.changes.length + " change").join(", ") +
      "; preview_after en.hello=" + r.plan.entries.find((e) => e.object === 1001).preview_after.hello;
  }
  rec("C1_plan_build_model_3_entries", okShape && okFields && okSha,
    "plan id=" + (r.plan ? r.plan.id : "?") + "; " + detailFields +
    "; before_sha khớp script gốc: " + okSha + (r.ok ? "" : "; lỗi bridge: " + JSON.stringify(r)),
    performance.now() - t0);
}

{ // C2. dry-run không đổi file, không ghi output
  const t0 = performance.now();
  const before = fs.readFileSync(FX);
  const r = call("plan_apply", JSON.stringify(plan), INPUT, true, null, null);
  const after = new Uint8Array(fsRead(INPUT));
  const unchanged = Buffer.compare(Buffer.from(before), Buffer.from(after)) === 0;
  const noOut = !fsExists("/tmp/plan_out") && !fsExists("/tmp/plan.zip");
  rec("C2_dry_run_no_write", r.ok === true && r.dry_run === true && r.status === "done" &&
    r.applied.length === 3 && unchanged && noOut && r.outputs.length === 0,
    "applied=" + (r.applied || []).length + "; file gốc giữ nguyên: " + unchanged +
    "; không có /tmp/plan_out hay /tmp/plan.zip: " + noOut, performance.now() - t0);
}

let patchedBytes = null;
{ // C3. Bỏ tick 1 entry rồi áp -> chỉ entry được tick đổi
  const t0 = performance.now();
  const p2 = JSON.parse(JSON.stringify(plan));
  for (const e of p2.entries) if (e.object === 1003) e.selected = false; // dialog.json
  planApplied = p2;
  const r = call("plan_apply", JSON.stringify(p2), INPUT, false, null, null);
  let detail = "applied=" + (r.applied || []).length + ", errors=" + (r.errors || []).length +
    ", outputs=" + JSON.stringify(r.outputs || []);
  let okContent = false;
  if (r.ok && r.dest) {
    patchedBytes = Buffer.from(fsRead(r.dest));
    const got = JSON.parse(pyodide.runPython(`
import json
from sdk import unity as u
from core.limits import Limits
_be = u.UnityBackend("NATIVE")
_b = u.parse_bundle(bytes(${JSON.stringify(Array.from(patchedBytes))}), _be, Limits())
_sf = u.parse_serialized(_b.get_entry("${CAB}"), Limits(), where="${CAB}")
json.dumps({n: json.loads(s) for _p, n, s in _sf.text_assets()}, ensure_ascii=False)`));
    okContent = got["en.json"].hello === "Xin chào" && got["en.json"].play === "Chơi" &&
      got["menu.json"].start === "Bắt đầu chơi" &&
      got["dialog.json"].greeting === "Welcome, hero!" && got["dialog.json"].farewell === "Goodbye!";
    detail += "; en.hello=" + got["en.json"].hello + ", menu.start=" + got["menu.json"].start +
      ", dialog.greeting GIỮ NGUYÊN=" + got["dialog.json"].greeting;
  }
  const backupExists = fsExists("/tmp/plan_backup/" + plan.id);
  rec("C3_untick_one_apply_selected_only", r.ok === true && r.status === "done" &&
    r.applied.length === 2 && okContent && backupExists,
    detail + "; backup /tmp/plan_backup/" + plan.id + " tồn tại: " + backupExists,
    performance.now() - t0);
}

{ // C4. Undo khôi phục đúng before_sha (backup, rồi patch ngược khi mất backup)
  const t0 = performance.now();
  let okA = false, okB = false, detailA = "", detailB = "";
  // Output thật từ C3: /tmp/plan_out/<tên file> — lấy tên file từ FS.
  const outNames = (() => { try { return pyodide.FS.readdir("/tmp/plan_out"); } catch { return []; } })();
  const outFile = outNames.find((n) => n !== "." && n !== "..");
  // plan_undo chỉ cần đơn vị bundle, không cần zip bọc ngoài: ghi bundle
  // đã patch ra /tmp/input/patched.bundle và sửa plan trỏ thẳng tới CAB.
  const undoInput = "/tmp/input/patched.bundle";
  pyodide.FS.writeFile(undoInput, fsRead("/tmp/plan_out/" + outFile));
  // plan cho undo phải là plan ĐÚNG NHƯ ĐÃ ÁP (dialog bỏ tick) — undo chỉ
  // hoàn tác entry đã chọn; đưa plan gốc (dialog tick) thì patch ngược
  // dialog sẽ lỗi vì dialog chưa từng được áp.
  const planDirect = JSON.parse(JSON.stringify(planApplied));
  for (const e of planDirect.entries) e.vpath = CAB;
  planDirect.unit_entry = CAB;
  // 4a. Undo từ backup.
  const ru = call("plan_undo", JSON.stringify(planDirect), undoInput);
  if (ru.ok && ru.dest) {
    const restored = JSON.parse(pyodide.runPython(`
import json
from sdk import unity as u
from core.limits import Limits
_be = u.UnityBackend("NATIVE")
_data = open("${ru.dest}", "rb").read()
_b = u.parse_bundle(_data, _be, Limits())
_sf = u.parse_serialized(_b.get_entry("${CAB}"), Limits(), where="${CAB}")
json.dumps({n: json.loads(s) for _p, n, s in _sf.text_assets()}, ensure_ascii=False)`));
    // Chỉ 2 entry đã áp (dialog bỏ tick) nên chỉ 2 entry được hoàn tác;
    // dialog vốn chưa đổi nên vẫn là bản gốc.
    okA = (ru.restored || []).length === 2 && (ru.restored || []).every((x) => x.how === "backup") &&
      restored["en.json"].hello === "Hello" && restored["menu.json"].start === "Start Game" &&
      restored["dialog.json"].greeting === "Welcome, hero!";
    detailA = "restored=" + (ru.restored || []).length + " (backup, đúng 2 entry đã áp), en.hello=" + restored["en.json"].hello;
  } else {
    detailA = "plan_undo lỗi: " + JSON.stringify(ru);
  }
  // 4b. Xoá backup -> undo bằng patch ngược, vẫn verify before_sha.
  pyodide.runPython(`import shutil; shutil.rmtree("/tmp/plan_backup/${plan.id}", ignore_errors=True)`);
  const ru2 = call("plan_undo", JSON.stringify(planDirect), undoInput);
  if (ru2.ok && ru2.dest) {
    const restored2 = JSON.parse(pyodide.runPython(`
import json
from sdk import unity as u
from core.limits import Limits
_be = u.UnityBackend("NATIVE")
_data = open("${ru2.dest}", "rb").read()
_b = u.parse_bundle(_data, _be, Limits())
_sf = u.parse_serialized(_b.get_entry("${CAB}"), Limits(), where="${CAB}")
json.dumps({n: json.loads(s) for _p, n, s in _sf.text_assets()}, ensure_ascii=False)`));
    okB = (ru2.restored || []).length === 2 && (ru2.restored || []).every((x) => x.how === "reverse-patch") &&
      restored2["en.json"].hello === "Hello" && restored2["dialog.json"].farewell === "Goodbye!";
    detailB = "restored=" + (ru2.restored || []).length + " (reverse-patch, đúng 2 entry đã áp), en.hello=" + restored2["en.json"].hello;
  } else {
    detailB = "plan_undo (không backup) lỗi: " + JSON.stringify(ru2);
  }
  rec("C4_undo_backup_and_reverse_patch", okA && okB, "4a backup: " + detailA + " | 4b reverse-patch: " + detailB, performance.now() - t0);
}

{ // C5. Resume sau khi dừng giữa chừng (stop_after=1 + checkpoint)
  const t0 = performance.now();
  const r1 = call("plan_apply", JSON.stringify(plan), INPUT, false, null, 1);
  // Giữ lại output lần 1 NGAY (lần resume ghi đè cùng tên file trong /tmp/plan_out).
  if (r1.dest) pyodide.FS.writeFile("/tmp/input/r1.bundle", fsRead(r1.dest));
  const ck = JSON.stringify(r1.checkpoint);
  const r2 = call("plan_apply", JSON.stringify(plan), INPUT, false, ck, null);
  const okResume = r1.ok && r1.status === "partial" && r1.applied.length === 1 &&
    (r1.remaining || []).length === 2 &&
    r2.ok && r2.status === "done" && r2.applied.length === 2 &&
    (r2.checkpoint.completed || []).length === 3;
  // Nội dung gộp: output r1 (1 entry) + output r2 (2 entry) phải bằng apply đầy đủ.
  let okMerged = false;
  if (okResume && r1.dest && r2.dest) {
    const s1 = JSON.parse(pyodide.runPython(`
import json
from sdk import unity as u
from core.limits import Limits
_be = u.UnityBackend("NATIVE")
_b = u.parse_bundle(open("/tmp/input/r1.bundle", "rb").read(), _be, Limits())
_sf = u.parse_serialized(_b.get_entry("${CAB}"), Limits(), where="${CAB}")
json.dumps({n: json.loads(s) for _p, n, s in _sf.text_assets()}, ensure_ascii=False)`));
    const s2 = JSON.parse(pyodide.runPython(`
import json
from sdk import unity as u
from core.limits import Limits
_be = u.UnityBackend("NATIVE")
_b = u.parse_bundle(open("${r2.dest}", "rb").read(), _be, Limits())
_sf = u.parse_serialized(_b.get_entry("${CAB}"), Limits(), where="${CAB}")
json.dumps({n: json.loads(s) for _p, n, s in _sf.text_assets()}, ensure_ascii=False)`));
    // Hợp nhất theo từng key: en từ lần 1 (chỉ lần 1 mới áp en),
    // menu/dialog từ lần resume (chỉ lần 2 mới áp chúng). Spread {...s1,
    // ...s2} hay ngược lại đều sai vì mỗi output đều chứa cả 3 asset
    // (asset chưa áp ở lần đó vẫn là bản gốc và sẽ ghi đè bản đã dịch).
    const merged = {
      "en.json": s1["en.json"],
      "menu.json": s2["menu.json"],
      "dialog.json": s2["dialog.json"],
    };
    okMerged = merged["en.json"].hello === "Xin chào" && merged["menu.json"].start === "Bắt đầu chơi" &&
      merged["dialog.json"].greeting === "Chào mừng, anh hùng!";
  }
  rec("C5_resume_after_stop", okResume && okMerged,
    "lần 1: partial, applied=" + (r1.applied || []).length + ", remaining=" + (r1.remaining || []).length +
    "; lần 2 (checkpoint): done, applied=" + (r2.applied || []).length +
    ", checkpoint completed=" + ((r2.checkpoint || {}).completed || []).length +
    "; nội dung gộp đủ 3 entry đã dịch: " + okMerged, performance.now() - t0);
}

{ // C6. Plan JSON xuất ra đọc lại được + build lại cho cùng plan id
  const t0 = performance.now();
  const text = JSON.stringify(plan, null, 2);
  fs.writeFileSync("/tmp/item8_plan_export.json", text);
  const back = JSON.parse(fs.readFileSync("/tmp/item8_plan_export.json", "utf8"));
  const r = call("plan_build", INPUT, JSON.stringify(VI), VPATH, "REBUILD_SECTION", false, null);
  const okRound = back.id === plan.id && back.entries.length === 3 &&
    JSON.stringify(back.entries.map((e) => e.id).sort()) ===
    JSON.stringify(plan.entries.map((e) => e.id).sort());
  rec("C6_plan_json_roundtrip_stable_id", okRound && r.ok && r.plan.id === plan.id,
    "xuất " + text.length + "B JSON, đọc lại đủ 3 entry, id giữ nguyên " + back.id +
    "; build lại cho cùng id: " + (r.plan && r.plan.id), performance.now() - t0);
}

{ // C7. op REBUILD_ALL bị từ chối rõ ràng (M4) — entry khác vẫn áp
  const t0 = performance.now();
  const p7 = JSON.parse(JSON.stringify(plan));
  p7.entries[0].op = "REBUILD_ALL";
  const r = call("plan_apply", JSON.stringify(p7), INPUT, true, null, null);
  const err = (r.errors || [])[0] || {};
  rec("C7_rebuild_all_op_refused_m4", r.ok === true && r.applied.length === 2 &&
    r.errors.length === 1 && err.code === "E_WRITE_DISABLED" && /M4/.test(err.message || ""),
    "applied=" + (r.applied || []).length + " (2 entry còn lại vẫn áp), errors=" + (r.errors || []).length +
    ", code=" + err.code + ", message=" + JSON.stringify(err.message || ""), performance.now() - t0);
}

{ // C8. rebuild_all=true áp toàn bộ, bỏ qua tick
  const t0 = performance.now();
  const p8 = JSON.parse(JSON.stringify(plan));
  for (const e of p8.entries) e.selected = false;
  p8.rebuild_all = true;
  const r = call("plan_apply", JSON.stringify(p8), INPUT, true, null, null);
  rec("C8_rebuild_all_flag_applies_all", r.ok === true && r.applied.length === 3 &&
    (r.skipped || []).length === 0,
    "tất cả entry bỏ tick nhưng rebuild_all=true -> applied=" + (r.applied || []).length +
    ", skipped=" + (r.skipped || []).length, performance.now() - t0);
}

// ================= D. Trình duyệt thật 390px =================
const MIME = { ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript",
  ".json": "application/json", ".css": "text/css", ".webmanifest": "application/manifest+json",
  ".wasm": "application/wasm", ".png": "image/png", ".b64": "text/plain" };
function makeServer(root) {
  return http.createServer((req, res) => {
    const urlPath = decodeURIComponent(req.url.split("?")[0]);
    const fp = path.join(root, urlPath === "/" ? "index.html" : urlPath);
    if (!fp.startsWith(root)) { res.writeHead(403); res.end(); return; }
    fs.readFile(fp, (err, data) => {
      if (err) { res.writeHead(404); res.end("not found"); return; }
      res.writeHead(200, { "Content-Type": MIME[path.extname(fp)] || "application/octet-stream" });
      res.end(data);
    });
  });
}
const server = makeServer(SITE);
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const PORT = server.address().port;

let browser = null;
const pageErrors = [];
try {
  const { chromium } = await import("playwright-core");
  const candidates = [
    process.env.HOME + "/.cache/ms-playwright/chromium_headless_shell-1148/chrome-linux/headless_shell",
    process.env.HOME + "/.cache/ms-playwright/chromium_headless_shell-1148/chrome-linux/chrome",
  ];
  const exe = candidates.find((c) => fs.existsSync(c));
  if (!exe) throw new Error("không tìm thấy Chromium headless shell ở ~/.cache/ms-playwright");
  browser = await chromium.launch({ executablePath: exe, args: ["--no-sandbox"] });
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, acceptDownloads: true });
  const page = await ctx.newPage();
  await page.addInitScript(() => {
    try { Object.defineProperty(navigator, "canShare", { value: () => false }); } catch (e) {}
  });
  page.on("pageerror", (e) => pageErrors.push(String(e && e.message ? e.message : e)));
  page.on("console", (m) => { if (m.type() === "error") pageErrors.push("console: " + m.text()); });
  const elText = (id) => page.evaluate((i) => (document.getElementById(i) || {}).textContent || "", id);
  const waitReady = async () => {
    await page.waitForFunction(
      () => { const el = document.getElementById("status"); return el && el.textContent.includes("Sẵn sàng"); },
      null, { timeout: 240000 });
  };

  { // D1. Chọn fixture -> boot -> tab Mod -> Lập kế hoạch hiện 3 entry
    const t0 = performance.now();
    await page.goto("http://127.0.0.1:" + PORT + "/index.html");
    await page.setInputFiles("#file-input", FX);
    await page.waitForFunction(
      (n) => { const el = document.getElementById("file-info"); return el && el.textContent.includes(n); },
      "item8_fixture.zip", { timeout: 120000 });
    await waitReady();
    const status = await elText("status");
    await page.click("#btn-nav-tools");
    await page.click("#btn-mod");
    await page.waitForFunction(
      () => { const el = document.getElementById("tab-mod"); return el && !el.hidden; },
      null, { timeout: 15000 });
    await page.fill("#plan-translations", JSON.stringify(VI));
    await page.fill("#plan-entry", VPATH);
    await page.click("#btn-plan-build");
    await page.waitForFunction(
      () => { const el = document.getElementById("plan-list"); return el && el.textContent.includes("en.json"); },
      null, { timeout: 120000 });
    const listTxt = await elText("plan-list");
    const boxes = await page.$$eval("#plan-list input[type=checkbox]", (els) => els.length);
    const hasAll = ["en.json", "menu.json", "dialog.json"].every((n) => listTxt.includes(n)) &&
      listTxt.includes("Xin chào") && boxes === 3;
    rec("browser_plan_build_3_entries", status.includes("Sẵn sàng") && status.includes("20 plugin") && hasAll,
      "status: " + JSON.stringify(status) + "; plan-list có đủ 3 asset + diff Xin chào: " + hasAll +
      " (checkbox: " + boxes + ")", performance.now() - t0);
  }

  { // D2. Bỏ tick 1 entry -> dry-run chỉ báo 2 entry áp
    const t0 = performance.now();
    await page.evaluate(() => {
      const boxes = [...document.querySelectorAll("#plan-list input[type=checkbox]")];
      const row = boxes.find((b) => b.closest("tr") && b.closest("tr").textContent.includes("dialog.json"));
      if (row) { row.checked = false; row.dispatchEvent(new Event("change", { bubbles: true })); }
    });
    await sleep(300);
    await page.click("#btn-plan-dryrun");
    await page.waitForFunction(
      () => { const el = document.getElementById("plan-result"); return el && el.textContent.includes("dry-run"); },
      null, { timeout: 120000 });
    const txt = await elText("plan-result");
    // Kết quả dry-run liệt kê 2 entry đã áp (en + menu) và dialog ở mục "Bỏ qua".
    const skippedDialog = txt.includes("Bỏ qua") && txt.includes("dialog.json");
    rec("browser_plan_untick_dryrun", txt.includes("2 entry đã áp") && skippedDialog,
      "plan-result: " + JSON.stringify(txt.slice(0, 300)), performance.now() - t0);
  }

  { // D3. Nút chọn/bỏ chọn tất cả + tải plan JSON
    const t0 = performance.now();
    const dlPromise = page.waitForEvent("download", { timeout: 120000 }).catch(() => null);
    await page.click("#btn-plan-deselect-all");
    await sleep(300);
    const unchecked = await page.$$eval("#plan-list input[type=checkbox]", (els) => els.filter((e) => !e.checked).length);
    await page.click("#btn-plan-select-all");
    await sleep(300);
    const checked = await page.$$eval("#plan-list input[type=checkbox]", (els) => els.filter((e) => e.checked).length);
    await page.click("#btn-plan-download");
    const dl = await dlPromise;
    let dlOk = false, dlDetail = "không bắt được sự kiện download";
    if (dl) {
      const fname = dl.suggestedFilename();
      const savePath = "/tmp/item8_plan_downloaded.json";
      await dl.saveAs(savePath);
      try {
        const p = JSON.parse(fs.readFileSync(savePath, "utf8"));
        dlOk = fname.startsWith("plan-") && fname.endsWith(".json") && p.version === 1 &&
          Array.isArray(p.entries) && p.entries.length === 3;
        dlDetail = "download: " + fname + " (" + fs.statSync(savePath).size + "B); version=" + p.version + ", entries=" + p.entries.length;
      } catch (e) { dlDetail = "download " + fname + " nhưng không đọc được JSON: " + e.message; }
    }
    rec("browser_plan_select_all_and_download", unchecked === 3 && checked === 3 && dlOk,
      "bỏ chọn tất cả -> chưa tick: " + unchecked + "/3; chọn tất cả -> đã tick: " + checked +
      "/3; " + dlDetail, performance.now() - t0);
  }

  { // D4. 390px: không tràn ngang + không lỗi trang cả phiên
    const t0 = performance.now();
    const overflow = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
    const realErrors = pageErrors.filter((e) => !e.includes("Failed to load resource"));
    rec("browser_390_no_overflow_no_errors", overflow.sw <= overflow.cw && realErrors.length === 0,
      "scrollWidth=" + overflow.sw + " clientWidth=" + overflow.cw +
      "; pageerror/console-error thật: " + (realErrors.join("; ") || "không có"),
      performance.now() - t0);
  }
} finally {
  if (browser) await browser.close().catch(() => {});
  server.close();
}

// ================= TỔNG KẾT =================
const total = Object.keys(results).length;
const passed = Object.values(results).filter((r) => r.ok).length;
fs.writeFileSync(EXP + "/item8_results.json", JSON.stringify(
  { item: 8, passed, failed: total - passed, total, results }, null, 2));
console.log(`\n===== MỤC 8: ${passed}/${total} PASS, ${total - passed} FAIL =====`);
process.exit(failures ? 1 : 0);
