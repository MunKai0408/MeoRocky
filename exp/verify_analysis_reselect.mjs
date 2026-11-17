// verify_analysis_reselect.mjs — Harness PHẦN (C): nút "Chọn lại file"
// ở mục "File gần đây" (Trang chủ). Chạy trên code hiện tại (chưa đẩy).
// Chạy: node verify_analysis_reselect.mjs (từ thư mục exp/ của repo)
//   ONLY=1,2,3,4,5 — chỉ chạy section đó (để mỗi lượt vừa giới hạn phiên).
// Kết quả: analysis_reselect_results.md + .json (ghi đè phần đã chạy)
//   + out/reselect/*.png (ảnh UI 390px)
//
// Section:
//  1. Tĩnh/thuần: hàm chọn lại tồn tại + định tuyến đúng tab; renderRecent
//     có nút "Chọn lại file" trên MỌI dòng (còn trong phiên: kèm "Tải lại";
//     hết phiên: kèm ghi chú "hết phiên — chọn lại file từ máy", KHÔNG có
//     "Tải lại"); sw.js CACHE_VERSION v23; files.json=95 / tests.json=168.
//  2. UI 390px — CẢ 5 game thật, CÒN TRONG PHIÊN: mở file ở khung chính
//     rồi bấm "Chọn lại file" trên dòng đó -> mở lại ngay (không mở hộp
//     chọn file), đúng tab Danh sách, state.file vẫn là file đó.
//  3. UI 390px — CẢ 5 game thật, HẾT PHIÊN: tải lại trang (chỉ còn
//     metadata localStorage) rồi bấm "Chọn lại file" -> mở hộp chọn file,
//     chọn đúng file đó -> khớp tên+dung lượng, mở lại đúng tab.
//  4. Trường hợp khác: chọn NHẦM file khác -> báo rõ tên/dung lượng file
//     vừa chọn so với file gần đây, vẫn mở file vừa chọn; nút "Xóa" vẫn
//     xóa dòng (kể cả sau tải lại trang); file .json còn trong phiên ->
//     mở trong tab Mod (đủ 11.356 key), hết phiên -> chọn lại vẫn vào Mod.
//  5. Hồi quy: module-worker 17/17, C2 13/13, C9 16/16, (A) 33/33,
//     (B) 24/24, item8 static 0 fails, full suite Python xanh.
//
// Lưu ý: Chromium trong sandbox không có HTTPS ngoài nên UI chạy trên HTTP
// local (127.0.0.1) phục vụ cây site/ — ĐÚNG code sẽ đẩy, chỉ khác host.
// KHÔNG phải Safari thật — Safari vẫn cần Joseph thử.
// File data.unity3d 590MB + polybridge3.ipa 482MB >400MB nên hộp thoại
// cảnh báo file lớn (mục C4) hiện khi mở — harness bấm "Tiếp tục".
import fs from "node:fs";
import path from "node:path";
import http from "node:http";
import { execSync, spawnSync } from "node:child_process";

const ROOT = decodeURIComponent(new URL("..", import.meta.url).pathname).replace(/\/$/, "");
const SITE = ROOT;
const EXP = ROOT + "/exp";
const OUT = EXP + "/out/reselect";
const REAL = "/home/hatch/workspace/real_ipa";
const EXTRACT_OUT = EXP + "/out/extract_5games";
fs.mkdirSync(OUT, { recursive: true });

const ONLY = process.env.ONLY ? process.env.ONLY.split(",").map((s) => s.trim()) : null;
const want = (n) => !ONLY || ONLY.includes(String(n));
const results = {};
try {
  const prev = JSON.parse(fs.readFileSync(EXP + "/analysis_reselect_results.json", "utf8"));
  if (prev && prev.results) Object.assign(results, prev.results);
} catch (_) {}
function rec(name, ok, detail, ms) {
  results[name] = { ok: !!ok, detail: String(detail || ""), ms: Math.round(ms || 0) };
  console.log((ok ? "PASS " : "FAIL ") + name + " | " + (detail || ""));
}
const t0 = performance.now();
const sec = (ms) => Math.round(ms / 100) / 10;
function sh(cmd, cwd, timeoutMs) {
  return spawnSync("bash", ["-c", cmd], { cwd: cwd || EXP, encoding: "utf8", timeout: timeoutMs || 600000 });
}

