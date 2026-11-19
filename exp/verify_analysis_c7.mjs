// verify_analysis_c7.mjs — Kiểm chứng MỤC C7: patch pack nhị phân (BearTea web).
// Web chỉ đọc được asset nhị phân nên xuất "patch pack" — manifest JSON +
// script Python áp patch — để user mang sang iSH/desktop áp vào file gốc.
// (repo MunKai0408/MeoRocky, nhánh main; KHÔNG đẩy lên GitHub.)
//
// Chạy: node verify_analysis_c7.mjs (từ thư mục exp/ của repo)
// Kết quả: analysis_c7_results.json (bản này) + analysis_c7_results.md (báo cáo tay).
//
//  A. Tĩnh: node --check; py_compile; check_analysis_c7_static.py
//     (patch_pack.py stdlib-only + Python 3.9, không chuỗi cấm iSH-only,
//     manifest files.json=105 / tests.json=186, sw.js precache +
//     CACHE_VERSION v20, bridge có op patch_pack, UI đủ 2 nút).
//  B. Unit test Python: tests.test_patch_pack (23) + tests.test_plan (19).
//  C. Node Pyodide + bridge thật (fixture item8_fixture.zip: bundle v7 LZ4,
//     3 TextAsset JSON en/menu/dialog):
//     C1 patch_pack(plan) -> zip 3 file, manifest đúng schema tương thích
//        plan JSON (format/op/operation/before_sha/after_sha/preview).
//     C2 patch_pack(mod) sau mod_patch dry-run -> 1 entry en.json.
//     C3 patch_pack từ chối plan có entry op REBUILD_ALL (M4).
//  D. Trình duyệt THẬT (Playwright chromium headless shell, 390px, serve
//     site qua HTTP local): chọn fixture -> boot -> tab Mod ->
//     D1 Lập kế hoạch hiện 3 entry;
//     D2 "Xuất patch pack" từ plan -> tải patch-pack-*.zip, manifest đúng
//        (3 entry, source_sha256 khớp file fixture);
//     D3 Mod thử (dry-run) rồi "Xuất patch pack" từ Mod -> zip 1 entry;
//     D4 Áp THẬT script trong pack D2 bằng Python hệ thống lên bản sao
//        fixture: dry-run mặc định không chạm file gốc, --allow-write mới
//        ghi (text đổi đúng, verify after_sha, backup = gốc), sửa
//        before_sha thì từ chối (exit 2, không ghi gì);
//     D5 390px không tràn ngang, không pageerror.
//  S. Vỏ trang sau C7 (ghi số thật, không đặt ngưỡng mới — FAIL item6 đã biết).
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import zlib from "node:zlib";
import { createHash } from "node:crypto";
import { execSync, execFileSync, spawnSync } from "node:child_process";

