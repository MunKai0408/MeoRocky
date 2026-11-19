"""Test cầu nối LZ4 JS — lz4js trên web (mục C1, tốc độ).

`UnityBackend.decompress` cho block LZ4/LZ4HC (mã 2/3) thử theo thứ tự:
binding `lz4` (chế độ AUTO/LIBRARY, khi có) -> cầu nối JS
`pb3_lz4_decompress` (worker.js công bố lz4js, mọi mode kể cả NATIVE)
-> `lz4pure` (Python thuần). Ở CPython test dùng module `js` giả để
kiểm chứng đường gọi Python -> JS (theo đúng mẫu test Zstd trong
test_unity.py::TestBundleV9Zstd); bản thân lz4js được kiểm chứng thật
bằng Node trong exp/verify_lz4_js.mjs (block thật 9K + block 128 KiB,
byte-identical với binding, ~370 MB/s so với lz4pure ~7,9 MB/s).
"""
import base64
import sys
import types
import unittest
from unittest import mock

from core.errors import PB3Error
from sdk import unity as u
from tests.helpers import pytest  # noqa: F401 - marker needs_fixture (stub trên iSH)

# Block LZ4 thật, sinh bằng `lz4.block.compress` (store_size=False) từ
# _TINY_PLAIN; dùng để thử cầu nối JS mà không cần encoder lúc chạy test.
_TINY_PLAIN = b"Xin chao BearTea - cau noi LZ4 thu nghiem 12345"
_TINY_BLOCK_B64 = ("8CBYaW4gY2hhbyBCZWFyVGVhIC0gY2F1IG5vaSBMWjQgdGh1IG5naGllbSAxMjM0NQ==")


def nat():
    return u.UnityBackend("NATIVE")


def auto_no_binding():
    be = u.UnityBackend("AUTO")
    be.has_lz4_binding = False
    return be


