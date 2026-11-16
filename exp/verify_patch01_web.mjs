// verify_patch01_web.mjs — Hồi quy patch01 trên bản web (Pyodide cục bộ, Node).
// Trigger gốc (iSH 2026-11-16, log session-20261116-005829-18): file XML thụt
// đầu dòng trong IPA bị yaml_ini sniff chấm 0.7 như INI -> probe đưa XML vào
// configparser -> AttributeError (bug configparser: dòng nối tiếp sau option
// không giá trị gọi .append trên None) -> crash cả extract.
// Kiểm tra: (1) extract zip chứa đúng trigger XML đó hoàn thành, không crash;
// (2) file XML được xử lý bởi xml_strings, KHÔNG phải yaml_ini;
// (3) mức đơn vị: yaml_ini sniff XML = 0.0, _parse_ini_struct(XML) -> E_CORRUPT
//     (không AttributeError), normalize_typed_path bỏ dấu "." cuối.
// Chạy: node verify_patch01_web.mjs (từ /home/hatch/workspace/pb3_web/exp)
// Kết quả: patch01_web_results.md + patch01_web_results.json
import { loadPyodide } from "../site/pyodide/pyodide.mjs";
import fs from "node:fs";

const SITE = "/home/hatch/workspace/pb3_web/site";
const EXP = "/home/hatch/workspace/pb3_web/exp";
const sec = (ms) => Math.round(ms / 100) / 10;
const results = {};
let failures = 0;

function rec(name, ok, detail, ms) {
  results[name] = { ok, detail, seconds: sec(ms) };
  if (!ok) failures++;
  console.log(`\n### ${name}: ${ok ? "PASS" : "FAIL"} (${sec(ms)}s)\n${detail}`);
}

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

await pyodide.loadPackage("lz4");
{
  const t0 = performance.now();
  const man = JSON.parse(fs.readFileSync(SITE + "/pb3/files.json", "utf8"));
  for (const rel of man.files) {
    const buf = fs.readFileSync(SITE + "/" + rel);
    const target = "/pb3web/" + rel.slice(4);
    pyodide.FS.mkdirTree(target.slice(0, target.lastIndexOf("/")));
    pyodide.FS.writeFile(target, new Uint8Array(buf));
  }
  const b64 = fs.readFileSync(SITE + "/fonts/BeVietnamPro-Regular.ttf.b64", "utf8").replace(/\s+/g, "");
  const bin = Buffer.from(b64, "base64");
  pyodide.FS.mkdirTree("/pb3web/fonts");
  pyodide.FS.writeFile("/pb3web/fonts/BeVietnamPro-Regular.ttf", new Uint8Array(bin));
  rec("write_site_files", man.files.length === 89, `${man.files.length} file pb3/ + font b64 ghi vào /pb3web`, performance.now() - t0); // 89 = 88 + 1 (mục 8 thêm sdk/plan.py, 2026-11-16)
}

pyodide.runPython("import sys; sys.path.insert(0, '/pb3web')\nimport bridge");
function call(fn, ...args) {
  const b = pyodide.globals.get("bridge");
  return JSON.parse(b[fn](...args));
}
function runPy(code) {
  return pyodide.runPython(code);
}

// ---- boot 20/20 (điều kiện tiên quyết) ----
{
  const t0 = performance.now();
  const boot = call("boot");
  rec("boot", boot.ok === true && boot.plugins === 20, JSON.stringify(boot), performance.now() - t0);
}

// ---- (1) extract zip trigger XML: hoàn thành, không crash ----
let extractResult = null;
{
  const t0 = performance.now();
  const buf = fs.readFileSync(EXP + "/fixtures/xml_trigger.zip");
  pyodide.FS.mkdirTree("/tmp/input");
  pyodide.FS.writeFile("/tmp/input/xml_trigger.zip", new Uint8Array(buf));
  extractResult = call("extract", "/tmp/input/xml_trigger.zip");
  const files = (extractResult.files || []).map((f) => `${f.path}@${f.plugin}`);
  rec("extract_xml_trigger_no_crash", extractResult.ok === true && extractResult.decision === "done",
    `decision=${extractResult.decision} total_entries=${extractResult.total_entries} files=[${files.join(", ")}] errors=${JSON.stringify(extractResult.errors)}`,
    performance.now() - t0);
}

