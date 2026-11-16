"""Luật X4: mọi `raise PB3Error(...)` trong sdk/unity.py + sdk/containers.py
phải kèm `where=` (đường dẫn đầy đủ file::entry::object, hoặc "?" khi thật sự
không có ngữ cảnh) — để lỗi luôn chỉ đúng vị trí."""
import ast
import importlib.util
import unittest
from pathlib import Path

from tests.helpers import ROOT
from tests.tiering import meta

TARGETS = [ROOT / "sdk" / "unity.py", ROOT / "sdk" / "containers.py"]
_RAISE_NAMES = ("PB3Error", "error_from_exception")
_FX = ROOT / "tests" / "fixtures" / "unity"
_spec = importlib.util.spec_from_file_location("pb3_build_fixtures_x4", str(_FX / "build_fixtures.py"))
bf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bf)


def _missing_where(path):
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(path))
    missing = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise) or node.exc is None:
            continue
        call = node.exc
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else "")
        if name in _RAISE_NAMES and "where" not in {kw.arg for kw in call.keywords}:
            seg = (ast.get_source_segment(src, call) or "").replace("\n", " ")[:70]
            missing.append("%s:%d %s" % (path.name, node.lineno, seg))
    return missing


@meta
class TestWhereOnRaises(unittest.TestCase):
    def test_all_raises_carry_where(self):
        bad = []
        for p in TARGETS:
            bad.extend(_missing_where(p))
        self.assertEqual(bad, [], "raise thiếu where=:\n" + "\n".join(bad))

    def test_where_values_are_full_vpaths(self):
        # Mẫu: parse + get_entry lỗi phải ghi where dạng file::entry.
        from sdk import unity as U
        from tests.fixtures.unity import build_fixtures as bf
        data = bf.build_bundle(version=7, entries=[("resources.assets", 0, b"xx")])
        bundle = U.parse_bundle(data, where="game.ipa::level.unity3d")
        self.assertEqual(bundle.where, "game.ipa::level.unity3d")
        with self.assertRaises(U.PB3Error) as cm:
            bundle.get_entry("khong_co.assets")
        self.assertEqual(cm.exception.where, "game.ipa::level.unity3d::khong_co.assets")
        sf = U.parse_serialized(bf.build_serialized_file(
            version=21, objects=[(1001, 0, bf.text_asset_data("hi", "hello"))],
            types=[(49, None)]), where="resources.assets")
        self.assertEqual(sf.where, "resources.assets")
        with self.assertRaises(U.PB3Error) as cm:
            sf.get_object(9999)
        self.assertEqual(cm.exception.where, "resources.assets::9999")


if __name__ == "__main__":
    unittest.main()