class TestLz4JsBridge(unittest.TestCase):
    """Cầu nối JS lz4js cho block LZ4/LZ4HC (mã 2/3)."""

    @staticmethod
    def _fake_js(decode):
        fake = types.ModuleType("js")
        fake.pb3_lz4_decompress = decode
        return fake

    @staticmethod
    def _real_decode(data, size):
        # Bộ giải đứng chỗ lz4js trong test CPython: binding lz4 (đã
        # chứng minh byte-identical với lz4js bằng Node ở verify_lz4_js).
        import lz4.block
        return lz4.block.decompress(bytes(data), size)

    def test_native_uses_js_bridge_when_present(self):
        block = base64.b64decode(_TINY_BLOCK_B64)
        calls = []

        def fake_decode(data, size):
            calls.append((bytes(data), size))
            return bytes(_TINY_PLAIN)

        with mock.patch.dict(sys.modules, {"js": self._fake_js(fake_decode)}):
            out = nat().decompress(block, len(_TINY_PLAIN), 2)
        self.assertEqual(out, _TINY_PLAIN)
        self.assertEqual(calls, [(block, len(_TINY_PLAIN))])

    def test_auto_without_binding_uses_js_bridge(self):
        block = base64.b64decode(_TINY_BLOCK_B64)
        calls = []

        def fake_decode(data, size):
            calls.append((bytes(data), size))
            return bytes(_TINY_PLAIN)

        with mock.patch.dict(sys.modules, {"js": self._fake_js(fake_decode)}):
            out = auto_no_binding().decompress(block, len(_TINY_PLAIN), 3)
        self.assertEqual(out, _TINY_PLAIN)
        self.assertEqual(calls, [(block, len(_TINY_PLAIN))])

    def test_js_absent_falls_back_to_lz4pure(self):
        # CPython: không có module `js` -> lz4pure, kết quả vẫn đúng.
        block = base64.b64decode(_TINY_BLOCK_B64)
        self.assertNotIn("js", sys.modules)
        out = nat().decompress(block, len(_TINY_PLAIN), 2)
        self.assertEqual(out, _TINY_PLAIN)

    def test_js_bridge_null_falls_back_to_lz4pure(self):
        # Web khi lz4js tải lỗi: worker đặt pb3_lz4_decompress = null.
        block = base64.b64decode(_TINY_BLOCK_B64)
        fake = types.ModuleType("js")
        fake.pb3_lz4_decompress = None
        with mock.patch.dict(sys.modules, {"js": fake}):
            out = nat().decompress(block, len(_TINY_PLAIN), 2)
        self.assertEqual(out, _TINY_PLAIN)

    def test_js_decompress_accepts_jsproxy_with_to_py(self):
        # Pyodide trả Uint8Array dưới dạng JsProxy: bytes(...) dùng
        # được nhờ buffer protocol; khi không được thì dùng to_py().
        block = base64.b64decode(_TINY_BLOCK_B64)

        class _FakeJsProxy:
            def __init__(self, data):
                self._data = data

            def to_py(self):
                return self._data

        def fake_decode(data, size):
            return _FakeJsProxy(bytes(_TINY_PLAIN))

        with mock.patch.dict(sys.modules, {"js": self._fake_js(fake_decode)}):
            out = nat().decompress(block, len(_TINY_PLAIN), 2)
        self.assertEqual(out, _TINY_PLAIN)

    def test_js_corrupt_raises_e_corrupt(self):
        block = base64.b64decode(_TINY_BLOCK_B64)

        def fake_decode(data, size):
            raise ValueError("LZ4_CORRUPT: offset khong hop le (0)")

        with mock.patch.dict(sys.modules, {"js": self._fake_js(fake_decode)}):
            with self.assertRaises(PB3Error) as cm:
                nat().decompress(block, len(_TINY_PLAIN), 2)
        self.assertEqual(cm.exception.code, "E_CORRUPT")
        self.assertIn("offset", cm.exception.detail)

    def test_js_size_mismatch_raises(self):
        block = base64.b64decode(_TINY_BLOCK_B64)

        def fake_decode(data, size):
            return b"ngan"

        with mock.patch.dict(sys.modules, {"js": self._fake_js(fake_decode)}):
            with self.assertRaises(PB3Error) as cm:
                nat().decompress(block, len(_TINY_PLAIN), 2)
        self.assertEqual(cm.exception.code, "E_CORRUPT")

    def test_js_broken_once_disables_bridge(self):
        # Lỗi JS không phải input hỏng: cầu nối tắt cho backend này,
        # các block sau rơi thẳng về lz4pure (chỉ gọi JS đúng 1 lần).
        block = base64.b64decode(_TINY_BLOCK_B64)
        calls = []

        def fake_decode(data, size):
            calls.append(1)
            raise RuntimeError("boom")

        be = nat()
        with mock.patch.dict(sys.modules, {"js": self._fake_js(fake_decode)}):
            out1 = be.decompress(block, len(_TINY_PLAIN), 2)
            out2 = be.decompress(block, len(_TINY_PLAIN), 2)
        self.assertEqual(out1, _TINY_PLAIN)
        self.assertEqual(out2, _TINY_PLAIN)
        self.assertEqual(len(calls), 1)
        self.assertTrue(be._lz4_js_broken)

    def test_parse_bundle_native_via_js_bridge(self):
        # Bundle LZ4 dựng trong bộ nhớ: parse_bundle ở chế độ NATIVE
        # đi qua cầu nối JS, payload byte-identical với đường binding.
        entry = ("CAB-0123456789abcdef0123456789abcdef", 4, b"Xin chao Unity!")
        bundle = u.build_bundle([entry], version=7, compression="lz4")
        with mock.patch.dict(sys.modules, {"js": self._fake_js(self._real_decode)}):
            parsed = u.parse_bundle(bundle, nat())
        self.assertEqual(parsed.compression_name, "lz4")
        self.assertEqual(parsed.get_entry(entry[0]), b"Xin chao Unity!")
        ref = u.parse_bundle(bundle, u.UnityBackend("AUTO"))
        self.assertEqual(parsed.payload, ref.payload)

    @pytest.mark.needs_fixture("unity/sample_v8_lz4.unity3d")
    def test_parse_fixture_v8_lz4_via_js_bridge(self):
        from pathlib import Path
        from tests.helpers import ROOT
        path = ROOT / "tests" / "fixtures" / "unity" / "sample_v8_lz4.unity3d"
        data = path.read_bytes()
        with mock.patch.dict(sys.modules, {"js": self._fake_js(self._real_decode)}):
            parsed = u.parse_bundle(data, nat())
        ref = u.parse_bundle(data, u.UnityBackend("AUTO"))
        self.assertEqual(parsed.payload, ref.payload)
        self.assertEqual([e.name for e in parsed.entries],
                         [e.name for e in ref.entries])


if __name__ == "__main__":
    unittest.main()
