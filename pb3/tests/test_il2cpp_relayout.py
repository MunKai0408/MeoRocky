# -*- coding: utf-8 -*-
"""Test re-layout blob literal IL2CPP (F1): ghi bản dịch DÀI HƠN gốc.

Fixture TỰ DỰNG (metadata v31/v39 tổng hợp, đủ 31 section theo đúng
bố cục đã xác minh trên file thật); test IPA thật ở cuối: skip nếu
thiếu file (env TOM_IPA / NINEKINGS_IPA hoặc ~/workspace/test-ipas/).
"""

from __future__ import annotations

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

# Section sau blob (nội dung giả, byte khác nhau để bắt lỗi dời sai).
_TAIL_STRINGS = b"STRINGS-DATA-0123456789-abcdefghij"
_TAIL_EVENTS = b"EVENTS-DATA-0123456789"


def _align4(n):
    return (n + 3) & ~3


def _build_meta_full(parts, version):
    """global-metadata.dat tổng hợp đủ 31 section (v31 pairs / v39
    triples), parts = list bytes literal theo thứ tự index."""
    n = len(parts)
    blob = b"".join(parts)
    if version == 31:
        table = bytearray()
        off = 0
        for raw in parts:
            table += struct.pack("<II", len(raw), off)
            off += len(raw)
        header_size = 256
    else:
        table = bytearray()
        off = 0
        for raw in parts:
            table += struct.pack("<I", off)
            off += len(raw)
        header_size = 380
    sizes = [len(table),
             _align4(len(blob)) if version == 31 else len(blob),
             len(_TAIL_STRINGS),
             len(_TAIL_EVENTS)] + [0] * 27
    offs = [header_size]
    for k in range(30):
        offs.append(_align4(offs[k] + sizes[k]))
    total = offs[-1]
    counts = [0] * 31
    if version == 39:
        counts[0] = n
        counts[1] = sum(1 for p in parts if p)
        counts[2] = 2
        counts[3] = 1
    out = bytearray(total)
    header = struct.pack("<Ii", 0xFAB11BAF, version)
    for k in range(31):
        if version == 31:
            header += struct.pack("<ii", offs[k], sizes[k])
        else:
            header += struct.pack("<iii", offs[k], sizes[k], counts[k])
    header += b"\x00" * (header_size - len(header))
    out[0:header_size] = header
    out[offs[0]:offs[0] + len(table)] = bytes(table)
    out[offs[1]:offs[1] + len(blob)] = blob
    out[offs[2]:offs[2] + len(_TAIL_STRINGS)] = _TAIL_STRINGS
    out[offs[3]:offs[3] + len(_TAIL_EVENTS)] = _TAIL_EVENTS
    return bytes(out)


def _build_meta_v31(parts):
    return _build_meta_full(parts, 31)


def _build_meta_v39(parts):
    return _build_meta_full(parts, 39)


def _literals_of(data):
    """Đọc lại toàn bộ literal text từ bytes metadata (theo index)."""
    fh = io.BytesIO(data)
    header = il2cpp_sdk.read_header(fh)
    return {idx: text for idx, text, _o, _l
            in il2cpp_sdk.iter_literals(fh, header)}


def _raw_entries_of(data):
    """Đọc lại toàn bộ literal theo byte thô: {index: bytes}."""
    fh = io.BytesIO(data)
    header = il2cpp_sdk.read_header(fh)
    entries = il2cpp_sdk._read_literal_entries(fh, header)
    blob = data[header.data_off:header.data_off + header.data_size]
    return {i: blob[off:off + ln]
            for i, (ln, off) in enumerate(entries)}


