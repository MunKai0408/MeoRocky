"""Test A6 — 4 fixture builder của IPA-LOCALIZATION chuyển sang pytest.

Builder port từ tests/make_*.py của app JS (make_pakheads_ipas,
make_text_ipas, make_font_ipas, make_raw_ipas), sinh IPA giả trong
tmp_path (không commit file nhị phân). Mỗi IPA được assert cấu trúc rồi
chạy parser mới lên: Heaps PAK, CastleDB, BMFont (fail-closed).
Dữ liệu giả — [FIXTURE], không phải game thật.
"""
import json
import plistlib
import struct
import unittest
import zipfile

from sdk import bmfont, heaps
from plugins.castle_db.plugin import parse_cdb_bytes
from tests.helpers import TmpCase

APP = "Payload/Game.app/"
PLIST = plistlib.dumps(
    {"CFBundleIdentifier": "com.test.game", "CFBundleName": "Game",
     "CFBundleExecutable": "GameExe", "CFBundleShortVersionString": "1.0",
     "CFBundleVersion": "1"}, fmt=plistlib.FMT_BINARY)


def _write_ipa(path, files):
    with zipfile.ZipFile(str(path), "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(APP + "Info.plist", PLIST)
        for name, data in files.items():
            z.writestr(APP + name, data)
    return str(path)


def _read_ipa(path):
    with zipfile.ZipFile(str(path)) as z:
        return {n: z.read(n) for n in z.namelist()}


def _ent(name, files=None, size=0, pos=0):
    n = name.encode("utf-8")
    if files is not None:
        return (bytes([len(n)]) + n + b"\x01"
                + struct.pack("<I", len(files)) + b"".join(files))
    return (bytes([len(n)]) + n + b"\x00"
            + struct.pack("<II", pos, size) + b"\xaa\xbb\xcc\xdd")


def _heaps_pak(files):
    """Pak Heaps từ danh sách (path, content) — port heaps() của
    make_text_ipas.py: cây theo đường dẫn, DATA rồi nối payload."""
    data = b""
    entries = []
    for path, content in files:
        entries.append((path, len(data), len(content)))
        data += content

    def enc(name, node):
        if isinstance(node, tuple):
            return _ent(name, size=node[1], pos=node[0])
        return _ent(name, [enc(k, v) for k, v in node.items()])

    tree_dict = {}
    for p, pos, sz in entries:
        parts = p.split("/")
        cur = tree_dict
        for q in parts[:-1]:
            cur = cur.setdefault(q, {})
        cur[parts[-1]] = (pos, sz)
    tree = enc("", tree_dict)
    hs = 12 + len(tree)
    return b"PAK\x00" + struct.pack("<II", hs, len(data)) + tree + b"DATA" + data


def _pak_entry_bytes(pak, parsed, path):
    """Cắt entry ra khỏi vùng DATA của pak theo pos/size của cây."""
    for f in parsed["_files"]:
        if f["path"] == path:
            start = parsed["data_start"] + f["pos"]
            return pak[start:start + f["size"]]
    raise AssertionError("không thấy %s trong pak" % path)


class TestPakheadsIpa(TmpCase):
    def test_pak_heaps_ipa(self):
        tree = _ent("", [_ent("lang", [_ent("en.xml", size=10, pos=0),
                                       _ent("fr.xml", size=10, pos=10)]),
                        _ent("data.cdb", size=20, pos=20)])
        hs = 12 + len(tree)
        pak = (b"PAK" + b"\x00" + struct.pack("<II", hs, 40) + tree
               + b"DATA" + b"\x00" * 40)
        ipa = _write_ipa(self.tmp / "pak_heaps.ipa",
                         {"res.pak": pak, "res1.pak": b"\x01\x02" * 64})
        entries = _read_ipa(ipa)
        self.assertIn(APP + "res.pak", entries)
        r = heaps.parse_heaps_pak(entries[APP + "res.pak"],
                                  len(entries[APP + "res.pak"]))
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["sample_paths"],
                         ["lang/en.xml", "lang/fr.xml", "data.cdb"])
        self.assertEqual(
            heaps.parse_heaps_pak(entries[APP + "res1.pak"], 128)["status"],
            "not_heaps_pak")

    def test_pak_heaps_nodata_ipa(self):
        tree = _ent("", [_ent("lang", [_ent("en.xml", size=10, pos=0)])])
        hs = 12 + len(tree)
        pak = (b"PAK" + b"\x00" + struct.pack("<II", hs, 40) + tree
               + b"XXXX" + b"\x00" * 40)
        ipa = _write_ipa(self.tmp / "pak_heaps_nodata.ipa", {"res.pak": pak})
        entries = _read_ipa(ipa)
        self.assertEqual(
            heaps.parse_heaps_pak(entries[APP + "res.pak"], 128)["status"],
            "unverified")

    def test_pak_none_and_godot_ipa(self):
        ipa = _write_ipa(self.tmp / "pak_none.ipa", {"a.png": b"x" * 8})
        entries = _read_ipa(ipa)
        self.assertFalse([n for n in entries if n.endswith(".pak")])
        ipa2 = _write_ipa(self.tmp / "pak_godot.ipa",
                          {"game.pak": b"GDPC" + b"\x00" * 40})
        entries2 = _read_ipa(ipa2)
        self.assertEqual(
            heaps.parse_heaps_pak(entries2[APP + "game.pak"], 44)["status"],
            "not_heaps_pak")


