# -*- coding: utf-8 -*-
"""Test "xem sâu" (inspect_deep) + "trích sâu" (raw scan / IL2CPP literals).

Fixture TỰ DỰNG (bytes/metadata tổng hợp, bundle từ builder có sẵn);
không dùng mạng. Test IPA thật ở cuối: skip nếu thiếu file
(env TOM_IPA / NINEKINGS_IPA hoặc ~/workspace/test-ipas/*.ipa).
"""

import io
import json
import os
import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests" / "fixtures" / "unity"))
from build_fixtures import (  # noqa: E402
    build_bundle, build_serialized_file, text_asset_data)

import bridge  # noqa: E402

bridge.WEB_ROOT = str(ROOT)
json.loads(bridge.boot())

from core.errors import PB3Error  # noqa: E402
from sdk import il2cpp as il2cpp_sdk  # noqa: E402
from sdk import rawscan  # noqa: E402
from tests.tiering import slow  # noqa: E402

TOM_IPA = Path(os.environ.get(
    "TOM_IPA", Path.home() / "workspace" / "test-ipas" /
    "My_Talking_Tom_v26.5.1_Decrypted.ipa"))
NINEKINGS_IPA = Path(os.environ.get(
    "NINEKINGS_IPA", Path.home() / "workspace" / "test-ipas" /
    "9kings-v1.0.6.ipa"))
TOM_META = ("Payload/MyTalkingTom-row.app/Frameworks/UnityFramework."
            "framework/Data/Managed/Metadata/global-metadata.dat")
TOM_BUNDLE = ("Payload/MyTalkingTom-row.app/Frameworks/UnityFramework."
              "framework/Data/data.unity3d")
NINEKINGS_META = ("Payload/9Kings.app/Data/Managed/Metadata/"
                  "global-metadata.dat")


# ------------------------------------------------------- builder metadata
def _build_meta_pairs(version, literals, header_size=256):
    """global-metadata.dat v24/v27/v29/v31 tổng hợp: header + bảng (len, idx).

    v24–v31 cùng layout pairs (Il2CppDumper + rea: header 4 field, không
    có số entry); v24 dùng header 264 byte theo rea, các version sau 256.
    """
    blob = b"".join(s.encode("utf-8") for s in literals)
    table = bytearray()
    off = 0
    for s in literals:
        raw = s.encode("utf-8")
        table += struct.pack("<II", len(raw), off)
        off += len(raw)
    header = struct.pack("<Ii", 0xFAB11BAF, version)
    header += struct.pack("<iiii", header_size, len(table),
                          header_size + len(table), len(blob))
    header += b"\x00" * (header_size - len(header))
    return header + bytes(table) + blob


def _build_meta_v31(literals):
    """global-metadata.dat v31 tổng hợp: header 256 + bảng (len, idx)."""
    return _build_meta_pairs(31, literals, 256)


def _build_meta_v39(literals):
    """global-metadata.dat v39 tổng hợp: header 380 + bảng delta idx."""
    blob = b"".join(s.encode("utf-8") for s in literals)
    idxs = []
    off = 0
    for s in literals:
        idxs.append(off)
        off += len(s.encode("utf-8"))
    table = struct.pack("<%dI" % len(idxs), *idxs) if idxs else b""
    header = struct.pack("<Ii", 0xFAB11BAF, 39)
    header += struct.pack("<iiiii", 380, len(table), len(idxs),
                          380 + len(table), len(blob))
    header += b"\x00" * (380 - len(header))
    return header + bytes(table) + blob