class RelayoutV31Test(unittest.TestCase):
    def test_relayout_longer_patch_grows_file_and_shifts_tail(self):
        parts = [b"Daily challenge!", b"Change lang",
                 b"Hello world test"]
        data = _build_meta_v31(parts)
        new, rep = il2cpp_sdk.relayout_literals(
            data, {1: "Doi ngon ngu day du qua dai"})
        self.assertEqual(rep["mode"], "relayout")
        self.assertTrue(rep["verified"])
        self.assertEqual(rep["patched"], [
            {"index": 1, "old_len": 11, "new_len": 27,
             "old_offset": 16, "new_offset": 16}])
        self.assertEqual(rep["skipped"], [])
        self.assertEqual(rep["sections_shifted"], 29)
        # File lớn lên đúng delta = byte thêm + đệm căn 4.
        self.assertEqual(rep["size_delta"], len(new) - len(data))
        self.assertEqual(rep["old_size"], len(data))
        self.assertEqual(rep["new_size"], len(new))
        after = _literals_of(new)
        self.assertEqual(after[1], "Doi ngon ngu day du qua dai")
        self.assertEqual(after[0], "Daily challenge!")
        self.assertEqual(after[2], "Hello world test")
        # Section sau blob dời nguyên vẹn từng byte.
        old_secs = il2cpp_sdk._read_sections(
            data, il2cpp_sdk.read_header(io.BytesIO(data)))
        new_secs = il2cpp_sdk._read_sections(
            new, il2cpp_sdk.read_header(io.BytesIO(new)))
        self.assertEqual(
            new[old_secs[2]["offset"] + rep["size_delta"]:],
            data[old_secs[2]["offset"]:])
        self.assertEqual(new_secs[2]["offset"],
                         old_secs[2]["offset"] + rep["size_delta"])
        self.assertIn(_TAIL_STRINGS, new)
        self.assertIn(_TAIL_EVENTS, new)

    def test_relayout_mixed_and_skips(self):
        parts = [b"Daily challenge!", b"Change lang",
                 b"Hello world test"]
        data = _build_meta_v31(parts)
        new, rep = il2cpp_sdk.relayout_literals(
            data, {0: "Thử thách dài hơn gốc nhiều",
                   1: "ngắn",
                   2: "Hello world test",
                   99: "X",
                   1.5: "Y"})
        self.assertEqual(len(rep["patched"]), 2)
        self.assertEqual(
            sorted(p["index"] for p in rep["patched"]), [0, 1])
        reasons = [s["reason"] for s in rep["skipped"]]
        self.assertTrue(any("unchanged" in r for r in reasons))
        self.assertTrue(any("ngoài bảng" in r for r in reasons))
        after = _literals_of(new)
        self.assertEqual(after[0], "Thử thách dài hơn gốc nhiều")
        self.assertEqual(after[1], "ngắn")

    def test_relayout_noop_returns_identical_bytes(self):
        parts = [b"Daily challenge!", b"Change lang"]
        data = _build_meta_v31(parts)
        new, rep = il2cpp_sdk.relayout_literals(data, {})
        self.assertEqual(new, data)
        self.assertEqual(rep["size_delta"], 0)
        new2, _rep2 = il2cpp_sdk.relayout_literals(
            data, {0: "Daily challenge!"})
        self.assertEqual(new2, data)

    def test_relayout_keeps_binary_literal_bytes(self):
        # Literal chứa byte điều khiển: không đọc được text nhưng byte
        # thô phải giữ nguyên sau khi dựng lại blob.
        parts = [b"Bad\x01string here now", b"Daily challenge!"]
        data = _build_meta_v31(parts)
        new, rep = il2cpp_sdk.relayout_literals(
            data, {0: "Im di!", 1: "Thu thach dai hon nhieu"})
        self.assertEqual(len(rep["skipped"]), 1)
        self.assertIn("không đọc được",
                      rep["skipped"][0]["reason"])
        self.assertEqual(_raw_entries_of(new)[0],
                         b"Bad\x01string here now")
        self.assertEqual(_literals_of(new)[1],
                         "Thu thach dai hon nhieu")

    def test_relayout_refuses_v24(self):
        data = _build_meta_full([b"Hello world test"], 31)
        data24 = b"\xaf\x1b\xb1\xfa" + struct.pack("<i", 24) \
            + data[8:]
        with self.assertRaises(PB3Error) as cm:
            il2cpp_sdk.relayout_literals(data24, {0: "Xin chào dài"})
        self.assertEqual(cm.exception.code, "E_UNSUPPORTED_FORMAT")

    def test_relayout_refuses_corrupt_tiling(self):
        data = _build_meta_v31([b"Hello world test"])
        bad = bytearray(data)
        # Dời section strings đi 4 byte (phá tính liền nhau).
        struct.pack_into("<i", bad, 8 + 8 * 2,
                         struct.unpack_from("<i", bad, 8 + 8 * 2)[0] + 4)
        with self.assertRaises(PB3Error) as cm:
            il2cpp_sdk.relayout_literals(bytes(bad),
                                         {0: "Xin chào dài hơn"})
        self.assertEqual(cm.exception.code, "E_CORRUPT")


