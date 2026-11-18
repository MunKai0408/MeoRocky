/* tm_chromium_check.js — Kiểm tra UI TM (T3) trên Chromium thật qua CDP.
 *
 * Không cần Playwright/Puppeteer: dùng WebSocket có sẵn của Node 22+
 * nói chuyện với chrome --remote-debugging-port. Chạy:
 *   1. cd <repo> && python3 -m http.server 8123 &
 *   2. chrome --headless=new --remote-debugging-port=9223 \
 *        --user-data-dir=<profile> http://127.0.0.1:8123/index.html &
 *   3. node exp/tm_chromium_check.js [http://127.0.0.1:9223] [tm-import.json]
 * Thoát 0 khi mọi kiểm tra PASS, 1 khi có FAIL.
 * Cần gói `ws` (npm i ws) vì WebSocket global của Node không nhận
 * message từ Chrome 131 trong môi trường này (đã đo thật).
 *
 * Luồng kiểm tra (IndexedDB thật trong Chromium):
 *   seed 2 job done -> hộp TM liệt kê đúng cặp/số mục -> bấm Xuất TM
 *   -> nhập file TM (2 mới + 1 trùng) -> buildTranslateTM thấy cặp
 *   mới -> xoá 1 mục -> xoá toàn bộ cặp en->de.
 */
"use strict";
const fs = require("fs");
// Dùng gói `ws` (npm i ws): WebSocket global của Node không nhận
// message từ Chrome 131 trong môi trường này (đã đo thật).
let WebSocket;
try { WebSocket = require("ws"); }
catch (e) {
  try { WebSocket = require(process.env.TM_WS_PATH || ""); }
  catch (e2) { WebSocket = globalThis.WebSocket; }
}

const DEBUG_PORT = process.argv[2] || "http://127.0.0.1:9223";
const IMPORT_FILE = process.argv[3] || null;

let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log("  PASS " + name); }
  else { fail++; console.log("  FAIL " + name + (extra ? " — " + extra : "")); }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function getJson(url) {
  const r = await fetch(url);
  return r.json();
}