const ROOT = decodeURIComponent(new URL("..", import.meta.url).pathname).replace(/\/$/, "");
const SITE = ROOT;
const EXP = ROOT + "/exp";
const FX = EXP + "/fixtures/item8_fixture.zip";
const BUNDLE = "Payload/9Kings.app/Data/Raw/aa/iOS/localization-locales_assets_all.bundle";
const CAB = "CAB-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const VPATH = BUNDLE + "::" + CAB;
const VI = {
  hello: "Xin chào", play: "Chơi", quit: "Thoát",
  start: "Bắt đầu chơi", options: "Tùy chọn", exit: "Thoát game",
  greeting: "Chào mừng, anh hùng!", farewell: "Tạm biệt!",
};
const VENV_PY = "python3";
const sec = (ms) => Math.round(ms / 100) / 10;
const results = {};
let failures = 0;
function rec(name, ok, detail, ms) {
  results[name] = { ok, detail, seconds: ms == null ? undefined : sec(ms) };
  if (!ok) failures++;
  console.log(`\n### ${name}: ${ok ? "PASS" : "FAIL"}${ms == null ? "" : " (" + sec(ms) + "s)"}\n${detail}`);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const sha256 = (buf) => createHash("sha256").update(buf).digest("hex");

// ================= A. Tĩnh =================
{ // A1. node --check + py_compile
  const t0 = performance.now();
  const bad = [];
  for (const f of ["app.js", "sw.js"]) {
    try { execFileSync("node", ["--check", SITE + "/" + f], { stdio: "pipe" }); }
    catch (e) { bad.push(f); }
  }
  try {
    execFileSync("python3", ["-m", "py_compile", SITE + "/pb3/bridge.py",
      SITE + "/pb3/sdk/patch_pack.py", SITE + "/pb3/tests/test_patch_pack.py"],
      { stdio: "pipe", cwd: SITE + "/pb3" });
  } catch (e) { bad.push("py_compile"); }
  rec("static_node_py_check", bad.length === 0,
    "node --check OK: app.js, sw.js; python3 -m py_compile OK: bridge.py, " +
    "sdk/patch_pack.py, tests/test_patch_pack.py" + (bad.length ? " | LỖI: " + bad.join(", ") : ""),
    performance.now() - t0);
}

{ // A2. Kiểm tra tĩnh chuyên mục C7
  const t0 = performance.now();
  const r = spawnSync("python3", [EXP + "/check_analysis_c7_static.py"],
    { encoding: "utf8" });
  let detail = (r.stdout || "").trim();
  let ok = r.status === 0;
  try {
    const j = JSON.parse(r.stdout || "{}");
    if (j.fails && j.fails.length) { ok = false; detail = j.fails.join(" | "); }
    else detail = "patch_pack.py stdlib-only + Python 3.9 + không chuỗi cấm " +
      "iSH-only + script ap parse được (stdlib + core/sdk) + files.json=105 " +
      "+ tests.json=186 + sw.js precache sdk/patch_pack.py + CACHE_VERSION " +
      "v55-20261118 + bridge patch_pack + UI đủ 2 nút xuất pack";
  } catch (e) { ok = false; detail = "không đọc được JSON: " + (r.stdout || "") + (r.stderr || ""); }
  rec("static_c7_checks", ok, detail, performance.now() - t0);
}

// ================= B. Unit test Python =================
{ // B1. tests.test_patch_pack (23) + tests.test_plan (19, hồi quy đường chung)
  const t0 = performance.now();
  const r = spawnSync(VENV_PY,
    ["-m", "unittest", "tests.test_patch_pack", "tests.test_plan"],
    { cwd: SITE + "/pb3", encoding: "utf8" });
  const out = (r.stdout || "") + (r.stderr || "");
  const m = out.match(/Ran (\d+) tests?/);
  const okRun = /OK$/.test(out.trim()) || /\nOK\n?$/.test(out);
  rec("python_unit_patch_pack_plan", r.status === 0 && !!m && okRun,
    "unittest tests.test_patch_pack + tests.test_plan: " +
    (m ? "Ran " + m[1] + " tests, " : "") + (okRun ? "OK" : "KHÔNG OK") +
    " (exit=" + r.status + ")" + (okRun ? "" : "; đuôi: " + out.slice(-800)),
    performance.now() - t0);
}

// ================= C. Node Pyodide + bridge thật =================
const { loadPyodide } = await import("../pyodide/pyodide.mjs");
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
  const bootOk = JSON.parse(pyodide.globals.get("bridge").__boot_result__).ok;
  if (!bootOk) throw new Error("Pyodide bridge boot failed");
  return { pyodide, bridge, call: (fn, ...args) => JSON.parse(bridge[fn](...args)) };
}
const { pyodide, bridge, call } = await bootPyodide();
pyodide.FS.mkdirTree("/tmp/input");
pyodide.FS.writeFile("/tmp/input/item8_fixture.zip", new Uint8Array(fs.readFileSync(FX)));
const INPUT = "/tmp/input/item8_fixture.zip";
const fsExists = (p) => { try { pyodide.FS.stat(p); return true; } catch { return false; } };
const fsRead = (p) => pyodide.FS.readFile(p);
function unpackZip(zipBytes, destDir) {
  fs.mkdirSync(destDir, { recursive: true });
  const tmpZip = destDir + "/_pack.zip";
  fs.writeFileSync(tmpZip, Buffer.from(zipBytes));
  execSync(`python3 -m zipfile -e ${tmpZip} ${destDir}`, { stdio: "pipe" });
  fs.unlinkSync(tmpZip);
  return fs.readdirSync(destDir).filter((n) => !n.startsWith("_"));
}

