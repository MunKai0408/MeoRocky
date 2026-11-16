# Plugin định dạng Unreal LOCRES (Claude 08, nhóm Unreal): chỉ đọc.
# Đọc file .locres nhị phân của Unreal (namespace -> key -> chuỗi đã dịch)
# thành LocTable (key="namespace/key", value=chuỗi đã dịch). Định dạng
# (đối chiếu pylocres / tài liệu công khai, chỉ dùng đặc tả):
#   v0 Legacy (không magic): u32 số namespace; mỗi namespace: FString tên
#       (thường UTF-16), u32 số key; mỗi key: FString key, u32 source_hash,
#       FString bản dịch (inline).
#   v1 Compact / v2 Optimized / v3 CityHash (magic 16 byte):
#       magic + u8 version + u64 offset bảng chuỗi; tại offset 25:
#       [u32 tổng số key nếu v>=2] + u32 số namespace; mỗi namespace:
#       [u32 hash namespace nếu v>=2] + FString tên + u32 số key; mỗi key:
#       [u32 hash key nếu v>=2] + FString key + u32 source_hash +
#       u32 chỉ số chuỗi. Bảng chuỗi tại offset: u32 số chuỗi; mỗi chuỗi:
#       FString + [u32 số lần tham chiếu nếu v>=2].
#   FString: i32 độ dài; 0=rỗng; >0=số byte UTF-8/ANSI (kể NUL cuối);
#       <0=số đơn vị mã UTF-16LE (kể NUL cuối). Hash chỉ để tra cứu nhanh,
#       parser không kiểm tra hash.
# Ghi/build bị từ chối E_WRITE_DISABLED (chỉ đọc). .locmeta dạng JSON đọc
# được bằng plugin json_text (không cần plugin riêng); .locmeta nhị phân
# gốc của Unreal chưa hỗ trợ.
# Kiểm tra: python3 menu.py plugin test unreal_locres
from __future__ import annotations

import struct
from typing import Any, Dict, List, Optional, Tuple

from core.errors import PB3Error
from sdk.formats import FormatPluginBase, make_entries
from sdk.unreal import read_fstring, write_fstring

MANIFEST = {
    "id": "unreal_locres",
    "name": "Unreal LOCRES localization",
    "version": "0.1.0",
    "api_version": 3,
    "kind": "format",
    "capabilities": ["read"],
    "maturity": "M2",
    "priority": 10,
    "true_cost": "cheap",
    "engines": ["unreal"],
    "match": {"extensions": [".locres"]},
    "limits": {"max_entry_bytes": 16 * 1024 * 1024, "hook_timeout_s": 10},
    "entry": "plugin:UnrealLocresPlugin",
    "selftest": True,
    "docs": "Đọc file .locres của Unreal Engine (v0 Legacy, v1 Compact, "
            "v2 Optimized, v3 CityHash) thành bảng localization: key là "
            "\"namespace/key\", value là chuỗi đã dịch. Chỉ đọc; build/verify "
            "bị từ chối E_WRITE_DISABLED. .locmeta dạng JSON đọc được bằng "
            "plugin json_text; .locmeta nhị phân gốc chưa hỗ trợ.",
}

LOCRES_MAGIC = b"\x0e\x14\x74\x75\x67\x4a\x03\xfc\x4a\x15\x90\x9d\xc3\x37\x7f\x1b"
MAX_LOCRES_ENTRIES = 100000


class _Cursor:
    """Con trỏ đọc trên bytes cho parser .locres (cắt cụt -> E_CORRUPT)."""

    def __init__(self, data: bytes, where: str = ""):
        self._data = data
        self._pos = 0
        self._where = where

    def tell(self) -> int:
        return self._pos

    def seek(self, pos: int) -> None:
        if pos < 0 or pos > len(self._data):
            raise PB3Error("E_CORRUPT", "vị trí đọc %d ngoài file" % pos, where=self._where)
        self._pos = pos

    def u32(self) -> int:
        if self._pos + 4 > len(self._data):
            raise PB3Error("E_CORRUPT", "file .locres cắt cụt", where=self._where)
        (v,) = struct.unpack_from("<I", self._data, self._pos)
        self._pos += 4
        return v

    def fstring(self) -> str:
        s, self._pos = read_fstring(self._data, self._pos, self._where)
        return s


