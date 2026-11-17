"""Test dong goi lai IPA (M2 — sdk/repack.py + truong "output" cua
sdk/rebuild.py + op bridge.repack_ipa).

Fixture TU DUNG (IPA tong hop bang zipfile, bytes tu viet); khong dung
mang, khong can IPA that — tru test 9K cuoi (skip neu thieu IPA, env
NINEKINGS_IPA hoac ~/workspace/test-ipas/9kings-v1.0.6.ipa) + test
bridge (skip neu thieu IPA).
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

import bridge  # noqa: E402

bridge.WEB_ROOT = str(ROOT)
json.loads(bridge.boot())

from core.errors import PB3Error  # noqa: E402
from core.limits import Limits  # noqa: E402
from sdk import rebuild as _rb  # noqa: E402
from sdk import repack as _repack  # noqa: E402
from sdk import unity as u  # noqa: E402
from tests.tiering import slow  # noqa: E402

LIM = Limits()
BE = u.UnityBackend("AUTO", LIM)

APP = "Payload/Test.app/"
INFO_PLIST = APP + "Info.plist"
ASSETS = APP + "Data/resources.assets"
BUNDLE = (APP + "Data/Raw/aa/iOS/"
          "localization-string-tables-english(en)_assets_all.bundle")


def _nine_kings_ipa():
    env = os.environ.get("NINEKINGS_IPA")
    if env:
        return Path(env)
    return Path.home() / "workspace" / "test-ipas" / "9kings-v1.0.6.ipa"


def _synthetic_ipa(tmp, name="test.ipa"):
    """IPA tong hop: 1 app + Info.plist + 2 don vi + 3 file khac (1
    stored, 1 deflate, 1 o META-INF) + 1 entry thu muc rong."""
    path = os.path.join(tmp, name)
    entries = [
        (APP, b"", zipfile.ZIP_STORED),
        (INFO_PLIST, b"<?xml version='1.0'?><plist><dict/></plist>",
         zipfile.ZIP_DEFLATED),
        (ASSETS, b"ASSETS-v1-" + b"A" * 1000, zipfile.ZIP_DEFLATED),
        (BUNDLE, b"BUNDLE-v1-" + b"B" * 500, zipfile.ZIP_STORED),
        (APP + "Test", b"EXE-v1", zipfile.ZIP_DEFLATED),
        (APP + "_CodeSignature/CodeResources",
         b"SIGNATURE-v1", zipfile.ZIP_DEFLATED),
        ("META-INF/MANIFEST.MF", b"Manifest-Version: 1.0\n",
         zipfile.ZIP_STORED),
    ]
    with zipfile.ZipFile(path, "w") as zf:
        for fname, data, method in entries:
            zf.writestr(fname, data, compress_type=method)
    return path, {fname: data for fname, data, _ in entries}


def _sha_map(path):
    out = {}
    with zipfile.ZipFile(path, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            out[info.filename] = hashlib.sha256(zf.read(info)).hexdigest()
    return out


class TestZipEntryForUnit(unittest.TestCase):
    def test_bundle_vpath_strips_cab(self):
        self.assertEqual(
            _repack.zip_entry_for_unit(
                BUNDLE + "::CAB-7f58a1d076a0579bc99ac19d9e397afa"),
            BUNDLE)

    def test_plain_vpath_unchanged(self):
        self.assertEqual(_repack.zip_entry_for_unit(ASSETS), ASSETS)

    def test_file_unit_rejected(self):
        for bad in (None, "", "<file>"):
            with self.assertRaises(ValueError):
                _repack.zip_entry_for_unit(bad)


class TestRepackSynthetic(unittest.TestCase):
    def test_roundtrip_bytes_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, _orig = _synthetic_ipa(tmp)
            before = _sha_map(src)
            new_assets = b"ASSETS-v2-" + b"Z" * 1200
            new_bundle = b"BUNDLE-v2-" + b"Y" * 600
            res = _repack.repack_ipa(
                Path(src).read_bytes(),
                {ASSETS: new_assets, BUNDLE: new_bundle},
                out_dir=os.path.join(tmp, "out"))
            self.assertTrue(res["ok"])
            self.assertEqual(res["filename"], "game-mod.ipa")
            self.assertEqual(res["replaced"], sorted([ASSETS, BUNDLE]))
            self.assertEqual(res["entries"], 7)
            self.assertTrue(all(c["passed"] for c in res["checks"]),
                            res["checks"])
            self.assertEqual(
                [c["name"] for c in res["checks"]],
                ["zip_integrity", "entry_list", "payload_structure",
                 "info_plist_intact", "replaced_entries",
                 "unchanged_entries"])
            dest = res["dest"]
            with zipfile.ZipFile(dest, "r") as zf:
                # Thu tu entry giong het goc.
                self.assertEqual(
                    [i.filename for i in zf.infolist()],
                    [i.filename for i in
                     zipfile.ZipFile(src, "r").infolist()])
                self.assertEqual(zf.read(ASSETS), new_assets)
                self.assertEqual(zf.read(BUNDLE), new_bundle)
            after = _sha_map(dest)
            for name, sha in before.items():
                if name in (ASSETS, BUNDLE):
                    self.assertNotEqual(after[name], sha)
                else:
                    self.assertEqual(after[name], sha,
                                     "entry chua sua doi sha: %s" % name)
            # File goc khong doi.
            self.assertEqual(_sha_map(src), before)

    def test_roundtrip_path_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, _orig = _synthetic_ipa(tmp, name="mygame.ipa")
            res = _repack.repack_ipa(
                src, {ASSETS: b"ASSETS-v2"},
                out_dir=os.path.join(tmp, "out"))
            self.assertEqual(res["filename"], "mygame-mod.ipa")
            self.assertTrue(all(c["passed"] for c in res["checks"]))

    def test_rejects_non_ipa_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "plain.zip")
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("a.txt", b"hello")
            with self.assertRaises(PB3Error) as ctx:
                _repack.repack_ipa(path, {"a.txt": b"bye"},
                                   out_dir=os.path.join(tmp, "o"))
            self.assertEqual(ctx.exception.code, "E_UNSUPPORTED_FORMAT")

    def test_rejects_non_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PB3Error) as ctx:
                _repack.repack_ipa(b"khong phai zip",
                                   {ASSETS: b"x"},
                                   out_dir=os.path.join(tmp, "o"))
            self.assertEqual(ctx.exception.code, "E_UNSUPPORTED_FORMAT")

    def test_rejects_missing_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, _orig = _synthetic_ipa(tmp)
            with self.assertRaises(PB3Error) as ctx:
                _repack.repack_ipa(src, {"Payload/Test.app/nope.bin": b"x"},
                                   out_dir=os.path.join(tmp, "o"))
            self.assertEqual(ctx.exception.code, "E_INPUT_NOT_FOUND")

    def test_rejects_empty_replacements(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, _orig = _synthetic_ipa(tmp)
            with self.assertRaises(ValueError):
                _repack.repack_ipa(src, {}, out_dir=os.path.join(tmp, "o"))

    def test_verify_detects_tampered_unchanged_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, _orig = _synthetic_ipa(tmp)
            res = _repack.repack_ipa(
                src, {ASSETS: b"ASSETS-v2"},
                out_dir=os.path.join(tmp, "out"))
            # Sao chep IPA moi nhung sua 1 byte trong entry chua sua.
            tampered = os.path.join(tmp, "tampered.ipa")
            with zipfile.ZipFile(res["dest"], "r") as zin, \
                    zipfile.ZipFile(tampered, "w") as zout:
                for info in zin.infolist():
                    data = zin.read(info.filename)
                    if info.filename == INFO_PLIST:
                        data = data + b" "
                    zout.writestr(info, data)
            v = _repack.verify_repacked_ipa(
                src, tampered, {ASSETS: b"ASSETS-v2"})
            self.assertFalse(v["passed"])
            bad = {c["name"] for c in v["checks"] if not c["passed"]}
            self.assertIn("unchanged_entries", bad)
            self.assertNotIn("replaced_entries", bad)

    def test_verify_detects_wrong_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, _orig = _synthetic_ipa(tmp)
            res = _repack.repack_ipa(
                src, {ASSETS: b"ASSETS-v2"},
                out_dir=os.path.join(tmp, "out"))
            v = _repack.verify_repacked_ipa(
                src, res["dest"], {ASSETS: b"ASSETS-KHAC"})
            self.assertFalse(v["passed"])
            bad = {c["name"] for c in v["checks"] if not c["passed"]}
            self.assertIn("replaced_entries", bad)


class TestRepackFromRebuildUnits(unittest.TestCase):
    def _write_units(self, tmp):
        units_dir = os.path.join(tmp, "units")
        os.makedirs(units_dir)
        assets = b"ASSETS-v2-" + b"Z" * 100
        bundle = b"BUNDLE-v2-" + b"Y" * 50
        with open(os.path.join(units_dir, "resources.assets"), "wb") as fh:
            fh.write(assets)
        with open(os.path.join(units_dir, "localization.en.bundle"),
                  "wb") as fh:
            fh.write(bundle)
        units = [
            {"unit": ASSETS, "output": "resources.assets"},
            {"unit": BUNDLE + "::CAB-7f58a1d076a0579bc99ac19d9e397afa",
             "output": "localization.en.bundle"},
        ]
        return units_dir, units, {ASSETS: assets, BUNDLE: bundle}

    def test_happy(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, _orig = _synthetic_ipa(tmp)
            units_dir, units, _want = self._write_units(tmp)
            res = _repack.repack_from_rebuild_units(
                src, units, out_dir=os.path.join(tmp, "out"),
                units_dir=units_dir)
            self.assertTrue(res["ok"])
            self.assertTrue(all(c["passed"] for c in res["checks"]))
            self.assertEqual(len(res["units"]), 2)
            self.assertEqual(res["units"][1]["entry"], BUNDLE)

    def test_collision_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, _orig = _synthetic_ipa(tmp)
            units_dir, units, _want = self._write_units(tmp)
            units.append({"unit": BUNDLE + "::CAB-khac",
                          "output": "localization.en.bundle"})
            with self.assertRaises(PB3Error) as ctx:
                _repack.repack_from_rebuild_units(
                    src, units, out_dir=os.path.join(tmp, "out"),
                    units_dir=units_dir)
            self.assertEqual(ctx.exception.code, "E_CONFIG_INVALID")

    def test_missing_output_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, _orig = _synthetic_ipa(tmp)
            units_dir, _units, _want = self._write_units(tmp)
            with self.assertRaises(PB3Error) as ctx:
                _repack.repack_from_rebuild_units(
                    src, [{"unit": ASSETS, "output": None}],
                    out_dir=os.path.join(tmp, "out"), units_dir=units_dir)
            self.assertEqual(ctx.exception.code, "E_INPUT_NOT_FOUND")

    def test_file_unit_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, _orig = _synthetic_ipa(tmp)
            units_dir, _units, _want = self._write_units(tmp)
            with self.assertRaises(PB3Error) as ctx:
                _repack.repack_from_rebuild_units(
                    src, [{"unit": "<file>", "output": "resources.assets"}],
                    out_dir=os.path.join(tmp, "out"), units_dir=units_dir)
            self.assertEqual(ctx.exception.code, "E_CONFIG_INVALID")

    def test_missing_unit_file_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, _orig = _synthetic_ipa(tmp)
            units_dir, _units, _want = self._write_units(tmp)
            with self.assertRaises(PB3Error) as ctx:
                _repack.repack_from_rebuild_units(
                    src, [{"unit": ASSETS, "output": "khong-co.bin"}],
                    out_dir=os.path.join(tmp, "out"), units_dir=units_dir)
            self.assertEqual(ctx.exception.code, "E_INPUT_NOT_FOUND")


class TestBridgeRepack(unittest.TestCase):
    @slow
    def test_bridge_op_synthetic(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, _orig = _synthetic_ipa(tmp)
            units_dir, units, _want = \
                TestRepackFromRebuildUnits()._write_units(tmp)
            # Bridge op doc bytes don vi tu units_dir mac dinh
            # (/tmp/rebuild_out) — o day ghi tam vao do roi don.
            import shutil
            os.makedirs(_rb.REBUILD_OUT_DIR, exist_ok=True)
            for name in os.listdir(units_dir):
                shutil.copy(os.path.join(units_dir, name),
                            os.path.join(_rb.REBUILD_OUT_DIR, name))
            try:
                out = json.loads(bridge.repack_ipa(
                    src, json.dumps(units)))
            finally:
                for name in os.listdir(units_dir):
                    p = os.path.join(_rb.REBUILD_OUT_DIR, name)
                    if os.path.exists(p):
                        os.unlink(p)
            self.assertTrue(out.get("ok"), out)
            self.assertEqual(out["filename"], "test-mod.ipa")
            self.assertTrue(all(c["passed"] for c in out["checks"]), out)
            self.assertTrue(os.path.exists(out["dest"]))


class TestRealNineKings(unittest.TestCase):
    @slow
    def test_repack_9kings_roundtrip(self):
        # [ĐÃ ĐO THẬT] 9K v1.0.6 en->vi 2252 key (round-trip M1 that):
        # apply (E) -> 2 don vi co "output" -> dong goi lai IPA -> IPA
        # moi mo duoc, entry da thay chua noi dung dich moi, moi entry
        # khac giu nguyen tung byte (sha), Info.plist nguyen ven.
        ipa_path = _nine_kings_ipa()
        if not ipa_path.exists():
            raise unittest.SkipTest("thieu IPA 9K: %s" % ipa_path)
        sample = json.loads(
            (Path.home() / "workspace" / "user" / "files" / "en.json"
             ).read_text(encoding="utf-8"))
        self.assertEqual(len(sample), 2252)
        tr = {k: ("Tiếng Việt: " + v) for k, v in sample.items()}
        ipa = ipa_path.read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = os.path.join(tmp, "rebuild_out")
            res = _rb.apply_rebuild(
                ipa, source_name="9kings-v1.0.6.ipa", translations=tr,
                dry_run=False, confirm=True, out_dir=out_dir)
            self.assertEqual(res["totals"]["changed"], 2252,
                             "apply doi %s thay vi 2252"
                             % (res["totals"],))
            units = res["units"]
            self.assertEqual(len(units), 2)
            self.assertTrue(all(u.get("output") for u in units),
                            "thieu truong output: %s" % (units,))
            repack_dir = os.path.join(tmp, "repack_out")
            rep = _repack.repack_from_rebuild_units(
                str(ipa_path), units, out_dir=repack_dir, units_dir=out_dir)
            self.assertTrue(rep["ok"], rep)
            self.assertEqual(rep["filename"], "9kings-v1.0.6-mod.ipa")
            self.assertTrue(all(c["passed"] for c in rep["checks"]),
                            rep["checks"])
            dest = rep["dest"]
            self.assertTrue(os.path.exists(dest))
            # Kiem doc lap: mo IPA moi, doi chieu sha tung entry voi goc.
            orig_names, orig_sha = [], {}
            with zipfile.ZipFile(io.BytesIO(ipa), "r") as zf:
                orig_names = [i.filename for i in zf.infolist()]
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    orig_sha[info.filename] = hashlib.sha256(
                        zf.read(info)).hexdigest()
            replaced = {u["entry"]: u for u in rep["units"]}
            with zipfile.ZipFile(dest, "r") as zf:
                self.assertEqual([i.filename for i in zf.infolist()],
                                 orig_names)
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    got = hashlib.sha256(zf.read(info)).hexdigest()
                    if info.filename in replaced:
                        self.assertNotEqual(got, orig_sha[info.filename])
                    else:
                        self.assertEqual(got, orig_sha[info.filename],
                                         "entry chua sua doi sha: %s"
                                         % info.filename)
                # Noi dung dich moi trong resources.assets da dong goi.
                sf = u.parse_serialized(
                    zf.read("Payload/9Kings.app/Data/resources.assets"),
                    LIM, where="repacked-assets")
                info_ls = u.parse_language_source_bytes(
                    sf.object_bytes(15312), sf.endian)
                self.assertEqual(info_ls["terms"][0]["values"][0],
                                 "Tiếng Việt: His Winningness")
                # Noi dung dich moi trong bundle ST en da dong goi.
                st_entry = [n for n in orig_names
                            if n.endswith(
                                "localization-string-tables-english(en)"
                                "_assets_all.bundle")]
                self.assertEqual(len(st_entry), 1)
                bundle = u.parse_bundle(zf.read(st_entry[0]), BE, LIM,
                                        where="repacked-st")
                found_farm = False
                for ename in bundle.entry_names():
                    sfx = u.parse_serialized(bundle.get_entry(ename), LIM,
                                             where=ename)
                    for o in sfx.objects:
                        if o.class_id != 114:
                            continue
                        try:
                            val = u.read_monobehaviour_object(sfx, o.path_id)
                        except PB3Error:
                            continue
                        for row in (val.get("m_TableData") or []):
                            if row.get("m_Localized") == \
                                    "Tiếng Việt: Farm":
                                found_farm = True
                self.assertTrue(found_farm,
                                "khong thay 'Tiếng Việt: Farm' trong "
                                "bundle ST cua IPA da dong goi")


if __name__ == "__main__":
    unittest.main()