class RelayoutV39Test(unittest.TestCase):
    def test_relayout_longer_and_shorter_mixed(self):
        parts = [b"Daily challenge!", b"Change lang",
                 b"Hello world test"]
        data = _build_meta_v39(parts)
        new, rep = il2cpp_sdk.relayout_literals(
            data, {0: "Thử thách hàng ngày dài hơn",
                   1: "ngắn",
                   2: "Hello world test extra long text"})
        self.assertEqual(rep["mode"], "relayout")
        self.assertTrue(rep["verified"])
        self.assertEqual(len(rep["patched"]), 3)
        self.assertEqual(len(rep["skipped"]), 0)
        after = _literals_of(new)
        self.assertEqual(after[0], "Thử thách hàng ngày dài hơn")
        self.assertEqual(after[1], "ngắn")
        self.assertEqual(after[2], "Hello world test extra long text")
        # Bảng delta vẫn đơn điệu sau khi dựng lại.
        fh = io.BytesIO(new)
        header = il2cpp_sdk.read_header(fh)
        idxs = struct.unpack_from(
            "<%dI" % header.entry_count, new, header.table_off)
        self.assertTrue(all(idxs[i] <= idxs[i + 1]
                            for i in range(len(idxs) - 1)))
        # Section sau blob dời nguyên vẹn.
        self.assertIn(_TAIL_STRINGS, new)
        self.assertIn(_TAIL_EVENTS, new)

    def test_relayout_empty_last_literal_stays_empty(self):
        # 9K thật có literal cuối rỗng; dataSize exact của v39 phải
        # giữ nó rỗng (không biến thành "\x00" do đệm căn 4).
        parts = [b"Daily challenge!", b"Change lang", b""]
        data = _build_meta_v39(parts)
        new, rep = il2cpp_sdk.relayout_literals(
            data, {0: "Thử thách dài hơn gốc"})
        self.assertEqual(_literals_of(new)[2], "")
        raw = _raw_entries_of(new)
        self.assertEqual(raw[2], b"")
        self.assertEqual(raw[0], "Thử thách dài hơn gốc".encode("utf-8"))

    def test_relayout_vietnamese_bytes(self):
        parts = [b"Daily challenge!"]
        data = _build_meta_v39(parts)
        new, rep = il2cpp_sdk.relayout_literals(
            data, {0: "Thử thách hằng ngày!"})
        self.assertEqual(rep["patched"][0]["new_len"],
                         len("Thử thách hằng ngày!".encode("utf-8")))
        self.assertEqual(_literals_of(new)[0],
                         "Thử thách hằng ngày!")


