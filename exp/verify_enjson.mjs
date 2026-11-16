// verify_enjson.mjs — Kiểm chứng PHẦN A: fix trích xuất thiếu en.json
// (repo MunKai0408/MeoRocky).
//
// Chạy: node verify_enjson.mjs (từ /home/hatch/workspace/pb3_web/exp)
// Kết quả: enjson_fix_results.md + enjson_fix_results.json
//
//  0. Bằng chứng lỗi cũ: extract.zip thật của Joseph (log [] rỗng, bundle
//     localization không có ở đâu trong report).
//  1. Fixture tự kiểm: parse lại bundle bằng chính parser web (Pyodide).
//  2. A1: auto extract (không chọn file, locales en+zh) trên fixture IPA
//     -> BẮT BUỘC có en.json, nguồn `en.json ← ...bundle`.
//  3. A2: bundle hỏng + bundle 70MB -> trong skipped kèm lý do, không biến
//     mất; log KHÔNG rỗng, có dòng "Unity: ... -> N TextAsset"; report txt
//     có dòng `output ← source`.
//  4. A3: tương thích ngược — extract không lọc (locales=None) vẫn ra đủ
//     en+zh+fr+de+settings như trước.
import fs from "node:fs";
import { loadPyodide } from "../site/pyodide/pyodide.mjs";

const SITE = "/home/hatch/workspace/pb3_web/site";
const EXP = "/home/hatch/workspace/pb3_web/exp";
const FX = EXP + "/fixtures/extract_select_fixture.zip";
const BK = EXP + "/fixtures/extract_broken_bundle.zip";
const REAL = "/home/hatch/workspace/user/files/extract.zip";
const BUNDLE = "Payload/9Kings.app/Data/Raw/aa/iOS/localization-locales_assets_all.bundle";
const sec = (ms) => Math.round(ms / 100) / 10;
const results = {};
let failures = 0;
function rec(name, ok, detail, ms) {
  results[name] = { ok, detail, seconds: ms == null ? undefined : sec(ms) };
  if (!ok) failures++;
  console.log(`\n### ${name}: ${ok ? "PASS" : "FAIL"}${ms == null ? "" : " (" + sec(ms) + "s)"}\n${detail}`);
}

// ---- 0. Bằng chứng lỗi cũ (extract.zip thật của Joseph) ----
{
  const t0 = performance.now();
  const { execFileSync } = await import("node:child_process");
  const out = execFileSync("python3", ["-c", `
import json, zipfile
z = zipfile.ZipFile("${REAL}")
r = json.loads(z.read("extraction_report.json"))
paths = [f["path"] for f in r["files"]]
sk = [s["path"] for s in r["skipped"]]
print(json.dumps({"log": r["log"], "files": len(paths), "skipped": len(sk),
  "bundle_in_files": any("localization-locales" in p for p in paths),
  "bundle_in_skipped": any("localization-locales" in p for p in sk),
  "bundle_in_candidates": any("localization-locales" in str(c) for c in r.get("candidates", [])),
  "total": r["total_entries"]}))`], { encoding: "utf8" });
  const o = JSON.parse(out);
  const ok = Array.isArray(o.log) && o.log.length === 0 && o.files === 36 &&
    o.skipped === 105 && o.total === 23741 &&
    !o.bundle_in_files && !o.bundle_in_skipped && !o.bundle_in_candidates;
  rec("old_evidence_log_empty_bundle_missing", ok,
    `extract.zip thật: log=${JSON.stringify(o.log)} (rỗng), files=${o.files}, skipped=${o.skipped}, ` +
    `total_entries=${o.total}; bundle localization trong files=${o.bundle_in_files}, ` +
    `skipped=${o.bundle_in_skipped}, candidates=${o.bundle_in_candidates} — đúng lỗi cần sửa`,
    performance.now() - t0);
}

// ---- Boot Pyodide + bridge thật ----
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
for (const [name, src] of [["fixture.zip", FX], ["broken.zip", BK]]) {
  pyodide.FS.writeFile("/tmp/input/" + name, new Uint8Array(fs.readFileSync(src)));
}
const readOut = (name) => new TextDecoder().decode(pyodide.FS.readFile("/tmp/pb3out/" + name));
const outExists = (name) => { try { pyodide.FS.stat("/tmp/pb3out/" + name); return true; } catch { return false; } };
const zipNames = () => pyodide.runPython(
  "import zipfile; '\\n'.join(sorted(zipfile.ZipFile('/tmp/extract.zip').namelist()))").split("\n").filter(Boolean);