// ---- (2) file XML đi qua xml_strings, không qua yaml_ini ----
{
  const t0 = performance.now();
  const files = extractResult.files || [];
  const xml = files.find((f) => f.path.endsWith("strings.xml"));
  const yamlTouched = files.some((f) => f.plugin === "yaml_ini");
  const xmlOk = xml && xml.plugin === "xml_strings" && xml.entries >= 1;
  rec("xml_routed_to_xml_strings", xmlOk === true && yamlTouched === false,
    `strings.xml plugin=${xml ? xml.plugin : "KHÔNG CÓ"} entries=${xml ? xml.entries : "?"}; có file nào qua yaml_ini=${yamlTouched}`,
    performance.now() - t0);
}

// ---- (3a) yaml_ini sniff XML = 0.0 ----
{
  const t0 = performance.now();
  let out;
  try {
    out = runPy(`
from plugins.yaml_ini.plugin import YamlIniPlugin
XML = b'<?xml version="1.0" encoding="utf-8"?>\\n<resources>\\n    <string name="app_name">9 Kings</string>\\n</resources>\\n'
p = YamlIniPlugin()
s = p._sniff(XML, "res/values/strings.xml")
s
`);
  } catch (e) {
    out = "JS_ERROR:" + String(e.message || e).slice(0, 200);
  }
  rec("yaml_ini_sniff_xml_zero", out === 0.0, `sniff(XML thụt đầu dòng) = ${out} (mong đợi 0.0)`, performance.now() - t0);
}

// ---- (3b) _parse_ini_struct(XML) -> E_CORRUPT, không AttributeError ----
{
  const t0 = performance.now();
  let out;
  try {
    out = runPy(`
from plugins.yaml_ini.plugin import _parse_ini_struct
from core.errors import PB3Error
XML = '<?xml version="1.0" encoding="utf-8"?>\\n<resources>\\n    <string name="app_name">9 Kings</string>\\n</resources>\\n'
try:
    _parse_ini_struct(XML, "test.xml")
    out = "NO_ERROR"
except PB3Error as e:
    out = "PB3Error:" + e.code
except AttributeError:
    out = "AttributeError:CRASH"
except Exception as e:
    out = "OTHER:" + type(e).__name__
out
`);
  } catch (e) {
    out = "JS_ERROR:" + String(e.message || e).slice(0, 200);
  }
  rec("parse_ini_struct_xml_e_corrupt", out === "PB3Error:E_CORRUPT",
    `_parse_ini_struct(trigger XML) = ${out} (mong đợi PB3Error:E_CORRUPT)`, performance.now() - t0);
}

// ---- (3c) normalize_typed_path bỏ dấu "." cuối ----
{
  const t0 = performance.now();
  let out;
  try {
    out = runPy(`
import os
from core.safe_io import normalize_typed_path
os.makedirs("/tmp/pathtest", exist_ok=True)
open("/tmp/pathtest/game.ipa", "wb").write(b"x")
a = normalize_typed_path("/tmp/pathtest/game.ipa.")
b = normalize_typed_path("/tmp/pathtest/game.ipa")
c = normalize_typed_path("/tmp/pathtest/khongco.ipa.")
(a, b, c)
`);
  } catch (e) {
    out = "JS_ERROR:" + String(e.message || e).slice(0, 200);
  }
  const got = out && out.toJs ? out.toJs() : out;
  const ok = Array.isArray(got) && got[0] === "/tmp/pathtest/game.ipa" && got[1] === "/tmp/pathtest/game.ipa" && got[2] === "/tmp/pathtest/khongco.ipa.";
  rec("normalize_typed_path_trailing_dot", ok,
    `game.ipa. -> ${Array.isArray(got) ? got[0] : got}; game.ipa -> ${Array.isArray(got) ? got[1] : "?"}; khongco.ipa. -> ${Array.isArray(got) ? got[2] : "?"}`, performance.now() - t0);
}

results._failures = failures;
fs.writeFileSync(EXP + "/patch01_web_results.json", JSON.stringify(results, null, 2));
const lines = ["# Hồi quy patch01 trên bản web PB3 (Pyodide cục bộ, Node)", "",
  `Ngày: 2026-11-16. Pyodide ${pyodide.version}. Chạy bridge.py trên bản vendor trong site/.`, "",
  "| Mục | Kết quả | Giây | Chi tiết |", "|---|---|---|---|"];
for (const [k, v] of Object.entries(results)) {
  if (k.startsWith("_")) continue;
  lines.push(`| ${k} | ${v.ok ? "PASS" : "FAIL"} | ${v.seconds} | ${String(v.detail).replace(/\|/g, "/").slice(0, 220)} |`);
}
lines.push("", `Thất bại: ${failures}`);
fs.writeFileSync(EXP + "/patch01_web_results.md", lines.join("\n"));
console.log(`\n=== XONG: ${Object.keys(results).length - 1} mục, ${failures} FAIL — xem patch01_web_results.md ===`);
process.exit(failures ? 1 : 0);