class RelayoutPatchApiTest(unittest.TestCase):
    def test_patch_literals_relayout_flag(self):
        data = _build_meta_v31([b"Daily challenge!", b"Change lang"])
        # Mặc định (vá tại chỗ): dài hơn bị từ chối.
        _p, rep = il2cpp_sdk.patch_literals(
            data, {1: "Doi ngon ngu day du qua dai"})
        self.assertEqual(rep["mode"], "inplace")
        self.assertEqual(rep["patched"], [])
        # relayout=True: ghi được.
        new, rep2 = il2cpp_sdk.patch_literals(
            data, {1: "Doi ngon ngu day du qua dai"}, relayout=True)
        self.assertEqual(rep2["mode"], "relayout")
        self.assertEqual(len(rep2["patched"]), 1)
        self.assertEqual(_literals_of(new)[1],
                         "Doi ngon ngu day du qua dai")

    def test_apply_with_manifest_relayout(self):
        data = _build_meta_v39([b"Daily challenge!", b"Change lang"])
        res = il2cpp_sdk.extract_literals(io.BytesIO(data), "test")
        table, manifest = il2cpp_sdk.literals_to_table(res)
        new, rep = il2cpp_sdk.apply_literal_patches(
            data, {"LIT_000000": "Thử thách hàng ngày dài hơn"},
            manifest, relayout=True)
        self.assertEqual(rep["mode"], "relayout")
        self.assertEqual(rep["patched_count"], 1)
        self.assertEqual(rep["key_results"][0]["outcome"], "patched")
        self.assertEqual(_literals_of(new)[0],
                         "Thử thách hàng ngày dài hơn")

    def test_bridge_patch_op_relayout(self):
        data = _build_meta_v31([b"Daily challenge!", b"Change lang"])
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "global-metadata.dat")
            with open(fp, "wb") as fh:
                fh.write(data)
            table = {"LIT_000001": "Doi ngon ngu day du qua dai"}
            out = json.loads(bridge.patch_il2cpp_literals(
                fp, None, json.dumps(table, ensure_ascii=False),
                None, True))
            self.assertTrue(out["ok"], out)
            self.assertEqual(out["mode"], "relayout")
            self.assertEqual(out["patched_count"], 1)
            self.assertEqual(out["skipped_count"], 0)
            self.assertGreater(out["patched_bytes"], len(data))
            patched = open("/tmp/pb3out/global-metadata.patched.dat",
                           "rb").read()
            self.assertEqual(_literals_of(patched)[1],
                             "Doi ngon ngu day du qua dai")
            self.assertEqual(open(fp, "rb").read(), data)

    def test_bridge_repack_after_relayout_synthetic_ipa(self):
        meta = _build_meta_v31([b"Daily challenge!", b"Change lang"])
        entry = ("Payload/Test.app/Data/Managed/Metadata/"
                 "global-metadata.dat")
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "test.ipa")
            with zipfile.ZipFile(src, "w") as zf:
                zf.writestr("Payload/Test.app/", b"",
                            compress_type=zipfile.ZIP_STORED)
                zf.writestr("Payload/Test.app/Info.plist",
                            b"<?xml version='1.0'?><plist><dict/></plist>",
                            compress_type=zipfile.ZIP_DEFLATED)
                zf.writestr("Payload/Test.app/Data/resources.assets",
                            b"ASSETS-v1-" + b"A" * 100,
                            compress_type=zipfile.ZIP_DEFLATED)
                zf.writestr(entry, meta,
                            compress_type=zipfile.ZIP_DEFLATED)
            table = {"LIT_000000": "Thử thách hàng ngày dài hơn"}
            patched = json.loads(bridge.patch_il2cpp_literals(
                src, entry, json.dumps(table, ensure_ascii=False),
                None, True))
            self.assertTrue(patched["ok"], patched)
            self.assertEqual(patched["patched_count"], 1)
            repacked = json.loads(bridge.il2cpp_repack(
                src, entry, out_dir=os.path.join(td, "repack_out")))
            self.assertTrue(repacked["ok"], repacked)
            self.assertTrue(all(c["passed"]
                                for c in repacked["checks"]),
                            repacked["checks"])
            with zipfile.ZipFile(repacked["dest"], "r") as zf:
                got = zf.read(entry)
            self.assertEqual(_literals_of(got)[0],
                             "Thử thách hàng ngày dài hơn")
            self.assertGreater(len(got), len(meta))


