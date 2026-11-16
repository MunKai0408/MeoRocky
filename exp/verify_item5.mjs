// verify_item5.mjs — Kiểm chứng MỤC 5: streaming reader + copy-through +
// DecompressionStream native + ghi output streaming + cảnh báo >600/800MB
// (repo MunKai0408/MeoRocky, KHÔNG đẩy lên GitHub).
//
// Chạy: node verify_item5.mjs (từ /home/hatch/workspace/pb3_web/exp)
// Kết quả: item5_results.md + item5_results.json
//
//  A. Unit stream.js trong Node vm: sizeWarning (ngưỡng 600/800MB/2GB),
//     listZipEntries bằng EOCD (khớp tên/cỡ/method/kind kỳ vọng), nested
//     -> fallback, ZIP64, không phải ZIP, stored copy-through +
//     deflate qua DecompressionStream (byte-identical với Python zipfile),
//     method không hỗ trợ -> E_UNSUPPORTED_METHOD, gzip member
//     (kể cả nhiều member nối nhau), output nhiều Blob part, đo heap/time.
//  B. Parity với bridge thật trên Pyodide Node: list_entries trên
//     ambig.zip, bigtest.zip (4000 entry), item5_flat.zip (thư mục, unity,
//     excluded) khớp từng entry (path/size/kind); game1.zip có container
//     lồng -> JS báo fallback (không liệt kê sai).
//  C. Kiểm tra tĩnh: node --check, không COI/SAB/pthreads, stream.js nối
//     vào index.html trước app.js, worker.js/bridge.py không thêm op mới.
//  D. Trình duyệt THẬT (Playwright chromium headless shell, 390px, serve
//     site qua HTTP local): boot tới Sẵn sàng, không lỗi console, không
//     tràn ngang; list tab liệt kê nhanh bằng EOCD; tải entry stored +
//     deflate (byte-identical); tải bản giải nén gzip; cảnh báo >600MB
//     hiện đúng + vẫn tiếp tục, >800MB khuyên iSH, >2GB từ chối.
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import vm from "node:vm";
import zlib from "node:zlib";
import { execFileSync, execSync } from "node:child_process";
import { loadPyodide } from "../site/pyodide/pyodide.mjs";

