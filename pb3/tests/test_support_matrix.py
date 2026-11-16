"""Claude 09 (support): ma trận hỗ trợ sinh tự động (sdk/support_matrix.py).

Fixture xác định, không os.urandom, không mạng. Registry dựng trên BẢN SAO
dự án (tests.helpers.copy_project) để không đụng cây thật.
"""
import argparse
import contextlib
import datetime
import io
import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace

from core import config
from core.paths import Paths
from registry.registry import build_registry
from sdk import support_matrix as sm
from services import plugin_manager as pm
from tests.helpers import TmpCase, copy_project


def make_app(root):
    paths = Paths(str(root))
    (paths.config_dir).mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(paths=paths, config=config.load_config(paths, apply_paths=False))


def run_cmd(app, ns):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = pm.run_cli(app, ns)
    return rc, out.getvalue(), err.getvalue()


class MatrixCase(TmpCase):
    # Ngày test cố định 2026-11-15 (UTC): sdk/support_matrix.py::_corpus_date
    # lấy ngày từ mtime của tests/corpus/<id>.json, mà git checkout đặt lại
    # mtime thành ngày checkout nên test lệch ngày trên máy mới/CI. Test tự
    # đặt mtime file corpus trong bản sao (không sửa cây thật, không nới
    # assertion, không đổi expected) — timezone không phải nguyên nhân vì
    # _corpus_date đã đổi sang UTC.
    CORPUS_MTIME = datetime.datetime(2026, 11, 15, 12, 0, 0,
                                     tzinfo=datetime.timezone.utc).timestamp()

    def setUp(self):
        super().setUp()
        self.proj = copy_project(self.tmp)
        corpus = self.proj / "tests" / "corpus" / "json_text.json"
        if corpus.is_file():
            os.utime(corpus, (self.CORPUS_MTIME, self.CORPUS_MTIME))

    def matrix_inputs(self, proj=None):
        """(rows, manifests) cho sm.generate từ Registry đã dựng (tầng test
        dựng Registry như tầng services; sdk không import registry #129)."""
        reg = build_registry(str(proj or self.proj))
        rows = reg.rows()
        manifests = {rec.plugin_id: getattr(rec, "manifest", None)
                     for rec in reg.records()}
        return rows, manifests

    def plugin_table_section(self, markdown):
        start = markdown.index("## Bảng plugin")
        end = markdown.index("## Bảng engine", start)
        return markdown[start:end]