class RealIpaRelayoutTest(unittest.TestCase):
    """Đo trên metadata thật: bản dịch giả dài hơn (thêm hậu tố
    tiếng Việt) — đếm số key vá được tại chỗ vs re-layout."""

    def _measure(self, ipa, meta_entry, version, want_total):
        out = json.loads(bridge.il2cpp_table(str(ipa), meta_entry))
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["metadata_version"], version)
        self.assertEqual(out["literals_total"], want_total)
        table = json.loads(open("/tmp/pb3out/il2cpp-literals.json",
                                encoding="utf-8").read())
        manifest = json.loads(open(
            "/tmp/pb3out/il2cpp-literals.manifest.json",
            encoding="utf-8").read())
        data, _label = il2cpp_sdk.read_metadata_bytes(
            str(ipa), meta_entry)
        suffix = " — đã dịch dài hơn"
        patches = {}
        for key, val in table.items():
            idx = manifest["keys"][key]["literal_index"]
            patches[idx] = val + suffix
        # Vá tại chỗ: bản dịch nào cũng dài hơn gốc -> 0 key.
        _p, rep_in = il2cpp_sdk.patch_literals(data, patches)
        self.assertEqual(rep_in["patched"], [])
        self.assertEqual(len(rep_in["skipped"]), len(patches))
        # Re-layout: vá được hết (trừ key lỗi khác — phải là 0).
        new, rep_re = il2cpp_sdk.patch_literals(
            data, patches, relayout=True)
        self.assertEqual(rep_re["mode"], "relayout")
        self.assertTrue(rep_re["verified"])
        self.assertEqual(len(rep_re["patched"]), len(patches),
                         rep_re["skipped"][:3])
        self.assertEqual(rep_re["skipped"], [])
        self.assertEqual(rep_re["size_delta"],
                         len(new) - len(data))
        # Đọc lại toàn bộ literal: đã vá đúng, còn lại giống hệt
        # (so theo byte hiệu dụng — gồm whitespace gốc 2 đầu).
        before = _literals_of(data)
        after = _literals_of(new)
        self.assertEqual(set(after), set(before))
        eff = il2cpp_sdk._effective_patches(before, patches)
        for idx, text in before.items():
            want = eff[idx] if idx in patches else text
            self.assertEqual(after[idx], want)
        # Section sau blob giống hệt từng byte (chỉ dời chỗ).
        old_secs = il2cpp_sdk._read_sections(
            data, il2cpp_sdk.read_header(io.BytesIO(data)))
        new_secs = il2cpp_sdk._read_sections(
            new, il2cpp_sdk.read_header(io.BytesIO(new)))
        self.assertEqual(new[old_secs[2]["offset"]
                             + rep_re["size_delta"]:],
                         data[old_secs[2]["offset"]:])
        self.assertEqual(len(new_secs), len(old_secs))
        return {"table_keys": len(table),
                "patched_inplace": len(rep_in["patched"]),
                "patched_relayout": len(rep_re["patched"]),
                "size_delta": rep_re["size_delta"],
                "old_size": len(data), "new_size": len(new)}

    @slow
    @unittest.skipUnless(TOM_IPA.exists(), "thiếu IPA Tom thật")
    def test_tom_v31_relayout_all_table_keys(self):
        res = self._measure(TOM_IPA, TOM_META, 31, 12090)
        self.assertEqual(res["table_keys"], 2904)
        self.assertEqual(res["patched_inplace"], 0)
        self.assertEqual(res["patched_relayout"], 2904)
        print("\nTom v31 relayout: table=%d inplace=%d relayout=%d "
              "delta=%+d old=%d new=%d" % (
                  res["table_keys"], res["patched_inplace"],
                  res["patched_relayout"], res["size_delta"],
                  res["old_size"], res["new_size"]))

    @slow
    @unittest.skipUnless(NINEKINGS_IPA.exists(), "thiếu IPA 9K thật")
    def test_9k_v39_relayout_all_table_keys(self):
        res = self._measure(NINEKINGS_IPA, NINEKINGS_META, 39, 28286)
        self.assertGreater(res["table_keys"], 0)
        self.assertEqual(res["patched_inplace"], 0)
        self.assertEqual(res["patched_relayout"], res["table_keys"])
        print("\n9K v39 relayout: table=%d inplace=%d relayout=%d "
              "delta=%+d old=%d new=%d" % (
                  res["table_keys"], res["patched_inplace"],
                  res["patched_relayout"], res["size_delta"],
                  res["old_size"], res["new_size"]))


if __name__ == "__main__":
    unittest.main()
