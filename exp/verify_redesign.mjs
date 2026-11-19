// verify_redesign.mjs — Redesign BearTea theo mockup của Joseph (2026-11-16):
// CHỈ đổi vỏ: header + hero + khung thả file + 6 thẻ + File gần đây +
// Thông tin Unity + bottom nav 5 mục + sáng/tối; giữ nguyên toàn bộ chức
// năng (11 thao tác, worker protocol, download Share, bảng vỡ chữ, không
// accept ở input file chính).
// Chạy: node verify_redesign.mjs (từ thư mục exp/ của repo)
// Kết quả: verify_redesign_results.json/.md + redesign_390_*.png
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import vm from "node:vm";
import { chromium } from "playwright-core";

const ROOT = decodeURIComponent(new URL("..", import.meta.url).pathname).replace(/\/$/, "");
const SITE = ROOT;
const EXP = ROOT + "/exp";
const GAME1 = SITE + "/pb3/tests/fixtures/extract/game1.zip";
const UCONTAINER = EXP + "/unity_container.zip";
const EXE = process.env.HOME + "/.cache/ms-playwright/chromium_headless_shell-1148/chrome-linux/headless_shell";
const sec = (ms) => Math.round(ms / 100) / 10;
const results = {};
let failures = 0;
function rec(name, ok, detail, ms) {
  results[name] = { ok, detail, seconds: sec(ms || 0) };
  if (!ok) failures++;
  console.log(`\n### ${name}: ${ok ? "PASS" : "FAIL"} (${sec(ms || 0)}s)\n${detail}`);
}
function assert(cond, msg) { if (!cond) throw new Error(msg); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const idxHtml = fs.readFileSync(SITE + "/index.html", "utf8");
const appJs = fs.readFileSync(SITE + "/app.js", "utf8");
const workerJs = fs.readFileSync(SITE + "/worker.js", "utf8");

// ============ Part A: kiểm tra tĩnh ============
{
  const t0 = performance.now();
  try {
    const checks = [];
    const ck = (name, ok) => { checks.push(name + "=" + ok); assert(ok, "thiếu/sai: " + name); };
    // A1. Mọi ID app.js dùng đều còn trong index.html (trừ 2 ID tạo động).
    const used = new Set([...appJs.matchAll(/\$\("([^"]+)"\)/g)].map((m) => m[1]));
    const have = new Set([...idxHtml.matchAll(/id="([^"]+)"/g)].map((m) => m[1]));
    const dynamic = new Set(["unity-bundle-select", "btn-unity-inspect-entry"]);
    const missing = [...used].filter((u) => !have.has(u) && !dynamic.has(u));
    ck("all_appjs_ids_present", missing.length === 0);
    // A2. Input file: 2 input chính không accept; json/sqlite giữ accept.
    const tag = (id) => (idxHtml.match(new RegExp(`<input[^>]*id="${id}"[^>]*>`)) || [""])[0];
    ck("file_input_no_accept", !/accept=/.test(tag("file-input")));
    ck("batch_files_no_accept", !/accept=/.test(tag("batch-files")));
    ck("translate_json_kept", /accept="\.json"/.test(tag("translate-json-file")));
    ck("kb_import_kept", /\.sqlite/.test(tag("kb-import-file")));
    // A3. Download Share + bảng vỡ chữ + mục 3 KB giữ nguyên.
    ck("share_btn_markup", /id="btn-share-download"[^>]*>Lưu \/ Chia sẻ</.test(idxHtml));
    ck("blob_btn_markup", /id="btn-blob-download"[^>]*>Tải về \(blob\)</.test(idxHtml));
    ck("no_5s_revoke", !/setTimeout\([^)]*,\s*5000\s*\)/.test(appJs));
    ck("revoke_10min", /600000/.test(appJs));
    ck("pagehide_revoke", /addEventListener\("pagehide", revokeLastDownloadUrl\)/.test(appJs));
    ck("css_no_global_breakall", !/th, td \{[^}]*word-break: break-all/s.test(idxHtml));
    ck("css_nowrap", /th, td \{[^}]*white-space: nowrap/s.test(idxHtml));
    ck("css_firstcol_break", /td:first-child \{[^}]*word-break: break-all/s.test(idxHtml));
    ck("css_table_scroll", /table \{[^}]*overflow-x: auto/s.test(idxHtml));
    ck("kb_ui_kept", ["kb-status", "kb-persist", "kb-export-reminder", "btn-pwa-install"]
      .every((id) => have.has(id)) && idxHtml.includes('rel="manifest" href="manifest.webmanifest"')
      && idxHtml.indexOf('src="kb-store.js"') < idxHtml.indexOf('src="app.js"'));
    ck("no_muc1_left", !/mục 1/.test(appJs));
    ck("binary_note_ge4", (idxHtml.match(/Asset nhị phân \(texture\/âm thanh\): chỉ đọc, dùng bản iSH/g) || []).length >= 4);
    ck("translate_proxy_kept", idxHtml.includes('id="translate-proxy"') && idxHtml.includes("diagnostics.html"));
    // "1GB" chi cam khi noi ve gioi han file; "~1GB" la gioi han RAM tab Safari
    // (canh bao gate-crash tu PR #44, van dung) nen loai truoc khi kiem.
    ck("limit_2gb_text", /≤2GB/.test(idxHtml) && /1–1,5GB/.test(idxHtml) && !/1GB/.test(idxHtml) && !/1GB/.test(appJs.replace(/~1GB/g, "")));
    // A4. Redesign: không URL ngoài, không ảnh ngoài, không file mẫu bịa,
    // không COOP/COEP, đủ view/nav/thẻ, không còn theme.
    // Chi cam TAI tai nguyen ngoai luc mo trang (script/link/img, CSS url(),
    // import dong); <a href> tai lieu + endpoint API GitHub (chi goi khi
    // nguoi dung bam dispatch) duoc phep — tu PR #53.
    ck("no_external_url", !/<(script|link|img)[^>]*(src|href)\s*=\s*["']https?:\/\//.test(idxHtml + appJs)
      && !/url\(\s*["']?https?:\/\//.test(idxHtml + appJs)
      && !/import\(\s*["']https?:\/\//.test(appJs));
    // Chi cam anh NGOAI (hotlink); icon gau local <img src="icons/..."> la
    // dung thiet ke moi (Duy chot anh day du 1242x1242 lam icon chinh).
    ck("no_img_tag", !/<img[^>]*src\s*=\s*["']https?:\/\//.test(idxHtml));
    ck("no_fake_sample_file", !/GenshinImpact|HonkaiImpact|PUBG|\.ipa.{0,20}564/.test(idxHtml + appJs));
    ck("no_coi", !/Cross-Origin-Opener|Cross-Origin-Embedder|coi-serviceworker/i.test(idxHtml + appJs));
    ck("views_present", ["view-home", "view-tools", "view-kb", "view-settings", "view-about"]
      .every((id) => have.has(id)));
    ck("home_default_visible", /<section id="view-home">/.test(idxHtml)
      && /<section id="view-tools" hidden>/.test(idxHtml)
      && /<section id="view-kb" hidden>/.test(idxHtml));
    ck("bottomnav_present", ["btn-nav-home", "btn-nav-tools", "btn-nav-kb", "btn-nav-settings", "btn-nav-about"]
      .every((id) => have.has(id)));
    ck("cards_present", ["btn-card-unity", "btn-card-extract", "btn-card-translate", "btn-card-mod", "btn-card-batch", "btn-card-kb"]
      .every((id) => have.has(id)));
    ck("no_theme", !have.has("btn-theme") && !have.has("setting-theme")
      && !/pb3_theme|data-theme|btn-theme/.test(idxHtml + appJs));
    ck("save_history_present", have.has("setting-save-history"));
    ck("bear_pastel", /--bg: #dcebf8/.test(idxHtml) && /--pink-text: #c24e77/.test(idxHtml)
      && /<img class="logo-bear" src="icons\/icon-192\.png"/.test(idxHtml)
      && (idxHtml.match(/<img src="icons\/icon-192\.png"/g) || []).length >= 5);
    ck("credit_locked", !/translate-credit/.test(idxHtml + appJs)
      && /luôn được gắn vào bản dịch/.test(idxHtml)
      && /"translate_json", \[text, to, true, from/.test(appJs));
    ck("recent_present", have.has("recent-list") && have.has("dropzone") && have.has("unity-info") && have.has("download-bar"));
    ck("download_bar_close", have.has("btn-download-close") && /btn-download-close/.test(appJs));
    ck("bear_icon_img", !idxHtml.includes('id="bear-mascot"') && !idxHtml.includes("<use href=")
      && idxHtml.includes('<link rel="icon" type="image/png" href="icons/icon-192.png">'));
    // A5. Mod: chỉ "Thay Text" là thật; các op khác disabled + ghi rõ.
    ck("mod_ops_honest", (idxHtml.match(/chưa hỗ trợ trên web \(dùng bản iSH\)/g) || []).length >= 5
      && /id="btn-mod-op-text"/.test(idxHtml));
    // A6. node --check.
    const { execFileSync } = await import("node:child_process");
    execFileSync("node", ["--check", SITE + "/app.js"], { stdio: "pipe" });
    execFileSync("node", ["--check", SITE + "/kb-store.js"], { stdio: "pipe" });
    ck("node_check", true);
    rec("static_checks", true, checks.join(" · ") + ` | app.js dùng ${used.size} id, thiếu 0`, performance.now() - t0);
  } catch (e) { rec("static_checks", false, String(e?.message ?? e), performance.now() - t0); }
}

// ============ Part B: fake-DOM — app.js nạp không lỗi + logic mới ============
// DOM giả KHÔNG có documentElement / querySelector (như harness clone-fix):
// code redesign phải guard, không được ném ở top-level.
function loadApp() {
  const els = new Map();
  const makeEl = (id) => {
    const el = {
      id, hidden: true, textContent: "", innerHTML: "", value: "", checked: false,
      disabled: false, files: [], style: {},
      classList: { _s: new Set(),
        add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); },
        toggle(c, f) { if (f === undefined) { if (this._s.has(c)) this._s.delete(c); else this._s.add(c); } else if (f) this._s.add(c); else this._s.delete(c); },
        contains(c) { return this._s.has(c); } },
      _handlers: {},
      addEventListener(ev, fn) { (this._handlers[ev] ||= []).push(fn); },
      async click() { let r; for (const h of (this._handlers.click || [])) r = await h({ target: this }); return r; },
      scrollIntoView() {}, focus() {},
      closest() { return null; }, getAttribute() { return null; },
    };
    return el;
  };
  const getEl = (id) => { if (!els.has(id)) els.set(id, makeEl(id)); return els.get(id); };
  const ls = new Map();
  const posted = [];
  class FakeWorker {
    constructor(u, o) { this.url = u; this.opts = o; this.onmessage = null; this.onerror = null; }
    postMessage(m, t) { posted.push({ m, t }); }
  }
  const sandbox = {
    console, Blob, File, TextEncoder,
    URL: { createObjectURL: () => "blob:fake", revokeObjectURL: () => {} },
    performance: { now: () => Date.now() },
    setTimeout: (fn) => setTimeout(fn, 0), clearTimeout: () => {},
    document: {
      getElementById: (id) => getEl(id),
      createElement: (tag) => makeEl(tag),
      body: { appendChild() {}, removeChild() {} },
    },
    navigator: { canShare: () => false, share: () => Promise.resolve(), storage: { persist: () => Promise.resolve(false), persisted: () => Promise.resolve(false) } },
    localStorage: {
      getItem: (k) => (ls.has(k) ? ls.get(k) : null),
      setItem: (k, v) => ls.set(k, String(v)),
      removeItem: (k) => ls.delete(k),
    },
    Worker: FakeWorker,
    confirm: () => true,
    addEventListener: () => {},
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(appJs, sandbox, { filename: "app.js" });
  return { ctx: sandbox, getEl, ls, posted };
}
{
  const t0 = performance.now();
  try {
    const t = loadApp();
    // B1. Bottom nav chuyển view.
    // (Stub DOM giả đặt hidden=true mặc định cho mọi phần tử — không phản
    // ánh HTML thật; đặt lại trạng thái ban đầu như HTML: view-home hiện.
    // Trạng thái mặc định này được kiểm ở static_checks: home_default_visible.)
    t.getEl("view-home").hidden = false;
    await t.getEl("btn-nav-kb").click();
    assert(t.getEl("view-kb").hidden === false && t.getEl("view-home").hidden === true,
      "click nav KB: view-kb hiện, view-home ẩn");
    assert(t.getEl("btn-nav-kb").classList.contains("active"), "nav KB phải active");
    await t.getEl("btn-nav-settings").click();
    assert(t.getEl("view-settings").hidden === false && t.getEl("view-kb").hidden === true,
      "click nav Cài đặt: view-settings hiện");
    await t.getEl("btn-nav-home").click();
    assert(t.getEl("view-home").hidden === false, "click nav Trang chủ: view-home hiện lại");
    // B2. Không còn theme; thanh tiến độ bật/tắt spinner + class loading.
    // Map.get tra undefined khi thieu key (khong phai null) nen kiem has().
    assert(!t.ls.has("pb3_theme"), "không còn key localStorage pb3_theme");
    t.ctx.progStart("Kiểm tra");
    assert(t.getEl("progress-spinner").hidden === false, "progStart phải hiện spinner");
    assert(t.getEl("progress-bar").classList.contains("loading"), "progStart phải gắn class loading");
    t.ctx.progDone("Kiểm tra");
    assert(t.getEl("progress-spinner").hidden === true, "progDone phải ẩn spinner");
    assert(!t.getEl("progress-bar").classList.contains("loading"), "progDone phải gỡ class loading");
    // B3. File gần đây: addRecent qua sandbox -> render + localStorage metadata.
    t.ctx.addRecent({ name: "game1.zip", size: 123456, lastModified: 1000, data: new ArrayBuffer(0) });
    const listHtml = t.getEl("recent-list").innerHTML;
    assert(listHtml.includes("game1.zip") && listHtml.includes("ZIP"),
      `recent-list phải có game1.zip + badge ZIP: ${listHtml.slice(0, 160)}`);
    const stored = JSON.parse(t.ls.get("pb3_recent_files") || "[]");
    assert(stored.length === 1 && stored[0].name === "game1.zip" && !("data" in stored[0]),
      "metadata localStorage chỉ có tên/size/kind/time, KHÔNG lưu nội dung file");
    // B4. Tắt "Lưu lịch sử gần đây": metadata đã lưu bị xóa, list chỉ còn trong phiên.
    t.ctx.addRecent({ name: "other.ipa", size: 999, lastModified: 0, data: new ArrayBuffer(0) });
    t.getEl("setting-save-history").checked = false;
    for (const h of (t.getEl("setting-save-history")._handlers.change || [])) await h({ target: t.getEl("setting-save-history") });
    assert(t.ls.get("pb3_recent_files") === null || t.ls.get("pb3_recent_files") === undefined,
      "tắt lưu lịch sử phải xóa metadata đã lưu");
    rec("fake_dom_new_ui", true,
      "nav chuyển view đúng; không còn theme, spinner tiến độ bật/tắt đúng; recent render + metadata localStorage (không nội dung file); tắt lưu lịch sử xóa metadata",
      performance.now() - t0);
  } catch (e) { rec("fake_dom_new_ui", false, String(e?.message ?? e), performance.now() - t0); }
}

// ============ Part C: trình duyệt thật 390px ============
const MIME = { ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript",
  ".json": "application/json", ".webmanifest": "application/manifest+json",
  ".wasm": "application/wasm", ".css": "text/css", ".png": "image/png",
  ".md": "text/markdown", ".ttf": "font/ttf", ".b64": "text/plain" };
const server = http.createServer((req, res) => {
  const urlPath = decodeURIComponent(req.url.split("?")[0]);
  const fp = path.join(SITE, urlPath === "/" ? "index.html" : urlPath);
  if (!fp.startsWith(SITE)) { res.writeHead(403); res.end(); return; }
  fs.readFile(fp, (err, data) => {
    if (err) { res.writeHead(404); res.end("not found"); return; }
    res.writeHead(200, { "Content-Type": MIME[path.extname(fp)] || "application/octet-stream" });
    res.end(data);
  });
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const PORT = server.address().port;
let browser = null;
const pageErrors = [];
try {
  browser = await chromium.launch({ executablePath: EXE, args: ["--no-sandbox"] });
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, acceptDownloads: true });
  const page = await ctx.newPage();
  await page.addInitScript(() => {
    try { Object.defineProperty(navigator, "canShare", { value: () => false }); } catch (e) {}
  });
  page.on("pageerror", (e) => pageErrors.push(String(e && e.message ? e.message : e)));
  const waitReady = async () => {
    // Mục 6 (lazy boot): boot chỉ chạy khi chọn file — chọn 1 file nhỏ
    // trước rồi mới chờ "Sẵn sàng".
    await page.setInputFiles("#file-input", GAME1);
    await page.waitForFunction(
      () => { const el = document.getElementById("status"); return el && el.textContent.includes("Sẵn sàng"); },
      null, { timeout: 240000 });
  };
  const shot = async (name) => page.screenshot({ path: EXP + "/" + name, fullPage: true });
  const overflow = async () => page.evaluate(() => ({
    sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));

  { // C1. Boot thật + home 390px không tràn ngang
    const t0 = performance.now();
    await page.goto("http://127.0.0.1:" + PORT + "/index.html");
    await waitReady();
    const ov = await overflow();
    assert(ov.sw <= ov.cw + 1, `home tràn ngang: scrollWidth=${ov.sw} clientWidth=${ov.cw}`);
    const hero = await page.evaluate(() => ({
      logoImg: !!document.querySelector('img.logo-bear')
        && document.querySelector(".hero-bear img")?.getAttribute("src") === "icons/icon-192.png",
      heroText: (document.querySelector(".hero h2") || {}).textContent || "",
      cards: document.querySelectorAll(".feature-card").length,
      nav: document.querySelectorAll(".bottomnav button").length,
    }));
    assert(hero.logoImg && hero.cards === 6 && hero.nav === 5,
      `hero phải có logo gấu <img> + 6 thẻ + 5 nút nav: ${JSON.stringify(hero)}`);
    assert(hero.heroText.includes("Unity"), `hero phải nhắc Unity: ${hero.heroText}`);
    await shot("redesign_390_home.png");
    rec("browser_boot_home_390", true,
      `boot Sẵn sàng; home scrollWidth=${ov.sw} (không tràn); logo gấu <img> + 6 thẻ + 5 nút nav; hero: ${hero.heroText.slice(0, 50)}…`,
      performance.now() - t0);
  }

  { // C2. Dropzone bấm mở khung chọn file + chọn game1.zip -> recent có nút Tải lại
    const t0 = performance.now();
    const [chooser] = await Promise.all([
      page.waitForEvent("filechooser", { timeout: 30000 }),
      page.click("#dropzone"),
    ]);
    await chooser.setFiles(GAME1);
    await page.waitForFunction(
      () => { const el = document.getElementById("file-info"); return el && !el.hidden && el.textContent.includes("game1.zip"); },
      null, { timeout: 30000 });
    const recent = await page.evaluate(() => {
      const el = document.getElementById("recent-list");
      return { html: el.innerHTML, hasDl: !!el.querySelector('[data-act="dl"]') };
    });
    assert(recent.html.includes("game1.zip") && recent.hasDl,
      `recent phải có game1.zip + nút Tải lại (còn trong phiên): ${recent.html.slice(0, 200)}`);
    rec("browser_dropzone_recent", true,
      "bấm dropzone mở khung chọn file; chọn game1.zip -> file-info hiện; recent có game1.zip + nút Tải lại",
      performance.now() - t0);
  }

  { // C3. Danh sách: bảng 390px không vỡ chữ + không tràn ngang
    const t0 = performance.now();
    await page.click("#btn-card-unity"); // mở view Chức năng (tab Unity)
    await page.click("#btn-list");
    await page.click("#btn-list-run");
    await page.waitForFunction(
      () => { const el = document.getElementById("list-result"); return el && el.textContent.includes("entry"); },
      null, { timeout: 60000 });
    const m = await page.evaluate(() => {
      const q = (sel) => [...document.querySelectorAll(sel)];
      // Số dòng THẬT của chữ trong ô: Range.getClientRects() đếm hộp dòng.
      const lines = (el) => { const r = document.createRange(); r.selectNodeContents(el); return r.getClientRects().length; };
      const th = q("#list-result th").map((el) => ({ text: el.textContent, lines: lines(el) }));
      const loai = q("#list-result td:nth-child(2)").map((el) => ({ text: el.textContent, lines: lines(el) }));
      const size = q("#list-result td:nth-child(3)").map((el) => ({ text: el.textContent, lines: lines(el) }));
      const tbl = document.querySelector("#list-result table");
      const cs = tbl ? getComputedStyle(tbl) : {};
      return { th, loai, size, overflowX: cs.overflowX, display: cs.display,
        sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth };
    });
    const shortMax = Math.max(...m.th.map((x) => x.lines), ...m.loai.map((x) => x.lines), ...m.size.map((x) => x.lines));
    assert(shortMax === 1, `cột ngắn phải 1 dòng, thực tế ${shortMax}: th=${JSON.stringify(m.th)}`);
    assert(m.overflowX === "auto" && m.display === "block", `bảng phải block+overflow-x:auto: ${m.display}/${m.overflowX}`);
    assert(m.sw <= m.cw + 1, `tools tràn ngang: sw=${m.sw} cw=${m.cw}`);
    await shot("redesign_390_tools_list.png");
    rec("browser_list_table_390", true,
      `390px: tiêu đề/Loại/Kích thước đúng 1 dòng; bảng block+overflow-x:auto; không tràn ngang (sw=${m.sw})`,
      performance.now() - t0);
  }

  { // C4. Unity inspect bundle trong ZIP -> thẻ Thông tin Unity hiện version thật
    const t0 = performance.now();
    await page.setInputFiles("#file-input", UCONTAINER);
    await page.waitForFunction(
      () => { const el = document.getElementById("file-info"); return el && el.textContent.includes("unity_container.zip"); },
      null, { timeout: 30000 });
    await page.click("#btn-unity"); // đang ở view Chức năng (tab Danh sách) sau C3
    await page.click("#btn-unity-run");
    await page.waitForFunction(
      () => { const el = document.getElementById("unity-result"); return el && el.textContent.includes("bundle"); },
      null, { timeout: 60000 });
    await page.click("#btn-unity-inspect-entry");
    await page.waitForFunction(
      () => { const el = document.getElementById("unity-result"); return el && /Unity/.test(el.textContent); },
      null, { timeout: 60000 });
    const info = await page.evaluate(() => (document.getElementById("unity-info") || {}).textContent || "");
    assert(/Unity Version:/.test(info) && !/Unity Version: \?/.test(info) && !/Build Target/.test(info),
      `thẻ Thông tin Unity phải có Unity Version thật, không bịa Build Target: ${info.slice(0, 200)}`);
    await shot("redesign_390_tools_unity.png");
    // Quay về Trang chủ chụp thẻ Thông tin Unity đã có dữ liệu.
    await page.click("#btn-nav-home");
    await shot("redesign_390_home_unityinfo.png");
    rec("browser_unity_info_card", true,
      `inspect bundle trong ZIP xong; thẻ Thông tin Unity: ${info.slice(0, 140)}…`,
      performance.now() - t0);
  }

  { // C5. Trích xuất game1.zip -> file extract.zip + thanh Lưu file hiện
    const t0 = performance.now();
    await page.setInputFiles("#file-input", GAME1);
    await page.waitForFunction(
      () => { const el = document.getElementById("file-info"); return el && el.textContent.includes("game1.zip"); },
      null, { timeout: 30000 });
    await page.click("#btn-card-extract"); // đang ở Trang chủ sau cuối C4
    const dlPromise = page.waitForEvent("download", { timeout: 120000 });
    await page.click("#btn-extract-run");
    const dl = await dlPromise;
    const barVisible = await page.evaluate(() => {
      const bar = document.getElementById("download-bar");
      const hint = document.getElementById("download-hint");
      return bar && !bar.hidden && hint && !hint.hidden;
    });
    const res = await page.evaluate(() => (document.getElementById("extract-result") || {}).textContent || "");
    assert(dl.suggestedFilename() === "extract.zip", `tên file tải phải extract.zip, thực tế ${dl.suggestedFilename()}`);
    assert(barVisible, "thanh Lưu file phải hiện sau khi trích xuất xong");
    assert(res.includes("extract.zip"), `kết quả extract phải nhắc extract.zip: ${res.slice(0, 120)}`);
    await page.click("#btn-download-close");
    const barHidden = await page.evaluate(() => document.getElementById("download-bar").hidden);
    assert(barHidden === true, "bấm ✕ phải ẩn thanh Lưu file");
    await shot("redesign_390_tools_extract.png");
    rec("browser_extract_download_bar", true,
      `extract.zip tải về (${dl.suggestedFilename()}); thanh Lưu file hiện; kết quả nhắc đúng tên file`,
      performance.now() - t0);
  }

  { // C6. Các màn còn lại: Dịch / Mod / Batch / KB / Cài đặt / BearTea — chụp + không tràn
    const t0 = performance.now();
    const views = [
      ["#btn-card-translate", "redesign_390_tools_translate.png"],
      ["#btn-card-mod", "redesign_390_tools_mod.png"],
      ["#btn-card-batch", "redesign_390_tools_batch.png"],
      ["#btn-nav-kb", "redesign_390_kb.png"],
      ["#btn-nav-settings", "redesign_390_settings.png"],
      ["#btn-nav-about", "redesign_390_about.png"],
    ];
    const notes = [];
    for (const [sel, file] of views) {
      // Thẻ chức năng chỉ có ở Trang chủ; nút bottom nav thì bấm được từ mọi view.
      if (sel.startsWith("#btn-card-")) await page.click("#btn-nav-home");
      await page.click(sel);
      await sleep(300);
      const ov = await overflow();
      assert(ov.sw <= ov.cw + 1, `${sel} tràn ngang: sw=${ov.sw} cw=${ov.cw}`);
      await shot(file);
      notes.push(sel + " sw=" + ov.sw);
    }
    // KB: trạng thái phải hiện số game (đã boot, KB trống = 0 game).
    const kbStatus = await page.evaluate(() => (document.getElementById("kb-status") || {}).textContent || "");
    assert(/KB: \d+ game/.test(kbStatus), `KB status phải có số game: ${kbStatus}`);
    // Mod: chỉ Thay Text là nút thật, 5 op còn lại disabled.
    await page.click("#btn-nav-home");
    await page.click("#btn-card-mod");
    const modOps = await page.evaluate(() => ({
      textEnabled: !document.getElementById("btn-mod-op-text").disabled,
      disabledCount: [...document.querySelectorAll(".op-card[disabled]")].length,
    }));
    assert(modOps.textEnabled && modOps.disabledCount === 5,
      `Mod phải có 1 op thật + 5 op disabled: ${JSON.stringify(modOps)}`);
    rec("browser_other_views_390", true,
      notes.join(" · ") + ` | KB status: ${kbStatus.slice(0, 60)}… | Mod: Thay Text thật + 5 op disabled`,
      performance.now() - t0);
  }

  { // C7. Giao diện pastel thật: không nút theme, logo là ảnh gấu, nền xanh pastel
    const t0 = performance.now();
    await page.click("#btn-nav-settings");
    const pastel = await page.evaluate(() => ({
      noThemeBtn: !document.getElementById("btn-theme"),
      noThemeSetting: !document.getElementById("setting-theme"),
      noDataTheme: !document.documentElement.hasAttribute("data-theme"),
      logoImg: !!document.querySelector("img.logo-bear"),
      logoSrc: document.querySelector("img.logo-bear")?.getAttribute("src"),
      bg: getComputedStyle(document.body).backgroundColor,
      creditNote: document.body.textContent.includes("luôn được gắn vào bản dịch"),
    }));
    assert(pastel.noThemeBtn && pastel.noThemeSetting && pastel.noDataTheme
      && pastel.logoImg && pastel.logoSrc === "icons/icon-192.png"
      && pastel.bg === "rgb(220, 235, 248)" && pastel.creditNote,
      `giao diện pastel chưa đúng: ${JSON.stringify(pastel)}`);
    await shot("redesign_390_settings_pastel.png");
    rec("browser_pastel_real", true,
      `không nút theme, logo <img> ${pastel.logoSrc}, nền body ${pastel.bg}, có ghi chú credit luôn gắn`,
      performance.now() - t0);
  }

  { // C8. Không lỗi trang trong suốt phiên trình duyệt
    const t0 = performance.now();
    rec("browser_no_pageerrors", pageErrors.length === 0,
      "pageerror: " + (pageErrors.length ? JSON.stringify(pageErrors) : "không có"),
      performance.now() - t0);
  }
  await browser.close();
  browser = null;
} catch (e) {
  if (browser) { try { await browser.close(); } catch (_) {} }
  rec("browser_end_to_end", false, "LỖI trình duyệt thật: " + String(e && e.message ? e.message : e), undefined);
}
server.close();

fs.writeFileSync(EXP + "/verify_redesign_results.json", JSON.stringify({ failures, results }, null, 2));
const md = ["# Redesign BearTea — kết quả harness", "",
  `Chạy: ${new Date().toISOString()} · FAIL: ${failures} / ${Object.keys(results).length}`, "",
  ...Object.entries(results).map(([k, v]) => `## ${v.ok ? "PASS" : "FAIL"} — ${k} (${v.seconds}s)\n${v.detail}\n`)];
fs.writeFileSync(EXP + "/verify_redesign_results.md", md.join("\n"));
console.log(`\n==== TỔNG: ${Object.keys(results).length - failures}/${Object.keys(results).length} PASS, ${failures} FAIL ====`);
process.exit(failures ? 1 : 0);
