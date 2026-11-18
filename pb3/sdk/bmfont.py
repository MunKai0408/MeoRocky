# Module BMFont (.fnt) — port từ IPA-LOCALIZATION js/modules/text_extract.js
# (parseBmfont) + nửa build: parse text/XML/binary v3 rồi build lại dạng
# text. Chỉ parse/build mô tả font, KHÔNG sinh atlas/glyph, KHÔNG xử lý
# ảnh atlas (nằm ngoài phạm vi đợt này).
#
# Định dạng nhị phân v3 (AngelCode BMFont): "BMF\x03", rồi các block
# type u8 + size u32 LE: 1=info (size u16, face sau 14 byte), 2=common,
# 3=pages (chuỗi kết thúc NUL), 4=chars (mỗi char 20 byte, id u32 đầu),
# 5=kernings (mỗi cặp 10 byte: first u32, second u32, amount i16).
# Dạng text: dòng info/common/page/chars/char/kernings/kerning key=value.
# Dạng XML: <font><info/><common/><pages/><chars><char/>...
# Trả None khi không nhận ra/không có char nào (fail-closed, như bản JS).
from __future__ import annotations

import re
import struct
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

from core.errors import PB3Error
from sdk.font import required_vietnamese_chars

_TEXT_KV_RE = re.compile(r"([A-Za-z]+)=(\"([^\"]*)\"|[^ \t\r\n]+)")
_CHAR_ID_RE = re.compile(r"^\s*char\s+id=(\d+)", re.MULTILINE)
_INFO_FACE_RE = re.compile(r"^\s*info\s[^\n]*?face=\"([^\"]*)\"", re.MULTILINE)
_INFO_SIZE_RE = re.compile(r"^\s*info\s[^\n]*?size=(-?\d+)", re.MULTILINE)
_XML_CHAR_RE = re.compile(r"<char\s[^>]*?\bid=\"(\d+)\"", re.MULTILINE)
_XML_INFO_FACE_RE = re.compile(r"<info[^>]*?\bface=\"([^\"]*)\"")
_XML_INFO_SIZE_RE = re.compile(r"<info[^>]*?\bsize=\"(-?\d+)\"")

_INFO_DEFAULTS = {
    "face": None, "size": None, "bold": 0, "italic": 0, "charset": "",
    "unicode": 1, "stretchH": 100, "smooth": 1, "aa": 1,
    "padding": (0, 0, 0, 0), "spacing": (1, 1), "outline": 0,
}
_COMMON_DEFAULTS = {
    "lineHeight": 0, "base": 0, "scaleW": 0, "scaleH": 0, "pages": 0,
    "packed": 0, "alphaChnl": 0, "redChnl": 0, "greenChnl": 0, "blueChnl": 0,
}


class BmFont(object):
    """Mô tả font BMFont đã parse (đủ để build lại dạng text)."""

    def __init__(self, fmt: str) -> None:
        self.format = fmt  # "text" | "xml" | "binary"
        self.info = dict(_INFO_DEFAULTS)  # type: Dict[str, Any]
        self.common = dict(_COMMON_DEFAULTS)  # type: Dict[str, Any]
        self.pages = []  # type: List[Dict[str, Any]]
        self.chars = []  # type: List[Dict[str, Any]]
        self.kernings = []  # type: List[Dict[str, Any]]

    @property
    def face(self) -> Optional[str]:
        return self.info.get("face")

    @property
    def size(self) -> Optional[int]:
        return self.info.get("size")

    @property
    def char_ids(self) -> set:
        return set(c["id"] for c in self.chars)


