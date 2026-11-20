"""Test cau noi Rust/WASM — LZ4, phat hien ngon ngu, doc typetree.

`UnityBackend.decompress` (LZ4/LZ4HC) / `decompress_frame` / `langdetect`
/ `read_typetree_value` thu cau noi JS `pb3_rust_*` (worker.js cong bo
tu wasm) TRUOC duong cu (lz4js/lz4pure/Python) — giong mau test_lz4_js.py.
O CPython test dung module `js` gia de kiem duong goi Python -> JS;
ban than WASM duoc kiem that bang Node o exp/verify_rust_wasm*.mjs
(block that 106 Kusa + frame + 34k chuoi + 1805 object typetree,
byte-identical voi Python).
"""
import base64
import json
import sys
import types
import unittest
from unittest import mock

from core.errors import PB3Error
from sdk import langdetect as ld
from sdk import lz4pure
from sdk import unity as u
from tests.helpers import pytest  # noqa: F401 - marker needs_fixture (stub trên iSH)

_TINY_PLAIN = b"Xin chao BearTea - cau noi LZ4 thu nghiem 12345"
_TINY_BLOCK_B64 = ("8CBYaW4gY2hhbyBCZWFyVGVhIC0gY2F1IG5vaSBMWjQgdGh1IG5naGllbSAxMjM0NQ==")


def nat():
    return u.UnityBackend("NATIVE")


def auto_no_binding():
    be = u.UnityBackend("AUTO")
    be.has_lz4_binding = False
    return be


def _fake_js(**fns):
    fake = types.ModuleType("js")
    for name, fn in fns.items():
        setattr(fake, name, fn)
    return fake


def _real_lz4_decode(data, size):
    import lz4.block
    return lz4.block.decompress(bytes(data), size)


class TestRustLz4Bridge(unittest.TestCase):
    """Cau noi Rust/WASM cho block LZ4/LZ4HC (ma 2/3) + frame."""

    def test_native_uses_rust_bridge_when_present(self):
        block = base64.b64decode(_TINY_BLOCK_B64)
        calls = []

        def fake_decode(data, size):
            calls.append((bytes(data), size))
            return bytes(_TINY_PLAIN)

        with mock.patch.dict(sys.modules, {"js": _fake_js(pb3_rust_lz4_decompress=fake_decode)}):
            out = nat().decompress(block, len(_TINY_PLAIN), 2)
        self.assertEqual(out, _TINY_PLAIN)
        self.assertEqual(calls, [(block, len(_TINY_PLAIN))])

    def test_auto_without_binding_uses_rust_bridge(self):
        block = base64.b64decode(_TINY_BLOCK_B64)
        calls = []

        def fake_decode(data, size):
            calls.append((bytes(data), size))
            return bytes(_TINY_PLAIN)

        with mock.patch.dict(sys.modules, {"js": _fake_js(pb3_rust_lz4_decompress=fake_decode)}):
            out = auto_no_binding().decompress(block, len(_TINY_PLAIN), 3)
        self.assertEqual(out, _TINY_PLAIN)
        self.assertEqual(calls, [(block, len(_TINY_PLAIN))])

    def test_rust_absent_falls_back_to_lz4js_then_lz4pure(self):
        # Khong co module js -> bo qua ca Rust lan lz4js, ve lz4pure.
        block = base64.b64decode(_TINY_BLOCK_B64)
        out = nat().decompress(block, len(_TINY_PLAIN), 2)
        self.assertEqual(out, _TINY_PLAIN)

    def test_rust_corrupt_raises_e_corrupt(self):
        block = base64.b64decode(_TINY_BLOCK_B64)

        def bad_decode(data, size):
            raise Exception("RUST_LZ4_CORRUPT: khối LZ4: offset không hợp lệ (0)")

        with mock.patch.dict(sys.modules, {"js": _fake_js(pb3_rust_lz4_decompress=bad_decode)}):
            with self.assertRaises(PB3Error) as ctx:
                nat().decompress(block, len(_TINY_PLAIN), 2)
        self.assertEqual(ctx.exception.code, "E_CORRUPT")
        self.assertIn("offset không hợp lệ (0)", ctx.exception.detail)

    def test_rust_other_error_marks_broken_and_falls_back(self):
        # Loi JS khac (khong phai corrupt) -> danh dau hong, cac block sau
        # roi thang ve lz4pure (chi goi JS dung 1 lan).
        block = base64.b64decode(_TINY_BLOCK_B64)
        calls = []

        def flaky_decode(data, size):
            calls.append(1)
            raise RuntimeError("wasm chua nap xong")

        be = nat()
        with mock.patch.dict(sys.modules, {"js": _fake_js(pb3_rust_lz4_decompress=flaky_decode)}):
            out = be.decompress(block, len(_TINY_PLAIN), 2)
            out2 = be.decompress(block, len(_TINY_PLAIN), 2)
        self.assertEqual(out, _TINY_PLAIN)
        self.assertEqual(out2, _TINY_PLAIN)
        self.assertEqual(len(calls), 1)

    def test_frame_uses_rust_bridge_when_present(self):
        plain = b"frame test 1234567890 frame test"
        block = lz4pure.compress_block_literals(plain)
        import struct
        fr = bytearray([0x04, 0x22, 0x4D, 0x18, 0x64, 0x40])
        fr.append((lz4pure.xxhash32(bytes(fr[4:6]), 0) >> 8) & 0xFF)
        fr += struct.pack("<I", len(block)) + block + b"\x00\x00\x00\x00"
        fr += struct.pack("<I", lz4pure.xxhash32(plain, 0))
        calls = []

        def fake_frame(data):
            calls.append(bytes(data))
            return bytes(plain)

        with mock.patch.dict(sys.modules, {"js": _fake_js(pb3_rust_lz4_decompress_frame=fake_frame)}):
            out = nat().decompress_frame(bytes(fr))
        self.assertEqual(out, plain)
        self.assertEqual(calls, [bytes(fr)])

    def test_frame_rust_absent_falls_back_to_lz4pure(self):
        plain = b"frame test 1234567890 frame test"
        block = lz4pure.compress_block_literals(plain)
        import struct
        fr = bytearray([0x04, 0x22, 0x4D, 0x18, 0x64, 0x40])
        fr.append((lz4pure.xxhash32(bytes(fr[4:6]), 0) >> 8) & 0xFF)
        fr += struct.pack("<I", len(block)) + block + b"\x00\x00\x00\x00"
        fr += struct.pack("<I", lz4pure.xxhash32(plain, 0))
        out = nat().decompress_frame(bytes(fr))
        self.assertEqual(out, plain)


