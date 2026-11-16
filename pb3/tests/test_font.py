"""Test khóa cho công cụ font 4 chế độ (đợt tổng cuối, nhóm font).

Fixture TTF tổng hợp (tests/fixtures/font/build_fixtures.py), KHÔNG dùng
font thật của game — trừ font nhập sẵn fonts/BeVietnamPro-Regular.ttf
(đây là tài sản của PB3, kiểm độ phủ + giấy phép của chính nó).
"""
from __future__ import annotations

import argparse
import os
import unicodedata
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import List

from core.errors import PB3Error
from sdk import font as fs
from services import font_tool as ft
from tests.fixtures.font import build_fixtures as fx
from tests.helpers import TmpCase
from tests.helpers import pytest  # noqa: F401 - marker needs_fixture (stub trên iSH)

ROOT = Path(__file__).resolve().parent.parent


def _fake_app(tmp: Path, root: Path = ROOT):
    return SimpleNamespace(
        paths=SimpleNamespace(root=root, output=tmp / "out",
                              temp=tmp / "temp", cache=tmp / "cache"),
        overall=None)


def _ns(**kw):
    base = {"action": "scan", "path": "", "font": None, "donor": None,
            "with_font": None, "out": None, "allow_write": False, "chars": None}
    base.update(kw)
    return SimpleNamespace(**base)


class TestDetectKind(TmpCase):
    def test_kinds(self):
        self.assertEqual(fs.detect_font_kind(fx.build_ttf([0x41])), "ttf")
        self.assertEqual(fs.detect_font_kind(fx.build_otf_stub()), "otf")
        self.assertEqual(fs.detect_font_kind(fx.build_ttc_stub()), "ttc")
        self.assertEqual(fs.detect_font_kind(b""), "unknown")
        self.assertEqual(fs.detect_font_kind(b"PK\x03\x04"), "unknown")

    def test_ttc_parse_refuses(self):
        with self.assertRaises(PB3Error) as cm:
            fs.parse_font(fx.build_ttc_stub())
        self.assertEqual(cm.exception.code, "E_UNSUPPORTED_FORMAT")

    def test_garbage_refuses(self):
        with self.assertRaises(PB3Error) as cm:
            fs.parse_font(b"khong phai font gi ca" * 4)
        self.assertEqual(cm.exception.code, "E_CORRUPT")


class TestCmap(TmpCase):
    def test_format4_roundtrip(self):
        data = fx.build_ttf(fx.ascii_cps() + fx.vietnamese_cps(), name="F4")
        info = fs.parse_font(data, where="F4")
        self.assertEqual(info.family, "F4")
        self.assertEqual(len(info.cmap), 95 + 142)
        self.assertEqual(info.cmap[0x41], 0x41 - 0x20 + 1)  # ASCII gán gid theo thứ tự
        self.assertIn(0x103, info.cmap)
        self.assertIn(0x1EF9, info.cmap)

    def test_format12_with_non_bmp(self):
        cps = fx.ascii_cps() + fx.vietnamese_cps() + [0x1F600]
        data = fx.build_ttf(cps, name="F12", cmap_format=12)
        info = fs.parse_font(data, where="F12")
        self.assertIn(0x1F600, info.cmap)
        self.assertIn(0x1EA0, info.cmap)

    def test_builders_roundtrip_real_map(self):
        data = open(ROOT / "fonts" / "BeVietnamPro-Regular.ttf", "rb").read()
        orig = fs.parse_font(data)
        for builder in (fs._build_cmap_format4, fs._build_cmap_format12):
            sub = builder(orig.cmap)
            got = {}
            if builder is fs._build_cmap_format4:
                fs._parse_cmap_format4(sub, 0, got)
            else:
                fs._parse_cmap_format12(sub, 0, got)
            self.assertEqual(got, orig.cmap)


