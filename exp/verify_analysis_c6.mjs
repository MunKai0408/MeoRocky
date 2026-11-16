// verify_analysis_c6.mjs — Kiểm chứng MỤC C6: font fallback TMP (BearTea web).
// Web đọc TMP_FontAsset rồi xuất KẾ HOẠCH fallback (JSON) + script C# cho
// Unity Editor; KHÔNG đẩy lên GitHub.
//
// Chạy: node verify_analysis_c6.mjs (từ /home/hatch/workspace/pb3_web/exp)
// Kết quả: analysis_c6_results.json (bản này) + analysis_c6_results.md (báo cáo tay).
//
//  A. Tĩnh: node --check; py_compile; check_analysis_c6_static.py
//     (schema plan, C# khớp Python 134+8, đăng ký fallback TMP_Settings +
//     fallbackFontAssetTable, KHÔNG sửa asset gốc, không msdf-atlas-gen,
//     nút/handler/bridge/manifest).
//  B. Fixture studio (giả lập + byte thật DATA WING):
//     check_c6_fallback_plan.py (kế hoạch fallback đối chiếu glyph plan
//     mục 7: missing_total 142, 15 fallback trên IPA / 13 trên
//     resources.assets, 4 unplanned không bịa ký tự, Xolonium 106);
//     unit test Python: test_tmp_fallback (6) + test_tmp_font.
//  C. Trình duyệt THẬT (Playwright chromium headless shell, 390px, serve
//     site qua HTTP local): lazy boot tới "Sẵn sàng — 20 plugin" khi chọn
//     file .assets fixture; tab Font → "Đọc TMP_FontAsset" rồi
//     "Tạo font fallback" hiện kế hoạch + tải tmp_fallback_plan.json đúng
//     nội dung (schema, 142 ký tự thiếu, 13 fallback, dùng lại lần quét
//     trước); script C# phục vụ được qua HTTP và chứa đăng ký fallback;
//     390px không tràn ngang, không pageerror.
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { execSync, execFileSync, spawnSync } from "node:child_process";

const SITE = "/home/hatch/workspace/pb3_web/site";
const EXP = "/home/hatch/workspace/pb3_web/exp";
const VENDOR = "/home/hatch/workspace/pb3_web/vendor/ipa-localization-studio/IPA-LOCALIZATION-main";
const FX = "/tmp/c6_fx";
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
      SITE + "/pb3/sdk/tmp_font.py", SITE + "/pb3/tests/test_tmp_fallback.py"],
      { stdio: "pipe", cwd: SITE + "/pb3" });
  } catch (e) { bad.push("py_compile"); }
  rec("static_node_py_check", bad.length === 0,
    "node --check OK: app.js, sw.js; python3 -m py_compile OK: bridge.py, " +
    "sdk/tmp_font.py, tests/test_tmp_fallback.py" + (bad.length ? " | LỖI: " + bad.join(", ") : ""),
    performance.now() - t0);
}

