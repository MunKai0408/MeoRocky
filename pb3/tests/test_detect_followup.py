# -*- coding: utf-8 -*-
"""Test follow-up dò text sau PR #10: (a) parser .strings bplist,
(b) quét Mach-O theo section.

Fixture TỰ DỰNG (bplist tổng hợp bằng plistlib, Mach-O 64-bit/FAT
tổng hợp); không dùng mạng. Test IPA thật ở cuối: skip nếu thiếu file
(env TOM_IPA hoặc ~/workspace/test-ipas/My_Talking_Tom_v26.5.1_
Decrypted.ipa).

Ghi chú: việc bplist KHÔNG gộp vào file locale chính được bảo vệ ở 2 lớp:
- unit test _origin_kind_for (flavor "strings-bplist" -> "other");
- harness exp/verify_bug2_merge.py (9K en.json phải đúng 2252 key,
  byte-identical vs mẫu — gộp thêm 1 key InfoPlist.strings là vỡ).
"""

import json
import os
import plistlib
import struct
import sys
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bridge  # noqa: E402

bridge.WEB_ROOT = str(ROOT)
json.loads(bridge.boot())

from core.errors import PB3Error  # noqa: E402
from plugins.apple_strings.plugin import (  # noqa: E402
    AppleStringsPlugin, parse_strings_bytes)
from sdk import extraction as extraction_sdk  # noqa: E402
from sdk import inspect as inspect_sdk  # noqa: E402
from sdk import rawscan  # noqa: E402
from sdk.types import LocTable  # noqa: E402

TOM_IPA = Path(os.environ.get(
    "TOM_IPA", Path.home() / "workspace" / "test-ipas" /
    "My_Talking_Tom_v26.5.1_Decrypted.ipa"))
EK2_IPA = Path(os.environ.get(
    "EK2_IPA", Path.home() / "workspace" / "test-ipas" /
    "ek2-v0.0.18.ipa"))
TOM_BINARY = "Payload/MyTalkingTom-row.app/MyTalkingTom-row"
TOM_STRINGS = "Payload/MyTalkingTom-row.app/%s.lproj/Localizable.strings"
# 14 locale của Localizable.strings chính trong Tom [ĐÃ ĐO THẬT].
TOM_MAIN_LOCALES = ("en", "vi", "zh-Hans", "zh-Hant", "ja", "ko", "fr",
                    "de", "es", "it", "ru", "pt", "tr", "ar")


# ------------------------------------------------------- fixture bplist
def _bplist_bytes(pairs):
    return plistlib.dumps(dict(pairs), fmt=plistlib.FMT_BINARY)


# ------------------------------------------------------- fixture Mach-O
def _build_macho64(cstring=b"", ustring=b"", methname=b""):
    """Mach-O 64-bit tổng hợp: 1 LC_SEGMENT_64 (__TEXT) với các section
    __cstring / __ustring / __objc_methname (chỉ tạo section nào có dữ
    liệu). Trả bytes đầy đủ."""
    sections = []
    if cstring:
        sections.append(("__cstring", cstring))
    if ustring:
        sections.append(("__ustring", ustring))
    if methname:
        sections.append(("__objc_methname", methname))
    nsects = len(sections)
    cmdsize = 72 + 80 * nsects
    header_size = 32 + cmdsize
    offset = header_size
    sect_headers = b""
    body = b""
    for name, data in sections:
        sect_headers += struct.pack(
            "<16s16sQQIIIIIIII", name.encode(), b"__TEXT",
            0, len(data), offset, 0, 0, 0, 0, 0, 0, 0)
        body += data
        offset += len(data)
    header = struct.pack("<8I", 0xFEEDFACF, 0x0100000C, 3, 2, 1,
                         cmdsize, 0, 0)
    cmd = struct.pack("<2I16sQQQQIIII", 0x19, cmdsize, b"__TEXT",
                      0, 0, 0, offset, 0, 0, nsects, 0)
    return header + cmd + sect_headers + body