class TestCoverage(TmpCase):
    def test_required_set(self):
        req = fs.required_vietnamese_chars()
        self.assertEqual(len(req), 142)
        for cp in (0x103, 0xE2, 0x111, 0xEA, 0xF4, 0x1A1, 0x1B0,
                   0x1EA0, 0x1EF9, 0x300, 0x301, 0x323):
            self.assertIn(cp, req)
        # Hồi quy patch02: 30 chữ Latin-1/Latin Extended-A từng bị sót
        # (dải U+1EA0–U+1EF9 không bao gồm chúng) nay phải có đủ.
        for cp in (0xE0, 0xE1, 0xE3, 0xE8, 0xE9, 0xEC, 0xED, 0xF2, 0xF3,
                   0xF5, 0xF9, 0xFA, 0xFD, 0x129, 0x169,
                   0xC0, 0xC1, 0xC3, 0xC8, 0xC9, 0xCC, 0xCD, 0xD2, 0xD3,
                   0xD5, 0xD9, 0xDA, 0xDD, 0x128, 0x168):
            self.assertIn(cp, req)

    def test_missing_font_reports_135(self):
        rep = fs.coverage(fx.build_font_missing_vietnamese())
        self.assertEqual(rep.total, 142)
        self.assertEqual(rep.covered, 7)
        self.assertEqual(len(rep.missing), 135)
        self.assertIn(0x1EA0, rep.missing)
        self.assertFalse(rep.is_full)
        self.assertIn("thiếu 135/142", rep.summary_vi())

    def test_full_font_is_full(self):
        rep = fs.coverage(fx.build_font_full_vietnamese())
        self.assertTrue(rep.is_full)
        self.assertEqual(rep.pct, 100)

    def test_missing_chars_helper(self):
        self.assertEqual(fs.missing_chars(fx.build_font_full_vietnamese()), [])


class TestPatch02Vietnamese134(TmpCase):
    """Hồi quy patch02: bộ kiểm đủ 134 chữ cái tiếng Việt.

    Joseph phát hiện danh sách cũ (112) thiếu 30 chữ Latin-1/Latin
    Extended-A (à á ã è é ì í ò ó õ ù ú ý ĩ ũ, 2 case) — font thiếu
    à/á vẫn qua được kiểm 112/112. Bộ mới: 142 mục = 134 chữ cái
    (120 chữ có dấu + 14 gốc đặc biệt) + 8 dấu kết hợp.
    """

    # Định nghĩa độc lập bộ 134 — KHÔNG import hằng số của sdk.font để
    # khỏi kiểm chính mình (chỉ dùng lại quy tắc gốc × dấu đã chốt).
    _BASES = ("a", "ă", "â", "e", "ê", "i", "o", "ô", "ơ", "u", "ư", "y")
    _TONES = ("\u0300", "\u0301", "\u0309", "\u0303", "\u0323")
    _SPECIAL = (0x103, 0xE2, 0x111, 0xEA, 0xF4, 0x1A1, 0x1B0,
                0x102, 0xC2, 0x110, 0xCA, 0xD4, 0x1A0, 0x1AF)
    _MARKS = (0x300, 0x301, 0x302, 0x303, 0x306, 0x309, 0x31B, 0x323)

    @classmethod
    def _expected_letters(cls):
        out = set(cls._SPECIAL)
        for base in cls._BASES:
            for tone in cls._TONES:
                ch = unicodedata.normalize("NFC", base + tone)
                assert len(ch) == 1, (base, tone, ch)
                out.add(ord(ch))
                out.add(ord(ch.upper()))
        return out

    def test_required_is_142_with_134_letters(self):
        letters = self._expected_letters()
        self.assertEqual(len(letters), 134)
        self.assertEqual(len(fs.REQUIRED_VIETNAMESE), 142)
        self.assertEqual(set(fs.REQUIRED_VIETNAMESE) - set(self._MARKS), letters)
        self.assertEqual(len(fs.required_vietnamese_chars()), 142)

    def test_bundled_font_covers_all_134_letters(self):
        data = (ROOT / "fonts" / fs.BUNDLED_FONT_NAME).read_bytes()
        rep = fs.coverage(data)
        self.assertEqual(rep.total, 142)
        self.assertTrue(rep.is_full)
        self.assertEqual(rep.covered, 142)
        letters = self._expected_letters()
        info = fs.parse_font(data)
        self.assertEqual(sum(1 for cp in letters if cp in info.cmap), 134)

    def test_font_missing_a_grave_acute_reports_exactly_those_two(self):
        # Khóa lỗ hổng: font thiếu à/á phải bị báo thiếu đúng 2 chữ đó.
        cps = [cp for cp in fx.vietnamese_cps() if cp not in (0xE0, 0xE1)]
        data = fx.build_ttf(fx.ascii_cps() + cps, name="NoA")
        self.assertEqual(fs.missing_chars(data), [0xE0, 0xE1])
        rep = fs.coverage(data)
        self.assertEqual(rep.total, 142)
        self.assertEqual(rep.covered, 140)
        self.assertEqual(rep.missing, [0xE0, 0xE1])
        self.assertFalse(rep.is_full)