# ------------------------------------------------------- raw scan (đơn vị)
class TestRawScanUnits(unittest.TestCase):
    def test_ascii_runs(self):
        out = rawscan.scan_bytes(
            b"Hello world, this is a test\x00\x01\x02more text here",
            "fx")
        texts = [s["text"] for s in out["strings"]]
        self.assertIn("Hello world, this is a test", texts)
        self.assertIn("more text here", texts)
        self.assertEqual(out["label"], "unverified-raw-scan")

    def test_utf16le_both_phases(self):
        # Chuỗi UTF-16LE bắt đầu ở offset lẻ (sau 1 byte rác) và chẵn.
        data = (b"\xff" + "Hello beautiful world".encode("utf-16-le")
                + b"\x00\x00" + "Another fine day here".encode("utf-16-le"))
        out = rawscan.scan_bytes(data, "fx")
        texts = [s["text"] for s in out["strings"]]
        self.assertIn("Hello beautiful world", texts)
        self.assertIn("Another fine day here", texts)

    def test_chunk_boundary_spanning(self):
        # Chuỗi vắt qua ranh giới chunk 16 byte: tail hold phải giữ đủ.
        data = b"\x00" * 10 + b"Hello beautiful world" + b"\x00" * 10
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "fx.bin"
            p.write_bytes(data)
            out = rawscan.scan_file(str(p), chunk_size=16)
        texts = [s["text"] for s in out["strings"]]
        self.assertIn("Hello beautiful world", texts)

    def test_long_run_merged_not_kept(self):
        # Run dài > 4096 ký tự: bị gắn cờ merged, không giữ nguyên.
        # Chuỗi ngắn đứng sau (ngăn bằng NUL) vẫn giữ được.
        data = b"A" * 5000 + b"\x00some short text here"
        out = rawscan.scan_bytes(data, "fx")
        self.assertGreater(out["merged_long"], 0)
        texts = [s["text"] for s in out["strings"]]
        self.assertNotIn("A" * 5000, texts)
        self.assertIn("some short text here", texts)

    def test_noise_filter(self):
        # CamelCase 3+ humps / GUID / namespace / placeholder-only: bỏ.
        # snake_case 1 từ bị loại hẳn (noise — tên mã), không vào bucket nào;
        # từ đơn thường (vd "Vault") vào single_words.
        data = b"\x00".join([
            b"MyClassNameHere",
            b"12345678-1234-1234-1234-123456789012",
            b"Outfit7.Tom.Game.Manager",
            b"{0}",
            b"snake_case_name_here",
            b"Vault",
            b"Hello real world text",
        ])
        out = rawscan.scan_bytes(data, "fx")
        texts = [s["text"] for s in out["strings"]]
        self.assertNotIn("MyClassNameHere", texts)
        self.assertNotIn("12345678-1234-1234-1234-123456789012", texts)
        self.assertNotIn("Outfit7.Tom.Game.Manager", texts)
        self.assertNotIn("{0}", texts)
        self.assertNotIn("snake_case_name_here", texts)
        self.assertIn("Hello real world text", texts)
        singles = [s["text"] for s in out["single_words"]]
        self.assertNotIn("snake_case_name_here", singles)
        self.assertIn("Vault", singles)
        self.assertGreater(out["dropped_noise"], 0)


