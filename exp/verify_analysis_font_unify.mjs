// verify_analysis_font_unify.mjs — Harness PHẦN (D) FONT (Joseph 20:48 ý 8):
// liệt kê font của game + gom về 1 font Be Vietnam Pro đủ dấu.
// Chạy: node verify_analysis_font_unify.mjs (từ thư mục exp/ của repo)
//   ONLY=1,2,3,4,5 — chỉ chạy section đó.
// Kết quả: analysis_font_unify_results.json (+ .md viết tay sau)
//   + out/font_unify/*.png (ảnh UI 390px)
//
// Section:
//  1. Unit Python (tests.test_font_unify, 13 test) + tĩnh:
//     files.json=95 / tests.json=166 / sw.js CACHE_VERSION v21, node --check,
//     check_analysis_font_unify_static.py 0 fails.
//  2. Fixture IPA tổng hợp (CPython bridge): 5 font (TTF thiếu 4, TTF đủ,
//     OTF, TMP thiếu 42, Font nhúng) -> đúng hành động từng loại; TTF đã
//     gộp đủ 142/142; zip có plan + merged + fallback plan + donor.
//  3. 5 game thật (CPython, font_unify_5games.py): bảng font từng game,
//     EK2 Roboto-Bold.ttf 139/142 -> merged 142/142 đã xác minh.
//  4. UI trên Chromium thật 390px: nút liệt kê + nút gom 1 font có sẵn,
//     bấm liệt kê ra đủ font + thiếu; bấm gom ra kế hoạch + zip sẵn sàng;
//     0 page error, không tràn.
//  5. Hồi quy: module-worker 17/17, C2 13/13, C9 16/16, (A) 33/33,
//     (B) 25/25, (C) 26/26, Gộp 1 file 27/27, item8 static 0 fails,
//     full suite Python xanh.
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
const OUT = EXP + "/out/font_unify";
const FIXTURE = OUT + "/fixture.ipa";
fs.mkdirSync(OUT, { recursive: true });

const ONLY = process.env.ONLY ? process.env.ONLY.split(",").map((s) => s.trim()) : null;
const want = (n) => !ONLY || ONLY.includes(String(n));
const results = {};
try {
  const prev = JSON.parse(fs.readFileSync(EXP + "/analysis_font_unify_results.json", "utf8"));
  if (prev && prev.results) Object.assign(results, prev.results);
} catch (_) {}
function rec(name, ok, detail, ms) {
  results[name] = { ok: !!ok, detail: String(detail || ""), ms: Math.round(ms || 0) };
  console.log((ok ? "PASS " : "FAIL ") + name + " | " + (detail || ""));
}
const t0 = performance.now();
const sec = (ms) => Math.round(ms / 100) / 10;
function sh(cmd, cwd, timeoutMs) {
  return spawnSync("bash", ["-c", cmd], { cwd: cwd || EXP, encoding: "utf8", timeout: timeoutMs || 1800000 });
}

