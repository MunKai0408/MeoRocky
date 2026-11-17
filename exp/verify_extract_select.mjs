// verify_extract_select.mjs — Kiểm chứng PHẦN B: chọn file + lọc locale
// en/zh + báo nguồn gốc (repo MunKai0408/MeoRocky).
//
// Chạy: node verify_extract_select.mjs (từ thư mục exp/ của repo)
// Kết quả: extract_select_results.md + extract_select_results.json
//
// Bridge thật trên Pyodide Node (fixture extract_select_fixture.zip):
//  B1. Lọc locale: en -> chỉ English.json (+settings vì không rõ locale);
//      zh -> chỉ Chinese.json (+settings); cả hai -> đủ en+zh+settings,
//      KHÔNG có fr/de; skipped ghi lý do "locale X không phải en/zh".
//      (Đổi kỳ vọng 2026-11-16 theo lệnh mới của parent — ghi đè đặc tả
//      13:27: entry KHÔNG xác định được locale không còn bị loại khi lọc,
//      vì trên IPA thật điều đó làm ra 0 file + banner "Lỗi không rõ".)
//  B2. Chọn chỉ bundle + lọc en -> chỉ English.json từ bundle;
//      chọn resources.assets + lọc zh -> không ra file nào, báo rõ lý do.
//  B3. Chọn tay nhưng bỏ chọn bundle -> bundle ghi skipped "không được chọn".
//  B4. Báo nguồn gốc: report JSON files[].source + report txt `output ← source`
//      + bridge trả "source" cho từng file.
//  B5. read_extracted: đọc English.json đã trích xuất; tên sai/traversal/
//      file quá lớn -> lỗi rõ; file không phải JSON -> lỗi rõ.
import fs from "node:fs";
import { loadPyodide } from "../pyodide/pyodide.mjs";

const ROOT = decodeURIComponent(new URL("..", import.meta.url).pathname).replace(/\/$/, "");
const SITE = ROOT;
const EXP = ROOT + "/exp";
const FX = EXP + "/fixtures/extract_select_fixture.zip";
const BUNDLE = "Payload/9Kings.app/Data/Raw/aa/iOS/localization-locales_assets_all.bundle";
const RES = "Payload/9Kings.app/Data/resources.assets";
const LVL = "Payload/9Kings.app/Data/level1.unity3d";
const SETTINGS = "Payload/9Kings.app/Data/Raw/aa/settings.json";
const README = "Payload/9Kings.app/Data/Raw/readme.txt";
const sec = (ms) => Math.round(ms / 100) / 10;
const results = {};
let failures = 0;
function rec(name, ok, detail, ms) {
  results[name] = { ok, detail, seconds: ms == null ? undefined : sec(ms) };
  if (!ok) failures++;
  console.log(`\n### ${name}: ${ok ? "PASS" : "FAIL"}${ms == null ? "" : " (" + sec(ms) + "s)"}\n${detail}`);
}

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
  if (!JSON.parse(pyodide.globals.get("bridge").__boot_result__).ok) throw new Error("boot failed");
  return { pyodide, bridge, call: (fn, ...args) => JSON.parse(bridge[fn](...args)) };
}
const { pyodide, bridge, call } = await bootPyodide();
pyodide.FS.mkdirTree("/tmp/input");
pyodide.FS.writeFile("/tmp/input/fixture.zip", new Uint8Array(fs.readFileSync(FX)));
const readOut = (name) => new TextDecoder().decode(pyodide.FS.readFile("/tmp/pb3out/" + name));
const exportedNames = (r) => (r.files || []).map((f) => f.exported).sort();