def _build_fat(archs):
    """FAT tổng hợp: archs = [(cputype, macho_bytes), ...]."""
    nfat = len(archs)
    offset = 8 + 20 * nfat
    arch_hdrs = b""
    body = b""
    for cpu, data in archs:
        arch_hdrs += struct.pack(">5I", cpu, 3, offset, len(data), 0)
        body += data
        offset += len(data)
    return struct.pack(">2I", 0xCAFEBABE, nfat) + arch_hdrs + body


class TestBplistStrings(unittest.TestCase):
    def test_parse_fixture_key_value(self):
        data = _bplist_bytes([("Hello", "Xin chào"),
                              ("Camera", "Máy ảnh")])
        r = parse_strings_bytes(data, "en.lproj/Localizable.strings", "en")
        self.assertEqual(r["schema_hint"]["flavor"], "strings-bplist")
        kv = {e.key: e.value for e in r["entries"]}
        self.assertEqual(kv, {"Hello": "Xin chào", "Camera": "Máy ảnh"})
        self.assertEqual(r["stats"]["keys"], 2)

    def test_sniff_bplist_magic(self):
        p = AppleStringsPlugin()
        self.assertEqual(p._sniff(_bplist_bytes([("a", "b")]), "x"), 0.9)
        self.assertEqual(p._sniff(b"\x00\x01\x02\x03" * 16, "x"), 0.0)
        text = b'"Hello" = "Xin chao";\n'
        self.assertEqual(p._sniff(text, "x"), 0.8)

    def test_parse_text_still_works(self):
        r = parse_strings_bytes(b'"Hello" = "Xin chao";\n', "x", "en")
        self.assertEqual(r["schema_hint"]["flavor"], "strings")
        self.assertEqual([(e.key, e.value) for e in r["entries"]],
                         [("Hello", "Xin chao")])

    def test_parse_non_dict_bplist_rejected(self):
        data = plistlib.dumps(["a", "b"], fmt=plistlib.FMT_BINARY)
        with self.assertRaises(PB3Error) as ctx:
            parse_strings_bytes(data, "x", "en")
        self.assertEqual(ctx.exception.code, "E_CORRUPT")

    def test_parse_non_string_value_skipped(self):
        data = plistlib.dumps({"ok": "yes", "num": 42},
                              fmt=plistlib.FMT_BINARY)
        r = parse_strings_bytes(data, "x", "en")
        self.assertEqual([e.key for e in r["entries"]], ["ok"])
        self.assertEqual(len(r["warnings"]), 1)

    def test_build_round_trip_bplist(self):
        p = AppleStringsPlugin()
        base = _bplist_bytes([("Hello", "Xin chào"),
                              ("Camera", "Máy ảnh")])
        r0 = parse_strings_bytes(base, "en", "en")
        table = LocTable(list(r0["entries"]), "en", "apple_strings",
                         dict(r0["schema_hint"]), dict(r0["stats"]), [], [])
        # plistlib.dumps sort key mặc định nên thứ tự entry là thứ tự
        # alphabet (Camera trước Hello) — sửa theo key, không theo vị trí.
        for e in table.entries:
            if e.key == "Hello":
                e.value = "CHANGED"
        new = p._build_bytes(table, base)
        self.assertEqual(new[:8], b"bplist00")
        r1 = parse_strings_bytes(new, "en", "en")
        kv = {e.key: e.value for e in r1["entries"]}
        self.assertEqual(kv, {"Hello": "CHANGED", "Camera": "Máy ảnh"})


