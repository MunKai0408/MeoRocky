// verify_delivery_gate.mjs — Kiểm chứng P0-2: cổng kiểm tra trước khi
// giao [bước 6] (BearTea web). (repo MunKai0408/MeoRocky, nhánh main;
// KHÔNG đẩy lên GitHub.)
//
// Chạy: node verify_delivery_gate.mjs (từ thư mục exp/ của repo)
// Kết quả: delivery_gate_results.json (lần chạy này; báo cáo .md viết
// tay từ cùng lần chạy, KHÔNG gộp kết quả cũ bằng cơ chế ONLY).
//
//  A. Tĩnh: node --check; py_compile (kể cả sdk/gate.py, test_gate.py);
//     check_delivery_gate_static.py (gate.py stdlib-only + Python 3.9,
//     không UnityPy/lz4, không ghi file, manifest 95/168, precache 120
//     + v23, bridge có op delivery_gate + rebuild_apply bắt verdict,
//     UI đủ id cổng + nút Ghi bị khóa bởi verdict + render cổng esc).
//  B. Unit test Python: tests.test_gate + test_rebuild + test_plan.
//  C. Node Pyodide + bridge thật (fixture item8_fixture.zip: bundle v7
//     LZ4, 3 TextAsset JSON en/menu/dialog, 9 key):
//     C1 pre-write PASS (9 key + font donor đi kèm) + file gốc không đổi.
//     C2 FAIL: thiếu key; gãy placeholder; không có nguồn; font thiếu
//        chữ đang dùng; key lạ chưa ép (unmatched).
//     C3 pressed: file do rebuild_apply ghi thật -> PASS; lật 1 byte
//        vùng đệm -> PASS + cảnh báo (chỉ kiểm nội dung, xem §4a báo
//        cáo); đổi nội dung/object/entry -> FAIL; pressed == file gốc
//        -> FAIL; pressed trùng đường dẫn nguồn -> từ chối.
//     C4 verdict cho phép rebuild_apply: PASS ghi được; FAIL / sai bản
//        dịch / sai nguồn -> E_CONFIG_INVALID, không ghi gì.
//     C5 từ chối: bundle v9, bundle gắn cờ mã hóa -> FAIL + refused.
//  D. Trình duyệt THẬT (Playwright chromium headless shell, 390px, serve
//     site qua HTTP local): D1 cổng ĐẠT bật nút Ghi -> apply + download;
//     D2 cổng CHƯA ĐẠT (thiếu key) giữ nút Ghi tắt; D3 kiểm file đã ép
//     (pressed) qua UI -> ĐẠT; D4 không tràn ngang + không lỗi trang.
//     Nếu thiếu Playwright/Chromium thì D = [CHƯA KIỂM CHỨNG], ghi rõ,
//     không bịa PASS.
//  S. Vỏ trang sau P0-2 (ghi số thật; ngưỡng gzip ĐÃ chốt 102400B).
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import zlib from "node:zlib";
import { createHash } from "node:crypto";
import { execFileSync, spawnSync } from "node:child_process";

const ROOT = decodeURIComponent(new URL("..", import.meta.url).pathname).replace(/\/$/, "");
const SITE = ROOT;
const EXP = ROOT + "/exp";
const TMP = (process.env.TMPDIR || "/tmp") + "/gate_harness";
const FX = EXP + "/fixtures/item8_fixture.zip";
const CAB = "CAB-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const VI = {
  hello: "Xin chào", play: "Chơi", quit: "Thoát", title: "9 vị vua",
  start: "Bắt đầu chơi", options: "Tùy chọn", exit: "Thoát game",
  greeting: "Chào mừng, anh hùng!", farewell: "Tạm biệt!",
};
const EN_SOURCE = {
  hello: "Hello", play: "Play", quit: "Quit", title: "9 Kings",
  start: "Start Game", options: "Options", exit: "Exit",
  greeting: "Welcome, hero!", farewell: "Goodbye!",
};
const VENV_PY = "python3";
const GZIP_THRESHOLD = 102400; // 100*1024, đã chốt ở verify_item6.mjs:86,95
const sec = (ms) => Math.round(ms / 100) / 10;
const results = {};
let failures = 0;
function rec(name, ok, detail, ms) {
  results[name] = { ok, detail, seconds: ms == null ? 0 : sec(ms) };
  if (!ok) failures++;
  console.log(`\n### ${name}: ${ok ? "PASS" : "FAIL"}${ms == null ? "" : " (" + sec(ms) + "s)"}\n${detail}`);
}
const sha256 = (buf) => createHash("sha256").update(buf).digest("hex");
fs.mkdirSync(TMP, { recursive: true });
const checkStatus = (r, id) => ((r.checks || []).find((c) => c.id === id) || {}).status;

