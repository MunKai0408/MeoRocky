// verify_analysis_rebuild.mjs — Kiểm chứng P0-1 (E): ép bản dịch lại vào
// file gốc [bước 5] (BearTea web). (repo MunKai0408/MeoRocky, nhánh main;
// KHÔNG đẩy lên GitHub.)
//
// Chạy: node verify_analysis_rebuild.mjs (từ thư mục exp/ của repo)
// Kết quả: analysis_rebuild_results.json (lần chạy này; báo cáo .md viết
// tay từ cùng lần chạy, KHÔNG gộp kết quả cũ bằng cơ chế ONLY).
//
//  A. Tĩnh: node --check; py_compile; check_analysis_rebuild_static.py
//     (rebuild.py stdlib-only + Python 3.9, không UnityPy/lz4, không
//     expose rebuild_zip, manifest 95/167, precache 120 + v23, bridge có
//     op rebuild_apply, plan.py progress_fn, unity.py streaming + khóa
//     REBUILD_ALL, UI đủ id (E), 2 chỗ innerHTML f.source đã esc).
//  B. Unit test Python: tests.test_rebuild (32) + test_plan + test_unity.
//  C. Node Pyodide + bridge thật (fixture item8_fixture.zip: bundle v7 LZ4,
//     3 TextAsset JSON en/menu/dialog):
//     C1 preview quét-tất-cả (không provenance): 1+ đơn vị, đủ key đổi,
//        không ghi file nào, có cảnh báo quét tất cả.
//     C2 xung đột merge sidecar: mặc định kept; chọn dropped -> choice +
//        warning; key lạ -> ValueError.
//     C3 apply + round-trip: dest có, nội dung khớp bản dịch, sha gốc
//        không đổi; dry_run=False + confirm=False -> E_CONFIG_INVALID.
//     C4 streaming: bundle ~65MB tự sinh (entry TextAsset + filler),
//        rebuild_apply ghi qua đường streaming, nội dung khớp, ghi thời
//        gian + kích thước thật (RAM đo ở hồi quy CPython, không bịa).
//     C5 từ chối: bundle v9, bundle gắn cờ mã hóa, (Zstd -> none + warning
//        chỉ kiểm ở unit test nếu thiếu gói zstandard để sinh fixture).
//  D. Trình duyệt THẬT (Playwright chromium headless shell, 390px, serve
//     site qua HTTP local): D1 flow (E) preview -> cổng (P0-2) ĐẠT -> apply;
//     D2 reselect file khác tên -> cảnh báo; D3 file >400MB -> confirm streaming. Nếu thiếu
//     Playwright/Chromium thì D = [CHƯA KIỂM CHỨNG], ghi rõ, không bịa PASS.
//  S. Vỏ trang sau P0-1 (ghi số thật; ngưỡng gzip ĐÃ chốt 102400B).
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import zlib from "node:zlib";
import { createHash } from "node:crypto";
import { execSync, execFileSync, spawnSync } from "node:child_process";

const ROOT = decodeURIComponent(new URL("..", import.meta.url).pathname).replace(/\/$/, "");
const SITE = ROOT;
const EXP = ROOT + "/exp";
const TMP = (process.env.TMPDIR || "/tmp") + "/rebuild_harness";
const FX = EXP + "/fixtures/item8_fixture.zip";
const BUNDLE = "Payload/9Kings.app/Data/Raw/aa/iOS/localization-locales_assets_all.bundle";
const CAB = "CAB-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const VPATH = BUNDLE + "::" + CAB;
const VI = {
  hello: "Xin chào", play: "Chơi", quit: "Thoát", title: "9 vị vua",
  start: "Bắt đầu chơi", options: "Tùy chọn", exit: "Thoát game",
  greeting: "Chào mừng, anh hùng!", farewell: "Tạm biệt!",
};
// Bản gốc 9 key của fixture (en/menu/dialog) — P0-2: cổng cần nguồn để
// so placeholder/newline; driver D1 nhập làm nguồn cổng (chế độ file).
const EN_SOURCE = {
  hello: "Hello", play: "Play", quit: "Quit", title: "9 Kings",
  start: "Start Game", options: "Options", exit: "Exit",
  greeting: "Welcome, hero!", farewell: "Goodbye!",
};
const VENV_PY = "python3";
const GZIP_THRESHOLD = 102400; // 100*1024, đã chốt ở verify_item6.mjs:86,95
const sec = (ms) => Math.round(ms / 100) / 10;
const results = {};
let failures = 0;
function rec(name, ok, detail, ms) {
  results[name] = { ok, detail, seconds: ms == null ? 0 : sec(ms) };
  if (!ok) failures++;
  console.log(`\n### ${name}: ${ok ? "PASS" : "FAIL"}${ms == null ? "" : " (" + sec(ms) + "s)"}\n${detail}`);
}
const sha256 = (buf) => createHash("sha256").update(buf).digest("hex");
fs.mkdirSync(TMP, { recursive: true });