// ================= 1. Unit Python + tĩnh =================
if (want(1)) {
  let t = performance.now();
  const r = sh("python3 -m unittest tests.test_font_unify 2>&1", SITE + "/pb3");
  const m = (r.stdout || "").match(/Ran (\d+) tests? in [\d.]+s\n\n(OK|FAILED)/);
  const ok = r.status === 0 && !!m && m[2] === "OK";
  rec("py_unit_font_unify", ok,
    (m ? m[1] + " test, " + m[2] : "không đọc được kết quả") + " | " + (r.stdout || "").trim().split("\n").slice(-3).join(" / "),
    performance.now() - t);
  t = performance.now();
  const filesJson = JSON.parse(fs.readFileSync(SITE + "/pb3/files.json", "utf8"));
  const testsJson = JSON.parse(fs.readFileSync(SITE + "/pb3/tests.json", "utf8"));
  const swJs = fs.readFileSync(SITE + "/sw.js", "utf8");
  rec("static_counts_and_cache",
    filesJson.files.length === 95 && testsJson.files.length === 166 &&
    filesJson.files.includes("pb3/sdk/font_list.py") &&
    testsJson.files.includes("pb3/tests/test_font_unify.py") &&
    swJs.includes('CACHE_VERSION = "v21-20261117"') && !swJs.includes("v19-20261116") &&
    swJs.includes('"pb3/sdk/font_list.py"'),
    "files.json=" + filesJson.files.length + " (kỳ vọng 95) · tests.json=" + testsJson.files.length +
    " (kỳ vọng 166) · sw.js v21-20261117: " + swJs.includes('CACHE_VERSION = "v21-20261117"') +
    " · precache có font_list.py: " + swJs.includes('"pb3/sdk/font_list.py"'), performance.now() - t);
  t = performance.now();
  const c = sh("node --check " + SITE + "/app.js && node --check " + SITE + "/worker.js && node --check " + SITE + "/sw.js && python3 -m py_compile " + SITE + "/pb3/bridge.py " + SITE + "/pb3/sdk/font_list.py " + SITE + "/pb3/tests/test_font_unify.py", EXP);
  rec("static_syntax", c.status === 0, c.status === 0 ? "node --check app.js/worker.js/sw.js + py_compile bridge/font_list/test OK" : (c.stderr || "").slice(0, 300), performance.now() - t);
  t = performance.now();
  const s = sh("python3 check_analysis_font_unify_static.py 2>&1", EXP);
  let sj = null;
  try { sj = JSON.parse(s.stdout || "{}"); } catch (_) {}
  rec("static_font_unify_checks", s.status === 0 && sj && Array.isArray(sj.fails) && sj.fails.length === 0,
    sj ? (sj.fails.length === 0 ? "0 fails" : sj.fails.join(" | ")) : (s.stdout || "").slice(0, 200),
    performance.now() - t);
}

// ================= 2. Fixture IPA tổng hợp =================
if (want(2)) {
  let t = performance.now();
  const r = sh("python3 exp/check_font_unify_fixture.py 2>&1 | tail -n 3", ROOT);
  const fx = JSON.parse(fs.readFileSync(OUT + "/fixture.json", "utf8"));
  rec("fixture_list_5_fonts", r.status === 0 && fx.list_ok === true && fx.fonts === 5 &&
    JSON.stringify(fx.by_kind) === JSON.stringify({ ttf: 2, otf: 1, tmp_font_asset: 1, unity_font: 1 }) &&
    fx.ttf_partial && fx.ttf_partial.covered === 138 &&
    JSON.stringify(fx.ttf_partial.missing_codepoints) === JSON.stringify([768, 769, 7840, 7841]) &&
    fx.tmp_partial && fx.tmp_partial.covered === 100 && fx.tmp_partial.missing_total === 42 &&
    fx.unity_font_unreadable && fx.unity_font_unreadable.status === "unreadable" &&
    fx.unity_font_unreadable.path_id === 40 && fx.otf_partial && fx.otf_partial.status === "partial",
    "5 font: 2 TTF + 1 OTF + 1 TMP + 1 nhúng · TTF thiếu 138/142 (thiếu đúng 4: U+0300 U+0301 U+1EA0 U+1EA1) · TMP 100/142 (thiếu 42) · Font nhúng không đọc được (path_id 40) · OTF partial",
    performance.now() - t);
  t = performance.now();
  rec("fixture_unify_actions", fx.unify_ok === true &&
    fx.plan_counts.merge_glyphs === 1 && fx.plan_counts.tmp_fallback === 1 &&
    fx.plan_counts.replace_with_donor === 2 && fx.plan_counts.none_full === 1 &&
    fx.actions["ttf:GameFont.ttf"] === "merge_glyphs" &&
    fx.actions["ttf:FullFont.ttf"] === "none_full" &&
    fx.actions["otf:Game.otf"] === "replace_with_donor" &&
    fx.actions["tmp_font_asset:GameFont SDF"] === "tmp_fallback" &&
    fx.actions["unity_font:SourceCodePro-Regular"] === "replace_with_donor",
    "hành động đúng từng loại: TTF thiếu→merge_glyphs, TTF đủ→none_full, OTF→replace_with_donor, TMP→tmp_fallback, nhúng→replace_with_donor",
    performance.now() - t);
  t = performance.now();
  rec("fixture_unify_zip", fx.merged && fx.merged.length === 1 && fx.merged[0].output === "GameFont.vi.ttf" &&
    fx.merged[0].added === 4 && fx.merged_full && fx.merged_full.is_full === true &&
    fx.merged_full.covered === 142 && fx.donor_in_zip === true &&
    JSON.stringify(fx.zip_names) === JSON.stringify(["BeVietnamPro-Regular.ttf", "GameFont.vi.ttf", "font_unify_plan.json", "tmp_fallback_plan.json"]),
    "zip có 4 entry: plan + GameFont.vi.ttf (gộp 4 ký tự, đã xác minh 142/142) + tmp_fallback_plan.json + BeVietnamPro-Regular.ttf",
    performance.now() - t);
}