class TestTextIpa(TmpCase):
    def test_text_ios_ipa_structure(self):
        xcs = json.dumps({
            "sourceLanguage": "en",
            "strings": {"Play": {"localizations": {
                "en": {"stringUnit": {"state": "translated",
                                      "value": "Play"}},
                "vi": {"stringUnit": {"state": "translated",
                                      "value": "Chơi"}}}}}}).encode()
        strings = ('/* c */\n"hello" = "Xin chào %@!";\n'
                   '"quit" = "Quit\\nGame";\n"empty" = "";\n').encode("utf-16")
        ipa = _write_ipa(self.tmp / "text_ios.ipa", {
            "en.lproj/Localizable.strings": strings,
            "Localizable.xcstrings": xcs,
            "Data/Localization/en.json": json.dumps(
                {"ui": {"start": "Start the game"}}).encode(),
        })
        entries = _read_ipa(ipa)
        self.assertIn(APP + "en.lproj/Localizable.strings", entries)
        doc = json.loads(entries[APP + "Localizable.xcstrings"])
        self.assertEqual(doc["sourceLanguage"], "en")
        self.assertEqual(doc["strings"]["Play"]["localizations"]["vi"]
                         ["stringUnit"]["value"], "Chơi")

    def test_text_pak_ipa_cdb_and_fnt(self):
        pak_files = [
            ("lang/en.xml",
             b'<l><t id="hi">Hello %s &amp; welcome</t></l>'),
            ("data.cdb", json.dumps(
                {"sheets": [{"name": "items",
                             "columns": [{"name": "id"},
                                         {"name": "name",
                                          "kind": "localizable"}],
                             "lines": [{"id": "Sword",
                                        "name": "Sword of doom"}]}]}).encode()),
        ]
        ipa = _write_ipa(self.tmp / "text_pak.ipa",
                         {"res.pak": _heaps_pak(pak_files)})
        entries = _read_ipa(ipa)
        pak = entries[APP + "res.pak"]
        r = heaps.parse_heaps_pak(pak, len(pak))
        self.assertEqual(r["status"], "ok")
        cdb = _pak_entry_bytes(pak, r, "data.cdb")
        t = parse_cdb_bytes(cdb, "data.cdb")
        self.assertEqual([(e.key, e.value) for e in t["entries"]],
                         [("items[Sword].name", "Sword of doom")])

    def test_font_pak_ipa_fnt_has_no_chars(self):
        font_pak_files = [
            ("ui/big.fnt",
             b'info face="Big" size=16\ncommon lineHeight=18\n'),
            ("ui/big.png", b"\x89PNG" + b"1" * 30),
            ("lang/en.xml", b'<l><t id="a">Hello there</t></l>'),
        ]
        ipa = _write_ipa(self.tmp / "font_pak.ipa",
                         {"res.pak": _heaps_pak(font_pak_files)})
        entries = _read_ipa(ipa)
        pak = entries[APP + "res.pak"]
        r = heaps.parse_heaps_pak(pak, len(pak))
        self.assertEqual(r["status"], "ok")
        # .fnt chỉ có info+common, không có char nào -> fail-closed (None).
        self.assertIsNone(
            bmfont.parse_bmfont(_pak_entry_bytes(pak, r, "ui/big.fnt")))

    def test_text_pak_wrong_ipa(self):
        # Header khai header_size=40 nhưng cây thật dài tới offset 48:
        # cây vẫn đọc được (status ok) nhưng "DATA" không nằm ở vị trí
        # header khai — dấu hiệu bố cục sai, tầng trích xuất phải bỏ qua
        # nội dung (app JS: 0 dòng, ghi skipped + cảnh báo bố cục).
        tree = _ent("", [_ent("lang", [_ent("en.xml", size=10, pos=0)])])
        pak = (b"PAK\x00" + struct.pack("<II", 40, 10) + tree + b"DATA"
               + b"\xff\xfe\x00\x01" * 10)
        ipa = _write_ipa(self.tmp / "text_pak_wrong.ipa", {"res.pak": pak})
        entries = _read_ipa(ipa)
        r = heaps.parse_heaps_pak(entries[APP + "res.pak"], 128)
        self.assertEqual(r["status"], "ok")
        self.assertNotEqual(r.get("data_marker_at_header_size"), "DATA")


