"""Builder dựng font TTF tối thiểu TỔNG HỢP cho test (chỉ stdlib).

KHÔNG dùng font thật của game: mọi glyph đều rỗng (0 contour) hoặc
composite trỏ tới glyph rỗng khác — đủ để kiểm cmap/merge/checksum,
không đủ để hiển thị. Builder độc lập với `sdk/font.py` (tự tính
checksum, tự dựng cmap) để test không đi vòng qua mã sản phẩm.
"""
from __future__ import annotations

import math
import struct


def _csum(data: bytes) -> int:
    buf = data + b"\x00" * ((-len(data)) % 4)
    total = 0
    for i in range(0, len(buf), 4):
        total = (total + struct.unpack_from(">I", buf, i)[0]) & 0xFFFFFFFF
    return total


def _cmap_format4(mapping):
    """mapping: dict codepoint -> gid. Đoạn delta đều thì idDelta, còn lại range."""
    items = sorted((cp, g) for cp, g in mapping.items() if g != 0 and cp != 0xFFFF)
    runs = []
    for cp, g in items:
        if runs and runs[-1][-1][0] == cp - 1:
            runs[-1].append((cp, g))
        else:
            runs.append([(cp, g)])
    segs = []
    for run in runs:
        deltas = {(g - cp) & 0xFFFF for cp, g in run}
        if len(deltas) == 1:
            segs.append((run[0][0], run[-1][0], deltas.pop(), False))
        else:
            segs.append((run[0][0], run[-1][0], 0, True))
    segs.append((0xFFFF, 0xFFFF, 1, False))
    n = len(segs)
    n_range = sum(e - s + 1 for s, e, _d, rm in segs if rm)
    length = 16 + 8 * n + 2 * n_range
    sr = 2 * (2 ** int(math.floor(math.log2(n))))
    es = int(math.log2(sr // 2))
    rs = 2 * n - sr
    out = bytearray()
    out += struct.pack(">HHHHHHH", 4, length, 0, n * 2, sr, es, rs)
    for _s, e, _d, _r in segs:
        out += struct.pack(">H", e)
    out += struct.pack(">H", 0)
    for s, _e, _d, _r in segs:
        out += struct.pack(">H", s)
    for _s, _e, d, _r in segs:
        out += struct.pack(">h", d - 0x10000 if d > 0x7FFF else d)
    seen = 0
    for i, (_s, _e, _d, rm) in enumerate(segs):
        if not rm:
            out += struct.pack(">H", 0)
        else:
            out += struct.pack(">H", 2 * (n - i + seen))
            seen += _e - _s + 1
    for (s, e, _d, rm), run in zip(segs, runs + [[(0xFFFF, 0)]]):
        if rm:
            for _cp, g in run:
                out += struct.pack(">H", g)
    return bytes(out)


def _cmap_format12(mapping):
    items = sorted((cp, g) for cp, g in mapping.items() if g != 0)
    groups = []
    for cp, g in items:
        if groups and groups[-1][1] == cp - 1 and groups[-1][2] + (cp - groups[-1][0]) == g:
            groups[-1][1] = cp
        else:
            groups.append([cp, cp, g])
    out = bytearray()
    out += struct.pack(">HHIII", 12, 0, 16 + 12 * len(groups), 0, len(groups))
    for s, e, g0 in groups:
        out += struct.pack(">III", s, e, g0)
    return bytes(out)


def _name_table(family: str) -> bytes:
    def rec(platform, encoding, lang, name_id, text):
        raw = text.encode("utf-16-be") if platform in (0, 3) else text.encode("latin-1")
        return (platform, encoding, lang, name_id, raw)
    records = [
        rec(3, 1, 0x409, 1, family),
        rec(3, 1, 0x409, 2, "Regular"),
        rec(3, 1, 0x409, 4, family + " Regular"),
        rec(3, 1, 0x409, 6, family.replace(" ", "") + "-Regular"),
    ]
    header = struct.pack(">HHH", 0, len(records), 6 + 12 * len(records))
    body = bytearray()
    blob = bytearray()
    for platform, encoding, lang, name_id, raw in records:
        body += struct.pack(">HHHHHH", platform, encoding, lang, name_id, len(raw), len(blob))
        blob += raw
    return bytes(header) + bytes(body) + bytes(blob)


def build_ttf(codepoints, *, name="TestFont", cmap_format=4, composite=None):
    """Dựng một font TTF tối thiểu.

    codepoints: các codepoint có trong cmap (glyph rỗng, trừ glyph 0).
    composite: dict {codepoint: [codepoint_component, ...]} — glyph của
      codepoint đó là composite trỏ tới glyph của các component.
    Trả bytes font hợp lệ (checksum đúng, parse được bằng sdk.font).
    """
    cps = sorted(set(int(c) for c in codepoints))
    mapping = {}
    gid_of = {}
    next_gid = 1
    for cp in cps:
        mapping[cp] = next_gid
        gid_of[cp] = next_gid
        next_gid += 1
    num_glyphs = next_gid
    composite = dict(composite or {})
    # Glyph: 0 = rỗng; glyph thường = 0 contour; composite = -1 contour.
    glyphs = [b""]
    for cp in cps:
        if cp in composite:
            parts = bytearray(struct.pack(">hhhhh", -1, 0, 0, 0, 0))
            comps = composite[cp]
            for j, comp_cp in enumerate(comps):
                flags = 0x0001 | 0x0002  # args là word + là tọa độ x/y
                if j < len(comps) - 1:
                    flags |= 0x0020  # MORE_COMPONENTS
                parts += struct.pack(">HHhh", flags, gid_of[comp_cp], 0, 0)
            glyphs.append(bytes(parts))
        else:
            glyphs.append(struct.pack(">hhhhh", 0, 0, 0, 0, 0))
    glyf = b"".join(glyphs)
    loca = [0]
    for g in glyphs:
        loca.append(loca[-1] + len(g))
    if loca[-1] % 2:
        raise AssertionError("glyf fixture phải có độ dài chẵn để dùng loca short")
    tables = {}
    tables["head"] = struct.pack(
        ">IIIIHHqqhhhhHHhhh", 0x00010000, 0x00010000, 0, 0x5F0F3CF5,
        0, 1000, 0, 0, 0, 0, 0, 0, 0, 2, 0, 0, 0)
    tables["hhea"] = _hhea(num_glyphs)
    tables["maxp"] = struct.pack(">I" + "H" * 14, 0x00010000, num_glyphs,
                                 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    tables["hmtx"] = b"".join(struct.pack(">Hh", 600, 0) for _ in range(num_glyphs))
    tables["loca"] = b"".join(struct.pack(">H", o // 2) for o in loca)
    tables["glyf"] = glyf
    sub = _cmap_format4(mapping) if cmap_format == 4 else _cmap_format12(mapping)
    plat, enc = (3, 1) if cmap_format == 4 else (3, 10)
    tables["cmap"] = struct.pack(">HH", 0, 1) + struct.pack(">HHI", plat, enc, 12) + sub
    tables["name"] = _name_table(name)
    tables["OS/2"] = bytes(68)
    tables["post"] = struct.pack(">Iiiiii", 0x00030000, 0, 0, 0, 0, 0)
    return _assemble(tables)


def _hhea(num_glyphs):
    # hhea 36 byte: version + ascender/descender/lineGap + advanceWidthMax +
    # minLSB/minRSB/xMaxExtent + caretRise/Run/Offset + reserved x4 +
    # metricDataFormat + numberOfHMetrics.
    return struct.pack(">IhhhHhhhhhhhhhhhH", 0x00010000, 800, -200, 0, 1000,
                       0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, num_glyphs)


def _assemble(tables):
    tags = sorted(tables)
    num = len(tags)
    sr = 16 * (2 ** int(math.floor(math.log2(num))))
    es = int(math.log2(sr // 16))
    rs = num * 16 - sr
    header_len = 12 + 16 * num
    offset = (header_len + 3) // 4 * 4
    records = []
    body = bytearray()
    for tag in tags:
        raw = tables[tag]
        pad = (-len(raw)) % 4
        records.append((tag, _csum(raw), offset, len(raw)))
        body += raw + b"\x00" * pad
        offset += len(raw) + pad
    out = bytearray()
    out += struct.pack(">IHHHH", 0x00010000, num, sr, es, rs)
    for tag, cs, off, ln in records:
        out += tag.encode("latin-1") + struct.pack(">III", cs, off, ln)
    out += body
    # checkSumAdjustment: tổng toàn file (adjustment = 0) -> 0xB1B0AFBA.
    head_off = dict((t, o) for t, _c, o, _l in records)["head"]
    padded = bytes(out) + b"\x00" * ((-len(out)) % 4)
    total = 0
    for i in range(0, len(padded), 4):
        total = (total + struct.unpack_from(">I", padded, i)[0]) & 0xFFFFFFFF
    struct.pack_into(">I", out, head_off + 8, (0xB1B0AFBA - total) & 0xFFFFFFFF)
    return bytes(out)


# ------------------------------------------------------------------ fixture sẵn
def ascii_cps():
    """ASCII in được (0x20–0x7E)."""
    return list(range(0x20, 0x7F))


def vietnamese_cps():
    """Toàn bộ ký tự tiếng Việt bắt buộc (142 mục = 134 chữ cái + 8 dấu, theo sdk.font)."""
    from sdk.font import REQUIRED_VIETNAMESE
    return sorted(REQUIRED_VIETNAMESE)


def build_font_missing_vietnamese():
    """Font thiếu hết chữ ghép sẵn + dấu (chỉ có ASCII + ă â đ ê ô ơ ư)."""
    base = ascii_cps() + [0x103, 0xE2, 0x111, 0xEA, 0xF4, 0x1A1, 0x1B0]
    return build_ttf(base, name="GameFont")


def build_font_full_vietnamese():
    """Font đủ tiếng Việt (donor tổng hợp cho test gộp)."""
    return build_ttf(ascii_cps() + vietnamese_cps(), name="DonorFont")


def build_font_with_composite():
    """Donor có glyph composite: Ắ (U+1EAE) = Â (U+00C2) + dấu sắc (U+0301)."""
    cps = ascii_cps() + vietnamese_cps()
    return build_ttf(cps, name="DonorComposite",
                     composite={0x1EAE: [0xC2, 0x301]})


def build_otf_stub():
    """Font 'OTF' giả: cùng bảng với TTF nhưng magic OTTO (chỉ để test kind)."""
    raw = bytearray(build_ttf(ascii_cps(), name="OtfFont"))
    raw[0:4] = b"OTTO"
    return bytes(raw)


def build_ttc_stub():
    """Font collection giả (chỉ magic, đủ để test từ chối)."""
    return b"ttcf\x00\x01\x00\x00" + bytes(32)


def build_ttc(faces):
    """Dựng .ttc v1.0 thật từ list font rời (độc lập với sdk.font).

    Header ttcf + bảng offset từng face + bảng của từng face với offset
    TUYỆT ĐỐI từ đầu file (theo đặc tả TTC — face có thể chia sẻ bảng,
    ở đây mỗi face dùng bảng riêng). Checksum từng bảng copy nguyên từ
    font gốc (bytes bảng không đổi nên checksum vẫn đúng).
    """
    n = len(faces)
    out = bytearray(b"ttcf" + struct.pack(">II", 0x00010000, n)
                    + b"\x00" * (4 * n))
    face_offsets = []
    for face in faces:
        while len(out) % 4:
            out += b"\x00"
        face_off = len(out)
        face_offsets.append(face_off)
        num = struct.unpack_from(">H", face, 4)[0]
        dir_len = 12 + 16 * num
        out += b"\x00" * dir_len
        records = []
        for i in range(num):
            rec = 12 + 16 * i
            tag = face[rec:rec + 4]
            cs, _off, ln = struct.unpack_from(">III", face, rec + 4)
            raw = face[_off:_off + ln]
            while len(out) % 4:
                out += b"\x00"
            abs_off = len(out)
            out += raw
            records.append((tag, cs, abs_off, ln))
        sfnt = struct.unpack_from(">I", face, 0)[0]
        search_range, entry_selector, range_shift = struct.unpack_from(
            ">HHH", face, 6)
        struct.pack_into(">IHHHH", out, face_off, sfnt, num,
                         search_range, entry_selector, range_shift)
        for i, (tag, cs, abs_off, ln) in enumerate(records):
            rec = face_off + 12 + 16 * i
            out[rec:rec + 4] = tag
            struct.pack_into(">III", out, rec + 4, cs, abs_off, ln)
    for i, fo in enumerate(face_offsets):
        struct.pack_into(">I", out, 12 + 4 * i, fo)
    return bytes(out)
