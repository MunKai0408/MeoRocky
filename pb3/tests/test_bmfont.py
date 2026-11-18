"""Test sdk.bmfont — BMFont .fnt (port từ IPA-LOCALIZATION text_extract.js
parseBmfont + fontgen-style full.fnt 94 ký tự ASCII in được).

Chỉ nửa parse/build text: text, XML, nhị phân v3. Fixture tự dựng theo
đặc tả BMFont — [FIXTURE], chưa đối chiếu .fnt game thật.
"""
import ast
import os
import struct
import unittest

from sdk import bmfont
from sdk.font import required_vietnamese_chars
from tests.helpers import imports_of, read_src

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BMFONT_PATH = os.path.join(ROOT, "sdk", "bmfont.py")

# 94 ký tự ASCII in được (33..126) như full.fnt của make_fontgen_ipas.
ASCII94 = list(range(33, 127))


def _char_line(cid, x=0):
    return ('char id=%d x=%d y=0 width=10 height=12 xoffset=0 yoffset=0 '
            'xadvance=10 page=0 chnl=15' % (cid, x))


def _text_fnt(ids=ASCII94, face="Test", size=16):
    lines = [
        'info face="%s" size=%d bold=0 italic=0 charset="" unicode=1 '
        'stretchH=100 smooth=1 aa=1 padding=0,0,0,0 spacing=1,1 outline=0'
        % (face, size),
        'common lineHeight=20 base=16 scaleW=256 scaleH=256 pages=1 packed=0 '
        'alphaChnl=1 redChnl=0 greenChnl=0 blueChnl=0',
        'page id=0 file="test.png"',
        'chars count=%d' % len(ids),
    ]
    lines += [_char_line(cid, (i % 16) * 10) for i, cid in enumerate(ids)]
    lines += ['kernings count=1',
              'kerning first=65 second=66 amount=-1']
    return ("\n".join(lines) + "\n").encode("utf-8")


def _xml_fnt(ids=ASCII94, face="Test", size=16):
    chars = "".join(
        '<char id="%d" x="%d" y="0" width="10" height="12" xoffset="0" '
        'yoffset="0" xadvance="10" page="0" chnl="15"/>' % (cid, (i % 16) * 10)
        for i, cid in enumerate(ids))
    return ('<font><info face="%s" size="%d" bold="0" italic="0" unicode="1" '
            'stretchH="100" smooth="1" aa="1" padding="0,0,0,0" '
            'spacing="1,1" outline="0"/>'
            '<common lineHeight="20" base="16" scaleW="256" scaleH="256" '
            'pages="1" packed="0" alphaChnl="1" redChnl="0" greenChnl="0" '
            'blueChnl="0"/><pages><page id="0" file="test.png"/></pages>'
            '<chars count="%d">%s</chars>'
            '<kernings count="1"><kerning first="65" second="66" '
            'amount="-1"/></kernings></font>'
            % (face, size, len(ids), chars)).encode("utf-8")


def _block(btype, body):
    return bytes([btype]) + struct.pack("<I", len(body)) + body


def _binary_fnt(ids=ASCII94, face="Test", size=16):
    # Block info nhị phân: size i16 + bitField + charSet + stretchH u16 +
    # aa + padding(4) + spacing(2) + outline = 14 byte, rồi tên face.
    info = (struct.pack("<h", size) + bytes([0, 0])
            + struct.pack("<H", 100) + bytes([1, 0, 0, 0, 0, 1, 1, 0])
            + face.encode("utf-8") + b"\x00")
    common = struct.pack("<HHHHH", 20, 16, 256, 256, 1) + bytes([0, 1, 0, 0, 0])
    pages = b"test.png\x00"
    chars = b"".join(struct.pack("<IhhhhhhhBB", cid, (i % 16) * 10, 0, 10, 12,
                                 0, 0, 10, 0, 15)
                     for i, cid in enumerate(ids))
    kern = struct.pack("<IIh", 65, 66, -1)
    return (b"BMF\x03" + _block(1, info) + _block(2, common)
            + _block(3, pages) + _block(4, chars) + _block(5, kern))


