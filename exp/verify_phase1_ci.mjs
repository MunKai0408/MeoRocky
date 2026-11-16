// verify_phase1_ci.mjs — Kiểm chứng Phase 1 CI cho bản web PB3.
// 1) conftest hợp nhất: marker needs_fixture + hook skip + fixed_now/fixed_today + tiering còn nguyên.
// 2) test ngày pass sau khi touch corpus về hôm nay (chạy pytest thật qua venv sandbox).
// 3) pages.yml parse được + không chứa "0.26.4" + không có bước tải Pyodide.
// 4) test.html tồn tại + loadPyodide cùng origin + không CDN.
// 5) test.yml có 2 job unit + assets, STRICT_FIXTURES=1, gh release download fixtures-v1.
// 6) Probe Pyodide: boot Pyodide vendor local, micropip cài pytest, ghi cây pb3+tests
//    vào FS ảo rồi chạy pytest --tier smoke — kiểm chứng test.html/pyodide-node có khả thi không.
// Chạy: node verify_phase1_ci.mjs (từ /home/hatch/workspace/pb3_web/exp)
// Kết quả: phase1_ci_harness.json (không ghi đè phase1_ci_results.md — báo cáo viết tay).
import { loadPyodide } from "../site/pyodide/pyodide.mjs";
import fs from "node:fs";
import { execSync } from "node:child_process";

const SITE = "/home/hatch/workspace/pb3_web/site";
const WEB = "/home/hatch/workspace/pb3_web";
const EXP = "/home/hatch/workspace/pb3_web/exp";
const VENV_PY = "/home/hatch/workspace/_ci_sim/venv/bin/python";
const sec = (ms) => Math.round(ms / 100) / 10;
const results = {};
let failures = 0;

function rec(name, ok, detail, ms) {
  results[name] = { ok, detail, seconds: sec(ms) };
  if (!ok) failures++;
  console.log(`\n### ${name}: ${ok ? "PASS" : "FAIL"} (${sec(ms)}s)\n${detail}`);
}
function sh(cmd, opts = {}) {
  return execSync(cmd, { encoding: "utf8", timeout: 300000, ...opts });
}

