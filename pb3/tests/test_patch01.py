"""Test hồi quy đợt vá patch01: extract crash trên IPA thật (9 Kings).

Nguyên nhân gốc (iSH 2026-11-16, log session-20261116-005829-18):
file XML trong IPA bị yaml_ini sniff chấm 0.7 như cặp key=value INI
(dòng <?xml ...="..."?> chứa dấu "=") -> probe -> _parse_ini_struct
đưa XML vào configparser -> AttributeError thoát khỏi probe -> crash
cả extract. Bug configparser: dòng <resources> thành option KHÔNG giá
trị (None, allow_no_value=True); dòng tiếp theo thụt đầu dòng bị xem là
dòng nối tiếp -> cursect[optname].append(value) gọi .append trên None.
File XML không thụt dòng thì chỉ ra entry rác chứ không crash.
"""
import io
import json
import os
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from core.app import bootstrap
from core.errors import PB3Error
from core.limits import Limits
from core.safe_io import check_readable_input, normalize_typed_path
from plugins.yaml_ini.plugin import YamlIniPlugin, _parse_ini_struct
from sdk import extraction as ext
from sdk.api import PluginContext
from sdk.types import Candidate
from sdk.vfs import MemoryVFS
from services import language_tool
from tests.helpers import TmpCase, copy_project, register_tmp_dir, unregister_tmp_dir

# XML gây crash thật trên iSH (log session-20261116-005829-18).
XML_INDENTED = (b'<?xml version="1.0" encoding="utf-8"?>\n<resources>\n'
                b'    <string name="app_name">9 Kings</string>\n</resources>\n')
XML_PLAIN = (b'<?xml version="1.0" encoding="utf-8"?>\n<resources>\n'
             b'<string name="app_name">9 Kings</string>\n</resources>\n')
XML_DOCTYPE = (b'<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
               b'"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n<plist version="1.0">\n'
               b'<dict>\n    <key>name</key>\n    <string>9 Kings</string>\n</dict>\n</plist>\n')
XML_CDATA = (b'<?xml version="1.0"?>\n<root>\n'
             b'    <text><![CDATA[hello <world> & friends]]></text>\n'
             b'    <a b="c=d">x</a>\n</root>\n')
XML_STRINGS = (b'<?xml version="1.0" encoding="utf-8"?>\n<resources>\n'
               b'    <string name="play">Play</string>\n'
               b'    <string name="quit">Quit</string>\n</resources>\n')
INI_OK = b"[menu]\nnew_game = Tro choi moi\ncount = 3\n"
INI_SECTIONLESS = b"key1 = mot\nkey2 = hai\n"


def _ctx(files, tmp):
    vfs = MemoryVFS(files)
    return PluginContext(vfs, Limits(), None, None, str(tmp), True, None, None, "yaml_ini")


def _plug():
    return YamlIniPlugin()