def vi_coverage(char_ids) -> Dict[str, Any]:
    """Độ phủ tiếng Việt của tập codepoint (kiểu viCoverage của bản JS,
    dùng REQUIRED_VIETNAMESE của repo: 142 mục)."""
    req = required_vietnamese_chars()
    missing = [c for c in req if c not in char_ids]
    ascii_ok = all(c in char_ids for c in range(65, 91))
    ascii_ok = ascii_ok and all(c in char_ids for c in range(97, 123))
    if not missing:
        level = "full"
    elif len(missing) == len(req):
        level = "none"
    else:
        level = "partial"
    return {
        "level": level,
        "required": len(req),
        "covered": len(req) - len(missing),
        "missing_count": len(missing),
        "missing": ["U+%04X" % c for c in missing[:150]],
        "percent": round((len(req) - len(missing)) * 1000 / len(req)) / 10,
        "ascii_letters_ok": ascii_ok,
    }


def _parse_kv_line(line: str) -> Dict[str, Any]:
    out = {}  # type: Dict[str, Any]
    for m in _TEXT_KV_RE.finditer(line):
        key = m.group(1)
        raw = m.group(3) if m.group(3) is not None else m.group(2)
        out[key] = raw
    return out


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_int_tuple(value: Any, count: int, default: int = 0) -> tuple:
    parts = str(value or "").split(",")
    nums = []
    for i in range(count):
        nums.append(_to_int(parts[i], default) if i < len(parts) else default)
    return tuple(nums)


def _parse_text(data: bytes) -> Optional[BmFont]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if not re.match(r"\s*info\s", text):
        return None
    font = BmFont("text")
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        kv = _parse_kv_line(line)
        if line.startswith("info "):
            font.info["face"] = kv.get("face")
            font.info["size"] = (
                abs(_to_int(kv["size"])) if "size" in kv else None)
            for k in ("bold", "italic", "unicode", "stretchH", "smooth",
                      "aa", "outline"):
                if k in kv:
                    font.info[k] = _to_int(kv[k])
            if "charset" in kv:
                font.info["charset"] = kv["charset"]
            if "padding" in kv:
                font.info["padding"] = _parse_int_tuple(kv["padding"], 4)
            if "spacing" in kv:
                font.info["spacing"] = _parse_int_tuple(kv["spacing"], 2)
        elif line.startswith("common "):
            for k in ("lineHeight", "base", "scaleW", "scaleH", "pages",
                      "packed", "alphaChnl", "redChnl", "greenChnl",
                      "blueChnl"):
                if k in kv:
                    font.common[k] = _to_int(kv[k])
        elif line.startswith("page "):
            font.pages.append({
                "id": _to_int(kv.get("id")),
                "file": kv.get("file", ""),
            })
        elif line.startswith("char "):
            ch = {"id": _to_int(kv.get("id"))}
            for k in ("x", "y", "width", "height", "xoffset", "yoffset",
                      "xadvance", "page", "chnl"):
                ch[k] = _to_int(kv.get(k))
            font.chars.append(ch)
        elif line.startswith("kerning "):
            font.kernings.append({
                "first": _to_int(kv.get("first")),
                "second": _to_int(kv.get("second")),
                "amount": _to_int(kv.get("amount")),
            })
    if not font.chars:
        return None
    return font


def _parse_xml(data: bytes) -> Optional[BmFont]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if not re.match(r"\s*(<\?xml|<font)", text):
        return None
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    if root.tag != "font":
        return None
    font = BmFont("xml")
    info_el = root.find("info")
    if info_el is not None:
        font.info["face"] = info_el.get("face")
        size = info_el.get("size")
        font.info["size"] = abs(_to_int(size)) if size is not None else None
        for k in ("bold", "italic", "unicode", "stretchH", "smooth", "aa",
                  "outline"):
            if info_el.get(k) is not None:
                font.info[k] = _to_int(info_el.get(k))
        if info_el.get("charset") is not None:
            font.info["charset"] = info_el.get("charset")
        if info_el.get("padding") is not None:
            font.info["padding"] = _parse_int_tuple(info_el.get("padding"), 4)
        if info_el.get("spacing") is not None:
            font.info["spacing"] = _parse_int_tuple(info_el.get("spacing"), 2)
    common_el = root.find("common")
    if common_el is not None:
        for k in ("lineHeight", "base", "scaleW", "scaleH", "pages",
                  "packed", "alphaChnl", "redChnl", "greenChnl", "blueChnl"):
            if common_el.get(k) is not None:
                font.common[k] = _to_int(common_el.get(k))
    pages_el = root.find("pages")
    if pages_el is not None:
        for page_el in pages_el.findall("page"):
            font.pages.append({
                "id": _to_int(page_el.get("id")),
                "file": page_el.get("file", ""),
            })
    chars_el = root.find("chars")
    if chars_el is not None:
        for char_el in chars_el.findall("char"):
            ch = {"id": _to_int(char_el.get("id"))}
            for k in ("x", "y", "width", "height", "xoffset", "yoffset",
                      "xadvance", "page", "chnl"):
                ch[k] = _to_int(char_el.get(k))
            font.chars.append(ch)
    kernings_el = root.find("kernings")
    if kernings_el is not None:
        for k_el in kernings_el.findall("kerning"):
            font.kernings.append({
                "first": _to_int(k_el.get("first")),
                "second": _to_int(k_el.get("second")),
                "amount": _to_int(k_el.get("amount")),
            })
    if not font.chars:
        return None
    return font


