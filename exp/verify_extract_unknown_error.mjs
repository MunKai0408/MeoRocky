// verify_extract_unknown_error.mjs — Kiểm chứng fix banner đỏ
// "Trích xuất text lỗi: Lỗi không rõ" trên Safari thật (Joseph 15:42,
// repo MunKai0408/MeoRocky).
//
// Chạy: node verify_extract_unknown_error.mjs (từ /home/hatch/workspace/pb3_web/exp)
// Kết quả: extract_unknown_error_results.md + extract_unknown_error_results.json
//
// Bối cảnh: trang boot tốt (20 plugin, font 142/142) nhưng thao tác trích
// xuất trên IPA 9 Kings thật luôn ra banner "Lỗi không rõ" — cả lọc
// en+zh lẫn en-only. Nguyên nhân (đã kiểm chứng bằng fixture):
//  1. Bộ lọc locale ở vòng export loại cả entry KHÔNG xác định được locale
//     (`None not in {en,zh}`) nên IPA chỉ còn file config thì ra 0 file.
//  2. Mọi return sớm của run_extraction (0 file, kẹt ASK, hết ứng viên)
//     đều trả ok:false mà KHÔNG kèm error/code nên banner chỉ còn
//     "Lỗi không rõ" để hiện.
//  3. (phát hiện thêm khi sửa 1) Lọc chọn file `source_paths` chỉ lọc
//     inventory, vòng sinh ứng viên vẫn quét lớp gốc (cả ZIP) nên file
//     thường KHÔNG được chọn vẫn lọt vào — trước đây bị lọc locale che mất.
// Bridge thật trên Pyodide Node + trình duyệt thật 390px:
//  U1. Fixture chỉ config không locale + lọc en+zh BẬT -> ra file (không còn 0 file).
//  U2. Fixture chỉ config không locale + lọc en-only -> ra file.
//  U3. Fixture đầy đủ + lọc en-only -> có English.json (chứng tỏ trên
//      fixture thì en-only chạy được; IPA thật lỗi en-only thì lỗi nằm ở
//      parse bundle thật trên Safari — cần Joseph chạy lại với báo lỗi mới).
//  U4. Ca 0 file thật sự (chọn resources.assets + lọc zh) -> ok:false kèm
//      error thật (E_NO_CANDIDATE), KHÔNG "Lỗi không rõ".
//  U5. Bẫy trọng tài ASK (chọn resources.assets, không lọc) -> decision
//      "ask" kèm error E_ASK_NEEDED, KHÔNG "Lỗi không rõ".
//  U6. Bất biến: mọi kết quả ok:false của bridge đều có trường error
//      khác rỗng và khác "Lỗi không rõ".
//  D1. Trình duyệt thật 390px: fixture chỉ config + lọc mặc định -> kết
//      quả hiện file, KHÔNG có banner "Lỗi không rõ".
//  D2. Trình duyệt thật 390px: chọn tay chỉ level1.unity3d (de.json) +
//      lọc mặc định -> banner hiện đúng lý do (E_NO_CANDIDATE), KHÔNG
//      "Lỗi không rõ".
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { loadPyodide } from "../site/pyodide/pyodide.mjs";

const SITE = "/home/hatch/workspace/pb3_web/site";
const EXP = "/home/hatch/workspace/pb3_web/exp";
const FX = EXP + "/fixtures/extract_select_fixture.zip";
const BUNDLE = "Payload/9Kings.app/Data/Raw/aa/iOS/localization-locales_assets_all.bundle";
const RES = "Payload/9Kings.app/Data/resources.assets";
const LVL = "Payload/9Kings.app/Data/level1.unity3d";
const SETTINGS = "Payload/9Kings.app/Data/Raw/aa/settings.json";
const sec = (ms) => Math.round(ms / 100) / 10;
const results = {};
let failures = 0;
function rec(name, ok, detail, ms) {
  results[name] = { ok, detail, seconds: ms == null ? undefined : sec(ms) };
  if (!ok) failures++;
  console.log(`\n### ${name}: ${ok ? "PASS" : "FAIL"}${ms == null ? "" : " (" + sec(ms) + "s)"}\n${detail}`);
}
const hasRealError = (r) =>
  typeof r.error === "string" && r.error.length > 0 && !/Lỗi không rõ/.test(r.error);
const noUnknown = (r) => !JSON.stringify(r).includes("Lỗi không rõ");

// ---- 0. Fixture "chỉ config không locale" (mô phỏng IPA thật 13:22) ----
execFileSync("python3", ["-c", `
import zipfile
with zipfile.ZipFile("/tmp/nolocale.zip", "w") as z:
    z.writestr("Payload/9Kings.app/Data/Raw/aa/settings.json", '{"volume": "80", "quality": "high"}')
    z.writestr("Payload/9Kings.app/Data/Raw/aa/BuildSettings.json", '{"build": "1.0.5"}')
    z.writestr("Payload/9Kings.app/Data/Raw/aa/config.plist", '<?xml version="1.0"?><plist><dict></dict></plist>')
`]);
const NOLOCALE = "/tmp/nolocale.zip";

