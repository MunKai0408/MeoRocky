// verify_item7.mjs — Kiểm chứng MỤC 7: font atlas build-time (Unity Editor).
// Web đọc tham số TMP_FontAsset + convert bảng glyph (kế hoạch glyph) +
// script C# cho Unity Editor; KHÔNG đẩy lên GitHub.
//
// Chạy: node verify_item7.mjs (từ thư mục exp/ của repo)
// Kết quả: item7_results.json (bản này) + item7_results.md (báo cáo tay).
//
//  A. Tĩnh: node --check; py_compile; check_item7_static.py (stdlib-only +
//     Python 3.9, mảng codepoint C# khớp Python 134+8, API TMP đủ, không
//     fallback C7, không msdf-atlas-gen, manifest/precache/CACHE_VERSION).
//  B. Fixture studio (giả lập + byte thật DATA WING): run_tmp_tests.js
//     297 kiểm tra xanh; check_tmp_fixture.py so Python với
//     tmp_expected.json (18 asset + Xolonium thật + InBundle node bundle).
//     Unit test Python: test_tmp_font (17) + test_font.
//  C. Trình duyệt THẬT (Playwright chromium headless shell, 390px, serve
//     site qua HTTP local): lazy boot tới "Sẵn sàng — 20 plugin" khi chọn
//     file .assets fixture; tab Font → "Đọc TMP_FontAsset" hiện 134/134
//     (SourceCodePro) + tải tmp_glyph_plan.json đúng nội dung; quét font
//     Be Vietnam Pro vẫn 142/142; 390px không tràn ngang, không pageerror.
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { execSync, execFileSync, spawnSync } from "node:child_process";

const ROOT = decodeURIComponent(new URL("..", import.meta.url).pathname).replace(/\/$/, "");
const SITE = ROOT;
const EXP = ROOT + "/exp";
const VENDOR = path.join(path.dirname(ROOT), "IPA-LOCALIZATION-main");
const FX = "/tmp/item7_fx";
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
      SITE + "/pb3/sdk/tmp_font.py", SITE + "/pb3/tests/test_tmp_font.py"],
      { stdio: "pipe", cwd: SITE + "/pb3" });
  } catch (e) { bad.push("py_compile"); }
  rec("static_node_py_check", bad.length === 0,
    "node --check OK: app.js, sw.js; python3 -m py_compile OK: bridge.py, " +
    "sdk/tmp_font.py, tests/test_tmp_font.py" + (bad.length ? " | LỖI: " + bad.join(", ") : ""),
    performance.now() - t0);
}

{ // A2. Kiểm tra tĩnh chuyên mục 7 (stdlib/3.9, C#, manifest, precache)
  const t0 = performance.now();
  const r = spawnSync("python3", [EXP + "/check_item7_static.py"],
    { encoding: "utf8" });
  let detail = (r.stdout || "").trim();
  let ok = r.status === 0;
  try {
    const j = JSON.parse(r.stdout || "{}");
    if (j.fails && j.fails.length) { ok = false; detail = j.fails.join(" | "); }
    else detail = "stdlib-only + Python 3.9 + mảng C# khớp Python (134+8) + " +
      "API TMP đủ + không fallback C7 + không msdf-atlas-gen + manifest/precache " +
      "khớp + CACHE_VERSION=v7-20261116";
  } catch (e) { ok = false; detail = "không đọc được JSON: " + (r.stdout || "") + (r.stderr || ""); }
  rec("static_item7_checks", ok, detail, performance.now() - t0);
}

// ================= B. Fixture =================
{ // B1. Sinh lại fixture studio (tái tạo được)
  const t0 = performance.now();
  try {
    execSync(`python3 tests/make_tmp_ipas.py ${FX}`, { cwd: VENDOR, stdio: "pipe" });
    const has = fs.existsSync(FX + "/tmp_all.ipa") && fs.existsSync(FX + "/tmp_expected.json") &&
      fs.existsSync(FX + "/tmp_real_xolonium.bin");
    rec("fixture_regenerate", has,
      "make_tmp_ipas.py -> " + FX + "/tmp_all.ipa + tmp_expected.json + " +
      "tmp_real_xolonium.bin (18 TMP_FontAsset giả lập + byte thật Xolonium)",
      performance.now() - t0);
  } catch (e) {
    rec("fixture_regenerate", false, "sinh fixture lỗi: " + String(e && e.message ? e.message : e),
      performance.now() - t0);
  }
}

