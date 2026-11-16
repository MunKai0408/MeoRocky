"""Test nén IPA kiểu WinRAR (solid LZMA) — đợt rà soát cuối, nhóm nén IPA.

Test khóa luật bắt buộc: từ chối >2 GiB, đích <=400 MiB, output không bao giờ
>500 MiB (quá thì xóa + báo kích thước tốt nhất), nguồn không bị sửa, không
ghi đè trừ --force, khôi phục byte-identical (sha256), tiến độ theo byte thật.
Luật kích thước test bằng tham số cap nhỏ (không cần IPA thật 2 GiB).
Tầng fast: fixture nhỏ, không subprocess.
"""
import argparse
import contextlib
import hashlib
import io
import json
import tarfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace

from core.errors import PB3Error
from core.limits import Limits
from core.progress import CancelToken
from sdk import compress as c
from services import compress_tool
from tests.fixtures.compress import (compressible_ipa_bytes,
                                     corrupt_crc_ipa_bytes,
                                     duplicate_ipa_bytes, empty_ipa_bytes,
                                     encrypted_ipa_bytes, make_ipa_bytes,
                                     random_ipa_bytes, sample_ipa_bytes,
                                     unsafe_name_ipa_bytes)
from tests.helpers import TmpCase
from ui import handlers


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _noop_progress(done, total):
    return None


class FakeOverall:
    """Thanh Overall giả: ghi lại các mốc frac (để kiểm tăng dần từng 1%)."""

    def __init__(self):
        self.fracs = []
        self.notes = []

    def update(self, frac=None, note=None):
        self.fracs.append(frac)
        self.notes.append(note)

    def render(self):
        return None


def _fake_app(tmp: Path, overall=None):
    return SimpleNamespace(
        paths=SimpleNamespace(output=tmp / "output", temp=tmp / "temp",
                              root=tmp),
        limits=Limits(), overall=overall)


def _rebuild_archive(src: Path, dest: Path, *, mutate_manifest=None,
                     mutate_entry=None, drop_last=False, add_extra=False):
    """Đọc archive PB3 rồi ghi lại (để giả mạo nội dung/manifest khi test).

    mutate_manifest(dict) -> sửa manifest trước khi ghi; mutate_entry(name, bytes)
    -> trả bytes mới cho entry đó; drop_last: bỏ member cuối; add_extra: thêm
    member thừa cuối cùng."""
    with tarfile.open(str(src), "r:xz") as tf:
        members = []
        for m in tf.getmembers():
            stream = tf.extractfile(m)
            members.append((m, stream.read() if stream is not None else b""))
    out_members = []
    for m, data in members:
        if m.name == c.MANIFEST_NAME and mutate_manifest is not None:
            manifest = json.loads(data.decode("utf-8"))
            mutate_manifest(manifest)
            data = json.dumps(manifest, ensure_ascii=False,
                              sort_keys=True).encode("utf-8")
        elif mutate_entry is not None:
            data = mutate_entry(m.name, data)
        out_members.append((m, data))
    if drop_last:
        out_members = out_members[:-1]
    if add_extra:
        extra = tarfile.TarInfo("thua.txt")
        extra.size = 3
        out_members.append((extra, b"xyz"))
    with tarfile.open(str(dest), "w:xz", preset=6) as tf:
        for m, data in out_members:
            info = tarfile.TarInfo(m.name)
            info.size = len(data)
            info.mtime = 0
            info.mode = 0o644
            info.type = m.type
            tf.addfile(info, io.BytesIO(data))


