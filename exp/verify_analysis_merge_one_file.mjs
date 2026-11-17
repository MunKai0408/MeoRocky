// verify_analysis_merge_one_file.mjs — Harness GOP 1 FILE (Joseph 23:35):
// trich xuat gop thanh dung 1 file en.json/zh.json moi ngon ngu.
// Chay: node verify_analysis_merge_one_file.mjs (tu thu muc exp/ cua repo)
//   ONLY=1,2,3,4,5 — chi chay section do (de moi luot vua gioi han phien).
// Ket qua: analysis_merge_one_file_results.json (+ .md viet tay sau).
//   + out/merge_one_file/*.png (anh UI 390px)
//
// Section:
//  1. Unit Python (tests.test_merge_one_file, 10 test) + tinh:
//     files.json=94 / tests.json=165 / sw.js CACHE_VERSION v20, node --check.
//  2. Fixture acceptance (CPython bridge, file resources.assets that cua
//     Joseph): en -> dung 1 file en.json (9.753 key), zh -> dung 1 file
//     zh.json (3.380 key); doi chieu mau 2.086 key (thu tu, so key co);
//     mac dinh khong merge van ra file tach per-unit; chung minh bang code
//     vi sao mau khong the khop 100% (bang I2Languages build nay khong co
//     khe tieng Anh; 22 term vang mat hoan toan).
//  3. 5 game that (CPython, merge_5games.py chay moi): file gop == hop cac
//     file tach per-unit (thu tu + gia tri), 0 key #N, trung key/xung dot
//     dem du, sidecar provenance + report khop, file config giu nguyen.
//  4. UI tren Chromium that 390px: checkbox "Gop thanh 1 file" co san +
//     tick mac dinh; trich xuat fixture (en) ra en.json + dong bao gop;
//     bo tick trich xuat lai ra file tach per-unit; 0 page error, khong tran.
//  5. Hoi quy: module-worker 17/17, C2 13/13, C9 16/16, (A) 33/33,
//     (B) 24/24, (C) 26/26, item8 static 0 fails, full suite Python xanh.
//
// Luu y: Chromium trong sandbox khong co HTTPS ngoai nen UI chay tren HTTP
// local (127.0.0.1) phuc vu cay site/ — DUNG code se day, chi khac host.
// KHONG phai Safari that — Safari van can Joseph thu.
import fs from "node:fs";
import path from "node:path";
import http from "node:http";
import { execSync, spawnSync } from "node:child_process";

const ROOT = decodeURIComponent(new URL("..", import.meta.url).pathname).replace(/\/$/, "");
const SITE = ROOT;
const EXP = ROOT + "/exp";
const OUT = EXP + "/out/merge_one_file";
const FIXTURE = "/home/hatch/workspace/user/files/EAC193F5-FBAE-43A0-8CE2-6748EA5AF5DC-resources.assets";
const JOSEPH_EN = "/home/hatch/workspace/user/files/en.json";
fs.mkdirSync(OUT, { recursive: true });

