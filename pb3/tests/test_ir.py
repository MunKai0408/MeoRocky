import ast
import unittest

from tests.helpers import ROOT, imports_of
from core import model as M
from core.errors import PB3Error
from sdk import ir
from sdk.types import LocEntry, LocTable, SourceRef


def ref(enc="utf-8"):
    return SourceRef(["Payload/G.app/Data/x.unity3d", "resources.assets"], "i2/en", 16, 32, enc,
                     {"path_id": 7})


def make_doc():
    t1 = LocTable([LocEntry("a", "Xin chào\n  ", "vi", "native", "ok", ref(), ["ghi chú"]),
                   LocEntry("a", "dup", "vi", "native", "conflict", ref()),
                   LocEntry("b", "", "vi", "generated", "empty", ref("latin-1"))],
                  "vi", "p1", {"flavor": "csv"}, {"entries": 3}, [{"key": "a"}], [{"msg": "w"}])
    t2 = LocTable([LocEntry("c", "日本語", "ja", "rowindex", "ok", ref("shift_jis"))], "ja", "p2")
    return ir.IRDocument(producer=M.Provenance(plugin_id="p"), tables=[t1, t2], metadata={"x": [1, {"y": None}]})


class TestIR(unittest.TestCase):
    def test_roundtrip_exact(self):
        d = make_doc()
        e = ir.loads(ir.dumps(d))
        self.assertEqual(e, d)
        self.assertEqual([x.key for x in e.entries()], ["a", "a", "b", "c"])        # thứ tự + key trùng
        self.assertEqual(e.entries()[0].value, "Xin chào\n  ")                       # không strip
        self.assertEqual(e.tables[0].entries[0].source.container_stack[1], "resources.assets")
        self.assertEqual(e.tables[0].entries[0].source.extra, {"path_id": 7})
        self.assertEqual(e.tables[0].entries[0].notes, ["ghi chú"])

    def test_spec_version_is_2(self):
        self.assertEqual(ir.IR_VERSION, (2, 0))
        self.assertEqual(ir.IRDocument().version, "2.0")

    def test_version_compat(self):
        d = make_doc().to_dict()
        ir.IRDocument.from_dict(dict(d, version="2.0"))
        for bad in ("1.0", "3.0", "2.1", "x", None, "2"):
            with self.assertRaises(PB3Error, msg=str(bad)) as cm:
                ir.IRDocument.from_dict(dict(d, version=bad))
            self.assertEqual(cm.exception.code, "E_CORRUPT")
        with self.assertRaises(PB3Error):
            ir.IRDocument.from_dict(dict(d, schema="khac"))
        self.assertEqual(ir.check_compat("2.1", reader=(2, 3)), (2, 1))

    def test_missing_or_bad_data(self):
        d = make_doc().to_dict()
        t = d["tables"][0]
        cases = [dict(d, tables="x"), dict(d, bogus=1), dict(d, tables=[dict(t, entries=[{"key": "k"}])]),
                 dict(d, tables=[dict(t, entries=[dict(t["entries"][0], status="vui")])]),
                 dict(d, tables=[dict(t, entries=[dict(t["entries"][0], key_origin="x")])]),
                 dict(d, tables=[dict(t, entries=[dict(t["entries"][0], source=None)])]),
                 dict(d, tables=[dict(t, conflicts=["x"])])]
        for bad in cases:
            with self.assertRaises(PB3Error) as cm:
                ir.IRDocument.from_dict(bad)
            self.assertEqual(cm.exception.code, "E_CORRUPT")
        with self.assertRaises(PB3Error):
            ir.loads("{không json")

    def test_duplicates_for_locale_unencodable(self):
        d = make_doc()
        self.assertEqual(d.duplicates(), [("p1", "vi", "a")])
        self.assertEqual([e.key for e in d.for_locale("ja")], ["c"])
        self.assertEqual(len(d.for_locale("vi")), 3)
        d.tables[1].entries.append(LocEntry("z", "日本語", "ja", "native", "ok", ref("ascii")))
        self.assertEqual(d.unencodable(), ["z"])

    def test_core_never_imports_sdk(self):
        for p in sorted((ROOT / "core").glob("*.py")) + sorted((ROOT / "ui").glob("*.py")):
            for name, _ in imports_of(p):
                self.assertNotEqual(name.split(".")[0], "sdk", str(p))


if __name__ == "__main__":
    unittest.main()