def _parse_binary(data: bytes) -> Optional[BmFont]:
    if len(data) <= 4 or data[0:3] != b"BMF" or data[3] != 3:
        return None
    font = BmFont("binary")
    p = 4
    while p + 5 <= len(data):
        block_type = data[p]
        size = struct.unpack_from("<I", data, p + 1)[0]
        p += 5
        if size > len(data) - p:
            break
        body = data[p:p + size]
        if block_type == 1 and size > 15:
            font.info["size"] = abs(struct.unpack_from("<h", body, 0)[0])
            q = 14
            end = q
            while end < size and body[end] != 0:
                end += 1
            try:
                font.info["face"] = body[q:end].decode("utf-8")
            except UnicodeDecodeError:
                font.info["face"] = None
            if size >= 15:
                font.info["bold"] = body[2] if len(body) > 2 else 0
                font.info["italic"] = body[3] if len(body) > 3 else 0
                font.info["unicode"] = body[5] if len(body) > 5 else 1
                font.info["stretchH"] = (
                    struct.unpack_from("<H", body, 6)[0]
                    if len(body) >= 8 else 100)
                font.info["smooth"] = body[8] if len(body) > 8 else 1
                font.info["aa"] = body[9] if len(body) > 9 else 1
                if len(body) >= 14:
                    font.info["padding"] = tuple(body[10:14])
                    font.info["spacing"] = (body[12], body[13])
        elif block_type == 2 and size >= 15:
            (font.common["lineHeight"], font.common["base"],
             font.common["scaleW"], font.common["scaleH"],
             font.common["pages"]) = struct.unpack_from("<HHHHH", body, 0)
            font.common["packed"] = body[10] if len(body) > 10 else 0
            font.common["alphaChnl"] = body[11] if len(body) > 11 else 0
            font.common["redChnl"] = body[12] if len(body) > 12 else 0
            font.common["greenChnl"] = body[13] if len(body) > 13 else 0
            font.common["blueChnl"] = body[14] if len(body) > 14 else 0
        elif block_type == 3:
            parts = body.split(b"\x00")
            page_id = 0
            for part in parts:
                if not part:
                    continue
                try:
                    font.pages.append({
                        "id": page_id,
                        "file": part.decode("utf-8"),
                    })
                except UnicodeDecodeError:
                    pass
                page_id += 1
        elif block_type == 4:
            q = 0
            while q + 20 <= size:
                (cid, x, y, w, h, xo, yo, xa, page, chnl) = struct.unpack_from(
                    "<IhhhhhhhBB", body, q)
                font.chars.append({
                    "id": cid, "x": x, "y": y, "width": w, "height": h,
                    "xoffset": xo, "yoffset": yo, "xadvance": xa,
                    "page": page, "chnl": chnl,
                })
                q += 20
        elif block_type == 5:
            q = 0
            while q + 10 <= size:
                first, second, amount = struct.unpack_from("<IIh", body, q)
                font.kernings.append({
                    "first": first, "second": second, "amount": amount,
                })
                q += 10
        p += size
    if not font.chars:
        return None
    return font


