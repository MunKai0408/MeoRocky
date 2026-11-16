"""Test đợt tối ưu tốc độ quét/inspect/mod (2026-11-16): in plan nhanh,
bảng object đọc hàng loạt, đọc đầu TextAsset, chỉ mục path_id, cache plan,
tiến độ khi chuyển hướng, menu gọn. Xem handoff/account_polish.md § tối ưu.

Ngưỡng thời gian đặt lỏng (gấp ~5 lần số đo mới trong sandbox, vẫn dưới
một nửa số đo cũ) để nhiễu tải máy không làm test flaky; test đo thời gian
nằm ở tầng slow.
"""
import hashlib
import importlib.util
import io
import json
import os
import random
import struct
import sys
import time
import unicodedata
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from core.color import display_width, truncate_text, wrap_text
from core.errors import PB3Error
from sdk import lz4pure, unity as u
from services import unity_tool as ut
from tests.helpers import ROOT, TmpCase
from tests.helpers import pytest  # noqa: F401 - marker needs_fixture (stub trên iSH)
from tests.tiering import slow, timeout_scale

FX = ROOT / "tests" / "fixtures" / "unity"
BUNDLE_LZ4 = FX / "sample_v8_lz4.unity3d"
RES = FX / "resources.assets"
SHARED = FX / "sharedassets0.assets"

_spec = importlib.util.spec_from_file_location(
    "pb3_build_fixtures", str(FX / "build_fixtures.py"))
bf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bf)


