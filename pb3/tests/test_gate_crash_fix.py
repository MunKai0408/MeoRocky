"""Test sua sap tab khi bam cong kiem tra / ep file (gate-crash-fix).

- Fail-fast: (a)/(b) da FAIL thi bo qua mo phong nang (preview + lap
  plan + verify), verdict van FAIL, (c)/(e) ghi "fail" kem ly do
  skipped_simulation, ket qua them co simulation_skipped.
- Quet nhe: _scan_units_derived tra du lieu dan xuat giong he duong cu
  (giu SF cua tat ca don vi) — so sanh truc tiep tren fixture 2 don vi.
- Pre-flight cap (bridge): file vuot ngan sach RAM thi tu choi lich su
  bang loi tieng Viet, khong chay mo phong; file vua thi chay binh thuong.

Fixture tu dung bang builder CO SAN trong repo
(tests/fixtures/unity/build_fixtures.py + tests/fixtures/font/
build_fixtures.py), KHONG them file fixture roi, khong dung file game
that, khong can mang.
"""
from __future__ import annotations

import io
import json
import os
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
import importlib.util as _importlib_util  # noqa: E402
_spec = _importlib_util.spec_from_file_location(
    "font_build_fixtures",
    str(ROOT / "tests" / "fixtures" / "font" / "build_fixtures.py"))
font_fx = _importlib_util.module_from_spec(_spec)  # noqa: E402
_spec.loader.exec_module(font_fx)  # noqa: E402

import bridge  # noqa: E402
from core.limits import Limits  # noqa: E402
from sdk import gate  # noqa: E402
from sdk import plan as _plan  # noqa: E402
from sdk import rebuild as rb  # noqa: E402

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


def _run(data, translations, **kw):
    kw.setdefault("display_fonts", [_full_display()])
    return gate.run_gate(data, source_name=kw.pop("source_name", "game.zip"),
                         translations=translations, **kw)


def _check(res, check_id):
    for c in res["checks"]:
        if c["id"] == check_id:
            return c
    raise AssertionError("thieu check %s" % check_id)


def _no_simulation():
    """Mock cac buoc mo phong nang: goi toi la loi test."""
    return (
        mock.patch.object(rb, "build_rebuild_preview",
                          side_effect=AssertionError(
                              "fail-fast phai bo qua preview")),
        mock.patch.object(gate, "_build_plans",
                          side_effect=AssertionError(
                              "fail-fast phai bo qua _build_plans")),
        mock.patch.object(gate, "_verify_pressed_units",
                          side_effect=AssertionError(
                              "fail-fast phai bo qua verify")),
    )


class TestFailFast(unittest.TestCase):
    def test_no_source_skips_simulation(self):
        data = _fixture_zip()
        p1, p2, p3 = _no_simulation()
        with p1, p2, p3:
            res = _run(data, VI, source_texts=None,
                       provenance=_prov_all())
        self.assertEqual(res["verdict"], "FAIL")
        self.assertTrue(res.get("simulation_skipped"))
        self.assertEqual(_check(res, "translation_complete")["status"],
                         "fail")
        # (c)/(e) fail-closed kem ly do, khong co trang thai "skip".
        c = _check(res, "diffverify")
        self.assertEqual(c["status"], "fail")
        self.assertTrue(any(
            (f.get("kind") == "skipped_simulation")
            for f in (c.get("failures") or [])))
        e = _check(res, "integrity")
        self.assertEqual(e["status"], "fail")
        self.assertTrue(any(
            (f.get("kind") == "skipped_simulation")
            for f in (e.get("failures") or [])))
        # (d) font van chay binh thuong (font day du -> pass).
        self.assertEqual(_check(res, "fonts")["status"], "pass")
        # conflicts/warnings van that (group_by_unit van chay).
        self.assertEqual(res["totals"]["keys"], len(VI))

    def test_placeholder_error_skips_simulation(self):
        sf = _sf([(1001, "greet.json", {"greet": "Hello {name}!"})])
        data = _bundle(sf, CAB_A)
        p1, p2, p3 = _no_simulation()
        with p1, p2, p3:
            res = _run(data, {"greet": "Xin chào!"},
                       source_texts={"greet": "Hello {name}!"})
        self.assertEqual(res["verdict"], "FAIL")
        self.assertTrue(res.get("simulation_skipped"))
        self.assertEqual(_check(res, "placeholders")["status"], "fail")

    def test_normal_path_still_simulates(self):
        data = _fixture_zip()
        res = _run(data, VI, source_texts=SRC, provenance=_prov_all())
        self.assertFalse(res.get("simulation_skipped"))
        self.assertEqual(res["verdict"], "PASS")
        self.assertEqual(_check(res, "diffverify")["status"], "pass")
        self.assertEqual(_check(res, "integrity")["status"], "pass")
        # "title": "9 Kings" dich bang goc nen unchanged, con lai 7 doi.
        self.assertEqual(res["totals"]["changed"], len(SRC) - 1)
        self.assertEqual(res["totals"]["unchanged"], 1)
        self.assertEqual(res["totals"]["verified_keys"], len(SRC) - 1)