async function main() {
  const targets = await getJson(DEBUG_PORT + "/json/list");
  const page = targets.find((t) => t.type === "page" && (t.url || "").includes("index.html"));
  if (!page) throw new Error("không tìm thấy page target index.html");
  const ws = new WebSocket(page.webSocketDebuggerUrl, { perMessageDeflate: false });
  await new Promise((res, rej) => { ws.on("open", res); ws.on("error", rej); });
  let id = 0;
  const pending = new Map();
  const dialogQueue = [];
  ws.on("message", (data) => {
    const msg = JSON.parse(String(data));
    if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
    if (msg.method === "Page.javascriptDialogOpening") dialogQueue.push(msg.params);
  });
  function send(method, params) {
    return new Promise((resolve) => {
      const mid = ++id;
      pending.set(mid, resolve);
      ws.send(JSON.stringify({ id: mid, method, params: params || {} }));
    });
  }
  async function evaljs(expression, awaitPromise, timeoutMs) {
    const r = await Promise.race([
      send("Runtime.evaluate", { expression, awaitPromise: !!awaitPromise, returnByValue: true }),
      new Promise((_, rej) => setTimeout(() => rej(new Error("evaljs timeout " + (timeoutMs || 10000) + "ms: " + String(expression).slice(0, 80))), timeoutMs || 10000)),
    ]);
    if (r.result && r.result.exceptionDetails) {
      throw new Error("page JS lỗi: " + JSON.stringify(r.result.exceptionDetails).slice(0, 500));
    }
    return r.result && r.result.result ? r.result.result.value : undefined;
  }
  async function handleDialogs() { /* confirm() đã stub => true trong trang */ }
  // KHÔNG enable Page/Runtime/DOM: Page.enable treo không trả lời
  // trên trang app này (đã đo thật, probe6); Runtime.evaluate vẫn
  // chạy tốt khi chưa enable. Dialog confirm() được stub trong trang
  // ở bước seed (window.confirm = () => true) thay vì xử lý qua
  // Page domain.

  // Đợi app.js + tm.js nạp xong (tối đa 60s).
  let ready = false;
  for (let i = 0; i < 60; i++) {
    try {
      ready = await evaljs("typeof tmBuildExport === 'function' && !!document.getElementById('tm-box') && typeof refreshTmBox === 'function'", false, 5000);
    } catch (e) { ready = false; console.log("  (đợi trang: " + (e && e.message ? e.message.slice(0, 100) : e) + ")"); }
    if (ready) break;
    await sleep(1000);
  }
  check("trang nạp xong: tm.js + hộp tm-box + refreshTmBox", ready === true);

  // Seed 2 job done thật qua TranslateJobStore.
  const seeded = await evaljs(`(async () => {
    window.confirm = () => true; // stub dialog xoá (không có Page domain)
    const store = new TranslateJobStore();
    await store.save({id:'tr-seed-vi', name:'en.json', to:'vi', from:'en', status:'done',
      items:[], results:{}, tm:[['Play','Chơi'],['Quit','Thoát'],['His Winningness','Chiến thắng của']],
      order:[], total:3, tmCount:0, glossaryCount:0, createdAt: 1, updatedAt: 3});
    await store.save({id:'tr-seed-de', name:'en.json', to:'de', from:'en', status:'done',
      items:[], results:{}, tm:[['Play','Spielen']],
      order:[], total:1, tmCount:0, glossaryCount:0, createdAt: 1, updatedAt: 2});
    await refreshTmBox();
    return document.getElementById('tm-pair-select').innerHTML +
      '|||' + document.getElementById('tm-list').textContent +
      '|||' + document.getElementById('tm-info').textContent;
  })()`, true);
  check("seed job: select có 2 cặp (en->vi 3 mục, en->de 1 mục)",
    typeof seeded === "string" && seeded.includes("3 mục") && seeded.includes("1 mục"), String(seeded).slice(0, 300));
  check("seed job: danh sách hiện cặp worker thật",
    typeof seeded === "string" && seeded.includes("His Winningness") && seeded.includes("Chiến thắng của"));

  // Bấm Xuất TM (cặp đang chọn = en->vi).
  await evaljs("document.getElementById('btn-tm-export').click();");
  await sleep(800);
  const exportInfo = await evaljs("document.getElementById('tm-info').textContent");
  check("bấm Xuất TM: báo đã xuất 3 mục ra tm-en-vi.json",
    typeof exportInfo === "string" && exportInfo.includes("Đã xuất TM") && exportInfo.includes("3 mục"), String(exportInfo));

  // Nhập file TM: 2 mới + 1 trùng (Play đã có) + 1 bỏ qua (src==target).
  const importObj = {
    format: "meorocky-tm", version: 1, from: "en", to: "vi",
    exported_at: "2026-11-18T00:00:00.000Z", count: 4,
    entries: [
      { source: "Castle", target: "Lâu đài" },
      { source: "Dragon", target: "Rồng" },
      { source: "Play", target: "Chơi" },
      { source: "Same", target: "Same" },
    ],
  };
  const importText = JSON.stringify(importObj, null, 2);
  await evaljs(`(() => {
    const input = document.getElementById('tm-import-file');
    const dt = new DataTransfer();
    dt.items.add(new File([${JSON.stringify(importText)}], 'tm-import.json', { type: 'application/json' }));
    input.files = dt.files;
    input.dispatchEvent(new Event('change'));
    return input.files.length;
  })()`);
  let importInfo = "";
  for (let i = 0; i < 20; i++) {
    await sleep(500);
    importInfo = await evaljs("document.getElementById('tm-info').textContent");
    if (typeof importInfo === "string" && importInfo.includes("Đã nhập TM")) break;
  }
  check("nhập TM: báo 2 mới, 1 trùng, 1 bỏ qua",
    typeof importInfo === "string" && importInfo.includes("2 mục mới") &&
    importInfo.includes("1 mục trùng") && importInfo.includes("1 mục bỏ qua"), String(importInfo));
  const tmAfterImport = await evaljs(`(async () => {
    const tm = await buildTranslateTM('vi', 'en');
    return JSON.stringify([...tm.entries()].sort());
  })()`, true);
  check("sau nhập: buildTranslateTM thấy Castle/Dragon, giữ Play/Quit",
    typeof tmAfterImport === "string" && tmAfterImport.includes("Lâu đài") &&
    tmAfterImport.includes("Rồng") && tmAfterImport.includes("Chơi"), String(tmAfterImport).slice(0, 300));

  // Xoá 1 mục (nút Xoá của 'Quit') — confirm() tự chấp nhận.
  await evaljs(`(() => {
    const btns = [...document.querySelectorAll('#tm-list [data-tm-del]')];
    const b = btns.find((x) => x.getAttribute('data-tm-del') === 'Quit');
    if (b) b.click();
    return btns.length;
  })()`);
  await sleep(300);
  await handleDialogs();
  await sleep(1500);
  const tmAfterDel = await evaljs(`(async () => {
    const tm = await buildTranslateTM('vi', 'en');
    return JSON.stringify([...tm.keys()].sort());
  })()`, true);
  check("xoá 1 mục: Quit khỏi TM, Castle còn lại",
    typeof tmAfterDel === "string" && !tmAfterDel.includes("Quit") && tmAfterDel.includes("Castle"),
    String(tmAfterDel));

  // Xoá toàn bộ cặp en->de (chọn cặp de rồi bấm Xoá toàn bộ).
  await evaljs(`(async () => {
    const sel = document.getElementById('tm-pair-select');
    sel.value = 'en>de';
    await refreshTmBox();
  })()`, true);
  await sleep(500);
  await evaljs("document.getElementById('btn-tm-clear-pair').click();");
  await sleep(300);
  await handleDialogs();
  await sleep(1500);
  const pairsAfterClear = await evaljs(`(async () => {
    const store = new TranslateJobStore();
    return JSON.stringify(tmPairsByLang(await store.getAll()));
  })()`, true);
  check("xoá toàn bộ cặp en->de: chỉ còn en->vi",
    typeof pairsAfterClear === "string" && !pairsAfterClear.includes('"to":"de"') &&
    pairsAfterClear.includes('"to":"vi"'), String(pairsAfterClear));

  console.log("tm_chromium_check: " + pass + " PASS, " + fail + " FAIL");
  ws.close();
  process.exit(fail ? 1 : 0);
}
main().catch((e) => { console.error("tm_chromium_check ERROR: " + (e && e.stack || e)); process.exit(1); });