class TestXmlPlistStrings(unittest.TestCase):
    def test_parse_fixture_key_value(self):
        data = plistlib.dumps(
            {"NSUserTrackingUsageDescription": "We use tracking data."},
            fmt=plistlib.FMT_XML)
        r = parse_strings_bytes(data, "en.lproj/InfoPlist.strings", "en")
        self.assertEqual(r["schema_hint"]["flavor"], "strings-xml-plist")
        kv = {e.key: e.value for e in r["entries"]}
        self.assertEqual(kv, {"NSUserTrackingUsageDescription":
                              "We use tracking data."})

    def test_sniff_xml_plist(self):
        p = AppleStringsPlugin()
        data = plistlib.dumps({"a": "b"}, fmt=plistlib.FMT_XML)
        self.assertEqual(p._sniff(data, "x"), 0.9)
        # Text .strings chứa chữ "xml" trong giá trị KHÔNG bị nhận nhầm.
        self.assertEqual(
            p._sniff(b'"k" = "see <?xml inside";\n', "x"), 0.8)

    def test_build_round_trip_xml(self):
        p = AppleStringsPlugin()
        base = plistlib.dumps({"Hello": "Xin chào"},
                              fmt=plistlib.FMT_XML)
        r0 = parse_strings_bytes(base, "en", "en")
        table = LocTable(list(r0["entries"]), "en", "apple_strings",
                         dict(r0["schema_hint"]), dict(r0["stats"]), [], [])
        table.entries[0].value = "CHANGED"
        new = p._build_bytes(table, base)
        self.assertTrue(new.lstrip().startswith(b"<?xml"))
        r1 = parse_strings_bytes(new, "en", "en")
        self.assertEqual(r1["schema_hint"]["flavor"], "strings-xml-plist")
        kv = {e.key: e.value for e in r1["entries"]}
        self.assertEqual(kv, {"Hello": "CHANGED"})


class TestOriginKindBplist(unittest.TestCase):
    def test_bplist_is_other_not_merged(self):
        fn = extraction_sdk._origin_kind_for
        self.assertEqual(fn("a.strings", None, "apple_strings",
                            flavor="strings-bplist"), "other")
        self.assertEqual(fn("a.strings", None, "apple_strings",
                            flavor="strings-xml-plist"), "other")

    def test_text_strings_still_text_asset(self):
        fn = extraction_sdk._origin_kind_for
        self.assertEqual(fn("a.strings", None, "apple_strings",
                            flavor="strings"), "text_asset")
        self.assertEqual(fn("a.strings", None, "apple_strings"),
                         "text_asset")

    def test_existing_kinds_unchanged(self):
        fn = extraction_sdk._origin_kind_for
        self.assertEqual(fn("a.plist", None, "apple_plist"), "other")
        self.assertEqual(fn("a", None, "generic_strings"), "generic_flat")


class TestLocaleFromLproj(unittest.TestCase):
    def test_locales(self):
        fn = inspect_sdk._locale_from_lproj
        self.assertEqual(fn("Payload/App.app/en.lproj/Localizable.strings"),
                         "en")
        self.assertEqual(fn("x/zh-Hans.lproj/y.strings"), "zh-Hans")
        self.assertEqual(fn("x/Base.lproj/y.strings"), "Base")
        self.assertIsNone(fn("x/NoLproj/y.strings"))


class TestMachoMagic(unittest.TestCase):
    def test_magics(self):
        self.assertTrue(rawscan._is_macho_magic(b"\xcf\xfa\xed\xfe"))
        self.assertTrue(rawscan._is_macho_magic(b"\xce\xfa\xed\xfe"))
        self.assertTrue(rawscan._is_macho_magic(b"\xca\xfe\xba\xbe"))
        self.assertTrue(rawscan._is_macho_magic(b"\xbe\xba\xfe\xca"))
        self.assertFalse(rawscan._is_macho_magic(b"UnityFS"))
        self.assertFalse(rawscan._is_macho_magic(b"PK\x03\x04"))
        self.assertFalse(rawscan._is_macho_magic(b""))

    def test_32bit_rejected(self):
        data = struct.pack("<8I", 0xFEEDFACE, 0xC, 3, 2, 0, 0, 0, 0)
        with self.assertRaises(PB3Error) as ctx:
            rawscan.scan_macho_bytes(data, "macho32")
        self.assertEqual(ctx.exception.code, "E_UNSUPPORTED_FORMAT")

    def test_truncated_rejected(self):
        with self.assertRaises(PB3Error) as ctx:
            rawscan.scan_macho_bytes(b"\xcf\xfa\xed\xfe" + b"\x00" * 10,
                                     "trunc")
        self.assertEqual(ctx.exception.code, "E_CORRUPT")


