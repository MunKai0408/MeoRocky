"""Test tốc độ trích xuất (speed-extract, 2026-11-18).

Các tối ưu phải giữ output byte-identical — test này khóa điều đó:
- export_key_value_json streaming == dumps cũ (byte-identical).
- verify_export_bytes đọc trực tiếp trên bytes (không decode trước).
- make_entries dùng chung 1 SourceRef/file (notes vẫn riêng từng entry).
- _probe_bytes của json_text đếm ĐÚNG BẰNG _parse_bytes đầy đủ (entries /
  warnings / conflicts) trên mọi hình dạng, chỉ rẻ hơn khi object phẳng.
- _source_hash lười: chế độ web mặc định (merge_one_file=True,
  resume=False) không băm file nguồn; resume hoặc không gộp thì vẫn băm.
- _Reader.unpack dùng struct Struct biên dịch sẵn (ngữ nghĩa giữ nguyên).
- 9K thật: en.json 2252 key, md5 khớp baseline (test @slow, skip khi
  thiếu IPA trong ~/workspace/test-ipas/).
"""
import hashlib
import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from core.app import bootstrap
from core.errors import PB3Error
from core.limits import Limits
from plugins.json_text.plugin import JsonPlugin
from sdk import exporters as exp
from sdk import extraction as ext
from sdk import formats as fmt
from sdk import unity as unity_sdk
from sdk.api import PluginContext
from sdk.types import Candidate
from sdk.vfs import MemoryVFS
from services import language_tool
from tests.helpers import TmpCase, copy_project, register_tmp_dir, unregister_tmp_dir
from tests.helpers import pytest  # noqa: F401 - marker needs_fixture (stub trên iSH)
from tests.tiering import slow

GAME1 = "tests/fixtures/extract/game1.zip"


def _nine_kings_ipa():
    env = os.environ.get("NINEKINGS_IPA")
    if env:
        return Path(env)
    return Path.home() / "workspace" / "test-ipas" / "9kings-v1.0.6.ipa"