def parse_bmfont(data: bytes) -> Optional[BmFont]:
    """Đọc BMFont (.fnt: text, XML hoặc nhị phân v3) → BmFont, hoặc None
    khi không nhận ra/không có char nào. Không ném lỗi (fail-closed)."""
    if not data:
        return None
    if len(data) > 4 and data[0:3] == b"BMF" and data[3] == 3:
        return _parse_binary(data)
    try:
        head = data[:64].decode("utf-8", errors="ignore")
    except UnicodeDecodeError:
        return None
    if re.match(r"\s*(<\?xml|<font)", head):
        return _parse_xml(data)
    if re.match(r"\s*info\s", head):
        return _parse_text(data)
    return None


def _escape_attr(value: str) -> str:
    return (value.replace("&", "&amp;").replace('"', "&quot;")
            .replace("<", "&lt;").replace(">", "&gt;"))


def build_bmfont_text(font: BmFont) -> bytes:
    """Build lại .fnt dạng text từ BmFont đã parse. Chỉ giữ các trường đã
    parse được; thứ tự dòng theo đúng chuẩn BMFont text. Raise PB3Error khi
    font không có char nào (không build file rỗng)."""
    if not font.chars:
        raise PB3Error("E_CORRUPT", "BMFont không có char nào để build",
                       where="bmfont")
    info = font.info
    lines = []
    lines.append(
        'info face="%s" size=%d bold=%d italic=%d charset="%s" unicode=%d '
        "stretchH=%d smooth=%d aa=%d padding=%s spacing=%s outline=%d" % (
            _escape_attr(info.get("face") or ""),
            info.get("size") or 0,
            info.get("bold", 0), info.get("italic", 0),
            info.get("charset", ""), info.get("unicode", 1),
            info.get("stretchH", 100), info.get("smooth", 1),
            info.get("aa", 1),
            ",".join(str(v) for v in info.get("padding", (0, 0, 0, 0))),
            ",".join(str(v) for v in info.get("spacing", (1, 1))),
            info.get("outline", 0)))
    common = font.common
    lines.append(
        "common lineHeight=%d base=%d scaleW=%d scaleH=%d pages=%d "
        "packed=%d alphaChnl=%d redChnl=%d greenChnl=%d blueChnl=%d" % (
            common.get("lineHeight", 0), common.get("base", 0),
            common.get("scaleW", 0), common.get("scaleH", 0),
            common.get("pages", 0), common.get("packed", 0),
            common.get("alphaChnl", 0), common.get("redChnl", 0),
            common.get("greenChnl", 0), common.get("blueChnl", 0)))
    for page in font.pages:
        lines.append('page id=%d file="%s"' % (
            page.get("id", 0), _escape_attr(page.get("file", ""))))
    lines.append("chars count=%d" % len(font.chars))
    for ch in font.chars:
        lines.append(
            "char id=%d x=%d y=%d width=%d height=%d xoffset=%d yoffset=%d "
            "xadvance=%d page=%d chnl=%d" % (
                ch.get("id", 0), ch.get("x", 0), ch.get("y", 0),
                ch.get("width", 0), ch.get("height", 0),
                ch.get("xoffset", 0), ch.get("yoffset", 0),
                ch.get("xadvance", 0), ch.get("page", 0), ch.get("chnl", 0)))
    if font.kernings:
        lines.append("kernings count=%d" % len(font.kernings))
        for k in font.kernings:
            lines.append("kerning first=%d second=%d amount=%d" % (
                k.get("first", 0), k.get("second", 0), k.get("amount", 0)))
    return ("\n".join(lines) + "\n").encode("utf-8")


def summarize(font: BmFont) -> Dict[str, Any]:
    """Tóm tắt để hiển thị/log: format, face, size, số char, độ phủ Việt."""
    return {
        "format": font.format,
        "face": font.face,
        "size": font.size,
        "chars": len(font.chars),
        "vietnamese": vi_coverage(font.char_ids),
    }