class TestMachoFixture(unittest.TestCase):
    def test_thin_sections(self):
        cstring = b"Hello world test\x00Another test string here\x00"
        ustring = "Unicode test string here".encode("utf-16-le") + b"\x00\x00"
        methname = b"someMethodName\x00anotherSelector:\x00"
        data = _build_macho64(cstring, ustring, methname)
        out = rawscan.scan_macho_bytes(data, "fixture-macho")
        self.assertEqual(out["mode"], "macho")
        self.assertEqual(out["kind"], "thin")
        texts = [s["text"] for s in out["strings"]]
        self.assertIn("Hello world test", texts)
        self.assertIn("Another test string here", texts)
        self.assertIn("Unicode test string here", texts)
        # __objc_methname KHÔNG quét: tên selector không xuất hiện.
        all_text = texts + [w["text"] for w in out["single_words"]]
        self.assertNotIn("someMethodName", all_text)
        self.assertNotIn("anotherSelector:", all_text)
        # Offset tuyệt đối trong file (section __cstring bắt đầu sau header).
        first = [s for s in out["strings"]
                 if s["text"] == "Hello world test"][0]
        self.assertEqual(first["section"], "__TEXT,__cstring")
        self.assertEqual(first["offset"], 32 + 72 + 80 * 3)
        # sections chỉ liệt kê section MỤC TIÊU (__cstring + __ustring);
        # __objc_methname có trong file nhưng không quét nên không có mặt.
        self.assertEqual(len(out["sections"]), 2)
        self.assertEqual({s["section"] for s in out["sections"]},
                         {"__cstring", "__ustring"})
        self.assertEqual(out["skipped_sections"], [])

    def test_fat_picks_arm64_slice(self):
        arm = _build_macho64(b"Arm64 test string here\x00")
        x86 = _build_macho64(b"X86 test string here\x00")
        fat = _build_fat([(0x01000007, x86), (0x0100000C, arm)])
        out = rawscan.scan_macho_bytes(fat, "fixture-fat")
        self.assertEqual(out["kind"], "fat")
        texts = [s["text"] for s in out["strings"]]
        self.assertIn("Arm64 test string here", texts)
        self.assertNotIn("X86 test string here", texts)

    def test_scan_target_dispatches_macho(self):
        data = _build_macho64(b"Dispatch test string here\x00")
        path = Path(__file__).resolve().parent / ".tmp-macho-fixture"
        path.write_bytes(data)
        try:
            out = rawscan.scan_target(str(path))
            self.assertEqual(out["mode"], "macho")
            self.assertIn("Dispatch test string here",
                          [s["text"] for s in out["strings"]])
        finally:
            path.unlink(missing_ok=True)


# ------------------------------------------------------- IPA thật (skip nếu thiếu)
class TestBplistRealTom(unittest.TestCase):
    def setUp(self):
        if not TOM_IPA.exists():
            raise unittest.SkipTest("thieu IPA Tom: %s" % TOM_IPA)
        self.zf = zipfile.ZipFile(str(TOM_IPA))

    def tearDown(self):
        self.zf.close()

    def test_main_app_14_locales_595_keys(self):
        total = 0
        counts = {}
        for loc in TOM_MAIN_LOCALES:
            data = self.zf.read(TOM_STRINGS % loc)
            r = parse_strings_bytes(data, TOM_STRINGS % loc, loc)
            self.assertEqual(r["schema_hint"]["flavor"], "strings-bplist")
            counts[loc] = len(r["entries"])
            total += len(r["entries"])
        self.assertEqual(counts["en"], 64)
        self.assertEqual(counts["vi"], 36)
        self.assertEqual(counts["zh-Hans"], 64)
        self.assertEqual(total, 595)

    def test_known_pairs_en_vi(self):
        en = parse_strings_bytes(self.zf.read(TOM_STRINGS % "en"),
                                 TOM_STRINGS % "en", "en")
        vi = parse_strings_bytes(self.zf.read(TOM_STRINGS % "vi"),
                                 TOM_STRINGS % "vi", "vi")
        en_kv = {e.key: e.value for e in en["entries"]}
        vi_kv = {e.key: e.value for e in vi["entries"]}
        self.assertEqual(en_kv["Camera"], "Camera")
        self.assertEqual(vi_kv["Camera"], "Máy ảnh")
        self.assertEqual(en_kv["Allow Notifications"], "Allow Notifications")
        self.assertEqual(vi_kv["Allow Notifications"], "Cho phép thông báo")