const ONLY = process.env.ONLY ? process.env.ONLY.split(",").map((s) => s.trim()) : null;
const want = (n) => !ONLY || ONLY.includes(String(n));
const results = {};
try {
  const prev = JSON.parse(fs.readFileSync(EXP + "/analysis_merge_one_file_results.json", "utf8"));
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

// ================= 1. Unit Python + tinh =================
if (want(1)) {
  let t = performance.now();
  const r = sh("python3 -m unittest tests.test_merge_one_file 2>&1", SITE + "/pb3");
  const m = (r.stdout || "").match(/Ran (\d+) tests? in [\d.]+s\n\n(OK|FAILED)/);
  const ok = r.status === 0 && !!m && m[2] === "OK";
  rec("py_unit_merge_one_file", ok,
    (m ? m[1] + " test, " + m[2] : "khong doc duoc ket qua") + " | " + (r.stdout || "").trim().split("\n").slice(-3).join(" / "),
    performance.now() - t);
  t = performance.now();
  const filesJson = JSON.parse(fs.readFileSync(SITE + "/pb3/files.json", "utf8"));
  const testsJson = JSON.parse(fs.readFileSync(SITE + "/pb3/tests.json", "utf8"));
  const swJs = fs.readFileSync(SITE + "/sw.js", "utf8");
  const noV17 = [];
  for (const f of ["check_item8_static.py", "check_analysis_c6_static.py", "check_analysis_c7_static.py",
    "check_analysis_c8_static.py", "check_analysis_c9_static.py", "verify_analysis_c8.mjs",
    "verify_analysis_c9.mjs", "verify_limit_2gb.mjs", "verify_patch01_web.mjs", "verify_phase1_ci.mjs",
    "verify_analysis_translate_full.mjs", "verify_analysis_mod_search.mjs",
    "verify_analysis_reselect.mjs"]) {
    const c = fs.readFileSync(EXP + "/" + f, "utf8");
    if (f !== "verify_analysis_reselect.mjs" && (c.includes("v17-20261116") || c.includes("== 162") || c.includes("=== 162") || c.includes("ky vong 162") || c.includes("tests.json=162"))) noV17.push(f);
  }
  rec("static_counts_and_cache",
    filesJson.files.length === 94 && testsJson.files.length === 165 &&
    swJs.includes('CACHE_VERSION = "v20-20261117"') && !swJs.includes("v19-20261116") &&
    noV17.length === 0,
    "files.json=" + filesJson.files.length + " (ky vong 94) · tests.json=" + testsJson.files.length +
    " (ky vong 165) · sw.js v20-20261117: " + swJs.includes('CACHE_VERSION = "v20-20261117"') +
    " · file exp con sot v17/162: " + (noV17.join(",") || "khong"), performance.now() - t);
  t = performance.now();
  const c = sh("node --check " + SITE + "/app.js && node --check " + SITE + "/worker.js && node --check " + SITE + "/sw.js && python3 -m py_compile " + SITE + "/pb3/bridge.py " + SITE + "/pb3/sdk/extraction.py " + SITE + "/pb3/sdk/exporters.py " + SITE + "/pb3/tests/test_merge_one_file.py", EXP);
  rec("static_syntax", c.status === 0, c.status === 0 ? "node --check app.js/worker.js/sw.js + py_compile bridge/extraction/exporters/test OK" : (c.stderr || "").slice(0, 300), performance.now() - t);
}

// ================= 2. Fixture acceptance =================
if (want(2)) {
  let t = performance.now();
  const r = sh("python3 exp/check_merge_fixture.py 2>&1 | tail -n 8", ROOT);
  const fx = JSON.parse(fs.readFileSync(OUT + "/fixture.json", "utf8"));
  rec("fixture_en_one_file",
    r.status === 0 && fx.en.ok === true && fx.en.locale_en_files === 1 &&
    fx.en.exported_en[0] === "en.json" && fx.en.keys === 9753 &&
    fx.en.hash_suffix_keys === 0 && fx.en.first_key === "AfterGameUISO.s1" &&
    fx.en.config_files.length === 5 && fx.en.sidecar_keys === 9753 &&
    fx.en.sidecar_format === "beartea-merge-provenance/1",
    "en.json " + fx.en.keys + " key (" + fx.en.bytes + " byte), 1 file locale en, #N=" +
    fx.en.hash_suffix_keys + ", config giu " + fx.en.config_files.length + " file, sidecar " +
    fx.en.sidecar_keys + " key", performance.now() - t);
  t = performance.now();
  rec("fixture_en_sample_comparison",
    fx.en.sample_present === 560 && fx.en.sample_total === 2086 &&
    fx.en.sample_order_preserved === true &&
    fx.en.sample_first_present_key === "Achievement_KingoftheHill" &&
    fx.en.sample_first_present_pos === 231,
    "mau co " + fx.en.sample_present + "/" + fx.en.sample_total + " key, thu tu tuong doi giu nguyen, " +
    "key mau dau tien co mat o vi tri " + fx.en.sample_first_present_pos +
    " — KHONG khop 100% vi du lieu goc thieu (xem muc tiep theo)", performance.now() - t);
  t = performance.now();
  rec("fixture_why_not_2086",
    fx.raw_no_filter.total_strings === 66011 &&
    fx.raw_no_filter.his_winningness_present === false &&
    fx.raw_no_filter.win_one_game_present === false &&
    fx.raw_no_filter.a_little_treat_present === false &&
    fx.union_terms.distinct_term_keys === 2065 &&
    fx.union_terms.sample_present_in_union === 2064 &&
    fx.union_terms.sample_absent_entirely === 22 &&
    fx.union_terms.sample_in_union_but_no_english === 1504,
    "khong loc: 66.011 chuoi tho, 'His Winningness'/'Win one game.'/'A Little Treat' deu KHONG co trong file; " +
    "hop term qua moi ngon ngu 2.065 (mau co 2.064/2.086, vang mat hoan toan 22 key, 1.504 key co trong union " +
    "nhung khong co ban tieng Anh trong file — bang I2Languages build nay khong co khe en)", performance.now() - t);
  t = performance.now();
  rec("fixture_en_impurity_inherited_from_split",
    fx.en.en_value_equals_sample_vi === 347,
    "347/560 key mau co mat co gia tri bang dung ban dich tieng Viet cua Joseph " +
    "(chuoi tieng Viet bi gan nham vao file en o buoc tach (A) — do nhiem ke thua tu file tach, " +
    "buoc gop giu nguyen 100% noi dung file tach, khong tu sua; can sua can chinh vi tri (A) o dot sau)",
    performance.now() - t);
  t = performance.now();
  rec("fixture_zh_one_file",
    fx.zh.ok === true && fx.zh.locale_zh_files === 1 &&
    fx.zh.exported_zh[0] === "zh.json" && fx.zh.keys === 3380 &&
    fx.zh.hash_suffix_keys === 0 && fx.zh.cjk_values === 3355 &&
    fx.zh.sidecar_keys === 3380 && fx.zh.first_key === "Achievement_HisWinningness",
    "zh.json " + fx.zh.keys + " key, CJK " + fx.zh.cjk_values + "/" + fx.zh.keys +
    ", #N=0, key dau 'Achievement_HisWinningness'", performance.now() - t);
  t = performance.now();
  rec("fixture_merge_off_keeps_perunit",
    fx.merge_off_default.ok === true && fx.merge_off_default.has_en_json === false &&
    fx.merge_off_default.has_perunit_en === true,
    "khong truyen merge: khong co en.json, van co resources.monobehaviour.en.json (API/harness cu khong doi)",
    performance.now() - t);
}

// ================= 3. 5 game that =================
if (want(3)) {
  let t = performance.now();
  const r = sh("python3 exp/merge_5games.py 2>&1 | tail -n 20", ROOT);
  const sum = JSON.parse(fs.readFileSync(OUT + "/summary.json", "utf8"));
  const g = sum.games;
  const games = ["9kings", "data", "ek2", "pb3", "passpartout"];
  const allOk = games.every((k) => g[k] && g[k].perunit.ok === true && g[k].merged.ok === true);
  rec("merge_5games_runs_ok", allOk && r.status === 0,
    games.map((k) => k + "=" + (g[k] ? (g[k].merged.total_entries + " entry/" + g[k].merged.seconds + "s") : "?")).join(" · "),
    performance.now() - t);
  for (const k of games) {
    t = performance.now();
    const c = g[k].compare;
    const ok = c.en.order_and_values_match && c.zh.order_and_values_match &&
      c.en.hash_suffix_keys === 0 && c.zh.hash_suffix_keys === 0 &&
      c.en.sidecar_ok && c.zh.sidecar_ok && c.en.report_stat_ok && c.zh.report_stat_ok &&
      c.config_unchanged === true;
    rec("merge_5games_" + k, ok,
      "en " + c.en.merged_keys + " key (tu " + c.en.perunit_files + " file, tong " + c.en.perunit_entries_sum +
      ", trung giong " + c.en.duplicates_identical + ", xung dot " + c.en.conflicts + ") · zh " +
      c.zh.merged_keys + " key (tu " + c.zh.perunit_files + " file, trung giong " + c.zh.duplicates_identical +
      ", xung dot " + c.zh.conflicts + ") · config giu nguyen " + c.config_files.length + " file" +
      (c.en.conflicts ? " · xung dot en vd: " + JSON.stringify(c.en.conflict_keys.slice(0, 3)) : ""),
      performance.now() - t);
  }
}

// ================= 4. UI Chromium that 390px =================
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
  try {
    let t = performance.now();
    await openPage();
    await page.setInputFiles("#file-input", FIXTURE);
    await page.waitForFunction((n) => {
      const el = document.getElementById("file-info");
      return el && !el.hidden && el.textContent.includes(n);
    }, path.basename(FIXTURE), { timeout: 240000 });
    const cb = await page.evaluate(() => {
      const el = document.getElementById("extract-merge-one");
      return el ? { exists: true, checked: el.checked, label: (el.closest("label") || {}).textContent || "" } : { exists: false };
    });
    rec("ui_merge_checkbox_default_checked", cb.exists === true && cb.checked === true && cb.label.includes("en.json"),
      "checkbox #extract-merge-one co san, tick mac dinh, nhan co 'en.json': " + JSON.stringify(cb.label).slice(0, 120),
      performance.now() - t);
    // Trich xuat en, checkbox tick -> ra en.json + dong bao gop.
    t = performance.now();
    await page.click("#btn-card-extract");
    await page.waitForSelector("#extract-locales", { timeout: 10000 });
    await page.selectOption("#extract-locales", "en");
    await page.click("#btn-extract-run");
    await page.waitForFunction(() => {
      const el = document.getElementById("extract-result");
      return el && el.textContent.includes("en.json") && el.textContent.includes("Gộp thành 1 file");
    }, null, { timeout: 300000 });
    const resTxt = await page.$eval("#extract-result", (el) => el.textContent || "");
    const lastFiles = await page.evaluate(() => (state.lastExtractFiles || []).map((f) => (f.exported || "") + "|" + (f.locale || "")));
    const defFile = await page.evaluate(() => (typeof defaultTranslateFile === "function" ? defaultTranslateFile() : null));
    rec("ui_extract_merged_en_json", resTxt.includes("en.json") && resTxt.includes("Gộp thành 1 file") &&
      lastFiles.some((s) => s === "en.json|en") && !lastFiles.some((s) => s.includes("monobehaviour.en.json")) &&
      defFile === "en.json",
      "ket qua co en.json + dong 'Gop thanh 1 file'; file list: " + lastFiles.join(", ") +
      "; file dich mac dinh: " + defFile, performance.now() - t);
    // Bo tick -> trich xuat lai -> ra file tach per-unit, khong co en.json.
    t = performance.now();
    await page.uncheck("#extract-merge-one");
    await page.click("#btn-extract-run");
    await page.waitForFunction(() => {
      const el = document.getElementById("extract-result");
      return el && el.textContent.includes("monobehaviour.en.json") && !el.textContent.includes("Gộp thành 1 file");
    }, null, { timeout: 300000 });
    const lastFiles2 = await page.evaluate(() => (state.lastExtractFiles || []).map((f) => (f.exported || "") + "|" + (f.locale || "")));
    rec("ui_extract_unchecked_keeps_perunit", lastFiles2.some((s) => s.includes("monobehaviour.en.json")) &&
      !lastFiles2.some((s) => s === "en.json|en"),
      "bo tick: file list co resources.monobehaviour.en.json, khong co en.json: " + lastFiles2.join(", "),
      performance.now() - t);
    t = performance.now();
    const ov = await overflowCheck();
    await page.screenshot({ path: OUT + "/ui_merge_390px.png", fullPage: false });
    rec("ui_no_pageerror_no_overflow", pageErrors.length === 0 && !ov.overflow,
      "pageerror=" + pageErrors.length + " (" + pageErrors.slice(0, 2).join(" | ") + ") · tran ngang: " +
      (ov.overflow ? ("scrollW=" + ov.scrollW + " > innerW=" + ov.innerW) : "khong"),
      performance.now() - t);
  } catch (e) {
    rec("ui_playwright_section", false, "loi: " + (e && e.message ? e.message : String(e)), 0);
  }
}