{ // B2. Bộ test JS của studio (tham chiếu) — 297 kiểm tra
  const t0 = performance.now();
  const r = spawnSync("node", ["tests/run_tmp_tests.js", FX],
    { cwd: VENDOR, encoding: "utf8" });
  const out = (r.stdout || "") + (r.stderr || "");
  const m = out.match(/TẤT CẢ ĐẠT \((\d+) kiểm tra\)/);
  rec("studio_tmp_tests_297", r.status === 0 && !!m,
    "run_tmp_tests.js exit=" + r.status + "; " +
    (m ? "TẤT CẢ ĐẠT (" + m[1] + " kiểm tra)" : "không thấy dòng TẤT CẢ ĐẠT; đuôi output: " + out.slice(-400)),
    performance.now() - t0);
}

{ // B3. Python so với đáp án độc lập (18 asset + Xolonium thật + bundle node)
  const t0 = performance.now();
  const r = spawnSync("python3", [EXP + "/check_tmp_fixture.py", FX],
    { encoding: "utf8" });
  let detail = "";
  let ok = r.status === 0;
  try {
    const j = JSON.parse(r.stdout || "{}");
    detail = "assets Python=" + j.assets + " (kỳ vọng " + j.expected + " + 1 InBundle từ node bundle)" +
      "; summary=" + JSON.stringify(j.summary) + "; bundle_nodes=" + j.bundle_nodes +
      "; glyph_plan missing_total=" + j.glyph_plan_missing_total +
      "; files_scanned=" + (j.files_scanned || []).join(", ") +
      (j.fails && j.fails.length ? "; FAIL: " + j.fails.join(" | ") : "; 0 lệch");
    if (j.fails && j.fails.length) ok = false;
  } catch (e) { ok = false; detail = "không đọc được JSON: " + (r.stdout || "") + (r.stderr || ""); }
  rec("python_tmp_fixture_match", ok, detail, performance.now() - t0);
}

{ // B4. Unit test Python: test_tmp_font (17) + test_font
  const t0 = performance.now();
  const r = spawnSync("python3",
    ["-m", "unittest", "tests.test_tmp_font", "tests.test_font"],
    { cwd: SITE + "/pb3", encoding: "utf8" });
  const out = (r.stdout || "") + (r.stderr || "");
  const m = out.match(/Ran (\d+) tests?/);
  const okRun = /OK$/.test(out.trim()) || /\nOK\n?$/.test(out);
  rec("python_unit_tmp_font", r.status === 0 && !!m && okRun,
    "unittest tests.test_tmp_font + tests.test_font: " +
    (m ? "Ran " + m[1] + " tests, " : "") + (okRun ? "OK" : "KHÔNG OK") +
    " (exit=" + r.status + ")" + (okRun ? "" : "; đuôi: " + out.slice(-500)),
    performance.now() - t0);
}

// ---- Chuẩn bị fixture cho phần trình duyệt (sau khi B1 sinh lại) ----
// File .assets rời (17 TMP asset, 13 target cho glyph plan, missing_total 142).
execSync(`python3 -c "import zipfile; z=zipfile.ZipFile('${FX}/tmp_all.ipa'); open('/tmp/item7_resources.assets','wb').write(z.read('Payload/Game.app/Data/resources.assets'))"`);
execSync(`python3 -c "import base64; open('/tmp/item7_BeVietnamPro-Regular.ttf','wb').write(base64.b64decode(open('${SITE}/fonts/BeVietnamPro-Regular.ttf.b64','rb').read()))"`);
console.log("fixture trình duyệt: /tmp/item7_resources.assets (" +
  fs.statSync("/tmp/item7_resources.assets").size + "B), BeVietnamPro.ttf (" +
  fs.statSync("/tmp/item7_BeVietnamPro-Regular.ttf").size + "B)");

