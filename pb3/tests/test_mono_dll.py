"""Test F4: trich + va chuoi literal .NET trong Assembly-CSharp.dll (Mono).

Fixture: DLL .NET toi gian dung tay theo ECMA-335 (PE32, 1 section
.text, CLI header, metadata BSJB, 2 method — 1 Tiny + 1 Fat — moi
method 1 ldstr). Khong co game Mono that trong test-ipas (tat ca deu
IL2CPP) nen day la [FIXTURE]; game Mono that = "Cho nguoi dung".

Viet theo kieu unittest.TestCase cua repo (khong dung pytest function /
fixture — loader unittest cua run_tests chi nap TestCase; test_runner R8
doi plan_counts khop so nap that, xem CI do 01:00 UTC 22/11).
"""
from __future__ import annotations

import os
import struct
import tempfile
import unittest

from core.errors import PB3Error
from sdk import mono_dll


# ------------------------------------------------------- builder fixture DLL
def _align(n, a):
    return (n + a - 1) // a * a


def _cstr_blob(text):
    raw = text.encode("utf-16-le") + b"\x00"
    return bytes([len(raw)]) + raw


def _build_metadata(streams):
    """Metadata root BSJB tu [(ten, body)] -> bytes (offset stream tinh
    tu dau root, ten stream dem 4 byte)."""
    version = b"v4.0.30319\x00"
    root = struct.pack("<I", 0x424A5342) + struct.pack("<HH", 4, 0)
    root += struct.pack("<I", 0) + struct.pack("<I", len(version)) + version
    root += b"\x00" * ((-len(version)) % 4)
    root += struct.pack("<HH", 0, len(streams))
    header_len = len(root)
    names_len = 0
    for name, _b in streams:
        names_len += 8 + _align(len(name) + 1, 4)
    data_off = header_len + names_len
    for name, body in streams:
        root += struct.pack("<II", data_off, len(body))
        nb = name.encode("ascii") + b"\x00"
        root += nb + b"\x00" * ((-len(nb)) % 4)
        data_off += len(body)
    for _name, body in streams:
        root += body
    return root


