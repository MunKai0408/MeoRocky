// verify_analysis_mod_search.mjs — Harness PHẦN (B): Mod tìm theo chữ +
// nhập file .json. Chạy trên code hiện tại (chưa đẩy).
// Chạy: node verify_analysis_mod_search.mjs (từ thư mục exp/ của repo)
//   ONLY=1,2,3,4,5 — chỉ chạy section đó (để mỗi lượt vừa giới hạn phiên).
// Kết quả: analysis_mod_search_results.md + .json (ghi đè phần đã chạy)
//   + out/mod_search/*.png (ảnh UI 390px)
//
// Section:
//  1. Hàm thuần (trong trang Chromium, không cần boot worker):
//     modParseJsonObject + modSearchEntries + modProvenanceFromSidecar.
//  2. UI 390px — nhập file .json thật của CẢ 5 game (resources.
//     monobehaviour.en.json trích xuất thật, exp/out/extract_5games):
//     tìm "setting" (+ "play" cho Passpartout, vì setting=0 ở đó),
//     đếm đúng số thật, sửa 1 entry rồi đưa vào ô Bản dịch (plan) +
//     tải file .json đã sửa, kiểm tra nội dung file tải về.
//  3. End-to-end qua worker thật: trích xuất zip nhỏ (game.json 3 entry)
//     rồi Mod "Lấy file trích xuất đã chọn" nạp đúng file + provenance
//     extract; và "Lấy từ bản dịch" (state.lastTranslated giả lập).
//  4. Provenance khi nhập: chọn kèm sidecar .provenance.json thì giữ
//     provenance; không có sidecar thì ghi rõ "không rõ nguồn gốc";
//     JSON hỏng / mảng / ô tìm trống / phân biệt hoa-thường đều báo rõ.
//  5. Hồi quy: module-worker 17/17, C2 13/13, C9 16/16, item8 static
//     0 fails, full suite Python xanh. (Hồi quy (A) 33/33 chạy riêng ở
//     verify_analysis_translate_full.mjs — xem analysis_..._results.json.)
//
// Lưu ý: Chromium trong sandbox không có HTTPS ngoài nên UI chạy trên HTTP
// local (127.0.0.1) phục vụ cây site/ — ĐÚNG code sẽ đẩy, chỉ khác host.
// KHÔNG phải Safari thật — Safari vẫn cần Joseph thử.
import fs from "node:fs";
import path from "node:path";
import http from "node:http";
import { execSync, spawnSync } from "node:child_process";

const ROOT = decodeURIComponent(new URL("..", import.meta.url).pathname).replace(/\/$/, "");
const SITE = ROOT;
const EXP = ROOT + "/exp";
const OUT = EXP + "/out/mod_search";
const EXTRACT_OUT = EXP + "/out/extract_5games";
const TINY_ZIP = "/tmp/mod_tiny.zip";
fs.mkdirSync(OUT, { recursive: true });

const ONLY = process.env.ONLY ? process.env.ONLY.split(",").map((s) => s.trim()) : null;
const want = (n) => !ONLY || ONLY.includes(String(n));
const results = {};
try {
  const prev = JSON.parse(fs.readFileSync(EXP + "/analysis_mod_search_results.json", "utf8"));
  if (prev && prev.results) Object.assign(results, prev.results);
} catch (_) {}
function rec(name, ok, detail, ms) {
  results[name] = { ok: !!ok, detail: String(detail || ""), ms: Math.round(ms || 0) };
  console.log((ok ? "PASS " : "FAIL ") + name + " | " + (detail || ""));
}
const t0 = performance.now();
const sec = (ms) => Math.round(ms / 100) / 10;
function sh(cmd, cwd) {
  return spawnSync("bash", ["-c", cmd], { cwd: cwd || EXP, encoding: "utf8", timeout: 600000 });
}