class TestBmfontText(unittest.TestCase):
    def test_text_94_chars(self):
        f = bmfont.parse_bmfont(_text_fnt())
        self.assertIsNotNone(f)
        self.assertEqual(f.format, "text")
        self.assertEqual(f.face, "Test")
        self.assertEqual(f.size, 16)
        self.assertEqual(len(f.chars), 94)
        self.assertEqual(f.char_ids, set(ASCII94))
        self.assertEqual(len(f.kernings), 1)
        self.assertEqual(f.kernings[0], {"first": 65, "second": 66,
                                         "amount": -1})
        self.assertEqual(f.pages, [{"id": 0, "file": "test.png"}])

    def test_text_build_round_trip(self):
        f = bmfont.parse_bmfont(_text_fnt())
        rebuilt = bmfont.build_bmfont_text(f)
        f2 = bmfont.parse_bmfont(rebuilt)
        self.assertIsNotNone(f2)
        self.assertEqual(f2.format, "text")
        self.assertEqual(f2.face, "Test")
        self.assertEqual(f2.char_ids, set(ASCII94))
        self.assertEqual(len(f2.kernings), 1)

    def test_text_no_chars_is_none(self):
        data = (b'info face="X" size=16\ncommon lineHeight=20 base=16 '
                b'scaleW=256 scaleH=256 pages=1\npage id=0 file="x.png"\n'
                b'chars count=0\n')
        self.assertIsNone(bmfont.parse_bmfont(data))


class TestBmfontXmlBinary(unittest.TestCase):
    def test_xml_94_chars(self):
        f = bmfont.parse_bmfont(_xml_fnt())
        self.assertIsNotNone(f)
        self.assertEqual(f.format, "xml")
        self.assertEqual(f.face, "Test")
        self.assertEqual(len(f.chars), 94)
        self.assertEqual(f.char_ids, set(ASCII94))
        self.assertEqual(len(f.kernings), 1)

    def test_xml_bad_is_none(self):
        self.assertIsNone(bmfont.parse_bmfont(b"<font><info"))
        self.assertIsNone(bmfont.parse_bmfont(b"<html></html>"))

    def test_binary_94_chars(self):
        f = bmfont.parse_bmfont(_binary_fnt())
        self.assertIsNotNone(f)
        self.assertEqual(f.format, "binary")
        self.assertEqual(f.face, "Test")
        self.assertEqual(f.size, 16)
        self.assertEqual(len(f.chars), 94)
        self.assertEqual(f.char_ids, set(ASCII94))
        self.assertEqual(len(f.kernings), 1)
        self.assertEqual(f.kernings[0]["amount"], -1)

    def test_binary_bad_version_is_none(self):
        bad = b"BMF\x02" + _binary_fnt()[4:]
        self.assertIsNone(bmfont.parse_bmfont(bad))

    def test_binary_truncated_is_none(self):
        data = _binary_fnt()
        self.assertIsNone(bmfont.parse_bmfont(data[:10]))
        # Cắt giữa block chars -> không đủ char nào vẫn là None (fail-closed).
        self.assertIsNone(bmfont.parse_bmfont(data[:60]))


class TestBmfontMalformed(unittest.TestCase):
    def test_garbage_is_none(self):
        for data in (b"", b"hello world", b"\x00\x01\x02\x03" * 10,
                     b"BMF", b"info face="):
            self.assertIsNone(bmfont.parse_bmfont(data), data[:12])

    def test_build_empty_font_raises(self):
        f = bmfont.BmFont("text")
        with self.assertRaises(Exception):
            bmfont.build_bmfont_text(f)


class TestBmfontViCoverage(unittest.TestCase):
    def test_ascii_only_is_none_level(self):
        cov = bmfont.vi_coverage(set(ASCII94))
        self.assertEqual(cov["level"], "none")
        self.assertEqual(cov["required"], 142)
        self.assertEqual(cov["missing_count"], 142)
        self.assertTrue(cov["ascii_letters_ok"])
        self.assertEqual(cov["percent"], 0.0)

    def test_full_vietnamese_is_full(self):
        req = required_vietnamese_chars()
        cov = bmfont.vi_coverage(set(req) | set(range(32, 127)))
        self.assertEqual(cov["level"], "full")
        self.assertEqual(cov["missing_count"], 0)
        self.assertEqual(cov["percent"], 100.0)

    def test_partial(self):
        req = required_vietnamese_chars()
        cov = bmfont.vi_coverage(set(list(req)[:71]) | set(range(32, 127)))
        self.assertEqual(cov["level"], "partial")
        self.assertEqual(cov["covered"], 71)

    def test_summarize(self):
        f = bmfont.parse_bmfont(_text_fnt())
        s = bmfont.summarize(f)
        self.assertEqual(s["format"], "text")
        self.assertEqual(s["chars"], 94)
        self.assertEqual(s["vietnamese"]["level"], "none")


class TestBmfontStdlibPy39(unittest.TestCase):
    def test_stdlib_only_and_py39_syntax(self):
        ast.parse(read_src(BMFONT_PATH), feature_version=(3, 9))
        allowed = {"__future__", "re", "struct", "typing", "xml",
                   "core", "sdk"}
        for name, _line in imports_of(BMFONT_PATH):
            self.assertIn(name.split(".")[0], allowed, name)


if __name__ == "__main__":
    unittest.main()
