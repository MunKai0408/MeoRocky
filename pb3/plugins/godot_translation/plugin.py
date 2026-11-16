# Plugin định dạng Godot .translation (Claude 08, nhóm Godot): CHỈ ĐỌC.
# Đọc file .translation nhị phân (magic "TR") thành LocTable
# (key = msgid, value = msgstr). Ghi bị từ chối E_WRITE_DISABLED (kế thừa
# hành vi chỉ đọc của FormatPluginBase như sqlite_loc).
#
# Layout .translation (theo nhiệm vụ Claude 08; builder ở
# tests/fixtures/godot/builder.py):
#   magic "TR" (2 byte), version u32 LE (= 1),
#   locale: u32 độ dài + UTF-8 (có thể rỗng),
#   count u32 LE, rồi mỗi entry: msgid_len u32 + msgid UTF-8,
#   msgstr_len u32 + msgstr UTF-8.
# Giới hạn trung thực: layout "TR" này là định dạng theo nhiệm vụ, CHƯA
# đối chiếu được với file .translation do Godot thật xuất ra (file thật
# là binary resource / OptimizedTranslation, bắt đầu bằng "RSRC" — gặp
# loại này plugin báo E_UNSUPPORTED_FORMAT rõ ràng thay vì đọc sai).
# Không hỗ trợ context/plural (msgid có context nối bằng ký tự EOT
# U+0004 thì key giữ nguyên cả chuỗi, như gettext của Godot).
# Kiểm tra: python3 menu.py plugin test godot_translation
from __future__ import annotations

import struct
from typing import Any, Dict, List, Optional, Tuple

from core.errors import PB3Error
from sdk.formats import FormatPluginBase, make_entries

MANIFEST = {
    "id": "godot_translation",
    "name": "Godot .translation",
    "version": "1.0.0",
    "api_version": 3,
    "kind": "format",
    "capabilities": ["read"],
    "maturity": "M2",
    "priority": 10,
    "true_cost": "cheap",
    "engines": ["godot"],
    "match": {"extensions": [".translation"]},
    "limits": {"max_entry_bytes": 16 * 1024 * 1024, "hook_timeout_s": 20},
    "entry": "plugin:GodotTranslationPlugin",
    "selftest": True,
    "docs": "Đọc file .translation nhị phân của Godot (magic \"TR\", version 1) "
            "thành bảng msgid -> msgstr (chỉ đọc, không ghi). Locale lấy từ "
            "file. File .translation dạng binary resource Godot (RSRC / "
            "OptimizedTranslation) chưa hỗ trợ.",
}

TR_MAGIC = b"TR"
RSRC_MAGIC = b"RSRC"
TR_VERSION = 1
MAX_ENTRIES = 100000


def _build_selftest_bytes() -> bytes:
    """File .translation nhỏ trong RAM cho selftest (không import tests)."""
    pairs = [("Play", "Chơi"), ("Quit", "Thoát")]
    loc = b"vi"
    out = bytearray()
    out += TR_MAGIC
    out += struct.pack("<I", TR_VERSION)
    out += struct.pack("<I", len(loc)) + loc
    out += struct.pack("<I", len(pairs))
    for msgid, msgstr in pairs:
        a = msgid.encode("utf-8")
        b = msgstr.encode("utf-8")
        out += struct.pack("<I", len(a)) + a
        out += struct.pack("<I", len(b)) + b
    return bytes(out)


def parse_translation_bytes(data: bytes, path: str):
    """(locale, [(msgid, msgstr), ...]). Lỗi -> PB3Error có mã rõ.

    Dùng chung cho plugin và test (không import tests từ plugin)."""
    if data[:4] == RSRC_MAGIC:
        raise PB3Error("E_UNSUPPORTED_FORMAT",
                       "file .translation dạng binary resource Godot (RSRC / "
                       "OptimizedTranslation) chưa hỗ trợ",
                       where=path)
    if len(data) < 2 or data[:2] != TR_MAGIC:
        raise PB3Error("E_CORRUPT", "không phải file .translation (magic \"TR\")",
                       where=path)
    pos = 2

    def take_u32() -> int:
        nonlocal pos
        if pos + 4 > len(data):
            raise PB3Error("E_CORRUPT", "file .translation cắt cụt", where=path)
        value = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        return value

    def take_str() -> str:
        nonlocal pos
        n = take_u32()
        if n > len(data) - pos:
            raise PB3Error("E_CORRUPT", "chuỗi dài %d vượt cuối file" % n, where=path)
        raw = data[pos:pos + n]
        pos += n
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            raise PB3Error("E_CORRUPT", "chuỗi không phải UTF-8", where=path)

    version = take_u32()
    if version != TR_VERSION:
        raise PB3Error("E_UNSUPPORTED_FORMAT",
                       ".translation version %d chưa hỗ trợ (chỉ version 1)" % version,
                       where=path)
    locale = take_str()
    count = take_u32()
    if count > MAX_ENTRIES:
        raise PB3Error("E_TOO_LARGE",
                       "vượt giới hạn %d entry (file có %d)" % (MAX_ENTRIES, count),
                       where=path)
    pairs: List[Tuple[str, str]] = []
    for _ in range(count):
        msgid = take_str()
        msgstr = take_str()
        pairs.append((msgid, msgstr))
    if pos != len(data):
        raise PB3Error("E_CORRUPT",
                       "thừa %d byte sau bảng translation" % (len(data) - pos),
                       where=path)
    return locale, pairs


class GodotTranslationPlugin(FormatPluginBase):
    MANIFEST = MANIFEST
    EXTENSIONS = (".translation",)
    FLAVOR = "godot_translation"
    SELFTEST_BASE = _build_selftest_bytes()

    def _sniff(self, head: bytes, path: str) -> float:
        if head.startswith(TR_MAGIC):
            return 0.9
        return 0.0

    def _parse_bytes(self, data: bytes, path: str, locale: Optional[str]) -> Dict[str, Any]:
        file_locale, pairs = parse_translation_bytes(data, path)
        loc = file_locale or locale
        entries, conflicts = make_entries(
            [(k, v, "ok", []) for (k, v) in pairs], path, len(data), "utf-8", loc)
        return {"entries": entries,
                "schema_hint": {"flavor": "godot_translation", "version": TR_VERSION,
                                "locale": file_locale or ""},
                "stats": {"messages": len(pairs), "locale": file_locale or ""},
                "conflicts": conflicts, "warnings": []}

    def parse(self, ctx, cand):
        # Ghi đè lớp nền để locale của BẢNG lấy từ file (lớp nền dùng
        # ctx.locale.code, ở đây thường là None) — như sqlite_loc.
        from sdk.types import LocTable
        data = ctx.vfs.read(cand.path, max_bytes=ctx.limits.max_entry_bytes)
        if len(data) > self.manifest.max_entry_bytes:
            raise PB3Error("E_TOO_LARGE", "file %d byte > giới hạn %d"
                           % (len(data), self.manifest.max_entry_bytes), where=cand.path)
        res = self._parse_bytes(data, cand.path, ctx.locale.code)
        loc = res["schema_hint"].get("locale") or ctx.locale.code
        t = LocTable(res["entries"], loc, self.id,
                     dict(res.get("schema_hint") or {"flavor": "godot_translation"}),
                     dict(res.get("stats") or {}),
                     list(res.get("conflicts") or []), list(res.get("warnings") or []))
        t.stats = {"entries": len(t.entries), **t.count_by_status(), **(t.stats or {})}
        return t