// ================= A. Tĩnh =================
{ // A1. node --check + py_compile
  const t0 = performance.now();
  const bad = [];
  for (const f of ["app.js", "sw.js", "exp/verify_analysis_rebuild.mjs"]) {
    try { execFileSync("node", ["--check", SITE + "/" + f], { stdio: "pipe" }); }
    catch (e) { bad.push(f); }
  }
  try {
    execFileSync("python3", ["-m", "py_compile", SITE + "/pb3/bridge.py",
      SITE + "/pb3/sdk/rebuild.py", SITE + "/pb3/sdk/unity.py",
      SITE + "/pb3/sdk/plan.py", SITE + "/pb3/tests/test_rebuild.py"],
      { stdio: "pipe", cwd: SITE + "/pb3" });
  } catch (e) { bad.push("py_compile"); }
  rec("static_node_py_check", bad.length === 0,
    "node --check OK: app.js, sw.js, exp/verify_analysis_rebuild.mjs; " +
    "python3 -m py_compile OK: bridge.py, sdk/rebuild.py, sdk/unity.py, " +
    "sdk/plan.py, tests/test_rebuild.py" + (bad.length ? " | LỖI: " + bad.join(", ") : ""),
    performance.now() - t0);
}

{ // A2. Kiểm tra tĩnh chuyên mục (E)
  const t0 = performance.now();
  const r = spawnSync("python3", [EXP + "/check_analysis_rebuild_static.py"],
    { encoding: "utf8" });
  let detail = (r.stdout || "").trim();
  let ok = r.status === 0;
  try {
    const j = JSON.parse(r.stdout || "{}");
    if (j.fails && j.fails.length) { ok = false; detail = j.fails.join(" | "); }
    else detail = "rebuild.py stdlib-only + Python 3.9 + không UnityPy/lz4 + " +
      "không expose rebuild_zip + chốt dest!=source + files.json=95 / " +
      "tests.json=167 + sw.js precache 120 mục có sdk/rebuild.py, không " +
      "wheel chết, CACHE_VERSION v23-20261117 + bridge rebuild_apply + " +
      "plan.py progress_fn + unity.py streaming + khóa REBUILD_ALL + UI " +
      "đủ id (E) + 2 chỗ innerHTML f.source đã esc + render (E) esc";
  } catch (e) { ok = false; detail = "không đọc được JSON: " + (r.stdout || "") + (r.stderr || ""); }
  rec("static_rebuild_checks", ok, detail, performance.now() - t0);
}

