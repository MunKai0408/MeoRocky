// verify_hotfix_safari2.mjs — Hotfix Safari đợt 2 (Joseph 11:23–11:37, 6 ảnh + tin nhắn):
//  A. Download kẹt: bỏ revoke 5 giây (URL sống 10 phút + hủy URL cũ khi tải mới +
//     pagehide); navigator.share làm ĐƯỜNG LƯU CHÍNH trên iOS (nút "Lưu / Chia sẻ",
//     bảng Chia sẻ → "Lưu vào Tệp"), blob a.click() chỉ là dự phòng. Cơ chế đã rõ
//     (Joseph 11:36): daemon tải của Safari đọc blob URL bất đồng bộ, revoke 5 giây
//     giết link giữa chừng khi đang ghi file 95MB → file dở dang kẹt mãi trong
//     Files > Tải về. Giả thuyết "bấm trong 5 giây thì được" đã bị bác bỏ 11:34.
//  B. Inspect Unity trên nguyên IPA/ZIP → E_UNSUPPORTED_FORMAT: liệt kê bundle/.assets
//     trong ZIP cho user chọn inspect trực tiếp (unity_inspect_entry).
//  C. Giải nén sai file (.zip thay vì .tar.xz) → E_CORRUPT: kiểm tra đuôi phía JS +
//     mọi banner lỗi có nhãn thao tác.
//  D. Giải nén trả tên file GỐC từ trong tar thay vì restored.bin.
//  E. Bảng vỡ chữ (Joseph 11:37): word-break: break-all trên mọi ô làm "Loại" thành
//     "L o ạ i" — chỉ ngắt ở cột đường dẫn, cột ngắn nowrap, bảng cuộn ngang;
//     kiểm chứng render thật ở 390px bằng Chromium headless.
// Chạy: node verify_hotfix_safari2.mjs (từ thư mục exp/ của repo)
// Kết quả: verify_hotfix_safari2_results.json/.md + hotfix_table_390.png
import fs from "node:fs";
import vm from "node:vm";
import { loadPyodide } from "../pyodide/pyodide.mjs";
import { chromium } from "playwright-core";

const ROOT = decodeURIComponent(new URL("..", import.meta.url).pathname).replace(/\/$/, "");
const SITE = ROOT;
const PB3 = SITE + "/pb3";
const EXP = ROOT + "/exp";
const sec = (ms) => Math.round(ms / 100) / 10;
const results = {};
let failures = 0;
function rec(name, ok, detail, ms) {
  results[name] = { ok, detail, seconds: sec(ms || 0) };
  if (!ok) failures++;
  console.log(`\n### ${name}: ${ok ? "PASS" : "FAIL"} (${sec(ms || 0)}s)\n${detail}`);
}
function assert(cond, msg) { if (!cond) throw new Error(msg); }

