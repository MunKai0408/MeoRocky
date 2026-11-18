"""Test đợt polish (Wave 1): Unity U1/U3/U4/U7/U10/U13/U15, container V-items,
F-items (toml, salvage, thresholds, diag, sdk helpers, model, manifest, example_kv).

Fixture builder dùng lại tests/fixtures/unity/build_fixtures.py (độc lập UnityPy).
"""
import io
import importlib.util
import lz4.frame
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import copy

from core import config as cfgmod
from core.errors import PB3Error
from core.limits import Limits
from core.model import Asset, FileRef, InnerPath, Locale, Project, SourcePath
from core.tomlmini import TomlError, loads as tomlmini_loads
from registry.arbitration import thresholds_from_config
from sdk import containers, lz4pure, unity as u
from sdk.api import LocaleRequest, PluginContext
from sdk.example_kv import dec, parse_text
from sdk.manifest import parse_manifest
from sdk.types import Candidate, LocEntry, LocTable, SourceRef
from sdk.vfs import LimitedVFS, MemoryVFS
from tests.helpers import ROOT, TmpCase, subprocess_timeout
from tests.helpers import pytest  # noqa: F401 - marker needs_fixture (stub trên iSH)
from tests.tiering import slow
from tests.test_sdk import APPX_A

FX = ROOT / "tests" / "fixtures" / "unity"
ZIP_FX = ROOT / "tests" / "fixtures" / "zip"
V21 = FX / "sample_v21.assets"
BUNDLE_V8 = FX / "sample_v8.unity3d"
NESTED_IPA = FX / "nested.ipa"
ENTRY1 = "CAB-0123456789abcdef0123456789abcdef"

_spec = importlib.util.spec_from_file_location(
    "pb3_build_fixtures", str(FX / "build_fixtures.py"))
bf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bf)


def read(p):
    return Path(p).read_bytes()


def code_of(fn, *args, **kw):
    try:
        fn(*args, **kw)
    except PB3Error as exc:
        return exc.code
    return None


def _sf21_bytes():
    return bf.build_serialized_file(
        version=21, objects=[(1001, 0, bf.text_asset_data("greeting", "Xin chao 9 Kings!")),
                             (1002, 0, bf.text_asset_data("longtext", "x" * 100))],
        types=[(49, None)])


class TestPolishUnityVersions(TmpCase):
    """U15: SerializedFile v15/16/18/19/20/22 — dựng từ template v21 qua
    build_serialized rồi parse lại; mọi phiên bản đều đọc được TextAsset."""

    @pytest.mark.needs_fixture("unity/sample_v21.assets")
    def test_versions_roundtrip(self):
        sf = u.parse_serialized(read(V21))
        n_obj = len(sf.objects)
        names = {pid: name for pid, name, _ in sf.text_assets()}
        for ver in (15, 16, 18, 19, 20, 22):
            with self.subTest(version=ver):
                raw = u.build_serialized(sf, {}, version=ver)
                back = u.parse_serialized(raw)
                self.assertEqual(back.version, ver)
                self.assertEqual([o.path_id for o in back.objects],
                                 [o.path_id for o in sf.objects])
                self.assertEqual(len(back.objects), n_obj)
                texts = dict((pid, script) for pid, _, script in back.text_assets())
                for pid, name in names.items():
                    self.assertEqual(texts[pid], dict(
                        (p, s) for p, _, s in sf.text_assets())[pid])