class TestBundledFont(TmpCase):
    def test_bundled_font_full_and_clean(self):
        path = ROOT / "fonts" / fs.BUNDLED_FONT_NAME
        self.assertTrue(path.is_file(), "thiếu font nhập sẵn chế độ 4")
        data = path.read_bytes()
        info = fs.parse_font(data, where=path.name)
        self.assertEqual(info.family, "Be Vietnam Pro")
        rep = fs.coverage(data)
        self.assertTrue(rep.is_full, "font nhập sẵn phải đủ 134/134 chữ cái tiếng Việt (142 mục kiểm)")
        self.assertEqual(fs.verify_font(data), [])

    def test_bundled_license_is_ofl(self):
        lic = ROOT / "fonts" / fs.BUNDLED_FONT_LICENSE
        self.assertTrue(lic.is_file(), "thiếu file giấy phép OFL.txt")
        text = lic.read_text(encoding="utf-8")
        self.assertIn("SIL Open Font License", text)
        self.assertIn("Be Vietnam Pro", text)


class TestMerge(TmpCase):
    def test_merge_simple_full_coverage(self):
        target = fx.build_font_missing_vietnamese()
        donor = fx.build_font_full_vietnamese()
        res = fs.merge_glyphs(target, donor, fs.required_vietnamese_chars())
        self.assertEqual(len(res.added), 135)
        self.assertEqual(len(res.already), 7)
        self.assertEqual(res.donor_missing, [])
        self.assertEqual(res.glyphs_copied, 135)
        self.assertEqual(fs.verify_font(res.data), [])
        self.assertTrue(fs.coverage(res.data).is_full)
        merged = fs.parse_font(res.data)
        self.assertEqual(merged.num_glyphs, 103 + 135)

    def test_merge_keeps_original_glyphs_and_hmtx(self):
        target = fx.build_font_missing_vietnamese()
        res = fs.merge_glyphs(target, fx.build_font_full_vietnamese(),
                              fs.required_vietnamese_chars())
        t = fs.parse_font(target)
        m = fs.parse_font(res.data)
        for gid in range(t.num_glyphs):
            self.assertEqual(fs.glyph_data(target, t, gid),
                             fs.glyph_data(res.data, m, gid),
                             "glyph %d đổi sau khi gộp" % gid)
        self.assertEqual(fs._hmtx_entries(target, t),
                         fs._hmtx_entries(res.data, m)[:t.num_glyphs])

    def test_merge_composite_copies_components_and_remaps(self):
        target = fx.build_font_missing_vietnamese()
        donor = fx.build_font_with_composite()
        res = fs.merge_glyphs(target, donor, [0x1EAE, 0xC2, 0x301])
        self.assertEqual(sorted(res.added), [0xC2, 0x301, 0x1EAE])
        self.assertEqual(res.glyphs_copied, 3)
        self.assertEqual(fs.verify_font(res.data), [])
        m = fs.parse_font(res.data)
        comps = fs.composite_components(
            fs.glyph_data(res.data, m, m.cmap[0x1EAE]))
        self.assertEqual(comps, [m.cmap[0xC2], m.cmap[0x301]])

    def test_merge_skips_already_and_warns_donor_missing(self):
        target = fx.build_font_missing_vietnamese()
        donor = fx.build_ttf(fx.ascii_cps() + [0x103], name="PoorDonor")
        res = fs.merge_glyphs(target, donor, [0x103, 0x1EA0, 0x41])
        self.assertEqual(res.already, [0x41, 0x103])
        self.assertEqual(res.donor_missing, [0x1EA0])
        self.assertTrue(any("font nguồn cũng thiếu" in w for w in res.warnings))
        self.assertEqual(res.added, [])

    def test_merge_rejects_otf_and_ttc(self):
        target = fx.build_font_missing_vietnamese()
        donor = fx.build_font_full_vietnamese()
        with self.assertRaises(PB3Error) as cm:
            fs.merge_glyphs(fx.build_otf_stub(), donor, [0x1EA0])
        self.assertEqual(cm.exception.code, "E_UNSUPPORTED_FORMAT")
        with self.assertRaises(PB3Error) as cm:
            fs.merge_glyphs(target, fx.build_otf_stub(), [0x1EA0])
        self.assertEqual(cm.exception.code, "E_UNSUPPORTED_FORMAT")
        with self.assertRaises(PB3Error):
            fs.merge_glyphs(target, fx.build_ttc_stub(), [0x1EA0])

    def test_merge_progress_counts_glyphs(self):
        seen: List[int] = []
        fs.merge_glyphs(fx.build_font_missing_vietnamese(),
                        fx.build_font_full_vietnamese(),
                        fs.required_vietnamese_chars(),
                        progress_fn=lambda d, t: seen.append(d))
        self.assertEqual(seen[-1], 135)
        self.assertTrue(all(b >= a for a, b in zip(seen, seen[1:])))

    def test_verify_catches_flipped_byte(self):
        raw = bytearray(fx.build_font_missing_vietnamese())
        raw[-10] ^= 0xFF
        self.assertTrue(fs.verify_font(bytes(raw)))


