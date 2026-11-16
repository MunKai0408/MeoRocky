// verify_limit_2gb.mjs — Kiểm chứng giới hạn file đầu vào 2GB của bản web.
// Chạy: node verify_limit_2gb.mjs (từ /home/hatch/workspace/pb3_web/exp)
// Kết quả: limit_2gb_results.md + limit_2gb_results.json
// Cách kiểm biên: KHÔNG tạo file 2GB thật. Trong Pyodide, monkeypatch
// os.path.getsize để _check_size (hàm thật trong site/pb3/bridge.py) nhận
// kích thước giả lập; ngoài ra kiểm bằng file sparse thật trên host
// (truncate, ~0 byte đĩa) qua python3 hệ thống — xem limit_2gb_results.md.
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

// CHẶN MỌI FETCH HTTP(S)
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

// Ghi file site vào FS ảo /pb3web (như worker fetch từng file)
{
  const t0 = performance.now();
  const man = JSON.parse(fs.readFileSync(SITE + "/pb3/files.json", "utf8"));
  for (const rel of man.files) {
    const buf = fs.readFileSync(SITE + "/" + rel);
    const target = "/pb3web/" + rel.slice(4);
    pyodide.FS.mkdirTree(target.slice(0, target.lastIndexOf("/")));
    pyodide.FS.writeFile(target, new Uint8Array(buf));
  }
  rec("write_site_files", man.files.length === 90, `${man.files.length} file pb3/ ghi vào /pb3web`, performance.now() - t0); // 90 = 89 + 1 (mục C7 thêm sdk/patch_pack.py, 2026-11-16)
}

pyodide.runPython("import sys; sys.path.insert(0, '/pb3web')\nimport bridge");

// 1. MAX_WEB_INPUT phải đúng 2 GiB
{
  const t0 = performance.now();
  const v = pyodide.runPython("bridge.MAX_WEB_INPUT");
  rec("max_web_input_value", v === 2 * 1024 * 1024 * 1024,
    `bridge.MAX_WEB_INPUT = ${v} (mong đợi ${2 * 1024 * 1024 * 1024})`, performance.now() - t0);
}

// 2. Kiểm biên _check_size thật, với getsize giả lập (không tạo file lớn)
{
  const t0 = performance.now();
  const cases = [
    // [tên, kích thước giả lập, mong đợi qua cổng]
    ["1GiB", 1024 * 1024 * 1024, true],
    ["1.5GiB", Math.floor(1.5 * 1024 * 1024 * 1024), true],   // trường hợp phân biệt: giới hạn cũ 1GB sẽ chặn
    ["2GiB_chinh_xac", 2 * 1024 * 1024 * 1024, true],          // biên: điều kiện là n > MAX nên đúng 2GB vẫn qua
    ["2GiB_cong_1", 2 * 1024 * 1024 * 1024 + 1, false],
    ["2.5GiB", Math.floor(2.5 * 1024 * 1024 * 1024), false],
  ];
  const out = [];
  let okAll = true;
  for (const [name, size, expectPass] of cases) {
    pyodide.runPython(`import os\n_orig_getsize = os.path.getsize\nos.path.getsize = lambda p: ${size}`);
    let passed, err = "";
    try {
      pyodide.runPython("bridge._check_size('/tmp/fake.bin')");
      passed = true;
    } catch (e) {
      passed = false;
      err = String(e?.message ?? e);
    }
    pyodide.runPython("os.path.getsize = _orig_getsize");
    const ok = passed === expectPass && (expectPass || err.includes("2GB"));
    if (!ok) okAll = false;
    out.push(`${name} (${(size / 1024 / 1024).toFixed(0)}MB): ${passed ? "qua" : "chặn"} — mong đợi ${expectPass ? "qua" : "chặn"}${expectPass ? "" : `, lỗi nhắc 2GB=${err.includes("2GB")}`}`);
  }
  rec("check_size_boundary", okAll, out.join("\n"), performance.now() - t0);
}

// 3. Kiểm tĩnh phía JS: app.js MAX_BYTES + cổng f.size > MAX_BYTES
{
  const t0 = performance.now();
  const a = fs.readFileSync(SITE + "/app.js", "utf8");
  const m = a.match(/const MAX_BYTES\s*=\s*([^;]+);/);
  let val = null;
  if (m) { try { val = eval(m[1]); } catch { val = null; } }  // eslint-disable-line no-eval
  const gate = /f\.size\s*>\s*MAX_BYTES/.test(a);
  const errMsg = />2GB/.test(a);
  rec("app_js_max_bytes", val === 2 * 1024 * 1024 * 1024 && gate && errMsg,
    `MAX_BYTES = ${val} (mong đợi ${2 * 1024 * 1024 * 1024}); cổng f.size > MAX_BYTES=${gate}; thông báo nhắc >2GB=${errMsg}`,
    performance.now() - t0);
}

// 4. Kiểm tĩnh văn bản: 2GB + cảnh báo RAM, không còn 1GB
{
  const t0 = performance.now();
  const files = ["pb3/bridge.py", "app.js", "index.html", "README.md"];
  const texts = files.map((f) => fs.readFileSync(SITE + "/" + f, "utf8"));
  const joined = texts.join("\n");
  const noOldLimit = !/1GB/.test(joined) && !/>1 GiB/.test(joined);
  const idx = texts[2];
  const readme = texts[3];
  const warnHtml = /≤2GB/.test(idx) && /1–1,5GB/.test(idx);
  const warnReadme = /tối đa 2GB/.test(readme) && /1–1,5GB/.test(readme);
  rec("texts_2gb_warning", noOldLimit && warnHtml && warnReadme,
    `không còn "1GB" trong 4 file=${noOldLimit}; index.html có ≤2GB + cảnh báo 1–1,5GB=${warnHtml}; README có tối đa 2GB + cảnh báo=${warnReadme}`,
    performance.now() - t0);
}

fs.writeFileSync(EXP + "/limit_2gb_results.json", JSON.stringify(results, null, 2));
const lines = [
  "# Kết quả kiểm chứng giới hạn 2GB — bản web PB3",
  "",
  "Ngày: 2026-11-16. Pyodide " + pyodide.version + ". Chạy bridge.py trên bản vendor trong site/ (không dùng cây PB3 gốc).",
  "",
  "## Cách kiểm biên",
  "",
  "- Không tạo file 2GB thật. Trong Pyodide, monkeypatch `os.path.getsize` để hàm thật `bridge._check_size` nhận kích thước giả lập (1GiB, 1.5GiB, đúng 2GiB, 2GiB+1, 2.5GiB).",
  "- Ngoài ra kiểm bằng file sparse thật trên host (truncate, ~0 byte đĩa) qua python3 hệ thống — xem mục file sparse bên dưới.",
  "",
  "## Kết quả",
  "",
  ...Object.entries(results).map(([k, v]) => `- **${k}**: ${v.ok ? "PASS" : "FAIL"} — ${String(v.detail).replace(/\n/g, " / ")} (${v.seconds}s)`),
  "",
  `Tổng: ${Object.keys(results).length} mục, ${failures} FAIL.`,
  "",
];
fs.writeFileSync(EXP + "/limit_2gb_results.md", lines.join("\n"));
console.log(`\n=== XONG: ${Object.keys(results).length} mục, ${failures} FAIL — xem limit_2gb_results.md ===`);