// ================= 3. 5 game thật =================
if (want(3)) {
  let t = performance.now();
  const r = sh("python3 exp/font_unify_5games.py 2>&1 | tail -n 20", ROOT);
  const sum = JSON.parse(fs.readFileSync(OUT + "/summary.json", "utf8"));
  const g = sum.games;
  const games = ["9kings", "data", "ek2", "pb3", "passpartout"];
  const allOk = games.every((k) => g[k] && g[k].list_ok === true && g[k].unify_ok === true);
  rec("font_5games_runs_ok", allOk && r.status === 0,
    games.map((k) => k + "=" + (g[k] ? (g[k].fonts + " font/" + g[k].list_seconds + "s") : "?")).join(" · "),
    performance.now() - t);
  const expect = {
    "9kings": { fonts: 3, kinds: { unity_font: 3 }, tmp_items: 0 },
    "data": { fonts: 0, kinds: {}, tmp_items: 0 },
    "ek2": { fonts: 8, kinds: { unity_font: 7, ttf: 1 }, tmp_items: 0 },
    "pb3": { fonts: 16, kinds: { unity_font: 16 }, tmp_items: 0 },
    "passpartout": { fonts: 7, kinds: { unity_font: 7 }, tmp_items: 0 },
  };
  for (const k of games) {
    t = performance.now();
    const e = expect[k];
    const row = g[k];
    const kindsOk = JSON.stringify((row.summary || {}).by_kind) === JSON.stringify(e.kinds);
    rec("font_5games_" + k, row.fonts === e.fonts && kindsOk && row.tmp_items === e.tmp_items,
      k + ": " + row.fonts + " font " + JSON.stringify((row.summary || {}).by_kind) +
      " · TMP xác nhận " + row.tmp_items + " · ứng viên MonoBehaviour chưa xác nhận " + row.tmp_candidates +
      (k === "data" ? " · 0 font vì file 590MB vượt giới hạn 256MB: " + JSON.stringify(row.warnings) : "") +
      (k === "9kings" ? " · sharedassets0.assets 154MB bỏ qua (E_TOO_LARGE, giới hạn entry VFS 64MB)" : "") +
      (k === "ek2" ? " · Roboto-Bold.ttf 139/142, merged " + JSON.stringify(row.merged) : ""),
      performance.now() - t);
  }
  {
    const t = performance.now();
    const ek2 = g["ek2"];
    const m = (ek2.merged || [])[0];
    rec("font_5games_ek2_merged_verified", !!m && m.output === "Roboto-Bold.vi.ttf" &&
      m.added === 3 && m.verified_covered === 142 && m.verified_full === true &&
      ek2.zip_entries.some((e) => e.name === "Roboto-Bold.vi.ttf") &&
      ek2.zip_entries.some((e) => e.name === "BeVietnamPro-Regular.ttf"),
      "EK2 Roboto-Bold.ttf thiếu 3 dấu kết hợp (U+0302 U+0306 U+031B) -> gộp 3 glyph -> đã xác minh 142/142 trong zip",
      performance.now() - t);
  }
}