let plan = null;
{ // C0. Lập plan trước (dùng lại cho C1/C3)
  const t0 = performance.now();
  const r = call("plan_build", INPUT, JSON.stringify(VI), VPATH, "REBUILD_SECTION", false, null);
  plan = r.ok ? r.plan : null;
  rec("C0_plan_build_for_pack", r.ok === true && plan && plan.entries.length === 3,
    "plan_build: " + (r.ok ? "id=" + plan.id + ", entries=" + plan.entries.length : "lỗi: " + JSON.stringify(r)),
    performance.now() - t0);
}

let planPackDir = null;
{ // C1. patch_pack(plan) -> zip 3 file, manifest đúng schema
  const t0 = performance.now();
  const r = call("patch_pack", INPUT, "plan", JSON.stringify(plan));
  let detail = "";
  let ok = false;
  if (r.ok === true && r.zip_path) {
    const names = unpackZip(fsRead(r.zip_path), "/tmp/c7_pyodide_plan_pack");
    planPackDir = "/tmp/c7_pyodide_plan_pack";
    const man = JSON.parse(fs.readFileSync(planPackDir + "/patch_manifest.json", "utf8"));
    const script = fs.readFileSync(planPackDir + "/apply_patch.py", "utf8");
    const readme = fs.readFileSync(planPackDir + "/README.txt", "utf8");
    const hex64 = (s) => typeof s === "string" && /^[0-9a-f]{64}$/.test(s);
    const entriesOk = Array.isArray(man.entries) && man.entries.length === 3 &&
      man.entries.every((e) => e.asset_type === "TextAsset" && e.operation === "replace_text" &&
        e.op === "REBUILD_SECTION" && typeof e.object === "number" &&
        Array.isArray(e.changes) && e.changes.length > 0 &&
        e.preview_before && e.preview_after && hex64(e.before_sha) &&
        hex64(e.after_sha) && e.selected === true);
    ok = r.pack_id === plan.id && r.entries === 3 &&
      man.format === "pb3-patch-pack" && man.pack_version === 1 &&
      man.version === 1 && man.id === plan.id &&
      man.source_file === "item8_fixture.zip" &&
      man.source_sha256 === sha256(fs.readFileSync(FX)) &&
      man.apply_script === "apply_patch.py" && entriesOk &&
      names.includes("patch_manifest.json") && names.includes("apply_patch.py") &&
      names.includes("README.txt") && script.includes("--allow-write") &&
      script.includes("before_sha") && readme.includes("CACH DUNG");
    detail = "pack_id=" + r.pack_id + " (plan id=" + plan.id + "); zip " + r.zip_bytes + "B, file: " +
      names.join(", ") + "; manifest: format=" + man.format + ", source_sha256 khớp fixture=" +
      (man.source_sha256 === sha256(fs.readFileSync(FX))) + ", entries=" + man.entries.length +
      " (đủ trường plan + operation=replace_text): " + entriesOk;
  } else {
    detail = "patch_pack(plan) lỗi: " + JSON.stringify(r);
  }
  rec("C1_patch_pack_plan_zip_schema", ok, detail, performance.now() - t0);
}