# ------------------------------------------------------- IL2CPP (đơn vị)
class TestIl2cppUnits(unittest.TestCase):
    def test_v31_exact_split(self):
        # 2 literal liền nhau không có ký tự ngăn cách: quét run ngây thơ
        # nối thành 1, bảng literal tách đúng thành 2.
        lits = ["Hello world", "Daily challenge!", "Café au lait",
                "Line one\nLine two"]
        data = _build_meta_v31(lits)
        out = il2cpp_sdk.extract_literals(io.BytesIO(data), "fx")
        self.assertEqual(out["mode"], "il2cpp")
        self.assertEqual(out["metadata_version"], 31)
        self.assertEqual(out["literals_total"], 4)
        texts = [s["text"] for s in out["strings"]]
        for lit in lits:
            self.assertIn(lit, texts)
        self.assertNotIn("Hello worldDaily challenge!", texts)
        self.assertEqual(out["label"], "il2cpp-literals")

    def test_v24_v27_v29_exact_split(self):
        # [FIXTURE] v24/v27/v29 cùng layout pairs như v31 (Il2CppDumper +
        # rea: header 4 field, entry 8 byte; chỉ có fixture tổng hợp,
        # bộ mẫu thật chỉ có v31/v39) — tách đúng từng literal.
        lits = ["Hello world", "Daily challenge!", "Café au lait"]
        for version, header_size in ((24, 264), (27, 256), (29, 256)):
            with self.subTest(version=version):
                data = _build_meta_pairs(version, lits, header_size)
                out = il2cpp_sdk.extract_literals(io.BytesIO(data), "fx")
                self.assertEqual(out["mode"], "il2cpp")
                self.assertEqual(out["metadata_version"], version)
                self.assertEqual(out["literals_total"], 3)
                texts = [s["text"] for s in out["strings"]]
                for lit in lits:
                    self.assertIn(lit, texts)
                self.assertNotIn("Hello worldDaily challenge!", texts)

    def test_v39_exact_split(self):
        lits = ["Hello world", "Daily challenge!", "Custom level"]
        data = _build_meta_v39(lits)
        out = il2cpp_sdk.extract_literals(io.BytesIO(data), "fx")
        self.assertEqual(out["mode"], "il2cpp")
        self.assertEqual(out["metadata_version"], 39)
        self.assertEqual(out["literals_total"], 3)
        texts = [s["text"] for s in out["strings"]]
        for lit in lits:
            self.assertIn(lit, texts)

    def test_unsupported_version(self):
        data = bytearray(_build_meta_v31(["Hello world"]))
        struct.pack_into("<i", data, 4, 40)  # version 40 chưa xác minh
        with self.assertRaises(PB3Error) as ctx:
            il2cpp_sdk.extract_literals(io.BytesIO(bytes(data)), "fx")
        self.assertEqual(ctx.exception.code, "E_UNSUPPORTED_FORMAT")
        # v30/v32: không có bằng chứng từng phát hành (v32–v38 chưa từng
        # phát hành công khai — Il2CppDumper PR #925) -> vẫn từ chối.
        for version in (30, 32):
            with self.subTest(version=version):
                data = bytearray(_build_meta_v31(["Hello world"]))
                struct.pack_into("<i", data, 4, version)
                with self.assertRaises(PB3Error) as ctx:
                    il2cpp_sdk.extract_literals(io.BytesIO(bytes(data)),
                                                "fx")
                self.assertEqual(ctx.exception.code, "E_UNSUPPORTED_FORMAT")

    def test_corrupt_table_rejected(self):
        # Bảng toàn 0: literal rỗng — kiểm chứng mẫu phải từ chối thay vì
        # trả chuỗi rác.
        header = struct.pack("<Ii", 0xFAB11BAF, 31)
        header += struct.pack("<iiii", 256, 160, 416, 0)
        header += b"\x00" * (256 - len(header))
        data = header + b"\x00" * 160
        with self.assertRaises(PB3Error) as ctx:
            il2cpp_sdk.extract_literals(io.BytesIO(data), "fx")
        self.assertEqual(ctx.exception.code, "E_CORRUPT")

    def test_scan_target_detects_il2cpp_by_magic(self):
        # Không theo tên file: đặt tên tuỳ ý, nhận diện bằng magic.
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "whatever.bin"
            p.write_bytes(_build_meta_v31(["Daily challenge!"]))
            out = rawscan.scan_target(str(p))
            self.assertEqual(out["mode"], "il2cpp")
            self.assertIn("Daily challenge!",
                          [s["text"] for s in out["strings"]])


# ------------------------------------------------------- inspect (fixture)
class TestInspectDeepFixture(unittest.TestCase):
    def test_bundle_with_text_asset(self):
        sf = build_serialized_file(
            objects=[(1, 0, text_asset_data("config.json", '{"a": 1}'))],
            types=[(49, None)])
        bundle = build_bundle(entries=[("test.assets", 4, sf)])
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "fx.unity3d"
            p.write_bytes(bundle)
            out = json.loads(bridge.inspect_deep(str(p)))
        self.assertTrue(out["ok"])
        self.assertEqual(out["label"], "deep-inspect")
        self.assertEqual(len(out["unity_files"]), 1)
        deep = out["unity_files"][0]["deep"]
        self.assertEqual(deep["kind"], "bundle")
        self.assertEqual(deep["bundle"]["entries"][0]["name"], "test.assets")
        counts = deep.get("files", [{}])[0].get("objects_by_class", [])
        text_assets = [c for c in counts if c.get("class") == "TextAsset"]
        self.assertEqual(len(text_assets), 1)
        self.assertEqual(text_assets[0]["count"], 1)
        self.assertFalse(deep.get("language_sources_found", False))

    def test_list_entries_lazy_parity(self):
        # list_entries lười (chỉ đọc head + inventory) vẫn liệt kê đủ.
        with tempfile.TemporaryDirectory() as td:
            zp = Path(td) / "fx.zip"
            with zipfile.ZipFile(zp, "w") as zf:
                zf.writestr("a.txt", "hello")
                zf.writestr("dir/b.txt", "world")
            out = json.loads(bridge.list_entries(str(zp)))
        self.assertTrue(out["ok"])
        self.assertEqual(out["total"], 2)
        paths = [e["path"] for e in out["entries"]]
        self.assertIn("a.txt", paths)
        self.assertIn("dir/b.txt", paths)