class TestFontIpa(TmpCase):
    def test_font_fixtures_fail_closed(self):
        bm_txt = (b'info face="Test" size=32 bold=0 italic=0 charset="" '
                  b'unicode=1\ncommon lineHeight=36 base=29 scaleW=256 '
                  b'scaleH=256 pages=1 packed=0\npage id=0 file="t.png"\n'
                  b'chars count=0\n')
        bm_xml = (b'<?xml version="1.0"?>\n<font><info face="Test" '
                  b'size="32"/><common lineHeight="36" base="29"/>'
                  b'<pages><page id="0" file="t.png"/></pages></font>')
        bm_bin = b"BMF\x03" + b"\x01" + struct.pack("<I", 5) + b"abcde"
        weird = b"MZ\x90\x00" + b"\x00" * 40
        ipa = _write_ipa(self.tmp / "font_all.ipa", {
            "Fonts/atlas/Title.fnt": bm_txt,
            "Fonts/atlas/Xml.fnt": bm_xml,
            "Fonts/atlas/Bin.bmfont": bm_bin,
            "Fonts/Weird.fnt": weird,
            "Fonts/Main.ttf": b"\x00\x01\x00\x00" + b"\x00" * 60,
        })
        entries = _read_ipa(ipa)
        for name in ("Fonts/atlas/Title.fnt", "Fonts/atlas/Xml.fnt",
                     "Fonts/atlas/Bin.bmfont", "Fonts/Weird.fnt"):
            self.assertIsNone(bmfont.parse_bmfont(entries[APP + name]), name)
        # sfnt magic còn nguyên (chỉ assert cấu trúc; module font ngoài scope).
        self.assertTrue(entries[APP + "Fonts/Main.ttf"].startswith(
            b"\x00\x01\x00\x00"))


class TestRawIpa(TmpCase):
    def test_raw_pak_unreadable_header(self):
        body = (b"\x00" * 3000 + b"Press any button to continue the game."
                + b"\x00" * 500)
        pak = (b"PAK\x01" + (999).to_bytes(4, "little")
               + (5).to_bytes(4, "little") + b"\x66\x35\x66\x65" + body)
        ipa = _write_ipa(self.tmp / "raw_pak.ipa", {
            "res.pak": pak,
            "data.bin": (b"\x00" * 400
                         + b"Unity-style scene text line here"
                         + b"\x00" * 600),
        })
        entries = _read_ipa(ipa)
        self.assertEqual(
            heaps.parse_heaps_pak(entries[APP + "res.pak"], 512)["status"],
            "unverified")
        self.assertIn(b"Unity-style scene text line here",
                      entries[APP + "data.bin"])

    def test_raw_encrypted_pak(self):
        ipa = _write_ipa(self.tmp / "raw_encrypted.ipa",
                         {"res.pak": b"PAK\x01" + b"\xab" * 20000})
        entries = _read_ipa(ipa)
        self.assertEqual(
            heaps.parse_heaps_pak(entries[APP + "res.pak"], 512)["status"],
            "unverified")


if __name__ == "__main__":
    unittest.main()