// ================= 4. UI Chromium thật 390px =================
const server = http.createServer((req, res) => {
  try {
    let p = decodeURIComponent(req.url.split("?")[0]);
    if (p === "/") p = "/index.html";
    const fp = path.join(SITE, p);
    if (!fp.startsWith(SITE) || !fs.existsSync(fp) || !fs.statSync(fp).isFile()) { res.writeHead(404); res.end("nf"); return; }
    const ext = path.extname(fp);
    const type = { ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css", ".json": "application/json", ".wasm": "application/wasm", ".webmanifest": "application/manifest+json", ".png": "image/png", ".svg": "image/svg+xml", ".ttf": "font/ttf", ".txt": "text/plain" }[ext] || "application/octet-stream";
    res.writeHead(200, { "Content-Type": type });
    fs.createReadStream(fp).pipe(res);
  } catch (e) { res.writeHead(500); res.end(String(e)); }
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const BASE = "http://127.0.0.1:" + server.address().port;
let browser = null;
let page = null;
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
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, acceptDownloads: true });
  page = await ctx.newPage();
  page.on("pageerror", (e) => pageErrors.push(String(e)));
  await page.goto(BASE + "/index.html", { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForFunction(() => typeof state !== "undefined", null, { timeout: 30000 });
  return page;
}
function overflowCheck() {
  return page.evaluate(() => ({
    scrollW: document.documentElement.scrollWidth, innerW: window.innerWidth,
    overflow: document.documentElement.scrollWidth > window.innerWidth + 1,
  }));
}
if (want(4)) {
  // Chạy lại section này thì xoá kết quả cũ của nó (kể cả mục lỗi chung
  // ui_playwright_section) để không còn FAIL tồn từ lần chạy trước.
  for (const k of ["ui_font_buttons_present", "ui_font_list_renders",
                   "ui_font_unify_renders", "ui_no_pageerror_no_overflow",
                   "ui_playwright_section"]) delete results[k];
  try {
    let t = performance.now();
    await openPage();
    await page.setInputFiles("#file-input", FIXTURE);
    await page.waitForFunction((n) => {
      const el = document.getElementById("file-info");
      return el && !el.hidden && el.textContent.includes(n);
    }, path.basename(FIXTURE), { timeout: 240000 });
    const btns = await page.evaluate(() => ({
      list: !!document.getElementById("btn-font-list"),
      unify: !!document.getElementById("btn-font-unify"),
      listLabel: (document.getElementById("btn-font-list") || {}).textContent || "",
      unifyLabel: (document.getElementById("btn-font-unify") || {}).textContent || "",
    }));
    rec("ui_font_buttons_present", btns.list === true && btns.unify === true &&
      btns.listLabel.includes("Liệt kê font") && btns.unifyLabel.includes("Dùng chung 1 font"),
      "2 nút có sẵn trong tab Font: " + JSON.stringify(btns.listLabel) + " / " + JSON.stringify(btns.unifyLabel),
      performance.now() - t);
    // Mở view Công cụ rồi tab Font, sau đó bấm liệt kê.
    t = performance.now();
    await page.click("#btn-nav-tools");
    await page.click("#btn-font");
    await page.click("#btn-font-list");
    await page.waitForFunction(() => {
      const el = document.getElementById("font-result");
      return el && el.textContent.includes("GameFont.ttf") && el.textContent.includes("138/142");
    }, null, { timeout: 300000 });
    const listTxt = await page.$eval("#font-result", (el) => el.textContent || "");
    rec("ui_font_list_renders", listTxt.includes("5 font") && listTxt.includes("GameFont.ttf") &&
      listTxt.includes("138/142") && listTxt.includes("FullFont.ttf") &&
      listTxt.includes("GameFont SDF") && listTxt.includes("100/142") &&
      listTxt.includes("SourceCodePro-Regular") && listTxt.includes("font_list.json"),
      "liệt kê ra 5 font: GameFont.ttf 138/142 THIẾU, FullFont đủ, TMP 100/142, Font nhúng không đọc được, có font_list.json",
      performance.now() - t);
    // Bấm gom 1 font (dùng lại kết quả liệt kê — không quét lại file).
    t = performance.now();
    await page.click("#btn-font-unify");
    await page.waitForFunction(() => {
      const el = document.getElementById("font-result");
      return el && el.textContent.includes("font_unified.zip") && el.textContent.includes("GameFont.vi.ttf");
    }, null, { timeout: 300000 });
    const uniTxt = await page.$eval("#font-result", (el) => el.textContent || "");
    rec("ui_font_unify_renders", uniTxt.includes("Be Vietnam Pro") && uniTxt.includes("GameFont.vi.ttf") &&
      uniTxt.includes("font_unified.zip") && uniTxt.includes("Unity Editor") &&
      uniTxt.includes("Atlas SDF"),
      "gom 1 font ra kế hoạch: GameFont.vi.ttf + font_unified.zip + giới hạn Atlas SDF/Unity Editor",
      performance.now() - t);
    t = performance.now();
    const ov = await overflowCheck();
    await page.screenshot({ path: OUT + "/ui_font_unify_390px.png", fullPage: false });
    rec("ui_no_pageerror_no_overflow", pageErrors.length === 0 && !ov.overflow,
      "pageerror=" + pageErrors.length + " (" + pageErrors.slice(0, 2).join(" | ") + ") · tràn ngang: " +
      (ov.overflow ? ("scrollW=" + ov.scrollW + " > innerW=" + ov.innerW) : "không"),
      performance.now() - t);
  } catch (e) {
    rec("ui_playwright_section", false, "lỗi: " + (e && e.message ? e.message : String(e)), 0);
  }
}

// ================= 5. Hồi quy =================
function runHarness(name, cmd, check, timeoutMs) {
  const t = performance.now();
  const r = sh(cmd, EXP, timeoutMs || 1800000);
  const out = (r.stdout || "") + (r.stderr || "");
  rec(name, check(out), out.trim().split("\n").slice(-3).join(" / ").slice(0, 300), performance.now() - t);
}
if (want(5)) {
  runHarness("regress_module_worker",
    "node verify_module_worker.mjs 2>&1 | grep -E 'XONG'",
    (o) => /17 mục, 0 FAIL/.test(o));
  runHarness("regress_c2",
    "node verify_analysis_c2.mjs 2>&1 | grep -E 'TỔNG'",
    (o) => /13 mục, 0 FAIL/.test(o));
  runHarness("regress_c9",
    "node verify_analysis_c9.mjs 2>&1 | grep -E 'KẾT QUẢ C9'",
    (o) => /16\/16 PASS/.test(o));
  // env -u ONLY: harness con (A)/(B)/(C) KHÔNG được thừa hưởng ONLY của đợt
  // này — chúng phải chạy ĐẦY ĐỦ.
  runHarness("regress_translate_full_A",
    "env -u ONLY node verify_analysis_translate_full.mjs 2>&1 | grep -E 'TONG KET'",
    (o) => /33\/33 PASS, 0 FAIL/.test(o) && !/CHAY MOT PHAN/.test(o));
  runHarness("regress_mod_search_B",
    "env -u ONLY node verify_analysis_mod_search.mjs 2>&1 | grep -E 'TONG KET'",
    (o) => /25\/25 PASS, 0 FAIL/.test(o) && !/CHAY MOT PHAN/.test(o));
  runHarness("regress_reselect_C",
    "env -u ONLY node verify_analysis_reselect.mjs 2>&1 | grep -E 'TONG KET'",
    (o) => /26\/26 PASS, 0 FAIL/.test(o) && !/CHAY MOT PHAN/.test(o));
  runHarness("regress_merge_one_file",
    "env -u ONLY node verify_analysis_merge_one_file.mjs 2>&1 | grep -E 'TONG KET'",
    (o) => /27\/27 PASS, 0 FAIL/.test(o) && !/CHAY MOT PHAN/.test(o));
  runHarness("regress_item8_static",
    "python3 check_item8_static.py 2>&1 | tail -n 5",
    (o) => /"fails": \[\]/.test(o) || /0 fails/.test(o));
  {
    const t = performance.now();
    const r = sh("python3 -m unittest discover -s tests -t . 2>&1 | grep -E '^(Ran|OK|FAILED)'", SITE + "/pb3", 1800000);
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
const outJson = { when: new Date().toISOString(), total, passed, failures, results };
try {
  fs.writeFileSync(EXP + "/analysis_font_unify_results.json", JSON.stringify(outJson, null, 2));
} catch (e) {
  // Ghi chính lỗi EPERM thoáng qua (đã gặp 1 lần sau khi service restart):
  // ghi bản dự phòng thay vì crash toàn bộ harness.
  console.error("không ghi được analysis_font_unify_results.json: " + (e && e.message ? e.message : String(e)));
  fs.writeFileSync(EXP + "/analysis_font_unify_results.fallback.json", JSON.stringify(outJson, null, 2));
}
console.log("\n== TONG KET: " + passed + "/" + total + " PASS, " + failures + " FAIL (" + sec(performance.now() - t0) + "s)" +
  (ONLY ? " — CHAY MOT PHAN (ONLY=" + ONLY.join(",") + "), các mục khác lấy từ lần chạy trước" : "") + " ==");
if (failures) {
  for (const [k, v] of Object.entries(results)) if (!v.ok) console.log("FAIL: " + k + " | " + v.detail);
  process.exitCode = 1;
}
