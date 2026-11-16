// verify_ipa_direct.mjs — Kiểm chứng nhập trực tiếp file .ipa, không cần
// đổi tên thành .zip (Joseph 14:35, repo MunKai0408/MeoRocky).
//
// Chạy: node verify_ipa_direct.mjs (từ /home/hatch/workspace/pb3_web/exp)
// Kết quả: ipa_direct_results.md + ipa_direct_results.json
//
// Trình duyệt THẬT (Playwright chromium headless shell, 390px, serve site
// qua HTTP local):
//  U1. UI mới: nút #btn-import-ipa + dòng #ipa-direct-note + dòng dự phòng
//      #ipa-fallback-note; #file-input và #batch-files KHÔNG có accept.
//  U2. Bấm nút "Nhập file .ipa" mở được khung chọn (filechooser) và nhận
//      file .ipa.
//  U3. Kéo-thả tổng hợp (DataTransfer trong trình duyệt) file .ipa vào
//      #dropzone nhận file; file đuôi lạ (.exe) vẫn bị chặn bằng JS.
//  U4. Batch: #batch-files nhận file .ipa.
//  E1. Đầu-cuối với fixture (cùng bytes, khác đuôi): list → inspect →
//      extract trên .ipa phải giống hệt trên .zip (entry list, kết quả
//      inspect, từng member của extract.zip so bằng sha256).
//  E2. IPA thật nhỏ (nested.ipa từ cây iSH, chỉ đọc — copy ra fixtures):
//      list trên .ipa giống hệt trên .zip cùng bytes.
//  E3. 390px: không tràn ngang, không lỗi trang.
// KHÔNG thử được Safari thật (không có BrowserStack/LambdaTest sẵn — xem
// báo cáo): dòng dự phòng đổi tên thành .zip GIỮ LẠI trong UI.
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { execSync } from "node:child_process";