// File JSON trích xuất thật dùng cho bảng 5 game + số kết quả kỳ vọng
// (đếm bằng Python từ đúng file này — xem analysis_mod_search_results.md).
const GAMES = [
  // Entry/kwCount đếm lại 2026-11-17 từ file trích xuất thật bằng code hiện tại
  // (trích xuất đã đổi từ 16/11: tách langtable ra file riêng nên monobehaviour
  // chỉ còn 3273/6823/478 key thay vì 11356/14939/6261; xem NOTES harness-fix §3).
  { id: "9kings", file: EXTRACT_OUT + "/9kings/resources.monobehaviour.en.json", entries: 3273, kw: "setting", kwCount: 6, sampleKey: "Default Localization Settings.s1" },
  { id: "data", file: EXTRACT_OUT + "/data/resources.monobehaviour.en.json", entries: 3834, kw: "setting", kwCount: 79, sampleKey: "NPSettings.s2" },
  { id: "ek2", file: EXTRACT_OUT + "/ek2/resources.monobehaviour.en.json", entries: 6823, kw: "setting", kwCount: 9, sampleKey: "PhotonServerSettings.s2" },
  { id: "pb3", file: EXTRACT_OUT + "/pb3/resources.monobehaviour.en.json", entries: 478, kw: "setting", kwCount: 3, sampleKey: "TMP Settings.s1" },
  { id: "passpartout", file: EXTRACT_OUT + "/passpartout/resources.monobehaviour.en.json", entries: 2707, kw: "setting", kwCount: 0, kw2: "play", kw2Count: 68, sampleKey: null },
];

const MIME = { ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript",
  ".json": "application/json", ".webmanifest": "application/manifest+json",
  ".wasm": "application/wasm", ".png": "image/png", ".b64": "text/plain",
  ".ttf": "font/ttf", ".txt": "text/plain", ".md": "text/plain", ".css": "text/css" };