// ================= C. Trình duyệt thật 390px =================
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
  const reqUrls = [];
  page.on("request", (r) => reqUrls.push(r.url()));
  page.on("pageerror", (e) => pageErrors.push(String(e && e.message ? e.message : e)));
  page.on("console", (m) => { if (m.type() === "error") pageErrors.push("console: " + m.text()); });
  const elText = (id) => page.evaluate((i) => (document.getElementById(i) || {}).textContent || "", id);
  const bootState = () => page.evaluate(() => (typeof window.__PB3_BOOT_STATE === "function"
    ? window.__PB3_BOOT_STATE() : null));
  const waitReady = async () => {
    await page.waitForFunction(
      () => { const el = document.getElementById("status"); return el && el.textContent.includes("Sẵn sàng"); },
      null, { timeout: 240000 });
  };
  const selectFile = async (file) => {
    const name = file.split("/").pop();
    await page.setInputFiles("#file-input", file);
    await page.waitForFunction(
      (n) => { const el = document.getElementById("file-info"); return el && el.textContent.includes(n); },
      name, { timeout: 120000 });
  };
  const openFontTab = async () => {
    await page.click("#btn-nav-tools");
    await page.click("#btn-font");
    await page.waitForFunction(
      () => { const el = document.getElementById("tab-font"); return el && !el.hidden; },
      null, { timeout: 15000 });
  };

  { // C1. Tải trang: lazy boot — chưa chọn file thì không tải Pyodide
    const t0 = performance.now();
    await page.goto("http://127.0.0.1:" + PORT + "/index.html");
    await sleep(2000);
    const status = await elText("status");
    const bs = await bootState();
    const earlyPy = reqUrls.filter((u) => u.includes("/pyodide/") || u.includes("worker.js") || u.includes("/pb3/"));
    rec("browser_lazy_boot_state", status.includes("Chưa khởi động") && bs && bs.booted === false &&
      bs.workerCreated === false && earlyPy.length === 0,
      "status=" + JSON.stringify(status) + "; __PB3_BOOT_STATE=" + JSON.stringify(bs) +
      "; request Pyodide/worker.js/pb3 trước khi chọn file: " + (earlyPy.join(", ") || "KHÔNG CÓ"),
      performance.now() - t0);
  }

  { // C2. Chọn file .assets fixture -> boot -> tab Font -> Đọc TMP_FontAsset
    const t0 = performance.now();
    await selectFile("/tmp/item7_resources.assets");
    await waitReady();
    const status = await elText("status");
    await openFontTab();
    const dlPromise = page.waitForEvent("download", { timeout: 120000 }).catch(() => null);
    await page.click("#btn-font-tmp");
    await page.waitForFunction(
      () => { const el = document.getElementById("font-result"); return el && el.textContent.includes("TMP_FontAsset"); },
      null, { timeout: 120000 });
    const txt = await elText("font-result");
    const hasFull = txt.includes("134/134");
    const hasPlan = txt.includes("tmp_glyph_plan.json");
    const hasSource = txt.includes("SourceCodePro SDF");
    const hasAtlas = /1024×1024/.test(txt);
    const dl = await dlPromise;
    let dlDetail = "không bắt được sự kiện download";
    let dlOk = false;
    if (dl) {
      const fname = dl.suggestedFilename();
      const savePath = "/tmp/item7_plan_downloaded.json";
      await dl.saveAs(savePath);
      try {
        const plan = JSON.parse(fs.readFileSync(savePath, "utf8"));
        dlOk = fname === "tmp_glyph_plan.json" && plan.schema === "tmp_glyph_plan/1" &&
          plan.missing_total === 142 && Array.isArray(plan.targets) && plan.targets.length === 13;
        dlDetail = "download: " + fname + " (" + fs.statSync(savePath).size + "B); schema=" +
          plan.schema + ", missing_total=" + plan.missing_total + ", targets=" + plan.targets.length;
      } catch (e) { dlDetail = "download " + fname + " nhưng không đọc được JSON: " + e.message; }
    }
    rec("browser_tmp_font_read", status.includes("Sẵn sàng") && status.includes("20 plugin") &&
      hasFull && hasPlan && hasSource && hasAtlas,
      "status: " + JSON.stringify(status) + "; kết quả có SourceCodePro SDF: " + hasSource +
      ", 134/134: " + hasFull + ", atlas 1024×1024: " + hasAtlas +
      ", nhắc tmp_glyph_plan.json: " + hasPlan +
      "; đoạn đầu kết quả: " + JSON.stringify(txt.slice(0, 220)),
      performance.now() - t0);
    rec("browser_tmp_plan_download", dlOk, dlDetail);
  }

  { // C3. Quét font Be Vietnam Pro vẫn 142/142 (font scan cũ không vỡ)
    const t0 = performance.now();
    await selectFile("/tmp/item7_BeVietnamPro-Regular.ttf");
    await page.click("#btn-font-scan");
    await page.waitForFunction(
      () => { const el = document.getElementById("font-result"); return el && el.textContent.includes("142/142"); },
      null, { timeout: 120000 });
    const txt = await elText("font-result");
    rec("browser_font_scan_142", txt.includes("142/142") && txt.includes("Be Vietnam Pro"),
      "kết quả quét font: " + JSON.stringify(txt.slice(0, 200)),
      performance.now() - t0);
  }

  { // C4. 390px: không tràn ngang + không lỗi trang cả phiên
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
const failed = Object.keys(results).filter((k) => !results[k].ok);
fs.writeFileSync(EXP + "/item7_results.json",
  JSON.stringify({ total, failures: failed, results }, null, 2));
console.log("\n==== TỔNG: " + total + " mục, " + failed.length + " FAIL ====");
if (failed.length) { console.log("FAIL: " + failed.join(", ")); process.exit(1); }