// ---- 1) conftest hợp nhất ----
{
  const t0 = performance.now();
  const c = fs.readFileSync(SITE + "/pb3/tests/conftest.py", "utf8");
  const checks = {
    marker_needs_fixture: /needs_fixture\(\*names\)/.test(c),
    hook_skip: /pytest\.mark\.skip\(reason="thiếu fixture/.test(c),
    strict_env: /STRICT_FIXTURES/.test(c),
    fixed_now: /def fixed_now/.test(c),
    fixed_today: /def fixed_today/.test(c),
    fixture_path: /def fixture_path/.test(c),
    tiering_kept: /--tier/.test(c) && /tiering\.parse_tiers/.test(c) && /tiering\.classify_id/.test(c),
    fixtures_dir: /FIXTURES = pathlib\.Path\(__file__\)\.parent \/ "fixtures"/.test(c),
  };
  const bad = Object.entries(checks).filter(([, v]) => !v).map(([k]) => k);
  rec("conftest_merged", bad.length === 0,
    Object.entries(checks).map(([k, v]) => `${k}=${v}`).join(" ") + (bad.length ? ` THIẾU: ${bad.join(",")}` : ""),
    performance.now() - t0);
}

// ---- 1b) số marker needs_fixture trên test (116 decorator, phủ 126 test) ----
{
  const t0 = performance.now();
  const out = sh(`grep -rn "pytest.mark.needs_fixture" ${SITE}/pb3/tests/test_*.py | wc -l`).trim();
  const n = parseInt(out, 10);
  rec("markers_count", n === 116, `decorator needs_fixture trong test_*.py: ${n} (kỳ vọng 116, phủ 126 test: 5 class-level + 111 method-level)`, performance.now() - t0);
}

// ---- 2) test ngày pass sau khi touch corpus về hôm nay ----
{
  const t0 = performance.now();
  try {
    const out = sh(`cd ${SITE}/pb3 && touch tests/corpus/json_text.json && TMPDIR=/home/hatch/workspace/_ci_sim/tmp ${VENV_PY} -m pytest tests/test_support_matrix.py::TestGenerate::test_effective_maturity_uses_corpus_not_declared -q -p no:cacheprovider 2>&1 | tail -2`);
    const ok = /1 passed/.test(out);
    // Khôi phục mtime gốc 2026-11-15 để cây sạch.
    sh(`cd ${SITE}/pb3 && touch -d "2026-11-15 18:07:46 UTC" tests/corpus/json_text.json`);
    rec("date_test_after_touch", ok, out.trim().replace(/\n/g, " "), performance.now() - t0);
  } catch (e) {
    rec("date_test_after_touch", false, String(e?.message ?? e).slice(0, 500), performance.now() - t0);
  }
}

// ---- 3) pages.yml ----
{
  const t0 = performance.now();
  const y = fs.readFileSync(WEB + "/.github/workflows/pages.yml", "utf8");
  let parsed = "";
  try {
    parsed = sh(`python3 -c "import yaml; d=yaml.safe_load(open('${WEB}/.github/workflows/pages.yml')); print('jobs='+','.join(d['jobs']))"`);
  } catch (e) { parsed = "YAML PARSE FAIL: " + String(e?.message ?? e).slice(0, 200); }
  const checks = {
    parses: /^jobs=/.test(parsed.trim()),
    jobs_unit_assets_deploy: /unit/.test(parsed) && /assets/.test(parsed) && /build-deploy/.test(parsed),
    no_pyodide_0264: !/0\.26\.4/.test(y),
    no_pyodide_download: !/Vendor Pyodide/.test(y) && !/pyodide\.tar/.test(y) && !/curl.*pyodide/i.test(y),
    needs_both_tests: /needs: \[unit, assets\]/.test(y),
    deploy_only_main: /github\.ref == 'refs\/heads\/main'/.test(y),
    pages_source_note: /Source: GitHub Actions/.test(y),
  };
  const bad = Object.entries(checks).filter(([, v]) => !v).map(([k]) => k);
  rec("pages_yml", bad.length === 0,
    parsed.trim() + " | " + Object.entries(checks).map(([k, v]) => `${k}=${v}`).join(" "),
    performance.now() - t0);
}

// ---- 4) test.html ----
{
  const t0 = performance.now();
  const h = fs.readFileSync(SITE + "/test.html", "utf8");
  const checks = {
    exists: true,
    loadPyodide_same_origin: /import\s*\{\s*loadPyodide\s*\}\s*from\s*["']\.\/pyodide\/pyodide\.mjs["']/.test(h),
    no_cdn: !/cdn\.jsdelivr\.net|unpkg\.com|https:\/\/[^"'\s]*pyodide/.test(h),
    micropip_pytest: /micropip\.install\("pytest"\)/.test(h),
    tier_smoke_default: /value="smoke" selected/.test(h),
    no_full_tier: !/value="full"/.test(h),
    honest_note: /KHÔNG phải gate CI/.test(h),
    copy_button: /Sao chép kết quả/.test(h),
    tests_manifest: /pb3\/tests\.json/.test(h),
    pid_deselect: /--deselect', 'tests\/test_app_menu_cli\.py::TestBootstrap::test_detects_interrupted_previous_run'/.test(h),
  };
  const bad = Object.entries(checks).filter(([, v]) => !v).map(([k]) => k);
  const man = JSON.parse(fs.readFileSync(SITE + "/pb3/tests.json", "utf8"));
  rec("test_html", bad.length === 0 && man.files.length === 158, // 158 = 157 + 1 (mục C6 thêm tests/test_tmp_fallback.py, 2026-11-16)
    Object.entries(checks).map(([k, v]) => `${k}=${v}`).join(" ") + ` | tests.json entries=${man.files.length}`,
    performance.now() - t0);
}

// ---- 5) test.yml 2 job ----
{
  const t0 = performance.now();
  const y = fs.readFileSync(WEB + "/.github/workflows/test.yml", "utf8");
  let parsed = "";
  try {
    parsed = sh(`python3 -c "import yaml; d=yaml.safe_load(open('${WEB}/.github/workflows/test.yml')); print('jobs='+','.join(d['jobs']))"`);
  } catch (e) { parsed = "YAML PARSE FAIL"; }
  const checks = {
    parses_jobs: parsed.trim() === "jobs=unit,assets,pyodide-node",
    unit_marker_filter: /-m "not needs_fixture"/.test(y),
    strict_1: /STRICT_FIXTURES: "1"/.test(y),
    gh_release_download: /gh release download "\$FIXTURES_TAG"/.test(y) && /fixtures-v1/.test(y),
    lists_20_files: (y.match(/tests\/fixtures\/unity\/sample\.unity3d/g) || []).length >= 1 && /tests\/fixtures\/zip\/sample\.ipa/.test(y),
    no_old_28_text: !/28 file nhị phân\/UTF-16 chưa lên main/.test(y) && !/1 TEST SẼ ĐỎ/.test(y),
    font_restore_kept: /BeVietnamPro-Regular\.ttf\.b64/.test(y),
    python312: /python-version: "3\.12"/.test(y) && !/python-version: "3\.9"/.test(y),
    pytest_rs: /pytest tests -q -rs/.test(y),
    pyodide_node_job: /pyodide-node:/.test(y) && /\.github\/scripts\/pyodide_smoke\.mjs/.test(y),
  };
  const bad = Object.entries(checks).filter(([, v]) => !v).map(([k]) => k);
  rec("test_yml_two_jobs", bad.length === 0,
    parsed.trim() + " | " + Object.entries(checks).map(([k, v]) => `${k}=${v}`).join(" "),
    performance.now() - t0);
}

// ---- 6) Probe Pyodide: pytest --tier smoke trong Node (mô phỏng test.html) ----
// Cho phép fetch http ở đây (micropip cài pytest từ PyPI) — đếm số lần để báo cáo.
let httpFetchCount = 0;
let pyodideProbe = null;
const _fetch = globalThis.fetch;
globalThis.fetch = async (input, init) => {
  const url = String(typeof input === "string" ? input : input.url);
  if (/^https?:\/\//.test(url)) httpFetchCount++;
  return _fetch(input, init);
};
{
  const t0 = performance.now();
  try {
    const pyodide = await loadPyodide({ indexURL: SITE + "/pyodide/" });
    pyodideProbe = pyodide;
    let detail = `pyodide=${pyodide.version} `;
    try { await pyodide.loadPackage("lz4"); detail += "lz4=OK "; }
    catch (e) { detail += "lz4=FAIL(" + String(e?.message ?? e).slice(0, 80) + ") "; }
    await pyodide.loadPackage("micropip");
    const micropip = pyodide.pyimport("micropip");
    await micropip.install("pytest");
    detail += `micropip pytest=OK http_fetch=${httpFetchCount} `;
    // Ghi cây pb3 + tests vào FS ảo (đọc từ đĩa thay cho fetch, như test.html fetch).
    const man1 = JSON.parse(fs.readFileSync(SITE + "/pb3/files.json", "utf8"));
    const man2 = JSON.parse(fs.readFileSync(SITE + "/pb3/tests.json", "utf8"));
    let n = 0;
    for (const rel of [...man1.files, ...man2.files]) {
      const buf = fs.readFileSync(SITE + "/" + rel);
      const target = "/pb3web/" + rel.slice(4);
      pyodide.FS.mkdirTree(target.slice(0, target.lastIndexOf("/")));
      pyodide.FS.writeFile(target, new Uint8Array(buf));
      n++;
    }
    const b64 = fs.readFileSync(SITE + "/fonts/BeVietnamPro-Regular.ttf.b64", "utf8").replace(/\s+/g, "");
    const bin = Buffer.from(b64, "base64");
    pyodide.FS.mkdirTree("/pb3web/fonts");
    pyodide.FS.writeFile("/pb3web/fonts/BeVietnamPro-Regular.ttf", new Uint8Array(bin));
    detail += `files=${n} font=${bin.length}B `;
    let out = "";
    pyodide.setStdout({ batched: (s) => { out += s + "\n"; } });
    pyodide.setStderr({ batched: (s) => { out += s + "\n"; } });
    pyodide.runPython("import sys, os; sys.path.insert(0, '/pb3web'); os.chdir('/pb3web')");
    const code = pyodide.runPython("import pytest\npytest.main(['-q', '-rs', '--tier', 'smoke', '-p', 'no:cacheprovider', '--deselect', 'tests/test_app_menu_cli.py::TestBootstrap::test_detects_interrupted_previous_run', 'tests'])");
    const tail = out.trim().split("\n").slice(-4).join(" | ");
    detail += `pytest_smoke_exit=${code} | ${tail}`;
    rec("pyodide_pytest_smoke", code === 0, detail, performance.now() - t0);
  } catch (e) {
    rec("pyodide_pytest_smoke", false, "PROBE LỖI: " + String(e?.message ?? e).slice(0, 600) + ` http_fetch=${httpFetchCount}`, performance.now() - t0);
  }
}

// ---- 7) Fix picker .ipa: tĩnh + chạy thật (ZIP đổi tên .ipa qua bridge) ----
{
  const t0 = performance.now();
  const idx = fs.readFileSync(SITE + "/index.html", "utf8");
  const appjs = fs.readFileSync(SITE + "/app.js", "utf8");
  const tag = (id) => (idx.match(new RegExp(`<input[^>]*id="${id}"[^>]*>`)) || [""])[0];
  const checks = {
    file_input_no_accept: !/accept=/.test(tag("file-input")),
    batch_files_no_accept: !/accept=/.test(tag("batch-files")),
    translate_json_kept: /accept="\.json"/.test(tag("translate-json-file")),
    kb_import_kept: /\.sqlite/.test(tag("kb-import-file")),
    js_ext_check: /GAME_EXTS = \["\.ipa", "\.zip", "\.unity3d", "\.assets", "\.ttf", "\.json", "\.tar\.xz"\]/.test(appjs)
      && /gameExtOk\(f\.name\)/.test(appjs) && /gameExtError\(f\.name\)/.test(appjs),
  };
  const bad = Object.entries(checks).filter(([, v]) => !v).map(([k]) => k);
  let detail = Object.entries(checks).map(([k, v]) => `${k}=${v}`).join(" ");
  let runtimeOk = false;
  try {
    if (!pyodideProbe) throw new Error("probe Pyodide chưa boot (mục 6 fail)");
    const pyodide = pyodideProbe;
    pyodide.globals.set("pb3_post_progress", () => {});
    pyodide.runPython("import sys; sys.path.insert(0, '/pb3web')\nimport bridge\nbridge.set_progress_cb(pb3_post_progress)");
    const b = pyodide.globals.get("bridge");
    const boot = JSON.parse(b.boot());
    // ZIP fixture game1.zip đổi tên thành game1.ipa — bridge phải sniff nội
    // dung (ZIP) chứ không dựa vào đuôi .ipa.
    const zipBytes = fs.readFileSync("/home/hatch/workspace/pb3_fix/pb3/tests/fixtures/extract/game1.zip");
    pyodide.FS.mkdirTree("/tmp/input");
    pyodide.FS.writeFile("/tmp/input/game1.ipa", new Uint8Array(zipBytes));
    const listed = JSON.parse(b.list_entries("/tmp/input/game1.ipa"));
    const ext = JSON.parse(b.extract("/tmp/input/game1.ipa"));
    runtimeOk = boot.ok === true && listed.ok === true && listed.total === 11 &&
      ext.ok === true && ext.total_entries === 21 && ext.files.length === 6;
    detail += ` | boot_ok=${boot.ok} list_ipa_total=${listed.total} extract_entries=${ext.total_entries} files=${ext.files.length} runtime=${runtimeOk}`;
  } catch (e) {
    detail += " | RUNTIME LỖI: " + String(e?.message ?? e).slice(0, 300);
  }
  rec("picker_ipa", bad.length === 0 && runtimeOk, detail, performance.now() - t0);
}

results._failures = failures;
fs.writeFileSync(EXP + "/phase1_ci_harness.json", JSON.stringify(results, null, 2));
console.log(`\n=== XONG: ${Object.keys(results).length - 1} mục, ${failures} FAIL ===`);
process.exit(failures ? 1 : 0);