class TestMachoRealTom(unittest.TestCase):
    def test_scan_target_macho(self):
        if not TOM_IPA.exists():
            raise unittest.SkipTest("thieu IPA Tom: %s" % TOM_IPA)
        out = rawscan.scan_target(str(TOM_IPA), TOM_BINARY)
        self.assertEqual(out["mode"], "macho")
        self.assertEqual(out["kind"], "thin")
        secs = {s["section"]: s for s in out["sections"]}
        self.assertEqual(secs["__cstring"]["kept"], 10000)
        self.assertEqual(secs["__ustring"]["kept"], 498)
        self.assertEqual(out["kept"], 10498)
        texts = [s["text"] for s in out["strings"]]
        self.assertIn("Unity startup flow already started", texts)
        self.assertIn("Running Unity Framework", texts)
        self.assertIn("Native video ad: JS impression tracker at index "
                      "%ld succeeded", texts)
        self.assertEqual(out["skipped_sections"], [])


class TestInspectAppleStringsRealTom(unittest.TestCase):
    def test_inspect_deep_apple_strings(self):
        if not TOM_IPA.exists():
            raise unittest.SkipTest("thieu IPA Tom: %s" % TOM_IPA)
        out = json.loads(bridge.inspect_deep(str(TOM_IPA)))
        a = out["apple_strings"]
        self.assertEqual(a["label"], "apple-strings")
        self.assertEqual(a["total_files"], 159)
        self.assertEqual(a["total_keys"], 1404)
        self.assertEqual(a["skipped"], [])
        formats = {}
        for f in a["files"]:
            formats[f["format"]] = formats.get(f["format"], 0) + 1
        self.assertEqual(formats.get("strings-bplist"), 156)
        self.assertEqual(formats.get("strings"), 3)
        by_path = {f["path"]: f for f in a["files"]}
        en = by_path[TOM_STRINGS % "en"]
        self.assertEqual(en["keys"], 64)
        self.assertEqual(en["locale"], "en")
        self.assertEqual(en["sample"][2],
                         {"key": "Camera", "value": "Camera"})
        vi = by_path[TOM_STRINGS % "vi"]
        self.assertEqual(vi["keys"], 36)
        self.assertEqual(vi["sample"][2],
                         {"key": "Camera", "value": "Máy ảnh"})


class TestInspectAppleStringsRealEk2(unittest.TestCase):
    def test_inspect_deep_apple_strings_ek2(self):
        if not EK2_IPA.exists():
            raise unittest.SkipTest("thieu IPA EK2: %s" % EK2_IPA)
        out = json.loads(bridge.inspect_deep(str(EK2_IPA)))
        a = out["apple_strings"]
        # 52 file: 38 text + 7 bplist + 7 XML plist; 0 bỏ qua.
        self.assertEqual(a["total_files"], 52)
        self.assertEqual(a["total_keys"], 425)
        self.assertEqual(a["skipped"], [])
        formats = {}
        for f in a["files"]:
            formats[f["format"]] = formats.get(f["format"], 0) + 1
        self.assertEqual(formats.get("strings"), 38)
        self.assertEqual(formats.get("strings-bplist"), 7)
        self.assertEqual(formats.get("strings-xml-plist"), 7)
        by_path = {f["path"]: f for f in a["files"]}
        en = by_path["Payload/ipastore.app/en.lproj/InfoPlist.strings"]
        self.assertEqual(en["keys"], 1)
        self.assertEqual(en["format"], "strings-xml-plist")
        self.assertEqual(en["sample"][0]["key"],
                         "NSUserTrackingUsageDescription")
        self.assertTrue(en["sample"][0]["value"].startswith(
            "We use tracking data"))


if __name__ == "__main__":
    unittest.main()