def _odd_byte_start_assets():
    """U7: SerializedFile v21 tối giản, object duy nhất có byte_start LẺ (1).
    Dựng tay bằng struct (build_serialized_file luôn căn 8 nên không làm được)."""
    payload = bf.text_asset_data("greeting", "Xin chao 9 Kings!")
    meta = io.BytesIO()
    meta.write(b"2019.4.40f1\x00")
    meta.write(struct.pack("<i", 9))          # target_platform
    meta.write(b"\x00")                       # enable_type_tree = False
    meta.write(struct.pack("<i", 1))          # 1 type
    meta.write(struct.pack("<i", 49))         # TextAsset
    meta.write(b"\x00")                       # is_stripped (v>=16)
    meta.write(struct.pack("<h", -1))         # script_type_index (v>=17)
    meta.write(b"\x00" * 16)                  # old_type_hash (v>=13)
    meta.write(struct.pack("<i", 1))          # 1 object
    header_size = 20
    meta.seek((-(header_size + meta.tell()) % 4), 1)   # căn 4 tuyệt đối
    meta.write(struct.pack("<q", 1001))       # path_id
    meta.write(struct.pack("<I", 1))          # byte_start LẺ
    meta.write(struct.pack("<I", len(payload)))
    meta.write(struct.pack("<i", 0))          # type_id
    meta.write(struct.pack("<i", 0))          # script_types
    meta.write(struct.pack("<i", 0))          # externals
    meta.write(b"\x00")                       # user_information
    meta_bytes = meta.getvalue()
    data = b"\x00" + payload                  # 1 byte đệm -> payload ở offset 1
    data_offset = (20 + len(meta_bytes) + 15) // 16 * 16
    file_size = data_offset + len(data)
    out = io.BytesIO()
    out.write(struct.pack(">IIII", len(meta_bytes), file_size, 21, data_offset))
    out.write(b"\x00\x00\x00\x00")
    out.write(meta_bytes)
    out.seek(data_offset)
    out.write(data)
    return out.getvalue()


class TestPolishUnity(TmpCase):
    def test_u7_odd_byte_start_patch_inplace_passes(self):
        raw = _odd_byte_start_assets()
        sf = u.parse_serialized(raw)
        self.assertEqual(sf.text_assets(), [(1001, "greeting", "Xin chao 9 Kings!")])
        res = u.apply_patch(u.PatchRequest(
            data=raw, kind="assets", path_id=1001,
            old_text="9 Kings", new_text="8 Kings", write_mode="PATCH_INPLACE"))
        self.assertTrue(res.verified, res.verify_details)
        back = u.parse_serialized(res.output_bytes)
        self.assertEqual(back.text_assets()[0][2], "Xin chao 8 Kings!")

    @pytest.mark.needs_fixture("unity/sample_v21.assets", "unity/sample_v8.unity3d")
    def test_u1_container_per_entry_resilient_direct_hard_abort(self):
        v9 = bytearray(read(BUNDLE_V8))
        struct.pack_into(">I", v9, 8, 9)
        self.assertEqual(u.detect_bundle(bytes(v9))["version"], 9)
        # Inspect trực tiếp bundle v9 GIẢ (file v8 chỉ đổi nhãn version,
        # blocks-info vẫn layout v8 không có offset u64): hard-abort.
        # Từ C5, v9 thật đã được hỗ trợ đọc nên file giả này không còn
        # là "version chưa hỗ trợ" (E_UNSUPPORTED_FORMAT) mà là v9
        # SAI CẤU TRÚC (E_CORRUPT) — vẫn hard-abort như trước.
        self.assertEqual(code_of(u.plan_bytes, bytes(v9), name="x.unity3d"),
                         "E_CORRUPT")
        # Trong container: entry v9 thành dòng "unsupported", các entry khác vẫn plan được.
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("Payload/Game.app/Data/level.unity3d", bytes(v9))
            zf.writestr("Payload/Game.app/Data/level8.unity3d", read(BUNDLE_V8))
            zf.writestr("resources.assets", read(V21))
        plan = u.plan_bytes(buf.getvalue(), name="fake.ipa")
        self.assertTrue(plan.partial)
        bad = [r for r in plan.rows if r.object_type == "unsupported"]
        self.assertEqual(len(bad), 1)
        self.assertTrue(bad[0].entry.endswith("level.unity3d"))
        self.assertTrue(any(r.kind == "bundle" for r in plan.rows))
        self.assertTrue(any(r.container.endswith("resources.assets")
                            and r.object_type == "TextAsset" for r in plan.rows))
        self.assertTrue(any("E_PARSE_PARTIAL" in w or "unsupported" in w.lower()
                            for w in plan.warnings))

    def test_u3_bomb_block_size_rejected_before_decompress(self):
        bundle = bf.build_bundle(version=7, entries=[(ENTRY1, 0, _sf21_bytes())])
        # Tìm đầu blocks-info: header + 2 chuỗi NUL + (Q + 3I) rồi căn 16.
        pos = 8 + 4
        for _ in range(2):
            pos = bundle.index(b"\x00", pos) + 1
        pos += 8 + 4 + 4 + 4
        info_off = (pos + 15) // 16 * 16
        # info: 16 hash + i block_count + block đầu (unc, comp, flag).
        block_unc_off = info_off + 16 + 4
        bomb = bytearray(bundle)
        struct.pack_into(">I", bomb, block_unc_off, 0x40000000)   # 1 GiB, chưa nén
        self.assertEqual(code_of(u.parse_bundle, bytes(bomb)), "E_TOO_LARGE")

    def test_u4_lz4_frame_with_dict_id_rejected(self):
        frame = lz4.frame.compress(b"hello world " * 100)
        self.assertTrue(frame[4] & 0x08)         # frame mẫu có content-size
        flg = frame[4] | 0x01                    # bật cờ dictionary-ID
        header = bytes([flg, frame[5]]) + frame[6:14] + b"\x12\x34\x56\x78"
        hc = (lz4pure.xxhash32(header, 0) >> 8) & 0xFF
        forged = frame[:4] + header + bytes([hc]) + frame[15:]
        self.assertEqual(code_of(lz4pure.decompress_frame, forged),
                         "E_UNSUPPORTED_FORMAT")

    def test_u10_patch_bundle_preserves_flags(self):
        bundle = bf.build_bundle(version=8, unity_version="6000.0.23f1",
                                 entries=[(ENTRY1, 0, _sf21_bytes())],
                                 extra_data_flags=u.FLAG_OLD_WEB, pad_block_data=True)
        before = u.parse_bundle(bundle)
        self.assertTrue(before.data_flags & u.FLAG_OLD_WEB)
        self.assertTrue(before.data_flags & 0x200)
        res = u.apply_patch(u.PatchRequest(
            data=bundle, kind="bundle", entry=ENTRY1, path_id=1001,
            old_text="9 Kings", new_text="8 Kings", write_mode="PATCH_INPLACE"))
        self.assertTrue(res.verified)
        after = u.parse_bundle(res.output_bytes)
        self.assertTrue(after.data_flags & u.FLAG_OLD_WEB)
        self.assertTrue(after.data_flags & 0x200)

    def test_u13_lz4_slice_copy_non_overlapping(self):
        block = bytes([0x40]) + b"abcd" + struct.pack("<H", 4) + bytes([0x00])
        self.assertEqual(lz4pure.decompress_block(block, 8), b"abcdabcd")
        linked = bytes([0x00]) + struct.pack("<H", 4)
        self.assertEqual(lz4pure._decompress_block_linked(linked, b"abcd"), b"abcd")


