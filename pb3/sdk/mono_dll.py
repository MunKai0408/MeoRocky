"""Trich + va chuoi literal .NET trong Assembly-CSharp.dll (Unity Mono, F4).

Game Unity Mono (khong phai IL2CPP) khong co global-metadata.dat: text
nam trong heap #US cua metadata .NET, duoc nap bang lenh IL `ldstr`
(opcode 0x72 + token 0x70xxxxxx, xxxxxx = offset trong heap #US).
Module nay doc chinh xac theo ECMA-335 (khong quet tho toan file):

- PE: DOS stub + COFF + optional header (PE32/PE32+) + section table,
  data directory 14 = CLI header; RVA -> file offset qua section.
- Metadata root (magic BSJB 0x424A5342): cac stream #~ (bang),
  #Strings, #US, #GUID, #Blob.
- Bang #~: chi can doc Module/TypeRef/TypeDef/Field de toi MethodDef
  (RVA than method + ten); than method Tiny/Fat -> quet IL tim ldstr.
- Heap #US: blob = do dai nen (compressed length) + UTF-16LE + 1 byte
  cuoi (0x00/0x01, giu nguyen khi va); offset 0 la blob rong du tru.

VA (fail-closed, khong doi kich thuoc file / offset / metadata):
- Ban dich ngan hon hoac bang (theo byte blob): va tai cho trong cung
  blob, cap nhat prefix do dai, dem NUL phan thua.
- Ban dich dai hon: DOI BLOB trong heap — dung lai slot trong do
  giai phong tu cac blob da rut ngan truoc do trong cung lan va
  (free-list noi bo heap #US), roi cap nhat MOI toan tu token ldstr
  tro toi offset cu (4 byte, cung kich thuoc). Khong doi kich thuoc
  stream, khong doi metadata, khong doi kich thuoc file.
- Dai hon ma khong con slot trong du lon -> bo qua chuoi do, ghi ro
  trong bao cao (ma E_TOO_LONG), khong ghi lang le sai, khong cat
  ngan ban dich.
- DLL ky strong-name (CLI Flags & 0x8): TU CHOI toan bo (E_STRONG_NAME)
  vi va byte se pha chu ky, runtime tu choi load.
- Verify sau khi va: doc lai toan bo literal tu bytes da va, doi
  chieu tung site da va + so site + kich thuoc file khong doi; sai
  -> E_VERIFY_FAILED, khong tra bytes.

Chi thu vien chuan, Python 3.9. Khong ky lai IPA, khong bypass DRM.
"""
from __future__ import annotations

import os
import struct
from typing import Any, Dict, List, Optional, Tuple

from core.errors import PB3Error

TABLE_FILE = "mono-strings.json"
MANIFEST_FILE = "mono-strings.manifest.json"
PATCHED_FILE = "Assembly-CSharp.patched.dll"
PATCHED_SIDECAR = "Assembly-CSharp.patched.json"
# DLL Mono thuong vai MB; tran nay de khong nap ca tram MB vao RAM.
MAX_MEMBER_BYTES = 64 * 1024 * 1024

_MZ = b"MZ"
_PE = b"PE\x00\x00"
_BSJB = 0x424A5342
_CLI_FLAGS_STRONG_NAME = 0x00000008
_US_TOKEN_MASK = 0x70000000

# Thu tu bang ECMA-335 (chi can ten de doc kich thuoc hang khi bo qua).
_TABLE_NAMES = (
    "Module", "TypeRef", "TypeDef", "FieldPtr", "Field", "MethodPtr",
    "MethodDef", "ParamPtr", "Param", "InterfaceImpl", "MemberRef",
    "Constant", "CustomAttribute", "FieldMarshal", "DeclSecurity",
    "ClassLayout", "FieldLayout", "StandAloneSig", "EventMap",
    "EventPtr", "Event", "PropertyMap", "PropertyPtr", "Property",
    "MethodSemantics", "MethodImpl", "ModuleRef", "TypeSpec",
    "ImplMap", "FieldRVA", "ENCLog", "ENCMap", "Assembly",
    "AssemblyProcessor", "AssemblyOS", "AssemblyRef",
    "AssemblyRefProcessor", "AssemblyRefOS", "File", "ExportedType",
    "ManifestResource", "NestedClass", "GenericParam", "MethodSpec",
    "GenericParamConstraint",
)
_MODULE = 0
_TYPEREF = 1
_TYPEDEF = 2
_FIELD = 4
_METHODDEF = 6
_PARAM = 8