const SITE = "/home/hatch/workspace/pb3_web/site";
const EXP = "/home/hatch/workspace/pb3_web/exp";
const PB3 = "/home/hatch/workspace/pb3_fix/pb3";
const GAME1 = PB3 + "/tests/fixtures/extract/game1.zip";
const AMBIG = PB3 + "/tests/fixtures/extract/ambig.zip";
const BIG = EXP + "/fixtures/bigtest.zip";
const FLAT = EXP + "/fixtures/item5_flat.zip";
const GZ = EXP + "/fixtures/item5_gzip.gz";
const sec = (ms) => Math.round(ms / 100) / 10;
const results = {};
let failures = 0;
function rec(name, ok, detail, ms) {
  results[name] = { ok, detail, seconds: ms == null ? undefined : sec(ms) };
  if (!ok) failures++;
  console.log(`\n### ${name}: ${ok ? "PASS" : "FAIL"}${ms == null ? "" : " (" + sec(ms) + "s)"}\n${detail}`);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---- Fixture: ZIP phẳng (stored + deflate + bzip2 + thư mục + unity + excluded) ----
const FLAT_EXPECT = {}; // name -> {size, method, kind, bytes}
if (!fs.existsSync(FLAT) || !fs.existsSync(GZ)) {
  const py = `
import zipfile, gzip, json
stored = ("Xin chào BearTea! " * 200).encode("utf-8")
en = json.dumps({"hello": "Hello", "play": "Play"}, ensure_ascii=False).encode("utf-8")
with zipfile.ZipFile("${FLAT}", "w") as z:
    z.writestr(zipfile.ZipInfo("data/"), b"")
    z.writestr("data/big-stored.txt", stored, compress_type=zipfile.ZIP_STORED)
    z.writestr("data/en.json", en, compress_type=zipfile.ZIP_DEFLATED)
    z.writestr("data/sharedassets0.assets", b"UnityFS" + b"\\x00" * 100, compress_type=zipfile.ZIP_DEFLATED)
    z.writestr("data/font.ttf", b"\\x00\\x01\\x00\\x00" + b"\\x00" * 50, compress_type=zipfile.ZIP_DEFLATED)
    z.writestr("logs/debug.log", b"log line\\n", compress_type=zipfile.ZIP_DEFLATED)
    z.writestr("test/x.txt", b"test data", compress_type=zipfile.ZIP_DEFLATED)
    z.writestr("data/bz2.bin", b"bzip2 content " * 50, compress_type=zipfile.ZIP_BZIP2)
raw = ("Du lieu gzip tieng Viet co dau: Xin chào! " * 300).encode("utf-8")
with open("${GZ}", "wb") as f:
    # CHỈ 1 member: DecompressionStream('gzip') của Node 24 ném
    # ERR_TRAILING_JUNK_AFTER_STREAM_END với gzip nhiều member nối nhau
    # (đã kiểm chứng); Chromium chưa kết luận được (probe treo) nên
    # đường nhanh chỉ cam kết 1 member — xem exp/item5_results.md.
    f.write(gzip.compress(raw))
open("${EXP}/fixtures/item5_expected.json", "w").write(json.dumps({
  "stored": stored.decode("utf-8"), "en": en.decode("utf-8"), "gzip": raw.decode("utf-8")}))
print("fixtures built")
`;
  fs.writeFileSync("/tmp/build_item5_fixtures.py", py);
  execSync("python3 /tmp/build_item5_fixtures.py");
  console.log("built " + FLAT + " + " + GZ);
}
const EXPECT = JSON.parse(fs.readFileSync(EXP + "/fixtures/item5_expected.json", "utf8"));
const zipInfo = (file) => JSON.parse(execFileSync("python3", ["-c",
  "import zipfile,json,sys\nz=zipfile.ZipFile(sys.argv[1])\nprint(json.dumps([{'name':i.filename,'size':i.file_size,'method':i.compress_type,'crc':i.CRC} for i in z.infolist()]))",
  file], { encoding: "utf8" }));
const zipRead = (file, name) => execFileSync("python3", ["-c",
  "import zipfile,sys\nsys.stdout.buffer.write(zipfile.ZipFile(sys.argv[1]).read(sys.argv[2]))",
  file, name]);

// ---- Nạp stream.js trong vm (Node có DecompressionStream + Blob) ----
const streamSrc = fs.readFileSync(SITE + "/stream.js", "utf8");
const sandbox = { console, TextEncoder, TextDecoder, Blob, Response, DecompressionStream };
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(streamSrc + "\n;PB3Stream;", sandbox);
const S = sandbox.PB3Stream;

// ================= A. Unit =================
{ // A1. Ngưỡng cảnh báo kích thước
  const t0 = performance.now();
  const MB = 1024 * 1024;
  const cases = [
    [600 * MB - 1, "none"], [600 * MB, "none"], [600 * MB + 1, "warn"],
    [800 * MB, "warn"], [800 * MB + 1, "strong"],
    [2 * 1024 * MB, "strong"], [2 * 1024 * MB + 1, "reject"],
  ];
  const got = cases.map(([n, want]) => S.sizeWarning(n).level === want ? "ok" : "SAI(" + S.sizeWarning(n).level + "!=" + want + ")");
  const w600 = S.sizeWarning(650 * MB).text;
  const w800 = S.sizeWarning(900 * MB).text;
  const w2g = S.sizeWarning(3 * 1024 * MB).text;
  const ok = got.every((g) => g === "ok") &&
    />600MB/.test(w600) && /vẫn thử chạy|Vẫn thử chạy/.test(w600) &&
    />800MB/.test(w800) && /iSH/.test(w800) &&
    />2GB/.test(w2g) && /iSH/.test(w2g);
  rec("node_size_warning_thresholds", ok,
    "ngưỡng: " + cases.map(([n], i) => (n / MB) + "MB->" + got[i]).join(", ") +
    "; >600MB: \"" + w600 + "\"; >800MB: \"" + w800 + "\"; >2GB: \"" + w2g + "\"",
    performance.now() - t0);
}

{ // A2. EOCD khớp ambig.zip (tên/cỡ/method)
  const t0 = performance.now();
  const buf = fs.readFileSync(AMBIG);
  const r = S.listZipEntries(buf);
  const want = zipInfo(AMBIG);
  const ok = r.ok && !r.nested && r.entries.length === want.length &&
    want.every((w, i) => r.entries[i].name === w.name && r.entries[i].size === w.size &&
      r.entries[i].method === w.method && r.entries[i].crc === w.crc);
  rec("node_eocd_ambig_matches", ok,
    "JS: " + r.entries.map((e) => e.name + " " + e.size + "B m" + e.method + " " + e.kind).join(" | ") +
    "; kỳ vọng (python zipfile): " + want.map((w) => w.name + " " + w.size + "B m" + w.method).join(" | "),
    performance.now() - t0);
}

{ // A3. game1.zip có inner.zip -> nested, phải fallback về bridge
  const t0 = performance.now();
  const r = S.listZipEntries(fs.readFileSync(GAME1));
  const inner = r.entries.find((e) => e.name === "inner.zip");
  const ok = r.ok && r.nested === true && !!inner && inner.kind === "container";
  rec("node_eocd_nested_fallback", ok,
    "game1.zip: nested=" + r.nested + ", inner.zip kind=" + (inner && inner.kind) +
    ", tổng entry JS=" + r.entries.length + " (bridge sẽ liệt kê đệ quy dạng inner.zip::... nên JS KHÔNG dùng danh sách này)",
    performance.now() - t0);
}

{ // A4. flat fixture: kind thư mục/unity/excluded khớp bridge
  const t0 = performance.now();
  const r = S.listZipEntries(fs.readFileSync(FLAT));
  const wantKind = {
    "data/big-stored.txt": "file", "data/en.json": "file",
    "data/sharedassets0.assets": "unity", "data/font.ttf": "excluded",
    "logs/debug.log": "excluded", "test/x.txt": "excluded", "data/bz2.bin": "file",
  };
  const kindsOk = r.ok && !r.nested && r.entries.length === Object.keys(wantKind).length &&
    r.entries.every((e) => wantKind[e.name] === e.kind) &&
    !r.entries.some((e) => e.name.endsWith("/")) &&
    r.entries.filter((e) => e.kind === "excluded").every((e) => e.size === 0);
  const methodsOk = r.entries.find((e) => e.name === "data/big-stored.txt").method === 0 &&
    r.entries.find((e) => e.name === "data/en.json").method === 8 &&
    r.entries.find((e) => e.name === "data/bz2.bin").method === 12;
  rec("node_eocd_flat_kinds", kindsOk && methodsOk,
    "kinds JS: " + r.entries.map((e) => e.name + "=" + e.kind).join(", ") +
    "; kỳ vọng: " + Object.entries(wantKind).map(([k, v]) => k + "=" + v).join(", ") +
    "; methods stored/deflate/bzip2: " +
    ["data/big-stored.txt", "data/en.json", "data/bz2.bin"].map((n) => n.split("/").pop() + "=m" + r.entries.find((e) => e.name === n).method).join(", "),
    performance.now() - t0);
}

let flatBuf = fs.readFileSync(FLAT);
let flatList = S.listZipEntries(flatBuf);
{ // A5. stored copy-through: byte-identical, không copy thêm (heap delta)
  const t0 = performance.now();
  const entry = flatList.entries.find((e) => e.name === "data/big-stored.txt");
  const before = process.memoryUsage().heapUsed;
  const out = await S.extractZipEntryFromBuffer(flatBuf, entry);
  const after = process.memoryUsage().heapUsed;
  const text = await out.blob.text();
  const byteLen = new TextEncoder().encode(EXPECT.stored).length;
  const ok = out.copiedThrough === true && out.method === "stored" &&
    text === EXPECT.stored && out.bytes === byteLen;
  rec("node_copythrough_stored", ok,
    "stored " + out.bytes + " byte, byte-identical với Python zipfile: " + (text === EXPECT.stored) +
    "; copiedThrough=" + out.copiedThrough + "; heap delta Node khi trích: " +
    Math.round((after - before) / 1024) + " KB (Blob Node vẫn giữ dữ liệu trong bộ nhớ riêng — con số này chỉ là heap JS, không phải RAM tab Safari)",
    performance.now() - t0);
}

{ // A6. deflate qua DecompressionStream: byte-identical
  const t0 = performance.now();
  const entry = flatList.entries.find((e) => e.name === "data/en.json");
  const out = await S.extractZipEntryFromBuffer(flatBuf, entry);
  const text = await out.blob.text();
  // Đối chứng độc lập: Node zlib inflateRawSync trên vùng nén.
  const u8 = new Uint8Array(flatBuf);
  const dataStart = S.localDataOffset(u8, entry);
  const raw = zlib.inflateRawSync(Buffer.from(u8.subarray(dataStart, dataStart + entry.compressedSize))).toString("utf8");
  const ok = out.method === "deflate" && text === EXPECT.en && text === raw;
  rec("node_decompress_deflate", ok,
    "deflate en.json " + out.bytes + " byte, khớp Python zipfile và khớp zlib.inflateRawSync: " + (text === raw),
    performance.now() - t0);
}

{ // A7. method không hỗ trợ (bzip2) -> lỗi có code, caller fallback bridge
  const t0 = performance.now();
  const entry = flatList.entries.find((e) => e.name === "data/bz2.bin");
  let code = null;
  try { await S.extractZipEntryFromBuffer(flatBuf, entry); }
  catch (e) { code = e && e.code; }
  rec("node_unsupported_method_fallback", code === "E_UNSUPPORTED_METHOD",
    "bzip2 (method 12) ném code=" + code + " (app.js bắt và gợi ý dùng tab Trích xuất/bridge)",
    performance.now() - t0);
}

{ // A8. gzip member (1 member) byte-identical
  const t0 = performance.now();
  const gzBuf = fs.readFileSync(GZ);
  const blob = await S.extractGzipMember(gzBuf);
  const text = await blob.text();
  // Đối chứng độc lập bằng Python gzip.
  const pyGunzip = execFileSync("python3", ["-c",
    "import gzip,sys;sys.stdout.buffer.write(gzip.open(sys.argv[1],'rb').read())", GZ]).toString("utf8");
  const ok = text === EXPECT.gzip && text === pyGunzip;
  rec("node_decompress_gzip", ok,
    "gzip " + gzBuf.length + " byte nén -> " + blob.size + " byte, khớp Python gzip và Python gzip.open đọc lại: " + (text === pyGunzip),
    performance.now() - t0);
}

// A8b. gzip NHIỀU member nối nhau: DecompressionStream('gzip') của Node 24
// ném ERR_TRAILING_JUNK_AFTER_STREAM_END DƯỚI DẠNG LỖI KHÔNG BẮT ĐƯỢC
// (uncaught — await không reject mà process chết) nên KHÔNG gọi trực tiếp
// trong harness này; kiểm chứng bằng tiến trình con riêng và ghi nhận giới
// hạn. (Chromium: probe treo, chưa kết luận được; đường nhanh chỉ dùng cho 1 member.)
{ // A8b. multi-member gzip -> lỗi uncaught ở tiến trình con (giới hạn đã biết)
  const t0 = performance.now();
  const probe = "import zlib from 'node:zlib';\n" +
    "const m = Buffer.concat([zlib.gzipSync(Buffer.from('part1-')), zlib.gzipSync(Buffer.from('part2'))]);\n" +
    "try { const b = await new Response(new Blob([m]).stream().pipeThrough(new DecompressionStream('gzip'))).blob();\n" +
    "  console.log('UNEXPECTED-OK ' + await b.text()); }\n" +
    "catch (e) { console.log('REJECTED ' + (e.code || e.message)); }\n";
  fs.writeFileSync("/tmp/item5_gz_probe.mjs", probe);
  let out = "";
  try { out = execSync("node /tmp/item5_gz_probe.mjs", { encoding: "utf8", timeout: 30000 }); }
  catch (e) { out = String((e.stdout || "") + (e.stderr || "")); }
  const ok = /TRAILING_JUNK_AFTER_STREAM_END/.test(out);
  rec("node_gzip_multimember_limit", ok,
    "gzip 2 member nối nhau, tiến trình con: " + out.trim().split("\n").slice(-2).join(" | ") +
    " (giới hạn đã biết của đường nhanh; file .gz nhiều member vẫn xem được qua bridge list/diagnose, chỉ nút tải nhanh không dùng được)",
    performance.now() - t0);
}

{ // A9. output nhiều Blob part + textToBlobParts round-trip
  const t0 = performance.now();
  const big = JSON.stringify({ hello: "Xin chào", data: "x".repeat(200000) });
  const parts = S.textToBlobParts(big, 65536);
  const blob = S.makeOutputBlob(parts, "application/json");
  const back = await blob.text();
  const nParts = parts.length;
  const sizesOk = parts.every((p) => p.byteLength <= 65536) && nParts === Math.ceil(new TextEncoder().encode(big).length / 65536);
  const ok = back === big && sizesOk && S.blobPartsByteLength(parts) === new TextEncoder().encode(big).length;
  rec("node_output_multipart_blob", ok,
    "JSON " + big.length + " ký tự -> " + nParts + " part (mỗi part ≤64KB), Blob " + blob.size +
    " byte, round-trip giống hệt; blobPartsByteLength khớp tổng byte",
    performance.now() - t0);
}

{ // A10. không phải ZIP / ZIP64 / EOCD hỏng
  const t0 = performance.now();
  const notZip = S.listZipEntries(new Uint8Array([1, 2, 3, 4, 5]));
  const trunc = S.listZipEntries(new Uint8Array(fs.readFileSync(AMBIG)).subarray(0, 100));
  // ZIP64 giả: EOCD thật nhưng total/cdSize/cdOffset = 0xFFFF.../
  const fake = new Uint8Array(22);
  const dv = new DataView(fake.buffer);
  dv.setUint32(0, 0x06054b50, true); dv.setUint16(10, 0xffff, true);
  dv.setUint32(12, 0xffffffff, true); dv.setUint32(16, 0xffffffff, true);
  const eocd64 = S.findEocd(fake);
  const ok = notZip.ok === false && notZip.reason === "not-zip" &&
    trunc.ok === false && eocd64 && eocd64.zip64 === true;
  rec("node_eocd_edge_cases", ok,
    "bytes lạ -> " + JSON.stringify({ ok: notZip.ok, reason: notZip.reason }) +
    "; ZIP cắt cụt 100 byte -> ok=" + trunc.ok + " reason=" + trunc.reason +
    "; EOCD báo ZIP64 -> zip64=" + (eocd64 && eocd64.zip64) + " (fallback bridge)",
    performance.now() - t0);
}

{ // A11. Đo: liệt kê bigtest.zip (4000 entry) bằng EOCD — heap/time
  const t0 = performance.now();
  const before = process.memoryUsage().heapUsed;
  const buf = fs.readFileSync(BIG);
  const t1 = performance.now();
  const r = S.listZipEntries(buf);
  const t2 = performance.now();
  const after = process.memoryUsage().heapUsed;
  const ok = r.ok && !r.nested && r.entries.length === 4000;
  rec("node_eocd_bigtest_measure", ok,
    "bigtest.zip " + buf.length + " byte, 4000 entry deflate: EOCD liệt kê trong " +
    Math.round(t2 - t1) + "ms, heap delta " + Math.round((after - before) / 1024) +
    " KB (file đã nằm trong RAM từ trước — chỉ đo phần liệt kê, không đo phần đọc file; " +
    "mức RAM tiết kiệm so với đường bridge trên Safari thật: CHƯA ĐO — cần Joseph đo trên iPhone)",
    performance.now() - t0);
}

// ================= B. Parity với bridge thật (Pyodide Node) =================
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
  if (!JSON.parse(pyodide.globals.get("bridge").__boot_result__).ok) throw new Error("Pyodide bridge boot failed");
  return { pyodide, bridge, call: (fn, ...args) => JSON.parse(bridge[fn](...args)) };
}
const { pyodide, call } = await bootPyodide();
function bridgeList(name, src) {
  pyodide.FS.mkdirTree("/tmp/input");
  pyodide.FS.writeFile("/tmp/input/" + name, new Uint8Array(fs.readFileSync(src)));
  return call("list_entries", "/tmp/input/" + name);
}
function compareLists(jsList, bridgeParsed, label) {
  const b = (bridgeParsed.entries || []).map((e) => e.path + "|" + e.size + "|" + e.kind).join("\n");
  const j = jsList.entries.map((e) => e.name + "|" + e.size + "|" + e.kind).join("\n");
  return { match: b === j, bridgeCount: bridgeParsed.entries.length, jsCount: jsList.entries.length,
    firstDiff: b === j ? "" : ("bridge:\n" + b.split("\n").slice(0, 5).join("\n") + "\nJS:\n" + j.split("\n").slice(0, 5).join("\n")) };
}
{ // B1. ambig.zip: JS khớp bridge từng entry
  const t0 = performance.now();
  const b = bridgeList("ambig.zip", AMBIG);
  const j = S.listZipEntries(fs.readFileSync(AMBIG));
  const c = compareLists(j, b, "ambig");
  rec("bridge_parity_ambig", b.ok && c.match,
    "bridge kind=" + b.kind + " total=" + b.total + "; JS total=" + j.total + ", nested=" + j.nested +
    "; khớp path/size/kind từng entry: " + c.match + (c.match ? "" : "\n" + c.firstDiff),
    performance.now() - t0);
}
{ // B2. bigtest.zip 4000 entry: JS khớp bridge
  const t0 = performance.now();
  const b = bridgeList("bigtest.zip", BIG);
  const j = S.listZipEntries(fs.readFileSync(BIG));
  const c = compareLists(j, b, "bigtest");
  rec("bridge_parity_bigtest_4000", b.ok && c.match,
    "bridge total=" + b.total + "; JS total=" + j.total + "; khớp từng entry: " + c.match +
    (c.match ? "" : "\n" + c.firstDiff),
    performance.now() - t0);
}
{ // B3. flat fixture (thư mục/unity/excluded): JS khớp bridge
  const t0 = performance.now();
  const b = bridgeList("item5_flat.zip", FLAT);
  const j = S.listZipEntries(fs.readFileSync(FLAT));
  const c = compareLists(j, b, "flat");
  rec("bridge_parity_flat_kinds", b.ok && c.match,
    "bridge total=" + b.total + "; JS total=" + j.total + "; khớp từng entry (path/size/kind): " + c.match +
    (c.match ? "" : "\n" + c.firstDiff),
    performance.now() - t0);
}
{ // B4. game1.zip: bridge liệt kê đệ quy inner.zip::..., JS chỉ báo nested -> fallback
  const t0 = performance.now();
  const b = bridgeList("game1.zip", GAME1);
  const j = S.listZipEntries(fs.readFileSync(GAME1));
  const hasNested = (b.entries || []).some((e) => e.path.includes("::"));
  const ok = b.ok && hasNested && j.nested === true;
  rec("bridge_parity_nested_fallback", ok,
    "bridge total=" + b.total + " (có entry lồng dạng :: : " + hasNested + ", vd " +
    ((b.entries || []).find((e) => e.path.includes("::")) || {}).path + "); JS nested=" + j.nested +
    " -> app.js fallback về bridge, không dùng danh sách JS",
    performance.now() - t0);
}