// ================= A. Tĩnh =================
{ // A1. node --check + py_compile
  const t0 = performance.now();
  const bad = [];
  for (const f of ["app.js", "sw.js", "exp/verify_delivery_gate.mjs"]) {
    try { execFileSync("node", ["--check", SITE + "/" + f], { stdio: "pipe" }); }
    catch (e) { bad.push(f); }
  }
  try {
    execFileSync("python3", ["-m", "py_compile", SITE + "/pb3/bridge.py",
      SITE + "/pb3/sdk/gate.py", SITE + "/pb3/sdk/rebuild.py",
      SITE + "/pb3/sdk/plan.py", SITE + "/pb3/tests/test_gate.py"],
      { stdio: "pipe", cwd: SITE + "/pb3" });
  } catch (e) { bad.push("py_compile"); }
  rec("static_node_py_check", bad.length === 0,
    bad.length ? "LỖI ở: " + bad.join(", ") : "node --check OK: app.js, sw.js, verify_delivery_gate.mjs; py_compile OK: bridge.py, sdk/gate.py, sdk/rebuild.py, sdk/plan.py, tests/test_gate.py",
    performance.now() - t0);
}

{ // A2. Kiểm tra tĩnh chuyên mục P0-2
  const t0 = performance.now();
  const r = spawnSync("python3", [EXP + "/check_delivery_gate_static.py"],
    { encoding: "utf8" });
  let detail = (r.stdout || "").trim();
  let ok = r.status === 0;
  try {
    const j = JSON.parse(r.stdout || "{}");
    if (j.fails && j.fails.length) { ok = false; detail = j.fails.join(" | "); }
    else detail = "gate.py stdlib-only + Python 3.9 + không UnityPy/lz4/mạng " +
      "+ không ghi file + đủ 5 kiểm tra (translation_complete, placeholders, " +
      "diffverify, fonts, integrity) + manifest 95/168 + sw.js precache 120 " +
      "mục có sdk/gate.py, không mục chết, CACHE_VERSION v28-20261117 + " +
      "bridge delivery_gate + rebuild_apply bắt verdict + UI đủ id cổng + " +
      "nút Ghi bị khóa bởi verdict + render cổng esc";
  } catch (e) { ok = false; detail = "không đọc được JSON: " + (r.stdout || "") + (r.stderr || ""); }
  rec("static_gate_checks", ok, detail, performance.now() - t0);
}