// ---- Boot Pyodide + bridge thật ----
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
  if (!JSON.parse(pyodide.globals.get("bridge").__boot_result__).ok) throw new Error("boot failed");
  return { pyodide, bridge, call: (fn, ...args) => JSON.parse(bridge[fn](...args)) };
}
const { pyodide, bridge, call } = await bootPyodide();
pyodide.FS.mkdirTree("/tmp/input");
pyodide.FS.writeFile("/tmp/input/fixture.zip", new Uint8Array(fs.readFileSync(FX)));
pyodide.FS.writeFile("/tmp/input/nolocale.zip", new Uint8Array(fs.readFileSync(NOLOCALE)));
const exportedNames = (r) => (r.files || []).map((f) => f.exported).sort();
const failedResults = [];

{ // U1. Chỉ config không locale + lọc en+zh BẬT -> ra file
  const t0 = performance.now();
  const r = call("extract", "/tmp/input/nolocale.zip", null, ["en", "zh"]);
  const got = exportedNames(r);
  rec("U1_nolocale_filter_both_files_out", r.ok === true && r.decision === "done" &&
    got.includes("settings.json") && got.includes("BuildSettings.json") && noUnknown(r),
    `ok=${r.ok} decision=${r.decision}; exported=${JSON.stringify(got)} (cần settings.json + BuildSettings.json); ` +
    `trước fix: ok=false, 0 file, banner "Lỗi không rõ"`,
    performance.now() - t0);
}
{ // U2. Chỉ config không locale + lọc en-only -> ra file
  const t0 = performance.now();
  const r = call("extract", "/tmp/input/nolocale.zip", null, ["en"]);
  const got = exportedNames(r);
  rec("U2_nolocale_filter_en_only_files_out", r.ok === true && r.decision === "done" &&
    got.includes("settings.json") && got.includes("BuildSettings.json") && noUnknown(r),
    `ok=${r.ok} decision=${r.decision}; exported=${JSON.stringify(got)}`,
    performance.now() - t0);
}
{ // U3. Fixture đầy đủ + lọc en-only -> có English.json (Joseph thử en-only vẫn lỗi trên IPA thật)
  const t0 = performance.now();
  const r = call("extract", "/tmp/input/fixture.zip", null, ["en"]);
  const got = exportedNames(r);
  rec("U3_full_fixture_filter_en_only_english_out", r.ok === true && r.decision === "done" &&
    got.includes("English.json") && got.includes("settings.json") && !got.includes("Chinese.json"),
    `ok=${r.ok} decision=${r.decision}; exported=${JSON.stringify(got)} (cần English.json + settings.json, không Chinese.json); ` +
    `trên fixture en-only chạy được -> lỗi en-only trên IPA thật nằm ở parse bundle thật trên Safari, không phải ở filter`,
    performance.now() - t0);
}
{ // U4. Ca 0 file thật sự: chọn resources.assets + lọc zh -> error thật, không "Lỗi không rõ"
  const t0 = performance.now();
  const r = call("extract", "/tmp/input/fixture.zip", [RES], ["zh"]);
  failedResults.push(r);
  const e0 = (r.errors || [])[0] || {};
  rec("U4_true_zero_files_real_error", r.ok === false && r.decision === "none" &&
    (r.files || []).length === 0 && hasRealError(r) && e0.code === "E_NO_CANDIDATE" &&
    /không còn ứng viên nào sau bước lọc/.test(r.error || ""),
    `ok=${r.ok} decision=${r.decision} files=0; error=${JSON.stringify(r.error)}; errors[0].code=${JSON.stringify(e0.code)}`,
    performance.now() - t0);
}
{ // U5. Bẫy trọng tài ASK: chọn resources.assets, không lọc -> decision ask + error E_ASK_NEEDED
  const t0 = performance.now();
  const r = call("extract", "/tmp/input/fixture.zip", [RES], null);
  failedResults.push(r);
  const e0 = (r.errors || [])[0] || {};
  rec("U5_ask_trap_real_error", r.ok === false && r.decision === "ask" &&
    hasRealError(r) && e0.code === "E_ASK_NEEDED" && /cần chọn tay/.test(r.error || ""),
    `ok=${r.ok} decision=${r.decision}; error=${JSON.stringify(r.error)}; errors[0].code=${JSON.stringify(e0.code)}`,
    performance.now() - t0);
}
{ // U6. Bất biến: mọi kết quả ok:false đều có error thật
  const t0 = performance.now();
  const ok = failedResults.length === 2 && failedResults.every((r) => r.ok === false && hasRealError(r));
  rec("U6_every_failed_bridge_result_has_real_error", ok,
    `đã kiểm ${failedResults.length} kết quả ok:false, tất cả đều có error khác rỗng và khác "Lỗi không rõ"`,
    performance.now() - t0);
}

