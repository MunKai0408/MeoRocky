// verify_item3_kb.mjs — Kiểm chứng MỤC 3: KB → IndexedDB + autosave +
// persist + PWA + nhắc export (repo MunKai0408/MeoRocky).
//
// Chạy: node verify_item3_kb.mjs (từ /home/hatch/workspace/pb3_web/exp)
// Kết quả: item3_results.md + item3_results.json
//
// 4 phần:
//  A. Unit KBStore thật (site/kb-store.js) với IndexedDB GIẢ trong Node
//     (load/save/flush/autosave/lỗi rồi lưu lại/persist/import magic/
//     lưu khẩn khi ẩn tab).
//  B. Bridge thật trên Pyodide Node: kb_snapshot/kb_restore vòng tròn
//     (thêm game → snapshot → quên → restore → còn game; restore sai
//     thì KB cũ giữ nguyên), kb_list persisted/session_only.
//  C. Kiểm tra tĩnh: manifest, icon, nối index.html/app.js/kb-store.js,
//     không COI/OPFS, node --check.
//  D. Trình duyệt THẬT (Playwright chromium headless shell, serve site
//     qua HTTP local): boot → thêm game vào KB → reload trang → KB còn
//     (khôi phục từ IndexedDB thật) → nhắc export hiện → xuất .sqlite
//     (download thật) → localStorage ghi lần xuất → nhắc ẩn → PWA
//     beforeinstallprompt hiện nút Cài.
//     Lưu ý: addInitScript ép navigator.canShare -> false để đi đường
//     blob-download (headless shell không có Share sheet thật; trên
//     iPhone thật đường Share mới là chính — đã kiểm chứng ở hotfix 2).
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import vm from "node:vm";
import { execFileSync } from "node:child_process";
import { loadPyodide } from "../site/pyodide/pyodide.mjs";