// ============ Part A: fake-DOM — tải app.js thật trong Node vm ============
function loadApp({ canShare, shareImpl }) {
  const src = fs.readFileSync(SITE + "/app.js", "utf8");
  const els = new Map();
  const makeEl = (id) => {
    const el = {
      id, hidden: true, textContent: "", innerHTML: "", value: "", checked: false,
      disabled: false, files: [], _scrolled: false,
      style: {},
      classList: { _s: new Set(),
        add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); },
        toggle(c) { if (this._s.has(c)) this._s.delete(c); else this._s.add(c); },
        contains(c) { return this._s.has(c); } },
      _handlers: {},
      addEventListener(ev, fn) { (this._handlers[ev] ||= []).push(fn); },
      async click() {
        let r;
        for (const h of (this._handlers.click || [])) r = await h({ target: this });
        return r;
      },
      scrollIntoView() { el._scrolled = true; },
    };
    return el;
  };
  const getEl = (id) => { if (!els.has(id)) els.set(id, makeEl(id)); return els.get(id); };
  const anchors = [];
  const urls = { created: [], revoked: [] };
  const timers = [];
  let now = 0;
  const nav = {
    shareCalls: [],
    share(opts) { nav.shareCalls.push(opts); return shareImpl ? shareImpl(opts) : Promise.resolve(); },
  };
  if (canShare !== undefined) nav.canShare = () => canShare;
  const ls = new Map();
  const posted = [];
  class FakeWorker {
    constructor(u, o) { this.url = u; this.opts = o; this.onmessage = null; this.onerror = null; }
    postMessage(m, t) { posted.push({ m, t }); }
  }
  const sandbox = {
    console, Blob, File,
    URL: {
      createObjectURL(b) { const u = "blob:fake/" + urls.created.length; urls.created.push(u); return u; },
      revokeObjectURL(u) { urls.revoked.push(u); },
    },
    performance: { now: () => now },
    setTimeout: (fn, ms) => { const t = { fn, due: now + (ms || 0), fired: false }; timers.push(t); return t; },
    clearTimeout: () => {},
    document: {
      getElementById: (id) => getEl(id),
      createElement: (tag) => {
        if (tag === "a") {
          const a = { href: "", download: "", _clicked: 0,
            click() { a._clicked++; anchors.push(a); }, remove() {} };
          return a;
        }
        return makeEl(tag);
      },
      body: { appendChild() {}, removeChild() {} },
    },
    navigator: nav,
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
  vm.runInContext(src, sandbox, { filename: "app.js" });
  return {
    ctx: sandbox, getEl, anchors, urls, nav, posted,
    advance(ms) { now += ms; for (const t of timers) if (!t.fired && t.due <= now) { t.fired = true; t.fn(); } },
  };
}

// A1 — không share được (desktop): blob tự bấm ngay; URL sống đủ 10 phút,
// không bị hủy ở 5 giây / 60 giây / 599 giây; hủy đúng ở 600 giây.
{
  const t0 = performance.now();
  const t = loadApp({ canShare: false });
  try {
    t.ctx.download("out.tar.xz", new Uint8Array([1, 2, 3, 4]));
    assert(t.anchors.length === 1, `blob phải bấm ngay 1 lần, thực tế ${t.anchors.length}`);
    assert(t.getEl("btn-share-download").hidden === true, "nút Share phải ẩn khi không share được");
    assert(t.getEl("btn-blob-download").hidden === true, "nút blob phải ẩn khi blob đã tự bấm");
    assert(t.getEl("download-hint").textContent.includes("Đang tải về"), "hint phải nói đang tải về");
    t.advance(5000);
    assert(t.urls.revoked.length === 0, `revoke ở 5 giây: ${t.urls.revoked.length} (phải 0 — lỗi gốc)`);
    t.advance(55000);
    assert(t.urls.revoked.length === 0, `revoke ở 60 giây: ${t.urls.revoked.length}`);
    t.advance(539000);
    assert(t.urls.revoked.length === 0, `revoke ở 599 giây: ${t.urls.revoked.length}`);
    t.advance(1000);
    assert(t.urls.revoked.length === 1, `revoke ở 600 giây: ${t.urls.revoked.length} (phải 1)`);
    rec("download_blob_no_share", true,
      `blob bấm ngay; revoke 5s/60s/599s = 0, 600s = 1; hint: ${t.getEl("download-hint").textContent.slice(0, 60)}…`,
      performance.now() - t0);
  } catch (e) { rec("download_blob_no_share", false, String(e?.message ?? e), performance.now() - t0); }
}