// ---- Trình duyệt thật 390px ----
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
  const waitReady = async () => {
    // Mục 6 (lazy boot): boot chỉ chạy khi chọn file. Agent anh em có thể
    // đang ghi đè app.js giữa chừng (file phục vụ dở -> boot kẹt): thử tải
    // lại trang 1 lần nếu boot quá lâu.
    try {
      await page.setInputFiles("#file-input", NOLOCALE);
      await page.waitForFunction(
        () => { const el = document.getElementById("status"); return el && el.textContent.includes("Sẵn sàng"); },
        null, { timeout: 120000 });
    } catch (e) {
      await page.goto("http://127.0.0.1:" + PORT + "/index.html");
      await page.setInputFiles("#file-input", NOLOCALE);
      await page.waitForFunction(
        () => { const el = document.getElementById("status"); return el && el.textContent.includes("Sẵn sàng"); },
        null, { timeout: 240000 });
    }
  };

  { // D1. Fixture chỉ config + lọc mặc định -> kết quả hiện file, không banner "Lỗi không rõ"
    const t0 = performance.now();
    await page.goto("http://127.0.0.1:" + PORT + "/index.html");
    await waitReady();
    await page.click("#btn-nav-tools");
    await page.click("#btn-extract");
    await page.click("#btn-extract-run");
    await page.waitForFunction(
      () => { const el = document.getElementById("extract-result"); return el && el.textContent.includes("settings.json"); },
      null, { timeout: 120000 });
    const resText = await elText("extract-result");
    const errHidden = await elHidden("error");
    const errText = await elText("error");
    rec("D1_browser_nolocale_default_filter_shows_files", resText.includes("settings.json") &&
      resText.includes("BuildSettings.json") && errHidden === true && !/Lỗi không rõ/.test(errText),
      `extract-result có settings.json + BuildSettings.json: ${resText.includes("settings.json") && resText.includes("BuildSettings.json")}; ` +
      `banner #error ẩn: ${errHidden}; nội dung banner: ${JSON.stringify(errText.slice(0, 120))}`,
      performance.now() - t0);
  }
  { // D2. Chọn tay chỉ level1.unity3d (de.json) + lọc mặc định -> banner hiện đúng lý do
    const t0 = performance.now();
    await page.goto("http://127.0.0.1:" + PORT + "/index.html");
    await page.setInputFiles("#file-input", FX);
    await page.waitForFunction(
      () => { const el = document.getElementById("status"); return el && el.textContent.includes("Sẵn sàng"); },
      null, { timeout: 240000 });
    await page.click("#btn-nav-tools");
    await page.click("#btn-extract");
    await page.waitForFunction(
      (b) => { const el = document.getElementById("extract-file-list"); return el && el.textContent.includes(b); },
      LVL, { timeout: 60000 });
    await page.click("#extract-mode-manual");
    await page.waitForFunction(
      () => { const cbs = [...document.querySelectorAll("#extract-file-list input[type=checkbox]")]; return cbs.length > 0 && !cbs[0].disabled; },
      null, { timeout: 30000 });
    // Bỏ tick tất cả trừ level1.unity3d (de.json — bị lọc en/zh loại).
    await page.evaluate((lvl) => {
      for (const cb of document.querySelectorAll("#extract-file-list input[type=checkbox]")) {
        const keep = cb.getAttribute("data-path") === lvl;
        if (cb.checked !== keep) {
          cb.checked = keep;
          cb.dispatchEvent(new Event("change", { bubbles: true }));
        }
      }
    }, LVL);
    await page.click("#btn-extract-run");
    await page.waitForFunction(
      () => { const el = document.getElementById("error"); return el && !el.hidden && el.textContent.length > 0; },
      null, { timeout: 120000 });
    const errText = await elText("error");
    rec("D2_browser_true_zero_files_banner_shows_real_reason", !/Lỗi không rõ/.test(errText) &&
      /không còn ứng viên nào sau bước lọc|E_NO_CANDIDATE/.test(errText),
      `banner #error: ${JSON.stringify(errText)}`,
      performance.now() - t0);
  }
} finally {
  if (browser) await browser.close();
  server.close();
}

fs.writeFileSync(EXP + "/extract_unknown_error_results.json", JSON.stringify(
  { when: new Date().toISOString(), failures, results, pageErrors }, null, 2));
console.log("\n==== TỔNG: " + Object.keys(results).length + " mục, " + failures + " FAIL ====");
if (pageErrors.length) console.log("pageErrors: " + JSON.stringify(pageErrors));
process.exit(failures ? 1 : 0);