{ // C2. patch_pack(mod) sau mod_patch dry-run -> 1 entry en.json
  const t0 = performance.now();
  const edits = JSON.stringify([{ object: 1001, old: "Hello", new: "Xin chào",
                                  entry: VPATH, mode: "REBUILD_SECTION" }]);
  const mod = call("mod_patch", INPUT, edits, false);
  let detail = "mod_patch dry-run: " + JSON.stringify(mod).slice(0, 200);
  let ok = false;
  if (mod.ok === true && mod.dry_run === true && mod.patched === 1) {
    const r = call("patch_pack", INPUT, "mod", edits);
    if (r.ok === true && r.zip_path) {
      const names = unpackZip(fsRead(r.zip_path), "/tmp/c7_pyodide_mod_pack");
      const man = JSON.parse(fs.readFileSync("/tmp/c7_pyodide_mod_pack/patch_manifest.json", "utf8"));
      const e = man.entries[0] || {};
      ok = r.entries === 1 && man.entries.length === 1 &&
        e.asset_type === "TextAsset" && e.operation === "replace_text" &&
        e.op === "REBUILD_SECTION" && e.object === 1001 &&
        e.asset_path.includes("en.json") &&
        e.changes.length === 1 && e.changes[0].before === "Hello" &&
        e.changes[0].after === "Xin chào" &&
        man.source_sha256 === sha256(fs.readFileSync(FX)) &&
        names.includes("apply_patch.py");
      detail = "mod dry-run patched=1, verified=" + (mod.edits || [])[0]?.verified +
        "; pack entries=" + r.entries + ", asset=" + e.asset_path +
        ", op=" + e.op + ", change Hello->Xin chào: " +
        (e.changes && e.changes[0] && e.changes[0].after === "Xin chào");
    } else detail += "; patch_pack(mod) lỗi: " + JSON.stringify(r);
  }
  rec("C2_patch_pack_mod_after_dryrun", ok, detail, performance.now() - t0);
}

{ // C3. patch_pack từ chối plan có entry op REBUILD_ALL (M4)
  const t0 = performance.now();
  const badPlan = JSON.parse(JSON.stringify(plan));
  badPlan.entries[0].op = "REBUILD_ALL";
  const r = call("patch_pack", INPUT, "plan", JSON.stringify(badPlan));
  // Lưu ý: /tmp/patch_pack.zip từ C1 vẫn còn (bridge chỉ ghi đè khi thành
  // công) nên chỉ kiểm tra lỗi trả về, không kiểm tra file.
  rec("C3_patch_pack_refuses_rebuild_all", r.ok === false && /M4/.test(r.error || ""),
    "patch_pack(plan REBUILD_ALL) -> ok=" + r.ok + ", error=" + JSON.stringify(r.error || r),
    performance.now() - t0);
}

// ================= D. Trình duyệt thật 390px =================
const MIME = { ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript",
  ".json": "application/json", ".css": "text/css", ".webmanifest": "application/manifest+json",
  ".wasm": "application/wasm", ".png": "image/png", ".b64": "text/plain", ".cs": "text/plain" };
function makeServer(root) {
  return http.createServer((req, res) => {
    const urlPath = decodeURIComponent(req.url.split("?")[0]);
    const fp = path.join(root, urlPath === "/" ? "index.html" : urlPath);
    if (!fp.startsWith(root)) { res.writeHead(403); res.end(); return; }
    fs.readFile(fp, (err, data) => {
      if (err) { res.writeHead(404); res.end("not found"); return; }
      res.writeHead(200, { "Content-Type": MIME[path.extname(fp)] || "application/octet-stream" });
      res.end(data);
    });
  });
}
const server = makeServer(SITE);
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const PORT = server.address().port;