// A2 — share được (iOS): KHÔNG tự bấm blob; hiện nút Lưu/Chia sẻ + blob dự phòng;
// bấm blob dự phòng thì bấm anchor; bấm Share thì gọi navigator.share đúng tên file;
// AbortError (user hủy bảng chia sẻ) không báo lỗi.
{
  const t0 = performance.now();
  const t = loadApp({ canShare: true });
  try {
    t.ctx.download("extract.zip", new Uint8Array([9, 8, 7]));
    assert(t.anchors.length === 0, `iOS: blob KHÔNG được tự bấm, thực tế ${t.anchors.length}`);
    assert(t.getEl("btn-share-download").hidden === false, "nút Lưu/Chia sẻ phải hiện");
    assert(t.getEl("btn-blob-download").hidden === false, "nút Tải về (blob) phải hiện làm dự phòng");
    const hint = t.getEl("download-hint").textContent;
    assert(hint.includes("Lưu / Chia sẻ") && hint.includes("chắc chắn nhất"),
      `hint phải nói Share là cách chắc chắn nhất: ${hint}`);
    assert(t.getEl("download-hint")._scrolled === true, "hint phải cuộn vào tầm nhìn");
    await t.getEl("btn-blob-download").click();
    assert(t.anchors.length === 1 && t.anchors[0].download === "extract.zip",
      `bấm blob dự phòng phải bấm anchor đúng tên, thực tế ${t.anchors.length}`);
    await t.getEl("btn-share-download").click();
    assert(t.nav.shareCalls.length === 1, `share phải gọi 1 lần, thực tế ${t.nav.shareCalls.length}`);
    assert(t.nav.shareCalls[0].files[0].name === "extract.zip",
      `share phải đúng tên file, thực tế ${t.nav.shareCalls[0].files[0].name}`);
    assert(t.getEl("error").hidden === true, "share thành công không được báo lỗi");
    rec("download_share_primary", true,
      `blob không tự bấm; Share+blob hiện; share đúng tên extract.zip; hint: ${hint.slice(0, 60)}…`,
      performance.now() - t0);
  } catch (e) { rec("download_share_primary", false, String(e?.message ?? e), performance.now() - t0); }
}

// A3 — Share bị hủy (AbortError) thì im lặng; Share lỗi thật thì báo "Chia sẻ lỗi:".
{
  const t0 = performance.now();
  try {
    const abortErr = new Error("cancelled"); abortErr.name = "AbortError";
    const t1 = loadApp({ canShare: true, shareImpl: () => Promise.reject(abortErr) });
    t1.ctx.download("a.zip", new Uint8Array([1]));
    await t1.getEl("btn-share-download").click();
    assert(t1.getEl("error").hidden === true, "AbortError phải im lặng, không báo lỗi");
    const t2 = loadApp({ canShare: true, shareImpl: () => Promise.reject(new Error("share failed")) });
    t2.ctx.download("b.zip", new Uint8Array([1]));
    await t2.getEl("btn-share-download").click();
    assert(t2.getEl("error").hidden === false && t2.getEl("error").textContent.startsWith("Chia sẻ lỗi:"),
      `lỗi share thật phải báo có nhãn, thực tế: ${t2.getEl("error").textContent}`);
    rec("download_share_errors", true, "AbortError im lặng; lỗi thật báo 'Chia sẻ lỗi: …'",
      performance.now() - t0);
  } catch (e) { rec("download_share_errors", false, String(e?.message ?? e), performance.now() - t0); }
}

// A4 — lần tải mới hủy URL cũ ngay; timer cũ KHÔNG được hủy URL mới
// (lần 1 ở t=0, lần 2 ở t=1s: ở t=600s chỉ hủy URL lần 1 — đã hủy rồi nên 0;
// ở t=601s mới hủy URL lần 2).
{
  const t0 = performance.now();
  const t = loadApp({ canShare: false });
  try {
    t.ctx.download("first.zip", new Uint8Array([1]));
    assert(t.urls.revoked.length === 0, "chưa hủy gì sau lần 1");
    t.advance(1000);
    t.ctx.download("second.zip", new Uint8Array([2]));
    assert(t.urls.revoked.length === 1, `lần 2 phải hủy URL lần 1 ngay, thực tế ${t.urls.revoked.length}`);
    assert(t.anchors.length === 2, "lần 2 bấm blob riêng");
    t.advance(599000); // t = 600s: timer lần 1 nổ — URL lần 2 phải còn sống
    assert(t.urls.revoked.length === 1, `timer cũ không được hủy URL mới, thực tế ${t.urls.revoked.length}`);
    t.advance(1000); // t = 601s: timer lần 2 nổ
    assert(t.urls.revoked.length === 2, `timer lần 2 phải hủy URL lần 2, thực tế ${t.urls.revoked.length}`);
    rec("download_revoke_old_on_new", true,
      "lần mới hủy URL cũ ngay; timer cũ không chạm URL mới; mỗi URL sống đúng 10 phút",
      performance.now() - t0);
  } catch (e) { rec("download_revoke_old_on_new", false, String(e?.message ?? e), performance.now() - t0); }
}