const SITE = "/home/hatch/workspace/pb3_web/site";
const EXP = "/home/hatch/workspace/pb3_web/exp";
const OUT = EXP + "/out/ipa_direct";
const FX_ZIP = EXP + "/fixtures/extract_select_fixture.zip";
const FX_IPA = EXP + "/fixtures/extract_select_fixture.ipa";
const NESTED_IPA = EXP + "/fixtures/nested.ipa";
const NESTED_ZIP = EXP + "/fixtures/nested.zip";
const BUNDLE = "Payload/9Kings.app/Data/Raw/aa/iOS/localization-locales_assets_all.bundle";
const sec = (ms) => Math.round(ms / 100) / 10;
const results = {};
let failures = 0;
function rec(name, ok, detail, ms) {
  results[name] = { ok, detail, seconds: ms == null ? undefined : sec(ms) };
  if (!ok) failures++;
  console.log(`\n### ${name}: ${ok ? "PASS" : "FAIL"}${ms == null ? "" : " (" + sec(ms) + "s)"}\n${detail}`);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// sha256 từng member của 1 file zip (so sánh nội dung, không so bytes vỏ
// zip vì metadata/timestamp có thể khác dù nội dung giống hệt).
// CHUẨN HOÁ: report ghi đúng tên file nguồn (/tmp/input/*.ipa vs *.zip)
// nên 2 report khác nhau đúng 1 dòng tên file — đó là hành vi ĐÚNG, không
// phải lệch kết quả: chuẩn hoá tên file nguồn trước khi hash.
function zipMembers(fp) {
  const out = execSync(
    "python3 " + JSON.stringify(EXP + "/zip_members.py") + " " + JSON.stringify(fp),
    { encoding: "utf8" });
  return JSON.parse(out);
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
  const waitReady = async () => {
    // Mục 6 (lazy boot): boot chỉ chạy khi chọn file — chọn 1 file nhỏ
    // trước rồi mới chờ "Sẵn sàng". Agent anh em có thể đang ghi đè
    // stream.js/app.js giữa chừng (file phục vụ dở -> boot kẹt): thử tải
    // lại trang 1 lần nếu boot quá lâu (chọn lại file sau khi tải lại).
    try {
      await page.setInputFiles("#file-input", FX_ZIP);
      await page.waitForFunction(
        () => { const el = document.getElementById("status"); return el && el.textContent.includes("Sẵn sàng"); },
        null, { timeout: 120000 });
    } catch (e) {
      await page.goto("http://127.0.0.1:" + PORT + "/index.html");
      await page.setInputFiles("#file-input", FX_ZIP);
      await page.waitForFunction(
        () => { const el = document.getElementById("status"); return el && el.textContent.includes("Sẵn sàng"); },
        null, { timeout: 240000 });
    }
  };
  const elText = (id) => page.evaluate((i) => (document.getElementById(i) || {}).textContent || "", id);
  const elAttr = (id, attr) => page.evaluate(([i, a]) => { const el = document.getElementById(i); return el ? el.getAttribute(a) : null; }, [id, attr]);

  // Chạy 1 file (ipa hoặc zip) qua list → inspect → extract, trả kết quả
  // thô để so sánh. `viaButton` = chọn bằng nút "Nhập file .ipa" mới.
  async function runFile(fp, viaButton, dlName) {
    await page.goto("http://127.0.0.1:" + PORT + "/index.html");
    await waitReady();
    const base = path.basename(fp);
    if (viaButton) {
      const [fc] = await Promise.all([
        page.waitForEvent("filechooser", { timeout: 30000 }),
        page.click("#btn-import-ipa"),
      ]);
      await fc.setFiles(fp);
    } else {
      await page.setInputFiles("#file-input", fp);
    }
    await page.waitForFunction(
      (n) => { const el = document.getElementById("file-info"); return el && el.textContent.includes(n); },
      base, { timeout: 30000 });
    // List
    await page.click("#btn-nav-tools");
    await page.click("#btn-list-run");
    await page.waitForFunction(
      (b) => { const el = document.getElementById("list-result"); return el && el.textContent.includes(b); },
      BUNDLE, { timeout: 120000 });
    const listText = await elText("list-result");
    const listRows = await page.evaluate(() => {
      const rows = [...document.querySelectorAll("#list-result table tr")].slice(1);
      return rows.map((r) => [...r.querySelectorAll("td")].slice(0, 3).map((c) => c.textContent.trim()).join("|")).sort();
    });
    // Inspect: chọn bundle/.assets ĐẦU TIÊN trong select của unity_inspect
    // (danh sách bundle của tab Unity chỉ gồm .unity3d/.assets — file
    // .bundle localization KHÔNG nằm trong select này; cả 2 lượt .ipa/.zip
    // đều chọn cùng option đầu nên so sánh vẫn công bằng).
    await page.click("#btn-unity");
    await page.click("#btn-unity-run");
    await page.waitForFunction(
      () => { const s = document.getElementById("unity-bundle-select"); return s && s.options.length > 0; },
      null, { timeout: 120000 });
    const inspectedEntry = await page.evaluate(() => {
      const s = document.getElementById("unity-bundle-select");
      return { options: [...s.options].map((o) => o.value), first: s.options[0].value };
    });
    await page.selectOption("#unity-bundle-select", inspectedEntry.first);
    await page.click("#btn-unity-inspect-entry");
    await page.waitForFunction(
      () => { const el = document.getElementById("unity-result"); return el && /object/.test(el.textContent) && /TextAsset/.test(el.textContent); },
      null, { timeout: 120000 });
    const inspectText = await elText("unity-result");
    // Extract
    await page.click("#btn-extract");
    await page.waitForFunction(
      (b) => { const el = document.getElementById("extract-file-list"); return el && el.textContent.includes(b); },
      BUNDLE, { timeout: 60000 });
    const dlPromise = page.waitForEvent("download", { timeout: 120000 });
    await page.click("#btn-extract-run");
    const dl = await dlPromise;
    await page.waitForFunction(
      () => { const el = document.getElementById("extract-result"); return el && el.textContent.includes("English.json"); },
      null, { timeout: 120000 });
    const extractText = await elText("extract-result");
    const dlPath = OUT + "/" + dlName;
    await dl.saveAs(dlPath);
    return { listText, listRows, inspectText, inspectedEntry: inspectedEntry.first, inspectOptions: inspectedEntry.options, extractText, dlName: dl.suggestedFilename(), members: zipMembers(dlPath) };
  }

  { // U1. UI mới + input không accept
    const t0 = performance.now();
    await page.goto("http://127.0.0.1:" + PORT + "/index.html");
    await waitReady();
    const btnTxt = await elText("btn-import-ipa");
    const noteTxt = await elText("ipa-direct-note");
    const fbTxt = await elText("ipa-fallback-note");
    const accMain = await elAttr("file-input", "accept");
    const accBatch = await elAttr("batch-files", "accept");
    const btnVisible = await page.evaluate(() => { const b = document.getElementById("btn-import-ipa"); return !!b && b.offsetParent !== null; });
    rec("ui_ipa_button_and_notes", btnVisible && btnTxt.includes("Nhập file .ipa") &&
      noteTxt.includes("Chọn trực tiếp file .ipa, không cần đổi tên thành .zip") &&
      fbTxt.includes("Nếu iPhone không cho chọn file .ipa") && fbTxt.includes("đổi tên một bản sao thành .zip") &&
      accMain === null && accBatch === null,
      `nút hiện: ${btnVisible} ("${btnTxt.trim()}"); dòng trực tiếp: ${JSON.stringify(noteTxt.trim().slice(0, 80))}…; ` +
      `dòng dự phòng: ${JSON.stringify(fbTxt.trim())}; accept #file-input=${accMain}, #batch-files=${accBatch} (cần null = không giới hạn đuôi)`,
      performance.now() - t0);
  }

  let runIpa = null, runZip = null;
  { // E1a. .ipa qua nút mới: list → inspect → extract
    const t0 = performance.now();
    runIpa = await runFile(FX_IPA, true, "extract_ipa.zip");
    rec("e2e_ipa_via_new_button", runIpa.listRows.length === 5 && runIpa.dlName === "extract.zip" &&
      runIpa.extractText.includes("English.json") && runIpa.extractText.includes("Chinese.json"),
      `list ${runIpa.listRows.length} entry (cần 5, gồm bundle localization); ` +
      `inspect entry đã chọn: ${runIpa.inspectedEntry} (options: ${JSON.stringify(runIpa.inspectOptions)}); ` +
      `inspect: ${JSON.stringify(runIpa.inspectText.slice(0, 120))}…; ` +
      `extract download=${runIpa.dlName}, members=${JSON.stringify(runIpa.members.map((m) => m[0]))}`,
      performance.now() - t0);
  }
  { // E1b. .zip cùng bytes qua input cũ: list → inspect → extract
    const t0 = performance.now();
    runZip = await runFile(FX_ZIP, false, "extract_zip.zip");
    rec("e2e_zip_same_bytes", runZip.listRows.length === 5 && runZip.dlName === "extract.zip" &&
      runZip.extractText.includes("English.json"),
      `list ${runZip.listRows.length} entry; extract download=${runZip.dlName}, members=${JSON.stringify(runZip.members.map((m) => m[0]))}`,
      performance.now() - t0);
  }
  { // E1c. So sánh .ipa vs .zip: phải giống hệt
    const t0 = performance.now();
    const listSame = JSON.stringify(runIpa.listRows) === JSON.stringify(runZip.listRows);
    const inspectSame = runIpa.inspectText === runZip.inspectText;
    const membersSame = JSON.stringify(runIpa.members) === JSON.stringify(runZip.members);
    // extract-result có thể khác tên file đã chọn (ipa vs zip) trong 1-2
    // chỗ hiển thị tên file: so sánh sau khi chuẩn hoá tên file. Tương tự,
    // report trong extract.zip ghi đúng tên file nguồn nên đã chuẩn hoá
    // tên đó trước khi hash (xem zipMembers) — nội dung trích xuất thật
    // (English/Chinese.json, checkpoint) phải giống từng byte.
    const norm = (s) => s.replace(/extract_select_fixture\.(ipa|zip)/g, "FIXTURE");
    const extractSame = norm(runIpa.extractText) === norm(runZip.extractText);
    rec("ipa_vs_zip_identical", listSame && inspectSame && membersSame && extractSame,
      `list giống hệt: ${listSame} (${runIpa.listRows.length} dòng); inspect giống hệt: ${inspectSame}; ` +
      `extract.zip members+sha256 giống hệt: ${membersSame} (${runIpa.members.length} member); ` +
      `văn bản kết quả extract giống hệt (sau chuẩn hoá tên file): ${extractSame}` +
      (inspectSame ? "" : `\n  inspect .ipa: ${JSON.stringify(runIpa.inspectText.slice(0, 200))}\n  inspect .zip: ${JSON.stringify(runZip.inspectText.slice(0, 200))}`) +
      (extractSame ? "" : `\n  extract .ipa: ${JSON.stringify(runIpa.extractText.slice(0, 300))}\n  extract .zip: ${JSON.stringify(runZip.extractText.slice(0, 300))}`),
      performance.now() - t0);
  }
  { // U3. Kéo-thả tổng hợp file .ipa vào dropzone + đuôi lạ bị chặn
    const t0 = performance.now();
    await page.goto("http://127.0.0.1:" + PORT + "/index.html");
    await waitReady();
    const ipaBytes = [...fs.readFileSync(FX_IPA)];
    const dropOk = await page.evaluate(async (bytes) => {
      const dt = new DataTransfer();
      dt.items.add(new File([new Uint8Array(bytes)], "extract_select_fixture.ipa", { type: "application/octet-stream" }));
      const ev = new DragEvent("drop", { bubbles: true, cancelable: true });
      Object.defineProperty(ev, "dataTransfer", { value: dt });
      document.getElementById("dropzone").dispatchEvent(ev);
      return true;
    }, ipaBytes);
    await page.waitForFunction(
      () => { const el = document.getElementById("file-info"); return el && el.textContent.includes("extract_select_fixture.ipa"); },
      null, { timeout: 30000 });
    // Đuôi lạ: drop file .exe -> báo lỗi, không nhận file.
    const exeBytes = [0x4d, 0x5a, 0x00, 0x01];
    await page.evaluate(async (bytes) => {
      const dt = new DataTransfer();
      dt.items.add(new File([new Uint8Array(bytes)], "malware.exe", { type: "application/octet-stream" }));
      const ev = new DragEvent("drop", { bubbles: true, cancelable: true });
      Object.defineProperty(ev, "dataTransfer", { value: dt });
      document.getElementById("dropzone").dispatchEvent(ev);
      return true;
    }, exeBytes);
    await sleep(500);
    const errVisible = await page.evaluate(() => !(document.getElementById("error") || {}).hidden);
    const errTxt = await elText("error");
    const infoStillIpa = (await elText("file-info")).includes("extract_select_fixture.ipa");
    rec("dragdrop_ipa_accepted_exe_rejected", dropOk && errVisible && /không phải định dạng PB3 hỗ trợ/.test(errTxt) && infoStillIpa,
      `drop .ipa vào dropzone nhận file (file-info hiện tên .ipa): ${dropOk}; ` +
      `drop .exe -> lỗi hiện: ${errVisible} ${JSON.stringify(errTxt.slice(0, 100))}; file đang chọn vẫn là .ipa: ${infoStillIpa} ` +
      `(kéo-thả tổng hợp bằng DataTransfer trong Chromium — không phải kéo-thả OS từ app Files trên iOS, phần đó cần Joseph thử)`,
      performance.now() - t0);
  }
  { // U4. Batch nhận file .ipa
    const t0 = performance.now();
    await page.goto("http://127.0.0.1:" + PORT + "/index.html");
    await waitReady();
    await page.click("#btn-card-batch");
    await page.setInputFiles("#batch-files", FX_IPA);
    await page.waitForFunction(
      () => { const el = document.getElementById("batch-files-info"); return el && el.textContent.includes("extract_select_fixture.ipa"); },
      null, { timeout: 30000 });
    const batchTxt = await elText("batch-files-info");
    rec("batch_ipa_accepted", batchTxt.includes("extract_select_fixture.ipa"),
      `batch-files-info: ${JSON.stringify(batchTxt.slice(0, 120))} (#batch-files không accept nên picker batch cũng nhận .ipa)`,
      performance.now() - t0);
  }
  { // E2. nested.ipa thật (copy từ cây iSH): list .ipa vs .zip cùng bytes
    const t0 = performance.now();
    async function listOnly(fp) {
      await page.goto("http://127.0.0.1:" + PORT + "/index.html");
      await waitReady();
      await page.setInputFiles("#file-input", fp);
      await page.waitForFunction(
        (n) => { const el = document.getElementById("file-info"); return el && el.textContent.includes(n); },
        path.basename(fp), { timeout: 30000 });
      await page.click("#btn-nav-tools");
      await page.click("#btn-list-run");
      await page.waitForFunction(
        () => { const el = document.getElementById("list-result"); return el && el.textContent.includes("Payload/Game.app/Data/level.unity3d"); },
        null, { timeout: 120000 });
      return page.evaluate(() => {
        const rows = [...document.querySelectorAll("#list-result table tr")].slice(1);
        return rows.map((r) => [...r.querySelectorAll("td")].slice(0, 3).map((c) => c.textContent.trim()).join("|")).sort();
      });
    }
    const rowsIpa = await listOnly(NESTED_IPA);
    const rowsZip = await listOnly(NESTED_ZIP);
    rec("nested_ipa_list_matches_zip", JSON.stringify(rowsIpa) === JSON.stringify(rowsZip) && rowsIpa.length === 3,
      `.ipa ${rowsIpa.length} entry, .zip ${rowsZip.length} entry, giống hệt: ${JSON.stringify(rowsIpa) === JSON.stringify(rowsZip)} — ${JSON.stringify(rowsIpa)}`,
      performance.now() - t0);
  }
  { // E3. 390px: không tràn ngang, không lỗi trang
    const t0 = performance.now();
    const ov = await page.evaluate(() => ({
      sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth,
    }));
    rec("browser_390_no_overflow_no_errors", ov.sw <= ov.cw + 1 && pageErrors.length === 0,
      `390px: scrollWidth=${ov.sw} clientWidth=${ov.cw} (không tràn); pageerror: ${pageErrors.length ? JSON.stringify(pageErrors) : "không có"}`,
      performance.now() - t0);
  }
  await browser.close();
  browser = null;
} catch (e) {
  if (browser) { try { await browser.close(); } catch (_) {} }
  rec("browser_end_to_end", false, "LỖI trình duyệt thật: " + String(e && e.message ? e.message : e), undefined);
}
server.close();

const passed = Object.values(results).filter((v) => v.ok).length;
const lines = ["# Kiểm chứng nhập trực tiếp file .ipa (không cần đổi tên thành .zip)",
  "",
  "Ngày: 2026-11-16. Harness: exp/verify_ipa_direct.mjs.",
  "Trình duyệt thật (Playwright chromium headless shell, 390px, serve site qua HTTP local).",
  "Fixture: exp/fixtures/extract_select_fixture.zip copy thành .ipa (cùng bytes, khác đuôi) + nested.ipa (copy từ cây iSH, chỉ đọc).",
  "",
  "## Kết quả",
  ...Object.entries(results).map(([k, v]) => `- ${v.ok ? "PASS" : "FAIL"} **${k}**${v.seconds != null ? " (" + v.seconds + "s)" : ""} — ${v.detail}`),
  "",
  `Tổng: ${Object.keys(results).length} mục, ${passed} PASS, ${failures} FAIL.`,
  "",
  "## File thay đổi (CHƯA ĐẨY — coordinator đẩy sau khi kiểm chứng)",
  "- `site/index.html`: thêm nút #btn-import-ipa (inline onclick mở #file-input — không sửa app.js), dòng #ipa-direct-note, dòng dự phòng #ipa-fallback-note.",
  "- `site/app.js`: KHÔNG SỬA (không có chỗ nào chặn .ipa — xem dưới).",
  "- Fixture mới: `exp/fixtures/extract_select_fixture.ipa` (copy y bytes từ .zip), `exp/fixtures/nested.ipa` + `nested.zip` (copy từ cây iSH, chỉ đọc).",
  "- Harness: `exp/verify_ipa_direct.mjs` (+ kết quả này, `exp/out/ipa_direct/extract_ipa.zip`, `extract_zip.zip`).",
  "",
  "## Rà soát handler drop/change (app.js, chỉ đọc)",
  "- `GAME_EXTS` (app.js:72) đã gồm `.ipa`; `gameExtOk` kiểm tra đuôi không phân biệt hoa/thường.",
  "- `#file-input` change (app.js:742-750): chỉ chặn đuôi KHÔNG trong GAME_EXTS — .ipa đi qua, rồi `handleChosenFile` chỉ kiểm tra kích thước >2GB / cảnh báo >300MB (mục 5: >600MB cảnh báo, >800MB khuyên dùng iSH, vẫn cho tiếp tục).",
  "- `#dropzone` drop (app.js:762-768): cùng check `gameExtOk`, .ipa đi qua.",
  "- `#batch-files` change + `#batch-dropzone` drop (app.js:1909-1948): cùng check, .ipa đi qua; batch cũng không có accept.",
  "- Kết luận: KHÔNG có đoạn nào chặn đuôi .ipa — không cần sửa app.js, không đụng logic streaming của agent anh em.",
  "- `accept` còn lại trong index.html: chỉ ở input JSON dịch (`#translate-json-file`, accept=\".json\") và import KB (`#kb-import-file`, accept sqlite) — không liên quan, ĐỂ YÊN.",
  "",
  "## Safari thật — chưa thử được",
  "- Không có quyền truy cập BrowserStack/LambdaTest sẵn có trong workspace (grep không thấy cấu hình/tài khoản nào); KHÔNG tự đăng ký tài khoản hay trả phí theo brief.",
  "- Vì vậy dòng dự phòng đổi tên thành .zip GIỮ LẠI ngay trong UI dưới khung chọn (#ipa-fallback-note), không chỉ trong sách hướng dẫn.",
  "- CẦN JOSEPH (Safari thật, iPhone): mở trang, bấm nút \"Nhập file .ipa\", xem picker có cho chọn file .ipa không (có bị làm mờ không); thử kéo-thả file .ipa từ app Files vào khung; nếu picker vẫn làm mờ thì dùng cách dự phòng (đổi tên bản sao thành .zip).",
  "",
  "## Không cập nhật sách PDF",
  "- Theo đính chính của Joseph 14:36: KHÔNG cần cập nhật sách hướng dẫn PDF nữa — sách giữ nguyên như hiện tại, không có nợ mục này.",
  "",
  "## Còn chờ Joseph kiểm chứng trên Safari thật",
  "- Picker iOS có cho chọn file .ipa trực tiếp không (nút mới + khung chọn không accept).",
  "- Kéo-thả file .ipa từ app Files vào khung (harness chỉ thử được drop tổng hợp bằng DataTransfer trong Chromium).",
  "- Nút \"Lưu / Chia sẻ\" và Google Translate trên Safari thật (ngoài phạm vi đợt này, đã ghi ở các báo cáo trước).",
];
fs.writeFileSync(EXP + "/ipa_direct_results.md", lines.join("\n") + "\n");
fs.writeFileSync(EXP + "/ipa_direct_results.json", JSON.stringify(
  { when: new Date().toISOString(), failures, results }, null, 2));
console.log("\n==== TỔNG: " + Object.keys(results).length + " mục, " + failures + " FAIL ====");
process.exit(failures ? 1 : 0);