class TestLightScan(unittest.TestCase):
    def test_scan_derived_matches_direct(self):
        """Quet nhe tra du lieu giong he duong cu giu SF."""
        data = _fixture_zip()
        lim = Limits()
        scan = rb._scan_units_derived(data, None, lim, "en", {})
        # Duong truc tiep (cu): mo SF cua moi don vi, gop bang tay.
        units = rb._enumerate_units(data, None, lim)
        self.assertEqual({v for v, _k in units}, set(scan["seen"]))
        all_sfs = []
        for vpath, _kind in units:
            all_sfs.extend(rb._unit_sfs(data, vpath, None, lim))
        mb_index = _plan.build_mb_shared_index(all_sfs)
        self.assertEqual(scan["mb_index"], mb_index)
        mb_terms, mb_values = rb._collect_mb_info(
            all_sfs, source_locale="en", mb_index=mb_index)
        self.assertEqual(scan["mb_terms"], mb_terms)
        self.assertEqual(scan["mb_values"], mb_values)
        # text_values: gop TextAsset JSON dict, first-wins theo thu tu SF.
        direct_text = {}
        direct_nondict = []
        for sf in all_sfs:
            for _pid, _name, script in sf.text_assets():
                try:
                    obj = json.loads(script)
                except (ValueError, UnicodeDecodeError):
                    continue
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        direct_text.setdefault(k, v)
                else:
                    direct_nondict.append(script)
        self.assertEqual(scan["text_values"], direct_text)
        self.assertEqual(scan["nondict_scripts"], direct_nondict)

    def test_scan_add_unit_idempotent_and_safe(self):
        data = _fixture_zip()
        lim = Limits()
        scan = rb._scan_units_derived(data, None, lim, "en", {})
        before = (dict(scan["text_values"]), set(scan["mb_terms"]),
                  set(scan["seen"]))
        # Don vi da co (vpath day du): khong doi gi.
        rb._scan_add_unit(scan, data, "a.bundle::" + CAB_A, None, lim, "en")
        self.assertEqual(dict(scan["text_values"]), before[0])
        self.assertEqual(set(scan["mb_terms"]), before[1])
        self.assertEqual(set(scan["seen"]), before[2])
        # Don vi khong ton tai: khong crash, du lieu dan xuat khong doi
        # (chi ghi nhan da thu — seen co them khoa bogus la chap nhan duoc).
        rb._scan_add_unit(scan, data, "nope.bundle", None, lim, "en")
        self.assertEqual(dict(scan["text_values"]), before[0])
        self.assertEqual(set(scan["mb_terms"]), before[1])

    def test_preview_totals_unchanged_on_fixture(self):
        """Preview tren fixture 2 don vi: tong giong truoc khi sua
        (2252 key cua 9K duoc kiem bang do that tren VM, khong phai o day)."""
        data = _fixture_zip()
        prev = rb.build_rebuild_preview(
            data, source_name="game.zip", translations=VI)
        # "title": "9 Kings" dich bang goc nen unchanged, con lai 7 doi.
        self.assertEqual(prev["totals"]["changed"], len(SRC) - 1)
        self.assertEqual(prev["totals"]["unmatched"], 0)
        self.assertEqual(prev["totals"]["unchanged"], 1)
        self.assertEqual(prev["totals"]["refused"], 0)


class TestPreflightCap(unittest.TestCase):
    def _write_temp(self, nbytes):
        fd, path = tempfile.mkstemp(suffix=".bin")
        with os.fdopen(fd, "wb") as fh:
            fh.write(b"\x00" * nbytes)
        self.addCleanup(os.unlink, path)
        return path

    def test_cap_refuses_huge_file(self):
        # Ngan sach that (950 MB) cho phep ~104 MB; dat ngan sach xuong
        # 1 MB de buoc tu choi ma khong can file that lon.
        path = self._write_temp(1024)
        with mock.patch.object(bridge, "WEB_TAB_BUDGET_BYTES", 1024 * 1024):
            with self.assertRaisesRegex(ValueError, "Safari"):
                bridge._check_web_memory(path, "kiểm tra trước khi giao")

    def test_cap_allows_small_file(self):
        path = self._write_temp(1024)
        bridge._check_web_memory(path, "kiểm tra trước khi giao")

    def test_delivery_gate_refuses_before_simulation(self):
        # Cap chay truoc khi doc/mo phong: file 1 KB voi ngan sach 1 KB
        # thi loi la loi cap (tieng Viet, huong dan), khong phai loi
        # dinh dang file.
        path = self._write_temp(1024)
        with mock.patch.object(bridge, "WEB_TAB_BUDGET_BYTES", 1024):
            res = json.loads(bridge.delivery_gate(
                path, json.dumps({"hello": "Xin chào"}),
                source_json=json.dumps({"hello": "Hello"})))
        # Bridge op tra loi dang JSON thay vi raise (nhu moi op khac).
        self.assertFalse(res.get("ok"))
        self.assertIn("chọn file .assets lẻ", res.get("error", ""))

    def test_rebuild_apply_refuses_before_simulation(self):
        path = self._write_temp(1024)
        with mock.patch.object(bridge, "WEB_TAB_BUDGET_BYTES", 1024):
            res = json.loads(bridge.rebuild_apply(
                path, json.dumps({"hello": "Xin chào"}), dry_run=True))
        # Bridge op tra loi dang JSON thay vi raise (nhu moi op khac).
        self.assertFalse(res.get("ok"))
        self.assertIn("ép file", res.get("error", ""))


if __name__ == "__main__":
    unittest.main()
