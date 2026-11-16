import unittest

from tests import helpers  # noqa: F401  (them ROOT vao sys.path)
from core import model as M
from core.errors import PB3Error


def sample_project():
    ev = [M.Evidence("magic", "UnityFS", 1.0, "byte 0..8 = UnityFS")]
    f = M.FileRef(id="f1", source=M.SourcePath("/root/x.unity3d"), size=10, kind="unity3d",
                  support="M2", evidence=ev)
    c = M.Container(id="c1", file_id="f1", kind="unityfs", support="M3")
    a = M.Asset(id="a1", container_id="c1", inner=M.InnerPath("c1", "resources.assets"), kind="assets")
    r = M.Resource(id="r1", asset_id="a1", kind="i2languages", locale="vi",
                   metadata={"tbl": ["a", {"b": 1}]})
    return M.Project(name="demo", files=[f], containers=[c], assets=[a], resources=[r],
                     locales=[M.Locale("vi", aliases=["vi-VN"], script="Latn", display="Tiếng Việt", numeric_id=3)], output=M.OutputPath("out/x.unity3d"))


class TestModel(unittest.TestCase):
    def test_roundtrip_project(self):
        p = sample_project()
        self.assertEqual(p.validate(), [])
        q = M.loads(M.Project, M.dumps(p))
        self.assertEqual(q, p)
        self.assertEqual(M.dumps(q), M.dumps(p))

    def test_evidence_weighted_confidence(self):
        e = [M.Evidence("magic", "UnityFS", 1.0, "r"), M.Evidence("dirname", "aa", 0.25, "r2")]
        self.assertEqual(M.confidence(e), 1.0)                       # chặn trần 1.0
        self.assertAlmostEqual(M.confidence(e[1:]), 0.25)
        self.assertEqual(M.confidence([]), 0.0)
        self.assertEqual(M.Evidence.from_dict(e[0].to_dict()), e[0])
        for bad in (1.5, -0.1, True, "0.5", float("nan")):
            with self.assertRaises(PB3Error, msg=repr(bad)):
                M.Evidence("k", "v", bad)
        with self.assertRaises(PB3Error):
            M.Evidence("", "v", 0.1)

    def test_support_level_m0_m4(self):
        self.assertEqual(M.SupportLevel.ALL, ("M0", "M1", "M2", "M3", "M4"))
        self.assertLess(M.SupportLevel.rank("M1"), M.SupportLevel.rank("M3"))
        self.assertEqual(M.SupportLevel.label("M2"), "M2 EXTRACT")
        for bad in ("M5", "supported", "", None, 2):
            self.assertFalse(M.SupportLevel.valid(bad))
        with self.assertRaises(PB3Error):
            M.SupportLevel.rank("read_only")

    def test_locale_spec_fields(self):
        l = M.Locale("zh-Hans", aliases=["zh_CN", "Chinese"], script="Hans", numeric_id=1)
        self.assertEqual(M.Locale.from_dict(l.to_dict()), l)
        with self.assertRaises(PB3Error):
            M.Locale("")
        with self.assertRaises(PB3Error):
            M.Locale("en", numeric_id=True)

    def test_unsafe_paths_rejected(self):
        for bad in ("../x", "/abs", "a//b", "a\\b", "a/./b", "", "C:/x", "a\x00b"):
            with self.assertRaises(PB3Error, msg=bad) as cm:
                M.InnerPath("c", bad)
            self.assertEqual(cm.exception.code, "E_UNSAFE_PATH")
        with self.assertRaises(PB3Error):
            M.OutputPath("../out")
        M.SourcePath("/root/abs/ok")

    def test_bad_data_gives_E_CORRUPT(self):
        d = sample_project().to_dict()
        cases = [dict(d, model_version=99), dict(d, name=5), dict(d, bogus=1),
                 {k: v for k, v in d.items() if k != "name"}, dict(d, files="x"), "khong phai dict"]
        for bad in cases:
            with self.assertRaises(PB3Error) as cm:
                M.Project.from_dict(bad)
            self.assertEqual(cm.exception.code, "E_CORRUPT", bad if not isinstance(bad, dict) else "")
        with self.assertRaises(PB3Error):
            M.loads(M.Project, "{khong json")
        with self.assertRaises(PB3Error):
            M.Evidence("")
        with self.assertRaises(PB3Error):
            M.Container(id="c", support="supported")      # mức cũ không còn hợp lệ
        d2 = dict(d, files=[dict(d["files"][0], evidence="x")])
        with self.assertRaises(PB3Error):
            M.Project.from_dict(d2)

    def test_validate_finds_dangling_and_duplicates(self):
        p = sample_project()
        p.assets.append(M.Asset(id="a1", container_id="zz", inner=M.InnerPath("zz", "x")))
        p.resources.append(M.Resource(id="r2", asset_id="nope"))
        probs = " | ".join(p.validate())
        self.assertIn("asset id trùng: a1", probs)
        self.assertIn("container lạ: 'zz'", probs)
        self.assertIn("asset lạ: 'nope'", probs)

    def test_bool_not_accepted_as_int(self):
        d = sample_project().to_dict()
        d["files"][0]["size"] = True
        with self.assertRaises(PB3Error):
            M.Project.from_dict(d)


if __name__ == "__main__":
    unittest.main()