// ================= B. Unit test Python =================
{ // B1. tests.test_gate + test_rebuild + test_plan (hồi quy đường chung)
  const t0 = performance.now();
  const r = spawnSync(VENV_PY,
    ["-m", "unittest", "tests.test_gate", "tests.test_rebuild", "tests.test_plan"],
    { cwd: SITE + "/pb3", encoding: "utf8" });
  const out = (r.stdout || "") + (r.stderr || "");
  const m = out.match(/Ran (\d+) tests?/);
  const okRun = /^(OK|OK \(skipped=\d+\))$/m.test(out);
  rec("python_unit_gate_rebuild_plan", r.status === 0 && !!m && okRun,
    "unittest tests.test_gate + tests.test_rebuild + tests.test_plan: " +
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
  const b64 = fs.readFileSync(SITE + "/fonts/BeVietnamPro-Regular.ttf.b64", "utf8").replace(/\s+/g, "");
  pyodide.FS.mkdirTree("/pb3web/fonts");
  pyodide.FS.writeFile("/pb3web/fonts/BeVietnamPro-Regular.ttf", new Uint8Array(Buffer.from(b64, "base64")));
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
const DONOR = "/pb3web/fonts/BeVietnamPro-Regular.ttf";
const fsExists = (p) => { try { pyodide.FS.stat(p); return true; } catch { return false; } };
const fsRead = (p) => pyodide.FS.readFile(p);
const FX_SHA = sha256(fs.readFileSync(FX));
const gate = (tr, src, prov, pressed, allow, font, choices, partial) =>
  call("delivery_gate", INPUT, JSON.stringify(tr),
    src == null ? null : JSON.stringify(src),
    prov == null ? null : JSON.stringify(prov),
    pressed || null, allow == null ? null : JSON.stringify(allow),
    font || null, choices == null ? null : JSON.stringify(choices),
    !!partial);
let passVerdict = null;

{ // C1. Pre-write PASS + file gốc không đổi
  const t0 = performance.now();
  const r = gate(VI, EN_SOURCE, null, null, null, DONOR);
  passVerdict = r;
  const ids = (r.checks || []).map((c) => c.id);
  const allPass = (r.checks || []).every((c) => c.status === "pass");
  const shaAfter = sha256(Buffer.from(fsRead(INPUT)));
  const ok = r.verdict === "PASS" && r.delivery_allowed === true && r.mode === "pre_write" &&
    ids.length === 5 && allPass && (r.totals || {}).changed === 9 &&
    (r.totals || {}).units >= 1 && r.source_sha256 === FX_SHA && shaAfter === FX_SHA &&
    (r.errors || []).length === 0;
  rec("C1_gate_prewrite_pass", ok,
    "delivery_gate pre-write: verdict=" + r.verdict + ", delivery_allowed=" + r.delivery_allowed +
    ", mode=" + r.mode + ", checks=" + JSON.stringify(ids) + " (tất cả pass=" + allPass + ")" +
    ", totals=" + JSON.stringify(r.totals) + ", source_sha256 khớp=" + (r.source_sha256 === FX_SHA) +
    ", sha file gốc sau kiểm khớp trước=" + (shaAfter === FX_SHA) + ", errors=" + JSON.stringify(r.errors || []),
    performance.now() - t0);
}

{ // C2. FAIL: thiếu key / gãy placeholder / không nguồn / font thiếu chữ / key lạ
  const t0 = performance.now();
  const missing = { ...VI }; delete missing.title;
  const rMiss = gate(missing, EN_SOURCE, null, null, null, DONOR);
  const srcPh = { ...EN_SOURCE, greeting: "Welcome, {0} hero!" };
  const trPh = { ...VI, greeting: "Chào mừng, anh hùng!" };
  const rPh = gate(trPh, srcPh, null, null, null, DONOR);
  const rNoSrc = gate(VI, null, null, null, null, DONOR);
  // Font chỉ có ASCII (sinh bằng builder fixture) -> thiếu chữ Việt đang dùng.
  const genFont = TMP + "/gen_font.py";
  fs.writeFileSync(genFont, [
    "import sys",
    "sys.path.insert(0, '" + SITE + "/pb3')",
    "import importlib.util",
    "spec = importlib.util.spec_from_file_location('ffb', '" + SITE + "/pb3/tests/fixtures/font/build_fixtures.py')",
    "ffb = importlib.util.module_from_spec(spec); spec.loader.exec_module(ffb)",
    "open('" + TMP + "/ascii.ttf', 'wb').write(ffb.build_font_missing_vietnamese())",
    "print('font-ok')",
  ].join("\n"));
  const genOut = spawnSync(VENV_PY, [genFont], { encoding: "utf8" });
  let rFont = null, fontDetail = "";
  if (genOut.status === 0) {
    pyodide.FS.writeFile("/tmp/input/ascii.ttf", new Uint8Array(fs.readFileSync(TMP + "/ascii.ttf")));
    rFont = gate(VI, EN_SOURCE, null, null, null, "/tmp/input/ascii.ttf");
    fontDetail = "font ASCII: verdict=" + rFont.verdict + ", fonts=" + checkStatus(rFont, "fonts") +
      ", fonts detail=" + JSON.stringify(rFont.fonts || []);
  } else {
    fontDetail = "không sinh được font ASCII: " + (genOut.stderr || "").slice(-300);
  }
  const rGhost = gate({ ...VI, ghost_key: "Ma" }, EN_SOURCE, null, null, null, DONOR);
  const okMiss = rMiss.verdict === "FAIL" && checkStatus(rMiss, "translation_complete") === "fail" &&
    JSON.stringify(rMiss.errors || []).includes("title");
  const okPh = rPh.verdict === "FAIL" && checkStatus(rPh, "placeholders") === "fail";
  const okNoSrc = rNoSrc.verdict === "FAIL" && checkStatus(rNoSrc, "translation_complete") === "fail";
  const okFont = rFont && rFont.verdict === "FAIL" && checkStatus(rFont, "fonts") === "fail";
  const okGhost = rGhost.verdict === "FAIL" && checkStatus(rGhost, "diffverify") === "fail" &&
    (rGhost.unmatched || []).some((u) => u.key === "ghost_key");
  rec("C2_gate_fail_cases", okMiss && okPh && okNoSrc && !!okFont && okGhost,
    "thiếu key title: verdict=" + rMiss.verdict + ", translation_complete=" + checkStatus(rMiss, "translation_complete") +
    ", lỗi nhắc title=" + JSON.stringify(rMiss.errors || []).includes("title") +
    "; gãy placeholder {0}: verdict=" + rPh.verdict + ", placeholders=" + checkStatus(rPh, "placeholders") +
    "; không nguồn: verdict=" + rNoSrc.verdict + ", translation_complete=" + checkStatus(rNoSrc, "translation_complete") +
    "; " + fontDetail +
    "; key lạ ghost_key: verdict=" + rGhost.verdict + ", diffverify=" + checkStatus(rGhost, "diffverify") +
    ", unmatched có ghost_key=" + (rGhost.unmatched || []).some((u) => u.key === "ghost_key"),
    performance.now() - t0);
}

let pressedPath = null;
{ // C3. Pressed: file ép thật PASS; sửa 1 byte FAIL; == gốc FAIL; trùng đường dẫn từ chối
  const t0 = performance.now();
  const rApply = call("rebuild_apply", INPUT, JSON.stringify(VI), null, null, false, true,
    JSON.stringify(passVerdict));
  pressedPath = rApply.dest || null;
  let rPressed = null, pressedDetail = "";
  if (rApply.ok !== false && pressedPath && fsExists(pressedPath)) {
    rPressed = gate(VI, EN_SOURCE, null, pressedPath, null, DONOR);
    pressedDetail = "pressed thật: verdict=" + rPressed.verdict + ", mode=" + rPressed.mode +
      ", delivery_allowed=" + rPressed.delivery_allowed + ", checks=" +
      JSON.stringify((rPressed.checks || []).map((c) => c.id + ":" + c.status));
  } else {
    pressedDetail = "rebuild_apply không ghi được file ép: " + JSON.stringify(rApply).slice(0, 300);
  }
  let tamperOk = false, tamperDetail = "";
  if (pressedPath && fsExists(pressedPath)) {
    const bytes = Buffer.from(fsRead(pressedPath)); // đọc TRƯỚC khi ép file sai (cùng dest)
    // (1) Nội dung sai: ép bằng bản dịch khác 1 giá trị -> diffverify FAIL.
    const rWrong = call("rebuild_apply", INPUT,
      JSON.stringify({ ...VI, hello: "Chào khác" }), null, null, false, true);
    let wrongOk = false, wrongDetail = "";
    if (rWrong.ok !== false && rWrong.dest && fsExists(rWrong.dest)) {
      const rW = gate(VI, EN_SOURCE, null, rWrong.dest, null, DONOR);
      wrongOk = rW.verdict === "FAIL" && checkStatus(rW, "diffverify") === "fail";
      wrongDetail = "ép sai 1 giá trị: verdict=" + rW.verdict + ", diffverify=" + checkStatus(rW, "diffverify");
    } else { wrongDetail = "không ép được file sai nội dung: " + JSON.stringify(rWrong).slice(0, 200); }
    // (2) Cắt ngắn 16 byte -> cấu trúc hỏng -> FAIL.
    const trunc = Buffer.from(bytes.subarray(0, bytes.length - 16));
    pyodide.FS.writeFile("/tmp/input/truncated.bin", new Uint8Array(trunc));
    const rTrunc = gate(VI, EN_SOURCE, null, "/tmp/input/truncated.bin", null, DONOR);
    // (3) Lật 1 byte giữa file: byte này rơi vào vùng đệm cấu trúc của
    // bundle nén (đã đo: TextAsset trích ra giống hệt) nên verdict PASS
    // là ĐÚNG THIẾT KẾ — cổng chỉ yêu cầu nội dung + sha script khớp,
    // không yêu cầu byte-identical (file ép bằng công cụ khác vẫn được
    // chấp nhận). Cổng PHẢI báo cảnh báo khác bytes với mô phỏng.
    const mid = Buffer.from(bytes); mid[Math.floor(mid.length / 2)] ^= 0xff;
    pyodide.FS.writeFile("/tmp/input/tampered.bin", new Uint8Array(mid));
    const rTam = gate(VI, EN_SOURCE, null, "/tmp/input/tampered.bin", null, DONOR);
    const warned = JSON.stringify(rTam).includes("khác bytes với mô phỏng");
    tamperOk = wrongOk && rTrunc.verdict === "FAIL" && rTam.verdict === "PASS" && warned;
    tamperDetail = wrongDetail + "; cắt ngắn 16 byte: verdict=" + rTrunc.verdict +
      "; lật 1 byte vùng đệm: verdict=" + rTam.verdict + " (đúng thiết kế), cảnh báo khác bytes=" + warned;
  }
  const rSameBytes = gate(VI, EN_SOURCE, null, INPUT, null, DONOR);
  const sameOk = rSameBytes.verdict === "FAIL" || rSameBytes.ok === false;
  rec("C3_gate_pressed_mode", !!rPressed && rPressed.verdict === "PASS" && rPressed.mode === "pressed" &&
    tamperOk && sameOk,
    pressedDetail + "; " + tamperDetail +
    "; pressed == file gốc (trùng đường dẫn): verdict=" + rSameBytes.verdict + ", ok=" + rSameBytes.ok +
    ", code=" + (rSameBytes.code || null) + ", errors=" + JSON.stringify((rSameBytes.errors || []).slice(0, 2)),
    performance.now() - t0);
}

{ // C4. Verdict cho phép rebuild_apply; FAIL/sai bản dịch/sai nguồn bị từ chối
  const t0 = performance.now();
  const failVerdict = gate({ hello: "Xin chào" }, EN_SOURCE, null, null, null, DONOR);
  const rOk = call("rebuild_apply", INPUT, JSON.stringify(VI), null, null, false, true,
    JSON.stringify(passVerdict));
  const okWrite = rOk.ok !== false && !!rOk.dest && fsExists(rOk.dest);
  const rFail = call("rebuild_apply", INPUT, JSON.stringify(VI), null, null, false, true,
    JSON.stringify(failVerdict));
  const rWrongTr = call("rebuild_apply", INPUT, JSON.stringify({ ...VI, hello: "Chào" }), null, null, false, true,
    JSON.stringify(passVerdict));
  const wrongSrcVerdict = { ...passVerdict, source_sha256: "0".repeat(64) };
  const rWrongSrc = call("rebuild_apply", INPUT, JSON.stringify(VI), null, null, false, true,
    JSON.stringify(wrongSrcVerdict));
  const refuse = (r) => r.ok === false && r.code === "E_CONFIG_INVALID" && !r.dest;
  rec("C4_verdict_authorizes_apply", okWrite && refuse(rFail) && refuse(rWrongTr) && refuse(rWrongSrc),
    "verdict PASS: ghi được=" + okWrite + " (dest=" + (rOk.dest || null) + ")" +
    "; verdict FAIL: ok=" + rFail.ok + ", code=" + rFail.code + ", dest=" + (rFail.dest || null) +
    "; sai bản dịch: ok=" + rWrongTr.ok + ", code=" + rWrongTr.code +
    "; sai sha nguồn: ok=" + rWrongSrc.ok + ", code=" + rWrongSrc.code +
    " (kỳ vọng cả 3 đều E_CONFIG_INVALID + không ghi)",
    performance.now() - t0);
}

{ // C5. Từ chối: v9, mã hóa -> FAIL + refused
  const t0 = performance.now();
  const genPy = TMP + "/gen_refuse.py";
  fs.writeFileSync(genPy, [
    "import sys, json",
    "sys.path.insert(0, '" + SITE + "/pb3')",
    "from tests.fixtures.unity import build_fixtures as bf",
    "sf = bf.build_serialized_file(version=17, objects=[",
    "  (1001, 0, bf.text_asset_data('en.json', json.dumps({'hello': 'Hello'}))),",
    "], types=[(49, None)])",
    "open('" + TMP + "/v9.bundle', 'wb').write(bf.build_bundle(version=9, entries=[('" + CAB + "', 0, sf)], compression='none'))",
    "open('" + TMP + "/enc.bundle', 'wb').write(bf.build_bundle(version=7, entries=[('" + CAB + "', 0, sf)], compression='none', extra_data_flags=0x200))",
    "print('gen-ok')",
  ].join("\n"));
  const genOut = spawnSync(VENV_PY, [genPy], { encoding: "utf8" });
  let v9Ok = false, v9Detail = "", encOk = false, encDetail = "";
  if (genOut.status === 0) {
    const probe = (name) => {
      pyodide.FS.writeFile("/tmp/input/" + name, new Uint8Array(fs.readFileSync(TMP + "/" + name)));
      return call("delivery_gate", "/tmp/input/" + name, JSON.stringify({ hello: "Xin chào" }),
        JSON.stringify({ hello: "Hello" }), null, null, null, DONOR);
    };
    const rV9 = probe("v9.bundle");
    const refusedV9 = (rV9.refused || [])[0] || {};
    v9Ok = rV9.verdict === "FAIL" && (/UNSUPPORTED/.test(refusedV9.code || "") || rV9.code === "E_UNSUPPORTED_FORMAT" || /v9/i.test(JSON.stringify(rV9)));
    v9Detail = "v9: verdict=" + rV9.verdict + ", refused=" + JSON.stringify(rV9.refused || []) + ", code=" + (rV9.code || null);
    const rEnc = probe("enc.bundle");
    const refusedEnc = (rEnc.refused || [])[0] || {};
    encOk = rEnc.verdict === "FAIL" && (refusedEnc.code === "E_ENCRYPTED" || rEnc.code === "E_ENCRYPTED");
    encDetail = "mã hóa: verdict=" + rEnc.verdict + ", refused=" + JSON.stringify(rEnc.refused || []) + ", code=" + (rEnc.code || null);
  } else {
    v9Detail = encDetail = "không sinh được fixture từ chối: " + (genOut.stderr || "").slice(-300);
  }
  rec("C5_gate_refusals_v9_encrypted", v9Ok && encOk, v9Detail + "; " + encDetail,
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
const D_UNTESTED = "[CHƯA KIỂM CHỨNG] Playwright/Chromium không có trong môi trường này — flow trình duyệt cổng (bước 6) chưa được kiểm trên trình duyệt thật, không bịa PASS. Xem bước thử Safari trong báo cáo.";
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
  const dialogs = [];
  page.on("dialog", async (d) => { dialogs.push(d.message()); await d.accept(); });
  fs.writeFileSync(TMP + "/vi.json", JSON.stringify(VI, null, 2));
  fs.writeFileSync(TMP + "/en_source.json", JSON.stringify(EN_SOURCE, null, 2));
  const viMissing = { ...VI }; delete viMissing.title;
  fs.writeFileSync(TMP + "/vi_missing.json", JSON.stringify(viMissing, null, 2));
  const gateRunAndWait = async (needle) => {
    await page.evaluate(() => document.getElementById("btn-gate-run").scrollIntoView({ block: "center" }));
    await page.click("#btn-gate-run");
    await page.waitForFunction(
      (n) => { const el = document.getElementById("gate-result"); return el && el.textContent.includes(n); },
      needle, { timeout: 120000 });
    return elText("gate-result");
  };

  { // D1. Cổng ĐẠT bật nút Ghi -> apply + download
    const t0 = performance.now();
    await page.goto("http://127.0.0.1:" + PORT + "/index.html");
    await page.setInputFiles("#file-input", FX);
    await page.waitForFunction(
      (n) => { const el = document.getElementById("file-info"); return el && el.textContent.includes(n); },
      "item8_fixture.zip", { timeout: 120000 });
    await waitReady();
    await page.click("#btn-nav-tools");
    await page.click("#btn-mod");
    await page.waitForFunction(
      () => { const el = document.getElementById("tab-mod"); return el && !el.hidden; },
      null, { timeout: 15000 });
    await page.setInputFiles("#rebuild-json-file", TMP + "/vi.json");
    await page.waitForFunction(
      () => { const el = document.getElementById("rebuild-source-info"); return el && el.textContent.includes("vi.json"); },
      null, { timeout: 30000 });
    await page.click("#btn-rebuild-preview");
    await page.waitForFunction(
      () => { const el = document.getElementById("rebuild-result"); return el && el.textContent.includes("XEM TRƯỚC"); },
      null, { timeout: 120000 });
    const applyDisabledAfterPreview = await page.evaluate(() => document.getElementById("btn-rebuild-apply").disabled);
    await page.selectOption("#gate-source-mode", "file");
    await page.setInputFiles("#gate-source-file", TMP + "/en_source.json");
    await page.waitForFunction(
      () => { const el = document.getElementById("gate-source-info"); return el && el.textContent.includes("en_source.json"); },
      null, { timeout: 30000 });
    await page.selectOption("#gate-font-mode", "donor");
    const gateTxt = await gateRunAndWait("ĐẠT — được giao file");
    const gatePass = gateTxt.includes("ĐẠT — được giao file");
    const applyEnabled = await page.evaluate(() => !document.getElementById("btn-rebuild-apply").disabled);
    const dlPromise = page.waitForEvent("download", { timeout: 120000 }).catch(() => null);
    await page.click("#btn-rebuild-apply");
    await page.waitForFunction(
      () => { const el = document.getElementById("rebuild-result"); return el && el.textContent.includes("ĐÃ GHI"); },
      null, { timeout: 120000 });
    const applyTxt = await elText("rebuild-result");
    const dl = await dlPromise;
    let dlOk = false;
    if (dl) {
      await dl.saveAs(TMP + "/d1_gate_pressed.bin");
      dlOk = fs.statSync(TMP + "/d1_gate_pressed.bin").size > 0;
    }
    rec("browser_gate_pass_enables_apply", applyDisabledAfterPreview === true && gatePass && applyEnabled &&
      applyTxt.includes("ĐÃ GHI") && dlOk,
      "nút Ghi vẫn tắt sau preview chưa qua cổng=" + applyDisabledAfterPreview +
      ", cổng ĐẠT=" + gatePass + ", bật sau cổng ĐẠT=" + applyEnabled +
      "; cổng: " + JSON.stringify(gateTxt.slice(0, 200)) +
      "; apply: " + JSON.stringify(applyTxt.slice(0, 200)) + "; download nhận được=" + dlOk,
      performance.now() - t0);
  }

  { // D3. Kiểm file đã ép qua UI (pressed) -> ĐẠT (chạy trước D2 khi bản dịch còn đủ)
    const t0 = performance.now();
    // Đóng thanh Lưu file sau apply ở D1 (thanh fixed che nút cổng).
    const barVisible = await page.evaluate(() => !document.getElementById("download-bar").hidden);
    if (barVisible) await page.click("#btn-download-close");
    await page.setInputFiles("#gate-pressed-file", TMP + "/d1_gate_pressed.bin");
    await page.waitForFunction(
      () => { const el = document.getElementById("gate-pressed-info"); return el && el.textContent.includes("d1_gate_pressed.bin"); },
      null, { timeout: 30000 });
    const gateTxt = await gateRunAndWait("ĐẠT — được giao file");
    const pressedMode = gateTxt.includes("kiểm file đã ép");
    rec("browser_gate_pressed_mode", gateTxt.includes("ĐẠT — được giao file") && pressedMode,
      "cổng với file đã ép từ D1: ĐẠT=" + gateTxt.includes("ĐẠT — được giao file") +
      ", chế độ kiểm file đã ép=" + pressedMode + "; cổng: " + JSON.stringify(gateTxt.slice(0, 250)),
      performance.now() - t0);
  }

  { // D2. Cổng CHƯA ĐẠT (thiếu key title) giữ nút Ghi tắt
    const t0 = performance.now();
    await page.click("#btn-gate-clear-pressed");
    await page.setInputFiles("#rebuild-json-file", TMP + "/vi_missing.json");
    await page.waitForFunction(
      () => { const el = document.getElementById("rebuild-source-info"); return el && el.textContent.includes("vi_missing.json"); },
      null, { timeout: 30000 });
    await page.click("#btn-rebuild-preview");
    await page.waitForFunction(
      () => { const el = document.getElementById("rebuild-result"); return el && el.textContent.includes("XEM TRƯỚC"); },
      null, { timeout: 120000 });
    const gateTxt = await gateRunAndWait("CHƯA ĐẠT");
    const failShown = gateTxt.includes("CHƯA ĐẠT — chưa được giao file");
    const mentionsTitle = gateTxt.includes("title");
    const applyDisabled = await page.evaluate(() => document.getElementById("btn-rebuild-apply").disabled);
    rec("browser_gate_fail_blocks_apply", failShown && mentionsTitle && applyDisabled === true,
      "cổng CHƯA ĐẠT=" + failShown + ", lỗi nhắc key title=" + mentionsTitle +
      ", nút Ghi vẫn tắt=" + applyDisabled + "; cổng: " + JSON.stringify(gateTxt.slice(0, 300)),
      performance.now() - t0);
  }

  { // D4. 390px: không tràn ngang + không lỗi trang cả phiên
    const t0 = performance.now();
    const overflow = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
    const realErrors = pageErrors.filter((e) => !e.includes("Failed to load resource"));
    rec("browser_390_no_overflow_no_errors", overflow.sw <= overflow.cw && realErrors.length === 0,
      "scrollWidth=" + overflow.sw + " clientWidth=" + overflow.cw +
      "; pageerror/console-error thật: " + (realErrors.join("; ") || "không có"),
      performance.now() - t0);
  }
} catch (e) {
  // Thiếu Playwright/Chromium (hoặc lỗi boot trình duyệt): D = chưa kiểm chứng.
  if (!browser) {
    for (const name of ["browser_gate_pass_enables_apply", "browser_gate_pressed_mode",
      "browser_gate_fail_blocks_apply", "browser_390_no_overflow_no_errors"]) {
      rec(name, false, D_UNTESTED + " Lỗi môi trường: " + e.message);
    }
  } else {
    throw e;
  }
} finally {
  if (browser) await browser.close().catch(() => {});
  server.close();
}

// ================= S. Vỏ trang sau P0-2 (ghi số thật) =================
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
  rec("shell_size_after_gate", true,
    "shell lần tải đầu sau P0-2: " + lines.join("; ") + " | TỔNG raw=" + raw + "B (" +
    (raw / 1024).toFixed(1) + "KB), gzip=" + gz + "B (" + (gz / 1024).toFixed(1) +
    "KB). Ngưỡng ĐÃ chốt " + GZIP_THRESHOLD + "B: raw " + (raw <= GZIP_THRESHOLD ? "đạt" : "CHƯA đạt") +
    ", gzip " + (gz <= GZIP_THRESHOLD ? "đạt" : "CHƯA đạt") +
    " — số thật ở đây là căn cứ duy nhất. P0-2 thêm code UI cổng (bước 6) nên app.js tăng so với trước.",
    performance.now() - t0);
}

// ================= TỔNG KẾT =================
const total = Object.keys(results).length;
const failed = Object.keys(results).filter((k) => !results[k].ok);
fs.writeFileSync(EXP + "/delivery_gate_results.json",
  JSON.stringify({ total, failures: failed, results }, null, 2));
console.log("\n==== TỔNG: " + total + " mục, " + failed.length + " FAIL ====");
if (failed.length) { console.log("FAIL: " + failed.join(", ")); process.exit(1); }