def _u16(data: bytes, off: int, where: str) -> int:
    if off < 0 or off + 2 > len(data):
        raise PB3Error("E_CORRUPT", "doc vuot qua du lieu (2 byte tai %d)" % off,
                       where=where)
    return struct.unpack_from("<H", data, off)[0]


def _u32(data: bytes, off: int, where: str) -> int:
    if off < 0 or off + 4 > len(data):
        raise PB3Error("E_CORRUPT", "doc vuot qua du lieu (4 byte tai %d)" % off,
                       where=where)
    return struct.unpack_from("<I", data, off)[0]


def _need(data: bytes, off: int, size: int, where: str) -> None:
    if off < 0 or size < 0 or off + size > len(data):
        raise PB3Error("E_CORRUPT",
                       "vung du lieu [%d:%d] vuot qua file %d byte"
                       % (off, off + size, len(data)), where=where)


def _compressed_len(data: bytes, off: int, where: str) -> Tuple[int, int]:
    """Do dai nen ECMA-335 II.23.2 tai off -> (do_dai_blob, so_byte_prefix)."""
    if off >= len(data):
        raise PB3Error("E_CORRUPT", "blob #US vuot qua heap (offset %d)" % off,
                       where=where)
    b0 = data[off]
    if b0 & 0x80 == 0:
        return b0, 1
    if b0 & 0xC0 == 0x80:
        if off + 2 > len(data):
            raise PB3Error("E_CORRUPT", "prefix do dai #US cat cut", where=where)
        return ((b0 & 0x3F) << 8) | data[off + 1], 2
    if b0 & 0xE0 == 0xC0:
        if off + 4 > len(data):
            raise PB3Error("E_CORRUPT", "prefix do dai #US cat cut", where=where)
        return (((b0 & 0x1F) << 24) | (data[off + 1] << 16)
                | (data[off + 2] << 8) | data[off + 3]), 4
    raise PB3Error("E_CORRUPT", "prefix do dai #US khong hop le 0x%02x" % b0,
                   where=where)


def _compressed_len_bytes(length: int) -> bytes:
    """Ma hoa do dai thanh prefix nen ECMA-335 (1/2/4 byte)."""
    if length < 0x80:
        return struct.pack("<B", length)
    if length < 0x4000:
        return struct.pack("<H", 0x8000 | length)
    return struct.pack("<I", 0xC0000000 | length)


def _read_us_blob(us: bytes, off: int, where: str) -> Tuple[str, int, int, int]:
    """Doc 1 blob #US tai offset -> (text, tong_byte_blob, byte_prefix, byte_cuoi).

    Blob rong du tru (offset 0) tra ("", 1, 1, 0). Blob phai co it nhat
    1 byte terminal (0x00/0x01) sau chuoi UTF-16LE; thieu -> E_CORRUPT.
    """
    if off == 0:
        return "", 1, 1, 0
    if off < 0 or off >= len(us):
        raise PB3Error("E_CORRUPT", "offset #US %d vuot qua heap %d byte"
                       % (off, len(us)), where=where)
    size, prefix = _compressed_len(us, off, where)
    start = off + prefix
    if start + size > len(us):
        raise PB3Error("E_CORRUPT", "blob #US tai %d dai %d vuot qua heap"
                       % (off, size), where=where)
    raw = us[start:start + size]
    if len(raw) < 1 or raw[-1] not in (0x00, 0x01):
        raise PB3Error("E_CORRUPT",
                       "blob #US tai %d thieu byte cuoi 0x00/0x01" % off,
                       where=where)
    try:
        text = raw[:-1].decode("utf-16-le")
    except UnicodeDecodeError as exc:
        raise PB3Error("E_CORRUPT",
                       "blob #US tai %d khong phai UTF-16LE hop le" % off,
                       where=where)
    return text, prefix + size, prefix, raw[-1]


def _read_cstr(heap: bytes, off: int, where: str) -> str:
    """Chuoi ket thuc NUL trong heap #Strings (UTF-8)."""
    if off == 0:
        return ""
    if off < 0 or off >= len(heap):
        raise PB3Error("E_CORRUPT", "offset #Strings %d vuot qua heap" % off,
                       where=where)
    end = heap.find(b"\x00", off)
    if end < 0:
        raise PB3Error("E_CORRUPT", "chuoi #Strings tai %d khong co NUL" % off,
                       where=where)
    try:
        return heap[off:end].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PB3Error("E_CORRUPT", "chuoi #Strings tai %d khong phai UTF-8"
                       % off, where=where)


