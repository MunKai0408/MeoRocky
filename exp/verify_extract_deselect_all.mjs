// verify_extract_deselect_all.mjs — Kiểm chứng nút "Chọn tất cả" /
// "Bỏ chọn tất cả" ở tab Trích xuất (Joseph 2026-11-16,
// repo MunKai0408/MeoRocky).
//
// Chạy: node verify_extract_deselect_all.mjs (từ thư mục exp/ của repo)
// Kết quả: extract_deselect_all_results.md + extract_deselect_all_results.json
//
// Trình duyệt thật 390px, fixture extract_select_fixture.zip:
//  S1. Chế độ tự động -> cả 2 nút đều disabled.
//  S2. Chuyển sang chọn tay -> cả 2 nút đều bật, checkbox mặc định tick tất cả.
//  S3. Bỏ tick tay 1 file rồi "Bỏ chọn tất cả" -> 0 checkbox tick;
//      "Chọn tất cả" -> tất cả checkbox tick lại.
//  S4. "Bỏ chọn tất cả" rồi bấm "Trích xuất" -> báo rõ "Chưa chọn file
//      nào..." (không chạy, không "Lỗi không rõ", không tự động trích hết).
//  S5. "Chọn tất cả" rồi bấm "Trích xuất" -> chạy bình thường, ra English.json.
import fs from "node:fs";
import http from "node:http";
import path from "node:path";

const ROOT = decodeURIComponent(new URL("..", import.meta.url).pathname).replace(/\/$/, "");
const SITE = ROOT;
const EXP = ROOT + "/exp";
const FX = EXP + "/fixtures/extract_select_fixture.zip";
const sec = (ms) => Math.round(ms / 100) / 10;
const results = {};
let failures = 0;
function rec(name, ok, detail, ms) {
  results[name] = { ok, detail, seconds: ms == null ? undefined : sec(ms) };
  if (!ok) failures++;
  console.log(`\n### ${name}: ${ok ? "PASS" : "FAIL"}${ms == null ? "" : " (" + sec(ms) + "s)"}\n${detail}`);
}