class TestRoundTrip(TmpCase):
    def _write_ipa(self, name, data):
        p = self.tmp / name
        p.write_bytes(data)
        return p

    def test_roundtrip_sample_byte_identical(self):
        src = self._write_ipa("game.ipa", sample_ipa_bytes())
        arc = self.tmp / "game.tar.xz"
        rep = c.compress_ipa(src, arc, progress=_noop_progress)
        self.assertEqual(rep.entries, 7)
        self.assertTrue(rep.target_met)
        self.assertEqual(rep.original_sha256, _sha(src))
        out = self.tmp / "restored.ipa"
        rep2 = c.decompress_archive(arc, out, progress=_noop_progress)
        self.assertTrue(rep2.byte_identical)
        self.assertEqual(out.read_bytes(), src.read_bytes())
        self.assertEqual(rep2.restored_sha256, _sha(src))

    def test_roundtrip_empty_ipa(self):
        src = self._write_ipa("empty.ipa", empty_ipa_bytes())
        arc = self.tmp / "empty.tar.xz"
        c.compress_ipa(src, arc, progress=_noop_progress)
        out = self.tmp / "empty-restored.ipa"
        rep = c.decompress_archive(arc, out, progress=_noop_progress)
        self.assertTrue(rep.byte_identical)
        self.assertEqual(out.read_bytes(), src.read_bytes())

    def test_roundtrip_single_large_entry_streaming(self):
        big = (b"0123456789abcdef" * 320 * 1024)  # ~5 MiB, nén được
        src = self._write_ipa("big.ipa", make_ipa_bytes([("big.bin", big)]))
        arc = self.tmp / "big.tar.xz"
        seen = []

        def prog(done, total):
            seen.append((done, total))
        c.compress_ipa(src, arc, progress=prog)
        self.assertGreater(len(seen), 5)  # tiến độ gọi nhiều lần (streaming)
        out = self.tmp / "big-restored.ipa"
        c.decompress_archive(arc, out, progress=_noop_progress)
        self.assertEqual(out.read_bytes(), src.read_bytes())

    def test_manifest_contents(self):
        src = self._write_ipa("game.ipa", sample_ipa_bytes())
        arc = self.tmp / "game.tar.xz"
        rep = c.compress_ipa(src, arc, preset=7, progress=_noop_progress)
        man = c.load_manifest(arc)
        self.assertEqual(man["format"], c.FORMAT)
        self.assertEqual(man["version"], c.FORMAT_VERSION)
        self.assertEqual(man["original_name"], "game.ipa")
        self.assertEqual(man["original_size"], src.stat().st_size)
        self.assertEqual(man["original_sha256"], _sha(src))
        self.assertEqual(man["preset"], 7)
        self.assertEqual(len(man["entries"]), rep.entries)
        by_name = {e.name: e for e in man["entries"]}
        from tests.fixtures.compress import COMPRESSIBLE_TEXT
        expected_text = (COMPRESSIBLE_TEXT * 400).encode("utf-8")
        entry = by_name["Payload/Game.app/Data/text1.txt"]
        self.assertEqual(entry.sha256,
                         hashlib.sha256(expected_text).hexdigest())
        self.assertEqual(entry.size, len(expected_text))
        self.assertEqual(entry.method, "deflated")
        self.assertEqual(by_name["Payload/Game.app/Data/raw.bin"].method, "stored")
        self.assertTrue(by_name["Payload/Game.app/Data/"].is_dir)

    def test_output_is_plain_tar_xz(self):
        src = self._write_ipa("game.ipa", sample_ipa_bytes())
        arc = self.tmp / "game.tar.xz"
        c.compress_ipa(src, arc, progress=_noop_progress)
        with tarfile.open(str(arc), "r:xz") as tf:
            names = [m.name for m in tf.getmembers()]
        self.assertEqual(names[0], c.MANIFEST_NAME)
        self.assertIn("Payload/Game.app/Data/text1.txt", names)
        self.assertIn("config.json", names)

    def test_solid_beats_zip_on_repeated_entries(self):
        # 3 entry văn bản GIỐNG NHAU: solid LZMA nén 1 lần, ZIP nén 3 lần.
        src = self._write_ipa("text.ipa", compressible_ipa_bytes())
        arc = self.tmp / "text.tar.xz"
        rep = c.compress_ipa(src, arc, progress=_noop_progress)
        self.assertLess(rep.compressed_size, src.stat().st_size // 2)
        self.assertGreater(rep.ratio, 2.0)

    def test_random_data_ratio_near_one(self):
        src = self._write_ipa("rand.ipa", random_ipa_bytes())
        arc = self.tmp / "rand.tar.xz"
        rep = c.compress_ipa(src, arc, progress=_noop_progress)
        self.assertLessEqual(rep.compressed_size, src.stat().st_size * 1.05 + 65536)
        out = self.tmp / "rand-restored.ipa"
        rep2 = c.decompress_archive(arc, out, progress=_noop_progress)
        self.assertTrue(rep2.byte_identical)

    def test_source_never_modified(self):
        src = self._write_ipa("game.ipa", sample_ipa_bytes())
        before = _sha(src)
        arc = self.tmp / "game.tar.xz"
        c.compress_ipa(src, arc, progress=_noop_progress)
        c.decompress_archive(arc, self.tmp / "r.ipa", progress=_noop_progress)
        self.assertEqual(_sha(src), before)


class TestSizeRules(TmpCase):
    def test_constants(self):
        self.assertEqual(c.MAX_INPUT_BYTES, 2 * 1024 ** 3)
        self.assertEqual(c.TARGET_BYTES, 400 * 1024 ** 2)
        self.assertEqual(c.MAX_OUTPUT_BYTES, 500 * 1024 ** 2)

    def test_reject_over_2gb(self):
        src = self.tmp / "game.ipa"
        src.write_bytes(sample_ipa_bytes())
        with self.assertRaises(PB3Error) as ctx:
            c.compress_ipa(src, self.tmp / "o.tar.xz", max_input_bytes=100,
                           progress=_noop_progress)
        self.assertEqual(ctx.exception.code, "E_TOO_LARGE")
        self.assertFalse((self.tmp / "o.tar.xz").exists())

    def test_output_over_cap_deleted_with_best_size(self):
        src = self.tmp / "game.ipa"
        src.write_bytes(sample_ipa_bytes())
        dest = self.tmp / "o.tar.xz"
        with self.assertRaises(PB3Error) as ctx:
            c.compress_ipa(src, dest, max_output_bytes=100,
                           progress=_noop_progress)
        self.assertEqual(ctx.exception.code, "E_TOO_LARGE")
        self.assertIn("KHÔNG NÉN ĐƯỢC", ctx.exception.detail)
        self.assertFalse(dest.exists())

    def test_target_miss_still_succeeds_with_note(self):
        src = self.tmp / "game.ipa"
        src.write_bytes(sample_ipa_bytes())
        dest = self.tmp / "o.tar.xz"
        rep = c.compress_ipa(src, dest, target_bytes=10,
                             progress=_noop_progress)
        self.assertFalse(rep.target_met)
        self.assertTrue(dest.exists())

    def test_no_overwrite_without_force(self):
        src = self.tmp / "game.ipa"
        src.write_bytes(sample_ipa_bytes())
        dest = self.tmp / "o.tar.xz"
        dest.write_bytes(b"cu")
        with self.assertRaises(PB3Error) as ctx:
            c.compress_ipa(src, dest, progress=_noop_progress)
        self.assertEqual(ctx.exception.code, "E_OUTPUT_EXISTS")
        self.assertEqual(dest.read_bytes(), b"cu")
        rep = c.compress_ipa(src, dest, force=True, progress=_noop_progress)
        self.assertEqual(rep.dest, str(dest))

    def test_decompress_no_overwrite_without_force(self):
        src = self.tmp / "game.ipa"
        src.write_bytes(sample_ipa_bytes())
        arc = self.tmp / "game.tar.xz"
        c.compress_ipa(src, arc, progress=_noop_progress)
        out = self.tmp / "r.ipa"
        out.write_bytes(b"cu")
        with self.assertRaises(PB3Error) as ctx:
            c.decompress_archive(arc, out, progress=_noop_progress)
        self.assertEqual(ctx.exception.code, "E_OUTPUT_EXISTS")

    def test_cancel_token(self):
        src = self.tmp / "game.ipa"
        src.write_bytes(sample_ipa_bytes())
        token = CancelToken()
        token.cancel()
        with self.assertRaises(PB3Error) as ctx:
            c.compress_ipa(src, self.tmp / "o.tar.xz", cancel=token,
                           progress=_noop_progress)
        self.assertEqual(ctx.exception.code, "E_CANCELLED")


class TestRejects(TmpCase):
    def test_not_a_zip(self):
        src = self.tmp / "x.ipa"
        src.write_bytes(b"day khong phai zip" * 100)
        with self.assertRaises(PB3Error) as ctx:
            c.compress_ipa(src, self.tmp / "o.tar.xz", progress=_noop_progress)
        self.assertEqual(ctx.exception.code, "E_BAD_ARCHIVE")

    def test_missing_input(self):
        with self.assertRaises(PB3Error) as ctx:
            c.compress_ipa(self.tmp / "khong-co.ipa", self.tmp / "o.tar.xz",
                           progress=_noop_progress)
        self.assertEqual(ctx.exception.code, "E_INPUT_NOT_FOUND")

    def test_corrupt_crc_rejected(self):
        src = self.tmp / "bad.ipa"
        src.write_bytes(corrupt_crc_ipa_bytes())
        with self.assertRaises(PB3Error) as ctx:
            c.compress_ipa(src, self.tmp / "o.tar.xz", progress=_noop_progress)
        self.assertEqual(ctx.exception.code, "E_BAD_ARCHIVE")

    def test_encrypted_entry_rejected(self):
        src = self.tmp / "enc.ipa"
        src.write_bytes(encrypted_ipa_bytes())
        with self.assertRaises(PB3Error) as ctx:
            c.compress_ipa(src, self.tmp / "o.tar.xz", progress=_noop_progress)
        self.assertEqual(ctx.exception.code, "E_ENCRYPTED")

    def test_unsafe_name_rejected(self):
        src = self.tmp / "unsafe.ipa"
        src.write_bytes(unsafe_name_ipa_bytes())
        with self.assertRaises(PB3Error) as ctx:
            c.compress_ipa(src, self.tmp / "o.tar.xz", progress=_noop_progress)
        self.assertEqual(ctx.exception.code, "E_UNSAFE_PATH")

    def test_duplicate_name_rejected(self):
        src = self.tmp / "dup.ipa"
        src.write_bytes(duplicate_ipa_bytes())
        with self.assertRaises(PB3Error) as ctx:
            c.compress_ipa(src, self.tmp / "o.tar.xz", progress=_noop_progress)
        self.assertEqual(ctx.exception.code, "E_BAD_ARCHIVE")


class TestDecompressVerify(TmpCase):
    def _make_archive(self):
        src = self.tmp / "game.ipa"
        src.write_bytes(sample_ipa_bytes())
        arc = self.tmp / "game.tar.xz"
        c.compress_ipa(src, arc, progress=_noop_progress)
        return src, arc

    def test_decompress_plain_tarxz_rejected(self):
        plain = self.tmp / "plain.tar.xz"
        with tarfile.open(str(plain), "w:xz", preset=6) as tf:
            info = tarfile.TarInfo("a.txt")
            info.size = 1
            tf.addfile(info, io.BytesIO(b"x"))
        with self.assertRaises(PB3Error) as ctx:
            c.decompress_archive(plain, self.tmp / "r.ipa", progress=_noop_progress)
        self.assertEqual(ctx.exception.code, "E_BAD_ARCHIVE")

    def test_decompress_truncated_rejected(self):
        _src, arc = self._make_archive()
        raw = arc.read_bytes()
        bad = self.tmp / "cut.tar.xz"
        bad.write_bytes(raw[: len(raw) // 2])
        with self.assertRaises(PB3Error) as ctx:
            c.decompress_archive(bad, self.tmp / "r.ipa", progress=_noop_progress)
        self.assertIn(ctx.exception.code, ("E_BAD_ARCHIVE", "E_CORRUPT"))

    def test_decompress_entry_tamper_verify_failed(self):
        _src, arc = self._make_archive()
        tampered = self.tmp / "tampered.tar.xz"

        def flip(name, data):
            if name == "Payload/Game.app/Data/text1.txt":
                return b"X" + data[1:]
            return data
        _rebuild_archive(arc, tampered, mutate_entry=flip)
        out = self.tmp / "r.ipa"
        with self.assertRaises(PB3Error) as ctx:
            c.decompress_archive(tampered, out, progress=_noop_progress)
        self.assertEqual(ctx.exception.code, "E_VERIFY_FAILED")
        self.assertFalse(out.exists())
        # Bản ứng viên được giữ lại để điều tra (mẫu #72).
        self.assertTrue((self.tmp / "r.ipa.failed").exists())

    def test_decompress_manifest_method_tamper(self):
        _src, arc = self._make_archive()
        tampered = self.tmp / "badmethod.tar.xz"

        def bad_method(manifest):
            manifest["entries"][0]["method"] = "bogus"
        _rebuild_archive(arc, tampered, mutate_manifest=bad_method)
        with self.assertRaises(PB3Error) as ctx:
            c.decompress_archive(tampered, self.tmp / "r.ipa",
                                 progress=_noop_progress)
        self.assertEqual(ctx.exception.code, "E_UNSUPPORTED_FORMAT")

    def test_decompress_missing_member(self):
        _src, arc = self._make_archive()
        tampered = self.tmp / "missing.tar.xz"
        _rebuild_archive(arc, tampered, drop_last=True)
        with self.assertRaises(PB3Error) as ctx:
            c.decompress_archive(tampered, self.tmp / "r.ipa",
                                 progress=_noop_progress)
        self.assertEqual(ctx.exception.code, "E_BAD_ARCHIVE")

    def test_decompress_extra_member(self):
        _src, arc = self._make_archive()
        tampered = self.tmp / "extra.tar.xz"
        _rebuild_archive(arc, tampered, add_extra=True)
        with self.assertRaises(PB3Error) as ctx:
            c.decompress_archive(tampered, self.tmp / "r.ipa",
                                 progress=_noop_progress)
        self.assertEqual(ctx.exception.code, "E_BAD_ARCHIVE")


class TestPreset(TmpCase):
    def test_parse_preset(self):
        self.assertEqual(c.parse_preset(6), 6)
        self.assertEqual(c.parse_preset("7"), 7)
        self.assertEqual(c.parse_preset("9e"), "9e")
        self.assertEqual(c.parse_preset(" 9E "), "9e")
        for bad in (10, -1, "x", "10", None, True, 6.5):
            with self.assertRaises(PB3Error) as ctx:
                c.parse_preset(bad)
            self.assertEqual(ctx.exception.code, "E_CONFIG_INVALID")

    def test_preset_ram_estimates(self):
        self.assertLess(c.preset_ram_estimate_mb(6), c.preset_ram_estimate_mb(7))
        self.assertLess(c.preset_ram_estimate_mb(7), c.preset_ram_estimate_mb(8))
        self.assertLess(c.preset_ram_estimate_mb(8), c.preset_ram_estimate_mb(9))
        self.assertLess(c.preset_ram_estimate_mb(9), c.preset_ram_estimate_mb("9e"))
        # preset 6 ~ 100 MiB (số đo thật: 113 MiB trên fixture 86 MiB).
        self.assertLess(c.preset_ram_estimate_mb(6), 150)
        self.assertIsNone(c.preset_ram_warning(6))
        self.assertIsNotNone(c.preset_ram_warning(8))
        self.assertIsNotNone(c.preset_ram_warning("9e"))
        self.assertEqual(c.preset_dict_size(6), 8 * 1024 * 1024)
        self.assertEqual(c.preset_dict_size(9), 64 * 1024 * 1024)


class TestProgress(TmpCase):
    def test_progress_real_bytes_monotonic(self):
        src = self.tmp / "game.ipa"
        src.write_bytes(sample_ipa_bytes())
        total_uncompressed = 0
        with zipfile.ZipFile(str(src)) as zf:
            total_uncompressed = sum(i.file_size for i in zf.infolist())
        seen = []
        c.compress_ipa(src, self.tmp / "o.tar.xz",
                       progress=lambda d, t: seen.append((d, t)))
        self.assertTrue(seen)
        totals = {t for _d, t in seen}
        self.assertEqual(totals, {2 * total_uncompressed})
        dones = [d for d, _t in seen]
        self.assertEqual(dones, sorted(dones))
        self.assertEqual(dones[-1], 2 * total_uncompressed)

    def test_progress_decompress_monotonic(self):
        src = self.tmp / "game.ipa"
        src.write_bytes(sample_ipa_bytes())
        arc = self.tmp / "o.tar.xz"
        c.compress_ipa(src, arc, progress=_noop_progress)
        seen = []
        c.decompress_archive(arc, self.tmp / "r.ipa",
                             progress=lambda d, t: seen.append((d, t)))
        dones = [d for d, _t in seen]
        self.assertEqual(dones, sorted(dones))
        self.assertGreater(len(seen), 1)


class TestService(TmpCase):
    def test_handlers_commands(self):
        self.assertIn("compress", handlers.COMMANDS)
        self.assertIn("decompress", handlers.COMMANDS)
        self.assertEqual(handlers.COMMANDS["compress"].module,
                         "services.compress_tool")
        self.assertEqual(handlers.COMMANDS["decompress"].module,
                         "services.compress_tool")

    def test_add_arguments(self):
        parser = argparse.ArgumentParser(prog="python3 menu.py")
        subs = parser.add_subparsers(dest="command")
        sp = subs.add_parser("compress")
        compress_tool.add_arguments(sp)
        ns = parser.parse_args(["compress", "game.ipa", "--preset", "7", "--force"])
        self.assertEqual(ns.preset, "7")
        self.assertTrue(ns.force)
        self.assertEqual(ns.path, "game.ipa")
        parser2 = argparse.ArgumentParser(prog="python3 menu.py")
        subs2 = parser2.add_subparsers(dest="command")
        sp2 = subs2.add_parser("decompress")
        compress_tool.add_arguments(sp2)
        ns2 = parser2.parse_args(["decompress", "game.tar.xz", "--out", "outdir"])
        self.assertEqual(ns2.path, "game.tar.xz")
        self.assertFalse(hasattr(ns2, "preset"))

    def test_cmd_compress_and_decompress_via_service(self):
        src = self.tmp / "game.ipa"
        src.write_bytes(sample_ipa_bytes())
        app = _fake_app(self.tmp, overall=FakeOverall())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = compress_tool.run_cli(
                app, SimpleNamespace(command="compress", path=str(src),
                                     out=None, preset="6", force=False))
        self.assertEqual(rc, 0)
        out_text = buf.getvalue()
        self.assertIn("KHÔNG phải IPA cài trực tiếp", out_text)
        self.assertIn("đạt đích", out_text)
        arc = self.tmp / "output" / "game.tar.xz"
        self.assertTrue(arc.exists())
        # Thanh Overall nhận mốc % tăng dần, không lùi, tới 100%.
        fracs = app.overall.fracs
        self.assertTrue(fracs)
        self.assertEqual(fracs, sorted(fracs))
        self.assertAlmostEqual(fracs[-1], 1.0)
        app.overall = FakeOverall()
        buf2 = io.StringIO()
        with contextlib.redirect_stdout(buf2):
            rc = compress_tool.run_cli(
                app, SimpleNamespace(command="decompress", path=str(arc),
                                     out=None, force=False))
        self.assertEqual(rc, 0)
        self.assertIn("byte-identical", buf2.getvalue())
        restored = self.tmp / "output" / "game" / "game.ipa"
        self.assertEqual(restored.read_bytes(), src.read_bytes())

    def test_cmd_compress_preset_warning_printed(self):
        src = self.tmp / "game.ipa"
        src.write_bytes(sample_ipa_bytes())
        app = _fake_app(self.tmp, overall=FakeOverall())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            compress_tool.run_cli(
                app, SimpleNamespace(command="compress", path=str(src),
                                     out=None, preset="8", force=False))
        self.assertIn("cảnh báo RAM", buf.getvalue())

    def test_cmd_compress_bad_preset(self):
        src = self.tmp / "game.ipa"
        src.write_bytes(sample_ipa_bytes())
        app = _fake_app(self.tmp)
        with self.assertRaises(PB3Error) as ctx:
            compress_tool.run_cli(
                app, SimpleNamespace(command="compress", path=str(src),
                                     out=None, preset="99", force=False))
        self.assertEqual(ctx.exception.code, "E_CONFIG_INVALID")


if __name__ == "__main__":
    unittest.main()