def _zip_bytes(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in sorted(entries.items()):
            zf.writestr(name, data)
    return buf.getvalue()


class TestXmlIniCrash(TmpCase):
    """(a) XML thụt đầu dòng vào _parse_ini_struct không được AttributeError."""

    def test_repro_exact_xml_raises_e_corrupt(self):
        # Repro đúng log iSH: AttributeError ở configparser._read
        # cursect[optname].append(value) trên None.
        with self.assertRaises(PB3Error) as cm:
            _parse_ini_struct(XML_INDENTED.decode("utf-8"), "test.xml")
        self.assertEqual(cm.exception.code, "E_CORRUPT")

    def test_doctype_plist_indented_raises_e_corrupt(self):
        with self.assertRaises(PB3Error) as cm:
            _parse_ini_struct(XML_DOCTYPE.decode("utf-8"), "Info.plist")
        self.assertEqual(cm.exception.code, "E_CORRUPT")

    def test_cdata_indented_raises_e_corrupt(self):
        with self.assertRaises(PB3Error) as cm:
            _parse_ini_struct(XML_CDATA.decode("utf-8"), "data.xml")
        self.assertEqual(cm.exception.code, "E_CORRUPT")

    def test_strings_xml_indented_raises_e_corrupt(self):
        with self.assertRaises(PB3Error) as cm:
            _parse_ini_struct(XML_STRINGS.decode("utf-8"), "strings.xml")
        self.assertEqual(cm.exception.code, "E_CORRUPT")

    def test_parse_bytes_rejects_xml_early(self):
        plug = _plug()
        for data in (XML_INDENTED, XML_PLAIN, XML_DOCTYPE):
            with self.assertRaises(PB3Error) as cm:
                plug._parse_bytes(data, "x.xml", "en")
            self.assertEqual(cm.exception.code, "E_CORRUPT")

    def test_sniff_xml_zero_ini_and_yaml_kept(self):
        plug = _plug()
        self.assertEqual(plug._sniff(XML_INDENTED, "x.xml"), 0.0)
        self.assertEqual(plug._sniff(XML_DOCTYPE, "Info.plist"), 0.0)
        self.assertEqual(plug._sniff(INI_OK, "a.ini"), 0.8)
        self.assertEqual(plug._sniff(b"menu:\n  new_game: Choi\n", "a.yaml"), 0.7)

    def test_valid_ini_still_parses(self):
        sections, sectionless = _parse_ini_struct(INI_OK.decode("utf-8"), "a.ini")
        self.assertFalse(sectionless)
        self.assertEqual(sections[0][0], "menu")
        sections2, sectionless2 = _parse_ini_struct(
            INI_SECTIONLESS.decode("utf-8"), "b.ini")
        self.assertTrue(sectionless2)
        self.assertEqual(dict(sections2[0][1])["key1"], "mot")

    def test_duplicate_key_still_e_corrupt(self):
        with self.assertRaises(PB3Error) as cm:
            _parse_ini_struct("[s]\nk = 1\nk = 2\n", "dup.ini")
        self.assertEqual(cm.exception.code, "E_CORRUPT")


class TestProbeNeverCrashes(TmpCase):
    """(b) probe với plugin ném exception lạ vẫn trả ok: False."""

    def _probe(self, plug, files, path):
        ctx = _ctx(files, self.tmp).for_plugin(plug.manifest)
        return plug.probe(ctx, Candidate(path, plug.id, 0.9, []))

    def test_probe_xml_entry_returns_false(self):
        res = self._probe(_plug(), {"x.xml": XML_INDENTED}, "x.xml")
        self.assertFalse(res["ok"])

    def test_probe_plugin_raising_attribute_error_returns_false(self):
        class Boom(YamlIniPlugin):
            def _parse_bytes(self, data, path, locale):
                raise AttributeError("boom")

        plug = Boom()
        res = self._probe(plug, {"a.ini": INI_OK}, "a.ini")
        self.assertFalse(res["ok"])
        self.assertIn("AttributeError", res["reason"])

    def test_probe_plugin_raising_runtime_error_returns_false(self):
        class Boom(YamlIniPlugin):
            def _parse_bytes(self, data, path, locale):
                raise RuntimeError("boom")

        res = self._probe(Boom(), {"a.ini": INI_OK}, "a.ini")
        self.assertFalse(res["ok"])


class TestTrailingDotPath(TmpCase):
    """(c) đường dẫn gõ thừa dấu '.' cuối (bàn phím iOS tự chèn) vẫn chạy."""

    def test_normalize_strips_dot_when_stripped_exists(self):
        target = self.tmp / "game.ipa"
        target.write_bytes(b"x")
        self.assertEqual(normalize_typed_path(str(target) + "."),
                         str(target))

    def test_normalize_strips_double_dot(self):
        target = self.tmp / "game.ipa"
        target.write_bytes(b"x")
        self.assertEqual(normalize_typed_path(str(target) + ".."),
                         str(target))

    def test_normalize_leaves_missing_path(self):
        missing = str(self.tmp / "khong_co.ipa") + "."
        self.assertEqual(normalize_typed_path(missing), missing)

    def test_normalize_leaves_existing_dotted_name(self):
        dotted = self.tmp / "weird."
        dotted.write_bytes(b"x")
        self.assertEqual(normalize_typed_path(str(dotted)), str(dotted))

    def test_normalize_leaves_dot_and_dotdot(self):
        self.assertEqual(normalize_typed_path("."), ".")
        self.assertEqual(normalize_typed_path(".."), "..")

    def test_check_readable_input_dotted_resolves(self):
        target = self.tmp / "game.ipa"
        target.write_bytes(b"x")
        got = check_readable_input(str(target) + ".", allow_dir=False)
        self.assertEqual(got, target.resolve())

    def test_check_readable_input_missing_dotted_still_not_found(self):
        with self.assertRaises(PB3Error) as cm:
            check_readable_input(str(self.tmp / "khong_co.ipa") + ".",
                                 allow_dir=False)
        self.assertEqual(cm.exception.code, "E_INPUT_NOT_FOUND")


class TestExtractionXmlNoCrash(TmpCase):
    """Extract đầu-cuối: zip chứa XML thụt dòng (như IPA 9 Kings) không crash."""

    _shared_proj = None
    _shared_td = None

    @classmethod
    def setUpClass(cls):
        cls._shared_td = tempfile.mkdtemp(prefix="pb3-patch01-")
        register_tmp_dir(cls._shared_td)
        cls._shared_proj = copy_project(Path(cls._shared_td))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._shared_td, ignore_errors=True)
        unregister_tmp_dir(cls._shared_td)

    def setUp(self):
        super().setUp()
        self.app = bootstrap(self._shared_proj)
        self.addCleanup(self.app.close)
        self.reg = language_tool._registry(self.app)
        self.out = self.tmp / "out"

    def run_ext(self, source, **kw):
        kw.setdefault("registry", self.reg)
        kw.setdefault("tempdir", str(self.app.paths.temp))
        kw.setdefault("root", str(self._shared_proj))
        kw.setdefault("out_dir", str(self.out))
        return ext.run_extraction(ext.ExtractionRequest(source=source, **kw))

    def _zip_file(self, entries):
        p = self.tmp / "case.zip"
        p.write_bytes(_zip_bytes(entries))
        return str(p)

    def test_zip_with_indented_xml_does_not_crash(self):
        # Mô phỏng IPA thật: 1 file XML thụt dòng + 1 JSON thật.
        src = self._zip_file({"config": XML_INDENTED,
                              "en.json": b'{"play": "Play"}'})
        result = self.run_ext(src)
        by_path = {f.path: f for f in result.files}
        self.assertIn("en.json", by_path)
        # XML thụt dòng không còn crash: plugin xml_strings nhận nó.
        self.assertEqual(by_path["config"].plugin, "xml_strings")
        exported = json.loads((self.out / "English.json").read_text(encoding="utf-8"))
        self.assertEqual(exported.get("play"), "Play")

    def test_probe_raising_plugin_does_not_crash_extraction(self):
        # Lưới an toàn ở extraction: probe ghi đè ném AttributeError thì
        # ứng viên đó bị bỏ qua, các file khác vẫn trích xuất được.
        # (Patch thẳng instance trong registry: loader nạp plugin thành
        # package riêng nên patch class ở module gốc không có tác dụng.)
        src = self._zip_file({"a.ini": INI_OK,
                              "en.json": b'{"play": "Play"}'})
        rec = self.reg.get("yaml_ini")
        self.assertIsNotNone(rec)
        with mock.patch.object(rec.instance, "probe",
                               side_effect=AttributeError("boom")):
            result = self.run_ext(src)
        paths = [f.path for f in result.files]
        self.assertIn("en.json", paths)
        self.assertNotIn("a.ini", paths)
        skipped = " ".join(s.get("reason", "") for s in result.skipped)
        self.assertIn("E_INTERNAL", skipped)


if __name__ == "__main__":
    unittest.main()
