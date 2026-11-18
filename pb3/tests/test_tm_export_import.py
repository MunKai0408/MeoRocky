"""Test T3: xuất / nhập / quản lý bộ nhớ dịch (TM) thành file.

- Logic TM thuần sống trong tm.js (script classic, Node-compatible):
  chạy trực tiếp bằng node qua exp/tm_roundtrip_test.js — test này
  chỉ gọi node rồi khóa exit code + số PASS, và khóa các điểm neo
  tĩnh: tm.js nằm trong precache + được nạp ở index.html, hộp TM có
  đủ id mới, nhãn "bộ nhớ dịch: N chuỗi" ở app.js còn nguyên (không
  thuộc phạm vi T3), buildTranslateTM/applyJobTM đã chuyển sang tm.js.
- Định dạng file TM được validate lại bằng Python trên 1 mẫu xuất
  (5 cặp dịch THẬT qua worker — xem exp/tm_roundtrip_test.js).
- Không gọi mạng thật, không cần IndexedDB / trình duyệt. Bỏ qua
  (skip) khi máy không có node.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent  # gốc repo

TM_IDS = [
    "tm-box", "tm-pair-select", "btn-tm-export", "btn-tm-import",
    "tm-import-file", "btn-tm-clear-pair", "tm-pair-count",
    "tm-list", "tm-info",
]

# 5 cặp en->vi dịch THẬT qua Cloudflare Worker (Workers AI) lúc
# 2026-11-18 ~05:47 +07 — month_used 150->233, ai_daily_used
# 2.14755->3.4299 (xem exp/tm_roundtrip_test.js). Dùng làm mẫu xuất.
REAL_EXPORT_SAMPLE = {
    "format": "meorocky-tm",
    "version": 1,
    "from": "en",
    "to": "vi",
    "exported_at": "2026-11-18T00:00:00.000Z",
    "count": 5,
    "entries": [
        {"source": "Beat the game on King difficulty.",
         "target": "Đánh bại trò chơi trên King khó khăn."},
        {"source": "Cheapskate", "target": "giá rẻ"},
        {"source": "His Winningness", "target": "Chiến thắng của"},
        {"source": "True Royalty", "target": "Hoàng gia đích thực"},
        {"source": "Win one game.", "target": "Chiến thắng 1 game"},
    ],
}


def validate_tm_export(obj):
    """Mirror bằng Python của tmParseImportText (tm.js): True nếu
    object là file TM version 1 hợp lệ, entries xếp theo source."""
    if not isinstance(obj, dict):
        return False
    if obj.get("format") != "meorocky-tm" or obj.get("version") != 1:
        return False
    if not obj.get("from") or not obj.get("to"):
        return False
    entries = obj.get("entries")
    if not isinstance(entries, list):
        return False
    if obj.get("count") != len(entries):
        return False
    seen = set()
    sources = []
    for e in entries:
        if not isinstance(e, dict):
            return False
        src, dst = e.get("source"), e.get("target")
        if not isinstance(src, str) or not isinstance(dst, str):
            return False
        if not src or not dst or src == dst or src in seen:
            return False
        seen.add(src)
        sources.append(src)
    return sources == sorted(sources)


class TestTmExportImport(unittest.TestCase):
    def test_node_roundtrip(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("không có node trên máy này")
        script = ROOT / "exp" / "tm_roundtrip_test.js"
        self.assertTrue(script.is_file(), "thiếu exp/tm_roundtrip_test.js")
        r = subprocess.run([node, str(script)], capture_output=True,
                           text=True, timeout=120)
        self.assertEqual(r.returncode, 0,
                         "node tm_roundtrip_test fail:\n" + r.stdout + r.stderr)
        self.assertIn("42 PASS, 0 FAIL", r.stdout)

    def test_node_roundtrip_with_real_9k_values(self):
        """Round-trip với 200 value nguồn THẬT của en.json 9K v1.0.6
        (bản dịch trong test vẫn là [FIXTURE] "VI: <gốc>"). Bỏ qua
        khi không có file mẫu 9K trong cây làm việc."""
        node = shutil.which("node")
        if not node:
            self.skipTest("không có node trên máy này")
        values = None
        for cand in (ROOT / "pb3" / "tests" / "fixtures" / "extract" / "9kings-v1.0.6-en.json",
                     Path.home() / "workspace" / "your_files" / "extract-9k-v106.zip"):
            if cand.is_file():
                if cand.suffix == ".json":
                    values = json.loads(cand.read_text(encoding="utf-8"))
                    values = list(values.values())[:200]
                else:
                    import zipfile
                    with zipfile.ZipFile(cand) as z:
                        values = list(json.loads(z.read("en.json")).values())[:200]
                break
        if values is None:
            self.skipTest("không có file mẫu en.json 9K")
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json",
                                        delete=False, encoding="utf-8") as f:
            json.dump(values, f, ensure_ascii=False)
            values_path = f.name
        try:
            r = subprocess.run(
                [node, str(ROOT / "exp" / "tm_roundtrip_test.js"),
                 "--values", values_path],
                capture_output=True, text=True, timeout=120)
        finally:
            Path(values_path).unlink(missing_ok=True)
        self.assertEqual(r.returncode, 0,
                         "node tm_roundtrip_test --values fail:\n" + r.stdout + r.stderr)
        self.assertIn("9K-scale: TM sau nhập đủ 200 cặp", r.stdout)

    def test_tm_js_wired_in_precache_and_html(self):
        sw = (ROOT / "sw.js").read_text(encoding="utf-8")
        m = re.search(r"const PRECACHE_URLS = \[(.*?)\];", sw, re.S)
        self.assertIsNotNone(m, "không tìm thấy PRECACHE_URLS trong sw.js")
        self.assertIn('"tm.js"', m.group(1), "sw.js precache thiếu tm.js")
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn('<script src="tm.js"></script>', html)
        for tid in TM_IDS:
            self.assertIn('id="%s"' % tid, html, "index.html thiếu id " + tid)
        # Không trùng id mới với id cũ.
        ids = re.findall(r'id="([^"]+)"', html)
        self.assertEqual(len(ids), len(set(ids)), "index.html có id trùng")

    def test_translate_summary_label_untouched(self):
        """Nhãn tổng kết tab Dịch KHÔNG thuộc T3 (agent web riêng):
        T3 không được sửa — khóa nội dung tại đây."""
        app = (ROOT / "app.js").read_text(encoding="utf-8")
        self.assertIn('" (Google: " + googleCount + " đoạn', app)
        self.assertIn('" chuỗi · bộ nhớ dịch: " + job.tmCount + " chuỗi', app)

    def test_tm_functions_moved_to_tm_js(self):
        tm = (ROOT / "tm.js").read_text(encoding="utf-8")
        app = (ROOT / "app.js").read_text(encoding="utf-8")
        for fn in ("function buildTranslateTM", "function applyJobTM",
                   "function tmBuildExport", "function tmParseImportText",
                   "function tmMergeImport", "function tmMakeImportJob",
                   "function tmRemovePair", "function tmClearPair",
                   "function tmPairsByLang", "function tmCollectPairs"):
            self.assertIn(fn, tm, "tm.js thiếu " + fn)
        self.assertNotIn("function buildTranslateTM", app)
        self.assertNotIn("function applyJobTM", app)
        # app.js vẫn gọi qua tên toàn cục.
        self.assertIn("buildTranslateTM(to, from)", app)
        self.assertIn("applyJobTM(job, tm)", app)

    def test_export_format_validates_in_python(self):
        self.assertTrue(validate_tm_export(REAL_EXPORT_SAMPLE))
        bad = json.loads(json.dumps(REAL_EXPORT_SAMPLE))
        bad["count"] = 4
        self.assertFalse(validate_tm_export(bad))
        bad = json.loads(json.dumps(REAL_EXPORT_SAMPLE))
        bad["entries"] = list(reversed(bad["entries"]))
        self.assertFalse(validate_tm_export(bad))  # chưa xếp theo source
        bad = json.loads(json.dumps(REAL_EXPORT_SAMPLE))
        bad["entries"][0]["target"] = bad["entries"][0]["source"]
        self.assertFalse(validate_tm_export(bad))  # source == target
        bad = json.loads(json.dumps(REAL_EXPORT_SAMPLE))
        bad["version"] = 2
        self.assertFalse(validate_tm_export(bad))


if __name__ == "__main__":
    unittest.main()
