"""Phụ lục B của đặc tả (skeleton plugin) PHẢI chạy nguyên văn trên SDK. Bản sao: tests/appendix_b_plugin.py."""
import importlib.util
import unittest

from tests.helpers import ROOT
from sdk.api import PluginContext
from sdk.manifest import parse_manifest
from sdk.vfs import MemoryVFS


def load():
    spec = importlib.util.spec_from_file_location("appendix_b_plugin", str(ROOT / "tests/appendix_b_plugin.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)                 # `from sdk.api import Plugin, api_version` phải import được
    return mod


class TestAppendixB(unittest.TestCase):
    def test_runs_verbatim(self):
        mod = load()
        with open(str(ROOT / "tests/appendix_b_plugin.py"), encoding="utf-8") as f:
            self.assertLessEqual(len(f.read().splitlines()), 80)          # Phụ lục B: <= 80 dòng
        p = mod.MyEnginePlugin()
        m = parse_manifest(mod.MANIFEST)
        self.assertEqual((m.id, m.api_version, m.read_only, m.maturity), ("myengine_text", 3, True, "M2"))
        vfs = MemoryVFS({"Data/lang.txt": b"# c\nHELLO=Xin ch\xc3\xa0o\nHELLO=dup\nBYE = Bye\nrandom",
                         "Data/readme.md": b"a=b", "Data/bin.txt": b"a=\x00b"})
        ctx = PluginContext(vfs, locale="vi").for_plugin(p.manifest)
        ev = p.detect(ctx)
        self.assertEqual(ev[0].kind, "extension")
        cands = p.list_candidates(ctx)
        self.assertEqual([c.path for c in cands], ["Data/lang.txt"])
        self.assertEqual(p.probe(ctx, cands[0])["pairs"], 3)
        t = p.parse(ctx, cands[0])
        self.assertEqual([(e.key, e.value) for e in t.entries], [("HELLO", "Xin chào"), ("BYE", "Bye")])
        self.assertEqual((t.locale, t.conflicts), ("vi", [{"key": "HELLO", "line": 3}]))
        self.assertEqual(t.to_key_values(), {"HELLO": "Xin chào", "BYE": "Bye"})
        # không có build() -> chỉ đọc, và build bị chặn đúng mã
        from core.errors import PB3Error
        with self.assertRaises(PB3Error) as cm:
            p.build(ctx, t, b"")
        self.assertEqual(cm.exception.code, "E_WRITE_DISABLED")

    def test_no_locale_requested(self):
        mod = load()
        p = mod.MyEnginePlugin()
        ctx = PluginContext(MemoryVFS({"l.txt": b"a=b"})).for_plugin(p.manifest)
        self.assertIsNone(p.parse(ctx, p.list_candidates(ctx)[0]).locale)


if __name__ == "__main__":
    unittest.main()