class TestRustLangdetectBridge(unittest.TestCase):
    """Cau noi Rust/WASM cho detect_language / detect_chinese_variant."""

    def setUp(self):
        ld._RS_BROKEN = False

    def tearDown(self):
        ld._RS_BROKEN = False

    def test_rust_detect_language_used_when_present(self):
        calls = []
        samples = ["すばらしいわ！", "Häufe in einem einzigen Durchlauf 999+ Gold an.",
                   "Save 999+ gold in a single run.", "123 !!!",
                   "Xin chào các bạn, chúc một ngày tốt lành!"]
        expected = [ld.detect_language(s) for s in samples]
        # Gia lap WASM: tra JSON cua ket qua Python (chi kiem duong goi).
        table = {s: json.dumps(list(e), ensure_ascii=False)
                 for s, e in zip(samples, expected)}

        def fake_detect2(text):
            calls.append(text)
            return table[text]

        with mock.patch.dict(sys.modules, {"js": _fake_js(pb3_rust_detect_language=fake_detect2)}):
            for s, want in zip(samples, expected):
                self.assertEqual(ld.detect_language(s), want)
        self.assertEqual(calls, samples)

    def test_rust_detect_language_absent_uses_python(self):
        self.assertEqual(ld.detect_language("すばらしいわ！"), ("ja", "strong"))
        self.assertEqual(ld.detect_language("Save 999+ gold in a single run."),
                         ("en", "weak"))

    def test_rust_broken_falls_back_to_python(self):
        def bad_detect(text):
            raise RuntimeError("wasm loi")

        with mock.patch.dict(sys.modules, {"js": _fake_js(pb3_rust_detect_language=bad_detect)}):
            self.assertEqual(ld.detect_language("すばらしいわ！"), ("ja", "strong"))
            # Lan 2: cau noi da bi danh dau hong, khong goi JS nua.
            self.assertEqual(ld.detect_language("丹青妙手，神来之笔啊！"), ("zh", "strong"))

    def test_rust_chinese_variant(self):
        def fake_variant(text):
            return {"學國會": "Hant", "学国会": "Hans"}.get(text)

        with mock.patch.dict(sys.modules, {"js": _fake_js(pb3_rust_detect_chinese_variant=fake_variant)}):
            self.assertEqual(ld.detect_chinese_variant("學國會"), "Hant")
            self.assertEqual(ld.detect_chinese_variant("学国会"), "Hans")
            self.assertIsNone(ld.detect_chinese_variant("Hello"))