class TestExportStreaming(unittest.TestCase):
    def test_export_key_value_json_streaming_matches_dumps(self):
        cases = [
            {},
            {"a": "x"},
            {"a": "héllo {0} $VAR <b>", "b": "x" * 5000,
             "c": "dòng\nmới\t\"trích\" \\", "": "empty-key",
             "emoji": "🎮 việt"},
            {("k%05d" % i): ("v %d {0}" % i) for i in range(2000)},
        ]
        for kv in cases:
            old = (json.dumps(kv, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            self.assertEqual(exp.export_key_value_json(kv), old)

    def test_verify_export_bytes_ok_and_tampered(self):
        kv = {"a": "x {0}", "b": "y"}
        data = exp.export_key_value_json(kv)
        self.assertTrue(exp.verify_export_bytes(data, kv)["ok"])
        self.assertFalse(exp.verify_export_bytes(data, {"a": "x {1}", "b": "y"})["ok"])
        self.assertFalse(exp.verify_export_bytes(data, {"a": "x {0}"})["ok"])

    def test_verify_export_bytes_bad_utf8_and_bad_json(self):
        kv = {"a": "x"}
        with self.assertRaises(PB3Error) as cm:
            exp.verify_export_bytes(b"\xff\xfe{bad", kv)
        self.assertEqual(cm.exception.code, "E_CORRUPT")
        with self.assertRaises(PB3Error) as cm:
            exp.verify_export_bytes(b"{not json", kv)
        self.assertEqual(cm.exception.code, "E_CORRUPT")


class TestMakeEntriesSharedRef(unittest.TestCase):
    def test_shared_source_ref_and_independent_notes(self):
        pairs = [("k1", "v1", "ok", []), ("k2", "v2", "ok", None),
                 ("k1", "v3", "ok", ["ghi chú"])]
        entries, conflicts = fmt.make_entries(pairs, "p.json", 123, "utf-8", "en")
        self.assertEqual([e.key for e in entries], ["k1", "k2", "k1"])
        self.assertIs(entries[0].source, entries[1].source)
        self.assertIs(entries[0].source, entries[2].source)
        self.assertIsNot(entries[0].notes, entries[1].notes)
        entries[0].notes.append("riêng")
        self.assertEqual(entries[1].notes, [])
        self.assertEqual(entries[2].status, "conflict")
        self.assertEqual(conflicts, [{"key": "k1", "locale": "en"}])
        self.assertEqual(entries[2].notes, ["ghi chú"])


class TestJsonLightProbe(TmpCase):
    CASES = {
        "flat": b'{"a": "x", "b": "y", "c": 3, "d": true, "e": null}',
        "flat-dup": b'{"a": "1", "a": "2", "b": "z"}',
        "empty": b'{}',
        "nested": b'{"a": {"b": "c"}, "d": "e"}',
        "list-str": b'["a", "b"]',
        "list-dict": b'[{"key": "k1", "value": "v1"}, {"key": "k2", "value": "v2"}]',
        "list-dict-bad": b'[{"key": "k1"}, {"value": "v2"}]',
        "list-mixed": b'[{"key": "k1", "value": {"x": 1}}]',
        "bad-json": b'{not json',
        "scalar": b'42',
        "empty-list": b'[]',
        "mixed-list": b'{"a": [1, "x"]}',
    }

    def _probe_full(self, plug, data):
        try:
            r = plug._parse_bytes(data, "a.json", None)
            return (len(r["entries"]), len(r.get("warnings", [])),
                    len(r.get("conflicts", [])))
        except PB3Error as exc:
            return ("ERR", exc.code)

    def test_probe_light_counts_match_full_parse(self):
        plug = JsonPlugin()
        for name, data in self.CASES.items():
            full = self._probe_full(plug, data)
            try:
                lc = plug._probe_bytes(data, "a.json", None)
                light = (lc["entries"], lc["warnings"], lc["conflicts"])
            except PB3Error as exc:
                light = ("ERR", exc.code)
            self.assertEqual(light, full, "probe lệch ở case %s" % name)

    def test_probe_ok_flag_matches_full_parse(self):
        plug = JsonPlugin()
        for name, data in self.CASES.items():
            vfs = MemoryVFS({"a.json": data})
            ctx = PluginContext(vfs, Limits(), None, None, str(self.tmp),
                                True, None, None, "json_text").for_plugin(plug.manifest)
            got = plug.probe(ctx, Candidate("a.json", plug.id, 0.9, []))
            full = self._probe_full(plug, data)
            self.assertEqual(got["ok"], full[0] != "ERR", "probe ok lệch ở %s" % name)
            if got["ok"]:
                self.assertEqual(got["entries"], full[0])


class TestLazySourceHash(TmpCase):
    _shared_proj = None
    _shared_td = None
    # run_tests.py chạy mỗi test trong 1 thread riêng; 2 test dưới cùng
    # mock.patch.object(ext, "_source_hash") nên phải khóa chung kẻo
    # patch/restore đua nhau làm test kia lỗi (đã thấy: lỗi luân phiên).
    _patch_lock = threading.Lock()

    @classmethod
    def setUpClass(cls):
        cls._shared_td = tempfile.mkdtemp(prefix="pb3-speed-extract-")
        register_tmp_dir(cls._shared_td)
        cls._shared_proj = copy_project(Path(cls._shared_td))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._shared_td, ignore_errors=True)
        unregister_tmp_dir(cls._shared_td)

    def setUp(self):
        super().setUp()
        self.app = bootstrap(self._shared_proj)
        self.addCleanup(self.app.close)
        self.reg = language_tool._registry(self.app)
        self.out = self.tmp / "out"

    def run_ext(self, **kw):
        kw.setdefault("registry", self.reg)
        kw.setdefault("tempdir", str(self.app.paths.temp))
        kw.setdefault("root", str(self._shared_proj))
        kw.setdefault("out_dir", str(self.out))
        return ext.run_extraction(ext.ExtractionRequest(
            source=str(self._shared_proj / GAME1), **kw))

    def test_source_hash_skipped_in_merge_mode(self):
        with type(self)._patch_lock:
            calls = []
            real = ext._source_hash

            def counting(*a, **kw):
                calls.append(1)
                return real(*a, **kw)

            with mock.patch.object(ext, "_source_hash", side_effect=counting):
                r = self.run_ext(locales={"en", "zh"}, merge_one_file=True)
            self.assertTrue(r.ok)
            self.assertEqual(calls, [], "merge mode không dùng checkpoint nên không được băm nguồn")

    def test_source_hash_kept_without_merge_and_with_resume(self):
        with type(self)._patch_lock:
            calls = []
            real = ext._source_hash

            def counting(*a, **kw):
                calls.append(1)
                return real(*a, **kw)

            with mock.patch.object(ext, "_source_hash", side_effect=counting):
                r = self.run_ext()
            self.assertTrue(r.ok)
            self.assertGreaterEqual(len(calls), 1, "không gộp thì checkpoint cần hash nguồn")
            calls.clear()
            with mock.patch.object(ext, "_source_hash", side_effect=counting):
                r = self.run_ext(locales={"en", "zh"}, merge_one_file=True, resume=True)
            self.assertTrue(r.ok)
            self.assertGreaterEqual(len(calls), 1, "resume thì checkpoint cần hash nguồn")


class TestReaderStructCache(unittest.TestCase):
    def test_struct_cache_same_object_and_size(self):
        import struct
        a = unity_sdk._reader_struct("<", "i")
        b = unity_sdk._reader_struct("<", "i")
        self.assertIs(a, b)
        self.assertEqual(a.size, struct.calcsize("i"))
        self.assertIsNot(a, unity_sdk._reader_struct(">", "i"))

    def test_reader_unpack_both_endians_and_overrun(self):
        r = unity_sdk._Reader(b"\x01\x00\x00\x00\x02\x00", "<", where="t")
        self.assertEqual(r.i32(), 1)
        self.assertEqual(r.u16(), 2)
        r2 = unity_sdk._Reader(b"\x00\x00\x00\x01", ">", where="t")
        self.assertEqual(r2.i32(), 1)
        with self.assertRaises(PB3Error) as cm:
            r2.i32()
        self.assertEqual(cm.exception.code, "E_CORRUPT")


class TestNineKingsUnchanged(TmpCase):
    _shared_proj = None
    _shared_td = None

    @classmethod
    def setUpClass(cls):
        cls._shared_td = tempfile.mkdtemp(prefix="pb3-speed-extract-9k-")
        register_tmp_dir(cls._shared_td)
        cls._shared_proj = copy_project(Path(cls._shared_td))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._shared_td, ignore_errors=True)
        unregister_tmp_dir(cls._shared_td)

    def setUp(self):
        super().setUp()
        self.app = bootstrap(self._shared_proj)
        self.addCleanup(self.app.close)
        self.reg = language_tool._registry(self.app)
        self.out = self.tmp / "out"

    @slow
    def test_9k_en_json_keys_and_md5_unchanged(self):
        # [ĐÃ ĐO THẬT] baseline main 4ba72fa: en.json 2252 key,
        # md5 4c22c9ebcdd04cc21c7f3f4a4952e955 — mọi tối ưu tốc độ phải
        # giữ nguyên output này.
        ipa = _nine_kings_ipa()
        if not ipa.exists():
            raise unittest.SkipTest("thiếu IPA 9K: %s" % ipa)
        r = ext.run_extraction(ext.ExtractionRequest(
            source=str(ipa), registry=self.reg,
            tempdir=str(self.app.paths.temp), root=str(self._shared_proj),
            out_dir=str(self.out), locales={"en", "zh"}, merge_one_file=True))
        self.assertTrue(r.ok, "lỗi trích xuất: %s" % (r.errors[:2],))
        data = (self.out / "en.json").read_bytes()
        self.assertEqual(len(json.loads(data)), 2252)
        self.assertEqual(hashlib.md5(data).hexdigest(),
                         "4c22c9ebcdd04cc21c7f3f4a4952e955")


if __name__ == "__main__":
    unittest.main()