const server = http.createServer((req, res) => {
  const url = new URL(req.url || "/", "http://127.0.0.1");
  const fp = path.join(SITE, decodeURIComponent(url.pathname) === "/" ? "index.html" : decodeURIComponent(url.pathname));
  if (!fp.startsWith(SITE)) { res.writeHead(403); res.end(); return; }
  fs.readFile(fp, (err, data) => {
    if (err) { res.writeHead(404); res.end("not found"); return; }
    res.writeHead(200, { "content-type": MIME[path.extname(fp)] || "application/octet-stream" });
    res.end(data);
  });
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const BASE = "http://127.0.0.1:" + server.address().port;

let browser = null;
let page = null;
let ctx = null;
const pageErrors = [];
async function openPage() {
  if (page) return page;
  const { chromium } = await import("playwright-core");
  const cands = [
    process.env.HOME + "/.cache/ms-playwright/chromium_headless_shell-1148/chrome-linux/headless_shell",
    process.env.HOME + "/.cache/ms-playwright/chromium-1148/chrome-linux/chrome",
  ];
  const exe = cands.find((c) => fs.existsSync(c));
  if (!exe) throw new Error("không tìm thấy Chromium ở ~/.cache/ms-playwright");
  browser = await chromium.launch({ executablePath: exe, args: ["--no-sandbox", "--disable-dev-shm-usage"] });
  ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, acceptDownloads: true });
  page = await ctx.newPage();
  page.on("pageerror", (e) => pageErrors.push(String(e)));
  await page.goto(BASE + "/index.html", { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForFunction(() => typeof state !== "undefined", null, { timeout: 30000 });
  await page.click("#btn-card-mod");
  await openModAdvanced();
  await page.waitForSelector("#mod-search", { timeout: 10000 });
  return page;
}
async function openModAdvanced() {
  // PR #11 (Mod UI tidy): các nút tìm/sửa/plan nằm trong <details id="mod-advanced">
  // (mặc định đóng) — mở trước khi bấm/fill, nếu không Playwright fail vì phần tử
  // không hiển thị (waitForSelector/click/fill đều yêu cầu visible).
  await page.evaluate(() => { const d = document.getElementById("mod-advanced"); if (d) d.open = true; });
}
const elText = (id) => page.$eval("#" + id, (el) => el.textContent || "");
async function importJson(files, expectedEntries) {
  const name = path.basename(files[0]);
  await page.setInputFiles("#mod-json-file", files);
  // 5 game dùng CHUNG tên file resources.monobehaviour.en.json nên chỉ
  // chờ "Đã mở <tên>" sẽ khớp ngay với info CŨ của game trước (race đã
  // làm FAIL ek2/passpartout ở lần chạy 2026-11-16: info đọc được là
  // 3834/6261 entry của game trước, trong khi số kết quả tìm vẫn đúng).
  // Khi biết trước số entry thì chờ thêm đúng số đó trong info.
  await page.waitForFunction((a) => {
    const el = document.querySelector("#mod-json-info");
    const t = (el && el.textContent) || "";
    return t.includes("Đã mở " + a.name) && (a.entries == null || t.includes(a.entries + " entry"));
  }, { name, entries: expectedEntries == null ? null : expectedEntries }, { timeout: 30000 });
}
async function searchKw(kw, scope, caseSensitive) {
  await page.fill("#mod-search", kw);
  if (scope) await page.selectOption("#mod-search-scope", scope);
  if (caseSensitive !== undefined) {
    const checked = await page.$eval("#mod-search-case", (el) => el.checked);
    if (checked !== caseSensitive) await page.click("#mod-search-case");
  }
  await page.click("#btn-mod-search");
  await page.waitForFunction((k) => {
    const el = document.querySelector("#mod-search-count");
    return el && (el.textContent || "").includes("“" + k + "”");
  }, kw, { timeout: 30000 });
  return elText("mod-search-count");
}
function countFromText(t) {
  const m = (t || "").match(/Tìm thấy (\d+) kết quả/);
  return m ? parseInt(m[1], 10) : (t.includes("Không tìm thấy") ? 0 : null);
}

// ================= 1. Hàm thuần =================
if (want(1)) {
  await openPage();
  let t = performance.now();
  const pure = await page.evaluate(() => {
    const out = {};
    // modParseJsonObject: giữ thứ tự key, từ chối mảng + JSON hỏng.
    try {
      const p = modParseJsonObject('{"b": "2", "a": "1"}');
      out.parse_order = p.keys.join(",");
      out.parse_entries = Object.keys(p.entries).length;
    } catch (e) { out.parse_error = String(e.message); }
    try { modParseJsonObject('[1,2]'); out.parse_array_rejected = false; }
    catch (e) { out.parse_array_rejected = true; out.parse_array_msg = String(e.message); }
    try { modParseJsonObject('{khong json'); out.parse_bad_rejected = false; }
    catch (e) { out.parse_bad_rejected = true; }
    // modSearchEntries: key+value, không phân biệt hoa/thường mặc định.
    const sample = {
      "MAINMENU_SETTINGS": "Settings",
      "setting_volume": "Âm lượng",
      "Play": "Play the game",
      "Quit": "Quit",
      "num": 42, "flag": true, "nothing": null,
    };
    out.search_setting = modSearchEntries(sample, "setting").map((r) => r.key);
    out.search_setting_upper_case = modSearchEntries(sample, "SETTING", { caseSensitive: true }).map((r) => r.key);
    out.search_setting_lower_case = modSearchEntries(sample, "setting", { caseSensitive: true }).map((r) => r.key);
    out.search_setting_key_only = modSearchEntries(sample, "setting", { scope: "key" }).map((r) => r.key);
    out.search_setting_value_only = modSearchEntries(sample, "setting", { scope: "value" }).map((r) => r.key);
    out.search_play_value = modSearchEntries(sample, "play the", { scope: "value" }).map((r) => r.key);
    out.search_num = modSearchEntries(sample, "42").map((r) => r.key);
    out.search_empty = modSearchEntries(sample, "").length;
    out.search_none = modSearchEntries(sample, "zzz-khong-co").length;
    // modProvenanceFromSidecar: đúng format thì giữ, sai thì null.
    out.prov_ok = modProvenanceFromSidecar(JSON.stringify({
      format: "beartea-translation-provenance/1",
      provenance: { origin: "extract", gameFile: "9kings.ipa", extractName: "resources.monobehaviour.en.json", source: "9kings.ipa::Payload/9Kings.app/Data/resources.assets", locale: "en", keys: 5 },
    }));
    out.prov_bad_format = modProvenanceFromSidecar(JSON.stringify({ format: "khac/1", provenance: {} }));
    out.prov_bad_json = modProvenanceFromSidecar("{hong");
    out.short_source = modShortSource({ source: "9kings.ipa::Payload/9Kings.app/Data/resources.assets" });
    out.short_source_none = modShortSource(null);
    return out;
  });
  rec("pure_parse_json_object",
    pure.parse_order === "b,a" && pure.parse_entries === 2 &&
    pure.parse_array_rejected === true && pure.parse_bad_rejected === true,
    "thu tu key giu nguyen: " + pure.parse_order + " · mang bi tu choi: " + pure.parse_array_rejected +
    " · JSON hong bi tu choi: " + pure.parse_bad_rejected, performance.now() - t);
  rec("pure_search_case_insensitive_key_value",
    JSON.stringify(pure.search_setting) === JSON.stringify(["MAINMENU_SETTINGS", "setting_volume"]) &&
    JSON.stringify(pure.search_setting_upper_case) === JSON.stringify(["MAINMENU_SETTINGS"]) &&
    JSON.stringify(pure.search_setting_lower_case) === JSON.stringify(["setting_volume"]) &&
    JSON.stringify(pure.search_setting_key_only) === JSON.stringify(["MAINMENU_SETTINGS", "setting_volume"]) &&
    JSON.stringify(pure.search_setting_value_only) === JSON.stringify(["MAINMENU_SETTINGS"]) &&
    JSON.stringify(pure.search_play_value) === JSON.stringify(["Play"]) &&
    pure.search_empty === 0 && pure.search_none === 0,
    "setting (mac dinh): " + JSON.stringify(pure.search_setting) +
    " · SETTING phan biet hoa/thuong: " + JSON.stringify(pure.search_setting_upper_case) +
    " · setting phan biet hoa/thuong: " + JSON.stringify(pure.search_setting_lower_case) +
    " · chi key: " + JSON.stringify(pure.search_setting_key_only) +
    " · chi value: " + JSON.stringify(pure.search_setting_value_only) +
    " · so 42 tim duoc: " + JSON.stringify(pure.search_num), performance.now() - t);
  rec("pure_provenance_sidecar",
    pure.prov_ok && pure.prov_ok.origin === "extract" && pure.prov_ok.gameFile === "9kings.ipa" &&
    pure.prov_ok.source === "9kings.ipa::Payload/9Kings.app/Data/resources.assets" &&
    pure.prov_bad_format === null && pure.prov_bad_json === null &&
    pure.short_source === "resources.assets" && pure.short_source_none === "—",
    "sidecar dung -> origin=" + (pure.prov_ok && pure.prov_ok.origin) +
    " · sai format/JSON hong -> null · rut gon source: " + pure.short_source, performance.now() - t);
}

// ================= 2. UI 390px — bảng 5 game =================
if (want(2)) {
  await openPage();
  const five = {};
  for (const g of GAMES) {
    let t = performance.now();
    const exists = fs.existsSync(g.file);
    if (!exists) {
      rec("game_" + g.id + "_import_search", false, "thieu file trich xuat that: " + g.file, performance.now() - t);
      continue;
    }
    await importJson([g.file], g.entries);
    const info = await elText("mod-json-info");
    const entriesOk = info.includes(String(g.entries) + " entry") && info.includes(path.basename(g.file));
    const countTxt = await searchKw(g.kw);
    const n = countFromText(countTxt);
    const sampleOk = !g.sampleKey || await page.evaluate((k) => {
      const box = document.querySelector("#mod-search-results");
      return box && (box.textContent || "").includes(k);
    }, g.sampleKey);
    five[g.id] = { file: path.basename(g.file), entries: g.entries, kw: g.kw, count: n, expected: g.kwCount, info };
    rec("game_" + g.id + "_import_search",
      entriesOk && n === g.kwCount && sampleOk,
      "file " + path.basename(g.file) + " · " + g.entries + " entry · “" + g.kw + "” = " + n +
      " (ky vong " + g.kwCount + ") · mau '" + (g.sampleKey || "—") + "' trong ket qua: " + sampleOk +
      " · info: " + info.slice(0, 120), performance.now() - t);
    if (g.kw2) {
      t = performance.now();
      const c2 = countFromText(await searchKw(g.kw2));
      five[g.id].kw2 = g.kw2; five[g.id].kw2Count = c2; five[g.id].kw2Expected = g.kw2Count;
      rec("game_" + g.id + "_second_keyword",
        c2 === g.kw2Count,
        "“" + g.kw2 + "” = " + c2 + " (ky vong " + g.kw2Count + ") — “setting” = 0 o game nay nen can tu khoa thu 2 that",
        performance.now() - t);
    }
  }
  globalThis.__five = five;
  // Sửa 1 entry tìm được (9 Kings) rồi đưa vào ô Bản dịch + tải file.
  {
    let t = performance.now();
    const g = GAMES[0];
    await importJson([g.file], g.entries);
    await searchKw("setting");
    const first = await page.evaluate(() => {
      const input = document.querySelector("#mod-search-results input[data-mod-edit]");
      const btn = document.querySelector("#mod-search-results button[data-mod-save]");
      if (!input || !btn) return null;
      return { key: input.getAttribute("data-mod-edit"), old: input.value };
    });
    let edited = null;
    if (first) {
      const newVal = "CÀI ĐẶT KIỂM CHỨNG B";
      await page.fill("#mod-search-results input[data-mod-edit]", newVal);
      await page.click("#mod-search-results button[data-mod-save]");
      await page.waitForFunction(() => {
        const el = document.querySelector("#mod-json-info");
        return el && /đã sửa 1 entry/.test(el.textContent || "");
      }, null, { timeout: 10000 });
      edited = { key: first.key, old: first.old, new: newVal };
    }
    rec("ui_edit_entry_in_results",
      !!edited && await page.evaluate((k) => state.modJson && state.modJson.entries[k] === "CÀI ĐẶT KIỂM CHỨNG B", edited ? edited.key : ""),
      edited ? ("key '" + edited.key + "': '" + edited.old + "' -> '" + edited.new + "' · info: " + await elText("mod-json-info")) : "khong tim duoc dong ket qua de sua",
      performance.now() - t);
    t = performance.now();
    await page.click("#btn-mod-to-plan");
    await page.waitForFunction(() => {
      const el = document.querySelector("#mod-search-count");
      return el && /Đã đưa/.test(el.textContent || "");
    }, null, { timeout: 10000 });
    const planTxt = await page.$eval("#plan-translations", (el) => el.value);
    let planJson = null;
    try { planJson = JSON.parse(planTxt); } catch (_) {}
    rec("ui_edit_reaches_plan_textarea",
      !!planJson && edited && planJson[edited.key] === "CÀI ĐẶT KIỂM CHỨNG B" &&
      Object.keys(planJson).length === g.entries,
      "plan-translations co " + (planJson ? Object.keys(planJson).length : "?") + " key · gia tri moi co trong plan: " +
      (!!planJson && edited && planJson[edited.key] === "CÀI ĐẶT KIỂM CHỨNG B"), performance.now() - t);
    t = performance.now();
    const dl = page.waitForEvent("download", { timeout: 30000 }).catch(() => null);
    await page.click("#btn-mod-download-json");
    const download = await dl;
    let dlOk = false, dlDetail = "khong co download event";
    if (download) {
      const p = await download.path();
      if (p) {
        const txt = fs.readFileSync(p, "utf8");
        const obj = JSON.parse(txt);
        dlOk = download.suggestedFilename().endsWith(".mod.json") &&
          obj[edited.key] === "CÀI ĐẶT KIỂM CHỨNG B" && Object.keys(obj).length === g.entries &&
          Object.keys(obj)[0] === Object.keys(JSON.parse(fs.readFileSync(g.file, "utf8")))[0];
        dlDetail = "file: " + download.suggestedFilename() + " · " + Object.keys(obj).length +
          " key · gia tri moi: " + (obj[edited.key] === "CÀI ĐẶT KIỂM CHỨNG B") +
          " · giu thu tu key goc: " + dlOk;
      }
    }
    rec("ui_download_edited_json", dlOk, dlDetail, performance.now() - t);
    await page.screenshot({ path: OUT + "/ui_mod_search_390px.png", fullPage: false });
    rec("ui_no_page_errors_so_far", pageErrors.length === 0, pageErrors.slice(0, 3).join(" | "), performance.now() - t);
  }
}

// ================= 3. End-to-end qua worker: trích xuất + bản dịch =================
if (want(3)) {
  await openPage();
  // 3a. Trích xuất zip nhỏ thật (game.json 3 entry) rồi lấy vào Mod.
  // Fixture e2e nằm ở /tmp (thư mục tạm — file có thể mất sau khi khởi
  // động lại máy, từng làm rớt mục này ở đợt gộp 1 file 2026-11-16):
  // tự tạo lại đúng hình dạng đã ghi nếu thiếu, để harness tự chạy được.
  if (!fs.existsSync(TINY_ZIP)) {
    sh("python3 -c \"import json, zipfile; z = zipfile.ZipFile('/tmp/mod_tiny.zip', 'w', zipfile.ZIP_DEFLATED); z.writestr('game.json', json.dumps({'setting': 'Cài đặt', 'play': 'Chơi', 'quit': 'Thoát'}, ensure_ascii=False, indent=2)); z.close()\"", EXP);
  }
  {
    let t = performance.now();
    if (!fs.existsSync(TINY_ZIP)) {
      rec("e2e_extract_tiny_zip", false, "thieu " + TINY_ZIP + " (tao bang python zipfile: game.json 3 entry)", 0);
    } else {
      await page.setInputFiles("#file-input", TINY_ZIP);
      await page.waitForFunction(() => {
        const el = document.getElementById("file-info");
        return el && el.textContent.includes("mod_tiny.zip");
      }, null, { timeout: 60000 });
      await page.click("#btn-nav-tools");
      await page.click("#btn-extract");
      // Thanh Lưu file (download-bar) còn hiện từ lần tải ở section 2 sẽ
      // che nút Trích xuất ở 390px — ẩn nó trước khi bấm.
      await page.evaluate(() => {
        const b = document.getElementById("btn-download-close");
        if (b && !document.getElementById("download-bar").hidden) b.click();
      });
      await page.click("#btn-extract-run");
      await page.waitForFunction(() => {
        const el = document.getElementById("extract-result");
        return el && el.textContent.includes("Kết quả:");
      }, null, { timeout: 300000 });
      // Muc nay truoc day CHI duoc ghi khi file thieu (FAIL) ma khong bao
      // gio duoc ghi PASS khi chay duoc — ket qua cu FAIL bi giu lai mai
      // trong results.json do co che gop ket qua cu (dot gop 1 file
      // 2026-11-16: file /tmp mat di, muc FAIL them vao roi khong bao gio
      // duoc ghi de). Ghi PASS tai day de muc tu chua lanh.
      rec("e2e_extract_tiny_zip", true, "mod_tiny.zip co san, trich xuat zip nho chay duoc", performance.now() - t);
      const extractTxt = await elText("extract-result");
      // Đang ở view Chức năng sau extract: dùng tab #btn-mod (nút
      // #btn-card-mod chỉ có ở trang chủ, đang ẩn nên không click được).
      await page.click("#btn-mod");
      await openModAdvanced();
      await page.waitForSelector("#mod-search", { timeout: 10000 });
      const selVal = await page.$eval("#mod-json-source", (el) => el.value);
      await page.click("#btn-mod-load-extract");
      await page.waitForFunction(() => {
        const el = document.querySelector("#mod-json-info");
        return el && /Đã mở en\.json/.test(el.textContent || "");
      }, null, { timeout: 120000 });
      const info = await elText("mod-json-info");
      const countTxt = await searchKw("setting");
      rec("e2e_extract_then_mod_load",
        extractTxt.includes("3 entry") && selVal === "en.json" &&
        info.includes("3 entry") && info.includes("trích xuất từ game.json") &&
        countFromText(countTxt) === 1,
        "extract: " + extractTxt.slice(0, 120) + " · dropdown Mod: " + selVal +
        " · info: " + info.slice(0, 160) + " · “setting” = " + countFromText(countTxt),
        performance.now() - t);
    }
  }
  // 3b. Lấy từ bản dịch trong phiên (state.lastTranslated giả lập đúng
  // dạng mục A để lại) — kèm provenance origin manual.
  {
    let t = performance.now();
    await page.evaluate(() => {
      state.lastTranslated = {
        name: "translated.vi.json", from: "en", to: "vi",
        translated: { "Play": "Chơi", "Setting": "Cài đặt", "Quit": "Thoát" },
        keys: ["Play", "Setting", "Quit"], at: new Date().toISOString(),
        provenance: { origin: "manual", gameFile: null, extractName: null, source: null, locale: "en", from: "en", to: "vi", keys: 3, at: new Date().toISOString() },
      };
    });
    await page.click("#btn-mod-load-translate");
    await page.waitForFunction(() => {
      const el = document.querySelector("#mod-json-info");
      return el && /Đã mở translated\.vi\.json/.test(el.textContent || "");
    }, null, { timeout: 10000 });
    const info = await elText("mod-json-info");
    const countTxt = await searchKw("cài");
    rec("e2e_load_from_translation",
      info.includes("3 entry") && /không rõ nguồn gốc/.test(info) && countFromText(countTxt) === 1,
      "info: " + info.slice(0, 160) + " · “cài” = " + countFromText(countTxt), performance.now() - t);
  }
}

// ================= 4. Provenance khi nhập + trường hợp lỗi =================
if (want(4)) {
  await openPage();
  // 4a. Nhập kèm sidecar -> giữ provenance extract.
  {
    let t = performance.now();
    const main = OUT + "/translated.vi.json";
    const side = OUT + "/translated.vi.provenance.json";
    fs.writeFileSync(main, JSON.stringify({ "Play": "Chơi", "Setting": "Cài đặt" }, null, 1), "utf8");
    fs.writeFileSync(side, JSON.stringify({
      format: "beartea-translation-provenance/1",
      provenance: { origin: "extract", gameFile: "9kings.ipa", extractName: "resources.monobehaviour.en.json", source: "9kings.ipa::Payload/9Kings.app/Data/resources.assets", locale: "en", from: "en", to: "vi", keys: 2, at: "2026-11-16T00:00:00.000Z" },
    }, null, 2), "utf8");
    await importJson([main, side], 2);
    const info = await elText("mod-json-info");
    const prov = await page.evaluate(() => state.modJson && state.modJson.provenance);
    rec("import_with_sidecar_keeps_provenance",
      info.includes("trích xuất từ 9kings.ipa::Payload/9Kings.app/Data/resources.assets") &&
      prov && prov.origin === "extract" && prov.gameFile === "9kings.ipa" && prov.keys === 2,
      "info: " + info.slice(0, 180) + " · prov: " + JSON.stringify(prov), performance.now() - t);
  }
  // 4b. Nhập không có sidecar -> ghi rõ "không rõ nguồn gốc", không bịa.
  {
    let t = performance.now();
    const solo = OUT + "/solo.json";
    fs.writeFileSync(solo, JSON.stringify({ "Hello": "Xin chào" }, null, 1), "utf8");
    await importJson([solo], 1);
    const info = await elText("mod-json-info");
    const prov = await page.evaluate(() => state.modJson && state.modJson.provenance);
    rec("import_without_sidecar_says_unknown_origin",
      /không rõ nguồn gốc/.test(info) && prov && prov.origin === "upload" && prov.source === null,
      "info: " + info.slice(0, 180) + " · prov.origin=" + (prov && prov.origin), performance.now() - t);
  }
  // 4c. JSON hỏng / mảng / ô tìm trống / phân biệt hoa-thường.
  {
    let t = performance.now();
    const bad = OUT + "/bad.json";
    fs.writeFileSync(bad, "{khong phai json", "utf8");
    await page.setInputFiles("#mod-json-file", [bad]);
    await page.waitForSelector("#error:not([hidden])", { timeout: 10000 }).catch(() => null);
    const errBad = await page.$eval("#error", (el) => el.textContent || "");
    const arr = OUT + "/arr.json";
    fs.writeFileSync(arr, "[1,2,3]", "utf8");
    await page.setInputFiles("#mod-json-file", [arr]);
    await page.waitForSelector("#error:not([hidden])", { timeout: 10000 }).catch(() => null);
    const errArr = await page.$eval("#error", (el) => el.textContent || "");
    rec("import_invalid_json_reports_error",
      /không hợp lệ/.test(errBad) && /phải là object phẳng/.test(errArr),
      "JSON hong: " + errBad.slice(0, 100) + " · mang: " + errArr.slice(0, 100), performance.now() - t);
    t = performance.now();
    const solo = OUT + "/solo.json";
    await importJson([solo], 1);
    await page.fill("#mod-search", "");
    await page.click("#btn-mod-search");
    await page.waitForSelector("#error:not([hidden])", { timeout: 10000 }).catch(() => null);
    const errEmpty = await page.$eval("#error", (el) => el.textContent || "");
    // "Xin" hoa/thuong: khong phan biet -> 1; phan biet -> 0 ("xin" thuong khong co).
    await page.fill("#mod-search", "xin");
    await page.click("#btn-mod-search");
    await page.waitForFunction(() => /Tìm thấy|Không tìm thấy/.test(document.querySelector("#mod-search-count").textContent || ""), null, { timeout: 10000 });
    const nInsensitive = countFromText(await elText("mod-search-count"));
    await page.click("#mod-search-case");
    await page.click("#btn-mod-search");
    await page.waitForFunction(() => /Tìm thấy|Không tìm thấy/.test(document.querySelector("#mod-search-count").textContent || ""), null, { timeout: 10000 });
    const nSensitive = countFromText(await elText("mod-search-count"));
    rec("search_empty_and_case_toggle",
      /Hãy nhập chữ cần tìm/.test(errEmpty) && nInsensitive === 1 && nSensitive === 0,
      "o trong: " + errEmpty.slice(0, 60) + " · “xin” khong phan biet: " + nInsensitive +
      " · phan biet: " + nSensitive, performance.now() - t);
  }
}

// ================= 5. Hồi quy =================
if (want(5)) {
  const runHarness = (name, cmd, parse) => {
    const t = performance.now();
    const r = sh(cmd, EXP);
    const out = (r.stdout || "") + (r.stderr || "");
    const ok = r.status === 0 && parse(out);
    rec(name, ok, out.trim().split("\n").slice(-2).join(" / ").slice(0, 300), performance.now() - t);
  };
  runHarness("regress_module_worker",
    "node verify_module_worker.mjs 2>&1 | grep -E 'XONG'",
    (o) => /17 mục, 0 FAIL/.test(o));
  runHarness("regress_c2",
    "node verify_analysis_c2.mjs 2>&1 | grep -E 'TỔNG'",
    (o) => /13 mục, 0 FAIL/.test(o));
  runHarness("regress_c9",
    "node verify_analysis_c9.mjs 2>&1 | grep -E 'KẾT QUẢ C9'",
    (o) => /16\/16 PASS/.test(o));
  runHarness("regress_item8_static",
    "python3 check_item8_static.py 2>&1 | tail -n 5",
    (o) => /"fails": \[\]/.test(o) || /0 fails/.test(o));
  {
    const t = performance.now();
    const r = sh("python3 -m unittest discover -s tests -t . 2>&1 | grep -E '^(Ran|OK|FAILED)'", SITE + "/pb3");
    const out = (r.stdout || "") + (r.stderr || "");
    const m = out.match(/Ran (\d+) tests? in [\d.]+s\n(OK|FAILED)/);
    rec("regress_full_suite_python", r.status === 0 && !!m && m[2] === "OK",
      m ? (m[1] + " test, " + m[2]) : out.trim().split("\n").slice(-2).join(" / ").slice(0, 200),
      performance.now() - t);
  }
}

// ================= Tổng kết =================
if (browser) await browser.close().catch(() => {});
server.close();
const total = Object.keys(results).length;
const passed = Object.values(results).filter((v) => v.ok).length;
const failures = total - passed;
console.log("\n== TONG KET: " + passed + "/" + total + " PASS, " + failures + " FAIL (" + sec(performance.now() - t0) + "s)" +
  (ONLY ? " — CHAY MOT PHAN (ONLY=" + ONLY.join(",") + "), cac muc khac lay tu lan chay truoc" : "") + " ==");
const summary = {
  generated_at: new Date().toISOString(),
  total, passed, failed: total - passed,
  results,
  five_games: globalThis.__five || null,
  page_errors: pageErrors,
  notes: "Chromium sandbox khong co HTTPS ngoai: UI chay tren HTTP local 127.0.0.1 (dung code se day). KHONG phai Safari that.",
};
fs.writeFileSync(EXP + "/analysis_mod_search_results.json", JSON.stringify(summary, null, 2), "utf8");
process.exit(failures ? 1 : 0);