def build_fixture_dll(strong_name=False):
    """DLL .NET toi gian: Program.Main ldstr "Hello world" (Tiny),
    Program.Greet ldstr "Second string is quite long indeed" (Fat)."""
    # --- #Strings: 0=NUL, 1=TestAssembly, 14=Program, 22=Main, 27=Greet
    strings = b"\x00" + b"TestAssembly\x00" + b"Program\x00" + b"Main\x00" + b"Greet\x00"
    # --- #US: 0=NUL, 1=blob "Hello world", 25=blob "Second string is quite long indeed"
    us = b"\x00" + _cstr_blob("Hello world") + _cstr_blob(
        "Second string is quite long indeed")
    assert us[1] == 23 and len(us) == 95, (us[1], len(us))
    # --- #Blob: 0=NUL, 1=sig DEFAULT/0 param/VOID (3 byte)
    blob = b"\x00" + bytes([3]) + b"\x00\x00\x01"
    # --- #GUID: Mvid 16 byte 0
    guid = b"\x00" * 16
    # --- #~ : Module(1) + TypeDef(1) + MethodDef(2); HeapSizes=0 (idx 2 byte)
    tables = bytearray()
    tables += struct.pack("<I", 0) + bytes([2, 0, 0, 0])
    tables += struct.pack("<Q", (1 << 0) | (1 << 2) | (1 << 6))
    tables += struct.pack("<Q", 0)
    tables += struct.pack("<III", 1, 1, 2)
    # Module: Generation u16, Name stridx=1, Mvid guididx=1, EncId=0, EncBaseId=0
    tables += struct.pack("<H", 0) + struct.pack("<H", 1)
    tables += struct.pack("<HHH", 1, 0, 0)
    # TypeDef: Flags u32, Name=14, Namespace=0, Extends=0, FieldList=1, MethodList=1
    tables += struct.pack("<I", 1) + struct.pack("<HH", 14, 0)
    tables += struct.pack("<H", 0) + struct.pack("<HH", 1, 1)
    # MethodDef: RVA u32, ImplFlags u16, Flags u16, Name u16, Sig u16, ParamList u16
    method_rows_off = len(tables)
    tables += b"\x00" * (14 * 2)
    streams = [("#~", tables), ("#Strings", strings), ("#US", us),
               ("#GUID", guid), ("#Blob", blob)]
    metadata = _build_metadata(streams)
    # --- Than method (dat sau metadata trong .text)
    cli_size = 72
    code_base = cli_size + len(metadata)
    main_code = b"\x72" + struct.pack("<I", 0x70000001) + b"\x26\x2a"
    main_body = bytes([(len(main_code) << 2) | 0x02]) + main_code
    greet_code = b"\x72" + struct.pack("<I", 0x70000019) + b"\x26\x2a"
    greet_body = struct.pack("<HHI", 0x3003, 8, len(greet_code)) \
        + struct.pack("<I", 0) + greet_code
    main_rva = 0x2000 + code_base
    greet_rva = 0x2000 + code_base + len(main_body)
    struct.pack_into("<IHHHHH", tables, method_rows_off,
                     main_rva, 0, 0x16, 22, 1, 1)
    struct.pack_into("<IHHHHH", tables, method_rows_off + 14,
                     greet_rva, 0, 0x16, 27, 1, 1)
    metadata = _build_metadata(streams)
    # --- CLI header
    flags = 0x1 | (0x8 if strong_name else 0x0)
    cli = struct.pack("<I", 72) + struct.pack("<HH", 2, 5)
    cli += struct.pack("<II", 0x2000 + cli_size, len(metadata))
    cli += struct.pack("<I", flags) + struct.pack("<I", 0x06000001)
    cli += b"\x00" * (72 - len(cli))
    text = cli + metadata + main_body + greet_body
    # --- PE headers
    raw_size = _align(len(text), 0x200)
    dos = bytearray(0x80)
    dos[0:2] = b"MZ"
    struct.pack_into("<I", dos, 0x3C, 0x80)
    pe = b"PE\x00\x00" + struct.pack("<HHIIIHH", 0x14C, 1, 0, 0, 0, 224, 0x0102)
    opt = bytearray(224)
    struct.pack_into("<H", opt, 0, 0x10B)
    struct.pack_into("<I", opt, 4, raw_size)
    struct.pack_into("<I", opt, 16, 0x2000)
    struct.pack_into("<I", opt, 20, 0)
    struct.pack_into("<I", opt, 24, 0x400000)
    struct.pack_into("<I", opt, 28, 0x2000)
    struct.pack_into("<I", opt, 32, 0x200)
    struct.pack_into("<I", opt, 56, 0x4000)
    struct.pack_into("<I", opt, 60, 0x200)
    struct.pack_into("<H", opt, 68, 3)
    struct.pack_into("<I", opt, 72, 0x100000)
    struct.pack_into("<I", opt, 76, 0x1000)
    struct.pack_into("<I", opt, 80, 0x100000)
    struct.pack_into("<I", opt, 84, 0x1000)
    struct.pack_into("<I", opt, 92, 16)
    struct.pack_into("<II", opt, 96 + 14 * 8, 0x2000, 72)
    sec = b".text\x00\x00\x00" + struct.pack(
        "<IIII", len(text), 0x2000, raw_size, 0x200) + b"\x00" * 12
    sec += struct.pack("<I", 0x60000020)
    headers = bytes(dos) + pe + bytes(opt) + sec
    headers += b"\x00" * (0x200 - len(headers))
    return headers + text + b"\x00" * (raw_size - len(text))