class TestPolishContainers(TmpCase):
    def test_v16_tar_bz2(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:bz2") as tf:
            for name, data in (("a.txt", b"hello"), ("dir/b.txt", b"world!")):
                ti = tarfile.TarInfo(name)
                ti.size = len(data)
                tf.addfile(ti, io.BytesIO(data))
        vfs = containers.open_vfs(buf.getvalue(), source_name="x.tar.bz2")
        names = sorted(e.name for e in vfs.inventory().entries if not e.is_dir)
        self.assertEqual(names, ["a.txt", "dir/b.txt"])
        self.assertEqual(vfs.read("dir/b.txt"), b"world!")

    def test_v19_prepended_data_zip(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("a.txt", b"hello")
        vfs = containers.open_vfs(b"JUNKJUNK" + buf.getvalue(), source_name="x.zip")
        self.assertEqual(vfs.read("a.txt"), b"hello")

    def test_v12_rebuild_zip_duplicate_name_only_first_replaced(self):
        src = self.tmp / "dup.zip"
        with zipfile.ZipFile(src, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("a.txt", b"OLD-1")
            zf.writestr("a.txt", b"OLD-2")
            zf.writestr("b.txt", b"B")
        dest = self.tmp / "out.zip"
        rep = containers.rebuild_zip(src, dest, replacements={"a.txt": b"NEW"})
        self.assertEqual(rep.replaced, ["a.txt"])
        self.assertTrue(any("trùng" in w for w in rep.warnings))
        with zipfile.ZipFile(dest) as zf:
            names = zf.namelist()
            self.assertEqual(names, ["a.txt", "a.txt", "b.txt"])
            infos = zf.infolist()
            self.assertEqual(zf.open(infos[0]).read(), b"NEW")   # lần đầu = bản thay
            self.assertEqual(zf.open(infos[1]).read(), b"OLD-2")  # lần sau giữ nguyên

    def test_v4_nested_inventory_full_path(self):
        buf = io.BytesIO()
        inner = io.BytesIO()
        with zipfile.ZipFile(inner, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("inner.txt", b"in")
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("inner.zip", inner.getvalue())
            zf.writestr("top.txt", b"top")
        vfs = containers.open_vfs(buf.getvalue(), source_name="outer.zip")
        nested = containers.NestedVFS(vfs)
        try:
            inv = nested.inventory("inner.zip")
        finally:
            nested.close()
        self.assertEqual([e.name for e in inv.entries], ["inner.txt"])

    @pytest.mark.needs_fixture("zip/sample.ipa")
    def test_v17_container_list_limit_and_formats_cli(self):
        out = subprocess.run(
            [sys.executable, "menu.py", "container", "list",
             str(ZIP_FX / "sample.ipa"), "--limit", "2"],
            cwd=str(ROOT), capture_output=True, text=True,
            timeout=subprocess_timeout(30)).stdout
        self.assertIn("còn 1 entry", out)
        out = subprocess.run(
            [sys.executable, "menu.py", "container", "formats"],
            cwd=str(ROOT), capture_output=True, text=True,
            timeout=subprocess_timeout(30)).stdout
        self.assertIn("TAR.BZ2", out)
        self.assertIn("16 MiB", out)

    @pytest.mark.needs_fixture("zip/sample.ipa")
    def test_v22_container_read_nested_chunked(self):
        out_path = self.tmp / "res.assets"
        r = subprocess.run(
            [sys.executable, "menu.py", "container", "read",
             str(ZIP_FX / "sample.ipa"),
             "Payload/Game.app/Data/level.unity3d::resources.assets",
             "--out", str(out_path)],
            cwd=str(ROOT), capture_output=True, text=True,
            timeout=subprocess_timeout(30))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(out_path.stat().st_size, 223)


class TestPolishCore(TmpCase):
    def test_f3_toml_parity_rejects_datetime_inf_nan_hex(self):
        for text in ("x = 1979-05-27", "x = inf", "x = nan", "x = 0x10",
                     "x = 1979-05-27T07:32:00Z"):
            with self.subTest(text=text):
                self.assertRaises(ValueError, tomlmini_loads, text)
                self.assertRaises(ValueError, cfgmod.parse_toml, text)
        self.assertEqual(tomlmini_loads("x = 1\ny = 'a'"), {"x": 1, "y": "a"})

    def test_f10_salvage_drops_bad_lines(self):
        tree, dropped = cfgmod._salvage_toml("a = 1\nDONG HONG!!!\nb = 2\n")
        self.assertEqual(tree, {"a": 1, "b": 2})
        self.assertEqual(dropped, [2])

    def test_f6_thresholds_from_config(self):
        auto, ask, warn = thresholds_from_config(
            {"scoring.auto_select": 0.9, "scoring.ask_min": 0.6})
        self.assertEqual((auto, ask, warn), (0.9, 0.6, None))
        auto, ask, warn = thresholds_from_config(
            {"scoring.auto_select": 0.5, "scoring.ask_min": 0.6})
        self.assertEqual((auto, ask), (0.80, 0.55))
        self.assertIsNotNone(warn)

    def test_f31_diag_error_logs_as_error(self):
        ctx = PluginContext(MemoryVFS({"a.txt": b"x"}), tempdir=str(self.tmp))
        seen = []
        ctx2 = PluginContext(MemoryVFS({"a.txt": b"x"}), tempdir=str(self.tmp),
                             sink=lambda level, text: seen.append(level))
        ctx2.diag("E_INTERNAL", "lỗi thử", severity="error")
        self.assertEqual(ctx2.messages[-1][0], "error")
        self.assertEqual(seen[-1], "error")

    def test_f21_plugin_context_tempfile(self):
        ctx = PluginContext(MemoryVFS({"a.txt": b"x"}), tempdir=str(self.tmp))
        p = ctx.tempfile("probe", suffix=".txt")
        self.assertTrue(p.startswith(str(self.tmp)))
        self.assertTrue(Path(p).exists())

    def test_f31_for_plugin_copies_locale(self):
        ctx = PluginContext(MemoryVFS({"a.txt": b"x"}),
                            locale=LocaleRequest("vi", {"en": "vi"}))
        man = parse_manifest(dict(copy.deepcopy(APPX_A), id="probe_p"))
        sub = ctx.for_plugin(man)
        sub.locale.mapping["en"] = "xx"
        self.assertEqual(ctx.locale.mapping["en"], "vi")

    def test_f31_limited_vfs_slice_counts_actual_bytes(self):
        inner = MemoryVFS({"big.bin": b"x" * 100})
        lim = LimitedVFS(inner, Limits(max_memory_read_bytes=50))
        # Đọc 10 byte cuối: chỉ tính 10 byte thực đọc, không tính size yêu cầu 1000.
        self.assertEqual(lim.slice("big.bin", 90, 1000), b"x" * 10)

    def test_f1_counting_stream_raises_over_limit(self):
        inner = MemoryVFS({"big.bin": b"x" * 100})
        lim = LimitedVFS(inner, Limits(max_entry_bytes=10))
        stream = lim.open_stream("big.bin")
        self.assertEqual(code_of(stream.read, 20), "E_TOO_LARGE")


class TestPolishSdkModel(TmpCase):
    def test_f19_f20_f21_loctable(self):
        src = SourceRef.from_vpath("a.ipa::b/c.unity3d::resources.assets")
        self.assertEqual(src.container_stack, ["a.ipa", "b/c.unity3d"])
        self.assertEqual(src.rel_path, "resources.assets")
        t = LocTable.from_key_values({"a": "1", "b": "2"}, adapter_id="kv",
                                     source=src)
        self.assertEqual(t.to_key_values(), {"a": "1", "b": "2"})
        self.assertEqual(t.validate(), [])
        dup = LocTable([LocEntry("a", "1", None, "generated", "ok", src),
                        LocEntry("a", "2", None, "generated", "ok", src)],
                       None, "kv")
        self.assertEqual(dup.to_key_values(on_conflict="first"), {"a": "1"})
        self.assertEqual(dup.to_key_values(on_conflict="last"), {"a": "2"})
        self.assertEqual(code_of(dup.to_key_values_strict), "E_CORRUPT")
        bad = LocTable([LocEntry("a", "1", None, "generated", "ok", src)], None, "")
        self.assertEqual(bad.validate(), ["LocTable.adapter_id rỗng"])

    def test_f21_candidate_for_path(self):
        c = Candidate.for_path("a/b.json", "kv", score=0.5, reasons=["đuôi .json"])
        self.assertEqual(c.path, "a/b.json")
        self.assertEqual(code_of(Candidate.for_path, "../x.json", "kv"),
                         "E_UNSAFE_PATH")

    def test_f22_locale_compile_validate(self):
        loc = Locale("zh-Hans", aliases=["zh_CN", "Chinese", " zh-cn "],
                     detect_regex="(?i)zh")
        self.assertEqual(loc.aliases, ["zh_cn", "chinese", "zh-cn"])
        self.assertEqual(loc.validate(), [])
        self.assertIsNotNone(loc.compile())
        bad = Locale("xx", detect_regex="[")
        self.assertTrue(bad.validate())
        self.assertEqual(code_of(bad.compile), "E_CORRUPT")

    def test_f23_fileref_innerpath_project(self):
        self.assertEqual(code_of(FileRef, id="f", source=SourcePath("/root/x"),
                                 size=-1), "E_CORRUPT")
        self.assertEqual(code_of(FileRef, id="f", source=SourcePath("/root/x"),
                                 sha256="XYZ"), "E_CORRUPT")
        self.assertEqual(code_of(InnerPath, "../evil", "a/b"), "E_UNSAFE_PATH")
        proj = Project(name="p",
                       files=[FileRef(id="same", source=SourcePath("/root/x"))],
                       assets=[Asset(id="same", inner=InnerPath("c.ipa", "a/b"))])
        self.assertTrue(any("nhiều nhóm" in p for p in proj.validate()))

    def test_f25_manifest_magic_offset_bound(self):
        bad = copy.deepcopy(APPX_A)
        bad["match"] = {"magic": [{"offset": 2097152, "hex": "7B"}]}
        self.assertEqual(code_of(parse_manifest, bad), "E_PLUGIN_INVALID")
        ok = copy.deepcopy(APPX_A)
        ok["match"] = {"magic": [{"offset": 0, "hex": "7B"}]}
        self.assertIsNotNone(parse_manifest(ok))

    def test_f31_dec_keeps_unknown_escape(self):
        self.assertEqual(dec("a\\qb"), "a\\qb")
        self.assertEqual(dec("a\\nb"), "a\nb")
        self.assertEqual(dec("a\\\\b"), "a\\b")

    def test_f31_example_kv_probe_matches_parse_on_big_file(self):
        from sdk.example_kv import KvPlugin
        text = "".join("k%05d = v%d\n" % (i, i) for i in range(5000))
        self.assertGreater(len(text.encode()), 64 * 1024)
        ctx = PluginContext(MemoryVFS({"big.txt": text.encode()}),
                            locale=LocaleRequest("vi"))
        cand = Candidate.for_path("big.txt", "kv")
        ad = KvPlugin()
        self.assertEqual(ad.probe(ctx, cand)["pairs"],
                         len(parse_text(text)))
        table = ad.parse(ctx, cand)
        self.assertEqual(len(table.entries), 5000)


@slow
class TestPolishBigIpa(unittest.TestCase):
    """X10: IPA lớn tự sinh (50 entry, bundle UnityFS v8 ~5MB) — inspect phải xong hết, không partial."""

    N_ENTRIES = 50
    BIG_MB = 5

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        big = bf.text_asset_data("bigtext", "A" * (cls.BIG_MB * 1024 * 1024))
        assets = bf.build_serialized_file(version=22, objects=[(1001, 0, big)], types=[(49, None)])
        bundle = bf.build_bundle(version=8, entries=[("CAB-" + "a" * 32, 4, assets)], compression="none")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for i in range(cls.N_ENTRIES - 1):
                zf.writestr("Payload/Game.app/Data/file%02d.txt" % i, b"small %d\n" % i)
            zf.writestr("Payload/Game.app/Data/level.unity3d", bundle)
        cls.ipa = buf.getvalue()
        cls.bundle_size = len(bundle)
        cls.assets_size = len(assets)

    def test_plan_covers_all_entries_and_big_bundle(self):
        self.assertGreaterEqual(self.bundle_size, self.BIG_MB * 1024 * 1024)
        seen = []
        plan = u.plan_bytes(self.ipa, name="big.ipa", on_entry=lambda d, t, n: seen.append(n))
        self.assertFalse(plan.partial, plan.warnings)
        self.assertEqual(len(seen), self.N_ENTRIES)
        txt = [r for r in plan.rows if r.path_id == 1001]
        self.assertEqual(len(txt), 1)
        self.assertEqual(txt[0].object_type, "TextAsset")
        self.assertEqual(txt[0].kind, "assets")
        bundle_rows = [r for r in plan.rows if r.kind == "bundle"]
        self.assertTrue(bundle_rows)
        # Dòng bundle-entry mang kích thước entry CAB (không tính header UnityFS).
        self.assertEqual(max(r.size for r in bundle_rows), self.assets_size)

    def test_container_lists_all_entries(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".ipa", delete=False) as f:
            f.write(self.ipa)
            path = f.name
        try:
            inv = containers.open_vfs(path).inventory()
            self.assertEqual(len(inv.entries), self.N_ENTRIES)
        finally:
            Path(path).unlink()


class TestPolishPlanProbe(TmpCase):
    """X12: prefix-probe 64 byte + plan_max_entry_bytes trong Limits/config."""

    def _zip_with(self, entries):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, data in entries:
                zf.writestr(name, data)
        return buf.getvalue()

    def _bundle_bytes(self):
        assets = bf.build_serialized_file(
            version=21, objects=[(1001, 0, bf.text_asset_data("greeting", "Xin chao 9 Kings!"))],
            types=[(49, None)])
        return bf.build_bundle(version=7, entries=[("resources.assets", 0, assets)])

    def test_prefix_probe_skips_plain_media_without_reading(self):
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 500
        data = self._zip_with([("pic.png", png), ("level.unity3d", self._bundle_bytes())])
        reads = []

        class CountingVFS:
            def __init__(self, inner):
                self._inner = inner

            def __getattr__(self, name):
                return getattr(self._inner, name)

            def read(self, path, max_bytes=None):
                reads.append(path)
                return self._inner.read(path, max_bytes)

        real_open = containers.open_vfs
        proxies = {}

        def counting_open(d, *, limits=None, source_name=""):
            vfs = real_open(d, limits=limits, source_name=source_name)
            proxy = CountingVFS(vfs)
            proxies["vfs"] = proxy
            return proxy

        with mock.patch.object(containers, "open_vfs", counting_open):
            plan = u.plan_bytes(data, name="test.zip")
        self.assertNotIn("pic.png", reads)          # probe bỏ qua, không đọc cả entry
        self.assertIn("level.unity3d", reads)        # bundle vẫn đọc đủ
        # Dòng của entry lạ (đệ quy plan_bytes cũng chỉ trả 1 dòng "file" với
        # container = vpath đầy đủ, entry rỗng) — probe phải trả đúng dòng đó.
        png_rows = [r for r in plan.rows if r.container == "test.zip::pic.png"]
        self.assertEqual(len(png_rows), 1)
        self.assertEqual((png_rows[0].kind, png_rows[0].guess), ("file", "binary"))
        self.assertTrue(any(r.object_type == "TextAsset" for r in plan.rows))

    def test_plain_media_magics(self):
        self.assertTrue(u._is_plain_media(b"\x89PNG\r\n\x1a\n" + b"x" * 10))
        self.assertTrue(u._is_plain_media(b"\xff\xd8\xff\xe0" + b"x" * 10))
        self.assertTrue(u._is_plain_media(b"GIF89a" + b"x" * 10))
        self.assertTrue(u._is_plain_media(b"RIFF\x00\x00\x00\x00WEBP" + b"x" * 10))
        self.assertTrue(u._is_plain_media(b"\x00\x00\x00\x00ftypisom" + b"x" * 10))
        self.assertFalse(u._is_plain_media(b"UnityFS\x00"))
        self.assertFalse(u._is_plain_media(b"PK\x03\x04" + b"x" * 10))
        self.assertFalse(u._is_plain_media(b""))   # rỗng -> không kết luận, đọc đủ

    def test_plan_max_entry_bytes_from_limits(self):
        big = b"x" * 200
        data = self._zip_with([("big.assets", big)])
        plan = u.plan_bytes(data, name="test.zip",
                            limits=Limits(plan_max_entry_bytes=100))
        rows = [r for r in plan.rows if r.entry == "big.assets"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].guess, "binary (lớn, chưa đọc)")

    def test_plan_max_entry_bytes_default_and_config(self):
        # caps-po: plan_max 8 MiB -> 32 MiB (file .po DST 17-19 MB vào được plan);
        # max_entry 64 MiB -> 256 MiB; stream_entry_bytes mới 32 MiB.
        self.assertEqual(Limits().plan_max_entry_bytes, 32 * 1024 * 1024)
        self.assertEqual(Limits().max_entry_bytes, 256 * 1024 * 1024)
        self.assertEqual(Limits().stream_entry_bytes, 32 * 1024 * 1024)
        keys = {o.key for o in cfgmod.OPTIONS}
        self.assertIn("limits.plan_max_entry_bytes", keys)
        self.assertIn("limits.stream_entry_bytes", keys)
        default = (ROOT / "config" / "default.toml").read_text(encoding="utf-8")
        self.assertIn("plan_max_entry_bytes", default)
        self.assertIn("stream_entry_bytes", default)


if __name__ == "__main__":
    unittest.main()
