// verify_analysis_c8.mjs — Kiểm chứng MỤC C8: format probe cho engine lạ (BearTea web).
// (repo MunKai0408/MeoRocky, nhánh main; KHÔNG đẩy lên GitHub.)
//
// Chạy: node verify_analysis_c8.mjs (từ thư mục exp/ của repo)
// Kết quả: analysis_c8_results.json (+ analysis_c8_results.md báo cáo tay).
//
//  A. Tĩnh: node --check; py_compile; check_analysis_c8_static.py
//     (format_probe.py stdlib-only + Python 3.9, không gọi mạng, không chuỗi
//     cấm; ENGINE_SUPPORT trung thực — mọi engine đều có unsupported, unknown
//     có supported rỗng; manifest files.json=95 / tests.json=167; sw.js
//     precache + CACHE_VERSION v20; bridge có op format_probe; UI đủ nút
//     probe + input file riêng + nút tải JSON; tiếng Việt đủ dấu).
//  B. Unit test Python: tests.test_format_probe (22) + tests.test_engine_detect
//     (35, hồi quy đường discovery chung).
//  C. Node Pyodide + bridge thật:
//     C0 BASELINE (hành vi cũ, vẫn giữ): Liệt kê entry trên file lạ ->
//        ok:false E_UNSUPPORTED_FORMAT — chỉ một dòng lỗi, không báo cáo.
//     C1-C10 format_probe trên từng fixture: Godot PCK v2 (CHẮC CHẮN),
//        Godot .translation TR (CÓ KHẢ NĂNG), Unreal PAK v8 (CHẮC CHẮN qua
//        footer), Unreal .locres v3 (CHẮC CHẮN), GameMaker data.win
//        (CHẮC CHẮN, chỉ nhận diện), Cocos ZIP (CÓ KHẢ NĂNG, chỉ nhận diện),
//        Defold ZIP (CÓ KHẢ NĂNG, chỉ nhận diện), byte ngẫu nhiên (unknown,
//        supported rỗng, ok:true), ZIP thường không phải game (unknown),
//        UnityFS head (CHẮC CHẮN).
//  D. Trình duyệt THẬT (Playwright chromium headless shell, 390px, serve
//     site qua HTTP local), tab Chẩn đoán:
//     D1 Probe file .pck qua input riêng -> thẻ Godot CHẮC CHẮN + Hỗ trợ được.
//     D2 Probe byte ngẫu nhiên -> “chưa hỗ trợ” có cấu trúc, không crash,
//        không pageerror; nút Tải báo cáo JSON cho file đúng schema.
//     D3 Probe file .pak qua input riêng -> Unreal CHẮC CHẮN.
//     D4 Probe qua file chính (ZIP Cocos, không chọn file riêng) -> Cocos
//        CÓ KHẢ NĂNG.
//     D5 Liệt kê file .gz lạ -> banner lỗi CÓ gợi ý “Probe định dạng”.
//     D6 390px không tràn ngang, không pageerror cả phiên.
//  S. Vỏ trang sau C8 (ghi số thật, không đặt ngưỡng mới — FAIL item6 đã biết).
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import zlib from "node:zlib";
import { execFileSync, spawnSync } from "node:child_process";

const ROOT = decodeURIComponent(new URL("..", import.meta.url).pathname).replace(/\/$/, "");
const SITE = ROOT;
const EXP = ROOT + "/exp";
const FX = EXP + "/fixtures";
const VENV_PY = "python3";
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
      SITE + "/pb3/sdk/format_probe.py", SITE + "/pb3/tests/test_format_probe.py"],
      { stdio: "pipe", cwd: SITE + "/pb3" });
  } catch (e) { bad.push("py_compile"); }
  rec("static_node_py_check", bad.length === 0,
    "node --check OK: app.js, sw.js; python3 -m py_compile OK: bridge.py, " +
    "sdk/format_probe.py, tests/test_format_probe.py" + (bad.length ? " | LỖI: " + bad.join(", ") : ""),
    performance.now() - t0);
}