class TestScan(TmpCase):
    def _game_dir(self, tmp: Path) -> Path:
        game = tmp / "game"
        (game / "fonts").mkdir(parents=True)
        (game / "fonts" / "GameFont.ttf").write_bytes(fx.build_font_missing_vietnamese())
        (game / "fonts" / "FullFont.ttf").write_bytes(fx.build_font_full_vietnamese())
        (game / "fonts" / "MainFont SDF.ttf").write_bytes(fx.build_font_missing_vietnamese())
        (game / "readme.txt").write_text("khong phai font", encoding="utf-8")
        return game

    def test_scan_dir(self):
        game = self._game_dir(self.tmp)
        scan = ft.scan_fonts(str(game))
        self.assertEqual(len(scan.fonts), 3)
        by_name = {f.family: f for f in scan.fonts}
        self.assertFalse(by_name["GameFont"].is_full)
        self.assertTrue(by_name["DonorFont"].is_full)
        self.assertEqual(len(scan.sdf_suspects), 1)
        self.assertIn("SDF", scan.sdf_suspects[0])

    def test_scan_dir_parent_with_tmp_underscore_not_sdf(self):
        # Hồi quy tích hợp đợt tổng cuối: thư mục cha chứa 'tmp_' (vd TMPDIR
        # .tmp_suite) từng làm mọi font bị nghi SDF oan (fail 3 != 1).
        parent = self.tmp / "tmp_x"
        game = parent / "game"
        (game / "fonts").mkdir(parents=True)
        (game / "fonts" / "GameFont.ttf").write_bytes(fx.build_font_missing_vietnamese())
        (game / "fonts" / "FullFont.ttf").write_bytes(fx.build_font_full_vietnamese())
        (game / "fonts" / "MainFont SDF.ttf").write_bytes(fx.build_font_missing_vietnamese())
        scan = ft.scan_fonts(str(game))
        self.assertEqual(len(scan.fonts), 3)
        self.assertEqual(len(scan.sdf_suspects), 1)
        self.assertIn("SDF", scan.sdf_suspects[0])

    def test_scan_missing_source(self):
        with self.assertRaises(PB3Error) as cm:
            ft.scan_fonts(str(self.tmp / "khong-co"))
        self.assertEqual(cm.exception.code, "E_INPUT_NOT_FOUND")

    def test_scan_zip_entry(self):
        zpath = self.tmp / "game.zip"
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr("fonts/GameFont.ttf", fx.build_font_missing_vietnamese())
            zf.writestr("notes.txt", "hello")
        scan = ft.scan_fonts(str(zpath))
        self.assertEqual(len(scan.fonts), 1)
        self.assertEqual(scan.fonts[0].kind, "zip-entry")
        self.assertFalse(scan.fonts[0].is_full)

    @pytest.mark.needs_fixture("unity/resources.assets")
    def test_scan_unity_assets_without_fonts(self):
        fixture = ROOT / "tests" / "fixtures" / "unity" / "resources.assets"
        scan = ft.scan_fonts(str(fixture))
        self.assertEqual(scan.fonts, [])
        self.assertEqual(scan.warnings, [])

    @pytest.mark.needs_fixture("unity/resources.assets")
    def test_list_unity_font_objects_empty_on_fixture(self):
        from sdk import unity as uni
        data = (ROOT / "tests" / "fixtures" / "unity" / "resources.assets").read_bytes()
        sf = uni.parse_serialized(data)
        self.assertEqual(fs.list_unity_font_objects(sf), [])

    def test_is_sdf_name(self):
        self.assertTrue(fs.is_sdf_name("MainFont SDF"))
        self.assertTrue(fs.is_sdf_name("tmp_atlas"))
        self.assertFalse(fs.is_sdf_name("GameFont"))
        # Chỉ xét basename: đường dẫn cha chứa 'tmp_' không làm font bị nghi oan
        # (hồi quy tích hợp đợt tổng cuối: TMPDIR .tmp_suite làm fail 3 != 1).
        self.assertFalse(fs.is_sdf_name("/a/tmp_x/game/fonts/GameFont.ttf"))
        self.assertTrue(fs.is_sdf_name("/a/dir/MainFont SDF.ttf"))