// A5 — nút blob sau khi URL đã hủy (quá 10 phút) thì tạo lại URL rồi bấm.
{
  const t0 = performance.now();
  const t = loadApp({ canShare: true });
  try {
    t.ctx.download("late.zip", new Uint8Array([5]));
    t.advance(600000);
    assert(t.urls.revoked.length === 1, "URL phải hủy sau 10 phút");
    await t.getEl("btn-blob-download").click();
    assert(t.urls.created.length === 2, `blob sau 10 phút phải tạo URL mới, thực tế ${t.urls.created.length}`);
    assert(t.anchors.length === 1 && t.anchors[0].download === "late.zip", "blob phải bấm đúng tên");
    rec("download_blob_after_revoke", true, "quá 10 phút: tạo lại URL từ blob đang giữ rồi bấm",
      performance.now() - t0);
  } catch (e) { rec("download_blob_after_revoke", false, String(e?.message ?? e), performance.now() - t0); }
}

// ============ Part B: kiểm tra tĩnh ============
{
  const t0 = performance.now();
  try {
    const a = fs.readFileSync(SITE + "/app.js", "utf8");
    const w = fs.readFileSync(SITE + "/worker.js", "utf8");
    const b = fs.readFileSync(SITE + "/pb3/bridge.py", "utf8");
    const idx = fs.readFileSync(SITE + "/index.html", "utf8");
    const checks = [];
    const ck = (name, ok) => { checks.push(name + "=" + ok); assert(ok, "thiếu/sai: " + name); };
    ck("no_5s_revoke", !/setTimeout\([^)]*,\s*5000\s*\)/.test(a));
    ck("revoke_10min", /600000/.test(a));
    ck("pagehide_revoke", /addEventListener\("pagehide", revokeLastDownloadUrl\)/.test(a));
    ck("share_primary", /canShareFile\(blob, name\)/.test(a) && /navigator\.share\(\{\s*files:\s*\[file\]\s*\}\)/.test(a));
    ck("no_unlabeled_error", !/^[^/]*showError\("Lỗi: "\s*\+\s*parsed\.error\)/m.test(a) && !/showError\("Lỗi: " \+ parsed\.error\)/.test(a));
    // runOp ném lỗi CÓ NHÃN khi bridge trả ok:false; message được dựng từ
    // parsed.error (+ chi tiết parsed.errors, decision, số skipped — fix lỗi
    // "Lỗi không rõ" 2026-11-16) rồi mới throw, nên KHÔNG kiểm hình thức
    // "throw new Error(parsed.error" ngay sau "{" nữa mà kiểm hành vi:
    // nhánh ok===false có throw Error + message lấy từ parsed.error/code +
    // có đọc parsed.errors + catch hiện "label lỗi: message".
    ck("runop_throws_labeled", /if \(parsed && parsed\.ok === false\) \{[\s\S]{0,1500}?throw new Error\(/.test(a) && /parsed\.error \|\| parsed\.code/.test(a) && /parsed\.errors/.test(a) && /showError\(label \+ " lỗi: " \+ e\.message\)/.test(a));
    ck("decompress_guard", /File giải nén phải là \.tar\.xz/.test(a));
    ck("compress_guard", /File \.tar\.xz đã là file nén/.test(a));
    ck("unity_picker", /unity-bundle-select/.test(a) && /unity_inspect_entry/.test(a) && /inspectUnityEntry/.test(a));
    ck("bridge_inspect_entry", /def unity_inspect_entry\(/.test(b));
    ck("bridge_container", /is_container/.test(b) && /"bundles"/.test(b));
    ck("bridge_filename", /"filename": fname/.test(b) || /filename.*original_name/.test(b));
    ck("worker_filename", /parsed\.filename/.test(w));
    ck("index_share_btn", /id="btn-share-download"[^>]*>Lưu \/ Chia sẻ</.test(idx));
    ck("index_blob_btn", /id="btn-blob-download"[^>]*>Tải về \(blob\)</.test(idx));
    ck("index_compress_wording", /khôi phục file\s*gốc \(giữ nguyên tên gốc\)/.test(idx));
    ck("css_no_global_breakall", !/th, td \{[^}]*word-break: break-all/s.test(idx));
    ck("css_nowrap", /th, td \{[^}]*white-space: nowrap/s.test(idx));
    ck("css_firstcol_break", /td:first-child \{[^}]*word-break: break-all/s.test(idx));
    ck("css_table_scroll", /table \{[^}]*overflow-x: auto/s.test(idx));
    rec("static_checks", true, checks.join(" · "), performance.now() - t0);
  } catch (e) { rec("static_checks", false, String(e?.message ?? e), performance.now() - t0); }
}

// ============ Part C: Pyodide bridge — inspect ZIP/entry + decompress ============
let httpFetchCount = 0;
const _fetch = globalThis.fetch;
globalThis.fetch = async (input, init) => {
  const url = String(typeof input === "string" ? input : input.url);
  if (/^https?:\/\//.test(url)) { httpFetchCount++; throw new Error("BLOCKED http fetch: " + url); }
  return _fetch(input, init);
};
const pyodide = await loadPyodide({ indexURL: SITE + "/pyodide/" });
console.log("pyodide=" + pyodide.version + " python=" + pyodide.runPython("import sys; sys.version.split()[0]"));
rec("no_http_fetch_during_load", httpFetchCount === 0, `http fetch trong lúc loadPyodide: ${httpFetchCount}`, 0);
{
  const t0 = performance.now();
  try {
    await pyodide.loadPackage("lz4");
    const man = JSON.parse(fs.readFileSync(SITE + "/pb3/files.json", "utf8"));
    for (const rel of man.files) {
      const buf = fs.readFileSync(SITE + "/" + rel);
      const target = "/pb3web/" + rel.slice(4);
      pyodide.FS.mkdirTree(target.slice(0, target.lastIndexOf("/")));
      pyodide.FS.writeFile(target, new Uint8Array(buf));
    }
    const b64 = fs.readFileSync(SITE + "/fonts/BeVietnamPro-Regular.ttf.b64", "utf8").replace(/\s+/g, "");
    pyodide.FS.mkdirTree("/pb3web/fonts");
    pyodide.FS.writeFile("/pb3web/fonts/BeVietnamPro-Regular.ttf", new Uint8Array(Buffer.from(b64, "base64")));
    pyodide.globals.set("pb3_post_progress", () => {});
    pyodide.runPython("import sys; sys.path.insert(0, '/pb3web')\nimport bridge\nbridge.set_progress_cb(pb3_post_progress)");
    rec("pyodide_boot_bridge", true, `${man.files.length} file pb3/ + font ghi vào /pb3web, bridge import OK`, performance.now() - t0);
  } catch (e) { rec("pyodide_boot_bridge", false, String(e?.message ?? e), performance.now() - t0); }
}
{
  const t0 = performance.now();
  const boot = call("boot");
  rec("bridge_boot", boot.ok === true && boot.plugins === 20,
    `boot ok=${boot.ok} plugins=${boot.plugins}`, performance.now() - t0);
}
function putInput(hostPath, name) {
  const buf = fs.readFileSync(hostPath);
  pyodide.FS.mkdirTree("/tmp/input");
  pyodide.FS.writeFile("/tmp/input/" + name, new Uint8Array(buf));
  return "/tmp/input/" + name;
}
function call(fn, ...args) {
  const b = pyodide.globals.get("bridge");
  return JSON.parse(b[fn](...args));
}
{
  const t0 = performance.now();
  try {
    const p = putInput(EXP + "/unity_container.zip", "game.zip");
    const r = call("unity_inspect", p);
    assert(r.ok === true && r.is_container === true && r.kind === "zip",
      `inspect ZIP phải trả container: ${JSON.stringify(r).slice(0, 300)}`);
    const paths = (r.bundles || []).map((x) => x.path);
    assert(paths.includes("Payload/Game.app/Data/sample.unity3d") && paths.includes("Payload/Game.app/Data/resources.assets"),
      `bundles phải liệt kê cả 2: ${JSON.stringify(paths)}`);
    assert(r.bundles_total === 2, `bundles_total phải 2, thực tế ${r.bundles_total}`);
    assert(r.total_entries >= 4, `total_entries phải >= 4, thực tế ${r.total_entries}`);
    rec("unity_inspect_zip_lists_bundles", true,
      `is_container, bundles=${JSON.stringify(paths)}, total_entries=${r.total_entries}`, performance.now() - t0);
  } catch (e) { rec("unity_inspect_zip_lists_bundles", false, String(e?.message ?? e), performance.now() - t0); }
}
{
  const t0 = performance.now();
  try {
    const p = putInput(EXP + "/unity_container.zip", "game.zip");
    const rb = call("unity_inspect_entry", p, "Payload/Game.app/Data/sample.unity3d");
    assert(rb.ok === true && rb.bundle === true && (rb.files || []).length >= 1,
      `inspect entry bundle: ${JSON.stringify(rb).slice(0, 300)}`);
    const ra = call("unity_inspect_entry", p, "Payload/Game.app/Data/resources.assets");
    const f0 = (ra.files || [])[0] || {};
    assert(ra.ok === true && f0.version === 17 && f0.unity_version === "2019.4.40f1",
      `inspect entry .assets phải version 17: ${JSON.stringify(ra).slice(0, 300)}`);
    rec("unity_inspect_entry", true,
      `bundle files=${(rb.files || []).length}; assets version=${f0.version} ${f0.unity_version}`, performance.now() - t0);
  } catch (e) { rec("unity_inspect_entry", false, String(e?.message ?? e), performance.now() - t0); }
}
{
  const t0 = performance.now();
  try {
    const p = putInput(PB3 + "/tests/fixtures/extract/game1.zip", "game1.zip");
    const r = call("unity_inspect", p);
    assert(r.ok === true && r.is_container === true && r.bundles_total === 0 && (r.bundles || []).length === 0,
      `ZIP không bundle phải trả bundles=[]: ${JSON.stringify(r).slice(0, 300)}`);
    rec("unity_inspect_zip_no_bundles", true, "game1.zip: is_container, bundles=[] (UI hiện hướng dẫn trích xuất trước)",
      performance.now() - t0);
  } catch (e) { rec("unity_inspect_zip_no_bundles", false, String(e?.message ?? e), performance.now() - t0); }
}
{
  const t0 = performance.now();
  try {
    const p = putInput(PB3 + "/tests/fixtures/unity/resources.assets", "resources.assets");
    const r = call("unity_inspect", p);
    const f0 = (r.files || [])[0] || {};
    assert(r.ok === true && r.is_container === false && f0.version === 17,
      `inspect trực tiếp .assets phải version 17: ${JSON.stringify(r).slice(0, 300)}`);
    rec("unity_inspect_direct_assets", true, `version=${f0.version} ${f0.unity_version} (không đổi so với trước)`,
      performance.now() - t0);
  } catch (e) { rec("unity_inspect_direct_assets", false, String(e?.message ?? e), performance.now() - t0); }
}
{
  const t0 = performance.now();
  try {
    const p = putInput(PB3 + "/tests/fixtures/zip/sample.ipa", "sample.ipa");
    const c = call("compress_file", p, 6);
    assert(c.ok === true, `compress phải ok: ${JSON.stringify(c).slice(0, 200)}`);
    const d = call("decompress_file", "/tmp/out.tar.xz");
    assert(d.ok === true && d.byte_identical === true, `decompress phải byte-identical: ${JSON.stringify(d).slice(0, 300)}`);
    assert(d.filename === "sample.ipa", `decompress phải trả tên gốc sample.ipa, thực tế ${d.filename}`);
    rec("decompress_original_filename", true,
      `filename=${d.filename} (không còn restored.bin), byte_identical=${d.byte_identical}`, performance.now() - t0);
  } catch (e) { rec("decompress_original_filename", false, String(e?.message ?? e), performance.now() - t0); }
}
{
  const t0 = performance.now();
  try {
    const p = putInput(PB3 + "/tests/fixtures/extract/game1.zip", "game1.zip");
    const d = call("decompress_file", p);
    assert(d.ok === false && typeof d.error === "string" && d.error.length > 0,
      `giải nén file ZIP phải trả JSON lỗi sạch: ${JSON.stringify(d).slice(0, 300)}`);
    assert(!/Traceback/.test(d.error), "lỗi không được là traceback thô");
    rec("decompress_zip_clean_error", true, `ok=false, error=${d.error.slice(0, 120)}`, performance.now() - t0);
  } catch (e) { rec("decompress_zip_clean_error", false, String(e?.message ?? e), performance.now() - t0); }
}

// ============ Part D: render bảng ở 390px (Chromium headless) ============
// Đối chứng âm: CSS CŨ (word-break: break-all mọi ô) phải vỡ chữ thật — chứng
// minh phép đo phát hiện được lỗi; CSS MỚI phải giữ cột ngắn 1 dòng.
// Trang test bọc nội dung trong <main> như trang thật (redesign 2026-11-16:
// body padding 0 + main padding 0 12px để header sticky tràn full-width;
// không bọc <main> thì card rộng hơn trang thật 24px, bảng plugin (nội dung
// ~341–364px) vừa khít 364px và phép đo cuộn ngang sai thành "không cuộn"
// dù trên trang thật (bảng 340px) nó vẫn cuộn — lỗi hình học của trang test,
// không phải lỗi sản phẩm).
function tablePage(css) {
  return `<!DOCTYPE html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>${css}</style></head><body><main>
<div class="card"><table id="list-table"><tr><th>Đường dẫn</th><th>Loại</th><th>Kích thước</th></tr>
<tr><td>Payload/9Kings.app/Frameworks/AppleCoreNative.framework/_CodeSignature/CodeResources</td><td>file</td><td>1,2 MB</td></tr>
<tr><td>Payload/9Kings.app/Data/sharedassets0.assets</td><td>file</td><td>95,6 MB</td></tr>
<tr><td>Payload/9Kings.app/Info.plist</td><td>file</td><td>4,1 KB</td></tr>
<tr><td>Payload/9Kings.app/Data</td><td>thư mục</td><td>—</td></tr></table></div>
<div class="card"><table id="plugin-table"><tr><th>Plugin</th><th>Loại</th><th>Engine</th><th>Maturity hiệu lực</th></tr>
<tr><td>json</td><td>Format</td><td>Unity</td><td>stable — đã test trên game thật</td></tr></table></div>
</main></body></html>`;
}
function extractStyle(html) {
  const m = html.match(/<style>([\s\S]*?)<\/style>/);
  return m ? m[1] : "";
}
const OLD_CSS = `* { box-sizing: border-box; }
  body { font-family: -apple-system, "Segoe UI", Roboto, sans-serif; margin: 0; padding: 12px; background: #101418; color: #e8ecf0; line-height: 1.5; }
  .card { background: #1a2129; border: 1px solid #2a343f; border-radius: 10px; padding: 12px; margin: 10px 0; }
  table { border-collapse: collapse; width: 100%; font-size: 13px; }
  th, td { border-bottom: 1px solid #2a343f; padding: 5px 6px; text-align: left; word-break: break-all; }`;
{
  const t0 = performance.now();
  let browser = null;
  try {
    const idxHtml = fs.readFileSync(SITE + "/index.html", "utf8");
    const newCss = extractStyle(idxHtml);
    assert(newCss.length > 500, "không tách được <style> từ index.html thật");
    browser = await chromium.launch({
      executablePath: process.env.HOME + "/.cache/ms-playwright/chromium_headless_shell-1148/chrome-linux/headless_shell",
      args: ["--no-sandbox"],
    });
    const measure = async (css) => {
      const page = await browser.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 });
      await page.setContent(tablePage(css));
      const m = await page.evaluate(() => {
        const q = (sel) => [...document.querySelectorAll(sel)];
        // Số dòng THẬT của chữ trong ô: Range.getClientRects() đếm hộp dòng.
        // (Đo chiều cao ô thì sai — chiều cao ô = chiều cao HÀNG, hàng cao do
        // cột đường dẫn ngắt nhiều dòng, dù chữ ở cột Loại vẫn 1 dòng.)
        const lines = (el) => { const r = document.createRange(); r.selectNodeContents(el); return r.getClientRects().length; };
        const th = q("#list-table th").map((el) => ({ text: el.textContent, lines: lines(el) }));
        const loai = q("#list-table td:nth-child(2)").map((el) => ({ text: el.textContent, lines: lines(el) }));
        const size = q("#list-table td:nth-child(3)").map((el) => ({ text: el.textContent, lines: lines(el) }));
        const path = q("#list-table td:nth-child(1)").map((el) => ({ lines: lines(el) }));
        const tbl = document.querySelector("#list-table");
        const ptbl = document.querySelector("#plugin-table");
        const cs = getComputedStyle(tbl);
        return { th, loai, size, path,
          tableOverflowX: cs.overflowX, tableDisplay: cs.display,
          pluginScrollW: ptbl.scrollWidth, pluginClientW: ptbl.clientWidth };
      });
      await page.screenshot({ path: EXP + (css === newCss ? "/hotfix_table_390.png" : "/hotfix_table_390_old.png"), fullPage: true });
      await page.close();
      return m;
    };
    const oldM = await measure(OLD_CSS);
    const oldMaxLines = Math.max(...oldM.th.map((x) => x.lines), ...oldM.loai.map((x) => x.lines));
    const oldBroken = oldMaxLines > 1;
    rec("table_old_css_broken_control", oldBroken,
      `CSS cũ: chữ trong ô tiêu đề/Loại tối đa ${oldMaxLines} dòng (vỡ chữ nếu >1) — th=${JSON.stringify(oldM.th)}`,
      performance.now() - t0);
    const t1 = performance.now();
    const m = await measure(newCss);
    const shortMax = Math.max(...m.th.map((x) => x.lines), ...m.loai.map((x) => x.lines), ...m.size.map((x) => x.lines));
    const pathLines = m.path.map((x) => x.lines);
    assert(shortMax === 1, `cột ngắn phải đúng 1 dòng, thực tế tối đa ${shortMax} dòng — th=${JSON.stringify(m.th)} loai=${JSON.stringify(m.loai)} size=${JSON.stringify(m.size)}`);
    assert(m.tableOverflowX === "auto" && m.tableDisplay === "block",
      `bảng phải display:block + overflow-x:auto, thực tế ${m.tableDisplay}/${m.tableOverflowX}`);
    assert(m.pluginScrollW > m.pluginClientW,
      `bảng plugin (chữ dài nowrap) phải cuộn ngang, scrollW=${m.pluginScrollW} clientW=${m.pluginClientW}`);
    rec("table_390px_new_css", true,
      `390px: tiêu đề/Loại/Kích thước đúng 1 dòng (không vỡ chữ); cột đường dẫn ${JSON.stringify(pathLines)} dòng (được ngắt); bảng block+overflow-x:auto, plugin scrollW=${m.pluginScrollW} > clientW=${m.pluginClientW}`,
      performance.now() - t1);
  } catch (e) { rec("table_390px_new_css", false, String(e?.message ?? e), performance.now() - t0); }
  finally { if (browser) await browser.close(); }
}

// ============ Báo cáo ============
fs.writeFileSync(EXP + "/verify_hotfix_safari2_results.json", JSON.stringify({ failures, results }, null, 2));
const md = ["# Hotfix Safari đợt 2 — kết quả harness", "",
  `Chạy: ${new Date().toISOString()} · FAIL: ${failures} / ${Object.keys(results).length}`, "",
  ...Object.entries(results).map(([k, v]) => `## ${v.ok ? "PASS" : "FAIL"} — ${k} (${v.seconds}s)\n${v.detail}\n`)];
fs.writeFileSync(EXP + "/verify_hotfix_safari2_results.md", md.join("\n"));
console.log(`\n==== TỔNG: ${Object.keys(results).length - failures}/${Object.keys(results).length} PASS, ${failures} FAIL ====`);
process.exit(failures ? 1 : 0);