def build_locres(namespaces, *, version: int = 3) -> bytes:
    """Dựng file .locres trong RAM — CHỈ để làm fixture kiểm thử.

    namespaces: [(tên, [(key, bản_dịch, source_hash|None), ...]), ...].
    version: 0 (Legacy, chuỗi inline), 1 (Compact), 2 (Optimized),
    3 (CityHash). Hash namespace/key ghi 0 vì parser không kiểm tra hash
    (hash chỉ để tra cứu nhanh, không phải checksum)."""
    if version not in (0, 1, 2, 3):
        raise ValueError("build_locres chỉ hỗ trợ version 0-3")
    if version == 0:
        out = struct.pack("<I", len(namespaces))
        for (ns, entries) in namespaces:
            out += write_fstring(ns, utf16=True)
            out += struct.pack("<I", len(entries))
            for (key, text, h) in entries:
                out += write_fstring(key)
                out += struct.pack("<I", h or 0)
                out += write_fstring(text)
        return out
    strings: List[str] = []
    index_of: Dict[str, int] = {}
    for (_ns, entries) in namespaces:
        for (_key, text, _h) in entries:
            if text not in index_of:
                index_of[text] = len(strings)
                strings.append(text)
    out = bytearray()
    out += LOCRES_MAGIC
    out += struct.pack("<B", version)
    out += b"\x00" * 8  # chỗ giữ offset bảng chuỗi
    if version >= 2:
        out += struct.pack("<I", sum(len(e) for (_ns, e) in namespaces))
    out += struct.pack("<I", len(namespaces))
    for (ns, entries) in namespaces:
        if version >= 2:
            out += struct.pack("<I", 0)  # hash namespace
        out += write_fstring(ns)
        out += struct.pack("<I", len(entries))
        for (key, text, h) in entries:
            if version >= 2:
                out += struct.pack("<I", 0)  # hash key
            out += write_fstring(key)
            out += struct.pack("<I", h or 0)
            out += struct.pack("<I", index_of[text])
    text_offset = len(out)
    out[17:25] = struct.pack("<Q", text_offset)
    out += struct.pack("<I", len(strings))
    for s in strings:
        out += write_fstring(s)
        if version >= 2:
            refcount = sum(1 for (_ns, e) in namespaces
                           for (_k, t, _h) in e if t == s)
            out += struct.pack("<I", refcount)
    return bytes(out)


