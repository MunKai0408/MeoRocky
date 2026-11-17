# -*- coding: utf-8 -*-
"""Test đường literals IL2CPP -> bảng dịch -> vá tại chỗ -> đóng gói IPA.

Fixture TỰ DỰNG (metadata v31/v39 tổng hợp); không dùng mạng.
Test IPA thật ở cuối: skip nếu thiếu file
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
sys.path.insert(0, str(ROOT))

import bridge  # noqa: E402

bridge.WEB_ROOT = str(ROOT)
json.loads(bridge.boot())

from core.errors import PB3Error  # noqa: E402
from sdk import il2cpp as il2cpp_sdk  # noqa: E402
from tests.tiering import slow  # noqa: E402

TOM_IPA = Path(os.environ.get(
    "TOM_IPA", Path.home() / "workspace" / "test-ipas" /
    "My_Talking_Tom_v26.5.1_Decrypted.ipa"))
NINEKINGS_IPA = Path(os.environ.get(
    "NINEKINGS_IPA", Path.home() / "workspace" / "test-ipas" /
    "9kings-v1.0.6.ipa"))
TOM_META = ("Payload/MyTalkingTom-row.app/Frameworks/UnityFramework."
            "framework/Data/Managed/Metadata/global-metadata.dat")
NINEKINGS_META = ("Payload/9Kings.app/Data/Managed/Metadata/"
                  "global-metadata.dat")


def _build_meta_v31(literals):
    """global-metadata.dat v31 tổng hợp: header 256 + bảng (len, idx)."""
    blob = b"".join(s.encode("utf-8") for s in literals)
    table = bytearray()
    off = 0
    for s in literals:
        raw = s.encode("utf-8")
        table += struct.pack("<II", len(raw), off)
        off += len(raw)
    header = struct.pack("<Ii", 0xFAB11BAF, 31)
    header += struct.pack("<iiii", 256, len(table), 256 + len(table),
                          len(blob))
    header += b"\x00" * (256 - len(header))
    return header + bytes(table) + blob


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


def _literals_of(data):
    """Đọc lại toàn bộ literal text từ bytes metadata (theo index)."""
    fh = io.BytesIO(data)
    header = il2cpp_sdk.read_header(fh)
    return {idx: text for idx, text, _o, _l
            in il2cpp_sdk.iter_literals(fh, header)}


class LiteralsTableTest(unittest.TestCase):
    def test_table_key_format(self):
        self.assertEqual(il2cpp_sdk.table_key(0), "LIT_000000")
        self.assertEqual(il2cpp_sdk.table_key(123), "LIT_000123")
        self.assertEqual(il2cpp_sdk.table_key(12089), "LIT_012089")

    def test_table_from_v31_result(self):
        data = _build_meta_v31(["Daily challenge!", "Change lang",
                                "Hello world test", "SaveGame"])
        res = il2cpp_sdk.extract_literals(io.BytesIO(data), "test")
        table, manifest = il2cpp_sdk.literals_to_table(res)
        # "SaveGame" là từ đơn -> không vào bảng mặc định.
        self.assertEqual(sorted(table),
                         ["LIT_000000", "LIT_000001", "LIT_000002"])
        self.assertEqual(table["LIT_000000"], "Daily challenge!")
        self.assertEqual(table["LIT_000001"], "Change lang")
        m = manifest["LIT_000000"]
        self.assertEqual(m["literal_index"], 0)
        self.assertEqual(m["source"], "Daily challenge!")
        self.assertEqual(m["byte_length"], len(b"Daily challenge!"))
        self.assertEqual(m["encoding"], "utf-8")
        self.assertIn("offset", m)

    def test_table_includes_single_words_when_asked(self):
        data = _build_meta_v31(["Daily challenge!", "SaveGame"])
        res = il2cpp_sdk.extract_literals(io.BytesIO(data), "test")
        table, manifest = il2cpp_sdk.literals_to_table(
            res, include_single_words=True)
        self.assertEqual(table["LIT_000001"], "SaveGame")
        self.assertTrue(manifest["LIT_000001"]["single_word"])

    def test_table_duplicate_text_gets_distinct_keys(self):
        data = _build_meta_v31(["Same text here", "Same text here"])
        res = il2cpp_sdk.extract_literals(io.BytesIO(data), "test")
        table, _m = il2cpp_sdk.literals_to_table(res)
        self.assertEqual(len(table), 2)
        self.assertEqual(table["LIT_000000"], table["LIT_000001"])


class PatchLiteralsV31Test(unittest.TestCase):
    def setUp(self):
        self.lits = ["Daily challenge!", "Change lang",
                     "Hello world test"]
        self.data = _build_meta_v31(self.lits)

    def test_patch_shorter_updates_length_and_pads_nul(self):
        patched, rep = il2cpp_sdk.patch_literals(
            self.data, {0: "Im di!"})
        self.assertEqual(len(patched), len(self.data))
        self.assertTrue(rep["verified"])
        self.assertEqual(rep["patched"], [
            {"index": 0, "old_len": 16, "new_len": 6}])
        self.assertEqual(rep["skipped"], [])
        after = _literals_of(patched)
        self.assertEqual(after[0], "Im di!")
        self.assertEqual(after[1], "Change lang")
        self.assertEqual(after[2], "Hello world test")
        # Length trong bảng đã sửa (đọc thô): entry 0 length = 6.
        fh = io.BytesIO(patched)
        header = il2cpp_sdk.read_header(fh)
        fh.seek(header.table_off)
        ln, _off = struct.unpack("<II", fh.read(8))
        self.assertEqual(ln, 6)

    def test_patch_exact_length_ok(self):
        patched, rep = il2cpp_sdk.patch_literals(
            self.data, {1: "Doi ngon ng"})  # 11 byte == gốc 11 byte
        self.assertEqual(rep["patched"][0]["new_len"], 11)
        self.assertEqual(_literals_of(patched)[1], "Doi ngon ng")

    def test_patch_too_long_skipped_with_reason(self):
        patched, rep = il2cpp_sdk.patch_literals(
            self.data, {1: "Doi ngon ngu day du qua dai"})
        self.assertEqual(rep["patched"], [])
        self.assertEqual(len(rep["skipped"]), 1)
        self.assertIn("too_long", rep["skipped"][0]["reason"])
        # File không đổi gì (literal gốc còn nguyên).
        self.assertEqual(_literals_of(patched)[1], "Change lang")

    def test_patch_unchanged_and_unknown_index_skipped(self):
        patched, rep = il2cpp_sdk.patch_literals(
            self.data, {2: "Hello world test", 99: "X"})
        self.assertEqual(rep["patched"], [])
        reasons = [s["reason"] for s in rep["skipped"]]
        self.assertTrue(any("unchanged" in r for r in reasons))
        self.assertTrue(any("ngoài bảng" in r for r in reasons))
        self.assertEqual(_literals_of(patched), _literals_of(self.data))

    def test_patch_vietnamese_bytes_counted(self):
        # "Thử thách!" = 13 byte UTF-8 (ử = 3 byte, á = 2 byte — đếm
        # theo BYTE, không phải ký tự: đây là điểm dễ sai nhất khi vá).
        patched, rep = il2cpp_sdk.patch_literals(
            self.data, {0: "Thử thách!"})
        self.assertEqual(rep["patched"][0],
                         {"index": 0, "old_len": 16, "new_len": 13})
        self.assertEqual(_literals_of(patched)[0], "Thử thách!")

    def test_patch_empty_string_ok_v31(self):
        patched, rep = il2cpp_sdk.patch_literals(self.data, {0: ""})
        self.assertEqual(rep["patched"][0]["new_len"], 0)
        self.assertEqual(_literals_of(patched)[0], "")


class PatchLiteralsV39Test(unittest.TestCase):
    def setUp(self):
        self.lits = ["Daily challenge!", "Change lang"]
        self.data = _build_meta_v39(self.lits)

    def test_patch_exact_length_only(self):
        patched, rep = il2cpp_sdk.patch_literals(
            self.data, {0: "Thu thach ngay!!"})  # 16 byte == gốc
        self.assertEqual(len(rep["patched"]), 1)
        self.assertEqual(_literals_of(patched)[0], "Thu thach ngay!!")

    def test_patch_shorter_rejected_v39(self):
        # V39 delta: độ dài suy ra từ index kế tiếp nên ngắn hơn cũng
        # không vá được (phần đệm NUL sẽ thành 1 phần của chuỗi).
        patched, rep = il2cpp_sdk.patch_literals(
            self.data, {0: "Im di!"})
        self.assertEqual(rep["patched"], [])
        self.assertIn("length_must_match", rep["skipped"][0]["reason"])
        self.assertEqual(_literals_of(patched)[0], "Daily challenge!")

    def test_patch_longer_rejected_v39(self):
        patched, rep = il2cpp_sdk.patch_literals(
            self.data, {0: "Daily challenge! extra"})
        self.assertEqual(rep["patched"], [])
        self.assertIn("length_must_match", rep["skipped"][0]["reason"])


class ApplyLiteralPatchesTest(unittest.TestCase):
    def setUp(self):
        self.data = _build_meta_v31(["Daily challenge!", "Change lang"])

    def test_apply_with_manifest_verifies_source(self):
        res = il2cpp_sdk.extract_literals(io.BytesIO(self.data), "test")
        table, manifest = il2cpp_sdk.literals_to_table(res)
        patched, rep = il2cpp_sdk.apply_literal_patches(
            self.data, {"LIT_000000": "Im di!"}, manifest)
        self.assertEqual(rep["patched_count"], 1)
        self.assertEqual(_literals_of(patched)[0], "Im di!")
        self.assertEqual(rep["key_results"][0]["outcome"], "patched")

    def test_apply_manifest_source_mismatch_skipped(self):
        res = il2cpp_sdk.extract_literals(io.BytesIO(self.data), "test")
        _table, manifest = il2cpp_sdk.literals_to_table(res)
        manifest["LIT_000000"]["source"] = "WRONG SOURCE TEXT"
        patched, rep = il2cpp_sdk.apply_literal_patches(
            self.data, {"LIT_000000": "Im di!"}, manifest)
        self.assertEqual(rep["patched_count"], 0)
        self.assertIn("source_mismatch",
                      rep["key_results"][0]["reason"])
        self.assertEqual(_literals_of(patched)[0], "Daily challenge!")

    def test_apply_without_manifest_parses_key(self):
        patched, rep = il2cpp_sdk.apply_literal_patches(
            self.data, {"LIT_000001": "Doi ngon ng"})
        self.assertEqual(rep["patched_count"], 1)
        self.assertEqual(_literals_of(patched)[1], "Doi ngon ng")

    def test_apply_bad_key_skipped(self):
        patched, rep = il2cpp_sdk.apply_literal_patches(
            self.data, {"NOT_A_KEY": "X", "LIT_000000": "Im di!"})
        self.assertEqual(rep["patched_count"], 1)
        self.assertTrue(any(r["key"] == "NOT_A_KEY"
                            and r["outcome"] == "skipped"
                            for r in rep["key_results"]))

    def test_apply_duplicate_index_via_manifest_skipped(self):
        # 2 key khác nhau nhưng manifest cùng trỏ 1 literal_index:
        # key sau bị bỏ (trùng literal_index với key trước).
        manifest = {
            "LIT_000000": {"literal_index": 0,
                           "source": "Daily challenge!"},
            "LIT_999999": {"literal_index": 0,
                           "source": "Daily challenge!"},
        }
        patched, rep = il2cpp_sdk.apply_literal_patches(
            self.data,
            {"LIT_000000": "Im di!", "LIT_999999": "Im di! 2"},
            manifest)
        self.assertEqual(rep["patched_count"], 1)
        dup = [r for r in rep["key_results"]
               if r["key"] == "LIT_999999"][0]
        self.assertEqual(dup["outcome"], "skipped")
        self.assertIn("trùng literal_index", dup["reason"])
        self.assertEqual(_literals_of(patched)[0], "Im di!")


class ReadMetadataBytesTest(unittest.TestCase):
    def test_read_plain_file(self):
        data = _build_meta_v31(["Hello world test"])
        with tempfile.NamedTemporaryFile(suffix=".dat",
                                         delete=False) as tf:
            tf.write(data)
            name = tf.name
        try:
            got, label = il2cpp_sdk.read_metadata_bytes(name)
            self.assertEqual(got, data)
            self.assertEqual(label, name)
        finally:
            os.unlink(name)

    def test_read_zip_member_and_missing_entry(self):
        data = _build_meta_v31(["Hello world test"])
        with tempfile.NamedTemporaryFile(suffix=".zip",
                                         delete=False) as tf:
            zpath = tf.name
        try:
            with zipfile.ZipFile(zpath, "w") as zf:
                zf.writestr("a/b/global-metadata.dat", data)
            got, label = il2cpp_sdk.read_metadata_bytes(
                zpath, "a/b/global-metadata.dat")
            self.assertEqual(got, data)
            self.assertIn("a/b/global-metadata.dat", label)
            with self.assertRaises(PB3Error) as cm:
                il2cpp_sdk.read_metadata_bytes(zpath, "no/such.dat")
            self.assertEqual(cm.exception.code, "E_INPUT_NOT_FOUND")
        finally:
            os.unlink(zpath)


class BridgeIl2cppTableTest(unittest.TestCase):
    def test_il2cpp_table_on_synthetic_file(self):
        data = _build_meta_v31(["Daily challenge!", "Change lang",
                                "Hello world test"])
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "global-metadata.dat")
            with open(fp, "wb") as fh:
                fh.write(data)
            out = json.loads(bridge.il2cpp_table(fp))
            self.assertTrue(out["ok"])
            self.assertEqual(out["metadata_version"], 31)
            self.assertEqual(out["literals_total"], 3)
            self.assertEqual(out["table_keys"], 3)
            self.assertEqual(out["table_file"], "il2cpp-literals.json")
            table = json.loads(open(
                "/tmp/pb3out/il2cpp-literals.json",
                encoding="utf-8").read())
            self.assertEqual(table["LIT_000000"], "Daily challenge!")
            manifest = json.loads(open(
                "/tmp/pb3out/il2cpp-literals.manifest.json",
                encoding="utf-8").read())
            self.assertEqual(
                manifest["keys"]["LIT_000001"]["source"], "Change lang")

    def test_il2cpp_table_rejects_non_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "plain.bin")
            with open(fp, "wb") as fh:
                fh.write(b"not il2cpp at all, just bytes....")
            out = json.loads(bridge.il2cpp_table(fp))
            self.assertFalse(out["ok"])

    def test_translate_json_accepts_table_as_dict(self):
        # Hồi quy audit §A1: bảng literals là dict thuần nên tab Dịch
        # KHÔNG stringify cả list thành 1 chuỗi khổng lồ.
        table = {"LIT_000000": "Daily challenge!",
                 "LIT_000001": "Change lang",
                 "LIT_000002": "Hello world test"}
        out = json.loads(bridge.translate_json(
            json.dumps(table, ensure_ascii=False), to="vi", src="en"))
        self.assertTrue(out["ok"])
        self.assertEqual(out["total"], 3)
        self.assertEqual(sorted(out["translated"]),
                         sorted(table))
        for v in out["translated"].values():
            self.assertIsInstance(v, str)
            self.assertLess(len(v), 1000)
            self.assertNotIn('"label"', v)

    def test_patch_op_on_synthetic_file(self):
        data = _build_meta_v31(["Daily challenge!", "Change lang"])
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "global-metadata.dat")
            with open(fp, "wb") as fh:
                fh.write(data)
            table = {"LIT_000000": "Im di!",
                     "LIT_000001": "Doi ngon ngu day du qua dai"}
            out = json.loads(bridge.patch_il2cpp_literals(
                fp, None, json.dumps(table, ensure_ascii=False)))
            self.assertTrue(out["ok"])
            self.assertEqual(out["patched_count"], 1)
            self.assertEqual(out["skipped_count"], 1)
            self.assertTrue(out["verified"])
            self.assertEqual(out["filename"],
                             "global-metadata.patched.dat")
            patched = open("/tmp/pb3out/global-metadata.patched.dat",
                           "rb").read()
            self.assertEqual(_literals_of(patched)[0], "Im di!")
            # File gốc không đổi.
            self.assertEqual(open(fp, "rb").read(), data)

    def test_repack_op_requires_patched_file(self):
        # /tmp/pb3out có thể còn file đã vá từ test trước — test này chỉ
        # kiểm tra lỗi khi sidecar không khớp file đang chọn.
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "game.ipa")
            with open(fp, "wb") as fh:
                fh.write(b"PK\x03\x04not a real ipa")
            out = json.loads(bridge.il2cpp_repack(fp, None))
            self.assertFalse(out["ok"])


class RealIpaIl2cppTest(unittest.TestCase):
    @slow
    @unittest.skipUnless(TOM_IPA.exists(), "thiếu IPA Tom thật")
    def test_tom_table_counts(self):
        out = json.loads(bridge.il2cpp_table(str(TOM_IPA), TOM_META))
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["metadata_version"], 31)
        self.assertEqual(out["literals_total"], 12090)
        # 2.904 chuỗi nhiều từ [ĐÃ ĐO THẬT — audit §A1 / PR #10].
        self.assertEqual(out["table_keys"], 2904)
        self.assertFalse(out["truncated"])
        table = json.loads(open("/tmp/pb3out/il2cpp-literals.json",
                                encoding="utf-8").read())
        self.assertIn("Daily challenge!", table.values())
        self.assertIn("Change lang", table.values())

    @slow
    @unittest.skipUnless(TOM_IPA.exists(), "thiếu IPA Tom thật")
    def test_tom_patch_one_literal_and_repack(self):
        # Vá 1 literal thật bằng bản dịch ngắn hơn rồi đóng gói lại IPA.
        out = json.loads(bridge.il2cpp_table(str(TOM_IPA), TOM_META))
        self.assertTrue(out["ok"], out)
        table = json.loads(open("/tmp/pb3out/il2cpp-literals.json",
                                encoding="utf-8").read())
        manifest = json.loads(open(
            "/tmp/pb3out/il2cpp-literals.manifest.json",
            encoding="utf-8").read())
        key = next(k for k, v in table.items()
                   if v == "Daily challenge!")
        # "Thử thách!" = 12 byte <= gốc 16 byte (v31 ghi được).
        patched = json.loads(bridge.patch_il2cpp_literals(
            str(TOM_IPA), TOM_META,
            json.dumps({key: "Thử thách!"}, ensure_ascii=False),
            json.dumps(manifest, ensure_ascii=False)))
        self.assertTrue(patched["ok"], patched)
        self.assertEqual(patched["patched_count"], 1)
        self.assertTrue(patched["verified"])
        # Đọc lại literal từ file đã vá.
        data = open("/tmp/pb3out/global-metadata.patched.dat",
                    "rb").read()
        after = _literals_of(data)
        idx = manifest["keys"][key]["literal_index"]
        self.assertEqual(after[idx], "Thử thách!")
        # Đóng gói lại IPA với metadata đã vá (kiểm tra 6 mục M2).
        # out_dir đĩa lớn: /tmp tmpfs chỉ 512 MB mà AtomicWriter cần
        # 2.5x kích thước IPA (~525 MB cho Tom 210 MB) nên không đóng
        # gói được trong /tmp/repack_out mặc định.
        repacked = json.loads(bridge.il2cpp_repack(
            str(TOM_IPA), TOM_META,
            out_dir="/home/hatch/tmp-pb3test/repack_out"))
        self.assertTrue(repacked["ok"], repacked)
        self.assertTrue(all(c["passed"] for c in repacked["checks"]),
                        repacked["checks"])
        self.assertIn(TOM_META, repacked["replaced"])
        # IPA mới chứa metadata đã vá.
        with zipfile.ZipFile(repacked["dest"], "r") as zf:
            self.assertEqual(zf.read(TOM_META), data)

    @slow
    @unittest.skipUnless(NINEKINGS_IPA.exists(), "thiếu IPA 9K thật")
    def test_9k_table_v39(self):
        # 9K dùng metadata v39 (delta): bảng vẫn xuất được; vá thì chỉ
        # literal đúng bằng byte mới ghi được (xem PatchLiteralsV39Test).
        out = json.loads(bridge.il2cpp_table(str(NINEKINGS_IPA),
                                             NINEKINGS_META))
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["metadata_version"], 39)
        self.assertEqual(out["literals_total"], 28286)
        self.assertGreater(out["table_keys"], 0)


if __name__ == "__main__":
    unittest.main()