const SITE = "/home/hatch/workspace/pb3_web/site";
const EXP = "/home/hatch/workspace/pb3_web/exp";
const PB3 = "/home/hatch/workspace/pb3_fix/pb3";
const GAME1 = PB3 + "/tests/fixtures/extract/game1.zip";
const sec = (ms) => Math.round(ms / 100) / 10;
const results = {};
let failures = 0;
function rec(name, ok, detail, ms) {
  results[name] = { ok, detail, seconds: ms == null ? undefined : sec(ms) };
  if (!ok) failures++;
  console.log(`\n### ${name}: ${ok ? "PASS" : "FAIL"}${ms == null ? "" : " (" + sec(ms) + "s)"}\n${detail}`);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ================= A. Fake IndexedDB + KBStore thật =================
const fakeStores = new Map(); // dbName -> Map(storeName -> Map)
const docListeners = {};
const docStub = {
  visibilityState: "visible",
  addEventListener(type, fn) { (docListeners[type] = docListeners[type] || []).push(fn); },
};
const winListeners = {};
function makeFakeDB(name) {
  if (!fakeStores.has(name)) fakeStores.set(name, new Map());
  const stores = fakeStores.get(name);
  return {
    createObjectStore(s) { if (!stores.has(s)) stores.set(s, new Map()); },
    transaction(storeName, _mode) {
      const store = stores.get(storeName) || new Map();
      stores.set(storeName, store);
      const tx = {
        objectStore() {
          return {
            get(k) { return { result: store.has(k) ? store.get(k) : undefined }; },
            put(v, k) { store.set(k, v); return { result: k }; },
            delete(k) { store.delete(k); return { result: undefined }; },
          };
        },
        oncomplete: null, onerror: null, onabort: null, error: null,
      };
      setTimeout(() => { if (tx.oncomplete) tx.oncomplete(); }, 0);
      return tx;
    },
    close() {},
  };
}
const fakeIndexedDB = {
  open(name, _version) {
    const req = { result: null, onupgradeneeded: null, onsuccess: null, onerror: null, error: null };
    setTimeout(() => {
      const isNew = !fakeStores.has(name);
      req.result = makeFakeDB(name);
      if (isNew && req.onupgradeneeded) req.onupgradeneeded();
      if (req.onsuccess) req.onsuccess();
    }, 0);
    return req;
  },
};
const sandboxWindow = {};
const sandbox = {
  indexedDB: fakeIndexedDB,
  navigator: { storage: { persist: async () => true } },
  document: docStub,
  addEventListener(type, fn) { (winListeners[type] = winListeners[type] || []).push(fn); },
  window: sandboxWindow,
  console,
  setTimeout, clearTimeout,
};
vm.createContext(sandbox);
const kbStoreSrc = fs.readFileSync(SITE + "/kb-store.js", "utf8");
const KBStore = vm.runInContext(kbStoreSrc + "\n;KBStore;", sandbox);
const SQLITE_MAGIC = Buffer.from("SQLite format 3\0", "latin1");

{ // A1. load khi trống -> null
  const t0 = performance.now();
  const kb = new KBStore("kb.sqlite", { debounceMs: 0 });
  const v = await kb.load();
  rec("kbstore_load_empty", v === null, "load() trên IndexedDB trống -> " + JSON.stringify(v), performance.now() - t0);
}
{ // A2. saveNow -> load đúng bytes
  const t0 = performance.now();
  const kb = new KBStore("kb.sqlite", { debounceMs: 0 });
  const bytes = new Uint8Array([...SQLITE_MAGIC, 1, 2, 3, 4]);
  await kb.saveNow(bytes);
  const v = await kb.load();
  const ok = v && v.length === bytes.length && v.every((b, i) => b === bytes[i]);
  rec("kbstore_save_load_roundtrip", !!ok, "saveNow 20 byte (magic SQLite + 4 byte) -> load() dài " + (v && v.length) + " byte, khớp từng byte: " + !!ok, performance.now() - t0);
}
{ // A3. markDirty + flush: autosave qua attach(getBytes)
  const t0 = performance.now();
  const kb = new KBStore("kb2.sqlite", { debounceMs: 0 });
  const bytes = new Uint8Array([...SQLITE_MAGIC, 9, 9, 9]);
  kb.attach(async () => bytes);
  kb.markDirty();
  await kb.flush();
  const v = await kb.load();
  const ok = v && v.length === bytes.length && v.every((b, i) => b === bytes[i]);
  rec("kbstore_markdirty_flush", !!ok && kb.lastSavedAt > 0, "markDirty() + flush() -> IndexedDB có bytes mới (dài " + (v && v.length) + "), lastSavedAt=" + kb.lastSavedAt, performance.now() - t0);
}
{ // A4. flush lỗi -> dirty lại + onStatus error; sửa xong flush lại được
  const t0 = performance.now();
  const kb = new KBStore("kb3.sqlite", { debounceMs: 0 });
  const events = [];
  kb.onStatus = (s, d) => events.push(s + (d ? ":" + (d.message || d) : ""));
  let failOnce = true;
  kb.attach(async () => { if (failOnce) { failOnce = false; throw new Error("snapshot loi gia"); } return new Uint8Array([...SQLITE_MAGIC, 7]); });
  kb.markDirty();
  await kb.flush();
  const dirtyAgain = kb._dirty === true;
  const sawError = events.some((e) => e.startsWith("error:"));
  kb.markDirty();
  await kb.flush();
  const v = await kb.load();
  const savedOk = v && v.length === 17 && v[16] === 7;
  rec("kbstore_flush_error_redirty", dirtyAgain && sawError && savedOk,
    "flush lỗi -> _dirty=" + dirtyAgain + " (lưu lại sau), onStatus=" + JSON.stringify(events) +
    "; flush lại sau khi sửa -> load() dài " + (v && v.length) + " byte đúng: " + !!savedOk, performance.now() - t0);
}
{ // A5. requestPersistence gọi navigator.storage.persist
  const t0 = performance.now();
  const kb = new KBStore("kb4.sqlite", { debounceMs: 0 });
  const got = await kb.requestPersistence();
  rec("kbstore_request_persistence", got === true, "requestPersistence() -> " + got + " (navigator.storage.persist giả trả true)", performance.now() - t0);
}
{ // A6. importFile: magic SQLite đúng thì qua, sai thì ném lỗi
  const t0 = performance.now();
  const goodFile = { slice: (a, b) => ({ arrayBuffer: async () => SQLITE_MAGIC.slice(a, b).buffer.slice(0, 16) }), arrayBuffer: async () => new Uint8Array([...SQLITE_MAGIC, 5]).buffer };
  // slice(0,16).arrayBuffer phải trả đúng 16 byte magic: dùng Buffer slice rồi .buffer có thể lệch offset nên làm file giả rõ ràng:
  const goodFile2 = {
    slice: () => ({ arrayBuffer: async () => SQLITE_MAGIC.buffer.slice(SQLITE_MAGIC.byteOffset, SQLITE_MAGIC.byteOffset + 16) }),
    arrayBuffer: async () => { const u = new Uint8Array([...SQLITE_MAGIC, 5]); return u.buffer; },
  };
  const badFile = {
    slice: () => ({ arrayBuffer: async () => new Uint8Array([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16]).buffer }),
    arrayBuffer: async () => new Uint8Array(16).buffer,
  };
  let goodOk = false, badErr = "";
  try { const b = await KBStore.importFile(goodFile2); goodOk = b.length === 17; } catch (e) { goodOk = false; }
  try { await KBStore.importFile(badFile); } catch (e) { badErr = e.message; }
  rec("kbstore_importfile_magic", goodOk && /Không phải file SQLite/.test(badErr),
    "file magic đúng -> importFile trả 17 byte: " + goodOk + "; file magic sai -> ném: " + JSON.stringify(badErr), performance.now() - t0);
}
{ // A7. Ẩn tab (visibilitychange hidden) thì flush khẩn
  const t0 = performance.now();
  const kb = new KBStore("kb5.sqlite", { debounceMs: 60000 }); // debounce dài: chỉ lifecycle mới cứu
  kb.attach(async () => new Uint8Array([...SQLITE_MAGIC, 3, 3]));
  kb.markDirty();
  docStub.visibilityState = "hidden";
  for (const fn of (docListeners.visibilitychange || [])) fn();
  await sleep(150);
  const v = await kb.load();
  const ok = v && v.length === 18 && v[16] === 3;
  docStub.visibilityState = "visible";
  rec("kbstore_lifecycle_flush_on_hide", !!ok, "debounce 60s nhưng ẩn tab -> flush khẩn chạy, load() dài " + (v && v.length) + " byte đúng: " + !!ok, performance.now() - t0);
}
{ // A8. Ghi đè: saveNow lần 2 thay hẳn lần 1
  const t0 = performance.now();
  const kb = new KBStore("kb6.sqlite", { debounceMs: 0 });
  await kb.saveNow(new Uint8Array([...SQLITE_MAGIC, 1]));
  await kb.saveNow(new Uint8Array([...SQLITE_MAGIC, 2, 2]));
  const v = await kb.load();
  rec("kbstore_overwrite", v && v.length === 18 && v[16] === 2 && v[17] === 2,
    "save 17 byte rồi save 18 byte -> load() dài " + (v && v.length) + ", byte cuối đúng bản mới", performance.now() - t0);
}

// ================= B. Bridge thật trên Pyodide Node =================
const pyodide = await loadPyodide({ indexURL: SITE + "/pyodide/" });
await pyodide.loadPackage("lz4");
{
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
}
pyodide.globals.set("pb3_post_progress", () => {});
pyodide.runPython("import sys; sys.path.insert(0, '/pb3web')\nimport bridge\nbridge.set_progress_cb(pb3_post_progress)");
function call(fn, ...args) {
  return JSON.parse(pyodide.globals.get("bridge")[fn](...args));
}
function putInput(hostPath, name) {
  const buf = fs.readFileSync(hostPath);
  pyodide.FS.mkdirTree("/tmp/input");
  pyodide.FS.writeFile("/tmp/input/" + name, new Uint8Array(buf));
  return "/tmp/input/" + name;
}
const b64ToBuf = (s) => Buffer.from(s, "base64");
const isSqlite = (buf) => buf.slice(0, 16).equals(SQLITE_MAGIC);

let boot;
{ // B1. boot + bridge có kb_snapshot/kb_restore
  const t0 = performance.now();
  boot = call("boot");
  const has = JSON.parse(pyodide.runPython(
    "import json; json.dumps({'kb_snapshot': hasattr(bridge, 'kb_snapshot'), 'kb_restore': hasattr(bridge, 'kb_restore'), 'kb_list': hasattr(bridge, 'kb_list')})"));
  rec("bridge_boot_and_kb_fns", boot.ok === true && boot.plugins === 20 && Object.values(has).every(Boolean),
    "boot plugins=" + boot.plugins + "; bridge fns: " + JSON.stringify(has), performance.now() - t0);
}
{ // B2. kb_list: persisted true, session_only false (Mục 3 đổi thật)
  const t0 = performance.now();
  const l = call("kb_list");
  rec("bridge_kb_list_persisted_flags", l.ok === true && l.persisted === true && l.session_only === false,
    "kb_list -> persisted=" + l.persisted + " session_only=" + l.session_only + " games=" + l.games.length, performance.now() - t0);
}
let snapB64 = null, gameId = null;
{ // B3. snapshot vòng tròn: thêm -> snapshot -> quên -> restore -> còn
  const t0 = performance.now();
  putInput(GAME1, "game1.zip");
  const a = call("kb_add", "/tmp/input/game1.zip");
  gameId = a.game_id;
  const snap = call("kb_snapshot");
  snapB64 = snap.b64;
  const snapBytes = snap.b64 ? b64ToBuf(snap.b64) : null;
  const snapOk = snap.ok === true && snap.games === 1 && snapBytes && isSqlite(snapBytes) && snap.size === snapBytes.length;
  // Sau snapshot KB vẫn dùng được (snapshot mở lại KB).
  const qAfter = call("kb_query", "/tmp/input/game1.zip");
  const f = call("kb_forget", String(gameId));
  const l0 = call("kb_list");
  const r = call("kb_restore", snapB64);
  const l1 = call("kb_list");
  const restoredId = (l1.games || [])[0] && l1.games[0].id;
  rec("bridge_kb_snapshot_restore_roundtrip",
    a.ok === true && snapOk && qAfter.ok === true && qAfter.found === true &&
    f.ok === true && f.forgotten === true && l0.games.length === 0 &&
    r.ok === true && r.games === 1 && l1.games.length === 1 && restoredId === gameId,
    `kb_add game_id=${gameId}; snapshot games=${snap.games} size=${snap.size}B magic SQLite=${snapBytes && isSqlite(snapBytes)}; ` +
    `query sau snapshot found=${qAfter.found}; forget -> list=${l0.games.length}; restore games=${r.games} list=${l1.games.length} id_khoi_phuc=${restoredId} (phải = ${gameId})`,
    performance.now() - t0);
}
{ // B4. restore sai: KB cũ giữ nguyên
  const t0 = performance.now();
  const badB64 = call("kb_restore", "!!!khong-phai-base64!!!");
  const notSqlite = call("kb_restore", Buffer.from("hello khong phai sqlite").toString("base64"));
  const empty = call("kb_restore", "");
  const l = call("kb_list");
  rec("bridge_kb_restore_invalid_keeps_old",
    badB64.ok === false && notSqlite.ok === false && empty.ok === false &&
    l.ok === true && l.games.length === 1 && l.games[0].id === gameId,
    "base64 sai -> ok=" + badB64.ok + " (" + (badB64.error || "").slice(0, 60) + "); " +
    "base64 đúng nhưng không phải SQLite -> ok=" + notSqlite.ok + "; rỗng -> ok=" + empty.ok +
    "; sau 3 lần restore lỗi, kb_list vẫn 1 game id=" + ((l.games || [])[0] || {}).id, performance.now() - t0);
}
{ // B5. snapshot KB trống -> 0 game, b64 vẫn là SQLite hợp lệ; restore -> 0 game
  const t0 = performance.now();
  call("kb_forget", String(gameId));
  const snap = call("kb_snapshot");
  const snapBytes = snap.b64 ? b64ToBuf(snap.b64) : null;
  const r = call("kb_restore", snap.b64);
  const l = call("kb_list");
  rec("bridge_kb_snapshot_empty",
    snap.ok === true && snap.games === 0 && snapBytes && isSqlite(snapBytes) &&
    r.ok === true && r.games === 0 && l.games.length === 0,
    "snapshot KB trống: games=" + snap.games + " size=" + snap.size + "B magic SQLite=" + !!(snapBytes && isSqlite(snapBytes)) +
    "; restore -> games=" + r.games + " list=" + l.games.length, performance.now() - t0);
}

// ================= C. Kiểm tra tĩnh =================
function read(p) { return fs.readFileSync(p, "utf8"); }
function has(p, s) { try { return read(p).includes(s); } catch (e) { return false; } }
function pngSize(p) {
  const b = fs.readFileSync(p);
  if (b.length < 24 || b.readUInt32BE(0) !== 0x89504e47) return null;
  return { w: b.readUInt32BE(16), h: b.readUInt32BE(20) };
}
{ // C1. manifest hợp lệ + đủ field + icon tồn tại
  const t0 = performance.now();
  let m = null, parseErr = "";
  try { m = JSON.parse(read(SITE + "/manifest.webmanifest")); } catch (e) { parseErr = e.message; }
  const iconsOk = m && (m.icons || []).every((ic) => fs.existsSync(SITE + "/" + ic.src));
  const ok = !!m && m.short_name === "BearTea" && m.display === "standalone" &&
    typeof m.start_url === "string" && m.theme_color === "#101418" &&
    (m.icons || []).some((ic) => ic.sizes === "192x192") &&
    (m.icons || []).some((ic) => ic.sizes === "512x512") && iconsOk;
  rec("static_manifest_valid", ok,
    "manifest parse " + (m ? "OK" : "LỖI: " + parseErr) + "; short_name=" + (m && m.short_name) +
    " display=" + (m && m.display) + " start_url=" + (m && m.start_url) +
    " icons=" + JSON.stringify((m && m.icons || []).map((i) => i.sizes)) + " (file tồn tại: " + iconsOk + ")", performance.now() - t0);
}
{ // C2. icon PNG đúng kích thước
  const t0 = performance.now();
  const s192 = pngSize(SITE + "/icons/icon-192.png");
  const s512 = pngSize(SITE + "/icons/icon-512.png");
  const s180 = pngSize(SITE + "/icons/apple-touch-icon.png");
  rec("static_icons_png_size",
    s192 && s192.w === 192 && s192.h === 192 &&
    s512 && s512.w === 512 && s512.h === 512 &&
    s180 && s180.w === 180 && s180.h === 180,
    "icon-192=" + JSON.stringify(s192) + " icon-512=" + JSON.stringify(s512) + " apple-touch-icon=" + JSON.stringify(s180), performance.now() - t0);
}
{ // C3. index.html nối manifest + kb-store.js trước app.js + UI KB/PWA
  const t0 = performance.now();
  const h = read(SITE + "/index.html");
  const kbStorePos = h.indexOf('src="kb-store.js"');
  const appPos = h.indexOf('src="app.js"');
  const checks = {
    manifest_link: h.includes('rel="manifest" href="manifest.webmanifest"'),
    apple_touch_icon: h.includes('rel="apple-touch-icon"'),
    theme_color: h.includes('name="theme-color"'),
    kb_store_before_app: kbStorePos > 0 && appPos > 0 && kbStorePos < appPos,
    kb_status_el: h.includes('id="kb-status"'),
    kb_persist_el: h.includes('id="kb-persist"'),
    kb_reminder_el: h.includes('id="kb-export-reminder"'),
    pwa_button: h.includes('id="btn-pwa-install"'),
    pwa_note_ios: h.includes("Thêm vào Màn hình chính"),
    seven_day_note: h.includes("7 ngày"),
    no_session_only_text: !h.includes("chỉ sống trong phiên"),
  };
  const bad = Object.entries(checks).filter(([, v]) => !v).map(([k]) => k);
  rec("static_index_wiring", bad.length === 0,
    "thiếu/sai: " + (bad.length ? bad.join(", ") : "không có") +
    " | kb-store.js ở vị trí " + kbStorePos + ", app.js ở " + appPos + " (kb-store phải trước)", performance.now() - t0);
}
{ // C4. app.js nối KB bền vững + PWA
  const t0 = performance.now();
  const a = read(SITE + "/app.js");
  const checks = {
    kbstore_new: a.includes("new KBStore("),
    attach_snapshot: a.includes("kb_snapshot"),
    restore_fn: a.includes("restoreKbFromIdb") && a.includes("kb_restore"),
    persist_now: a.includes("persistKbNow()"),
    persist_api: a.includes("requestPersistence") && a.includes("navigator.storage.persisted"),
    last_export_key: a.includes("pb3_kb_last_export"),
    reminder_7d: a.includes("KB_EXPORT_REMINDER_DAYS"),
    pwa_event: a.includes("beforeinstallprompt"),
    pwa_button_id: a.includes("btn-pwa-install"),
    boot_calls_restore: a.includes("restoreKbFromIdb().then"),
    no_session_only_note: !a.includes("chỉ sống trong phiên"),
  };
  const bad = Object.entries(checks).filter(([, v]) => !v).map(([k]) => k);
  rec("static_app_wiring", bad.length === 0,
    "thiếu/sai: " + (bad.length ? bad.join(", ") : "không có"), performance.now() - t0);
}
{ // C5. kb-store.js: node --check + API đủ + classic (không export) + không OPFS/COI
  const t0 = performance.now();
  let checkOk = false, checkErr = "";
  try { execFileSync("node", ["--check", SITE + "/kb-store.js"], { stdio: "pipe" }); checkOk = true; }
  catch (e) { checkErr = String(e.message).slice(0, 200); }
  try { execFileSync("node", ["--check", SITE + "/app.js"], { stdio: "pipe" }); }
  catch (e) { checkOk = false; checkErr += " | app.js: " + String(e.message).slice(0, 120); }
  const k = read(SITE + "/kb-store.js");
  // "Không dùng OPFS" chỉ kiểm tra API thật (createSyncAccessHandle /
  // showOpenFilePicker / getDirectory), không bắt chữ OPFS trong comment
  // giải thích ở đầu file ("KHÔNG dùng OPFS/SAH ở giai đoạn này").
  const noOpfsApi = !/createSyncAccessHandle|showOpenFilePicker|getDirectory\s*\(/i.test(k);
  const ok = checkOk &&
    k.includes("indexedDB.open") && k.includes("requestPersistence") &&
    k.includes("markDirty") && k.includes("async flush") &&
    k.includes("importFile") && k.includes("visibilitychange") &&
    !/^\s*export\s/m.test(k) && noOpfsApi &&
    !/Cross-Origin-Opener-Policy|Cross-Origin-Embedder-Policy|coi-serviceworker/i.test(k);
  rec("static_kbstore_file", ok,
    "node --check kb-store.js + app.js: " + (checkOk ? "OK" : "LỖI " + checkErr) +
    " | có indexedDB.open/requestPersistence/markDirty/flush/importFile/visibilitychange: " +
    ["indexedDB.open", "requestPersistence", "markDirty", "async flush", "importFile", "visibilitychange"].map((s) => k.includes(s)).join(",") +
    " | classic (không export): " + !/^\s*export\s/m.test(k) + " | không API OPFS/COI: " +
    (noOpfsApi && !/Cross-Origin-Opener-Policy/i.test(k)), performance.now() - t0);
}
{ // C6. bridge.py: có kb_snapshot/kb_restore, kb_list đổi cờ, py_compile OK
  const t0 = performance.now();
  let compileOk = false;
  try { execFileSync("python3", ["-m", "py_compile", SITE + "/pb3/bridge.py"], { stdio: "pipe" }); compileOk = true; }
  catch (e) { compileOk = false; }
  const b = read(SITE + "/pb3/bridge.py");
  const ok = compileOk && b.includes("def kb_snapshot(") && b.includes("def kb_restore(") &&
    b.includes('"session_only": False') && b.includes('"persisted": True') &&
    b.includes('b"SQLite format 3\\x00"') && b.includes("import base64");
  rec("static_bridge_kb_api", ok,
    "py_compile: " + (compileOk ? "OK" : "LỖI") + " | kb_snapshot=" + b.includes("def kb_snapshot(") +
    " kb_restore=" + b.includes("def kb_restore(") + " session_only False=" + b.includes('"session_only": False') +
    " persisted True=" + b.includes('"persisted": True') + " magic check=" + b.includes('b"SQLite format 3\\x00"'), performance.now() - t0);
}
{ // C7. Không COI trong file mới/sửa của mục 3
  const t0 = performance.now();
  const files = [SITE + "/kb-store.js", SITE + "/app.js", SITE + "/index.html",
    SITE + "/manifest.webmanifest", SITE + "/pb3/bridge.py"];
  const bad = files.filter((f) => /Cross-Origin-Opener-Policy|Cross-Origin-Embedder-Policy|coi-serviceworker/i.test(read(f)));
  rec("static_no_coi_item3", bad.length === 0,
    "file chứa COI: " + (bad.length ? bad.join(", ") : "không có"), performance.now() - t0);
}

// ================= D. Trình duyệt THẬT (Playwright headless shell) =================
const MIME = {
  ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript",
  ".json": "application/json", ".webmanifest": "application/manifest+json",
  ".wasm": "application/wasm", ".png": "image/png", ".b64": "text/plain",
  ".ttf": "font/ttf", ".txt": "text/plain", ".md": "text/plain", ".css": "text/css",
};
const server = http.createServer((req, res) => {
  const urlPath = decodeURIComponent(new URL(req.url || "/", "http://127.0.0.1").pathname);
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
try {
  const { chromium } = await import("playwright-core");
  const candidates = [
    process.env.HOME + "/.cache/ms-playwright/chromium_headless_shell-1148/chrome-linux/headless_shell",
    process.env.HOME + "/.cache/ms-playwright/chromium-1148/chrome-linux/chrome",
  ];
  const exe = candidates.find((c) => fs.existsSync(c));
  if (!exe) throw new Error("không tìm thấy Chromium headless shell ở ~/.cache/ms-playwright (đã thử: " + candidates.join(", ") + ")");
  browser = await chromium.launch({ executablePath: exe, args: ["--no-sandbox"] });
  const ctx = await browser.newContext({ acceptDownloads: true });
  const page = await ctx.newPage();
  // Ép canShare -> false để đi đường blob-download (headless shell không
  // có Share sheet thật; trên iPhone thật đường Share mới là chính).
  // REDESIGN 2026-11-16: panel KB nay nằm ở view riêng "Kho KB" (bottom nav
  // #btn-nav-kb), nút tab cũ #btn-kb chỉ còn trong view "Chức năng" (ẩn khi
  // đang ở Trang chủ) nên Playwright click #btn-kb sẽ timeout vì phần tử ẩn.
  // Đổi 4 lần click #btn-kb -> #btn-nav-kb (cùng đích: hiện panel KB); các
  // nút #btn-kb-add / #btn-kb-list / #btn-kb-export / #btn-pwa-install bên
  // trong panel không đổi. Đây là thích ứng harness theo thay đổi điều hướng
  // thật của sản phẩm, không phải nới lỏng kiểm tra.
  await page.addInitScript(() => {
    try { Object.defineProperty(navigator, "canShare", { value: () => false }); } catch (e) {}
  });
  const pageErrors = [];
  page.on("pageerror", (e) => pageErrors.push(String(e && e.message ? e.message : e)));
  const waitReady = async (label) => {
    // Mục 6 (lazy boot): boot chỉ chạy khi chọn file — chọn 1 file nhỏ
    // trước rồi mới chờ "Sẵn sàng".
    await page.setInputFiles("#file-input", GAME1);
    await page.waitForFunction(
      () => { const el = document.getElementById("status"); return el && el.textContent.includes("Sẵn sàng"); },
      null, { timeout: 240000 });
  };
  const kbStatusText = () => page.evaluate(() => (document.getElementById("kb-status") || {}).textContent || "");

  { // D1. Boot thật trong trình duyệt
    const t0 = performance.now();
    await page.goto("http://127.0.0.1:" + PORT + "/index.html");
    await waitReady("boot1");
    await page.click("#btn-nav-kb");
    await page.waitForFunction(
      () => { const el = document.getElementById("kb-status"); return el && /KB: \d+ game/.test(el.textContent); },
      null, { timeout: 60000 });
    const st = await kbStatusText();
    rec("browser_boot_and_kb_status", /KB: \d+ game/.test(st),
      "status=Sẵn sàng; #kb-status sau boot: " + JSON.stringify(st), performance.now() - t0);
  }
  { // D2. Thêm game vào KB -> autosave -> #kb-status hiện 1 game
    const t0 = performance.now();
    await page.setInputFiles("#file-input", GAME1);
    await page.waitForFunction(
      () => { const el = document.getElementById("file-info"); return el && !el.hidden && el.textContent.includes("game1.zip"); },
      null, { timeout: 30000 });
    await page.click("#btn-nav-kb");
    await page.click("#btn-kb-add");
    await page.waitForFunction(
      () => { const el = document.getElementById("kb-result"); return el && el.textContent.includes("Đã thêm game vào KB"); },
      null, { timeout: 60000 });
    await page.waitForFunction(
      () => { const el = document.getElementById("kb-status"); return el && /KB: 1 game/.test(el.textContent); },
      null, { timeout: 60000 });
    rec("browser_kb_add_autosave", true,
      "#kb-result: Đã thêm game vào KB; #kb-status sau autosave: " + JSON.stringify(await kbStatusText()), performance.now() - t0);
  }
  { // D3. Nhắc export hiện (chưa từng xuất + KB có dữ liệu) + trạng thái persist
    const t0 = performance.now();
    const reminderVisible = await page.evaluate(() => {
      const el = document.getElementById("kb-export-reminder");
      return el && !el.hidden && el.textContent.length > 0;
    });
    const persistText = await page.evaluate(() => (document.getElementById("kb-persist") || {}).textContent || "");
    const reminderText = await page.evaluate(() => (document.getElementById("kb-export-reminder") || {}).textContent || "");
    rec("browser_export_reminder_and_persist", reminderVisible && persistText.includes("Lưu trữ bền vững:"),
      "nhắc export hiện: " + reminderVisible + " (" + reminderText.slice(0, 90) + "…); #kb-persist: " + JSON.stringify(persistText), performance.now() - t0);
  }
  { // D4. Reload trang -> KB còn (khôi phục từ IndexedDB thật)
    const t0 = performance.now();
    await page.reload();
    await waitReady("boot2");
    await page.waitForFunction(
      () => { const el = document.getElementById("kb-status"); return el && /KB: 1 game/.test(el.textContent); },
      null, { timeout: 60000 });
    await page.click("#btn-nav-kb");
    await page.click("#btn-kb-list");
    await page.waitForFunction(
      () => { const el = document.getElementById("kb-result"); return el && el.textContent.includes("1 game trong KB"); },
      null, { timeout: 60000 });
    const res = await page.evaluate(() => (document.getElementById("kb-result") || {}).textContent || "");
    rec("browser_kb_survives_reload", res.includes("1 game trong KB"),
      "sau reload + boot lại: #kb-status 1 game; #kb-result sau Liệt kê: " + JSON.stringify(res.slice(0, 120)), performance.now() - t0);
  }
  { // D5. Xuất .sqlite (download thật) -> localStorage ghi lần xuất -> nhắc ẩn
    const t0 = performance.now();
    await page.click("#btn-nav-kb");
    const dlPromise = page.waitForEvent("download", { timeout: 60000 });
    await page.click("#btn-kb-export");
    const dl = await dlPromise;
    const fname = dl.suggestedFilename();
    const lastExport = await page.evaluate(() => localStorage.getItem("pb3_kb_last_export"));
    const reminderHidden = await page.evaluate(() => {
      const el = document.getElementById("kb-export-reminder");
      return el ? el.hidden : null;
    });
    rec("browser_kb_export_download_and_reminder_off", fname === "pb3_kb.sqlite" &&
      lastExport !== null && Number(lastExport) > 0 && reminderHidden === true,
      "download suggestedFilename=" + fname + "; localStorage pb3_kb_last_export=" + lastExport +
      "; nhắc export đã ẩn: " + reminderHidden, performance.now() - t0);
  }
  { // D6. PWA: beforeinstallprompt -> nút Cài hiện; bấm không lỗi (Safari không có prompt thật)
    const t0 = performance.now();
    await page.evaluate(() => window.dispatchEvent(new Event("beforeinstallprompt")));
    const btnVisible = await page.evaluate(() => {
      const b = document.getElementById("btn-pwa-install");
      return b && !b.hidden;
    });
    await page.click("#btn-pwa-install");
    await sleep(300);
    const manifestOk = await page.evaluate(async () => {
      const r = await fetch("manifest.webmanifest");
      if (!r.ok) return false;
      const m = await r.json();
      return m.short_name === "BearTea";
    });
    rec("browser_pwa_install_prompt", btnVisible === true && manifestOk === true && pageErrors.length === 0,
      "dispatch beforeinstallprompt -> nút Cài hiện: " + btnVisible + "; bấm nút (không có prompt thật) không lỗi trang; " +
      "fetch manifest.webmanifest short_name=BearTea: " + manifestOk + "; pageerror: " + JSON.stringify(pageErrors), performance.now() - t0);
  }
  { // D7. Không lỗi trang trong suốt phiên trình duyệt
    const t0 = performance.now();
    rec("browser_no_pageerrors", pageErrors.length === 0,
      "pageerror trong cả phiên (boot x2 + thêm KB + reload + xuất + PWA): " + (pageErrors.length ? JSON.stringify(pageErrors) : "không có"), performance.now() - t0);
  }
  await browser.close();
  browser = null;
} catch (e) {
  if (browser) { try { await browser.close(); } catch (_) {} }
  rec("browser_end_to_end", false, "LỖI trình duyệt thật: " + String(e && e.message ? e.message : e), undefined);
}
server.close();

fs.writeFileSync(EXP + "/item3_results.json", JSON.stringify(
  { when: new Date().toISOString(), failures, results }, null, 2));
const lines = ["# Kiểm chứng Mục 3 — KB → IndexedDB + autosave + persist + PWA", "",
  "Ngày: 2026-11-16. Harness: exp/verify_item3_kb.mjs.",
  "A: unit KBStore với IndexedDB giả. B: bridge thật trên Pyodide Node.",
  "C: kiểm tra tĩnh. D: trình duyệt thật (Playwright chromium headless shell, serve site qua HTTP local).",
  "",
  "| Mục | Kết quả | Giây | Chi tiết |", "|---|---|---|---|"];
for (const [k, v] of Object.entries(results)) {
  if (k.startsWith("_")) continue;
  lines.push(`| ${k} | ${v.ok ? "PASS" : "FAIL"} | ${v.seconds == null ? "—" : v.seconds} | ${String(v.detail).replace(/\|/g, "/").slice(0, 260)} |`);
}
lines.push("", `Thất bại: ${failures}`);
fs.writeFileSync(EXP + "/item3_results.md", lines.join("\n"));
console.log(`\n==== KẾT LUẬN: ${failures === 0 ? "TẤT CẢ XANH" : failures + " FAIL"} ====`);
process.exit(failures === 0 ? 0 : 1);