// 5 game thật (thứ tự: nhẹ trước, data.unity3d 590MB cuối).
const GAMES = [
  { id: "9kings", file: REAL + "/9kings.ipa", size: 173 },
  { id: "passpartout", file: REAL + "/passpartout.ipa", size: 159 },
  { id: "ek2", file: REAL + "/exploding_kittens2.ipa", size: 290 },
  { id: "pb3", file: REAL + "/polybridge3.ipa", size: 482 },
  { id: "data", file: REAL + "/data.unity3d", size: 590 },
];
const JSON_9K = EXTRACT_OUT + "/9kings/resources.monobehaviour.en.json"; // 11.356 entry

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
  return page;
}
async function reloadPage() {
  await page.reload({ waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForFunction(() => typeof state !== "undefined", null, { timeout: 30000 });
  await page.waitForSelector("#recent-list", { timeout: 10000 });
}
const elText = (id) => page.$eval("#" + id, (el) => el.textContent || "");
// Mở 1 file game/json ở khung chính (#file-input). File >400MB thì hộp
// thoại mục C4 hiện trước khi đọc — bấm "Tiếp tục".
async function openGameFile(fp) {
  const name = path.basename(fp);
  await page.setInputFiles("#file-input", fp);
  const modal = await page.waitForSelector("#large-file-modal:not([hidden])", { timeout: 3000 }).catch(() => null);
  if (modal) await page.click("#large-file-continue");
  await page.waitForFunction((n) => {
    const el = document.getElementById("file-info");
    return el && !el.hidden && el.textContent.includes(n);
  }, name, { timeout: 240000 });
  return name;
}
async function waitReselect(exp, timeoutMs) {
  await page.waitForFunction((e) => {
    const st = window.__PB3_RESELECT_STATE && window.__PB3_RESELECT_STATE();
    const l = st && st.last;
    return l && l.name === e.name && l.inSession === e.inSession && l.matched === e.matched && l.tab === e.tab;
  }, exp, { timeout: timeoutMs || 60000 });
}
async function rowInfo(name) {
  return page.evaluate((n) => {
    const rows = [...document.querySelectorAll("#recent-list .recent-row")];
    const row = rows.find((r) => (r.querySelector(".recent-name") || {}).textContent === n);
    if (!row) return null;
    return {
      hasDl: !!row.querySelector("button[data-act='dl']"),
      hasReselect: !!row.querySelector("button[data-act='reselect']"),
      hasRm: !!row.querySelector("button[data-act='rm']"),
      note: (row.querySelector(".note") || {}).textContent || "",
      text: row.textContent || "",
    };
  }, name);
}
async function clickReselect(name) {
  await page.locator(".recent-row", { hasText: name }).locator("button[data-act='reselect']").click({ timeout: 15000 });
}
async function overflowCheck() {
  return page.evaluate(() => ({
    scrollW: document.documentElement.scrollWidth, innerW: window.innerWidth,
    overflow: document.documentElement.scrollWidth > window.innerWidth + 1,
  }));
}

// ================= 1. Tĩnh / thuần =================
if (want(1)) {
  let t = performance.now();
  const appJs = fs.readFileSync(SITE + "/app.js", "utf8");
  const swJs = fs.readFileSync(SITE + "/sw.js", "utf8");
  const idx = fs.readFileSync(SITE + "/index.html", "utf8");
  const filesJson = JSON.parse(fs.readFileSync(SITE + "/pb3/files.json", "utf8"));
  const testsJson = JSON.parse(fs.readFileSync(SITE + "/pb3/tests.json", "utf8"));
  const noV17 = [];
  for (const f of ["check_item8_static.py", "check_analysis_c6_static.py", "check_analysis_c7_static.py",
    "check_analysis_c8_static.py", "check_analysis_c9_static.py", "verify_analysis_c8.mjs",
    "verify_analysis_c9.mjs", "verify_limit_2gb.mjs", "verify_patch01_web.mjs", "verify_phase1_ci.mjs",
    "verify_analysis_translate_full.mjs", "verify_analysis_mod_search.mjs"]) {
    const c = fs.readFileSync(EXP + "/" + f, "utf8");
    if (c.includes("v17-20261116") || c.includes("v17 ")) noV17.push(f);
  }
  rec("static_counts_and_cache",
    filesJson.files.length === 95 && testsJson.files.length === 168 &&
    swJs.includes('CACHE_VERSION = "v27-20261117"') && !swJs.includes("v19-20261116") &&
    noV17.length === 0,
    "files.json=" + filesJson.files.length + " (ky vong 95) · tests.json=" + testsJson.files.length +
    " (ky vong 168) · sw.js v27-20261117: " + swJs.includes('CACHE_VERSION = "v27-20261117"') +
    " · file exp con sot v17: " + (noV17.join(",") || "khong"), performance.now() - t);
  t = performance.now();
  const r = sh("node --check " + SITE + "/app.js && node --check " + SITE + "/worker.js && node --check " + SITE + "/worker-pool.js", EXP);
  rec("static_js_syntax", r.status === 0, r.status === 0 ? "node --check app.js + worker.js + worker-pool.js OK" : (r.stderr || "").slice(0, 300), performance.now() - t);

  await openPage();
  t = performance.now();
  const pure = await page.evaluate(() => {
    const out = {};
    out.fns = ["reselectTargetTab", "openJsonInMod", "reselectInSession", "openPickedReselect",
      "setCurrentGameFile", "recentInSessionEntry", "recentInSession"].every((f) => typeof window[f] === "function" || typeof eval(f) === "function");
    out.routes = {
      ipa: reselectTargetTab("9kings.ipa"), zip: reselectTargetTab("game.zip"),
      unity3d: reselectTargetTab("data.unity3d"), assets: reselectTargetTab("resources.assets"),
      tarxz: reselectTargetTab("a.tar.xz"), gz: reselectTargetTab("a.gz"),
      json: reselectTargetTab("en.json"), ttf: reselectTargetTab("font.ttf"),
    };
    out.hasStateFn = typeof window.__PB3_RESELECT_STATE === "function";
    // renderRecent: 1 dòng hết phiên + 1 dòng còn trong phiên (dữ liệu giả).
    addRecent({ name: "het-phien-test.zip", size: 1234, lastModified: 1 });
    const rowOut = document.querySelector("#recent-list").innerHTML;
    out.outSessionHtml = rowOut;
    state.file = { name: "het-phien-test.zip", size: 1234, lastModified: 1, data: new ArrayBuffer(8) };
    renderRecent();
    const rowIn = document.querySelector("#recent-list").innerHTML;
    out.inSessionHtml = rowIn;
    // Dọn: gỡ entry giả, trả state về rỗng.
    state.file = null;
    recentFiles = recentFiles.filter((e) => e.name !== "het-phien-test.zip");
    saveRecent(); renderRecent();
    return out;
  });
  const routesOk = pure.routes.ipa === "list" && pure.routes.zip === "list" &&
    pure.routes.unity3d === "list" && pure.routes.assets === "list" &&
    pure.routes.tarxz === "list" && pure.routes.gz === "list" &&
    pure.routes.json === "mod" && pure.routes.ttf === "font";
  const outHasReselect = (pure.outSessionHtml.match(/Chọn lại file/g) || []).length >= 1;
  const outHasNote = pure.outSessionHtml.includes("hết phiên — chọn lại file từ máy");
  const outNoDl = !pure.outSessionHtml.includes("Tải lại");
  const inHasDl = pure.inSessionHtml.includes("Tải lại");
  const inHasReselect = pure.inSessionHtml.includes("Chọn lại file");
  rec("pure_reselect_fns_and_rows",
    pure.fns && pure.hasStateFn && routesOk && outHasReselect && outHasNote && outNoDl && inHasDl && inHasReselect,
    "ham du: " + pure.fns + " · __PB3_RESELECT_STATE: " + pure.hasStateFn +
    " · dinh tuyen: " + JSON.stringify(pure.routes) +
    " · dong het phien: nut Chon lai=" + outHasReselect + ", ghi chu het phien=" + outHasNote + ", khong co Tai lai=" + outNoDl +
    " · dong con trong phien: Tai lai=" + inHasDl + ", Chon lai=" + inHasReselect,
    performance.now() - t);
  t = performance.now();
  const noteOk = idx.includes("file còn trong phiên thì") && idx.includes("mở lại ngay đúng tab làm việc") &&
    idx.includes("hết phiên thì mở hộp chọn file") && idx.includes("nếu chọn nhầm file") &&
    idx.includes('id="reselect-file-input"');
  rec("static_home_note_honest",
    noteOk,
    "ghi chu File gan day mo ta dung 2 truong hop + co input #reselect-file-input: " + noteOk,
    performance.now() - t);
}

// ================= 2. Còn trong phiên — 5 game =================
const five = {};
try {
  const prev = JSON.parse(fs.readFileSync(EXP + "/analysis_reselect_results.json", "utf8"));
  if (prev && prev.five_games) Object.assign(five, prev.five_games);
} catch (_) {}
if (want(2)) {
  await openPage();
  for (const g of GAMES) {
    let t = performance.now();
    const name = path.basename(g.file);
    if (!fs.existsSync(g.file)) {
      rec("game_" + g.id + "_reselect_insession", false, "thieu file that: " + g.file, performance.now() - t);
      continue;
    }
    await openGameFile(g.file);
    const row = await rowInfo(name);
    const before = await page.evaluate(() => window.__PB3_RESELECT_STATE().last);
    await clickReselect(name);
    await waitReselect({ name, inSession: true, matched: true, tab: "list" });
    const listVisible = await page.evaluate(() => !document.getElementById("tab-list").hidden);
    const curFile = await page.evaluate(() => state.file && state.file.name);
    const ov = await overflowCheck();
    five[g.id] = Object.assign(five[g.id] || {}, {
      file: name, sizeMB: g.size,
      inSession: { ok: listVisible && curFile === name, tab: "list", ms: Math.round(performance.now() - t) },
    });
    rec("game_" + g.id + "_reselect_insession",
      row && row.hasReselect && row.hasDl && row.hasRm && listVisible && curFile === name && !ov.overflow,
      "mo " + name + " (" + g.size + "MB) -> dong recent co Chon lai/Tai lai/Xoa: " +
      (row ? (row.hasReselect + "/" + row.hasDl + "/" + row.hasRm) : "khong co dong") +
      " · bam Chon lai -> tab Danh sach hien: " + listVisible + " · state.file van la " + curFile +
      " · khong tran 390px (scrollW=" + ov.scrollW + ")" +
      " · last truoc khi bam: " + JSON.stringify(before),
      performance.now() - t);
    // Quay về Trang chủ để game sau mở ở khung chính như user thật.
    await page.click("#btn-nav-home");
    await page.waitForSelector("#recent-list", { timeout: 10000 });
  }
  await page.screenshot({ path: OUT + "/recent_insession_390px.png", fullPage: false });
  globalThis.__five = five;
}

// ================= 3. Hết phiên — 5 game (tải lại trang) =================
if (want(3)) {
  await openPage();
  for (const g of GAMES) {
    let t = performance.now();
    const name = path.basename(g.file);
    if (!fs.existsSync(g.file)) {
      rec("game_" + g.id + "_reselect_outsession", false, "thieu file that: " + g.file, performance.now() - t);
      continue;
    }
    await openGameFile(g.file);
    await reloadPage();
    const row = await rowInfo(name);
    const rowOk = row && row.hasReselect && !row.hasDl && row.hasRm &&
      row.note.includes("hết phiên — chọn lại file từ máy");
    let openedOk = false, lastOk = false, detail = "";
    if (rowOk) {
      const [chooser] = await Promise.all([
        page.waitForEvent("filechooser", { timeout: 15000 }),
        clickReselect(name),
      ]);
      await chooser.setFiles(g.file);
      const modal = await page.waitForSelector("#large-file-modal:not([hidden])", { timeout: 3000 }).catch(() => null);
      if (modal) await page.click("#large-file-continue");
      await page.waitForFunction((n) => {
        const el = document.getElementById("file-info");
        return el && !el.hidden && el.textContent.includes(n);
      }, name, { timeout: 240000 });
      await waitReselect({ name, inSession: false, matched: true, tab: "list" });
      const listVisible = await page.evaluate(() => !document.getElementById("tab-list").hidden);
      const curFile = await page.evaluate(() => state.file && state.file.name);
      openedOk = listVisible && curFile === name;
      lastOk = openedOk;
      detail = " · chon lai dung file -> tab Danh sach: " + listVisible + " · state.file=" + curFile;
    } else {
      detail = " · dong recent sau tai lai trang khong dung: " + JSON.stringify(row);
    }
    const ov = await overflowCheck();
    five[g.id] = Object.assign(five[g.id] || {}, {
      outSession: { ok: rowOk && openedOk && !ov.overflow, matched: true, ms: Math.round(performance.now() - t) },
    });
    rec("game_" + g.id + "_reselect_outsession",
      rowOk && openedOk && !ov.overflow,
      "tai lai trang -> dong " + name + ": Chon lai=" + (row && row.hasReselect) + ", khong Tai lai=" + (row && !row.hasDl) +
      ", ghi chu het phien=" + (row && row.note.includes("hết phiên")) + detail +
      " · khong tran 390px (scrollW=" + ov.scrollW + ")", performance.now() - t);
    await page.click("#btn-nav-home");
    await page.waitForSelector("#recent-list", { timeout: 10000 });
  }
  await page.screenshot({ path: OUT + "/recent_outsession_390px.png", fullPage: false });
  globalThis.__five = five;
}

// ================= 4. Nhầm file + Xóa + JSON =================
if (want(4)) {
  await openPage();
  // 4a. Chọn nhầm file khác: recent 9kings, chọn passpartout.
  {
    let t = performance.now();
    await openGameFile(GAMES[0].file);
    await reloadPage();
    const [chooser] = await Promise.all([
      page.waitForEvent("filechooser", { timeout: 15000 }),
      clickReselect("9kings.ipa"),
    ]);
    await chooser.setFiles(GAMES[1].file);
    await page.waitForFunction((n) => {
      const el = document.getElementById("file-info");
      return el && !el.hidden && el.textContent.includes(n);
    }, "passpartout.ipa", { timeout: 120000 });
    await waitReselect({ name: "passpartout.ipa", inSession: false, matched: false, tab: "list" });
    const errTxt = await page.$eval("#error", (el) => el.textContent || "").catch(() => "");
    const errVisible = await page.$eval("#error", (el) => !el.hidden).catch(() => false);
    const curFile = await page.evaluate(() => state.file && state.file.name);
    const listVisible = await page.evaluate(() => !document.getElementById("tab-list").hidden);
    rec("mismatch_reports_and_opens_new",
      errVisible && errTxt.includes("khác với file gần đây") && errTxt.includes("9kings.ipa") &&
      errTxt.includes("passpartout.ipa") && curFile === "passpartout.ipa" && listVisible,
      "chon passpartout thay cho 9kings -> banner hien: " + errVisible + " · noi dung: " + errTxt.slice(0, 160) +
      " · state.file=" + curFile + " · tab Danh sach: " + listVisible, performance.now() - t);
  }
  // 4b. Nút Xóa vẫn xóa dòng, kể cả sau tải lại trang.
  {
    let t = performance.now();
    await page.click("#btn-nav-home");
    await page.waitForSelector("#recent-list", { timeout: 10000 });
    const before = await rowInfo("passpartout.ipa");
    await page.locator(".recent-row", { hasText: "passpartout.ipa" }).locator("button[data-act='rm']").click({ timeout: 15000 });
    await page.waitForFunction(() => {
      const rows = [...document.querySelectorAll("#recent-list .recent-row")];
      return !rows.some((r) => (r.querySelector(".recent-name") || {}).textContent === "passpartout.ipa");
    }, null, { timeout: 10000 });
    await reloadPage();
    const after = await rowInfo("passpartout.ipa");
    rec("delete_button_still_works",
      before && before.hasRm && after === null,
      "truoc khi xoa dong passpartout co nut Xoa: " + !!(before && before.hasRm) +
      " · sau khi xoa + tai lai trang: dong con khong: " + (after !== null), performance.now() - t);
  }
  // 4c. File .json: còn trong phiên -> Mod; hết phiên -> chọn lại -> Mod.
  {
    let t = performance.now();
    if (!fs.existsSync(JSON_9K)) {
      rec("json_reselect_insession", false, "thieu " + JSON_9K, 0);
      rec("json_reselect_outsession", false, "thieu " + JSON_9K, 0);
    } else {
      const jname = path.basename(JSON_9K);
      await openGameFile(JSON_9K);
      const row = await rowInfo(jname);
      await clickReselect(jname);
      await waitReselect({ name: jname, inSession: true, matched: true, tab: "mod" });
      await page.waitForFunction((n) => {
        const el = document.querySelector("#mod-json-info");
        return el && el.textContent.includes("Đã mở " + n);
      }, jname, { timeout: 30000 });
      const infoIn = await elText("mod-json-info");
      const modVisible = await page.evaluate(() => !document.getElementById("tab-mod").hidden);
      const keysIn = await page.evaluate(() => state.modJson && state.modJson.keys.length);
      rec("json_reselect_insession",
        row && row.hasReselect && modVisible && infoIn.includes("11356 entry") && keysIn === 11356,
        "mo " + jname + " -> bam Chon lai (con trong phien) -> tab Mod: " + modVisible +
        " · info: " + infoIn.slice(0, 120) + " · keys=" + keysIn, performance.now() - t);
      t = performance.now();
      await reloadPage();
      const row2 = await rowInfo(jname);
      const [chooser] = await Promise.all([
        page.waitForEvent("filechooser", { timeout: 15000 }),
        clickReselect(jname),
      ]);
      await chooser.setFiles(JSON_9K);
      await waitReselect({ name: jname, inSession: false, matched: true, tab: "mod" });
      await page.waitForFunction((n) => {
        const el = document.querySelector("#mod-json-info");
        return el && el.textContent.includes("Đã mở " + n);
      }, jname, { timeout: 30000 });
      const infoOut = await elText("mod-json-info");
      const modVisible2 = await page.evaluate(() => !document.getElementById("tab-mod").hidden);
      rec("json_reselect_outsession",
        row2 && row2.hasReselect && !row2.hasDl && modVisible2 && infoOut.includes("11356 entry"),
        "tai lai trang -> dong JSON: Chon lai=" + (row2 && row2.hasReselect) + ", khong Tai lai=" + (row2 && !row2.hasDl) +
        " · chon lai dung file -> tab Mod: " + modVisible2 + " · info: " + infoOut.slice(0, 120),
        performance.now() - t);
    }
  }
  {
    let t = performance.now();
    await page.screenshot({ path: OUT + "/reselect_json_mod_390px.png", fullPage: false });
    rec("ui_no_page_errors", pageErrors.length === 0, pageErrors.slice(0, 3).join(" | ") || "khong co pageerror", performance.now() - t);
  }
}

// ================= 5. Hồi quy =================
if (want(5)) {
  const runHarness = (name, cmd, parse, timeoutMs) => {
    const t = performance.now();
    const r = sh(cmd, EXP, timeoutMs);
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
  // env -u ONLY: harness con (A)/(B) KHONG được thừa hưởng ONLY của đợt
  // này — chúng phải chạy ĐẦY ĐỦ (chạy từng phần rồi gộp kết quả cũ sẽ
  // cho số 33/33, 24/24 không trung thực sau khi sửa code).
  runHarness("regress_translate_full_A",
    "env -u ONLY node verify_analysis_translate_full.mjs 2>&1 | grep -E 'TONG KET'",
    (o) => /33\/33 PASS, 0 FAIL/.test(o) && !/CHAY MOT PHAN/.test(o), 1800000);
  runHarness("regress_mod_search_B",
    "env -u ONLY node verify_analysis_mod_search.mjs 2>&1 | grep -E 'TONG KET'",
    (o) => /25\/25 PASS, 0 FAIL/.test(o) && !/CHAY MOT PHAN/.test(o), 1800000);
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
console.log("\n== TONG KET: " + passed + "/" + total + " PASS, " + failures + " FAIL (" + sec(performance.now() - t0) + "s)" +
  (ONLY ? " — CHAY MOT PHAN (ONLY=" + ONLY.join(",") + "), cac muc khac lay tu lan chay truoc" : "") + " ==");
const summary = {
  generated_at: new Date().toISOString(),
  total, passed, failed: total - passed,
  results,
  five_games: globalThis.__five || five,
  page_errors: pageErrors,
  notes: "Chromium sandbox khong co HTTPS ngoai: UI chay tren HTTP local 127.0.0.1 (dung code se day). KHONG phai Safari that. Data.unity3d 590MB + polybridge3.ipa 482MB >400MB nen hop thoai canh bao file lon (C4) hien khi mo — harness bam Tiep tuc.",
};
fs.writeFileSync(EXP + "/analysis_reselect_results.json", JSON.stringify(summary, null, 2), "utf8");
process.exit(failures ? 1 : 0);