let browser = null;
const pageErrors = [];
let planPackPath = null;
try {
  const { chromium } = await import("playwright-core");
  const candidates = [
    process.env.HOME + "/.cache/ms-playwright/chromium_headless_shell-1148/chrome-linux/headless_shell",
    process.env.HOME + "/.cache/ms-playwright/chromium_headless_shell-1148/chrome-linux/chrome",
  ];
  const exe = candidates.find((c) => fs.existsSync(c));
  if (!exe) throw new Error("không tìm thấy Chromium headless shell ở ~/.cache/ms-playwright");
  browser = await chromium.launch({ executablePath: exe, args: ["--no-sandbox"] });
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, acceptDownloads: true });
  const page = await ctx.newPage();
  await page.addInitScript(() => {
    try { Object.defineProperty(navigator, "canShare", { value: () => false }); } catch (e) {}
  });
  page.on("pageerror", (e) => pageErrors.push(String(e && e.message ? e.message : e)));
  page.on("console", (m) => { if (m.type() === "error") pageErrors.push("console: " + m.text()); });
  const elText = (id) => page.evaluate((i) => (document.getElementById(i) || {}).textContent || "", id);
  const waitReady = async () => {
    await page.waitForFunction(
      () => { const el = document.getElementById("status"); return el && el.textContent.includes("Sẵn sàng"); },
      null, { timeout: 240000 });
  };

  { // D1. Chọn fixture -> boot -> tab Mod -> Lập kế hoạch hiện 3 entry
    const t0 = performance.now();
    await page.goto("http://127.0.0.1:" + PORT + "/index.html");
    await page.setInputFiles("#file-input", FX);
    await page.waitForFunction(
      (n) => { const el = document.getElementById("file-info"); return el && el.textContent.includes(n); },
      "item8_fixture.zip", { timeout: 120000 });
    await waitReady();
    const status = await elText("status");
    await page.click("#btn-nav-tools");
    await page.click("#btn-mod");
    await page.waitForFunction(
      () => { const el = document.getElementById("tab-mod"); return el && !el.hidden; },
      null, { timeout: 15000 });
    // PR #11 (Mod UI tidy): #plan-translations/#plan-entry + các nút plan nằm trong
    // <details id="mod-advanced"> (mặc định đóng) — mở trước khi fill/bấm, nếu không
    // Playwright fail vì phần tử không hiển thị.
    await page.evaluate(() => { const d = document.getElementById("mod-advanced"); if (d) d.open = true; });
    await page.fill("#plan-translations", JSON.stringify(VI));
    await page.fill("#plan-entry", VPATH);
    await page.click("#btn-plan-build");
    await page.waitForFunction(
      () => { const el = document.getElementById("plan-list"); return el && el.textContent.includes("en.json"); },
      null, { timeout: 120000 });
    const listTxt = await elText("plan-list");
    const boxes = await page.$$eval("#plan-list input[type=checkbox]", (els) => els.length);
    const packBtnDisabled = await page.evaluate(() => document.getElementById("btn-plan-patch-pack").disabled);
    const modPackDisabled = await page.evaluate(() => document.getElementById("btn-mod-patch-pack").disabled);
    rec("browser_plan_build_3_entries", status.includes("Sẵn sàng") && status.includes("20 plugin") &&
      ["en.json", "menu.json", "dialog.json"].every((n) => listTxt.includes(n)) && boxes === 3 &&
      packBtnDisabled === false && modPackDisabled === true,
      "status: " + JSON.stringify(status) + "; plan-list đủ 3 asset, checkbox=" + boxes +
      "; nút Xuất pack (plan) bật=" + !packBtnDisabled + ", nút Xuất pack (Mod) tắt trước khi Mod thử=" + modPackDisabled,
      performance.now() - t0);
  }

  { // D2. "Xuất patch pack" từ plan -> tải zip, manifest đúng
    const t0 = performance.now();
    const dlPromise = page.waitForEvent("download", { timeout: 120000 }).catch(() => null);
    await page.click("#btn-plan-patch-pack");
    await page.waitForFunction(
      () => { const el = document.getElementById("plan-result"); return el && el.textContent.includes("Patch pack"); },
      null, { timeout: 120000 });
    const txt = await elText("plan-result");
    const dl = await dlPromise;
    let dlOk = false, dlDetail = "không bắt được sự kiện download";
    if (dl) {
      const fname = dl.suggestedFilename();
      const savePath = "/tmp/c7_browser_plan_pack.zip";
      await dl.saveAs(savePath);
      planPackPath = savePath;
      try {
        const names = unpackZip(fs.readFileSync(savePath), "/tmp/c7_browser_plan_pack");
        const man = JSON.parse(fs.readFileSync("/tmp/c7_browser_plan_pack/patch_manifest.json", "utf8"));
        dlOk = fname.startsWith("patch-pack-") && fname.endsWith(".zip") &&
          man.format === "pb3-patch-pack" && man.entries.length === 3 &&
          man.entries.every((e) => e.operation === "replace_text" && e.op === "REBUILD_SECTION") &&
          man.source_file === "item8_fixture.zip" &&
          man.source_sha256 === sha256(fs.readFileSync(FX)) &&
          names.includes("apply_patch.py") && names.includes("README.txt");
        dlDetail = "download: " + fname + " (" + fs.statSync(savePath).size + "B); manifest entries=" +
          man.entries.length + ", source_sha256 khớp fixture=" +
          (man.source_sha256 === sha256(fs.readFileSync(FX))) +
          ", file trong zip: " + names.join(", ");
      } catch (e) { dlDetail = "download " + fname + " nhưng không đọc được zip: " + e.message; }
    }
    rec("browser_plan_patch_pack_download", txt.includes("Patch pack") && txt.includes("3 thay đổi") && dlOk,
      "plan-result: " + JSON.stringify(txt.slice(0, 200)) + "; " + dlDetail,
      performance.now() - t0);
  }

  { // D3. Mod thử (dry-run) rồi "Xuất patch pack" từ Mod -> zip 1 entry
    const t0 = performance.now();
    await page.fill("#mod-entry", VPATH);
    await page.fill("#mod-object", "1001");
    await page.selectOption("#mod-mode", "REBUILD_SECTION");
    await page.fill("#mod-old", "Hello");
    await page.fill("#mod-new", "Xin chào");
    await page.click("#btn-mod-dryrun");
    await page.waitForFunction(
      () => { const el = document.getElementById("mod-result"); return el && el.textContent.includes("dry-run"); },
      null, { timeout: 120000 });
    const modTxt = await elText("mod-result");
    const btnEnabled = await page.evaluate(() => !document.getElementById("btn-mod-patch-pack").disabled);
    const dlPromise = page.waitForEvent("download", { timeout: 120000 }).catch(() => null);
    await page.click("#btn-mod-patch-pack");
    await page.waitForFunction(
      () => { const el = document.getElementById("mod-result"); return el && el.textContent.includes("Patch pack"); },
      null, { timeout: 120000 });
    const txt = await elText("mod-result");
    const dl = await dlPromise;
    let dlOk = false, dlDetail = "không bắt được sự kiện download";
    if (dl) {
      const fname = dl.suggestedFilename();
      const savePath = "/tmp/c7_browser_mod_pack.zip";
      await dl.saveAs(savePath);
      try {
        const names = unpackZip(fs.readFileSync(savePath), "/tmp/c7_browser_mod_pack");
        const man = JSON.parse(fs.readFileSync("/tmp/c7_browser_mod_pack/patch_manifest.json", "utf8"));
        const e = man.entries[0] || {};
        dlOk = fname.startsWith("patch-pack-") && fname.endsWith(".zip") &&
          man.entries.length === 1 && e.object === 1001 &&
          e.asset_path.includes("en.json") && e.operation === "replace_text" &&
          e.changes.length === 1 && e.changes[0].before === "Hello" &&
          e.changes[0].after === "Xin chào" &&
          man.source_sha256 === sha256(fs.readFileSync(FX)) &&
          names.includes("apply_patch.py");
        dlDetail = "download: " + fname + " (" + fs.statSync(savePath).size + "B); entries=" +
          man.entries.length + ", asset=" + e.asset_path + ", change Hello->Xin chào: " +
          (e.changes && e.changes[0] && e.changes[0].after === "Xin chào");
      } catch (e) { dlDetail = "download " + fname + " nhưng không đọc được zip: " + e.message; }
    }
    rec("browser_mod_dryrun_patch_pack_download",
      modTxt.includes("dry-run") && btnEnabled && txt.includes("Patch pack") &&
      txt.includes("1 thay đổi") && dlOk,
      "mod dry-run xong, nút Xuất pack bật=" + btnEnabled + "; mod-result: " +
      JSON.stringify(txt.slice(0, 200)) + "; " + dlDetail,
      performance.now() - t0);
  }

  { // D4. Áp THẬT script trong pack D2 bằng Python hệ thống
    const t0 = performance.now();
    const work = "/tmp/c7_apply";
    execSync(`rm -rf ${work} && mkdir -p ${work} && cp ${FX} ${work}/item8_fixture.zip && cp -r /tmp/c7_browser_plan_pack/* ${work}/`,
      { stdio: "pipe" });
    const target = work + "/item8_fixture.zip";
    const shaBefore = sha256(fs.readFileSync(target));
    const run = (args) => spawnSync("python3",
      [work + "/apply_patch.py", target].concat(args),
      { cwd: work, encoding: "utf8",
        env: { ...process.env, PB3_ROOT: SITE + "/pb3" } });
    // 4a. Dry-run mặc định: không chạm file gốc, không có thư mục output.
    const rDry = run([]);
    const shaAfterDry = sha256(fs.readFileSync(target));
    const noOutDry = !fs.existsSync(work + "/item8_fixture.pb3-patched");
    // 4b. --allow-write: ghi bundle đã patch, text đổi đúng, backup = gốc.
    const rWrite = run(["--allow-write"]);
    const outDir = work + "/item8_fixture.pb3-patched";
    const outBundle = outDir + "/localization-locales_assets_all.bundle";
    let contentOk = false, backupOk = false, outDetail = "";
    if (fs.existsSync(outBundle)) {
      const got = JSON.parse(execSync(
        `${VENV_PY} -c "import json,sys; sys.path.insert(0,'${SITE}/pb3'); ` +
        `from sdk import unity as u; from core.limits import Limits; ` +
        `b=u.parse_bundle(open('${outBundle}','rb').read(), u.UnityBackend('NATIVE'), Limits()); ` +
        `sf=u.parse_serialized(b.get_entry('${CAB}'), Limits()); ` +
        `print(json.dumps({n: json.loads(s) for _p,n,s in sf.text_assets()}, ensure_ascii=False))"`,
        { encoding: "utf8" }));
      contentOk = got["en.json"].hello === "Xin chào" && got["en.json"].play === "Chơi" &&
        got["menu.json"].start === "Bắt đầu chơi" &&
        got["dialog.json"].greeting === "Chào mừng, anh hùng!";
      // Backup phải đúng bằng bundle gốc trong fixture zip.
      const origBundle = execSync(
        `python3 -c "import sys,zipfile; sys.stdout.buffer.write(zipfile.ZipFile('${FX}').read('${BUNDLE}'))"`,
        { maxBuffer: 64 * 1024 * 1024 });
      backupOk = fs.existsSync(outDir + "/backup-goc/localization-locales_assets_all.bundle") &&
        Buffer.compare(fs.readFileSync(outDir + "/backup-goc/localization-locales_assets_all.bundle"),
          origBundle) === 0;
      outDetail = "en.hello=" + got["en.json"].hello + ", menu.start=" + got["menu.json"].start +
        ", dialog.greeting=" + got["dialog.json"].greeting + "; backup tồn tại: " + backupOk;
    }
    const shaAfterWrite = sha256(fs.readFileSync(target));
    // 4c. Sửa before_sha trong manifest -> từ chối (exit 2, không ghi gì).
    execSync(`rm -rf ${work}/item8_fixture.pb3-patched`, { stdio: "pipe" });
    execSync(`python3 -c "import json; m=json.load(open('${work}/patch_manifest.json')); ` +
      `m['entries'][0]['before_sha']='0'*64; ` +
      `json.dump(m, open('${work}/patch_manifest.json','w'), ensure_ascii=False, indent=2)"`,
      { stdio: "pipe" });
    const rBad = run(["--allow-write"]);
    const noOutBad = !fs.existsSync(work + "/item8_fixture.pb3-patched");
    const shaAfterBad = sha256(fs.readFileSync(target));
    const ok = rDry.status === 0 && /DRY-RUN/.test(rDry.stdout) &&
      shaAfterDry === shaBefore && noOutDry &&
      rWrite.status === 0 && /DA GHI/.test(rWrite.stdout) && contentOk && backupOk &&
      shaAfterWrite === shaBefore &&
      rBad.status === 2 && /before_sha/.test(rBad.stderr) && noOutBad &&
      shaAfterBad === shaBefore;
    rec("browser_apply_script_real_python", ok,
      "dry-run exit=" + rDry.status + " (DRY-RUN=" + /DRY-RUN/.test(rDry.stdout) +
      ", file gốc không đổi=" + (shaAfterDry === shaBefore) + ", chưa có out=" + noOutDry +
      "); --allow-write exit=" + rWrite.status + " (DA GHI=" + /DA GHI/.test(rWrite.stdout) +
      ", " + outDetail + ", file gốc không đổi=" + (shaAfterWrite === shaBefore) +
      "); before_sha sai -> exit=" + rBad.status + ", stderr có before_sha=" +
      /before_sha/.test(rBad.stderr) + ", không ghi gì=" + noOutBad +
      ", file gốc không đổi=" + (shaAfterBad === shaBefore),
      performance.now() - t0);
  }

  { // D5. 390px: không tràn ngang + không lỗi trang cả phiên
    const t0 = performance.now();
    const overflow = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
    const realErrors = pageErrors.filter((e) => !e.includes("Failed to load resource"));
    rec("browser_390_no_overflow_no_errors", overflow.sw <= overflow.cw && realErrors.length === 0,
      "scrollWidth=" + overflow.sw + " clientWidth=" + overflow.cw +
      "; pageerror/console-error thật: " + (realErrors.join("; ") || "không có"),
      performance.now() - t0);
  }
} finally {
  if (browser) await browser.close().catch(() => {});
  server.close();
}