// ---- 1. Fixture tự kiểm bằng chính parser web ----
{
  const t0 = performance.now();
  const out = pyodide.runPython(`
import json, zipfile
from sdk import unity as u
from core.limits import Limits
lim = Limits(); be = u.UnityBackend("AUTO", lim)
res = {}
with zipfile.ZipFile("/tmp/input/fixture.zip") as z:
    for name in z.namelist():
        data = z.read(name)
        det = u.detect_bundle(data)
        if det is not None:
            b = u.parse_bundle(data, be, lim, where=name)
            tas = []
            for e in b.entry_names():
                sf = u.parse_serialized(b.get_entry(e), lim, where=e)
                tas += [n for _, n, _ in sf.text_assets()]
            res[name] = {"bundle": True, "version": b.version,
                         "compression": b.compression_name, "text_assets": sorted(tas)}
        elif name.endswith(".assets"):
            sf = u.parse_serialized(data, lim, where=name)
            res[name] = {"bundle": False, "text_assets": [n for _, n, _ in sf.text_assets()]}
json.dumps(res, ensure_ascii=False)`);
  const o = JSON.parse(out);
  const bundle = o[BUNDLE] || {};
  const ok = bundle.bundle === true && bundle.version === 7 && bundle.compression === "lz4" &&
    JSON.stringify(bundle.text_assets) === JSON.stringify(["en.json", "fr.json", "zh.json"]) &&
    (o["Payload/9Kings.app/Data/level1.unity3d"] || {}).version === 8 &&
    JSON.stringify((o["Payload/9Kings.app/Data/resources.assets"] || {}).text_assets) === JSON.stringify(["en"]);
  rec("fixture_parses_with_web_parser", ok,
    "bundle v7 lz4 TextAsset=" + JSON.stringify(bundle.text_assets) +
    "; level1 v8=" + JSON.stringify((o["Payload/9Kings.app/Data/level1.unity3d"] || {}).text_assets) +
    "; resources.assets=" + JSON.stringify((o["Payload/9Kings.app/Data/resources.assets"] || {}).text_assets),
    performance.now() - t0);
}

// ---- 2. A1: auto extract, locales en+zh -> BẮT BUỘC có en.json ----
{
  const t0 = performance.now();
  const r = call("extract", "/tmp/input/fixture.zip", null, ["en", "zh"]);
  const enOk = outExists("English.json") && JSON.parse(readOut("English.json")).hello === "Hello";
  const zhOk = outExists("Chinese.json") && JSON.parse(readOut("Chinese.json")).hello === "你好";
  const rep = JSON.parse(readOut("extraction_report.json"));
  const enFile = (rep.files || []).find((f) => f.exported === "English.json") || {};
  const zhFile = (rep.files || []).find((f) => f.exported === "Chinese.json") || {};
  const srcOk = enFile.source === BUNDLE && zhFile.source === BUNDLE;
  const unityLog = (rep.log || []).filter((l) => l.startsWith("Unity:"));
  const logOk = (rep.log || []).length > 0 &&
    unityLog.some((l) => l.includes("localization-locales_assets_all.bundle -> 3 TextAsset"));
  const txt = readOut("extraction_report.txt");
  const txtOk = txt.includes("English.json ← " + BUNDLE) && txt.includes("Chinese.json ← " + BUNDLE);
  const names = zipNames();
  const zipOk = names.includes("English.json") && names.includes("Chinese.json") &&
    names.includes("extraction_report.json") && names.includes("extraction_report.txt");
  const frAbsent = !outExists("French.json") && !outExists("German.json");
  rec("A1_auto_extract_enjson_present", r.decision === "done" && enOk && zhOk && srcOk && logOk && txtOk && zipOk && frAbsent,
    `decision=${r.decision} files=${(r.files || []).map((f) => f.exported).join(",")}; ` +
    `en.json nội dung hello=Hello: ${enOk}; zh hello=你好: ${zhOk}; ` +
    `source English.json=${JSON.stringify(enFile.source)} (cần đúng bundle); ` +
    `log ${rep.log.length} dòng, Unity: ${JSON.stringify(unityLog)}; ` +
    `txt có "English.json ← ...bundle": ${txtOk}; extract.zip chứa report: ${zipOk}; ` +
    `không có French/German.json: ${frAbsent}`, performance.now() - t0);
}