class TestMonoDll(unittest.TestCase):
    def setUp(self):
        self.dll = build_fixture_dll()

    def test_extract_two_strings(self):
        res = mono_dll.extract_mono_strings(self.dll)
        self.assertIs(res["info"]["is_dotnet"], True)
        self.assertEqual(res["info"]["methods"], 2)
        self.assertEqual(res["info"]["ldstr_sites"], 2)
        self.assertEqual(res["info"]["unique_strings"], 2)
        self.assertIs(res["info"]["strong_name_signed"], False)
        by_off = {s["us_offset"]: s for s in res["strings"]}
        self.assertEqual(by_off[1]["text"], "Hello world")
        self.assertEqual(by_off[1]["method_name"], "Main")
        self.assertEqual(by_off[25]["text"], "Second string is quite long indeed")
        self.assertEqual(by_off[25]["method_name"], "Greet")

    def test_strings_to_table(self):
        res = mono_dll.extract_mono_strings(self.dll)
        table, manifest = mono_dll.strings_to_table(res)
        self.assertEqual(table, {"MONO_000001": "Hello world",
                                 "MONO_000025": "Second string is quite long indeed"})
        self.assertEqual(manifest["MONO_000001"]["source"], "Hello world")
        self.assertEqual(len(manifest["MONO_000001"]["sites"]), 1)
        self.assertEqual(manifest["MONO_000025"]["byte_length"], 68)

    def test_info_summary(self):
        info = mono_dll.mono_dll_info(self.dll)
        self.assertIs(info["is_dotnet"], True)
        self.assertEqual(info["ldstr_sites"], 2)
        self.assertEqual(info["metadata_version"], "v4.0.30319")

    def test_patch_shorter_inplace(self):
        out, rep = mono_dll.patch_mono_strings(self.dll, [
            {"us_offset": 1, "before": "Hello world", "after": "Chao"}])
        self.assertEqual(len(out), len(self.dll))
        self.assertEqual(rep["patched_count"], 1)
        self.assertEqual(rep["patched"][0]["mode"], "inplace")
        self.assertEqual(rep["skipped_count"], 0)
        res = mono_dll.extract_mono_strings(out)
        by_off = {s["us_offset"]: s for s in res["strings"]}
        self.assertEqual(by_off[1]["text"], "Chao")
        self.assertEqual(by_off[25]["text"], "Second string is quite long indeed")
        self.assertEqual(len(res["strings"]), 2)

    def test_patch_longer_relocated_via_freed_slot(self):
        # Rut ngan chuoi dai truoc (giai phong slot 64 byte), roi doi chuoi
        # ngan sang ban dich dai hon (can 36 byte blob) -> relocated.
        out, rep = mono_dll.patch_mono_strings(self.dll, [
            {"us_offset": 25, "before": "Second string is quite long indeed",
             "after": "Hi"},
            {"us_offset": 1, "before": "Hello world",
             "after": "Xin chao cac ban!"}])
        self.assertEqual(len(out), len(self.dll))
        modes = {r["us_offset"]: r["mode"] for r in rep["patched"]}
        self.assertEqual(modes[25], "inplace")
        self.assertEqual(modes[1], "relocated")
        self.assertEqual(rep["relocated_count"], 1)
        res = mono_dll.extract_mono_strings(out)
        texts = sorted(s["text"] for s in res["strings"])
        self.assertEqual(texts, ["Hi", "Xin chao cac ban!"])
        self.assertEqual(len(res["strings"]), 2)
        # Token ldstr cua Main da tro sang offset moi, khong con tro offset 1.
        self.assertTrue(all(s["us_offset"] != 1 for s in res["strings"]))

    def test_patch_too_long_fail_closed(self):
        long_vi = "Xin chao " * 12  # 96 ky tu, khong slot trong nao du
        out, rep = mono_dll.patch_mono_strings(self.dll, [
            {"us_offset": 1, "before": "Hello world", "after": long_vi}])
        self.assertEqual(out, self.dll)  # khong doi 1 byte nao
        self.assertEqual(rep["patched_count"], 0)
        self.assertEqual(rep["skipped_count"], 1)
        self.assertEqual(rep["skipped"][0]["code"], "E_TOO_LONG")

    def test_patch_before_mismatch_skipped(self):
        out, rep = mono_dll.patch_mono_strings(self.dll, [
            {"us_offset": 1, "before": "Hello WORLD", "after": "Chao"}])
        self.assertEqual(out, self.dll)
        self.assertEqual(rep["skipped"][0]["code"], "E_CONFIG_INVALID")

    def test_patch_reserved_offset_zero_rejected(self):
        out, rep = mono_dll.patch_mono_strings(self.dll, [
            {"us_offset": 0, "before": "", "after": "X"}])
        self.assertEqual(out, self.dll)
        self.assertEqual(rep["skipped"][0]["code"], "E_CONFIG_INVALID")

    def test_strong_name_refused(self):
        dll = build_fixture_dll(strong_name=True)
        info = mono_dll.mono_dll_info(dll)
        self.assertIs(info["strong_name_signed"], True)
        with self.assertRaises(PB3Error) as cm:
            mono_dll.patch_mono_strings(dll, [
                {"us_offset": 1, "before": "Hello world", "after": "Chao"}])
        self.assertEqual(cm.exception.code, "E_STRONG_NAME")

    def test_not_dotnet(self):
        with self.assertRaises(PB3Error) as cm:
            mono_dll.extract_mono_strings(b"PK\x03\x04" + b"\x00" * 200)
        self.assertEqual(cm.exception.code, "E_UNSUPPORTED_FORMAT")

    def test_truncated_dll(self):
        dll = build_fixture_dll()
        with self.assertRaises(PB3Error):
            mono_dll.extract_mono_strings(dll[:100])

    def test_corrupt_us_terminal_byte_skips_site(self):
        bad = bytearray(self.dll)
        # Blob "Hello world" o heap #US: tim offset file cua heap qua parser.
        from sdk.mono_dll import _DotNet
        dn = _DotNet(bytes(self.dll))
        # byte cuoi cua blob 1 = us_off + 1 + 23 (prefix 1 + body 23) - 1
        bad[dn.us_off + 1 + 23] = 0x07
        res = mono_dll.extract_mono_strings(bytes(bad))
        self.assertEqual(res["info"]["ldstr_sites"], 1)
        self.assertEqual(res["strings"][0]["text"], "Second string is quite long indeed")

    def test_info_not_dotnet(self):
        info = mono_dll.mono_dll_info(b"not a dll at all")
        self.assertIs(info["is_dotnet"], False)
        self.assertEqual(info["error"], "E_UNSUPPORTED_FORMAT")