// ================= S. Vỏ trang sau C7 (ghi số thật) =================
{ // S1. Shell tĩnh (cùng cách đo mục 6: tổng raw + gzip 7 file lần tải đầu)
  const t0 = performance.now();
  const files = ["index.html", "kb-store.js", "worker-pool.js", "stream.js", "opfs.js", "app.js", "gt.js"];
  let raw = 0, gz = 0;
  const lines = [];
  for (const f of files) {
    const buf = fs.readFileSync(SITE + "/" + f);
    const g = zlib.gzipSync(buf, { level: 9 }).length;
    raw += buf.length; gz += g;
    lines.push(f + " raw=" + buf.length + "B gzip=" + g + "B");
  }
  rec("shell_size_after_c7", true,
    "shell lần tải đầu sau C7: " + lines.join("; ") + " | TỔNG raw=" + raw + "B (" +
    (raw / 1024).toFixed(1) + "KB), gzip=" + gz + "B (" + (gz / 1024).toFixed(1) +
    "KB). Ngưỡng raw 100KB vẫn chưa đạt như mục 6 đã ghi (app.js monolith) — C7 không đổi kết luận.",
    performance.now() - t0);
}

// ================= TỔNG KẾT =================
const total = Object.keys(results).length;
const failed = Object.keys(results).filter((k) => !results[k].ok);
fs.writeFileSync(EXP + "/analysis_c7_results.json",
  JSON.stringify({ total, failures: failed, results }, null, 2));
console.log("\n==== TỔNG: " + total + " mục, " + failed.length + " FAIL ====");
if (failed.length) { console.log("FAIL: " + failed.join(", ")); process.exit(1); }