// ================= C. Kiểm tra tĩnh =================
{ // C1. node --check + không COI/SAB/pthreads + nối file
  const t0 = performance.now();
  const checks = {};
  try { execSync("node --check " + SITE + "/stream.js && node --check " + SITE + "/app.js"); checks["node --check stream.js + app.js"] = true; }
  catch (e) { checks["node --check stream.js + app.js"] = false; }
  const stream = fs.readFileSync(SITE + "/stream.js", "utf8");
  checks["stream.js không có new SharedArrayBuffer/Atomics/pthreads"] =
    !/new SharedArrayBuffer|Atomics\.|pthreads/i.test(stream);
  checks["stream.js không bật COOP/COEP"] = !/Cross-Origin-Embedder-Policy|Cross-Origin-Opener-Policy/i.test(stream);
  const index = fs.readFileSync(SITE + "/index.html", "utf8");
  checks["index.html nạp stream.js trước app.js"] =
    index.indexOf('src="stream.js"') !== -1 && index.indexOf('src="stream.js"') < index.indexOf('src="app.js"');
  const app = fs.readFileSync(SITE + "/app.js", "utf8");
  checks["app.js dùng PB3Stream.sizeWarning + fastListEntries + download(parts)"] =
    app.includes("PB3Stream.sizeWarning") && app.includes("fastListEntries()") &&
    app.includes("Array.isArray(data) ? new Blob(data");
  const bridgeSrc = fs.readFileSync(SITE + "/pb3/bridge.py", "utf8");
  checks["bridge.py không thêm op mới (không có def liên quan stream)"] =
    !/def (stream|zip_entry|gzip)_/.test(bridgeSrc);
  const workerSrc = fs.readFileSync(SITE + "/worker.js", "utf8");
  checks["worker.js không đổi protocol (vẫn send-once/files + dest/zip_path)"] =
    workerSrc.includes('"/tmp/input/"') && workerSrc.includes("parsed.dest") && workerSrc.includes("parsed.zip_path");
  const bad = Object.entries(checks).filter(([, v]) => !v).map(([k]) => k);
  rec("static_checks", bad.length === 0,
    Object.entries(checks).map(([k, v]) => (v ? "✓ " : "✗ ") + k).join("\n"),
    performance.now() - t0);
}

