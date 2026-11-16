// pyodide_smoke.mjs — Chạy pytest tier smoke qua Pyodide trong Node.
// Dùng để kiểm chứng đúng runtime trình duyệt (Pyodide đã vendor, micropip
// cài pytest) mà không cần browser thật. KHÔNG phải gate deploy: job CI
// chạy nó chỉ để báo cáo; deploy chỉ cần job unit + assets xanh.
//
// Chạy local (sandbox):  node .github/scripts/pyodide_smoke.mjs [site-root]
//   site-root mặc định tự dò: thư mục hiện tại nếu có pyodide/pyodide.mjs,
//   nếu không thì ./site (layout workspace sandbox).
// Chạy CI: node .github/scripts/pyodide_smoke.mjs (cwd = repo root).
//
// BỎ QUA 1 TEST (deselect, có lý do, không giấu):
//   tests/test_app_menu_cli.py::TestBootstrap::test_detects_interrupted_previous_run
// Wasm không có bảng process: os.kill(pid, 0) dưới Pyodide không bao giờ
// raise ProcessLookupError nên core/runstate.py::pid_alive() luôn trả True
// (mặc định an toàn khi errno lạ) → trạng thái 'other_running' thay vì
// 'interrupted'. Đây là giới hạn của môi trường wasm, KHÔNG phải lỗi sản
// phẩm — test này vẫn chạy thật ở job unit/assets trên Linux.
// micropip cài pytest từ package index của Pyodide (CDN jsDelivr) — chỉ
// dùng cho job kiểm chứng này, app chính không chạm CDN.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
function detectRoot() {
  const arg = process.argv[2];
  if (arg) return path.resolve(arg);
  if (fs.existsSync(path.join(process.cwd(), "pyodide", "pyodide.mjs"))) return process.cwd();
  if (fs.existsSync(path.join(process.cwd(), "site", "pyodide", "pyodide.mjs"))) return path.join(process.cwd(), "site");
  // Mặc định: script ở <root>/.github/scripts/ → root là 3 cấp lên.
  return path.resolve(HERE, "..", "..", "..");
}
const ROOT = detectRoot();

const DESELECT = "tests/test_app_menu_cli.py::TestBootstrap::test_detects_interrupted_previous_run";

const { loadPyodide } = await import(path.join(ROOT, "pyodide", "pyodide.mjs"));
const pyodide = await loadPyodide({ indexURL: path.join(ROOT, "pyodide") + "/" });
console.log("pyodide=" + pyodide.version);
await pyodide.loadPackage("lz4");
await pyodide.loadPackage("micropip");
await pyodide.pyimport("micropip").install("pytest");
console.log("pytest (micropip) OK");

for (const mf of ["pb3/files.json", "pb3/tests.json"]) {
  const man = JSON.parse(fs.readFileSync(path.join(ROOT, mf), "utf8"));
  let skipped = [];
  for (const rel of man.files) {
    const src = path.join(ROOT, rel);
    // File nhị phân chưa lên main (20 file trong Release fixtures-v1) sẽ
    // vắng mặt ở CI checkout — BỎ QUA thay vì crash ENOENT. Test nào cần
    // chúng sẽ SKIP qua marker needs_fixture của conftest (đã gắn đủ 126
    // test); test không cần chúng chạy bình thường. Khi Release tồn tại,
    // job assets vẫn tải đủ 20 file — job này không tải Release để giữ
    // nhẹ và độc lập (chỉ kiểm chứng runtime trình duyệt).
    if (!fs.existsSync(src)) { skipped.push(rel); continue; }
    const buf = fs.readFileSync(src);
    const target = "/pb3web/" + rel.slice(4);
    pyodide.FS.mkdirTree(target.slice(0, target.lastIndexOf("/")));
    pyodide.FS.writeFile(target, new Uint8Array(buf));
  }
  console.log(mf + ": " + man.files.length + " file (" + skipped.length + " thiếu, bỏ qua: " + skipped.join(", ") + ")");
}
const b64 = fs.readFileSync(path.join(ROOT, "fonts", "BeVietnamPro-Regular.ttf.b64"), "utf8").replace(/\s+/g, "");
pyodide.FS.mkdirTree("/pb3web/fonts");
pyodide.FS.writeFile("/pb3web/fonts/BeVietnamPro-Regular.ttf", new Uint8Array(Buffer.from(b64, "base64")));
console.log("font: 132948 byte (kỳ vọng, từ .b64)");

pyodide.setStdout({ batched: (s) => process.stdout.write(s + "\n") });
pyodide.setStderr({ batched: (s) => process.stderr.write(s + "\n") });
pyodide.runPython("import sys, os; sys.path.insert(0, '/pb3web'); os.chdir('/pb3web')");
const code = pyodide.runPython(
  "import pytest\n" +
  "pytest.main(['-q', '-rs', '--tier', 'smoke', '-p', 'no:cacheprovider',\n" +
  "             '--deselect', '" + DESELECT + "', 'tests'])\n");
console.log("pytest smoke exit: " + code);
process.exit(code === 0 ? 0 : 1);