# ------------------------------------------------------- IPA thật (skip nếu thiếu)
class TestRealTom(unittest.TestCase):
    @slow
    def test_il2cpp_finds_main_text(self):
        # [ĐÃ ĐO THẬT] Tom v31: 12.090 literal; 'Daily challenge!' tách
        # chính xác thành 1 literal (quét thô không thấy vì nằm trong
        # run dài). Không tính 32 key quảng cáo.
        if not TOM_IPA.exists():
            raise unittest.SkipTest("thieu IPA Tom: %s" % TOM_IPA)
        out = json.loads(bridge.raw_scan(str(TOM_IPA), TOM_META))
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["mode"], "il2cpp")
        self.assertEqual(out["label"], "il2cpp-literals")
        self.assertEqual(out["metadata_version"], 31)
        self.assertEqual(out["literals_total"], 12090)
        texts = [s["text"] for s in out["strings"]]
        for want in ("Daily challenge!", "Daily Vault Challenge",
                     "Change lang", "Cut it out!",
                     "Confirm resetting all local achievements."):
            self.assertIn(want, texts)
        self.assertGreaterEqual(out["kept"], 2000)

    @slow
    def test_inspect_deep_tom(self):
        # [ĐÃ ĐO THẬT] inspect_deep: 1693 entry (1692 file + 1 unity);
        # data.unity3d 78.469.953 B, UnityFS v8, 1307 block LZ4HC, 141
        # entry; pipeline bỏ qua vì > 64 MB; 0 nguồn ngôn ngữ.
        if not TOM_IPA.exists():
            raise unittest.SkipTest("thieu IPA Tom: %s" % TOM_IPA)
        out = json.loads(bridge.inspect_deep(str(TOM_IPA)))
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["total_entries"], 1693)
        self.assertEqual(out["entries_by_kind"], {"file": 1692, "unity": 1})
        self.assertEqual(len(out["unity_files"]), 1)
        uf = out["unity_files"][0]
        self.assertTrue(uf["path"].endswith("data.unity3d"))
        self.assertEqual(uf["size"], 78469953)
        self.assertIn("67108864", uf["pipeline"])  # giới hạn 64 MB
        deep = uf["deep"]
        self.assertEqual(deep["bundle"]["version"], 8)
        self.assertEqual(deep["bundle"]["blocks"], 1307)
        self.assertEqual(len(deep["bundle"]["entries"]), 141)
        self.assertFalse(deep.get("language_sources_found", False))

    @slow
    def test_list_entries_tom(self):
        if not TOM_IPA.exists():
            raise unittest.SkipTest("thieu IPA Tom: %s" % TOM_IPA)
        out = json.loads(bridge.list_entries(str(TOM_IPA)))
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["total"], 1693)

    @slow
    def test_raw_scan_bundle_tom(self):
        # Quét payload bundle đã giải nén (mode unity-bundle): tìm được
        # chuỗi (cap 10000), không nạp cả file 210 MB vào RAM.
        if not TOM_IPA.exists():
            raise unittest.SkipTest("thieu IPA Tom: %s" % TOM_IPA)
        out = json.loads(bridge.raw_scan(str(TOM_IPA), TOM_BUNDLE))
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["mode"], "unity-bundle")
        self.assertEqual(out["label"], "unverified-raw-scan")
        self.assertGreater(out["kept"], 1000)


class TestRealNineKingsIl2cpp(unittest.TestCase):
    @slow
    def test_v39_finds_literals(self):
        # [ĐÃ ĐO THẬT] 9K v39: 28.286 literal, 27.943 đọc được.
        if not NINEKINGS_IPA.exists():
            raise unittest.SkipTest("thieu IPA 9K: %s" % NINEKINGS_IPA)
        out = json.loads(bridge.raw_scan(str(NINEKINGS_IPA),
                                         NINEKINGS_META))
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["mode"], "il2cpp")
        self.assertEqual(out["metadata_version"], 39)
        self.assertEqual(out["literals_total"], 28286)
        self.assertGreaterEqual(out["kept"], 5000)


if __name__ == "__main__":
    unittest.main()