// ---- 3. A2a: bundle hỏng + locales en+zh -> skipped kèm lý do, log không rỗng ----
{
  const t0 = performance.now();
  const r = call("extract", "/tmp/input/broken.zip", null, ["en", "zh"]);
  const sk = r.skipped || [];
  const bundleSk = sk.find((s) => s.path === BUNDLE);
  const hugeSk = sk.find((s) => s.path === "Payload/9Kings.app/Data/huge.bundle");
  const bundleOk = bundleSk && /E_CORRUPT/.test(bundleSk.reason) && /cắt cụt|hỏng/.test(bundleSk.reason);
  const hugeOk = hugeSk && /67108864|64MB|giới hạn entry/.test(hugeSk.reason);
  const log = r.log || [];
  const logOk = log.length > 0 &&
    log.some((l) => l.includes("localization-locales_assets_all.bundle -> 0 TextAsset") && l.includes("E_CORRUPT")) &&
    log.some((l) => l.includes("resources.assets -> 1 TextAsset")) &&
    log.some((l) => l.includes("huge.bundle") && l.includes("giới hạn"));
  rec("A2a_broken_bundle_skipped_with_reason", bundleOk && hugeOk && logOk,
    `decision=${r.decision} (không còn "done" vì bundle hỏng + TextAsset en duy nhất kẹt ASK giữa json_text 0,71/csv_table 0,61 — xem báo cáo); ` +
    `bundle skipped: ${JSON.stringify(bundleSk && bundleSk.reason)}; ` +
    `huge.bundle skipped: ${JSON.stringify(hugeSk && hugeSk.reason)}; ` +
    `log bridge ${log.length} dòng, có Unity 0 TextAsset + E_CORRUPT: ${logOk}`,
    performance.now() - t0);
}

// ---- 3b. A2b: bundle hỏng, KHÔNG lọc -> decision done, report trên đĩa có log + ← ----
{
  const t0 = performance.now();
  const r = call("extract", "/tmp/input/broken.zip", null, null);
  const rep = JSON.parse(readOut("extraction_report.json"));
  const txt = readOut("extraction_report.txt");
  const logOk = (rep.log || []).length > 0 &&
    rep.log.some((l) => l.includes("localization-locales_assets_all.bundle -> 0 TextAsset"));
  const txtOk = txt.includes("settings.json ← Payload/9Kings.app/Data/Raw/aa/settings.json");
  const sk = rep.skipped || [];
  const bundleOk = sk.some((s) => s.path === BUNDLE && /E_CORRUPT/.test(s.reason));
  rec("A2b_broken_bundle_report_on_disk", r.decision === "done" && logOk && txtOk && bundleOk,
    `decision=${r.decision}; disk report log ${rep.log.length} dòng (trước đây luôn []); ` +
    `txt có "settings.json ← ...settings.json": ${txtOk}; ` +
    `bundle trong skipped kèm E_CORRUPT: ${bundleOk}; file xuất: ${(r.files || []).map((f) => f.exported).join(",")}`,
    performance.now() - t0);
}

// ---- 4. A3: tương thích ngược — không lọc -> đủ en+zh+fr+de+settings ----
{
  const t0 = performance.now();
  const r = call("extract", "/tmp/input/fixture.zip", null, null);
  const got = (r.files || []).map((f) => f.exported).sort();
  const want = ["Chinese.json", "English.json", "French.json", "German.json", "settings.json"];
  const ok = r.decision === "done" && JSON.stringify(got) === JSON.stringify(want) &&
    JSON.parse(readOut("French.json")).hello === "Bonjour" &&
    JSON.parse(readOut("German.json")).hello === "Hallo";
  rec("A3_no_filter_back_compat", ok,
    `decision=${r.decision}; exported=${JSON.stringify(got)} (cần ${JSON.stringify(want)}); ` +
    `French.json hello=Bonjour, German.json hello=Hallo`,
    performance.now() - t0);
}

fs.writeFileSync(EXP + "/enjson_fix_results.json", JSON.stringify(
  { when: new Date().toISOString(), failures, results }, null, 2));
console.log("\n==== TỔNG: " + Object.keys(results).length + " mục, " + failures + " FAIL ====");
process.exit(failures ? 1 : 0);