class TestRustTypetreeBridge(unittest.TestCase):
    """Cau noi Rust/WASM cho read_typetree_value."""

    def setUp(self):
        u._TYPETREE_RS_BROKEN = False

    def tearDown(self):
        u._TYPETREE_RS_BROKEN = False

    @staticmethod
    def _fake_typetree(node_json, data, pos, little):
        # Gia lap WASM bang Python: doc bang ban Python roi serialize
        # dung dinh dang JSON cua Rust ({"$bytes": base64} cho bytes).
        node = json.loads(node_json if isinstance(node_json, str) else str(node_json))
        def build(j):
            return u.TypeTreeNode(type="", name=j["name"], byte_size=j["byte_size"],
                                  index=0, level=0, meta_flag=0,
                                  children=[build(c) for c in j["children"]])
        val, end = u._read_typetree_value_py(bytes(data), build(node), int(pos),
                                             "<" if little else ">")
        def conv(v):
            if isinstance(v, bytes):
                return {"$bytes": base64.b64encode(v).decode()}
            if isinstance(v, dict):
                return {k: conv(x) for k, x in v.items()}
            if isinstance(v, list):
                return [conv(x) for x in v]
            return v
        return json.dumps({"value": conv(val), "end": end}, ensure_ascii=False)

    def test_rust_typetree_used_when_present(self):
        # struct { a: i32, name: string } — doc qua cau noi gia.
        def leaf(name, size):
            return u.TypeTreeNode(type="", name=name, byte_size=size,
                                  index=0, level=0, meta_flag=0)
        str_node = u.TypeTreeNode(
            type="", name="m_Name", byte_size=0, index=0, level=0, meta_flag=0,
            children=[u.TypeTreeNode(type="", name="Array", byte_size=0,
                                     index=0, level=0, meta_flag=0,
                                     children=[leaf("common_795", 4), leaf("common_106", 1)])])
        root = u.TypeTreeNode(type="", name="root", byte_size=0,
                              index=0, level=0, meta_flag=0,
                              children=[leaf("a", 4), str_node])
        import struct as _st
        data = _st.pack("<i", 42) + _st.pack("<i", 5) + b"Hello" + b"\x00\x00\x00"
        calls = []

        def fake_fn(node_json, data_b, pos, little):
            calls.append(1)
            return self._fake_typetree(node_json, data_b, pos, little)

        with mock.patch.dict(sys.modules, {"js": _fake_js(pb3_rust_typetree_value=fake_fn)}):
            val, end = u.read_typetree_value(data, root, 0, "<")
        self.assertEqual(val["a"], 42)
        self.assertEqual(val["m_Name"], "Hello")
        self.assertEqual(end, len(data))
        self.assertEqual(len(calls), 1)

    def test_rust_typetree_absent_uses_python(self):
        def leaf(name, size):
            return u.TypeTreeNode(type="", name=name, byte_size=size,
                                  index=0, level=0, meta_flag=0)
        root = u.TypeTreeNode(type="", name="root", byte_size=0,
                              index=0, level=0, meta_flag=0,
                              children=[leaf("a", 4)])
        import struct as _st
        val, end = u.read_typetree_value(_st.pack("<i", 7), root, 0, "<")
        self.assertEqual((val, end), ({"a": 7}, 4))

    def test_rust_typetree_corrupt_raises_e_corrupt(self):
        def leaf(name, size):
            return u.TypeTreeNode(type="", name=name, byte_size=size,
                                  index=0, level=0, meta_flag=0)
        root = u.TypeTreeNode(type="", name="root", byte_size=0,
                              index=0, level=0, meta_flag=0,
                              children=[leaf("a", 4)])

        def bad_fn(node_json, data_b, pos, little):
            raise Exception("RUST_TYPETREE_CORRUPT: đọc vượt quá dữ liệu (4 byte tại 0)")

        with mock.patch.dict(sys.modules, {"js": _fake_js(pb3_rust_typetree_value=bad_fn)}):
            with self.assertRaises(PB3Error) as ctx:
                u.read_typetree_value(b"\x01\x02", root, 0, "<")
        self.assertEqual(ctx.exception.code, "E_CORRUPT")
        self.assertIn("đọc vượt quá dữ liệu", ctx.exception.detail)


if __name__ == "__main__":
    unittest.main()