// ================= 5. Hoi quy =================
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
  // env -u ONLY: harness con (A)/(B)/(C) KHONG duoc thua huong ONLY cua dot
  // nay — chung phai chay DAY DU.
  runHarness("regress_translate_full_A",
    "env -u ONLY node verify_analysis_translate_full.mjs 2>&1 | grep -E 'TONG KET'",
    (o) => /33\/33 PASS, 0 FAIL/.test(o) && !/CHAY MOT PHAN/.test(o));
  runHarness("regress_mod_search_B",
    "env -u ONLY node verify_analysis_mod_search.mjs 2>&1 | grep -E 'TONG KET'",
    (o) => /25\/25 PASS, 0 FAIL/.test(o) && !/CHAY MOT PHAN/.test(o));
  runHarness("regress_reselect_C",
    "env -u ONLY node verify_analysis_reselect.mjs 2>&1 | grep -E 'TONG KET'",
    (o) => /26\/26 PASS, 0 FAIL/.test(o) && !/CHAY MOT PHAN/.test(o));
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
fs.writeFileSync(EXP + "/analysis_merge_one_file_results.json", JSON.stringify(
  { when: new Date().toISOString(), total, passed, failures, results }, null, 2));
console.log("\n== TONG KET: " + passed + "/" + total + " PASS, " + failures + " FAIL (" + sec(performance.now() - t0) + "s)" +
  (ONLY ? " — CHAY MOT PHAN (ONLY=" + ONLY.join(",") + "), cac muc khac lay tu lan chay truoc" : "") + " ==");
if (failures) {
  for (const [k, v] of Object.entries(results)) if (!v.ok) console.log("FAIL: " + k + " | " + v.detail);
  process.exitCode = 1;
}