const MIME = {
  ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript",
  ".json": "application/json", ".webmanifest": "application/manifest+json",
  ".wasm": "application/wasm", ".png": "image/png", ".b64": "text/plain",
  ".ttf": "font/ttf", ".txt": "text/plain", ".md": "text/plain", ".css": "text/css",
};
const server = http.createServer((req, res) => {
  const url = new URL(req.url || "/", "http://127.0.0.1");
  const urlPath = decodeURIComponent(url.pathname);
  const fp = path.join(SITE, urlPath === "/" ? "index.html" : urlPath);
  if (!fp.startsWith(SITE)) { res.writeHead(403); res.end(); return; }
  fs.readFile(fp, (err, data) => {
    if (err) { res.writeHead(404); res.end("not found: " + urlPath); return; }
    res.writeHead(200, { "content-type": MIME[path.extname(fp)] || "application/octet-stream" });
    res.end(data);
  });
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const PORT = server.address().port;

let browser = null;
const pageErrors = [];
try {
  const { chromium } = await import("playwright-core");
  const candidates = [
    process.env.HOME + "/.cache/ms-playwright/chromium_headless_shell-1148/chrome-linux/headless_shell",
    process.env.HOME + "/.cache/ms-playwright/chromium-1148/chrome-linux/chrome",
  ];
  const exe = candidates.find((c) => fs.existsSync(c));
  if (!exe) throw new Error("không tìm thấy Chromium headless shell ở ~/.cache/ms-playwright");
  browser = await chromium.launch({ executablePath: exe, args: ["--no-sandbox"] });
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, acceptDownloads: true });
  const page = await ctx.newPage();
  page.on("pageerror", (e) => pageErrors.push(String(e && e.message ? e.message : e)));
  const elText = (id) => page.evaluate((i) => (document.getElementById(i) || {}).textContent || "", id);
  const elHidden = (id) => page.evaluate((i) => { const el = document.getElementById(i); return el ? !!el.hidden : null; }, id);
  const btnDisabled = (id) => page.evaluate((i) => { const el = document.getElementById(i); return el ? !!el.disabled : null; }, id);
  const cbState = () => page.evaluate(() => {
    const cbs = [...document.querySelectorAll("#extract-file-list input[type=checkbox]")];
    return { total: cbs.length, checked: cbs.filter((c) => c.checked).length };
  });

  await page.goto("http://127.0.0.1:" + PORT + "/index.html");
  try {
    await page.setInputFiles("#file-input", FX);
    await page.waitForFunction(
      () => { const el = document.getElementById("status"); return el && el.textContent.includes("Sẵn sàng"); },
      null, { timeout: 120000 });
  } catch (e) {
    await page.goto("http://127.0.0.1:" + PORT + "/index.html");
    await page.setInputFiles("#file-input", FX);
    await page.waitForFunction(
      () => { const el = document.getElementById("status"); return el && el.textContent.includes("Sẵn sàng"); },
      null, { timeout: 240000 });
  }
  await page.click("#btn-nav-tools");
  await page.click("#btn-extract");
  await page.waitForFunction(
    () => { const el = document.getElementById("extract-file-list"); return el && el.textContent.includes("level1.unity3d"); },
    null, { timeout: 60000 });

  { // S1. Chế độ tự động -> cả 2 nút disabled
    const t0 = performance.now();
    const selDis = await btnDisabled("btn-extract-select-all");
    const desDis = await btnDisabled("btn-extract-deselect-all");
    rec("S1_auto_mode_buttons_disabled", selDis === true && desDis === true,
      `"Chọn tất cả" disabled=${selDis}; "Bỏ chọn tất cả" disabled=${desDis} (chế độ tự động mặc định)`,
      performance.now() - t0);
  }
  { // S2. Chuyển sang chọn tay -> cả 2 nút bật, checkbox tick tất cả
    const t0 = performance.now();
    await page.click("#extract-mode-manual");
    await page.waitForFunction(
      () => { const b = document.getElementById("btn-extract-select-all"); return b && !b.disabled; },
      null, { timeout: 30000 });
    const selDis = await btnDisabled("btn-extract-select-all");
    const desDis = await btnDisabled("btn-extract-deselect-all");
    const cb = await cbState();
    rec("S2_manual_mode_buttons_enabled_all_checked", selDis === false && desDis === false &&
      cb.total > 0 && cb.checked === cb.total,
      `"Chọn tất cả" disabled=${selDis}; "Bỏ chọn tất cả" disabled=${desDis}; checkbox ${cb.checked}/${cb.total} tick (mặc định tick tất cả)`,
      performance.now() - t0);
  }
  { // S3. Bỏ tick tay 1 file -> "Bỏ chọn tất cả" -> 0 tick -> "Chọn tất cả" -> tick hết
    const t0 = performance.now();
    await page.evaluate(() => {
      const cb = document.querySelector("#extract-file-list input[type=checkbox]");
      if (cb) cb.click();
    });
    const afterManual = await cbState();
    await page.click("#btn-extract-deselect-all");
    const afterDeselect = await cbState();
    await page.click("#btn-extract-select-all");
    const afterSelect = await cbState();
    rec("S3_deselect_all_then_select_all", afterManual.checked === afterManual.total - 1 &&
      afterDeselect.checked === 0 && afterSelect.checked === afterSelect.total && afterSelect.total > 0,
      `bỏ tick tay 1 file: ${afterManual.checked}/${afterManual.total}; ` +
      `sau "Bỏ chọn tất cả": ${afterDeselect.checked}/${afterDeselect.total}; ` +
      `sau "Chọn tất cả": ${afterSelect.checked}/${afterSelect.total}`,
      performance.now() - t0);
  }
  { // S4. "Bỏ chọn tất cả" rồi bấm "Trích xuất" -> báo rõ, không chạy
    const t0 = performance.now();
    await page.click("#btn-extract-deselect-all");
    const before = await cbState();
    await page.click("#btn-extract-run");
    await page.waitForFunction(
      () => { const el = document.getElementById("error"); return el && !el.hidden && el.textContent.length > 0; },
      null, { timeout: 30000 });
    const errText = await elText("error");
    const resText = await elText("extract-result");
    rec("S4_extract_with_empty_selection_blocked_clearly", before.checked === 0 &&
      /Chưa chọn file nào/.test(errText) && /Tự động/.test(errText) && !/Lỗi không rõ/.test(errText) &&
      resText.trim() === "",
      `checkbox tick trước khi bấm Trích xuất: ${before.checked}; banner: ${JSON.stringify(errText)}; ` +
      `extract-result vẫn trống (không chạy ngầm): ${resText.trim() === ""}`,
      performance.now() - t0);
  }
  { // S5. "Chọn tất cả" rồi bấm "Trích xuất" -> chạy bình thường
    const t0 = performance.now();
    await page.click("#btn-extract-select-all");
    const cb = await cbState();
    await page.click("#btn-extract-run");
    await page.waitForFunction(
      () => { const el = document.getElementById("extract-result"); return el && el.textContent.includes("English.json"); },
      null, { timeout: 120000 });
    const resText = await elText("extract-result");
    const errHidden = await elHidden("error");
    rec("S5_select_all_then_extract_works", cb.checked === cb.total && resText.includes("English.json") &&
      errHidden === true,
      `checkbox ${cb.checked}/${cb.total} tick; extract-result có English.json: ${resText.includes("English.json")}; banner ẩn: ${errHidden}`,
      performance.now() - t0);
  }
} finally {
  if (browser) await browser.close();
  server.close();
}

fs.writeFileSync(EXP + "/extract_deselect_all_results.json", JSON.stringify(
  { when: new Date().toISOString(), failures, results, pageErrors }, null, 2));
console.log("\n==== TỔNG: " + Object.keys(results).length + " mục, " + failures + " FAIL ====");
if (pageErrors.length) console.log("pageErrors: " + JSON.stringify(pageErrors));
process.exit(failures ? 1 : 0);