// ================= D. Trình duyệt thật 390px =================
const MIME = { ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript", ".json": "application/json", ".css": "text/css", ".webmanifest": "application/manifest+json", ".wasm": "application/wasm", ".png": "image/png", ".b64": "text/plain" };
const server = http.createServer((req, res) => {
  const url = new URL(req.url, "http://127.0.0.1");
  if (url.pathname === "/translate-proxy") { res.writeHead(200, { "content-type": "application/json" }); res.end("{}"); return; }
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
  const pageErrors = [];
  page.on("pageerror", (e) => pageErrors.push(String(e && e.message ? e.message : e)));
  const elText = (id) => page.evaluate((i) => (document.getElementById(i) || {}).textContent || "", id);
  const elHtml = (id) => page.evaluate((i) => (document.getElementById(i) || {}).innerHTML || "", id);
  const waitReady = async () => {
    await page.waitForFunction(
      () => { const el = document.getElementById("status"); return el && el.textContent.includes("Sẵn sàng"); },
      null, { timeout: 240000 });
  };
  const selectFile = async (file) => {
    const name = file.split("/").pop();
    await page.setInputFiles("#file-input", file);
    await page.waitForFunction(
      (n) => { const el = document.getElementById("file-info"); return el && el.textContent.includes(n); },
      name, { timeout: 120000 });
  };
  const openListTab = async () => {
    await page.click("#btn-nav-tools");
    await page.click("#btn-list");
    await page.click("#btn-list-run");
    await page.waitForFunction(
      () => { const el = document.getElementById("list-result"); return el && el.textContent.includes("entry"); },
      null, { timeout: 60000 });
  };

  { // D0. Boot 390px: Sẵn sàng, không lỗi console, không tràn ngang
    const t0 = performance.now();
    await page.goto("http://127.0.0.1:" + PORT + "/index.html");
    await waitReady();
    const overflow = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
    const status = await elText("status");
    rec("browser_boot_390", status.includes("Sẵn sàng") && pageErrors.length === 0 && overflow.sw <= overflow.cw,
      "status: \"" + status + "\"; pageerror: " + (pageErrors.join("; ") || "không có") +
      "; scrollWidth=" + overflow.sw + " clientWidth=" + overflow.cw + " (không tràn ngang)",
      performance.now() - t0);
  }

  { // D1. List tab ambig.zip: liệt kê nhanh EOCD + tải entry deflate byte-identical
    const t0 = performance.now();
    await selectFile(AMBIG);
    await openListTab();
    const html = await elHtml("list-result");
    const fastNote = html.includes("liệt kê nhanh bằng EOCD");
    const dlPromise = page.waitForEvent("download", { timeout: 60000 });
    await page.click("#list-result [data-entry='a/en.json']");
    const dl = await dlPromise;
    const dlPath = await dl.path();
    const got = fs.readFileSync(dlPath);
    const want = zipRead(AMBIG, "a/en.json");
    rec("browser_fast_list_and_entry_download", fastNote && got.equals(want),
      "ghi chú EOCD: " + fastNote + "; tải a/en.json: " + got.length + " byte, byte-identical với Python zipfile: " + got.equals(want) +
      " (deflate qua DecompressionStream, nút Tải trong tab Danh sách)",
      performance.now() - t0);
  }

  { // D2. flat fixture: tải entry stored copy-through + deflate, byte-identical
    const t0 = performance.now();
    await selectFile(FLAT);
    await openListTab();
    const html = await elHtml("list-result");
    const fastNote = html.includes("liệt kê nhanh bằng EOCD");
    const dl1 = page.waitForEvent("download", { timeout: 60000 });
    await page.click("#list-result [data-entry='data/big-stored.txt']");
    const gotStored = fs.readFileSync(await (await dl1).path());
    const dl2 = page.waitForEvent("download", { timeout: 60000 });
    await page.click("#list-result [data-entry='data/en.json']");
    const gotDeflate = fs.readFileSync(await (await dl2).path());
    const ok = fastNote && gotStored.toString("utf8") === EXPECT.stored &&
      gotDeflate.toString("utf8") === EXPECT.en;
    rec("browser_entry_stored_and_deflate", ok,
      "ghi chú EOCD: " + fastNote + "; stored big-stored.txt: " + gotStored.length + " byte, khớp: " + (gotStored.toString("utf8") === EXPECT.stored) +
      " (copy-through Blob.slice); deflate en.json: " + gotDeflate.length + " byte, khớp: " + (gotDeflate.toString("utf8") === EXPECT.en),
      performance.now() - t0);
  }

  { // D3. file .gz: nút tải bản giải nén (DecompressionStream) byte-identical
    const t0 = performance.now();
    await selectFile(GZ);
    await openListTab();
    const html = await elHtml("list-result");
    const hasBtn = html.includes("Tải bản đã giải nén (gzip, DecompressionStream)");
    let match = false, n = 0;
    if (hasBtn) {
      const dlPromise = page.waitForEvent("download", { timeout: 60000 });
      await page.click("#list-result [data-act='gzip-dl']");
      const dl = await dlPromise;
      const got = fs.readFileSync(await dl.path());
      n = got.length; match = got.toString("utf8") === EXPECT.gzip;
    }
    rec("browser_gzip_member_download", hasBtn && match,
      "nút gzip hiện: " + hasBtn + "; bản giải nén: " + n + " byte, byte-identical với Python gzip: " + match,
      performance.now() - t0);
  }

  { // D4. Cảnh báo >600MB hiện đúng + vẫn tiếp tục (file sparse 610MB)
    const t0 = performance.now();
    const big610 = "/tmp/item5_610m.zip";
    execSync("truncate -s 610M " + big610);
    await selectFile(big610);
    const err = await elText("error");
    const info = await elText("file-info");
    const errHidden = await page.evaluate(() => document.getElementById("error").hidden);
    const ok = !errHidden && err.includes(">600MB") && info.includes("610");
    rec("browser_warn_600mb", ok,
      "file sparse 610MB: #error hiện=" + !errHidden + ", text: \"" + err + "\"; file-info: \"" + info + "\" (cảnh báo còn hiện sau khi file đã nạp — trước đây bị clearError xóa)",
      performance.now() - t0);
    await page.evaluate(() => { document.getElementById("btn-clear-file").click(); });
  }

  { // D5. Cảnh báo >800MB khuyên iSH (file sparse 810MB)
    const t0 = performance.now();
    const big810 = "/tmp/item5_810m.zip";
    execSync("truncate -s 810M " + big810);
    await selectFile(big810);
    const err = await elText("error");
    const errHidden = await page.evaluate(() => document.getElementById("error").hidden);
    const ok = !errHidden && err.includes(">800MB") && /iSH/.test(err);
    rec("browser_warn_800mb_ish", ok,
      "file sparse 810MB: #error hiện=" + !errHidden + ", text: \"" + err + "\"",
      performance.now() - t0);
    await page.evaluate(() => { document.getElementById("btn-clear-file").click(); });
  }

  { // D6. >2GB từ chối trước khi đọc file (file sparse 2,1GB)
    const t0 = performance.now();
    const big21 = "/tmp/item5_2100m.zip";
    execSync("truncate -s 2100M " + big21);
    await page.setInputFiles("#file-input", big21);
    await sleep(500);
    const err = await elText("error");
    const errHidden = await page.evaluate(() => document.getElementById("error").hidden);
    const ok = !errHidden && err.includes(">2GB");
    rec("browser_reject_2gb", ok,
      "file sparse 2,1GB: #error hiện=" + !errHidden + ", text: \"" + err + "\" (từ chối trước khi đọc — không tốn RAM)",
      performance.now() - t0);
  }

  { // D7. Không tràn ngang sau khi render danh sách + không lỗi console mới
    const t0 = performance.now();
    const overflow = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
    rec("browser_390_no_overflow_final", overflow.sw <= overflow.cw && pageErrors.length === 0,
      "scrollWidth=" + overflow.sw + " clientWidth=" + overflow.cw + "; pageerror cả phiên: " + (pageErrors.join("; ") || "không có"),
      performance.now() - t0);
  }
} finally {
  if (browser) await browser.close().catch(() => {});
  server.close();
}

// ================= TỔNG KẾT =================
const total = Object.keys(results).length;
const failed = Object.keys(results).filter((k) => !results[k].ok);
const md = "# Mục 5 — streaming reader + copy-through + DecompressionStream + cảnh báo file lớn\n\n" +
  "Ngày kiểm chứng: " + new Date().toISOString() + " (giờ UTC). Chạy: `node verify_item5.mjs`.\n\n" +
  "## Kết quả: " + (total - failed.length) + "/" + total + " PASS" + (failed.length ? " — FAIL: " + failed.join(", ") : "") + "\n\n" +
  Object.entries(results).map(([k, v]) => "### " + k + ": " + (v.ok ? "PASS" : "FAIL") + (v.seconds != null ? " (" + v.seconds + "s)" : "") + "\n" + v.detail + "\n").join("\n") +
  "\n## Ghi chú\n\n" +
  "- Xem chi tiết thay đổi + giới hạn trong `exp/item5_results.md` (báo cáo tay, kèm số đo).\n";
fs.writeFileSync(EXP + "/item5_results.json", JSON.stringify({ total, failures: failed, results }, null, 2));
fs.writeFileSync(EXP + "/verify_item5_results.md", md);
console.log("\n==== TỔNG: " + total + " mục, " + failed.length + " FAIL ====");
if (failed.length) { console.log("FAIL: " + failed.join(", ")); process.exit(1); }