class UnrealLocresPlugin(FormatPluginBase):
    MANIFEST = MANIFEST
    EXTENSIONS = (".locres",)
    FLAVOR = "locres"
    SELFTEST_BASE = build_locres(
        [("UI", [("Play", "Chơi", 0), ("Quit", "Thoát", 0)])], version=3)

    def _sniff(self, head: bytes, path: str) -> float:
        if head.startswith(LOCRES_MAGIC):
            return 0.9
        return 0.0

    # ---- đọc ----
    def _parse_bytes(self, data: bytes, path: str, locale: Optional[str]) -> Dict[str, Any]:
        if not data:
            raise PB3Error("E_CORRUPT", "file .locres rỗng", where=path)
        if data[:16] == LOCRES_MAGIC:
            if len(data) < 25:
                raise PB3Error("E_CORRUPT", "file .locres cắt cụt (thiếu header)", where=path)
            version = data[16]
            if version < 1 or version > 3:
                raise PB3Error("E_UNSUPPORTED_FORMAT",
                               "phiên bản .locres %d chưa hỗ trợ (hỗ trợ 0-3)" % version,
                               where=path)
            (str_offset,) = struct.unpack_from("<Q", data, 17)
            if str_offset < 25 or str_offset >= len(data):
                raise PB3Error("E_CORRUPT", "offset bảng chuỗi %d ngoài file" % str_offset,
                               where=path)
            return self._parse_modern(data, path, locale, version, str_offset)
        return self._parse_legacy(data, path, locale)

    def _check_count(self, n: int, what: str, path: str) -> None:
        if n > MAX_LOCRES_ENTRIES:
            raise PB3Error("E_TOO_LARGE", "%s %d vượt giới hạn %d"
                           % (what, n, MAX_LOCRES_ENTRIES), where=path)

    def _parse_modern(self, data: bytes, path: str, locale: Optional[str],
                      version: int, str_offset: int) -> Dict[str, Any]:
        r = _Cursor(data, path)
        r.seek(25)
        if version >= 2:
            self._check_count(r.u32(), "tổng số key", path)
        ns_count = r.u32()
        self._check_count(ns_count, "số namespace", path)
        namespaces: List[Tuple[str, List[Tuple[str, int, int]]]] = []
        total = 0
        for _ in range(ns_count):
            if version >= 2:
                r.u32()  # hash namespace (không kiểm tra)
            name = r.fstring()
            key_count = r.u32()
            self._check_count(key_count, "số key", path)
            keys: List[Tuple[str, int, int]] = []
            for _ in range(key_count):
                if version >= 2:
                    r.u32()  # hash key (không kiểm tra)
                key = r.fstring()
                source_hash = r.u32()
                str_index = r.u32()
                keys.append((key, source_hash, str_index))
                total += 1
                if total > MAX_LOCRES_ENTRIES:
                    raise PB3Error("E_TOO_LARGE", "vượt giới hạn %d entry"
                                   % MAX_LOCRES_ENTRIES, where=path)
            namespaces.append((name, keys))
        if r.tell() > str_offset:
            raise PB3Error("E_CORRUPT", "phần key vượt quá offset bảng chuỗi", where=path)
        r.seek(str_offset)
        str_count = r.u32()
        self._check_count(str_count, "số chuỗi", path)
        strings: List[str] = []
        for _ in range(str_count):
            strings.append(r.fstring())
            if version >= 2:
                r.u32()  # số lần tham chiếu
        if r.tell() != len(data):
            raise PB3Error("E_CORRUPT", "dữ liệu thừa sau bảng chuỗi (%d byte)"
                           % (len(data) - r.tell()), where=path)
        pairs: List[Tuple] = []
        for (ns, keys) in namespaces:
            for (key, source_hash, str_index) in keys:
                if str_index >= len(strings):
                    raise PB3Error("E_CORRUPT", "chỉ số chuỗi %d vượt bảng (%d chuỗi)"
                                   % (str_index, len(strings)), where=path)
                full_key = (ns + "/" + key) if ns else key
                pairs.append((full_key, strings[str_index], "ok",
                              ["source_hash=0x%08X" % source_hash]))
        entries, conflicts = make_entries(pairs, path, len(data), "utf-8", locale)
        return {"entries": entries,
                "schema_hint": {"flavor": "locres", "version": version,
                                "namespaces": [ns for (ns, _k) in namespaces]},
                "stats": {"namespaces": len(namespaces), "keys": total, "version": version},
                "conflicts": conflicts, "warnings": []}

    def _parse_legacy(self, data: bytes, path: str, locale: Optional[str]) -> Dict[str, Any]:
        r = _Cursor(data, path)
        ns_count = r.u32()
        self._check_count(ns_count, "số namespace", path)
        pairs: List[Tuple] = []
        namespaces: List[str] = []
        total = 0
        for _ in range(ns_count):
            name = r.fstring()
            key_count = r.u32()
            self._check_count(key_count, "số key", path)
            namespaces.append(name)
            for _ in range(key_count):
                key = r.fstring()
                source_hash = r.u32()
                text = r.fstring()
                full_key = (name + "/" + key) if name else key
                pairs.append((full_key, text, "ok",
                              ["source_hash=0x%08X" % source_hash]))
                total += 1
                if total > MAX_LOCRES_ENTRIES:
                    raise PB3Error("E_TOO_LARGE", "vượt giới hạn %d entry"
                                   % MAX_LOCRES_ENTRIES, where=path)
        if r.tell() != len(data):
            raise PB3Error("E_CORRUPT", "dữ liệu thừa sau phần legacy (%d byte)"
                           % (len(data) - r.tell()), where=path)
        entries, conflicts = make_entries(pairs, path, len(data), "utf-8", locale)
        return {"entries": entries,
                "schema_hint": {"flavor": "locres", "version": 0,
                                "namespaces": namespaces},
                "stats": {"namespaces": len(namespaces), "keys": total, "version": 0},
                "conflicts": conflicts, "warnings": []}