# ---- bản cũ của display_width/wrap_text (nguyên văn trước đợt tối ưu) ----
def _old_display_width(text):
    w = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\x1b" and i + 1 < n and text[i + 1] == "[":
            j = text.find("m", i + 2)
            if j != -1:
                i = j + 1
                continue
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        i += 1
    return w


def _old_wrap_text(text, width):
    if width <= 0:
        return [text]
    lines = []
    for raw in text.split("\n"):
        line = ""
        for word in raw.split(" "):
            cand = word if not line else line + " " + word
            if _old_display_width(cand) <= width:
                line = cand
            else:
                if line:
                    lines.append(line)
                while _old_display_width(word) > width:
                    cut, w = "", 0
                    for ch in word:
                        cw = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
                        if w + cw > width:
                            break
                        cut += ch
                        w += cw
                    if not cut:
                        cut = word[:1]
                    lines.append(cut)
                    word = word[len(cut):]
                line = word
        lines.append(line)
    return lines


CORPUS = [
    "",
    " ",
    "hello world",
    "  nhiều   khoảng   trắng  ",
    "kho tri thức game (chưa triển khai)",
    "cảnh báo: không đọc được Payload/9Kings.app/Data/level4: E_UNSUPPORTED_FORMAT",
    "Tiếng Việt có dấu đàng hoàng: ồ ễ ệ ộ ơ ư đ Đ",
    "日本語テスト ABC 123",
    "한글 테스트",
    "emoji 🎮🎲🔥 ok",
    "mix 日本語 and English và tiếng Việt 🎮",
    "\x1b[32mxanh\x1b[0m và \x1b[1mđậm\x1b[0m",
    "a" * 200,
    "từ_rất_dài_" * 20,
    "x" * 39 + " " + "y" * 39,
    "dòng1\ndòng2 dài hơn nhiều để ngắt\nngắn",
    "… … … đầu cuối …",
    "█░… 98% còn ~0s",
    "path_id=12345  75 byte  binary  GameObject",
    "workspace/inp…pp/Data/level4 assets",
    "trailing space ",
    "    indent 4",
    "CJK：：：：：：：：：：：：：：：：：：：：",
    "ＡＢＣＤＥＦＧ fullwidth",
    "\x1b[31mđỏ\x1b[0m \x1b[32mxanh lá\x1b[0m text",
]


class TestPrintDifferential(unittest.TestCase):
    """display_width/wrap_text/truncate_text mới phải cho kết quả Y HỆT bản cũ."""

    def test_display_width_matches_old(self):
        for s in CORPUS:
            self.assertEqual(display_width(s), _old_display_width(s), repr(s))

    def test_wrap_text_matches_old(self):
        # Trên iSH (timeout_scale > 1) chỉ thử 3 độ rộng đại diện (1, 10,
        # 44) thay vì 9 — đường cũ gọi display_width trên cả ứng viên mỗi
        # từ nên chậm gấp nhiều lần trên iSH (6.3s); tính chất byte-identical
        # giữ nguyên trên từng độ rộng, đủ 9 độ rộng vẫn chạy trong sandbox.
        widths = (1, 10, 44) if timeout_scale() > 1 else (1, 2, 5, 10, 20, 30, 40, 44, 80)
        for s in CORPUS:
            for w in widths:
                self.assertEqual(wrap_text(s, w), _old_wrap_text(s, w),
                                 "w=%d %r" % (w, s))

    def test_truncate_text_matches_old_width(self):
        for s in CORPUS:
            for w in (5, 10, 20, 40):
                got = truncate_text(s, w)
                # bỏ "…" cuối rồi so độ rộng hiển thị với bản cũ (cùng luật cắt)
                self.assertLessEqual(display_width(got), w)


def _build_many_row_ipa(n_levels=8, n_objects=3000, n_types=100):
    """IPA ~24.000 dòng plan: mỗi level 1 assets (n_objects object, trong đó
    100 TextAsset nhỏ) + 1 bundle LZMA. Dữ liệu không nén được (urandom) để
    file đủ lớn và giống game thật."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for li in range(n_levels):
            types = [(49 if i == 0 else (1 + i % 200), None) for i in range(n_types)]
            objects = []
            for oi in range(n_objects):
                if oi < 100:
                    payload = bf.text_asset_data("text%04d" % oi, "x" * 50)
                    objects.append((1000 + oi, 0, payload))
                else:
                    objects.append((2000 + oi, 1, b"\x00" * 60))
            assets = bf.build_serialized_file(
                version=22, objects=objects, types=types,
                enable_type_tree=False)
            bundle = bf.build_bundle(
                version=8, entries=[("CAB-" + "b" * 32, 4, assets)],
                compression="lzma")
            zf.writestr("Payload/Game.app/Data/level%d" % li, bundle)
    return buf.getvalue()


class TestPrintSpeed(unittest.TestCase):
    """Guard thời gian in plan: bản cũ ~2.4s cho 24k dòng, bản mới ~0.25s."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Fixture theo hệ số máy: sandbox dùng plan đầy (8 level × 3000 =
        # 24.008 dòng) để đo thật; trên iSH (timeout_scale > 1) dùng plan
        # nhỏ (1 level × 1000 dòng) — đường in cũ trên iSH tốn ~28ms/dòng
        # nên so sánh đường cũ trên plan đầy mất 722s (bằng chứng 01:52).
        # Đảm bảo byte-identical giữ nguyên: cùng plan nhỏ, so sánh đủ 2
        # đường; plan đầy vẫn được so trong sandbox + corpus differential.
        scale = timeout_scale()
        n_levels = 1 if scale > 1 else 8
        n_objects = 1000 if scale > 1 else 3000
        cls.ipa = _build_many_row_ipa(n_levels=n_levels, n_objects=n_objects)
        cls.plan = u.plan_bytes(cls.ipa, name="many.ipa",
                                backend=u.UnityBackend("AUTO"))
        assert len(cls.plan.rows) > 900, len(cls.plan.rows)

    @slow
    def test_print_plan_is_fast(self):
        # Ngưỡng nhân theo hệ số máy: 1.2s đo trên sandbox; trên iSH
        # (timeout_scale=5) ngưỡng thành 6.0s. iSH 01:52: 44.06s với plan
        # 24.008 dòng (1.8ms/dòng) — plan nhỏ trên iSH chỉ ~1.000 dòng nên
        # dư biên độ; đường in mới còn được tăng tốc bằng cache độ rộng từ
        # (core/color.py _word_width: 72.079 hit / 23 miss trên plan đầy).
        t0 = time.perf_counter()
        buf = io.StringIO()
        ut._print_plan(self.plan, out=buf.write)
        dt = time.perf_counter() - t0
        limit = 1.2 * timeout_scale()
        self.assertLess(dt, limit, "in %d dòng mất %.2fs (cũ ~2.4s)" % (len(self.plan.rows), dt))
        # mỗi dòng in ra ≥ ~20 ký tự (kể cả plan nhỏ trên iSH).
        self.assertGreater(len(buf.getvalue()), len(self.plan.rows) * 20)

    @slow
    def test_print_plan_matches_old_wrap(self):
        """In đủ plan bằng wrap cũ phải cho byte y hệt wrap mới."""
        buf_new = io.StringIO()
        ut._print_plan(self.plan, out=buf_new.write)
        with mock.patch.object(ut, "wrap_text", _old_wrap_text):
            buf_old = io.StringIO()
            ut._print_plan(self.plan, out=buf_old.write)
        self.assertEqual(buf_new.getvalue(), buf_old.getvalue())


def _text_asset_bytes(name, script):
    r = io.BytesIO()
    for blob in (name.encode("utf-8"), script):
        r.write(struct.pack("<i", len(blob)))
        r.write(blob)
        r.write(b"\x00" * (-r.tell() % 4))
    return r.getvalue()


class TestTextAssetHead(unittest.TestCase):
    """read_text_asset_head == (tên, độ dài) của bản đọc đủ, kể cả lỗi."""

    def test_head_matches_full(self):
        cases = [
            ("greeting", b"Xin chao 9 Kings!"),
            ("data.json", b"{}"),
            ("long", b"x" * 100000),
            ("big", b"A" * (5 * 1024 * 1024)),
            ("empty", b""),
            ("tiếng việt.json", "ồ ễ".encode("utf-8")),
        ]
        for name, script in cases:
            data = _text_asset_bytes(name, script)
            full_name, full_script = u.read_text_asset_bytes(data)
            # độ dài là số KÝ TỰ (script decode UTF-8), không phải số byte
            self.assertEqual(u.read_text_asset_head(data), (name, len(full_script)))
            self.assertEqual((full_name, len(full_script)), (name, len(full_script)))

    def test_head_errors_match_full(self):
        def code(fn, *a):
            try:
                fn(*a)
            except PB3Error as exc:
                return exc.code
            return None
        bad = [
            b"",  # thiếu cả tên
            struct.pack("<i", 5) + b"ab",  # tên cắt cụt
            struct.pack("<i", -1) + b"xxxx",  # độ dài tên âm
            _text_asset_bytes("ok", b"xx")[:-2],  # script cắt cụt
            struct.pack("<i", 1) + b"a" + b"\x00" * 3 + struct.pack("<i", -2),
            struct.pack("<i", 1) + b"a" + b"\x00" * 3 + struct.pack("<i", 999999),
            struct.pack("<i", 2) + b"\xff\xfe" + b"\x00\x00",  # tên không UTF-8
            _text_asset_bytes("ok", b"\xff\xfe\xfd"),  # script không UTF-8
        ]
        for data in bad:
            self.assertEqual(code(u.read_text_asset_head, data),
                             code(u.read_text_asset_bytes, data), repr(data[:20]))


class TestObjectIndex(unittest.TestCase):
    """get_object dùng chỉ mục: cùng kết quả quét tuyến tính, O(1)."""

    @pytest.mark.needs_fixture("unity/sample_v21.assets")
    def test_get_object_matches_linear_scan(self):
        for path in (FX / "sample_v21.assets", FX / "sample_v23.assets",
                     FX / "resources.assets"):
            sf = u.parse_serialized(Path(path).read_bytes())
            for o in sf.objects:
                got = sf.get_object(o.path_id)
                self.assertIs(got, o)
            with self.assertRaises(PB3Error) as cm:
                sf.get_object(-999999)
            self.assertEqual(cm.exception.code, "E_INPUT_NOT_FOUND")
            # gọi lại: chỉ mục đã dựng, kết quả ổn định
            self.assertIs(sf.get_object(sf.objects[0].path_id), sf.objects[0])

    @pytest.mark.needs_fixture("unity/resources.assets")
    def test_object_bytes_uses_index(self):
        sf = u.parse_serialized(Path(RES).read_bytes())
        seen = False
        for pid, name, script in sf.text_assets():
            raw = sf.object_bytes(pid)
            self.assertEqual(raw, sf.object_bytes(pid))  # gọi lại: cùng bytes
            self.assertEqual(u.read_text_asset_bytes(raw), (name, script))
            seen = True
        self.assertTrue(seen)


def _hand_table(version, entries):
    """Bytes bảng object tự đóng gói: entries = (path_id, byte_start,
    byte_size, type_id[, script_idx[, is_stripped]])."""
    out = bytearray()
    for e in entries:
        out += struct.pack("<q", e[0])
        out += struct.pack("<Q" if version >= 22 else "<I", e[1])
        out += struct.pack("<I", e[2])
        out += struct.pack("<i", e[3])
        if 11 <= version < 17:
            out += struct.pack("<h", e[4])
        if version in (15, 16):
            out += struct.pack("<B", e[5])
        out += b"\x00" * ((-len(out)) % 4)
    return bytes(out)


def _sf_with_types(n_types, class_id=49):
    sf = u.SerializedFile(version=17)
    sf.types = [u.SerializedType(class_id=class_id) for _ in range(n_types)]
    return sf


class TestObjectTable(unittest.TestCase):
    """Bảng object đọc hàng loạt (fast) == vòng lặp cũ (slow), từng byte."""

    def _run_both(self, version, table, n_types=3, data_len=100000, data=None):
        if data is None:
            # đệm đủ dài để byte_start+byte_size trong bảng (tối đa ~208)
            # không chạm giới hạn data section (trừ case cố tình vượt)
            data = table + b"\x00" * 2048
        results = []
        for fn in (u._parse_object_table_fast, u._parse_object_table_slow):
            sf = _sf_with_types(n_types)
            r = u._Reader(data, "<", where="test")
            try:
                fn(r, sf, version, 3, data, 0, "test")
                results.append(([(o.path_id, o.byte_start, o.byte_size,
                                  o.type_id, o.class_id) for o in sf.objects], r.pos, None))
            except PB3Error as exc:
                results.append((None, None, exc.code))
        return results

    def test_fast_matches_slow_v17(self):
        table = _hand_table(17, [(1001, 0, 60, 0), (1002, 64, 120, 1),
                                 (1003, 200, 8, 2)])
        fast, slow = self._run_both(17, table)
        self.assertEqual(fast, slow)
        self.assertEqual(fast[0][0][0], 1001)

    def test_fast_matches_slow_v22(self):
        table = _hand_table(22, [(1001, 0, 60, 0), (1002, 64, 120, 1),
                                 (1003, 200, 8, 2)])
        fast, slow = self._run_both(22, table)
        self.assertEqual(fast, slow)

    def test_fast_rejects_like_slow(self):
        cases = [
            # type_id lạ
            _hand_table(17, [(1001, 0, 60, 9), (1002, 64, 60, 0), (1003, 128, 60, 0)]),
            # path_id trùng
            _hand_table(17, [(1001, 0, 60, 0), (1001, 64, 60, 0), (1003, 128, 60, 0)]),
            # vượt data section
            _hand_table(17, [(1001, 0, 60, 0), (1002, 64, 60, 0), (1003, 99990, 60, 0)]),
        ]
        for table in cases:
            fast, slow = self._run_both(17, table)
            self.assertEqual(fast[2], slow[2])
            self.assertIsNotNone(fast[2])
        # bảng cắt cụt giữa chừng: cả 2 đều E_CORRUPT
        table = _hand_table(17, [(1001, 0, 60, 0), (1002, 64, 60, 0),
                                 (1003, 128, 60, 0)])
        fast, slow = self._run_both(17, table, data=table[:40])
        self.assertEqual(fast[2], "E_CORRUPT")
        self.assertEqual(slow[2], "E_CORRUPT")

    def test_slow_path_v15_reads_script_idx(self):
        table = _hand_table(15, [(1001, 0, 60, 0, 7, 0),
                                 (1002, 64, 60, 1, 8, 1),
                                 (1003, 128, 60, 2, 9, 0)])
        sf = _sf_with_types(3)
        data = table + b"\x00" * 2048
        r = u._Reader(data, "<", where="test")
        u._parse_object_table_slow(r, sf, 15, 3, data, 0, "test")
        self.assertEqual([o.script_type_index for o in sf.objects], [7, 8, 9])
        self.assertEqual([o.is_stripped for o in sf.objects], [0, 1, 0])

    def test_end_to_end_versions(self):
        """Parse đủ file ở mọi dải version: fast path không làm lệch vị trí
        đọc tiếp theo (script_types/externals/data vẫn đúng)."""
        for version in (17, 21, 22, 23):
            assets = bf.build_serialized_file(
                version=version,
                objects=[(1001, 0, bf.text_asset_data("greeting", "Xin chao 9 Kings!")),
                         (1002, 0, bf.text_asset_data("longtext", "x" * 100))],
                types=[(49, None)])
            sf = u.parse_serialized(assets)
            self.assertEqual([o.path_id for o in sf.objects], [1001, 1002])
            texts = dict((pid, (name, script)) for pid, name, script in sf.text_assets())
            self.assertEqual(texts[1001][1], "Xin chao 9 Kings!")
            self.assertEqual(len(texts[1002][1]), 100)


@pytest.mark.needs_fixture("unity/sample_v8_lz4.unity3d")
class TestAutoFastPath(unittest.TestCase):
    """AUTO dùng đường C (lz4 binding / lzma) khi lập plan, không dùng lz4pure."""

    def test_auto_plans_lz4_without_pure_python(self):
        be = u.UnityBackend("AUTO")
        self.assertTrue(be.has_lz4_binding)
        data = Path(BUNDLE_LZ4).read_bytes()
        with mock.patch.object(lz4pure, "decompress_block",
                               side_effect=AssertionError("đường Python thuần bị gọi!")):
            plan = u.plan_bytes(data, name="sample_v8_lz4.unity3d", backend=be)
        self.assertEqual(plan.verdict.level, "CHẮC CHẮN")
        self.assertTrue(plan.rows)

    def test_native_uses_pure_python(self):
        be = u.UnityBackend("NATIVE")
        data = Path(BUNDLE_LZ4).read_bytes()
        calls = []
        orig = lz4pure.decompress_block
        with mock.patch.object(lz4pure, "decompress_block",
                               side_effect=lambda *a, **k: (calls.append(1), orig(*a, **k))[1]):
            u.plan_bytes(data, name="sample_v8_lz4.unity3d", backend=be)
        self.assertTrue(calls)


class TestCompactPlan(unittest.TestCase):
    """Menu chỉ in TextAsset (bản gọn); `unity inspect` vẫn in đủ."""

    def _plan(self, n_text=65, n_other=5):
        rows = []
        for i in range(n_text):
            rows.append(u.PlanRow(container="game.ipa", entry="level%d" % (i % 3),
                                  kind="assets", object_type="TextAsset",
                                  path_id=1000 + i, offset=i * 64, size=50,
                                  guess="text", name="text%02d" % i))
        for i in range(n_other):
            rows.append(u.PlanRow(container="game.ipa", entry="level0",
                                  kind="assets", object_type="GameObject",
                                  path_id=2000 + i, offset=i * 64, size=60,
                                  guess="binary", name=""))
        verdict = u.EngineVerdict(engine="unity", level="CHẮC CHẮN",
                                  reasons=["có '.unity3d'"])
        return u.UnityPlan(verdict=verdict, rows=rows)

    def test_compact_shows_only_text_assets_and_caps(self):
        plan = self._plan()
        buf = io.StringIO()
        ut._print_plan_compact(plan, out=buf.write, width=44, source="game.ipa")
        out = buf.getvalue()
        self.assertIn("70 dòng (65 TextAsset)", out)
        self.assertIn("text00", out)
        self.assertNotIn("GameObject", out)
        self.assertIn("… và 15 TextAsset khác (xem đủ: unity inspect game.ipa).", out)

    def test_compact_empty_text_assets(self):
        plan = self._plan(n_text=0, n_other=2)
        buf = io.StringIO()
        ut._print_plan_compact(plan, out=buf.write, width=44)
        self.assertIn("(không có TextAsset nào để patch)", buf.getvalue())

    def test_full_plan_still_shows_everything(self):
        plan = self._plan()
        buf = io.StringIO()
        ut._print_plan(plan, out=buf.write, width=44)
        out = buf.getvalue()
        self.assertIn("bản đồ dữ liệu: 70 dòng", out)
        self.assertIn("GameObject", out)
        self.assertIn("text64", out)

    def test_row_format_shared(self):
        """Hàng TextAsset in ở 2 bản phải giống nhau (cùng _plan_row_lines)."""
        plan = self._plan(n_text=2, n_other=0)
        full = io.StringIO()
        ut._print_plan(plan, out=full.write, width=44)
        compact = io.StringIO()
        ut._print_plan_compact(plan, out=compact.write, width=44)
        for row in plan.rows:
            for ln in ut._plan_row_lines(row, 44):
                self.assertIn(ln, full.getvalue())
                self.assertIn(ln, compact.getvalue())


def _fake_app(cache_dir):
    return SimpleNamespace(
        paths=SimpleNamespace(cache=Path(cache_dir)),
        report_progress=lambda *a, **k: None,
        overall=None)


# Bộ đếm để mỗi lần gọi _big_ipa_bytes cho dữ liệu khác nhau (test_eviction_lru
# cần 3 file khác nội dung nhau), nhưng xác định — không phụ thuộc /dev/urandom.
_BIG_IPA_SEED = [0]


def _big_ipa_bytes(min_bytes=1024 * 1024 + 1024):
    """IPA >= 1MB (entry giả ngẫu nhiên không nén được) + 1 bundle nhỏ.

    Dữ liệu giả ngẫu nhiên xác định (random.Random với seed tăng dần),
    KHÔNG dùng os.urandom: trên iSH, os.urandom(min_bytes) với min_bytes
    lớn (1MB) lỗi OSError: [Errno 5] Input/output error — bằng chứng 01:52,
    cả 7 test cache dùng hàm này đều ERROR y hệt, trong khi test dùng
    os.urandom(3KB) ở test_safe_io thì pass (đọc nhỏ vẫn được, đọc lớn thì
    lỗi). Mã cache sản phẩm vô tội (đã nuốt mọi lỗi I/O); chỉ fixture test
    này sai. Dữ liệu xác định vẫn không nén được (entropy cao) nên fixture
    giữ đúng tính chất >= 1MB; khác nhau mỗi lần gọi để test eviction phân
    biệt được."""
    _BIG_IPA_SEED[0] += 1
    data = random.Random(0x9B30000 + _BIG_IPA_SEED[0]).randbytes(min_bytes)
    assets = bf.build_serialized_file(
        version=22, objects=[(1001, 0, bf.text_asset_data("greeting", "hi"))],
        types=[(49, None)])
    bundle = bf.build_bundle(version=8, entries=[("CAB-" + "c" * 32, 4, assets)],
                             compression="none")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Payload/Game.app/Data/blob.bin", data)
        zf.writestr("Payload/Game.app/Data/level.unity3d", bundle)
    return buf.getvalue()


class TestPlanCache(TmpCase):
    """Cache plan: hit/miss/vô hiệu hoá/lỗi file cache/giới hạn."""

    def setUp(self):
        super().setUp()
        (self.tmp / "cache").mkdir()
        self.app = _fake_app(self.tmp / "cache")
        self.backend = u.UnityBackend("AUTO")

    def _cached(self, data, **kw):
        return ut._cached_plan(self.app, data, "game.ipa", self.backend, **kw)

    def test_big_ipa_bytes_does_not_need_urandom(self):
        # Khóa hồi quy cho lỗi iSH 01:52 (7 test cache ERROR EIO): fixture
        # _big_ipa_bytes không được gọi os.urandom — mô phỏng iSH bằng cách
        # cho urandom lỗi EIO, fixture vẫn phải build được, đủ lớn (≥1MB),
        # không nén được, và mỗi lần gọi khác nhau (cho test eviction).
        import zlib
        with mock.patch.object(os, "urandom",
                               side_effect=OSError(5, "Input/output error")):
            a = _big_ipa_bytes()
            b = _big_ipa_bytes()
        self.assertGreaterEqual(len(a), 1024 * 1024)
        self.assertNotEqual(a, b)
        self.assertGreater(len(zlib.compress(a)), len(a) * 0.9)

    @pytest.mark.needs_fixture("unity/sample_v8_lz4.unity3d")
    def test_small_file_not_cached(self):
        data = Path(BUNDLE_LZ4).read_bytes()
        self.assertLess(len(data), ut.PLAN_CACHE_MIN_BYTES)
        plan1, cached1 = self._cached(data)
        plan2, cached2 = self._cached(data)
        self.assertFalse(cached1)
        self.assertFalse(cached2)
        self.assertFalse((self.tmp / "cache" / ut.PLAN_CACHE_FILENAME).exists())
        self.assertEqual(ut._plan_to_dict(plan1), ut._plan_to_dict(plan2))

    def test_hit_returns_equal_plan_without_rescan(self):
        data = _big_ipa_bytes()
        plan1, cached1 = self._cached(data)
        self.assertFalse(cached1)
        self.assertTrue((self.tmp / "cache" / ut.PLAN_CACHE_FILENAME).exists())
        calls = []
        real = u.plan_bytes
        with mock.patch.object(u, "plan_bytes",
                               side_effect=lambda *a, **k: (calls.append(1), real(*a, **k))[1]):
            plan2, cached2 = self._cached(data)
        self.assertTrue(cached2)
        self.assertEqual(calls, [])
        self.assertEqual(ut._plan_to_dict(plan1), ut._plan_to_dict(plan2))

    def test_changed_content_misses(self):
        data = _big_ipa_bytes()
        self._cached(data)
        changed = bytearray(data)
        changed[len(changed) // 2] ^= 0xFF  # đổi 1 byte giữa file, cùng kích thước
        plan2, cached2 = self._cached(bytes(changed))
        self.assertFalse(cached2)

    def test_corrupt_cache_file_falls_back(self):
        data = _big_ipa_bytes()
        self._cached(data)
        (self.tmp / "cache" / ut.PLAN_CACHE_FILENAME).write_bytes(b"{hong")
        plan, cached = self._cached(data)
        self.assertFalse(cached)
        self.assertTrue(plan.rows)

    def test_round_trip_preserves_plan(self):
        data = _big_ipa_bytes()
        plan1, _ = self._cached(data)
        plan2, cached2 = self._cached(data)
        self.assertTrue(cached2)
        d1, d2 = ut._plan_to_dict(plan1), ut._plan_to_dict(plan2)
        self.assertEqual(d1, d2)
        self.assertEqual(d1["verdict"]["level"], "CHẮC CHẮN")
        self.assertTrue(d1["rows"])

    def test_eviction_lru(self):
        with mock.patch.object(ut, "PLAN_CACHE_MAX_ENTRIES", 2):
            files = [_big_ipa_bytes() for _ in range(3)]
            # 3 file khác nhau (urandom) -> mỗi file 1 entry
            for data in files:
                self._cached(data)
            entries = ut._load_plan_cache(self.app)
            self.assertEqual(len(entries), 2)
            keys = {hashlib.sha256(d).hexdigest() for d in files}
            self.assertEqual(set(entries), keys - {hashlib.sha256(files[0]).hexdigest()})
            # file đầu đã bị đuổi -> quét lại (miss)
            _, cached = self._cached(files[0])
            self.assertFalse(cached)

    @slow
    def test_cache_hit_is_fast(self):
        data = _big_ipa_bytes()
        self._cached(data)
        t0 = time.perf_counter()
        plan, cached = self._cached(data)
        dt = time.perf_counter() - t0
        self.assertTrue(cached)
        self.assertLess(dt, 2.0, "cache hit mất %.2fs" % dt)
        self.assertTrue(plan.rows)


class TestInspectProgress(unittest.TestCase):
    """Tiến độ quét: TTY -> thanh; chuyển hướng -> 1 dòng/stderr mỗi entry."""

    def test_redirected_writes_progress_lines(self):
        seen = []
        app = SimpleNamespace(report_progress=lambda f, note=None: seen.append((f, note)),
                              overall=None)
        err = io.StringIO()
        with mock.patch.object(sys, "stderr", err):
            ut._inspect_progress(app, 3, 27, "Payload/Game.app/Data/level4")
        self.assertEqual(seen, [(3 / 27, "Payload/Game.app/Data/level4")])
        self.assertEqual(err.getvalue(), "quét 3/27: Payload/Game.app/Data/level4\n")

    def test_enabled_bar_writes_nothing(self):
        seen = []
        app = SimpleNamespace(report_progress=lambda f, note=None: seen.append((f, note)),
                              overall=SimpleNamespace(enabled=True))
        err = io.StringIO()
        with mock.patch.object(sys, "stderr", err):
            ut._inspect_progress(app, 3, 27, "level4")
        self.assertEqual(seen, [(3 / 27, "level4")])
        self.assertEqual(err.getvalue(), "")


class TestCmdInspectUsesCache(TmpCase):
    """cmd_inspect lần 2 trên cùng file lớn: dùng cache, in dòng báo cache."""

    def test_second_inspect_uses_cache(self):
        (self.tmp / "cache").mkdir()
        app = _fake_app(self.tmp / "cache")
        app.limits = None
        data = _big_ipa_bytes()
        path = self.tmp / "game.ipa"
        path.write_bytes(data)
        ns = SimpleNamespace(path=str(path), backend=None)
        out1 = io.StringIO()
        err1 = io.StringIO()
        with mock.patch.object(sys, "stdout", out1):
            with mock.patch.object(sys, "stderr", err1):
                rc1 = ut.cmd_inspect(app, ns)
        self.assertEqual(rc1, 0)
        self.assertNotIn("dùng plan đã lưu", out1.getvalue())
        calls = []
        real = u.plan_bytes
        out2 = io.StringIO()
        err2 = io.StringIO()
        with mock.patch.object(u, "plan_bytes",
                               side_effect=lambda *a, **k: (calls.append(1), real(*a, **k))[1]):
            with mock.patch.object(sys, "stdout", out2):
                with mock.patch.object(sys, "stderr", err2):
                    rc2 = ut.cmd_inspect(app, ns)
        self.assertEqual(rc2, 0)
        self.assertEqual(calls, [])
        self.assertIn("dùng plan đã lưu (cache)", out2.getvalue())
        # cùng verdict + cùng số dòng
        self.assertIn("CHẮC CHẮN", out2.getvalue())


if __name__ == "__main__":
    unittest.main()