{ // A2. Kiểm tra tĩnh chuyên mục C8
  const t0 = performance.now();
  const r = spawnSync("python3", [EXP + "/check_analysis_c8_static.py"],
    { encoding: "utf8" });
  let detail = (r.stdout || "").trim();
  let ok = r.status === 0;
  try {
    const j = JSON.parse(r.stdout || "{}");
    if (j.fails && j.fails.length) { ok = false; detail = j.fails.join(" | "); }
    else detail = "format_probe.py stdlib-only + Python 3.9 + không gọi mạng " +
      "+ ENGINE_SUPPORT trung thực (mọi engine có unsupported, unknown " +
      "supported rỗng) + files.json=95 / tests.json=167 + sw.js precache " +
      "sdk/format_probe.py + CACHE_VERSION v23-20261117 + bridge format_probe " +
      "+ UI đủ nút probe/input riêng/tải JSON + gợi ý probe khi lỗi " +
      "E_UNSUPPORTED_FORMAT + tiếng Việt đủ dấu";
  } catch (e) { ok = false; detail = "không đọc được JSON: " + (r.stdout || "") + (r.stderr || ""); }
  rec("static_c8_checks", ok, detail, performance.now() - t0);
}

// ================= B. Unit test Python =================
{ // B1. tests.test_format_probe (22) + tests.test_engine_detect (35)
  const t0 = performance.now();
  const r = spawnSync(VENV_PY,
    ["-m", "unittest", "tests.test_format_probe", "tests.test_engine_detect"],
    { cwd: SITE + "/pb3", encoding: "utf8" });
  const out = (r.stdout || "") + (r.stderr || "");
  const m = out.match(/Ran (\d+) tests?/);
  const okRun = /OK$/.test(out.trim()) || /\nOK\n?$/.test(out);
  rec("python_unit_format_probe_engine_detect", r.status === 0 && !!m && okRun,
    "unittest tests.test_format_probe + tests.test_engine_detect: " +
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
function loadInput(name) {
  pyodide.FS.writeFile("/tmp/input/" + name, new Uint8Array(fs.readFileSync(FX + "/" + name)));
  return "/tmp/input/" + name;
}
const probe = (name) => call("format_probe", loadInput(name));

{ // C0. BASELINE hành vi cũ: Liệt kê entry trên file lạ -> lỗi E_UNSUPPORTED_FORMAT
  const t0 = performance.now();
  const r = call("list_entries", loadInput("c8_unknown.bin"));
  rec("C0_baseline_list_unknown_file_errors", r.ok === false && r.code === "E_UNSUPPORTED_FORMAT",
    "list_entries(c8_unknown.bin) -> ok=" + r.ok + ", code=" + r.code +
    ", error=" + JSON.stringify(r.error) + " — đây là hành vi CŨ (chỉ một dòng lỗi, " +
    "không báo cáo cấu trúc); probe C8 sinh ra để thay thế đúng chỗ này.",
    performance.now() - t0);
}

function checkProbe(name, file, want) {
  const t0 = performance.now();
  const r = call("format_probe", loadInput(file));
  const rep = r.ok === true ? r.report : null;
  const sup = rep ? rep.supported.map((s) => s.op).join(" | ") : "";
  const unsup = rep ? rep.unsupported.map((u) => u.op + " — " + u.reason).join(" | ") : "";
  const ok = r.ok === true && !!rep && rep.format === "pb3-format-probe" &&
    rep.probe_version === 1 &&
    rep.engine.id === want.engine && rep.engine.level === want.level &&
    (want.supported ? want.supported.every((s) => sup.includes(s)) : true) &&
    (want.unsupported ? want.unsupported.every((s) => unsup.includes(s)) : true) &&
    (want.extra ? want.extra(rep) : true);
  rec(name, ok,
    "format_probe(" + file + ") -> ok=" + r.ok +
    (rep ? "; engine=" + rep.engine.id + " (" + rep.engine.level + ", conf=" + rep.engine.confidence + ")" +
      "; container=" + rep.file.container_kind + ", magic=" + (rep.file.magic_head || "không có") +
      "; entries=" + rep.entries.total + "; supported=[" + sup.slice(0, 120) + "]" +
      "; unsupported=[" + unsup.slice(0, 120) + "]" +
      "; next_steps=" + rep.next_steps.length : "; lỗi: " + JSON.stringify(r).slice(0, 300)),
    performance.now() - t0);
}
checkProbe("C1_probe_godot_pck_certain", "c8_godot.pck",
  { engine: "godot", level: "CHẮC CHẮN", supported: ["pck v1/v2"],
    unsupported: ["Trích xuất text game Godot"] });
checkProbe("C2_probe_godot_translation_likely", "c8_godot_translation.translation",
  { engine: "godot", level: "CÓ KHẢ NĂNG", supported: [".translation"],
    unsupported: ["RSRC"] });
checkProbe("C3_probe_unreal_pak_certain", "c8_unreal_pak.pak",
  { engine: "unreal", level: "CHẮC CHẮN", supported: [".pak UE4/UE5"],
    unsupported: ["Oodle"],
    extra: (rep) => rep.file.pak_footer === true && rep.file.container_opened === true });
checkProbe("C4_probe_unreal_locres_certain", "c8_unreal_locres.locres",
  { engine: "unreal", level: "CHẮC CHẮN", supported: [".locres v0"] });
checkProbe("C5_probe_gamemaker_datawin_certain_detect_only", "c8_gamemaker_data.win",
  { engine: "gamemaker", level: "CHẮC CHẮN", supported: ["Nhận diện"],
    unsupported: ["Mở data.win"],
    extra: (rep) => rep.supported.every((s) => s.op.includes("Nhận diện")) });
checkProbe("C6_probe_cocos_zip_likely_detect_only", "c8_cocos.zip",
  { engine: "cocos", level: "CÓ KHẢ NĂNG", supported: ["Nhận diện"],
    unsupported: ["chưa có backend Cocos"],
    extra: (rep) => rep.supported.every((s) => s.op.includes("Nhận diện")) });
checkProbe("C7_probe_defold_zip_likely_detect_only", "c8_defold.zip",
  { engine: "defold", level: "CÓ KHẢ NĂNG", supported: ["Nhận diện"],
    unsupported: ["chưa có backend Defold"] });
checkProbe("C8_probe_random_bytes_unknown_structured", "c8_unknown.bin",
  { engine: "unknown", level: "CHƯA ĐỦ BẰNG CHỨNG",
    extra: (rep) => rep.supported.length === 0 && rep.unsupported.length > 0 &&
      rep.next_steps.length > 0 && rep.file.sha256.length === 64 });
checkProbe("C9_probe_plain_zip_unknown", "c8_plain.zip",
  { engine: "unknown", level: "CHƯA ĐỦ BẰNG CHỨNG",
    extra: (rep) => rep.supported.length === 0 && rep.file.container_opened === true &&
      rep.entries.total === 2 });
checkProbe("C10_probe_unity_head_certain", "c8_unity.unity3d",
  { engine: "unity", level: "CHẮC CHẮN", supported: ["Trích xuất text"] });

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
  const lastProbe = () => page.evaluate(() => window.__PB3_LAST_PROBE || null);
  const openDiagnose = async () => {
    await page.click("#btn-nav-tools");
    await page.click("#btn-diagnose");
    await page.waitForFunction(
      () => { const el = document.getElementById("tab-diagnose"); return el && !el.hidden; },
      null, { timeout: 15000 });
  };
  const probeViaInput = async (file) => {
    await page.setInputFiles("#probe-file-input", file);
    await page.click("#btn-probe-run");
    await page.waitForFunction(
      () => window.__PB3_LAST_PROBE != null,
      null, { timeout: 120000 });
    // Đợi render xong (thẻ có chữ "Bước tiếp theo").
    await page.waitForFunction(
      () => { const el = document.getElementById("probe-result"); return el && el.textContent.includes("Bước tiếp theo"); },
      null, { timeout: 15000 });
  };

  { // D0. Mở trang + boot (chọn file chính bất kỳ để boot chạy nền)
    const t0 = performance.now();
    await page.goto("http://127.0.0.1:" + PORT + "/index.html");
    await page.setInputFiles("#file-input", FX + "/c8_cocos.zip");
    await page.waitForFunction(
      (n) => { const el = document.getElementById("file-info"); return el && el.textContent.includes(n); },
      "c8_cocos.zip", { timeout: 120000 });
    await page.waitForFunction(
      () => { const el = document.getElementById("status"); return el && el.textContent.includes("Sẵn sàng"); },
      null, { timeout: 240000 });
    const status = await elText("status");
    rec("browser_boot_ready", status.includes("Sẵn sàng") && status.includes("20 plugin"),
      "status: " + JSON.stringify(status), performance.now() - t0);
  }

  { // D1. Probe .pck qua input riêng -> Godot CHẮC CHẮN + Hỗ trợ được
    const t0 = performance.now();
    await openDiagnose();
    await probeViaInput(FX + "/c8_godot.pck");
    const txt = await elText("probe-result");
    const rep = await lastProbe();
    rec("browser_probe_godot_pck", rep && rep.engine.id === "godot" &&
      rep.engine.level === "CHẮC CHẮN" && txt.includes("Godot") &&
      txt.includes("CHẮC CHẮN") && txt.includes("Hỗ trợ được") &&
      txt.includes("pck v1/v2") && txt.includes("Trích xuất text game Godot"),
      "engine=" + (rep && rep.engine.id) + " (" + (rep && rep.engine.level) + "); thẻ hiện: Godot + " +
      "CHẮC CHẮN + Hỗ trợ được (pck v1/v2) + Chưa hỗ trợ (Trích xuất text game Godot)",
      performance.now() - t0);
  }

  { // D2. Probe byte ngẫu nhiên -> “chưa hỗ trợ” có cấu trúc + tải JSON đúng schema
    const t0 = performance.now();
    await page.evaluate(() => { window.__PB3_LAST_PROBE = null; });
    await probeViaInput(FX + "/c8_unknown.bin");
    const txt = await elText("probe-result");
    const rep = await lastProbe();
    const dlPromise = page.waitForEvent("download", { timeout: 120000 }).catch(() => null);
    await page.click("#btn-probe-download");
    const dl = await dlPromise;
    let dlOk = false, dlDetail = "không bắt được sự kiện download";
    if (dl) {
      const fname = dl.suggestedFilename();
      const savePath = "/tmp/c8_browser_probe_unknown.json";
      await dl.saveAs(savePath);
      try {
        const j = JSON.parse(fs.readFileSync(savePath, "utf8"));
        const keys = ["file", "engine", "candidates", "entries", "open_error",
          "supported", "unsupported", "next_steps", "strings", "plugins_checked", "notes"];
        dlOk = fname === "beartea-probe-c8_unknown.bin.json" &&
          j.format === "pb3-format-probe" && j.probe_version === 1 &&
          j.engine.id === "unknown" && Array.isArray(j.supported) && j.supported.length === 0 &&
          j.unsupported.length > 0 && j.next_steps.length > 0 &&
          keys.every((k) => k in j) && j.file.sha256.length === 64;
        dlDetail = "download: " + fname + " (" + fs.statSync(savePath).size + "B); schema đủ " +
          keys.length + " trường, engine=unknown, supported=[], unsupported=" + j.unsupported.length;
      } catch (e) { dlDetail = "download " + fname + " nhưng JSON lỗi: " + e.message; }
    }
    rec("browser_probe_unknown_structured_download", rep && rep.engine.id === "unknown" &&
      txt.includes("Chưa rõ") && txt.includes("CHƯA ĐỦ BẰNG CHỨNG") &&
      txt.includes("Không có thao tác nào được hỗ trợ") && dlOk,
      "engine=unknown, thẻ hiện “Chưa rõ / CHƯA ĐỦ BẰNG CHỨNG / Không có thao tác nào " +
      "được hỗ trợ”; " + dlDetail, performance.now() - t0);
  }

  { // D3. Probe .pak qua input riêng -> Unreal CHẮC CHẮN
    const t0 = performance.now();
    await page.evaluate(() => { window.__PB3_LAST_PROBE = null; });
    await probeViaInput(FX + "/c8_unreal_pak.pak");
    const txt = await elText("probe-result");
    const rep = await lastProbe();
    rec("browser_probe_unreal_pak", rep && rep.engine.id === "unreal" &&
      rep.engine.level === "CHẮC CHẮN" && txt.includes("Unreal") &&
      txt.includes("CHẮC CHẮN") && txt.includes(".pak UE4/UE5"),
      "engine=" + (rep && rep.engine.id) + " (" + (rep && rep.engine.level) + "); thẻ hiện: Unreal + " +
      "CHẮC CHẮN + Hỗ trợ được (.pak UE4/UE5)", performance.now() - t0);
  }

  { // D4. Probe qua file chính (ZIP Cocos, không chọn file riêng) -> Cocos CÓ KHẢ NĂNG
    const t0 = performance.now();
    await page.evaluate(() => {
      window.__PB3_LAST_PROBE = null;
      const inp = document.getElementById("probe-file-input");
      if (inp) inp.value = "";
    });
    await page.click("#btn-probe-run");
    await page.waitForFunction(
      () => window.__PB3_LAST_PROBE != null,
      null, { timeout: 120000 });
    await page.waitForFunction(
      () => { const el = document.getElementById("probe-result"); return el && el.textContent.includes("Bước tiếp theo"); },
      null, { timeout: 15000 });
    const txt = await elText("probe-result");
    const rep = await lastProbe();
    rec("browser_probe_main_file_cocos", rep && rep.engine.id === "cocos" &&
      rep.engine.level === "CÓ KHẢ NĂNG" && rep.file.name === "c8_cocos.zip" &&
      txt.includes("Cocos") && txt.includes("CÓ KHẢ NĂNG") &&
      txt.includes("chưa có backend Cocos"),
      "không chọn file riêng -> probe dùng file chính c8_cocos.zip; engine=" +
      (rep && rep.engine.id) + " (" + (rep && rep.engine.level) + "); thẻ hiện Cocos + " +
      "CÓ KHẢ NĂNG + Chưa hỗ trợ (chưa có backend Cocos)", performance.now() - t0);
  }

  { // D5. Liệt kê file .gz lạ -> banner lỗi CÓ gợi ý “Probe định dạng”
    const t0 = performance.now();
    await page.setInputFiles("#file-input", FX + "/c8_fake.gz");
    await page.waitForFunction(
      (n) => { const el = document.getElementById("file-info"); return el && el.textContent.includes(n); },
      "c8_fake.gz", { timeout: 120000 });
    await page.click("#btn-nav-tools");
    await page.click("#btn-list");
    await page.waitForFunction(
      () => { const el = document.getElementById("tab-list"); return el && !el.hidden; },
      null, { timeout: 15000 });
    await page.click("#btn-list-run");
    await page.waitForFunction(
      () => { const el = document.getElementById("error"); return el && el.textContent.length > 0; },
      null, { timeout: 120000 });
    const err = await elText("error");
    rec("browser_list_unknown_suggests_probe", err.includes("Probe định dạng") &&
      err.includes("tab Chẩn đoán"),
      "banner lỗi sau khi Liệt kê c8_fake.gz: " + JSON.stringify(err.slice(0, 300)),
      performance.now() - t0);
  }

  { // D6. 390px: không tràn ngang + không lỗi trang cả phiên
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

// ================= S. Vỏ trang sau C8 (ghi số thật) =================
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
  rec("shell_size_after_c8", true,
    "shell lần tải đầu sau C8: " + lines.join("; ") + " | TỔNG raw=" + raw + "B (" +
    (raw / 1024).toFixed(1) + "KB), gzip=" + gz + "B (" + (gz / 1024).toFixed(1) +
    "KB). Ngưỡng raw 100KB vẫn chưa đạt như mục 6 đã ghi (app.js monolith) — C8 không đổi kết luận.",
    performance.now() - t0);
}

// ================= TỔNG KẾT =================
const total = Object.keys(results).length;
const failed = Object.keys(results).filter((k) => !results[k].ok);
fs.writeFileSync(EXP + "/analysis_c8_results.json",
  JSON.stringify({ total, failures: failed, results }, null, 2));
console.log("\n==== TỔNG: " + total + " mục, " + failed.length + " FAIL ====");
if (failed.length) { console.log("FAIL: " + failed.join(", ")); process.exit(1); }