class _DotNet(object):
    """Cau truc .NET da parse (noi bo): PE + CLI + metadata + bang #~."""

    def __init__(self, data: bytes, where: str = "mono_dll"):
        self.data = bytes(data)
        self.where = where
        self.pe32plus = False
        self.sections: List[Tuple[int, int, int, int]] = []
        self.cli_rva = 0
        self.cli_size = 0
        self.cli_flags = 0
        self.strong_name_signed = False
        self.metadata_version = ""
        self.metadata_off = 0
        self.metadata_size = 0
        self.streams: Dict[str, Tuple[int, int]] = {}
        self.us_off = 0
        self.us_size = 0
        self.strings_off = 0
        self.strings_size = 0
        self.methods: List[Dict[str, Any]] = []
        self._parse()

    # -- PE ------------------------------------------------------
    def rva_to_off(self, rva: int) -> int:
        for vsize, vaddr, raw_size, raw_ptr in self.sections:
            span = max(vsize, raw_size)
            if vaddr <= rva < vaddr + span:
                off = raw_ptr + (rva - vaddr)
                if off >= len(self.data):
                    raise PB3Error("E_CORRUPT", "RVA 0x%x vuot qua file" % rva,
                                   where=self.where)
                return off
        raise PB3Error("E_CORRUPT", "RVA 0x%x khong nam trong section nao" % rva,
                       where=self.where)

    def _parse_pe(self) -> None:
        data = self.data
        where = self.where
        if len(data) < 0x40 or data[:2] != _MZ:
            raise PB3Error("E_UNSUPPORTED_FORMAT",
                           "khong phai file PE (thieu MZ)", where=where)
        e_lfanew = _u32(data, 0x3C, where)
        if e_lfanew + 24 > len(data) or data[e_lfanew:e_lfanew + 4] != _PE:
            raise PB3Error("E_UNSUPPORTED_FORMAT",
                           "khong phai file PE (thieu chu ky PE)", where=where)
        coff = e_lfanew + 4
        n_sections = _u16(data, coff + 2, where)
        opt_size = _u16(data, coff + 16, where)
        opt = coff + 20
        if opt + opt_size > len(data) or opt_size < 96:
            raise PB3Error("E_CORRUPT", "optional header cat cut", where=where)
        magic = _u16(data, opt, where)
        if magic == 0x10B:
            self.pe32plus = False
            dirs_off = opt + 96
        elif magic == 0x20B:
            self.pe32plus = True
            dirs_off = opt + 112
        else:
            raise PB3Error("E_UNSUPPORTED_FORMAT",
                           "optional header magic 0x%x la (chi PE32/PE32+)"
                           % magic, where=where)
        n_dirs = _u32(data, opt + (92 if not self.pe32plus else 108), where)
        if n_dirs <= 14:
            raise PB3Error("E_UNSUPPORTED_FORMAT",
                           "PE khong co data directory CLI (khong phai .NET)",
                           where=where)
        cli_rva = _u32(data, dirs_off + 14 * 8, where)
        cli_size = _u32(data, dirs_off + 14 * 8 + 4, where)
        if not cli_rva or cli_size < 72:
            raise PB3Error("E_UNSUPPORTED_FORMAT",
                           "khong co CLI header (.NET) hop le", where=where)
        self.cli_rva = cli_rva
        self.cli_size = cli_size
        sec_off = opt + opt_size
        for i in range(n_sections):
            base = sec_off + i * 40
            if base + 40 > len(data):
                raise PB3Error("E_CORRUPT", "bang section cat cut", where=where)
            vsize = _u32(data, base + 8, where)
            vaddr = _u32(data, base + 12, where)
            raw_size = _u32(data, base + 16, where)
            raw_ptr = _u32(data, base + 20, where)
            self.sections.append((vsize, vaddr, raw_size, raw_ptr))

    # -- CLI header + metadata root -------------------------------
    def _parse_cli(self) -> None:
        off = self.rva_to_off(self.cli_rva)
        _need(self.data, off, 72, self.where)
        flags = _u32(self.data, off + 16, self.where)
        self.cli_flags = flags
        self.strong_name_signed = bool(flags & _CLI_FLAGS_STRONG_NAME)
        md_rva = _u32(self.data, off + 8, self.where)
        md_size = _u32(self.data, off + 12, self.where)
        if not md_rva or not md_size:
            raise PB3Error("E_UNSUPPORTED_FORMAT",
                           "CLI header khong tro toi metadata", where=self.where)
        self.metadata_off = self.rva_to_off(md_rva)
        self.metadata_size = md_size
        _need(self.data, self.metadata_off, md_size, self.where)

    def _parse_metadata_root(self) -> None:
        data = self.data
        off = self.metadata_off
        where = self.where
        if _u32(data, off, where) != _BSJB:
            raise PB3Error("E_UNSUPPORTED_FORMAT",
                           "metadata khong co magic BSJB", where=where)
        major = _u16(data, off + 4, where)
        minor = _u16(data, off + 6, where)
        ver_len = _u32(data, off + 12, where)
        if ver_len > 256 or off + 16 + ver_len + 4 > off + self.metadata_size:
            raise PB3Error("E_CORRUPT", "chuoi version metadata dai bat thuong",
                           where=where)
        try:
            self.metadata_version = data[off + 16:off + 16 + ver_len].split(
                b"\x00", 1)[0].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PB3Error("E_CORRUPT", "version metadata khong phai UTF-8",
                           where=where)
        pos = off + 16 + ((ver_len + 3) // 4) * 4
        n_streams = _u16(data, pos + 2, where)
        pos += 4
        streams: Dict[str, Tuple[int, int]] = {}
        for _ in range(n_streams):
            s_off = _u32(data, pos, where)
            s_size = _u32(data, pos + 4, where)
            name_start = pos + 8
            name_end = data.find(b"\x00", name_start)
            if name_end < 0 or name_end - name_start > 32:
                raise PB3Error("E_CORRUPT", "ten stream metadata khong hop le",
                               where=where)
            name = data[name_start:name_end].decode("ascii", "replace")
            streams[name] = (off + s_off, s_size)
            pos = name_end + 1
            pos = off + ((pos - off + 3) // 4) * 4
        self.streams = streams
        for need in ("#~", "#Strings", "#US"):
            if need not in streams and need == "#~" and "#-" not in streams:
                raise PB3Error("E_UNSUPPORTED_FORMAT",
                               "metadata thieu stream %s" % need, where=where)
        if "#US" not in streams:
            raise PB3Error("E_UNSUPPORTED_FORMAT",
                           "metadata thieu stream #US", where=where)
        self.us_off, self.us_size = streams["#US"]
        if "#Strings" in streams:
            self.strings_off, self.strings_size = streams["#Strings"]

    # -- Bang #~ ---------------------------------------------------
    def _parse_tables(self) -> None:
        data = self.data
        where = self.where
        t_off, _t_size = self.streams.get("#~", self.streams.get("#-", (0, 0)))
        if not t_off:
            raise PB3Error("E_UNSUPPORTED_FORMAT",
                           "metadata thieu stream bang #~", where=where)
        if t_off + 24 > len(data):
            raise PB3Error("E_CORRUPT", "header bang #~ cat cut", where=where)
        heap_sizes = data[t_off + 6]
        valid = struct.unpack_from("<Q", data, t_off + 8)[0]
        pos = t_off + 24
        row_counts: Dict[int, int] = {}
        for tid in range(64):
            if valid & (1 << tid):
                row_counts[tid] = _u32(data, pos, where)
                pos += 4
        str_idx = 4 if heap_sizes & 0x01 else 2
        guid_idx = 4 if heap_sizes & 0x02 else 2
        blob_idx = 4 if heap_sizes & 0x04 else 2

        def simple(tid: int) -> int:
            return 4 if row_counts.get(tid, 0) > 0xFFFF else 2

        def coded(tables: Tuple[int, ...], tag_bits: int) -> int:
            top = max([row_counts.get(t, 0) for t in tables] + [0])
            return 4 if top >= (1 << (16 - tag_bits)) else 2

        # TypeDefOrRef: tag 2 bit (TypeDef/TypeRef/TypeSpec).
        tdor = coded((_TYPEDEF, _TYPEREF, 27), 2)
        # ResolutionScope: tag 2 bit (Module/ModuleRef/AssemblyRef/TypeRef).
        rs = coded((_MODULE, 26, 35, _TYPEREF), 2)

        def row_size(tid: int) -> int:
            if tid == _MODULE:
                return 2 + str_idx + guid_idx * 3
            if tid == _TYPEREF:
                return rs + str_idx * 2
            if tid == _TYPEDEF:
                return 4 + str_idx * 2 + tdor + simple(_FIELD) + simple(_METHODDEF)
            if tid == _FIELD:
                return 2 + str_idx + blob_idx
            if tid == _METHODDEF:
                return 4 + 2 + 2 + str_idx + blob_idx + simple(_PARAM)
            raise PB3Error("E_UNSUPPORTED_FORMAT",
                           "bang #%d truoc MethodDef chua ho tro" % tid,
                           where=where)

        # Chi can di toi MethodDef (tid 6): bo qua Module/TypeRef/TypeDef/Field.
        for tid in (_MODULE, _TYPEREF, _TYPEDEF, _FIELD):
            pos += row_size(tid) * row_counts.get(tid, 0)
        strings_heap = data[self.strings_off:self.strings_off + self.strings_size]
        methods: List[Dict[str, Any]] = []
        for i in range(row_counts.get(_METHODDEF, 0)):
            base = pos + i * row_size(_METHODDEF)
            if base + row_size(_METHODDEF) > len(data):
                raise PB3Error("E_CORRUPT", "bang MethodDef vuot qua file",
                               where=where)
            rva = _u32(data, base, where)
            name_off = int.from_bytes(
                data[base + 8:base + 8 + str_idx], "little")
            name = _read_cstr(strings_heap, name_off, where) if self.strings_size else ""
            methods.append({"index": i + 1, "rva": rva, "name": name,
                            "token": 0x06000000 | (i + 1)})
        self.methods = methods

    def _parse(self) -> None:
        self._parse_pe()
        self._parse_cli()
        self._parse_metadata_root()
        self._parse_tables()

    # -- Than method + ldstr --------------------------------------
    def method_code(self, rva: int) -> Tuple[int, bytes]:
        """Than method tai RVA -> (file_offset_cua_code, code_bytes)."""
        if not rva:
            return 0, b""
        off = self.rva_to_off(rva)
        data = self.data
        where = self.where
        if off >= len(data):
            raise PB3Error("E_CORRUPT", "RVA than method 0x%x vuot qua file"
                           % rva, where=where)
        b0 = data[off]
        kind = b0 & 0x03
        if kind == 0x02:  # Tiny: size = b0 >> 2, code ngay sau 1 byte header.
            size = b0 >> 2
            start = off + 1
        elif kind == 0x03:  # Fat: flags u16, header dwords o bits 12-15.
            flags = _u16(data, off, where)
            if flags & 0x07 != 0x03:
                raise PB3Error("E_CORRUPT",
                               "than method Fat co flags 0x%x la" % flags,
                               where=where)
            header_dwords = (flags >> 12) & 0x0F
            if header_dwords < 3:
                raise PB3Error("E_CORRUPT", "than method Fat ngan bat thuong",
                               where=where)
            size = _u32(data, off + 4, where)
            start = off + header_dwords * 4
        else:
            raise PB3Error("E_UNSUPPORTED_FORMAT",
                           "than method header 0x%02x la (khong Tiny/Fat)"
                           % b0, where=where)
        if start + size > len(data):
            raise PB3Error("E_CORRUPT", "than method vuot qua file", where=where)
        return start, data[start:start + size]


def _scan_ldstr(dn: _DotNet) -> List[Dict[str, Any]]:
    """Quet ldstr trong moi than method -> 1 muc moi site (token + offset IL)."""
    us = dn.data[dn.us_off:dn.us_off + dn.us_size]
    sites: List[Dict[str, Any]] = []
    for m in dn.methods:
        if not m["rva"]:
            continue
        try:
            code_off, code = dn.method_code(m["rva"])
        except PB3Error:
            continue  # than method doc loi: bo qua method do, khong chet ca DLL
        i = 0
        while i + 5 <= len(code):
            if code[i] == 0x72:  # ldstr
                token = struct.unpack_from("<I", code, i + 1)[0]
                if token & 0xFF000000 == _US_TOKEN_MASK:
                    us_off = token & 0x00FFFFFF
                    try:
                        text, blob_size, _pre, _term = _read_us_blob(
                            us, us_off, dn.where)
                    except PB3Error:
                        i += 5
                        continue  # token tro toi blob hong: bo qua site do
                    sites.append({
                        "token": token, "us_offset": us_off, "text": text,
                        "method_rva": m["rva"], "method_name": m["name"],
                        "method_token": m["token"],
                        "il_offset": i, "code_file_offset": code_off + i,
                        "blob_size": blob_size,
                        "byte_length": len(text.encode("utf-16-le")),
                    })
                i += 5
            else:
                i += 1
    return sites


def read_dll_bytes(path, entry=None):
    """Doc bytes Assembly-CSharp.dll: file thuong (entry=None) hoac
    member trong ZIP/IPA (entry=duong dan member).

    Tra (data, source_label). Member/file > MAX_MEMBER_BYTES thi tu
    choi truoc khi doc (khong nap ca tram MB). Entry khong co trong
    ZIP thi E_INPUT_NOT_FOUND (khong de KeyError lot ra ngoai).
    """
    if entry:
        import zipfile
        try:
            with zipfile.ZipFile(path, "r") as zf:
                try:
                    info = zf.getinfo(entry)
                except KeyError:
                    raise PB3Error(
                        "E_INPUT_NOT_FOUND",
                        "không tìm thấy entry %r trong file ZIP/IPA"
                        % entry, where=str(entry))
                if info.file_size > MAX_MEMBER_BYTES:
                    raise PB3Error(
                        "E_TOO_LARGE",
                        "Assembly-CSharp.dll %d byte > giới hạn %d byte "
                        "cho đọc chuỗi Mono"
                        % (info.file_size, MAX_MEMBER_BYTES), where=entry)
                return zf.read(entry), "%s::%s" % (path, entry)
        except PB3Error:
            raise
        except (zipfile.BadZipFile, OSError) as exc:
            raise PB3Error("E_UNSUPPORTED_FORMAT",
                           "không mở được file ZIP/IPA (%s)"
                           % type(exc).__name__, where=str(path))
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        raise PB3Error("E_INPUT_NOT_FOUND",
                       "không đọc được file %r (%s)" % (path, exc),
                       where=str(path))
    if size > MAX_MEMBER_BYTES:
        raise PB3Error("E_TOO_LARGE",
                       "file %d byte > giới hạn %d byte cho đọc chuỗi Mono"
                       % (size, MAX_MEMBER_BYTES), where=str(path))
    with open(path, "rb") as fh:
        return fh.read(), str(path)


def extract_mono_strings(data: bytes, where: str = "mono_dll") -> Dict[str, Any]:
    """Trich moi site ldstr trong DLL .NET -> {info, strings}.

    strings: 1 muc moi SITE ldstr (cung chuoi dung o 2 method = 2 muc,
    cung us_offset — de va theo site). info: tom tat DLL (version
    metadata, so method, so site, so chuoi duy nhat, strong-name?).
    DLL khong phai .NET / metadata hong -> PB3Error ro rang.
    """
    dn = _DotNet(data, where)
    sites = _scan_ldstr(dn)
    unique = len({s["us_offset"] for s in sites})
    return {
        "info": {
            "is_dotnet": True,
            "pe32plus": dn.pe32plus,
            "metadata_version": dn.metadata_version,
            "methods": len(dn.methods),
            "ldstr_sites": len(sites),
            "unique_strings": unique,
            "strong_name_signed": dn.strong_name_signed,
            "us_bytes": dn.us_size,
            "cli_flags": dn.cli_flags,
        },
        "strings": sites,
    }


def mono_dll_info(data: bytes, where: str = "mono_dll") -> Dict[str, Any]:
    """Tom tat nhanh DLL (khong trich het chuoi): dung cho bao cao/canh bao."""
    try:
        dn = _DotNet(data, where)
    except PB3Error as exc:
        return {"is_dotnet": False, "error": exc.code,
                "detail": exc.message_vi}
    sites = _scan_ldstr(dn)
    return {
        "is_dotnet": True,
        "pe32plus": dn.pe32plus,
        "metadata_version": dn.metadata_version,
        "methods": len(dn.methods),
        "ldstr_sites": len(sites),
        "unique_strings": len({s["us_offset"] for s in sites}),
        "strong_name_signed": dn.strong_name_signed,
        "us_bytes": dn.us_size,
    }


def strings_to_table(result: Dict[str, Any]) -> Tuple[Dict[str, str], Dict[str, Any]]:
    """Ket qua extract_mono_strings -> (bang dich, manifest).

    Bang: {MONO_%06d: text} — key theo us_offset (on dinh, khong phu
    thuoc thu tu quet). Chuoi dung o nhieu site chi xuat 1 lan (site
    dau tien gap); manifest giu moi site de duong va cap nhat het token.
    Chuoi rong (blob du tru offset 0) khong dua vao bang.
    """
    table: Dict[str, str] = {}
    manifest: Dict[str, Any] = {}
    for s in result.get("strings") or []:
        key = "MONO_%06d" % s["us_offset"]
        if key in table:
            manifest[key]["sites"].append({
                "token": s["token"], "method_rva": s["method_rva"],
                "method_name": s["method_name"],
                "il_offset": s["il_offset"],
                "code_file_offset": s["code_file_offset"]})
            continue
        table[key] = s["text"]
        manifest[key] = {
            "us_offset": s["us_offset"], "token": s["token"],
            "source": s["text"], "byte_length": s["byte_length"],
            "blob_size": s["blob_size"],
            "sites": [{
                "token": s["token"], "method_rva": s["method_rva"],
                "method_name": s["method_name"],
                "il_offset": s["il_offset"],
                "code_file_offset": s["code_file_offset"]}],
        }
    table.pop("MONO_000000", None)
    manifest.pop("MONO_000000", None)
    return table, manifest


def _make_blob(text: str, terminal: int) -> bytes:
    """Blob #US moi: prefix do dai nen + UTF-16LE + byte cuoi goc."""
    body = text.encode("utf-16-le") + bytes([terminal])
    return _compressed_len_bytes(len(body)) + body


def patch_mono_strings(data: bytes, patches: List[Dict[str, Any]],
                       where: str = "mono_dll") -> Tuple[bytes, Dict[str, Any]]:
    """Va chuoi trong DLL .NET, tra (bytes_moi, bao_cao).

    patches: [{us_offset, before, after}] — us_offset la offset trong
    heap #US (lay tu bang trich xuat / manifest). before phai khop
    chuoi hien tai (khong khop -> bo qua key do, ghi ro ma
    E_CONFIG_INVALID; khong va mu). after == before -> khong lam gi.
    Moi patch thanh cong: {us_offset, before, after, mode} voi mode
    "inplace" (va tai cho) hoac "relocated" (doi blob toi slot trong).
    Patch that bai do dai: {us_offset, code: "E_TOO_LONG", reason}.
    Loi cau truc (khong phai .NET, metadata hong, strong-name) -> raise
    PB3Error ngay, khong tra bytes nua voi. Kich thuoc file khong doi.
    """
    dn = _DotNet(data, where)
    if dn.strong_name_signed:
        raise PB3Error("E_STRONG_NAME",
                       "DLL da ky strong-name (CLI Flags 0x%x); va byte se "
                       "pha chu ky" % dn.cli_flags, where=where)
    us = dn.data[dn.us_off:dn.us_off + dn.us_size]
    out = bytearray(dn.data)
    sites = _scan_ldstr(dn)
    # token operand nao tro toi us_offset nao (de cap nhat khi doi blob).
    token_sites: Dict[int, List[int]] = {}
    for s in sites:
        token_sites.setdefault(s["us_offset"], []).append(s["code_file_offset"] + 1)
    free: List[Tuple[int, int]] = []  # (offset_trong_heap, kich_thuoc) slot trong
    patched: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    done_offsets = set()
    for p in patches or []:
        try:
            us_off = int(p.get("us_offset"))
        except (TypeError, ValueError):
            skipped.append({"us_offset": p.get("us_offset"),
                            "code": "E_CONFIG_INVALID",
                            "reason": "us_offset khong phai so nguyen"})
            continue
        before = p.get("before")
        after = p.get("after")
        if not isinstance(before, str) or not isinstance(after, str):
            skipped.append({"us_offset": us_off, "code": "E_CONFIG_INVALID",
                            "reason": "before/after phai la chuoi"})
            continue
        if us_off in done_offsets:
            skipped.append({"us_offset": us_off, "code": "E_CONFIG_INVALID",
                            "reason": "us_offset lap lai trong cung lan va"})
            continue
        if us_off == 0:
            skipped.append({"us_offset": us_off, "code": "E_CONFIG_INVALID",
                            "reason": "offset 0 la blob rong du tru, khong va duoc"})
            continue
        try:
            cur, blob_size, _pre, term = _read_us_blob(us, us_off, where)
        except PB3Error as exc:
            skipped.append({"us_offset": us_off, "code": exc.code,
                            "reason": "khong doc duoc blob goc: %s" % exc.message_vi})
            continue
        if before != cur:
            skipped.append({"us_offset": us_off, "code": "E_CONFIG_INVALID",
                            "reason": "chuoi hien tai khac before (file da doi "
                            "so voi luc trich xuat)"})
            continue
        if after == cur:
            done_offsets.add(us_off)
            patched.append({"us_offset": us_off, "before": before,
                            "after": after, "mode": "noop"})
            continue
        if "\x00" in after:
            skipped.append({"us_offset": us_off, "code": "E_CONFIG_INVALID",
                            "reason": "ban dich chua ky tu NUL"})
            continue
        new_blob = _make_blob(after, term)
        if len(new_blob) <= blob_size:
            # Va tai cho: giu nguyen token, dem NUL phan thua cua blob cu.
            start = dn.us_off + us_off
            out[start:start + len(new_blob)] = new_blob
            for i in range(start + len(new_blob), start + blob_size):
                out[i] = 0
            free.append((us_off + len(new_blob), blob_size - len(new_blob)))
            done_offsets.add(us_off)
            patched.append({"us_offset": us_off, "before": before,
                            "after": after, "mode": "inplace"})
            continue
        # Dai hon: tim slot trong du lon (slot cua chinh no khong du).
        free.sort(key=lambda t: t[1])
        slot = None
        for j, (foff, fsize) in enumerate(free):
            if fsize >= len(new_blob):
                slot = (foff, fsize, j)
                break
        if slot is None:
            skipped.append({
                "us_offset": us_off, "code": "E_TOO_LONG",
                "reason": "ban dich %d byte > goc %d byte, heap #US khong "
                "con slot trong du lon (can %d byte blob)"
                % (len(after.encode("utf-16-le")), len(cur.encode("utf-16-le")),
                   len(new_blob))})
            continue
        foff, fsize, j = slot
        del free[j]
        start = dn.us_off + foff
        out[start:start + len(new_blob)] = new_blob
        if fsize > len(new_blob):
            free.append((foff + len(new_blob), fsize - len(new_blob)))
        # Giai phong span cu (tru truong hop slot nam trong chinh span cu —
        # khong xay ra vi slot chi lay tu free cua patch truoc).
        free.append((us_off, blob_size))
        # Cap nhat MOI toan tu ldstr tro toi offset cu.
        new_token = _US_TOKEN_MASK | foff
        for top in token_sites.get(us_off, []):
            out[top:top + 4] = struct.pack("<I", new_token)
        token_sites[foff] = token_sites.pop(us_off, [])
        done_offsets.add(us_off)
        patched.append({"us_offset": us_off, "before": before,
                        "after": after, "mode": "relocated",
                        "new_us_offset": foff})
    # Verify: doc lai tu bytes da va; kich thuoc file khong doi.
    if len(out) != len(dn.data):
        raise PB3Error("E_VERIFY_FAILED",
                       "kich thuoc file doi sau khi va (%d -> %d)"
                       % (len(dn.data), len(out)), where=where)
    try:
        after_res = extract_mono_strings(bytes(out), where)
    except PB3Error as exc:
        raise PB3Error("E_VERIFY_FAILED",
                       "doc lai DLL da va loi [%s]" % exc.code, where=where)
    after_by_off = {}
    for s in after_res["strings"]:
        after_by_off.setdefault(s["us_offset"], s["text"])
    for rec in patched:
        if rec["mode"] == "noop":
            continue
        check_off = rec.get("new_us_offset", rec["us_offset"])
        if after_by_off.get(check_off) != rec["after"]:
            raise PB3Error("E_VERIFY_FAILED",
                           "verify that bai o us_offset %d" % rec["us_offset"],
                           where=where)
    if len(after_res["strings"]) != len(sites):
        raise PB3Error("E_VERIFY_FAILED",
                       "so site ldstr doi sau khi va (%d -> %d)"
                       % (len(sites), len(after_res["strings"])), where=where)
    report: Dict[str, Any] = {
        "patched": patched,
        "skipped": skipped,
        "patched_count": len([r for r in patched if r["mode"] != "noop"]),
        "relocated_count": len([r for r in patched if r["mode"] == "relocated"]),
        "skipped_count": len(skipped),
        "file_size_unchanged": True,
    }
    return bytes(out), report