# ------------------------------------------------------- bridge ops
class TestMonoDllBridge(unittest.TestCase):
    def test_bridge_table_and_patch(self):
        import json as _json
        import bridge as _bridge
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "Assembly-CSharp.dll")
            with open(fp, "wb") as fh:
                fh.write(build_fixture_dll())
            out = _json.loads(_bridge.mono_dll_table(fp))
            self.assertIs(out["ok"], True)
            self.assertEqual(out["table_keys"], 2)
            self.assertEqual(out["ldstr_sites"], 2)
            self.assertIs(out["strong_name_signed"], False)
            table = _json.load(open("/tmp/pb3out/mono-strings.json",
                                    encoding="utf-8"))
            self.assertEqual(table["MONO_000001"], "Hello world")
            manifest = _json.load(open("/tmp/pb3out/mono-strings.manifest.json",
                                       encoding="utf-8"))
            self.assertEqual(manifest["keys"]["MONO_000001"]["source"], "Hello world")
            table2 = dict(table)
            table2["MONO_000001"] = "Chao"
            out2 = _json.loads(_bridge.patch_mono_dll(
                fp, None, _json.dumps(table2, ensure_ascii=False),
                _json.dumps(manifest, ensure_ascii=False)))
            self.assertIs(out2["ok"], True)
            self.assertEqual(out2["patched_count"], 1)
            self.assertEqual(out2["skipped_count"], 0)
            patched = open("/tmp/pb3out/Assembly-CSharp.patched.dll", "rb").read()
            self.assertEqual(len(patched), len(build_fixture_dll()))
            res = mono_dll.extract_mono_strings(patched)
            by_off = {s["us_offset"]: s["text"] for s in res["strings"]}
            self.assertEqual(by_off[1], "Chao")
            # File goc khong doi.
            self.assertEqual(open(fp, "rb").read(), build_fixture_dll())

    def test_bridge_table_strong_name_note(self):
        import json as _json
        import bridge as _bridge
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "Assembly-CSharp.dll")
            with open(fp, "wb") as fh:
                fh.write(build_fixture_dll(strong_name=True))
            out = _json.loads(_bridge.mono_dll_table(fp))
            self.assertIs(out["ok"], True)
            self.assertIs(out["strong_name_signed"], True)
            self.assertIn("CHỈ trích được", out["note"])
            table = _json.load(open("/tmp/pb3out/mono-strings.json",
                                    encoding="utf-8"))
            out2 = _json.loads(_bridge.patch_mono_dll(
                fp, None, _json.dumps(table, ensure_ascii=False)))
            self.assertIs(out2["ok"], False)
