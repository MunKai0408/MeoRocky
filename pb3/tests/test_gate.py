"""Test cong kiem tra truoc khi giao (P0-2, buoc 6, sdk/gate.py).

Fixture tu dung bang builder CO SAN trong repo
(tests/fixtures/unity/build_fixtures.py + tests/fixtures/font/
build_fixtures.py), KHONG them file fixture roi, khong dung file game
that, khong can mang, khong can marker needs_fixture.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests" / "fixtures" / "unity"))
from build_fixtures import (  # noqa: E402
    build_bundle, build_serialized_file, text_asset_data)
# Font builder cung ten file (build_fixtures.py) o thu muc khac — nap
# bang duong dan tuyet doi, khong qua sys.path (tranh dung module unity).
import importlib.util as _importlib_util  # noqa: E402
_spec = _importlib_util.spec_from_file_location(
    "font_build_fixtures",
    str(ROOT / "tests" / "fixtures" / "font" / "build_fixtures.py"))
font_fx = _importlib_util.module_from_spec(_spec)  # noqa: E402
_spec.loader.exec_module(font_fx)  # noqa: E402

from core.errors import PB3Error  # noqa: E402
from core.limits import Limits  # noqa: E402
from sdk import gate  # noqa: E402
from sdk import rebuild as rb  # noqa: E402
from sdk import unity as u  # noqa: E402
from sdk.exporters import placeholders as _exp_placeholders  # noqa: E402
from sdk.translate import placeholders_ok  # noqa: E402

EN = {"hello": "Hello", "play": "Play", "quit": "Quit",
      "title": "9 Kings"}
MENU = {"start": "Start Game", "options": "Options"}
DIALOG = {"greeting": "Welcome, hero!", "farewell": "Goodbye!"}
SRC = dict(EN)
SRC.update(MENU)
SRC.update(DIALOG)
VI = {"hello": "Xin chào", "play": "Chơi", "quit": "Thoát",
      "title": "9 Kings", "start": "Bắt đầu chơi",
      "options": "Tùy chọn", "greeting": "Chào mừng, anh hùng!",
      "farewell": "Tạm biệt!"}
CAB_A = "CAB-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
CAB_B = "CAB-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def _sf(entries):
    objs = []
    for pid, name, val in entries:
        script = val if isinstance(val, str) else json.dumps(
            val, ensure_ascii=False)
        objs.append((pid, 0, text_asset_data(name, script)))
    return build_serialized_file(version=17, objects=objs, types=[(49, None)])


def _bundle(sf_bytes, cab=CAB_A, compression="lz4", **kw):
    return build_bundle(version=7, entries=[(cab, 4, sf_bytes)],
                        compression=compression, **kw)


def _zip_two(bundle_a, bundle_b):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.bundle", bundle_a)
        zf.writestr("b.bundle", bundle_b)
    return buf.getvalue()


def _merge_prov(keys, conflicts=None):
    return {"format": "beartea-merge-provenance/1", "keys": dict(keys),
            "conflicts": conflicts or [],
            "conflicts_total": len(conflicts or [])}


def _prov_all():
    keys = {}
    for k in list(EN) + list(MENU):
        keys[k] = "a.bundle"
    for k in DIALOG:
        keys[k] = "b.bundle"
    return _merge_prov(keys)


def _fixture_zip():
    sf_a = _sf([(1001, "en.json", EN), (1002, "menu.json", MENU)])
    sf_b = _sf([(1003, "dialog.json", DIALOG)])
    return _zip_two(_bundle(sf_a, CAB_A), _bundle(sf_b, CAB_B))


def _full_display():
    return gate.font_entry_from_bytes(
        font_fx.build_font_full_vietnamese(), "DonorFont.ttf")


def _missing_a_font():
    cps = [c for c in font_fx.vietnamese_cps() if c != 0xE0]  # thieu à
    data = font_fx.build_ttf(font_fx.ascii_cps() + cps, name="ThieuA")
    return gate.font_entry_from_bytes(data, "ThieuA.ttf")


def _run(data, translations, **kw):
    kw.setdefault("display_fonts", [_full_display()])
    return gate.run_gate(data, source_name=kw.pop("source_name", "game.zip"),
                         translations=translations, **kw)


def _status(res, check_id):
    for c in res["checks"]:
        if c["id"] == check_id:
            return c["status"]
    raise AssertionError("thieu check %s" % check_id)


def _check(res, check_id):
    for c in res["checks"]:
        if c["id"] == check_id:
            return c
    raise AssertionError("thieu check %s" % check_id)


class TestCompleteness(unittest.TestCase):
    def test_all_present_pass(self):
        r = gate.check_translation_completeness(SRC, VI)
        self.assertEqual(r["status"], "pass")

    def test_missing_key_fail(self):
        tr = dict(VI)
        del tr["quit"]
        r = gate.check_translation_completeness(SRC, tr)
        self.assertEqual(r["status"], "fail")
        self.assertIn("quit", [f["key"] for f in r["failures"]])

    def test_missing_allowed_pass(self):
        tr = dict(VI)
        del tr["quit"]
        r = gate.check_translation_completeness(
            SRC, tr, allowed_missing=["quit"])
        self.assertEqual(r["status"], "pass")
        self.assertEqual(r["counts"]["missing_excused"], 1)

    def test_empty_value_fail(self):
        tr = dict(VI)
        tr["play"] = ""
        r = gate.check_translation_completeness(SRC, tr)
        self.assertEqual(r["status"], "fail")

    def test_empty_allowed_pass(self):
        tr = dict(VI)
        tr["play"] = ""
        r = gate.check_translation_completeness(
            SRC, tr, allowed_empty=["play"])
        self.assertEqual(r["status"], "pass")

    def test_whitespace_only_fail(self):
        tr = dict(VI)
        tr["play"] = "   "
        r = gate.check_translation_completeness(SRC, tr)
        self.assertEqual(r["status"], "fail")

    def test_source_empty_faithful_ok(self):
        r = gate.check_translation_completeness(
            {"a": "", "b": "Hi"}, {"a": "", "b": "Chào"})
        self.assertEqual(r["status"], "pass")

    def test_extra_key_fail_and_allowed(self):
        tr = dict(VI)
        tr["ghost"] = "Ma"
        r = gate.check_translation_completeness(SRC, tr)
        self.assertEqual(r["status"], "fail")
        r2 = gate.check_translation_completeness(
            SRC, tr, allowed_extra=["ghost"])
        self.assertEqual(r2["status"], "pass")

    def test_no_source_fail_closed(self):
        r = gate.check_translation_completeness(None, VI)
        self.assertEqual(r["status"], "fail")

    def test_partial_mode_missing_not_fail(self):
        tr = {"hello": "Xin chào"}
        r = gate.check_translation_completeness(
            SRC, tr, allow_partial=True)
        self.assertEqual(r["status"], "pass")
        self.assertTrue(r["counts"]["allow_partial"])
        r2 = gate.check_translation_completeness(SRC, tr)
        self.assertEqual(r2["status"], "fail")

    def test_unchanged_is_warning_not_fail(self):
        r = gate.check_translation_completeness(SRC, VI)
        self.assertEqual(r["status"], "pass")
        self.assertEqual(r["counts"]["unchanged"], 1)  # title giong goc
        self.assertTrue(r["warnings"])


class TestPlaceholders(unittest.TestCase):
    SRC_PH = {"a": "Hello {0}, you have %d coins",
              "b": "Line1\nLine2", "c": "<color=red>Hi</color> %s",
              "d": "Score: %i", "e": "Buy $item now"}

    def test_preserved_pass(self):
        tr = {"a": "Chào {0}, bạn có %d xu", "b": "Dòng1\nDòng2",
              "c": "<color=red>Chào</color> %s", "d": "Điểm: %i",
              "e": "Mua $item ngay"}
        r = gate.check_placeholders(self.SRC_PH, tr)
        self.assertEqual(r["status"], "pass")

    def test_dropped_placeholder_fail(self):
        tr = {"a": "Chào, bạn có %d xu", "b": "Dòng1\nDòng2",
              "c": "<color=red>Chào</color> %s", "d": "Điểm: %i",
              "e": "Mua $item ngay"}
        r = gate.check_placeholders(self.SRC_PH, tr)
        self.assertEqual(r["status"], "fail")
        self.assertEqual(r["failures"][0]["key"], "a")

    def test_dropped_tag_and_percent_fail(self):
        tr = dict(self.SRC_PH)
        tr["c"] = "Chào"
        r = gate.check_placeholders(self.SRC_PH, tr)
        self.assertEqual(r["status"], "fail")

    def test_newline_change_fail(self):
        tr = dict(self.SRC_PH)
        tr["b"] = "Dòng1 Dòng2"
        r = gate.check_placeholders(self.SRC_PH, tr)
        self.assertEqual(r["status"], "fail")
        self.assertEqual(r["failures"][0]["source_newlines"], 1)
        self.assertEqual(r["failures"][0]["translated_newlines"], 0)

    def test_duplicate_placeholder_stricter_than_placeholders_ok(self):
        # placeholders_ok (dedup) cho qua, cong (thu tu + so lan) FAIL.
        src, got = "A {0} B {0}", "A {0} B"
        self.assertTrue(placeholders_ok(src, got))
        self.assertEqual(_exp_placeholders(src), _exp_placeholders(got))
        r = gate.check_placeholders({"k": src}, {"k": got})
        self.assertEqual(r["status"], "fail")

    def test_reordered_placeholders_fail(self):
        r = gate.check_placeholders({"k": "{0} {1}"}, {"k": "{1} {0}"})
        self.assertEqual(r["status"], "fail")

    def test_allowed_key_pass(self):
        r = gate.check_placeholders({"k": "Hi {0}"}, {"k": "Chào"},
                                    allowed=["k"])
        self.assertEqual(r["status"], "pass")
        self.assertEqual(r["counts"]["allowed_skipped"], 1)

    def test_no_source_fail_closed(self):
        r = gate.check_placeholders(None, {"k": "v"})
        self.assertEqual(r["status"], "fail")


class TestFonts(unittest.TestCase):
    def test_display_full_pass(self):
        r = gate.check_font_coverage(VI, [], display_fonts=[_full_display()])
        self.assertEqual(r["status"], "pass")

    def test_display_missing_used_char_fail_lists_char(self):
        r = gate.check_font_coverage(
            {"k": "Xin chào à"}, [], display_fonts=[_missing_a_font()])
        self.assertEqual(r["status"], "fail")
        self.assertIn("à", r["failures"][0]["missing_chars"])

    def test_scanned_readable_missing_used_fail(self):
        entry = _missing_a_font()
        entry["source"] = "game"
        r = gate.check_font_coverage({"k": "à"}, [entry])
        self.assertEqual(r["status"], "fail")

    def test_scanned_missing_only_unused_pass(self):
        # Font thieu à nhung ban dich khong dung à.
        entry = _missing_a_font()
        r = gate.check_font_coverage({"k": "Chơi game"}, [entry])
        self.assertEqual(r["status"], "pass")

    def test_no_fonts_fail(self):
        r = gate.check_font_coverage({"k": "Xin chào"}, [])
        self.assertEqual(r["status"], "fail")

    def test_unreadable_only_fail(self):
        entry = {"name": "GameFont", "kind": "unity_font",
                 "source": "resources.assets", "status": "unreadable",
                 "covered": None, "total": 142, "missing_codepoints": [],
                 "reason": "không đọc được bytes font"}
        r = gate.check_font_coverage({"k": "Xin chào"}, [entry])
        self.assertEqual(r["status"], "fail")
        self.assertTrue(any("chưa kiểm được" in w for w in r["warnings"]))

    def test_unreadable_plus_readable_pass_warns(self):
        good = _full_display()
        bad = {"name": "GameFont", "kind": "unity_font",
               "source": "resources.assets", "status": "unreadable",
               "covered": None, "total": 142, "missing_codepoints": [],
               "reason": "không đọc được bytes font"}
        r = gate.check_font_coverage(VI, [good, bad])
        self.assertEqual(r["status"], "pass")
        self.assertEqual(r["counts"]["unreadable"], 1)
        self.assertTrue(r["warnings"])

    def test_no_vietnamese_chars_pass(self):
        r = gate.check_font_coverage({"k": "Hello world 123"}, [])
        self.assertEqual(r["status"], "pass")

    def test_font_entry_from_real_synthetic_ttf(self):
        full = gate.font_entry_from_bytes(
            font_fx.build_font_full_vietnamese(), "Full.ttf")
        self.assertEqual(full["status"], "full")
        self.assertEqual(full["covered"], 142)
        missing = gate.font_entry_from_bytes(
            font_fx.build_font_missing_vietnamese(), "Game.ttf")
        self.assertEqual(missing["status"], "partial")
        self.assertIn(0xE0, missing["missing_codepoints"])

    def test_font_entry_from_ttc_face(self):
        # [ĐÃ ĐO THẬT] cổng đọc đúng face của .ttc (face 0 thiếu, face 1 đủ);
        # không chọn face thì cổng từ chối rõ ràng.
        ttc = font_fx.build_ttc([
            font_fx.build_font_missing_vietnamese(),
            font_fx.build_font_full_vietnamese()])
        face0 = gate.font_entry_from_bytes(ttc, "Game.ttc", face_index=0)
        self.assertEqual(face0["status"], "partial")
        self.assertEqual(face0["face_index"], 0)
        face1 = gate.font_entry_from_bytes(ttc, "Game.ttc", face_index=1)
        self.assertEqual(face1["status"], "full")
        self.assertEqual(face1["covered"], 142)
        with self.assertRaises(PB3Error):
            gate.font_entry_from_bytes(ttc, "Game.ttc")

    def test_tmp_style_entry(self):
        entry = {"name": "TMP Font", "kind": "tmp_font_asset",
                 "source": "x.assets", "status": "partial",
                 "covered": 141, "total": 142,
                 "missing_codepoints": [0x1EB9], "reason": ""}
        r = gate.check_font_coverage({"k": "ẹ"}, [entry])
        self.assertEqual(r["status"], "fail")
        r2 = gate.check_font_coverage({"k": "à"}, [entry])
        self.assertEqual(r2["status"], "pass")


class TestGatePreWrite(unittest.TestCase):
    def test_pass_all_with_provenance(self):
        res = _run(_fixture_zip(), VI, source_texts=SRC,
                   provenance=_prov_all())
        self.assertEqual(res["verdict"], "PASS")
        self.assertTrue(res["delivery_allowed"])
        self.assertEqual(res["mode"], "pre_write")
        self.assertEqual([c["status"] for c in res["checks"]],
                         ["pass"] * 5)
        self.assertEqual(res["totals"]["changed"], 7)  # title giu nguyen
        self.assertEqual(res["totals"]["units"], 2)
        self.assertNotIn("dest", res)
        self.assertNotIn("zip_path", res)
        self.assertEqual(res["translations_sha256"],
                         gate.canonical_translations_sha(VI))

    def test_pass_scan_all_no_provenance(self):
        res = _run(_fixture_zip(), VI, source_texts=SRC)
        self.assertEqual(res["verdict"], "PASS")
        self.assertTrue(res["scan_all"])

    def test_fail_missing_key(self):
        tr = dict(VI)
        del tr["quit"]
        res = _run(_fixture_zip(), tr, source_texts=SRC,
                   provenance=_prov_all())
        self.assertEqual(res["verdict"], "FAIL")
        self.assertFalse(res["delivery_allowed"])
        self.assertEqual(_status(res, "translation_complete"), "fail")
        self.assertTrue(res["errors"])

    def test_fail_placeholder(self):
        src = dict(SRC)
        src["hello"] = "Hello {0}"
        tr = dict(VI)
        tr["hello"] = "Xin chào"
        res = _run(_fixture_zip(), tr, source_texts=src,
                   provenance=_prov_all())
        self.assertEqual(res["verdict"], "FAIL")
        self.assertEqual(_status(res, "placeholders"), "fail")

    def test_fail_unmatched_not_found(self):
        src = dict(SRC)
        src["ghost"] = "Boo"
        tr = dict(VI)
        tr["ghost"] = "Ma"
        res = _run(_fixture_zip(), tr, source_texts=src,
                   provenance=_prov_all())
        self.assertEqual(res["verdict"], "FAIL")
        dv = _check(res, "diffverify")
        self.assertEqual(dv["status"], "fail")
        self.assertEqual(dv["unmatched_by_reason"].get("not_found"), 1)

    def test_fail_no_source(self):
        res = _run(_fixture_zip(), VI, provenance=_prov_all())
        self.assertEqual(res["verdict"], "FAIL")
        self.assertEqual(_status(res, "translation_complete"), "fail")
        self.assertEqual(_status(res, "placeholders"), "fail")

    def test_fail_font(self):
        res = _run(_fixture_zip(), VI, source_texts=SRC,
                   provenance=_prov_all(), display_fonts=[_missing_a_font()])
        self.assertEqual(res["verdict"], "FAIL")
        self.assertEqual(_status(res, "fonts"), "fail")

    def test_fail_no_changes(self):
        res = _run(_fixture_zip(), dict(SRC), source_texts=SRC,
                   provenance=_prov_all())
        self.assertEqual(res["verdict"], "FAIL")
        self.assertEqual(_status(res, "diffverify"), "fail")

    def test_v9_passes(self):
        # [ĐÃ ĐO THẬT] bundle v9 nay ghi được (M-formats): cổng PASS như v7.
        # Chỉ dịch đúng 4 key có trong bundle (EN) — VI đầy đủ 8 key của
        # fixture zip 2 đơn vị sẽ thành 4 key unmatched, cổng FAIL đúng luật.
        sf = _sf([(1001, "en.json", EN)])
        bundle = build_bundle(version=9, entries=[(CAB_A, 4, sf)],
                              compression="none")
        tr = {k: VI[k] for k in EN}
        res = _run(bundle, tr, source_texts=EN, source_name="v9.unity3d")
        self.assertEqual(res["verdict"], "PASS")
        self.assertEqual(res["refused"], [])

    def test_refused_encrypted(self):
        sf = _sf([(1001, "en.json", EN)])
        bundle = build_bundle(version=7, entries=[(CAB_A, 4, sf)],
                              compression="none", extra_data_flags=0x200)
        res = _run(bundle, VI, source_texts=SRC, source_name="enc.unity3d")
        self.assertEqual(res["verdict"], "FAIL")
        codes = [r.get("code") for r in res["refused"]]
        self.assertIn("E_ENCRYPTED", codes)

    def test_invalid_inputs_raise(self):
        with self.assertRaises(ValueError):
            _run(_fixture_zip(), {})
        with self.assertRaises(ValueError):
            _run(_fixture_zip(), VI, source_texts=SRC,
                 provenance={"format": "la/1"})
        with self.assertRaises(ValueError):
            _run(_fixture_zip(), VI, source_texts=SRC,
                 allowlist={"allowed_empty": "quit"})

    def test_preview_mismatch_fails_integrity(self):
        fake = {"totals": {"units": 99, "changed": 99, "unmatched": 99,
                           "refused": 99}}
        with mock.patch.object(rb, "build_rebuild_preview",
                               return_value=fake):
            res = _run(_fixture_zip(), VI, source_texts=SRC,
                       provenance=_prov_all())
        self.assertEqual(_status(res, "integrity"), "fail")
        self.assertEqual(res["verdict"], "FAIL")

    def test_gate_writes_nothing(self):
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                _run(_fixture_zip(), VI, source_texts=SRC,
                     provenance=_prov_all())
                self.assertEqual(os.listdir(tmp), [])
            finally:
                os.chdir(cwd)


class TestGateMonoBehaviour(unittest.TestCase):
    def _mb_sf(self):
        def _aligned(text):
            raw = text.encode("utf-8")
            return struct.pack("<i", len(raw)) + raw + b"\x00" * ((-len(raw)) % 4)

        mb_raw = b"\x00" * 28 + _aligned("MyMB") + _aligned("Hello MB World")
        return build_serialized_file(
            version=17,
            objects=[(1001, 0, text_asset_data(
                "en.json", json.dumps({"other": "zzz"},
                                      ensure_ascii=False))),
                     (2001, 1, mb_raw)],
            types=[(49, None), (114, b"\x00" * 16)])

    def _mb_key(self, sf_bytes):
        sf = u.parse_serialized(sf_bytes, Limits(), where="t")
        terms = rb._collect_mb_keys([sf])
        self.assertTrue(terms)
        return sorted(terms)[0]

    def _mb_sf_ambiguous(self):
        # 2 object cung ten "MyMB", moi object 1 chuoi o 1 ngon ngu khac
        # nhau: key "MyMB.s1" anh xa 2 vi tri khac nhau -> mo ho, khong
        # co duong ghi (fail-closed) — de cong van bat unmatched.
        def _aligned(text):
            raw = text.encode("utf-8")
            return struct.pack("<i", len(raw)) + raw + b"\x00" * ((-len(raw)) % 4)

        mb_en = b"\x00" * 28 + _aligned("MyMB") + _aligned(
            "Press start to play the game now")
        mb_vi = (b"\x00" * 28 + _aligned("MyMB")
                 + _aligned("Tôi yêu tiếng Việt rất nhiều"))
        return build_serialized_file(
            version=17,
            objects=[(1001, 0, text_asset_data(
                "en.json", json.dumps({"other": "zzz"},
                                      ensure_ascii=False))),
                     (2001, 1, mb_en),
                     (2002, 1, mb_vi)],
            types=[(49, None), (114, b"\x00" * 16)])

    def test_mb_unmatched_fails_with_reason(self):
        # mb-write-rest: key generic MO HO (>1 vi tri) van khong ep
        # duoc -> cong FAIL voi reason monobehaviour.
        sf_bytes = self._mb_sf_ambiguous()
        key = self._mb_key(sf_bytes)
        self.assertEqual(key, "MyMB.s1")
        src = {"other": "zzz", key: key}
        tr = {"other": "zzz đã dịch", key: "[VI] " + key}
        res = _run(sf_bytes, tr, source_texts=src,
                   source_name="t.assets")
        self.assertEqual(res["verdict"], "FAIL")
        dv = _check(res, "diffverify")
        self.assertEqual(dv["unmatched_by_reason"].get("monobehaviour"), 1)
        self.assertIn("MonoBehaviour", json.dumps(res, ensure_ascii=False))

    def test_mb_generic_pressable_passes(self):
        # mb-write-rest: key generic KHONG mo ho gio ep duoc qua scan-path
        # -> khong con unmatched -> cong PASS.
        sf_bytes = self._mb_sf()
        key = self._mb_key(sf_bytes)
        src = {"other": "zzz", key: key}
        tr = {"other": "zzz đã dịch", key: "[VI] " + key}
        res = _run(sf_bytes, tr, source_texts=src,
                   source_name="t.assets")
        self.assertEqual(res["verdict"], "PASS")
        dv = _check(res, "diffverify")
        self.assertEqual(dv["unmatched_by_reason"], {})

    def test_mb_unchanged_key_passes(self):
        # 106-key 9K (run 44522190372): key MB ma ban dich == gia tri
        # goc thi khong can ep -> unchanged, khong phai unmatched ->
        # cong PASS. Hoi quy cho _build_plans khong truyen mb_values
        # (cong FAIL oan + lech voi ban xem truoc cua chinh no).
        sf_bytes = self._mb_sf()
        key = self._mb_key(sf_bytes)
        self.assertEqual(key, "MyMB.s1")
        src = {"other": "zzz", key: key}
        tr = {"other": "zzz đã dịch", key: "Hello MB World"}
        res = _run(sf_bytes, tr, source_texts=src,
                   source_name="t.assets")
        self.assertEqual(res["verdict"], "PASS")
        dv = _check(res, "diffverify")
        self.assertEqual(dv["unmatched_by_reason"], {})
        self.assertEqual(res["totals"]["unmatched"], 0)
        self.assertEqual(res["totals"]["unchanged"], 1)

    def test_mb_unmatched_allowed_passes(self):
        # Key mo ho khong ep duoc nhung nam trong allowlist -> PASS va
        # allowlist duoc tinh la da dung 1 lan.
        sf_bytes = self._mb_sf_ambiguous()
        key = self._mb_key(sf_bytes)
        src = {"other": "zzz", key: key}
        tr = {"other": "zzz đã dịch", key: "[VI] " + key}
        res = _run(sf_bytes, tr, source_texts=src,
                   source_name="t.assets",
                   allowlist={"allowed_unpressed": [key]})
        self.assertEqual(res["verdict"], "PASS")
        self.assertEqual(res["allowlist_used"]["counts"]["allowed_unpressed"], 1)


class TestGatePressed(unittest.TestCase):
    def _apply(self, data, translations, provenance=None):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = os.path.join(tmp, "out")
            zip_path = os.path.join(tmp, "rebuild.zip")
            res = rb.apply_rebuild(
                data, source_name="game.zip", translations=translations,
                provenance=provenance, dry_run=False, confirm=True,
                out_dir=out_dir, zip_path=zip_path)
            if res.get("dest"):
                with open(res["dest"], "rb") as fh:
                    return fh.read(), res
            with open(res["zip_path"], "rb") as fh:
                return fh.read(), res

    def test_pressed_single_unit_pass(self):
        sf = _sf([(1001, "en.json", EN), (1002, "menu.json", MENU)])
        bundle = _bundle(sf, CAB_A, compression="none")
        src = dict(EN)
        src.update(MENU)
        tr = {k: VI[k] for k in src}
        pressed, _res = self._apply(bundle, tr)
        res = _run(bundle, tr, source_texts=src, pressed_bytes=pressed,
                   pressed_name="unit.unity3d", source_name="unit.unity3d")
        self.assertEqual(res["verdict"], "PASS")
        self.assertEqual(res["mode"], "pressed")

    def test_pressed_zip_of_units_pass(self):
        pressed, _res = self._apply(_fixture_zip(), VI, _prov_all())
        res = _run(_fixture_zip(), VI, source_texts=SRC,
                   provenance=_prov_all(), pressed_bytes=pressed,
                   pressed_name="rebuild.zip")
        self.assertEqual(res["verdict"], "PASS")

    def test_pressed_equals_original_fail(self):
        bundle = _bundle(_sf([(1001, "en.json", EN)]), CAB_A,
                         compression="none")
        res = _run(bundle, VI, source_texts=SRC, pressed_bytes=bundle,
                   pressed_name="same.unity3d", source_name="unit.unity3d")
        self.assertEqual(res["verdict"], "FAIL")
        self.assertEqual(_status(res, "diffverify"), "fail")

    def test_pressed_wrong_value_fail(self):
        sf = _sf([(1001, "en.json", EN), (1002, "menu.json", MENU)])
        bundle = _bundle(sf, CAB_A, compression="none")
        src = dict(EN)
        src.update(MENU)
        tr = {k: VI[k] for k in src}
        wrong = dict(tr)
        wrong["hello"] = "Sai rồi"
        pressed, _res = self._apply(bundle, wrong)
        res = _run(bundle, tr, source_texts=src, pressed_bytes=pressed,
                   pressed_name="unit.unity3d", source_name="unit.unity3d")
        self.assertEqual(res["verdict"], "FAIL")
        dv = _check(res, "diffverify")
        kinds = [m.get("kind") for m in dv["failures"]]
        self.assertIn("value_mismatch", kinds)

    def test_pressed_extra_change_fail(self):
        sf = _sf([(1001, "en.json", EN), (1002, "menu.json", MENU)])
        bundle = _bundle(sf, CAB_A, compression="none")
        src = dict(EN)
        src.update(MENU)
        tr = {k: VI[k] for k in src}  # title giu nguyen "9 Kings"
        tampered = dict(tr)
        tampered["title"] = "9 vị vua lén đổi"
        pressed, _res = self._apply(bundle, tampered)
        res = _run(bundle, tr, source_texts=src, pressed_bytes=pressed,
                   pressed_name="unit.unity3d", source_name="unit.unity3d")
        self.assertEqual(res["verdict"], "FAIL")
        dv = _check(res, "diffverify")
        kinds = [m.get("kind") for m in dv["failures"]]
        self.assertIn("extra_change", kinds)

    def test_pressed_unit_not_found_fail(self):
        sf = _sf([(1001, "en.json", EN)])
        bundle = _bundle(sf, CAB_A, compression="none")
        other = _bundle(_sf([(9001, "khac.json", {"x": "y"})]),
                        "CAB-cccccccccccccccccccccccccccccccc",
                        compression="none")
        res = _run(bundle, VI, source_texts=SRC, pressed_bytes=other,
                   pressed_name="other.unity3d", source_name="unit.unity3d")
        self.assertEqual(res["verdict"], "FAIL")

    def test_pressed_extra_object_fails_integrity(self):
        sf = _sf([(1001, "en.json", EN)])
        bundle = _bundle(sf, CAB_A, compression="none")
        tr = {k: VI[k] for k in EN}
        pressed_ok, _res = self._apply(bundle, tr)
        # Chen them 1 object TextAsset vao SF da ep (noi dung giong).
        be = u.UnityBackend("NATIVE")
        b = u.parse_bundle(pressed_ok, be, Limits())
        sf_pressed = u.parse_serialized(b.get_entry(CAB_A), Limits())
        scripts = {n: s for _p, n, s in sf_pressed.text_assets()}
        tampered_sf = _sf([(1001, "en.json", json.loads(scripts["en.json"])),
                           (1002, "extra.json", {"z": "z"})])
        tampered = _bundle(tampered_sf, CAB_A, compression="none")
        res = _run(bundle, tr, source_texts=EN, pressed_bytes=tampered,
                   pressed_name="unit.unity3d", source_name="unit.unity3d")
        self.assertEqual(res["verdict"], "FAIL")
        self.assertEqual(_status(res, "integrity"), "fail")

    def test_same_path_fails_integrity(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "game.zip")
            with open(p, "wb") as fh:
                fh.write(_fixture_zip())
            res = _run(_fixture_zip(), VI, source_texts=SRC,
                       provenance=_prov_all(),
                       pressed_bytes=b"pressed", pressed_name="game.zip",
                       src_path=p, pressed_path=p)
        self.assertEqual(_status(res, "integrity"), "fail")
        kinds = [f.get("kind") for f in _check(res, "integrity")["failures"]]
        self.assertIn("same_path", kinds)


class TestCheckTmpSdf(unittest.TestCase):
    """Kiem tra atlas SDF tai tao (gate.check_tmp_sdf) — cau truc bang +
    atlas, khong do MAE."""

    @staticmethod
    def _glyphs_old():
        return [
            {"cp": 65, "metrics": (10.0, 12.0, 0.0, 12.0, 11.0),
             "rect": (0, 0, 10, 12)},
            {"cp": 32, "metrics": (0.0, 0.0, 0.0, 0.0, 5.0),
             "rect": (0, 0, 0, 0)},
        ]

    @staticmethod
    def _glyphs_new():
        return [
            {"gid": 4, "cp": 33, "metrics": (10.0, 12.0, 0.0, 12.0, 11.0),
             "rect": (0, 0, 10, 12)},
            {"gid": 3, "cp": 32, "metrics": (0.0, 0.0, 0.0, 0.0, 5.0),
             "rect": (0, 0, 0, 0)},
        ]

    def test_old_layout_pass(self):
        from sdk import sdf as S
        table = S.encode_glyph_table_old(self._glyphs_old())
        res = gate.check_tmp_sdf(table, "old", bytes(64 * 64), 64, 64,
                                 expected_count=2)
        self.assertEqual(res["status"], "pass")
        self.assertEqual(res["counts"]["glyphs"], 2)
        self.assertEqual(res["counts"]["placeholders"], 1)

    def test_new_layout_pass(self):
        from sdk import sdf as S
        table = S.encode_glyph_table_new(self._glyphs_new())
        res = gate.check_tmp_sdf(table, "new", bytes(64 * 64), 64, 64,
                                 expected_count=2)
        self.assertEqual(res["status"], "pass")

    def test_atlas_size_fail(self):
        from sdk import sdf as S
        table = S.encode_glyph_table_old(self._glyphs_old())
        res = gate.check_tmp_sdf(table, "old", bytes(10), 64, 64)
        self.assertEqual(res["status"], "fail")
        kinds = [f.get("kind") for f in res["failures"]]
        self.assertIn("atlas_size", kinds)

    def test_rect_out_of_bounds_fail(self):
        from sdk import sdf as S
        glyphs = [{"cp": 65, "metrics": (10.0, 12.0, 0.0, 12.0, 11.0),
                   "rect": (60, 60, 10, 12)}]
        table = S.encode_glyph_table_old(glyphs)
        res = gate.check_tmp_sdf(table, "old", bytes(64 * 64), 64, 64)
        self.assertEqual(res["status"], "fail")
        kinds = [f.get("kind") for f in res["failures"]]
        self.assertIn("rect_out_of_bounds", kinds)

    def test_count_mismatch_fail(self):
        from sdk import sdf as S
        table = S.encode_glyph_table_old(self._glyphs_old())
        res = gate.check_tmp_sdf(table, "old", bytes(64 * 64), 64, 64,
                                 expected_count=5)
        self.assertEqual(res["status"], "fail")

    def test_bad_layout_fail(self):
        res = gate.check_tmp_sdf(b"\x00" * 10, "bogus", bytes(64 * 64),
                                 64, 64)
        self.assertEqual(res["status"], "fail")


if __name__ == "__main__":
    unittest.main()