{ // A2. Kiểm tra tĩnh chuyên mục C6
  const t0 = performance.now();
  const r = spawnSync("python3", [EXP + "/check_analysis_c6_static.py"],
    { encoding: "utf8" });
  let detail = (r.stdout || "").trim();
  let ok = r.status === 0;
  try {
    const j = JSON.parse(r.stdout || "{}");
    if (j.fails && j.fails.length) { ok = false; detail = j.fails.join(" | "); }
    else detail = "schema tmp_fallback_plan/1 + C# khớp Python (134+8) + " +
      "đăng ký TMP_Settings.fallbackFontAssets + fallbackFontAssetTable + " +
      "CreateFontAsset/TryAddCharacters + KHÔNG sửa asset gốc + " +
      "nút/handler/bridge/cache + tests.json=159, files.json=90";
  } catch (e) { ok = false; detail = "không đọc được JSON: " + (r.stdout || "") + (r.stderr || ""); }
  rec("static_c6_checks", ok, detail, performance.now() - t0);
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

{ // B2. Kế hoạch fallback Python đối chiếu glyph plan mục 7
  const t0 = performance.now();
  const r = spawnSync("python3", [EXP + "/check_c6_fallback_plan.py", FX],
    { encoding: "utf8" });
  let detail = "";
  let ok = r.status === 0;
  try {
    const j = JSON.parse(r.stdout || "{}");
    detail = "assets=" + j.assets + "; fallback missing_total=" + j.fallback_missing_total +
      " (glyph plan " + j.glyph_plan_missing_total + "); fallbacks_needed=" + j.fallbacks_needed +
      "; already_full=" + j.already_full_count + "; unplanned=" + j.unplanned_count +
      "; Xolonium missing_total=" + j.xolonium_missing_total +
      ", fallback: " + (j.xolonium_fallbacks || []).join(", ") +
      (j.fails && j.fails.length ? "; FAIL: " + j.fails.join(" | ") : "; 0 lệch");
    if (j.fails && j.fails.length) ok = false;
  } catch (e) { ok = false; detail = "không đọc được JSON: " + (r.stdout || "") + (r.stderr || ""); }
  rec("python_fallback_plan_fixture", ok, detail, performance.now() - t0);
}

{ // B3. Unit test Python: test_tmp_fallback (6) + test_tmp_font
  const t0 = performance.now();
  const r = spawnSync("python3",
    ["-m", "unittest", "tests.test_tmp_fallback", "tests.test_tmp_font"],
    { cwd: SITE + "/pb3", encoding: "utf8" });
  const out = (r.stdout || "") + (r.stderr || "");
  const m = out.match(/Ran (\d+) tests?/);
  const okRun = /OK$/.test(out.trim()) || /\nOK\n?$/.test(out);
  rec("python_unit_tmp_fallback", r.status === 0 && !!m && okRun,
    "unittest tests.test_tmp_fallback + tests.test_tmp_font: " +
    (m ? "Ran " + m[1] + " tests, " : "") + (okRun ? "OK" : "KHÔNG OK") +
    " (exit=" + r.status + ")" + (okRun ? "" : "; đuôi: " + out.slice(-500)),
    performance.now() - t0);
}

// ---- Chuẩn bị fixture cho phần trình duyệt (sau khi B1 sinh lại) ----
// File .assets rời (17 TMP asset; 13 target cho glyph plan -> 13 fallback).
execSync(`python3 -c "import zipfile; z=zipfile.ZipFile('${FX}/tmp_all.ipa'); open('/tmp/c6_resources.assets','wb').write(z.read('Payload/Game.app/Data/resources.assets'))"`);
console.log("fixture trình duyệt: /tmp/c6_resources.assets (" +
  fs.statSync("/tmp/c6_resources.assets").size + "B)");

// ================= C. Trình duyệt thật 390px =================
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

  { // C2. Đọc TMP rồi Tạo font fallback: hiện kế hoạch + tải JSON đúng
    const t0 = performance.now();
    await selectFile("/tmp/c6_resources.assets");
    await waitReady();
    const status = await elText("status");
    await openFontTab();
    await page.click("#btn-font-tmp");
    await page.waitForFunction(
      () => { const el = document.getElementById("font-result"); return el && el.textContent.includes("TMP_FontAsset"); },
      null, { timeout: 120000 });
    const tmpTxt = await elText("font-result");
    const dlPromise = page.waitForEvent("download", { timeout: 120000 }).catch(() => null);
    await page.click("#btn-font-fallback");
    await page.waitForFunction(
      () => { const el = document.getElementById("font-result"); return el && el.textContent.includes("Kế hoạch font fallback"); },
      null, { timeout: 120000 });
    const txt = await elText("font-result");
    const hasPlanFile = txt.includes("tmp_fallback_plan.json");
    const hasSettings = txt.includes("TMP_Settings");
    const hasCsLink = await page.evaluate(() => {
      const a = document.querySelector('#font-result a[href*="TMP_VietnameseFallback.cs"]');
      return !!a && a.getAttribute("href") === "unity_editor/TMP_VietnameseFallback.cs";
    });
    const hasFallbackName = txt.includes("Vietnamese Fallback");
    const hasReuse = txt.includes("dùng lại kết quả quét TMP lần trước");
    const hasUnchanged = txt.includes("Material gốc: không đổi");
    const dl = await dlPromise;
    let dlDetail = "không bắt được sự kiện download";
    let dlOk = false;
    if (dl) {
      const fname = dl.suggestedFilename();
      const savePath = "/tmp/c6_plan_downloaded.json";
      await dl.saveAs(savePath);
      try {
        const plan = JSON.parse(fs.readFileSync(savePath, "utf8"));
        const scp = (plan.fallbacks || []).find((f) => f.name === "SourceCodePro SDF");
        dlOk = fname === "tmp_fallback_plan.json" && plan.schema === "tmp_fallback_plan/1" &&
          plan.missing_total === 142 && Array.isArray(plan.fallbacks) && plan.fallbacks.length === 13 &&
          plan.chars_text.length === 142 &&
          scp && scp.missing_letters_codepoints.length === 0 &&
          scp.missing_marks_codepoints.length === 8 &&
          scp.fallback_name === "Source Code Pro Vietnamese Fallback" &&
          scp.material_unchanged === true &&
          scp.base_point_size === 36 && scp.base_atlas_width === 1024 &&
          (plan.fallbacks || []).every((f) => f.fallback_atlas_width >= 256 && f.fallback_atlas_width <= 1024);
        dlDetail = "download: " + fname + " (" + fs.statSync(savePath).size + "B); schema=" +
          plan.schema + ", missing_total=" + plan.missing_total + ", fallbacks=" + (plan.fallbacks || []).length +
          ", chars_text.length=" + (plan.chars_text || "").length +
          ", SourceCodePro: thiếu chữ " + (scp ? scp.missing_letters_codepoints.length : "?") +
          " + dấu " + (scp ? scp.missing_marks_codepoints.length : "?") +
          ", atlas fallback " + (scp ? scp.fallback_atlas_width + "x" + scp.fallback_atlas_height : "?");
      } catch (e) { dlDetail = "download " + fname + " nhưng không đọc được JSON: " + e.message; }
    }
    rec("browser_tmp_fallback_ui", status.includes("Sẵn sàng") && status.includes("20 plugin") &&
      tmpTxt.includes("134/134") && hasPlanFile && hasSettings && hasCsLink &&
      hasFallbackName && hasReuse && hasUnchanged,
      "status: " + JSON.stringify(status) + "; đọc TMP có 134/134: " + tmpTxt.includes("134/134") +
      "; kế hoạch nhắc tmp_fallback_plan.json: " + hasPlanFile + ", TMP_Settings: " + hasSettings +
      ", link script C#: " + hasCsLink + ", tên fallback: " + hasFallbackName +
      ", dùng lại lần quét trước: " + hasReuse + ", material không đổi: " + hasUnchanged +
      "; đoạn đầu kết quả: " + JSON.stringify(txt.slice(0, 220)),
      performance.now() - t0);
    rec("browser_tmp_fallback_download", dlOk, dlDetail);
  }

  { // C3. Script C# phục vụ được qua HTTP + chứa đăng ký fallback
    const t0 = performance.now();
    const cs = await page.evaluate(async (port) => {
      const r = await fetch("http://127.0.0.1:" + port + "/unity_editor/TMP_VietnameseFallback.cs");
      return { status: r.status, text: r.status === 200 ? await r.text() : "" };
    }, PORT);
    const ok = cs.status === 200 &&
      cs.text.includes("TMP_Settings") && cs.text.includes("fallbackFontAssets") &&
      cs.text.includes("fallbackFontAssetTable") && cs.text.includes("CreateFontAsset") &&
      cs.text.includes("TryAddCharacters") && cs.text.includes("Chưa biên dịch") &&
      !/baseAsset\.atlasTexture\s*=|baseAsset\.glyphTable\s*=|baseAsset\.material\s*=/.test(cs.text);
    rec("browser_cs_script_served", ok,
      "GET unity_editor/TMP_VietnameseFallback.cs: HTTP " + cs.status + ", " + cs.text.length + " ký tự" +
      "; có TMP_Settings/fallbackFontAssets/fallbackFontAssetTable/CreateFontAsset/TryAddCharacters: " +
      ["TMP_Settings", "fallbackFontAssets", "fallbackFontAssetTable", "CreateFontAsset", "TryAddCharacters"]
        .map((k) => k + "=" + cs.text.includes(k)).join(" ") +
      "; không gán atlas/material asset gốc: " +
      !/baseAsset\.atlasTexture\s*=|baseAsset\.glyphTable\s*=|baseAsset\.material\s*=/.test(cs.text),
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
fs.writeFileSync(EXP + "/analysis_c6_results.json",
  JSON.stringify({ total, failures: failed, results }, null, 2));
console.log("\n==== TỔNG: " + total + " mục, " + failed.length + " FAIL ====");
if (failed.length) { console.log("FAIL: " + failed.join(", ")); process.exit(1); }