{ // B1a. Lọc en -> English.json + settings.json (settings không rõ
  // locale vẫn giữ — đổi kỳ vọng 2026-11-16, xem chú thích B1)
  const t0 = performance.now();
  const r = call("extract", "/tmp/input/fixture.zip", null, ["en"]);
  const got = exportedNames(r);
  const sk = r.skipped || [];
  const frSk = sk.some((s) => /fr\.json/.test(s.path) && /không phải en/.test(s.reason));
  const zhSk = sk.some((s) => /zh\.json/.test(s.path) && /không phải en/.test(s.reason));
  rec("B1a_locale_filter_en_only", r.decision === "done" && JSON.stringify(got) === JSON.stringify(["English.json", "settings.json"]) && frSk && zhSk,
    `decision=${r.decision}; exported=${JSON.stringify(got)} (cần ["English.json", "settings.json"]); ` +
    `fr.json skipped "không phải en": ${frSk}; zh.json skipped: ${zhSk}`,
    performance.now() - t0);
}
{ // B1b. Lọc zh -> Chinese.json + settings.json (như B1a)
  const t0 = performance.now();
  const r = call("extract", "/tmp/input/fixture.zip", null, ["zh"]);
  const got = exportedNames(r);
  const sk = r.skipped || [];
  const enSk = sk.some((s) => /en\.json/.test(s.path) && /không phải zh/.test(s.reason));
  rec("B1b_locale_filter_zh_only", r.decision === "done" && JSON.stringify(got) === JSON.stringify(["Chinese.json", "settings.json"]) && enSk,
    `decision=${r.decision}; exported=${JSON.stringify(got)} (cần ["Chinese.json", "settings.json"]); ` +
    `en.json skipped "không phải zh": ${enSk}`,
    performance.now() - t0);
}
{ // B1c. Lọc cả hai -> đủ en+zh+settings (settings không rõ locale
  // vẫn GIỮ — đổi kỳ vọng 2026-11-16, xem chú thích B1 ở đầu file),
  // KHÔNG có fr/de
  const t0 = performance.now();
  const r = call("extract", "/tmp/input/fixture.zip", null, ["en", "zh"]);
  const got = exportedNames(r);
  const sk = r.skipped || [];
  const reasons = {};
  for (const s of sk) {
    if (/fr\.json/.test(s.path)) reasons.fr = s.reason;
    if (/de\.json/.test(s.path)) reasons.de = s.reason;
    if (s.path === SETTINGS) reasons.settings = s.reason;
  }
  const ok = r.decision === "done" && JSON.stringify(got) === JSON.stringify(["Chinese.json", "English.json", "settings.json"]) &&
    /không phải en\/zh/.test(reasons.fr || "") && /không phải en\/zh/.test(reasons.de || "") &&
    !reasons.settings;
  rec("B1c_locale_filter_both", ok,
    `decision=${r.decision}; exported=${JSON.stringify(got)} (cần en+zh+settings, không fr/de); ` +
    `skipped fr=${JSON.stringify(reasons.fr)} de=${JSON.stringify(reasons.de)} settings=${JSON.stringify(reasons.settings)} (settings không rõ locale phải được GIỮ, không còn trong skipped)`,
    performance.now() - t0);
}
{ // B2a. Chọn chỉ bundle + lọc en -> chỉ English.json từ bundle
  const t0 = performance.now();
  const r = call("extract", "/tmp/input/fixture.zip", [BUNDLE], ["en"]);
  const got = exportedNames(r);
  const f = (r.files || [])[0] || {};
  const sk = r.skipped || [];
  const othersSkipped = [RES, LVL, SETTINGS, README].every((p) =>
    sk.some((s) => s.path === p && /không được chọn/.test(s.reason)));
  rec("B2a_select_bundle_only_en", r.decision === "done" && JSON.stringify(got) === JSON.stringify(["English.json"]) &&
    f.source === BUNDLE && othersSkipped,
    `decision=${r.decision}; exported=${JSON.stringify(got)}; source=${JSON.stringify(f.source)} (cần đúng bundle); ` +
    `resources/level1/settings/readme đều skipped "không được chọn": ${othersSkipped}`,
    performance.now() - t0);
}
{ // B2b. Chọn resources.assets + lọc zh -> không ra file nào, báo rõ lý do
  // (bridge phải có trường error + errors[0] mã E_NO_FILES, không còn
  // "Lỗi không rõ" — fix 2026-11-16)
  const t0 = performance.now();
  const r = call("extract", "/tmp/input/fixture.zip", [RES], ["zh"]);
  const sk = r.skipped || [];
  const bundleSk = sk.some((s) => s.path === BUNDLE && /không được chọn/.test(s.reason));
  const enSk = sk.some((s) => /resources\.assets::en/.test(s.path) && /không phải zh/.test(s.reason));
  const errOk = typeof r.error === "string" && /không còn ứng viên nào sau bước lọc/.test(r.error) &&
    !/Lỗi không rõ/.test(r.error) && (r.errors || [])[0] && (r.errors || [])[0].code === "E_NO_CANDIDATE";
  rec("B2b_select_resources_zh_none", r.decision === "none" && (r.files || []).length === 0 && bundleSk && enSk && errOk,
    `decision=${r.decision} (đúng: không có file zh nào trong resources.assets); files=0; ` +
    `bundle skipped "không được chọn": ${bundleSk}; resources en skipped "không phải zh": ${enSk}; ` +
    `bridge error=${JSON.stringify(r.error)}; errors[0].code=${JSON.stringify((r.errors || [])[0] && (r.errors || [])[0].code)}`,
    performance.now() - t0);
}
{ // B3. Chọn tay nhưng bỏ chọn bundle -> bundle ghi skipped "không được chọn"
  // (settings.json không rõ locale nay VẪN ĐƯỢC GIỮ nên đợt này ra
  // settings.json thay vì 0 file — đổi kỳ vọng 2026-11-16, xem chú thích B1)
  const t0 = performance.now();
  const r = call("extract", "/tmp/input/fixture.zip", [RES, LVL, SETTINGS, README], ["en", "zh"]);
  const sk = r.skipped || [];
  const bundleSk = sk.find((s) => s.path === BUNDLE);
  const got = exportedNames(r);
  rec("B3_unselected_bundle_skipped", bundleSk && /không được chọn/.test(bundleSk.reason) &&
    JSON.stringify(got) === JSON.stringify(["settings.json"]),
    `bundle (không được tick) skipped: ${JSON.stringify(bundleSk && bundleSk.reason)}; ` +
    `decision=${r.decision}; exported=${JSON.stringify(got)} (cần ["settings.json"] — settings không rõ locale được giữ); ` +
    `TextAsset en duy nhất còn lại (resources.assets) kẹt ASK giữa json_text/generic_strings (hành vi trọng tài có sẵn), de bị lọc`,
    performance.now() - t0);
}
{ // B4. Báo nguồn gốc: bridge + report JSON + report txt
  const t0 = performance.now();
  const r = call("extract", "/tmp/input/fixture.zip", null, ["en", "zh"]);
  const rep = JSON.parse(readOut("extraction_report.json"));
  const txt = readOut("extraction_report.txt");
  const bridgeSrc = (r.files || []).every((f) => typeof f.source === "string" && f.source.length > 0);
  const enRep = (rep.files || []).find((f) => f.exported === "English.json") || {};
  const jsonSrc = enRep.source === BUNDLE && rep.files.every((f) => typeof f.source === "string");
  const txtSrc = txt.includes("English.json ← " + BUNDLE);
  const resSrc = (rep.files || []).find((f) => f.path === RES + "::en");
  rec("B4_source_reporting", bridgeSrc && jsonSrc && txtSrc,
    `bridge files[].source có đủ: ${bridgeSrc}; report JSON English.json source=${JSON.stringify(enRep.source)}; ` +
    `txt có "English.json ← ...bundle": ${txtSrc}; ` +
    `(ghi chú: resources.assets::en thua trọng tài trước bundle en.json nên không có trong files — nguồn của nó chỉ hiện ở skipped)`,
    performance.now() - t0);
}
{ // B5. read_extracted
  const t0 = performance.now();
  call("extract", "/tmp/input/fixture.zip", null, ["en", "zh"]);
  const ok1 = call("read_extracted", "English.json");
  const ok1Good = ok1.ok === true && ok1.name === "English.json" &&
    JSON.parse(ok1.text).hello === "Hello" && ok1.bytes > 0;
  const missing = call("read_extracted", "French.json");
  const missingOk = missing.ok === false && /Không tìm thấy/.test(missing.error || "");
  const traversal = call("read_extracted", "../secret.json");
  const travOk = traversal.ok === false && /không hợp lệ/.test(traversal.error || "");
  // File quá lớn: ghi 5MB vào /tmp/pb3out rồi đọc.
  pyodide.FS.writeFile("/tmp/pb3out/big.json", new Uint8Array(5 * 1024 * 1024));
  const big = call("read_extracted", "big.json");
  const bigOk = big.ok === false && /quá lớn/.test(big.error || "");
  // File không phải JSON.
  pyodide.FS.writeFile("/tmp/pb3out/notjson.json", new TextEncoder().encode("khong phai json {"));
  const nj = call("read_extracted", "notjson.json");
  const njOk = nj.ok === false;
  rec("B5_read_extracted", ok1Good && missingOk && travOk && bigOk && njOk,
    `English.json đọc được (hello=Hello, ${ok1.bytes} byte): ${ok1Good}; ` +
    `file chưa trích xuất: ${JSON.stringify(missing.error)}; ` +
    `traversal "../secret.json": ${JSON.stringify(traversal.error)}; ` +
    `file 5MB: ${JSON.stringify(big.error)}; ` +
    `file không phải JSON: ok=${nj.ok}`,
    performance.now() - t0);
}

fs.writeFileSync(EXP + "/extract_select_results.json", JSON.stringify(
  { when: new Date().toISOString(), failures, results }, null, 2));
console.log("\n==== TỔNG: " + Object.keys(results).length + " mục, " + failures + " FAIL ====");
process.exit(failures ? 1 : 0);