class TestGenerate(MatrixCase):
    def test_warning_header_and_no_m4(self):
        markdown, data = sm.generate(self.proj, *self.matrix_inputs())
        self.assertIn(sm.WARNING, markdown)
        self.assertIn("Không có mục nào đạt M4", markdown)
        section = self.plugin_table_section(markdown)
        self.assertNotIn("| M4 |", section)
        self.assertEqual([p["id"] for p in data["plugins"]
                          if p["maturity_effective"] == "M4"], [])

    def test_effective_maturity_uses_corpus_not_declared(self):
        # json_text khai M3 và có tests/corpus/json_text.json PASS -> hiệu lực M3.
        _md, data = sm.generate(self.proj, *self.matrix_inputs())
        row = next(p for p in data["plugins"] if p["id"] == "json_text")
        self.assertEqual((row["maturity"], row["maturity_effective"]), ("M3", "M3"))
        self.assertEqual(row["test_date"], "2026-11-15")
        self.assertEqual(row["corpus"].get("read"), "PASS")
        # Xóa bằng chứng -> hiệu lực hạ xuống M2 (sàn), ngày test "chưa có".
        (self.proj / "tests" / "corpus" / "json_text.json").unlink()
        _md2, data2 = sm.generate(self.proj, *self.matrix_inputs())
        row2 = next(p for p in data2["plugins"] if p["id"] == "json_text")
        self.assertEqual(row2["maturity_effective"], "M2")
        self.assertEqual(row2["test_date"], "chưa có")

    def test_three_honest_lists(self):
        markdown, data = sm.generate(self.proj, *self.matrix_inputs())
        # Đã xác minh trên game thật: CHỈ Unity inspect trên IPA thật 9 Kings v1.0.5.
        self.assertEqual(len(data["real_game"]), 1)
        self.assertIn("9 Kings", data["real_game"][0]["muc"])
        self.assertIn("inspect", data["real_game"][0]["muc"])
        self.assertIn("handoff/account_05.md", data["real_game"][0]["nguon"])
        # Patch Unity chỉ mới test fixture -> không nằm trong danh sách game thật.
        self.assertNotIn("patch", data["real_game"][0]["muc"].lower())
        # Mới test fixture: mọi plugin còn lại.
        self.assertIn("json_text", data["fixture_only"])
        self.assertIn("godot_pck", data["fixture_only"])
        # Chưa hỗ trợ kèm lý do.
        unsupported = " ".join(u["muc"] + " " + u["ly_do"] for u in data["unsupported"])
        for needle in ("7z", ".locmeta", "PCK v0/v3", "Oodle", "v<15",
                       "mã hóa", "Cocos"):
            self.assertIn(needle, unsupported)
        self.assertIn("7z", markdown)

    def test_no_kb_means_zero_games_tested(self):
        _md, data = sm.generate(self.proj, *self.matrix_inputs())
        self.assertTrue(all(p["games_tested"] == 0 for p in data["plugins"]))

    def test_generate_without_rows_has_empty_plugin_table_and_note(self):
        # sdk không được import registry (#129): rows=None thì bảng plugin
        # trống kèm ghi chú, ma trận vẫn sinh được (cảnh báo + bảng khác đủ).
        markdown, data = sm.generate(self.proj)
        self.assertEqual(data["plugins"], [])
        self.assertIn(sm.WARNING, markdown)
        self.assertIn("Chưa nạp registry", markdown)
        self.assertIn("ZIP / IPA", markdown)

    def test_kb_stats_fill_games_tested(self):
        from sdk.kb import KB
        kb_path = self.proj / "kb" / "pb3_kb.sqlite"
        kb_path.parent.mkdir(parents=True, exist_ok=True)
        kb = KB(str(kb_path))
        try:
            kb.record_plugin_stats("json_text", "1.0.0", True)
            kb.record_plugin_stats("json_text", "1.0.0", False)
        finally:
            kb.close()
        _md, data = sm.generate(self.proj, *self.matrix_inputs())
        row = next(p for p in data["plugins"] if p["id"] == "json_text")
        self.assertEqual(row["games_tested"], 2)

    def test_generate_is_deterministic(self):
        rows, manifests = self.matrix_inputs()
        md1, _d1 = sm.generate(self.proj, rows=rows, manifests=manifests)
        md2, _d2 = sm.generate(self.proj, rows=rows, manifests=manifests)
        self.assertEqual(md1, md2)

    def test_tables_cover_containers_and_unity(self):
        markdown, data = sm.generate(self.proj, *self.matrix_inputs())
        self.assertTrue(any("ZIP / IPA" in c["format"] for c in data["containers"]))
        self.assertTrue(any("Godot PCK" in c["format"] for c in data["containers"]))
        self.assertTrue(any("Unreal PAK" in c["format"] for c in data["containers"]))
        self.assertTrue(any("UnityFS v6/v7/v8" in u["format"] for u in data["unity"]))
        self.assertTrue(any("SerializedFile v15" in u["format"] for u in data["unity"]))
        self.assertIn("UnityFS v6/v7/v8", markdown)


class TestWriteMatrix(MatrixCase):
    def test_write_matrix_writes_docs_file(self):
        dest = sm.write_matrix(self.proj, *self.matrix_inputs())
        self.assertEqual(dest, str(self.proj / "docs" / "SUPPORT_MATRIX.md"))
        path = Path(dest)
        self.assertTrue(path.is_file())
        markdown, _data = sm.generate(self.proj, *self.matrix_inputs())
        self.assertEqual(path.read_text(encoding="utf-8"), markdown)

    def test_support_cli_prints_and_writes(self):
        app = make_app(self.proj)
        # Bản sao có sẵn docs/SUPPORT_MATRIX.md (đã sinh ở cây thật): xóa
        # trước để kiểm được --write thật sự tạo file.
        (self.proj / "docs" / "SUPPORT_MATRIX.md").unlink()
        rc, out, err = run_cmd(app, argparse.Namespace(
            command="support", action=None, args=[], write=False))
        self.assertEqual(rc, 0, err)
        self.assertIn(sm.WARNING, out)
        self.assertFalse((self.proj / "docs" / "SUPPORT_MATRIX.md").exists())
        rc, out, err = run_cmd(app, argparse.Namespace(
            command="support", action=None, args=[], write=True))
        self.assertEqual(rc, 0, err)
        self.assertTrue((self.proj / "docs" / "SUPPORT_MATRIX.md").is_file())
        self.assertIn("Đã ghi ma trận hỗ trợ", out)


if __name__ == "__main__":
    unittest.main()