class TestRebuildReplace(TmpCase):
    def _game_dir(self) -> Path:
        game = self.tmp / "game"
        (game / "fonts").mkdir(parents=True)
        (game / "fonts" / "GameFont.ttf").write_bytes(fx.build_font_missing_vietnamese())
        (game / "fonts" / "OtherFont.ttf").write_bytes(fx.build_font_missing_vietnamese())
        return game

    def test_rebuild_dry_run_writes_nothing(self):
        game = self._game_dir()
        app = _fake_app(self.tmp)
        before = (game / "fonts" / "GameFont.ttf").read_bytes()
        res = ft.rebuild_fonts(str(game), donor_bytes=fx.build_font_full_vietnamese(),
                               app=app)
        self.assertTrue(res.dry_run)
        self.assertEqual(len(res.changed), 2)
        self.assertEqual(res.written, [])
        self.assertEqual((game / "fonts" / "GameFont.ttf").read_bytes(), before)
        self.assertFalse((self.tmp / "out").exists())

    def test_rebuild_write_roundtrip(self):
        game = self._game_dir()
        app = _fake_app(self.tmp)
        out_dir = str(self.tmp / "out1")
        res = ft.rebuild_fonts(str(game), donor_bytes=fx.build_font_full_vietnamese(),
                               app=app, allow_write=True, out_dir=out_dir)
        self.assertEqual(len(res.written), 2)
        for p in res.written:
            data = Path(p).read_bytes()
            self.assertTrue(fs.coverage(data).is_full)
            self.assertEqual(fs.verify_font(data), [])
        self.assertTrue(Path(out_dir).is_dir())

    def test_rebuild_main_font_mode2(self):
        game = self._game_dir()
        app = _fake_app(self.tmp)
        out_dir = str(self.tmp / "out2")
        res = ft.rebuild_fonts(str(game), font_name="GameFont",
                               donor_bytes=fx.build_font_full_vietnamese(),
                               app=app, allow_write=True, out_dir=out_dir)
        changed = {Path(c[0]).name: c for c in res.changed}
        self.assertIn("GameFont.ttf", changed)
        self.assertIn("OtherFont.ttf", changed)
        self.assertIn("gộp +135 glyph", changed["GameFont.ttf"][3])
        self.assertIn("dùng font chính", changed["OtherFont.ttf"][3])
        main_bytes = Path(out_dir, "fonts", "GameFont.ttf").read_bytes()
        other_bytes = Path(out_dir, "fonts", "OtherFont.ttf").read_bytes()
        self.assertEqual(other_bytes, main_bytes)
        self.assertTrue(fs.coverage(main_bytes).is_full)

    def test_rebuild_in_zip_roundtrip(self):
        zpath = self.tmp / "game.zip"
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr("fonts/GameFont.ttf", fx.build_font_missing_vietnamese())
            zf.writestr("data.txt", "giu nguyen")
        app = _fake_app(self.tmp)
        out_dir = str(self.tmp / "outz")
        orig = zpath.read_bytes()
        res = ft.rebuild_fonts(str(zpath), donor_bytes=fx.build_font_full_vietnamese(),
                               app=app, allow_write=True, out_dir=out_dir)
        self.assertEqual(len(res.written), 1)
        self.assertEqual(zpath.read_bytes(), orig)  # nguồn không đổi
        with zipfile.ZipFile(res.written[0]) as zf:
            self.assertEqual(zf.read("data.txt"), b"giu nguyen")
            new_font = zf.read("fonts/GameFont.ttf")
        self.assertTrue(fs.coverage(new_font).is_full)
        self.assertEqual(fs.verify_font(new_font), [])

    def test_replace_rejects_incomplete_font(self):
        game = self._game_dir()
        app = _fake_app(self.tmp)
        with self.assertRaises(PB3Error) as cm:
            ft.replace_fonts(str(game), new_font_bytes=fx.build_font_missing_vietnamese(),
                             app=app)
        self.assertEqual(cm.exception.code, "E_CORRUPT")
        self.assertIn("thiếu", cm.exception.detail)

    def test_replace_and_bundle_write_bundled(self):
        game = self._game_dir()
        app = _fake_app(self.tmp)
        bundled = (ROOT / "fonts" / "BeVietnamPro-Regular.ttf").read_bytes()
        res = ft.replace_fonts(str(game), new_font_bytes=bundled, app=app,
                               allow_write=True, out_dir=str(self.tmp / "out3"))
        self.assertEqual(len(res.written), 2)
        for p in res.written:
            self.assertEqual(fs.parse_font(Path(p).read_bytes()).family, "Be Vietnam Pro")
        res2 = ft.bundle_fonts(str(game), app=app, allow_write=True,
                               out_dir=str(self.tmp / "out4"))
        self.assertEqual(res2.mode, "bundle")
        self.assertTrue(any("Be Vietnam Pro" in w for w in res2.warnings))
        for p in res2.written:
            self.assertEqual(fs.parse_font(Path(p).read_bytes()).family, "Be Vietnam Pro")

    def test_rebuild_progress_monotonic(self):
        game = self._game_dir()
        app = _fake_app(self.tmp)
        seen: List[int] = []
        ft.rebuild_fonts(str(game), donor_bytes=fx.build_font_full_vietnamese(),
                         app=app, progress_fn=lambda d, t: seen.append((d, t)))
        merge_seen = [(d, t) for d, t in seen if t == 270]
        self.assertTrue(merge_seen, "phải có tiến độ gộp theo glyph")
        dones = [d for d, _t in merge_seen]
        self.assertTrue(all(b >= a for a, b in zip(dones, dones[1:])),
                        "tiến độ không được lùi")
        self.assertEqual(dones[-1], 270)
        self.assertTrue(all(d <= 270 for d in dones), "tiến độ không vượt tổng")