// ================= B. Unit test Python =================
{ // B1. tests.test_rebuild + test_plan + test_unity (hồi quy đường chung)
  const t0 = performance.now();
  const r = spawnSync(VENV_PY,
    ["-m", "unittest", "tests.test_rebuild", "tests.test_plan", "tests.test_unity"],
    { cwd: SITE + "/pb3", encoding: "utf8" });
  const out = (r.stdout || "") + (r.stderr || "");
  const m = out.match(/Ran (\d+) tests?/);
  const okRun = /^(OK|OK \(skipped=\d+\))$/m.test(out);
  rec("python_unit_rebuild_plan_unity", r.status === 0 && !!m && okRun,
    "unittest tests.test_rebuild + tests.test_plan + tests.test_unity: " +
    (m ? "Ran " + m[1] + " tests, " : "") + (okRun ? "OK" : "KHÔNG OK") +
    " (exit=" + r.status + ")" + (okRun ? "" : "; đuôi: " + out.slice(-800)),
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
const FX_SHA = sha256(fs.readFileSync(FX));

// Đọc nội dung TextAsset JSON trong bundle đã patch bằng CPython hệ thống.
function readBundleTexts(bundlePath) {
  const code = "import json,sys; sys.path.insert(0,'" + SITE + "/pb3'); " +
    "from sdk import unity as u; from core.limits import Limits; " +
    "b=u.parse_bundle(open('" + bundlePath + "','rb').read(), u.UnityBackend('NATIVE'), Limits()); " +
    "sf=u.parse_serialized(b.get_entry('" + CAB + "'), Limits()); " +
    "print(json.dumps({n: json.loads(s) for _p,n,s in sf.text_assets()}, ensure_ascii=False))";
  return JSON.parse(execSync(VENV_PY + " -c " + JSON.stringify(code), { encoding: "utf8" }));
}

{ // C1. Preview quét-tất-cả (không provenance)
  const t0 = performance.now();
  const r = call("rebuild_apply", INPUT, JSON.stringify(VI));
  const units = r.units || [];
  const changes = units.reduce((a, u) => a + (u.changes || 0), 0);
  const wroteSomething = !!(r.dest || r.zip_path);
  const scanWarn = /quet tat ca|quét tất cả/.test(JSON.stringify(r.warnings || []));
  const ok = r.ok !== false && r.dry_run === true && units.length >= 1 &&
    changes >= 8 && (r.refused || []).length === 0 && !wroteSomething && scanWarn;
  rec("C1_preview_scan_all", ok,
    "rebuild_apply preview (không provenance): ok=" + (r.ok !== false) +
    ", dry_run=" + r.dry_run + ", units=" + units.length +
    " (" + units.map((u) => u.unit + ":" + u.changes).join(", ") +
    "), tổng changes=" + changes + " (kỳ vọng >= 8), refused=" +
    (r.refused || []).length + ", dest/zip_path=" + wroteSomething +
    " (kỳ vọng không ghi), cảnh báo quét-tất-cả=" + scanWarn +
    (r.ok === false ? "; lỗi: " + JSON.stringify(r) : ""),
    performance.now() - t0);
}

{ // C2. Xung đột merge sidecar: kept mặc định, dropped + warning, key lạ
  const t0 = performance.now();
  const keys = {};
  for (const k of Object.keys(VI)) keys[k] = VPATH;
  const sidecar = {
    format: "beartea-merge-provenance/1", keys,
    conflicts: [{ key: "hello",
      kept: { value: "Hello", source: VPATH },
      dropped: { value: "Hola", source: VPATH } }],
    conflicts_total: 1, conflicts_truncated: false,
  };
  const rKept = call("rebuild_apply", INPUT, JSON.stringify(VI), JSON.stringify(sidecar));
  const rDrop = call("rebuild_apply", INPUT, JSON.stringify(VI), JSON.stringify(sidecar),
    JSON.stringify({ hello: "dropped" }));
  const rBad = call("rebuild_apply", INPUT, JSON.stringify(VI), JSON.stringify(sidecar),
    JSON.stringify({ no_such_key: "kept" }));
  const cKept = (rKept.conflicts || [])[0] || {};
  const cDrop = (rDrop.conflicts || [])[0] || {};
  const ok = cKept.choice === "kept" && !cKept.warning &&
    cDrop.choice === "dropped" && typeof cDrop.warning === "string" &&
    cDrop.warning.includes("dropped") &&
    rBad.ok === false && /key la/.test(rBad.error || "");
  rec("C2_conflict_choices", ok,
    "mặc định: choice=" + cKept.choice + ", warning=" + !!cKept.warning +
    "; chọn dropped: choice=" + cDrop.choice + ", warning=" +
    JSON.stringify(cDrop.warning || null) + "; key lạ: ok=" + rBad.ok +
    ", error=" + JSON.stringify(rBad.error || rBad),
    performance.now() - t0);
}

{ // C3. Apply + round-trip + chốt confirm
  const t0 = performance.now();
  const rGate = call("rebuild_apply", INPUT, JSON.stringify(VI), null, null, false, false);
  const gateOk = rGate.ok === false && rGate.code === "E_CONFIG_INVALID";
  const r = call("rebuild_apply", INPUT, JSON.stringify(VI), null, null, false, true);
  let contentOk = false, contentDetail = "";
  if (r.ok !== false && r.dest && fsExists(r.dest)) {
    const outPath = TMP + "/c3_patched.bundle";
    fs.writeFileSync(outPath, Buffer.from(fsRead(r.dest)));
    try {
      const got = readBundleTexts(outPath);
      contentOk = got["en.json"].hello === "Xin chào" && got["en.json"].play === "Chơi" &&
        got["menu.json"].start === "Bắt đầu chơi" &&
        got["dialog.json"].greeting === "Chào mừng, anh hùng!";
      contentDetail = "en.hello=" + got["en.json"].hello + ", menu.start=" + got["menu.json"].start +
        ", dialog.greeting=" + got["dialog.json"].greeting;
    } catch (e) { contentDetail = "không đọc được bundle đã patch: " + e.message; }
  } else {
    contentDetail = "apply lỗi hoặc thiếu dest: " + JSON.stringify(r).slice(0, 300);
  }
  const shaAfter = sha256(Buffer.from(fsRead(INPUT)));
  const ok = gateOk && r.ok !== false && contentOk && shaAfter === FX_SHA &&
    r.source_sha256 === FX_SHA && /KHONG phai IPA|không phải IPA/.test(r.note || "");
  rec("C3_apply_roundtrip_confirm_gate", ok,
    "confirm gate: ok=" + rGate.ok + ", code=" + rGate.code +
    " (kỳ vọng E_CONFIG_INVALID); apply: dest=" + (r.dest || null) +
    ", filename=" + r.filename + "; round-trip: " + contentDetail +
    "; sha file gốc sau apply khớp trước=" + (shaAfter === FX_SHA) +
    "; source_sha256 khớp=" + (r.source_sha256 === FX_SHA) +
    "; note có 'KHONG phai IPA'=" + /KHONG phai IPA|không phải IPA/.test(r.note || ""),
    performance.now() - t0);
}

{ // C4. Streaming: bundle ~65MB tự sinh, ghi qua đường streaming
  const t0 = performance.now();
  const genPy = TMP + "/gen_big.py";
  fs.writeFileSync(genPy, [
    "import os, sys",
    "sys.path.insert(0, '" + SITE + "/pb3')",
    "from tests.fixtures.unity import build_fixtures as bf",
    "import json",
    "sf = bf.build_serialized_file(version=17, objects=[",
    "  (1001, 0, bf.text_asset_data('en.json', json.dumps({'hello': 'Hello', 'play': 'Play'}))),",
    "], types=[(49, None)])",
    "entries = [('" + CAB + "', 0, sf)]",
    "for i in range(13):",
    "    entries.append(('filler-%02d' % i, 0, os.urandom(5 * 1024 * 1024)))",
    "data = bf.build_bundle(version=7, entries=entries, compression='none')",
    "open('" + TMP + "/big.bundle', 'wb').write(data)",
    "print(len(data))",
  ].join("\n"));
  const genOut = spawnSync(VENV_PY, [genPy], { encoding: "utf8" });
  let detail = "", ok = false;
  if (genOut.status !== 0) {
    detail = "không sinh được bundle lớn: " + (genOut.stderr || genOut.stdout || "").slice(-400);
  } else {
    const bigPath = TMP + "/big.bundle";
    const bigSize = fs.statSync(bigPath).size;
    pyodide.FS.writeFile("/tmp/input/big.bundle", new Uint8Array(fs.readFileSync(bigPath)));
    const tApply = performance.now();
    const r = call("rebuild_apply", "/tmp/input/big.bundle",
      JSON.stringify({ hello: "Xin chào", play: "Chơi" }), null, null, false, true);
    const applyMs = performance.now() - tApply;
    const u0 = (r.units || [])[0] || {};
    let contentOk = false, contentErr = "";
    if (r.ok !== false && r.dest && fsExists(r.dest)) {
      const outPath = TMP + "/c4_patched.bundle";
      fs.writeFileSync(outPath, Buffer.from(fsRead(r.dest)));
      try {
        const got = readBundleTexts(outPath);
        contentOk = got["en.json"].hello === "Xin chào" && got["en.json"].play === "Chơi";
      } catch (e) { detail = "không đọc được bundle streaming: " + e.message; contentErr = detail; }
    }
    ok = r.ok !== false && u0.streaming === true && contentOk &&
      u0.size_before === bigSize && typeof u0.size_after === "number";
    detail = "bundle tự sinh " + bigSize + "B (>= 64MiB) gồm 1 CAB TextAsset + 13 filler 5MiB; " +
      "rebuild_apply apply: streaming=" + u0.streaming + ", size_before=" + u0.size_before +
      ", size_after=" + u0.size_after + ", thời gian apply=" + sec(applyMs) + "s" +
      ", round-trip en.json khớp=" + contentOk + (contentErr ? " (" + contentErr + ")" : "") +
      " (RAM đỉnh đo ở hồi quy CPython, không đo trong Pyodide; RAM Safari = [CHƯA KIỂM CHỨNG])" +
      (r.ok === false ? "; lỗi: " + JSON.stringify(r).slice(0, 300) : "");
  }
  rec("C4_streaming_large_bundle", ok, detail, performance.now() - t0);
}

{ // C5. Từ chối: v9, mã hóa, (Zstd ở unit test nếu thiếu zstandard)
  const t0 = performance.now();
  const genPy = TMP + "/gen_refuse.py";
  fs.writeFileSync(genPy, [
    "import sys, json",
    "sys.path.insert(0, '" + SITE + "/pb3')",
    "from tests.fixtures.unity import build_fixtures as bf",
    "sf = bf.build_serialized_file(version=17, objects=[",
    "  (1001, 0, bf.text_asset_data('en.json', json.dumps({'hello': 'Hello'}))),",
    "], types=[(49, None)])",
    "open('" + TMP + "/v9.bundle', 'wb').write(bf.build_bundle(version=9, entries=[('" + CAB + "', 0, sf)], compression='none'))",
    "open('" + TMP + "/enc.bundle', 'wb').write(bf.build_bundle(version=7, entries=[('" + CAB + "', 0, sf)], compression='none', extra_data_flags=0x200))",
    "try:",
    "    open('" + TMP + "/zstd.bundle', 'wb').write(bf.build_bundle(version=7, entries=[('" + CAB + "', 0, sf)], compression='zstd'))",
    "    print('zstd-ok')",
    "except RuntimeError as e:",
    "    print('zstd-skip: ' + str(e))",
  ].join("\n"));
  const genOut = spawnSync(VENV_PY, [genPy], { encoding: "utf8" });
  const zstdBuilt = /zstd-ok/.test(genOut.stdout || "");
  let v9Ok = false, v9Detail = "", encOk = false, encDetail = "", zstdDetail = "không sinh được fixture Zstd (thiếu gói zstandard) — Zstd->none+warning chỉ kiểm ở unit test test_rebuild.";
  const probe = (name) => {
    const p = TMP + "/" + name;
    pyodide.FS.writeFile("/tmp/input/" + name, new Uint8Array(fs.readFileSync(p)));
    return call("rebuild_apply", "/tmp/input/" + name, JSON.stringify({ hello: "Xin chào" }));
  };
  if (genOut.status === 0) {
    const rV9 = probe("v9.bundle");
    const refusedV9 = (rV9.refused || [])[0] || {};
    v9Ok = (rV9.ok === false && /UNSUPPORTED/.test(rV9.code || "")) ||
      /UNSUPPORTED/.test(refusedV9.code || "");
    v9Detail = "v9: ok=" + rV9.ok + ", code=" + rV9.code + ", refused=" + JSON.stringify(rV9.refused || []);
    const rEnc = probe("enc.bundle");
    const refusedEnc = (rEnc.refused || [])[0] || {};
    encOk = (rEnc.ok === false && rEnc.code === "E_ENCRYPTED") ||
      refusedEnc.code === "E_ENCRYPTED";
    encDetail = "mã hóa: ok=" + rEnc.ok + ", code=" + rEnc.code + ", refused=" + JSON.stringify(rEnc.refused || []);
    if (zstdBuilt) {
      const rZ = probe("zstd.bundle");
      const u0 = (rZ.units || [])[0] || {};
      const warned = JSON.stringify(rZ).includes("Zstd") || JSON.stringify(rZ).includes("zstd");
      zstdDetail = "zstd: ok=" + (rZ.ok !== false) + ", mode=" + u0.mode + ", warning nhắc Zstd=" + warned +
        ", refused=" + JSON.stringify(rZ.refused || []);
    }
  } else {
    v9Detail = encDetail = "không sinh được fixture từ chối: " + (genOut.stderr || "").slice(-300);
  }
  rec("C5_refusals_v9_encrypted", v9Ok && encOk,
    v9Detail + "; " + encDetail + "; " + zstdDetail + " (gen stdout: " + JSON.stringify((genOut.stdout || "").trim()) + ")",
    performance.now() - t0);
}

// ================= D. Trình duyệt thật 390px =================
const MIME = { ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript",
  ".json": "application/json", ".css": "text/css", ".webmanifest": "application/manifest+json",
  ".wasm": "application/wasm", ".png": "image/png", ".b64": "text/plain", ".cs": "text/plain" };
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
const D_UNTESTED = "[CHƯA KIỂM CHỨNG] Playwright/Chromium không có trong môi trường này — flow trình duyệt (E) chưa được kiểm trên trình duyệt thật, không bịa PASS. Xem bước thử Safari trong báo cáo.";
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
  const dialogs = [];
  page.on("dialog", async (d) => { dialogs.push(d.message()); await d.accept(); });
  fs.writeFileSync(TMP + "/vi.json", JSON.stringify(VI, null, 2));
  fs.writeFileSync(TMP + "/en_source.json", JSON.stringify(EN_SOURCE, null, 2));

  { // D1. Flow (E): chọn fixture -> boot -> tab Mod -> nhập JSON -> preview -> cổng ĐẠT -> apply
    const t0 = performance.now();
    await page.goto("http://127.0.0.1:" + PORT + "/index.html");
    await page.setInputFiles("#file-input", FX);
    await page.waitForFunction(
      (n) => { const el = document.getElementById("file-info"); return el && el.textContent.includes(n); },
      "item8_fixture.zip", { timeout: 120000 });
    await waitReady();
    await page.click("#btn-nav-tools");
    await page.click("#btn-mod");
    await page.waitForFunction(
      () => { const el = document.getElementById("tab-mod"); return el && !el.hidden; },
      null, { timeout: 15000 });
    const applyDisabledBefore = await page.evaluate(() => document.getElementById("btn-rebuild-apply").disabled);
    await page.setInputFiles("#rebuild-json-file", TMP + "/vi.json");
    await page.waitForFunction(
      () => { const el = document.getElementById("rebuild-source-info"); return el && el.textContent.includes("vi.json"); },
      null, { timeout: 30000 });
    await page.click("#btn-rebuild-preview");
    await page.waitForFunction(
      () => { const el = document.getElementById("rebuild-result"); return el && el.textContent.includes("XEM TRƯỚC"); },
      null, { timeout: 120000 });
    const previewTxt = await elText("rebuild-result");
    // P0-2: nút Ghi vẫn TẮT sau preview khi chưa có verdict cổng; chạy
    // cổng (nguồn = file en_source.json, font = Be Vietnam Pro đi kèm)
    // rồi nút mới bật khi cổng ĐẠT.
    const applyDisabledAfterPreview = await page.evaluate(() => document.getElementById("btn-rebuild-apply").disabled);
    await page.selectOption("#gate-source-mode", "file");
    await page.setInputFiles("#gate-source-file", TMP + "/en_source.json");
    await page.waitForFunction(
      () => { const el = document.getElementById("gate-source-info"); return el && el.textContent.includes("en_source.json"); },
      null, { timeout: 30000 });
    await page.selectOption("#gate-font-mode", "donor");
    await page.click("#btn-gate-run");
    await page.waitForFunction(
      () => { const el = document.getElementById("gate-result"); return el && el.textContent.includes("ĐẠT — được giao file"); },
      null, { timeout: 120000 });
    const gateTxt = await elText("gate-result");
    const gatePass = gateTxt.includes("ĐẠT — được giao file");
    const applyEnabled = await page.evaluate(() => !document.getElementById("btn-rebuild-apply").disabled);
    const dlPromise = page.waitForEvent("download", { timeout: 120000 }).catch(() => null);
    await page.click("#btn-rebuild-apply");
    await page.waitForFunction(
      () => { const el = document.getElementById("rebuild-result"); return el && el.textContent.includes("ĐÃ GHI"); },
      null, { timeout: 120000 });
    const applyTxt = await elText("rebuild-result");
    const dl = await dlPromise;
    let dlOk = false;
    if (dl) {
      const savePath = TMP + "/d1_rebuild_out.bin";
      await dl.saveAs(savePath);
      dlOk = fs.statSync(savePath).size > 0;
    }
    rec("browser_rebuild_preview_apply", applyDisabledBefore === true && applyDisabledAfterPreview === true && gatePass && applyEnabled &&
      previewTxt.includes("XEM TRƯỚC") && /đơn vị/.test(previewTxt) &&
      applyTxt.includes("ĐÃ GHI") && dlOk,
      "nút Ghi tắt trước preview=" + applyDisabledBefore + ", vẫn tắt sau preview chưa qua cổng=" + applyDisabledAfterPreview +
      ", cổng ĐẠT=" + gatePass + " (font donor, nguồn en_source.json), bật sau cổng ĐẠT=" + applyEnabled +
      "; cổng: " + JSON.stringify(gateTxt.slice(0, 200)) +
      "; preview: " + JSON.stringify(previewTxt.slice(0, 200)) +
      "; apply: " + JSON.stringify(applyTxt.slice(0, 200)) + "; download nhận được=" + dlOk,
      performance.now() - t0);
  }

  { // D2. Reselect file khác tên so với sidecar -> cảnh báo + confirm
    const t0 = performance.now();
    // Sidecar ghi tên file lúc trích xuất là item8_fixture.zip.
    const sidecarKeys = {};
    for (const k of Object.keys(VI)) sidecarKeys[k] = VPATH;
    fs.writeFileSync(TMP + "/vi.provenance.json", JSON.stringify({
      format: "beartea-merge-provenance/1", gameFile: "item8_fixture.zip",
      keys: sidecarKeys, conflicts: [], conflicts_total: 0,
    }));
    await page.setInputFiles("#rebuild-sidecar-file", TMP + "/vi.provenance.json");
    await page.waitForFunction(
      () => { const el = document.getElementById("rebuild-source-info"); return el && el.textContent.includes("sidecar"); },
      null, { timeout: 30000 });
    const otherPath = TMP + "/ten-khac.zip";
    fs.copyFileSync(FX, otherPath);
    dialogs.length = 0;
    await page.setInputFiles("#file-input", otherPath);
    await page.waitForFunction(
      (n) => { const el = document.getElementById("file-info"); return el && el.textContent.includes(n); },
      "ten-khac.zip", { timeout: 120000 });
    await page.click("#btn-rebuild-preview");
    await page.waitForFunction(
      () => { const el = document.getElementById("rebuild-result"); return el && el.textContent.includes("XEM TRƯỚC"); },
      null, { timeout: 120000 });
    const infoTxt = await elText("rebuild-file-info");
    const warnShown = /CẢNH BÁO: tên khác lúc trích xuất/.test(infoTxt);
    const confirmShown = dialogs.some((m) => /khác tên lúc trích xuất/.test(m));
    rec("browser_rebuild_reselect_mismatch", warnShown && confirmShown,
      "sau khi chọn lại file tên khác (ten-khac.zip) rồi preview: rebuild-file-info: " +
      JSON.stringify(infoTxt.slice(0, 300)) + "; confirm khác tên hiện=" + confirmShown +
      " (" + JSON.stringify(dialogs) + ")",
      performance.now() - t0);
  }

  { // D3. File >400MB -> confirm streaming trước khi preview
    const t0 = performance.now();
    // Đuôi .unity3d (được hỗ trợ) + bấm "Tiếp tục" ở modal xác nhận file
    // lớn (C4) như harness c4/reselect vẫn làm: .bin bị cổng đuôi từ chối
    // trước khi đọc, và modal C4 chặn tới khi user quyết — cả hai đều là
    // hành vi đúng của app, harness phải lái đúng UI thì D3 mới tới được
    // confirm streaming của (E). Assertion của D3 không đổi.
    const bigFile = TMP + "/lon-401mb.unity3d";
    if (!fs.existsSync(bigFile)) {
      const fd = fs.openSync(bigFile, "w");
      fs.writeSync(fd, Buffer.alloc(1024 * 1024, 0), 0, 1024 * 1024, 401 * 1024 * 1024 - 1024 * 1024);
      fs.closeSync(fd);
    }
    dialogs.length = 0;
    await page.setInputFiles("#file-input", bigFile);
    await page.waitForFunction(
      () => !document.getElementById("large-file-modal").hidden,
      null, { timeout: 30000 });
    await page.click("#large-file-continue");
    await page.waitForFunction(
      () => { const el = document.getElementById("file-info"); return el && el.textContent.includes("lon-401mb.unity3d"); },
      null, { timeout: 180000 });
    await page.setInputFiles("#rebuild-json-file", TMP + "/vi.json");
    await page.click("#btn-rebuild-preview");
    await page.waitForTimeout(5000);
    const confirmShown = dialogs.some((m) => /400|streaming|RAM/.test(m));
    rec("browser_rebuild_over_400mb_confirm", confirmShown,
      "dialogs khi preview file 401MB: " + JSON.stringify(dialogs) +
      " (kỳ vọng có confirm nhắc 400MB/streaming/RAM)",
      performance.now() - t0);
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
} catch (e) {
  // Thiếu Playwright/Chromium (hoặc lỗi boot trình duyệt): D = chưa kiểm chứng.
  if (!browser) {
    for (const name of ["browser_rebuild_preview_apply", "browser_rebuild_reselect_mismatch",
      "browser_rebuild_over_400mb_confirm", "browser_390_no_overflow_no_errors"]) {
      rec(name, false, D_UNTESTED + " Lỗi môi trường: " + e.message);
    }
  } else {
    throw e;
  }
} finally {
  if (browser) await browser.close().catch(() => {});
  server.close();
}

// ================= S. Vỏ trang sau P0-1 (ghi số thật) =================
{ // S1. Shell tĩnh (cùng cách đo mục 6: tổng raw + gzip 7 file lần tải đầu)
  const t0 = performance.now();
  const files = ["index.html", "kb-store.js", "worker-pool.js", "stream.js", "opfs.js", "app.js", "gt.js"];
  let raw = 0, gz = 0;
  const lines = [];
  for (const f of files) {
    const buf = fs.readFileSync(SITE + "/" + f);
    const g = zlib.gzipSync(buf, { level: 9 }).length;
    raw += buf.length; gz += g;
    lines.push(f + " raw=" + buf.length + "B gzip=" + g + "B");
  }
  rec("shell_size_after_rebuild", true,
    "shell lần tải đầu sau P0-1: " + lines.join("; ") + " | TỔNG raw=" + raw + "B (" +
    (raw / 1024).toFixed(1) + "KB), gzip=" + gz + "B (" + (gz / 1024).toFixed(1) +
    "KB). Ngưỡng ĐÃ chốt " + GZIP_THRESHOLD + "B: raw " + (raw <= GZIP_THRESHOLD ? "đạt" : "CHƯA đạt") +
    ", gzip " + (gz <= GZIP_THRESHOLD ? "đạt" : "CHƯA đạt") +
    " — ĐÍNH CHÍNH: nhãn \"(gzip đạt)\" ở báo cáo (B)/(C)/(D) là SAI nếu hiểu là gzip ≤ ngưỡng khi số đo thật không đạt; " +
    "số thật ở đây là căn cứ duy nhất. P0-1 thêm code UI (E) nên app.js tăng so với trước.",
    performance.now() - t0);
}

// ================= TỔNG KẾT =================
const total = Object.keys(results).length;
const failed = Object.keys(results).filter((k) => !results[k].ok);
fs.writeFileSync(EXP + "/analysis_rebuild_results.json",
  JSON.stringify({ total, failures: failed, results }, null, 2));
console.log("\n==== TỔNG: " + total + " mục, " + failed.length + " FAIL ====");
if (failed.length) { console.log("FAIL: " + failed.join(", ")); process.exit(1); }