class TestCli(TmpCase):
    def _game_dir(self) -> Path:
        game = self.tmp / "game"
        (game / "fonts").mkdir(parents=True)
        (game / "fonts" / "GameFont.ttf").write_bytes(fx.build_font_missing_vietnamese())
        return game

    def test_scan_cli(self):
        game = self._game_dir()
        app = _fake_app(self.tmp)
        rc = ft.run_cli(app, _ns(action="scan", path=str(game)))
        self.assertEqual(rc, 0)

    def test_rebuild_cli_dry_run(self):
        game = self._game_dir()
        donor = self.tmp / "donor.ttf"
        donor.write_bytes(fx.build_font_full_vietnamese())
        app = _fake_app(self.tmp)
        rc = ft.run_cli(app, _ns(action="rebuild", path=str(game),
                                 donor=str(donor), out=str(self.tmp / "o")))
        self.assertEqual(rc, 0)
        self.assertFalse((self.tmp / "o").exists())

    def test_replace_cli_needs_with(self):
        game = self._game_dir()
        app = _fake_app(self.tmp)
        with self.assertRaises(PB3Error) as cm:
            ft.run_cli(app, _ns(action="replace", path=str(game)))
        self.assertEqual(cm.exception.code, "E_CONFIG_INVALID")

    def test_bad_action(self):
        app = _fake_app(self.tmp)
        with self.assertRaises(PB3Error) as cm:
            ft.run_cli(app, _ns(action="xoa", path=str(self.tmp)))
        self.assertEqual(cm.exception.code, "E_CONFIG_INVALID")

    def test_chars_arg(self):
        self.assertEqual(ft._parse_chars_arg("1EA0-1EA2,103"),
                         [0x103, 0x1EA0, 0x1EA1, 0x1EA2])
        self.assertIsNone(ft._parse_chars_arg(None))

    def test_add_arguments_parses(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        font_p = sub.add_parser("font")
        ft.add_arguments(font_p)
        ns = parser.parse_args(["font", "rebuild", "game.zip", "--font", "GameFont",
                                "--allow-write"])
        self.assertEqual(ns.action, "rebuild")
        self.assertEqual(ns.font, "GameFont")
        self.assertTrue(ns.allow_write)

    def test_handlers_entry(self):
        from ui import handlers
        feat = handlers.COMMANDS.get("font")
        self.assertIsNotNone(feat)
        self.assertEqual(feat.module, "services.font_tool")
        self.assertTrue(handlers.module_present(feat))
        mod = handlers.load_module(feat)
        self.assertTrue(callable(getattr(mod, "run_cli", None)))
        self.assertTrue(callable(getattr(mod, "add_arguments", None)))
        self.assertTrue(callable(getattr(mod, "run_interactive", None)))


class TestInteractive(TmpCase):
    def test_mode1_dry_run_then_exit(self):
        game = self.tmp / "game"
        (game / "fonts").mkdir(parents=True)
        (game / "fonts" / "GameFont.ttf").write_bytes(fx.build_font_missing_vietnamese())
        app = _fake_app(self.tmp)
        answers = iter(["1", str(game), ""])
        ft.run_interactive(app, input_fn=lambda _p="": next(answers),
                           say=lambda _s="": None)
        self.assertFalse((self.tmp / "out").exists())

    def test_mode4_write_after_confirm(self):
        game = self.tmp / "game"
        (game / "fonts").mkdir(parents=True)
        (game / "fonts" / "GameFont.ttf").write_bytes(fx.build_font_missing_vietnamese())
        app = _fake_app(self.tmp)
        answers = iter(["4", str(game), "", "y", ""])
        ft.run_interactive(app, input_fn=lambda _p="": next(answers),
                           say=lambda _s="": None)
        out_font = self.tmp / "out" / "game" / "fonts" / "GameFont.ttf"
        self.assertTrue(out_font.is_file())
        self.assertEqual(fs.parse_font(out_font.read_bytes()).family, "Be Vietnam Pro")
