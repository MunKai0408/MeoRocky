"""Test (E) Ep ban dich lai vao file goc (P0-1, sdk/rebuild.py + duong
ghi streaming sdk/unity.rebuild_bundle_streaming).

Fixture tu dung bang builder CO SAN trong repo
(tests/fixtures/unity/build_fixtures.py), KHONG them file fixture roi,
khong dung file game that, khong can mang, khong can marker
needs_fixture.
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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests" / "fixtures" / "unity"))
from build_fixtures import (  # noqa: E402
    build_bundle, build_serialized_file, text_asset_data)

from core.errors import PB3Error  # noqa: E402
from core.limits import Limits  # noqa: E402
from sdk import plan as pl  # noqa: E402
from sdk import rebuild as rb  # noqa: E402
from sdk import unity as u  # noqa: E402

EN = {"hello": "Hello", "play": "Play", "quit": "Quit"}
MENU = {"start": "Start Game", "options": "Options"}
VI = {"hello": "Xin chao", "play": "Choi", "quit": "Thoat",
      "start": "Bat dau choi", "options": "Tuy chon"}
CAB_A = "CAB-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
CAB_B = "CAB-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def _sf(entries):
    """entries: [(path_id, name, dict_or_str)] -> bytes SerializedFile."""
    objs = []
    for pid, name, val in entries:
        script = val if isinstance(val, str) else json.dumps(
            val, ensure_ascii=False)
        objs.append((pid, 0, text_asset_data(name, script)))
    return build_serialized_file(version=17, objects=objs, types=[(49, None)])


def _bundle(sf_bytes, cab=CAB_A, compression="lz4", **kw):
    return build_bundle(version=7, entries=[(cab, 4, sf_bytes)],
                        compression=compression, **kw)


def _scripts(bundle_bytes, cab=CAB_A):
    be = u.UnityBackend("NATIVE")
    b = u.parse_bundle(bundle_bytes, be, Limits())
    sf = u.parse_serialized(b.get_entry(cab), Limits(), where=cab)
    return {n: s for _p, n, s in sf.text_assets()}


def _zip_two(bundle_a, bundle_b):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.bundle", bundle_a)
        zf.writestr("b.bundle", bundle_b)
    return buf.getvalue()


def _merge_prov(keys, conflicts=None, total=None):
    return {"format": "beartea-merge-provenance/1", "keys": dict(keys),
            "conflicts": conflicts or [],
            "conflicts_total": len(conflicts or []) if total is None else total}


class TestGrouping(unittest.TestCase):
    def test_merge_provenance_groups_by_source(self):
        prov = _merge_prov({"hello": "a.bundle", "play": "a.bundle",
                            "start": "b.bundle"})
        g = rb.group_by_unit({"hello": "X", "play": "Y", "start": "Z"},
                             prov)
        self.assertEqual(set(g["groups"]), {"a.bundle", "b.bundle"})
        self.assertEqual(g["groups"]["a.bundle"]["translations"],
                         {"hello": "X", "play": "Y"})
        self.assertFalse(g["scan_all"])

    def test_translation_sidecar_single_source_fallback(self):
        prov = {"format": "beartea-translation-provenance/1",
                "translationFile": "translated.vi.json",
                "provenance": {"origin": "extract",
                               "source": "a.bundle", "locale": "en"}}
        g = rb.group_by_unit({"hello": "X", "start": "Z"}, prov)
        self.assertEqual(list(g["groups"]), ["a.bundle"])
        self.assertTrue(any("1 source" in w for w in g["warnings"]))

    def test_no_provenance_scans_all_with_warning(self):
        g = rb.group_by_unit({"hello": "X"}, None)
        self.assertTrue(g["scan_all"])
        self.assertIn(None, g["groups"])
        self.assertTrue(g["warnings"])

    def test_merge_keys_missing_fall_back_to_scan_all(self):
        prov = _merge_prov({"hello": "a.bundle"})
        g = rb.group_by_unit({"hello": "X", "orphan": "Y"}, prov)
        self.assertTrue(g["scan_all"])
        self.assertIn(None, g["groups"])
        self.assertIn("orphan", g["groups"][None]["translations"])

    def test_bare_provenance_object(self):
        g = rb.group_by_unit({"hello": "X"},
                             {"origin": "extract", "source": "b.bundle"})
        self.assertEqual(list(g["groups"]), ["b.bundle"])

    def test_invalid_provenance_format_rejected(self):
        with self.assertRaises(ValueError):
            rb.group_by_unit({"hello": "X"}, {"format": "la/1"})


class TestChoices(unittest.TestCase):
    CONFLICTS = [{"key": "hello",
                  "kept": {"value": "Hello", "source": "a.bundle"},
                  "dropped": {"value": "Hi", "source": "b.bundle"}}]

    def test_default_kept(self):
        res = rb.resolve_conflict_choices(self.CONFLICTS, None)
        self.assertEqual(res[0]["choice"], "kept")
        self.assertNotIn("warning", res[0])

    def test_dropped_reassigns_unit(self):
        prov = _merge_prov({"hello": "a.bundle"}, self.CONFLICTS)
        g = rb.group_by_unit({"hello": "X"}, prov, {"hello": "dropped"})
        self.assertEqual(list(g["groups"]), ["b.bundle"])
        self.assertEqual(g["conflicts"][0]["choice"], "dropped")
        self.assertIn("warning", g["conflicts"][0])

    def test_unknown_choice_key_rejected(self):
        with self.assertRaises(ValueError):
            rb.resolve_conflict_choices(self.CONFLICTS, {"nope": "kept"})

    def test_invalid_choice_value_rejected(self):
        with self.assertRaises(ValueError):
            rb.resolve_conflict_choices(self.CONFLICTS, {"hello": "both"})


class TestPlanPerUnit(unittest.TestCase):
    def test_multi_unit_zip_round_trip(self):
        bundle_a = _bundle(_sf([(1001, "en.json", EN)]), CAB_A)
        bundle_b = _bundle(_sf([(2001, "menu.json", MENU)]), CAB_B)
        data = _zip_two(bundle_a, bundle_b)
        prov = _merge_prov({"hello": "a.bundle", "play": "a.bundle",
                            "quit": "a.bundle", "start": "b.bundle",
                            "options": "b.bundle"})
        with tempfile.TemporaryDirectory() as d:
            res = rb.apply_rebuild(
                data, source_name="game.zip", translations=VI,
                provenance=prov, dry_run=False, confirm=True,
                out_dir=os.path.join(d, "out"),
                zip_path=os.path.join(d, "r.zip"))
            self.assertEqual(res["totals"]["units"], 2)
            self.assertEqual(res["totals"]["changed"], 5)
            self.assertEqual(res["totals"]["unmatched"], 0)
            self.assertEqual(len(res["outputs"]), 2)
            self.assertIsNotNone(res["zip_path"])
            with zipfile.ZipFile(res["zip_path"]) as zf:
                got_a = _scripts(zf.read("a.bundle"), CAB_A)
                got_b = _scripts(zf.read("b.bundle"), CAB_B)
        self.assertEqual(json.loads(got_a["en.json"]),
                         {"hello": "Xin chao", "play": "Choi",
                          "quit": "Thoat"})
        self.assertEqual(json.loads(got_b["menu.json"]),
                         {"start": "Bat dau choi", "options": "Tuy chon"})

    def test_unmatched_reasons(self):
        sf = _sf([(1001, "en.json", {"hello": "Hello", "count": 3}),
                  (1002, "list.json", '["greet", "bye"]')])
        bundle = _bundle(sf, CAB_A)
        trans = {"hello": "Xin chao", "count": "3",
                 "greet": "Chao", "ghost": "Ma"}
        res = rb.apply_rebuild(bundle, source_name="x.unity3d",
                               translations=trans, dry_run=True)
        reasons = {m["key"]: m["reason"] for m in res["unmatched"]}
        self.assertEqual(reasons.get("count"), "non_string_value")
        self.assertEqual(reasons.get("greet"), "not_json_dict")
        self.assertEqual(reasons.get("ghost"), "not_found")
        self.assertNotIn("hello", reasons)

    def test_unchanged_key_not_unmatched(self):
        bundle = _bundle(_sf([(1001, "en.json", EN)]), CAB_A)
        res = rb.apply_rebuild(bundle, source_name="x.unity3d",
                               translations={"hello": "Hello",
                                             "play": "Choi"},
                               dry_run=True)
        self.assertEqual(res["totals"]["unchanged"], 1)
        self.assertEqual(res["totals"]["unmatched"], 0)
        self.assertEqual(res["totals"]["changed"], 1)

    def test_refused_unit_does_not_fail_batch(self):
        good = _bundle(_sf([(1001, "en.json", EN)]), CAB_A)
        # Bundle ma hoa (co cu 0x200 + revision cu): refused E_ENCRYPTED.
        bad_sf = _sf([(1001, "en.json", EN)])
        bad = build_bundle(version=7, entries=[(CAB_B, 4, bad_sf)],
                           compression="none", extra_data_flags=0x200)
        data = _zip_two(good, bad)
        prov = _merge_prov({"hello": "a.bundle", "play": "b.bundle"})
        res = rb.apply_rebuild(data, source_name="game.zip",
                               translations={"hello": "Xin chao",
                                             "play": "Choi"},
                               provenance=prov, dry_run=True)
        self.assertEqual(len(res["units"]), 1)
        self.assertEqual(len(res["refused"]), 1)
        self.assertEqual(res["refused"][0]["code"], "E_ENCRYPTED")


class TestMBClassification(unittest.TestCase):
    """P0-1/G1: key MonoBehaviour o don vi KHONG lap duoc plan phai
    phan loai monobehaviour (khong phai not_found). Hoi quy cho viec
    thu SF chi cho don vi co plan."""

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

    def _mb_sf_ambiguous(self):
        # 2 object cung ten "MyMB", moi object 1 chuoi o 1 ngon ngu khac
        # nhau (strong): key "MyMB.s1" anh xa 2 vi tri khac nhau -> mo ho,
        # khong lap duoc plan (fail-closed).
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

    def test_mb_key_in_unit_without_plan_classified_monobehaviour(self):
        # mb-write-rest: key generic MO HO (>1 vi tri) van khong lap
        # duoc plan -> phan loai monobehaviour, khong phai not_found.
        sf_bytes = self._mb_sf_ambiguous()
        sf = u.parse_serialized(sf_bytes, Limits(), where="t")
        terms = rb._collect_mb_keys([sf])
        self.assertTrue(terms)
        key = sorted(terms)[0]
        self.assertEqual(key, "MyMB.s1")
        res = rb.apply_rebuild(
            sf_bytes, source_name="t.assets",
            translations={key: "[VI] " + key}, dry_run=True)
        self.assertEqual(res["units"], [])
        reasons = {m["key"]: m["reason"] for m in res["unmatched"]}
        self.assertEqual(reasons.get(key), "monobehaviour")

    def test_mb_values_all_equal_is_unchanged(self):
        # 106-key 9K: key MB ma MOI gia tri goc da biet deu == ban dich
        # thi unchanged (khong can ep), khong phai monobehaviour.
        unmatched, unchanged = rb._classify_unmatched(
            ["MyMB.s1"], {"MyMB.s1": "Hello MB World"},
            {}, {"MyMB.s1"},
            mb_values={"MyMB.s1": {"Hello MB World"}}, mb_index={})
        self.assertEqual(unmatched, [])
        self.assertEqual(unchanged, 1)

    def test_mb_values_one_differs_stays_monobehaviour(self):
        # Fail-closed: chi 1 gia tri goc khac ban dich thi van unmatched
        # (khong duoc dem unchanged) — cong van FAIL nhu cu.
        unmatched, unchanged = rb._classify_unmatched(
            ["MyMB.s1"], {"MyMB.s1": "[VI] MyMB.s1"},
            {}, {"MyMB.s1"},
            mb_values={"MyMB.s1": {"Press start", "Tôi yêu"}},
            mb_index={})
        self.assertEqual([(m["key"], m["reason"]) for m in unmatched],
                         [("MyMB.s1", "monobehaviour")])
        self.assertEqual(unchanged, 0)

    def test_st_other_unit_empty_translation_is_unchanged(self):
        # 9K: Spells/Cemetery_Desc + Spells/Rebirth_Desc — co trong
        # SharedTableData nhung khong co entry trong bang ST en, ban
        # dich "" == gia tri goc hieu dung ("") -> unchanged, khong
        # phai string_table_other_unit.
        unmatched, unchanged = rb._classify_unmatched(
            ["Spells/Cemetery_Desc"], {"Spells/Cemetery_Desc": ""},
            {}, set(), mb_values=None,
            mb_index={"Spells": {"Cemetery_Desc": 7}})
        self.assertEqual(unmatched, [])
        self.assertEqual(unchanged, 1)

    def test_st_other_unit_nonempty_translation_stays_unmatched(self):
        # Ban dich khac "" thi van other_unit (can chen entry — plan
        # xu ly o don vi cua bang), khong duoc dem unchanged.
        unmatched, unchanged = rb._classify_unmatched(
            ["Spells/X"], {"Spells/X": "dịch"},
            {}, set(), mb_values=None,
            mb_index={"Spells": {"X": 7}})
        self.assertEqual([(m["key"], m["reason"]) for m in unmatched],
                         [("Spells/X", "string_table_other_unit")])
        self.assertEqual(unchanged, 0)

    def test_mb_generic_key_now_pressable(self):
        # mb-write-rest: key generic KHONG mo ho gio ep duoc qua
        # scan-path -> co unit thay doi, khong con unmatched.
        sf_bytes = self._mb_sf()
        sf = u.parse_serialized(sf_bytes, Limits(), where="t")
        terms = rb._collect_mb_keys([sf])
        self.assertTrue(terms)
        key = sorted(terms)[0]
        res = rb.apply_rebuild(
            sf_bytes, source_name="t.assets",
            translations={key: "[VI] " + key}, dry_run=True)
        self.assertEqual(len(res["units"]), 1)
        self.assertEqual(res["unmatched"], [])


class TestStreaming(unittest.TestCase):
    def _write(self, d, name, data):
        p = os.path.join(d, name)
        with open(p, "wb") as fh:
            fh.write(data)
        return p

    def test_lz4_multi_entry_and_5mb_entry(self):
        big = "x" * (5 * 1024 * 1024)
        sf_big = _sf([(3001, "big.json", {"blob": big})])
        sf_small = _sf([(1001, "en.json", EN)])
        data = build_bundle(version=7, entries=[(CAB_A, 4, sf_small),
                                               (CAB_B, 4, sf_big)],
                            compression="lz4")
        with tempfile.TemporaryDirectory() as d:
            src = self._write(d, "a.unity3d", data)
            dst = os.path.join(d, "b.unity3d")
            patched, _mode = pl._apply_entry_changes(
                "assets", sf_small, None,
                {"id": "e", "object": 1001, "op": "REBUILD_SECTION",
                 "changes": [{"key": "hello", "before": "Hello",
                              "after": "Xin chao"}],
                 "after_sha": hashlib.sha256(json.dumps(
                     {"hello": "Xin chao", "play": "Play", "quit": "Quit"},
                     ensure_ascii=False).encode("utf-8")).hexdigest()},
                u.UnityBackend("NATIVE"), Limits())
            info = u.rebuild_bundle_streaming(
                src, dst, {CAB_A: patched},
                backend=u.UnityBackend("NATIVE"), limits=Limits())
            out = open(dst, "rb").read()
        self.assertGreater(info["blocks"], 1)  # payload > 128KiB -> nhieu block
        got = _scripts(out, CAB_A)
        self.assertEqual(json.loads(got["en.json"])["hello"], "Xin chao")
        be = u.UnityBackend("NATIVE")
        b = u.parse_bundle(out, be, Limits())
        self.assertEqual(b.get_entry(CAB_B), sf_big)

    def test_none_round_trip(self):
        sf = _sf([(1001, "en.json", EN)])
        data = _bundle(sf, CAB_A, compression="none")
        with tempfile.TemporaryDirectory() as d:
            src = self._write(d, "a.unity3d", data)
            dst = os.path.join(d, "b.unity3d")
            info = u.rebuild_bundle_streaming(
                src, dst, {}, backend=u.UnityBackend("NATIVE"),
                limits=Limits())
            out = open(dst, "rb").read()
        self.assertEqual(info["compression"], "none")
        self.assertEqual(json.loads(_scripts(out, CAB_A)["en.json"]), EN)

    def test_lzma_single_compressor_round_trip(self):
        sf = _sf([(1001, "en.json", EN)])
        data = _bundle(sf, CAB_A, compression="lzma")
        with tempfile.TemporaryDirectory() as d:
            src = self._write(d, "a.unity3d", data)
            dst = os.path.join(d, "b.unity3d")
            patched, _m = pl._apply_entry_changes(
                "assets", sf, None,
                {"id": "e", "object": 1001, "op": "REBUILD_SECTION",
                 "changes": [{"key": "play", "before": "Play",
                              "after": "Choi"}],
                 "after_sha": hashlib.sha256(json.dumps(
                     {"hello": "Hello", "play": "Choi", "quit": "Quit"},
                     ensure_ascii=False).encode("utf-8")).hexdigest()},
                u.UnityBackend("NATIVE"), Limits())
            info = u.rebuild_bundle_streaming(
                src, dst, {CAB_A: patched},
                backend=u.UnityBackend("NATIVE"), limits=Limits())
            out = open(dst, "rb").read()
        self.assertEqual(info["blocks"], 1)  # LZMA: 1 block duy nhat
        got = _scripts(out, CAB_A)
        self.assertEqual(json.loads(got["en.json"])["play"], "Choi")
        # Block LZMA giai nen 1 lan (one-shot) dung payload day du.
        be = u.UnityBackend("NATIVE")
        b = u.parse_bundle(out, be, Limits())
        self.assertEqual(b.payload, patched)

    def test_v9_round_trip(self):
        # [ĐÃ ĐO THẬT] ghi streaming bundle v9 (M-formats): dựng lại v9,
        # entry đã patch đúng nội dung, version giữ 9, offset block căn 16.
        sf = _sf([(1001, "en.json", EN)])
        data = build_bundle(version=9, entries=[(CAB_A, 4, sf)],
                            compression="none")
        with tempfile.TemporaryDirectory() as d:
            src = self._write(d, "a.unity3d", data)
            dst = os.path.join(d, "b.unity3d")
            patched, _m = pl._apply_entry_changes(
                "assets", sf, None,
                {"id": "e", "object": 1001, "op": "REBUILD_SECTION",
                 "changes": [{"key": "hello", "before": "Hello",
                              "after": "Xin chao"}],
                 "after_sha": hashlib.sha256(json.dumps(
                     {"hello": "Xin chao", "play": "Play", "quit": "Quit"},
                     ensure_ascii=False).encode("utf-8")).hexdigest()},
                u.UnityBackend("NATIVE"), Limits())
            info = u.rebuild_bundle_streaming(
                src, dst, {CAB_A: patched},
                backend=u.UnityBackend("NATIVE"), limits=Limits())
            out = open(dst, "rb").read()
        self.assertEqual(json.loads(_scripts(out, CAB_A)["en.json"]),
                         {"hello": "Xin chao", "play": "Play", "quit": "Quit"})
        be = u.UnityBackend("NATIVE")
        b = u.parse_bundle(out, be, Limits())
        self.assertEqual(b.version, 9)
        self.assertTrue(all((blk.offset or 0) % 16 == 0 for blk in b.blocks))
        self.assertEqual(info["blocks"], 1)

    def test_encrypted_rejected(self):
        sf = _sf([(1001, "en.json", EN)])
        data = build_bundle(version=7, entries=[(CAB_A, 4, sf)],
                            compression="none", extra_data_flags=0x200)
        with tempfile.TemporaryDirectory() as d:
            src = self._write(d, "a.unity3d", data)
            dst = os.path.join(d, "b.unity3d")
            with self.assertRaises(PB3Error) as cm:
                u.rebuild_bundle_streaming(
                    src, dst, {}, backend=u.UnityBackend("NATIVE"),
                    limits=Limits())
            self.assertEqual(cm.exception.code, "E_ENCRYPTED")
            self.assertFalse(os.path.exists(dst))

    def test_zstd_flag_writes_none_with_warning(self):
        # Bundle "none" that + co nen 5 o header (block that khong nen,
        # flag 64): gia lap cau fzstd tra nguyen blocks-info de writer
        # doc duoc layout — kiem nhanh Zstd -> none + warning.
        sf = _sf([(1001, "en.json", EN)])
        raw = bytearray(_bundle(sf, CAB_A, compression="none"))
        r = u._Reader(bytes(raw), ">", where="bundle")
        r.read(8)
        r.u32()
        r.string_to_null()
        r.string_to_null()
        r.unpack("QIII")
        flags_off = r.pos - 4
        flags = struct.unpack_from(">I", raw, flags_off)[0]
        struct.pack_into(">I", raw, flags_off, (flags & ~0x3F) | 5)
        be = u.UnityBackend("NATIVE")
        orig = be._decompress_zstd
        be._decompress_zstd = lambda data, size, where="unity": bytes(data)
        try:
            with tempfile.TemporaryDirectory() as d:
                src = self._write(d, "a.unity3d", bytes(raw))
                dst = os.path.join(d, "b.unity3d")
                info = u.rebuild_bundle_streaming(
                    src, dst, {}, backend=be, limits=Limits())
                out = open(dst, "rb").read()
        finally:
            be._decompress_zstd = orig
        self.assertEqual(info["compression"], "none")
        self.assertTrue(any("Zstd" in w for w in info["warnings"]))
        self.assertEqual(json.loads(_scripts(out, CAB_A)["en.json"]), EN)

    def test_blocks_info_at_end_round_trip(self):
        sf = _sf([(1001, "en.json", EN)])
        data = _bundle(sf, CAB_A, compression="lz4",
                       blocks_info_at_end=True)
        with tempfile.TemporaryDirectory() as d:
            src = self._write(d, "a.unity3d", data)
            dst = os.path.join(d, "b.unity3d")
            u.rebuild_bundle_streaming(src, dst, {},
                                       backend=u.UnityBackend("NATIVE"),
                                       limits=Limits())
            out = open(dst, "rb").read()
        be = u.UnityBackend("NATIVE")
        self.assertTrue(u.parse_bundle(out, be, Limits()).blocks_info_at_end)
        self.assertEqual(json.loads(_scripts(out, CAB_A)["en.json"]), EN)

    def test_progress_monotonic_and_on_entry(self):
        sf = _sf([(1001, "en.json", EN)])
        sf2 = _sf([(2001, "menu.json", MENU)])
        data = build_bundle(version=7, entries=[(CAB_A, 4, sf),
                                               (CAB_B, 4, sf2)],
                            compression="lz4")
        prog = []
        seen = []
        with tempfile.TemporaryDirectory() as d:
            src = self._write(d, "a.unity3d", data)
            dst = os.path.join(d, "b.unity3d")
            u.rebuild_bundle_streaming(
                src, dst, {}, backend=u.UnityBackend("NATIVE"),
                limits=Limits(),
                progress_fn=lambda a, b: prog.append((a, b)),
                on_entry=lambda n, s, z, r: seen.append((n, z, r)))
        self.assertTrue(prog)
        self.assertTrue(all(prog[i][0] <= prog[i + 1][0]
                            for i in range(len(prog) - 1)))
        self.assertEqual(prog[-1][0], prog[-1][1])
        self.assertEqual({n for n, _z, _r in seen}, {CAB_A, CAB_B})

    def test_dest_equals_source_guard(self):
        sf = _sf([(1001, "en.json", EN)])
        data = _bundle(sf, CAB_A, compression="none")
        with tempfile.TemporaryDirectory() as d:
            src = self._write(d, "a.unity3d", data)
            before = hashlib.sha256(open(src, "rb").read()).hexdigest()
            with self.assertRaises(PB3Error) as cm:
                u.rebuild_bundle_streaming(
                    src, src, {}, backend=u.UnityBackend("NATIVE"),
                    limits=Limits())
            self.assertEqual(cm.exception.code, "E_CONFIG_INVALID")
            after = hashlib.sha256(open(src, "rb").read()).hexdigest()
            self.assertEqual(before, after)


class TestRoundTripAndSafety(unittest.TestCase):
    def test_ram_round_trip_key_order_and_source_sha(self):
        bundle = _bundle(_sf([(1001, "en.json", EN)]), CAB_A)
        before = hashlib.sha256(bundle).hexdigest()
        with tempfile.TemporaryDirectory() as d:
            res = rb.apply_rebuild(
                bundle, source_name="x.unity3d", translations=VI,
                dry_run=False, confirm=True,
                out_dir=os.path.join(d, "out"),
                zip_path=os.path.join(d, "r.zip"))
            self.assertEqual(hashlib.sha256(bundle).hexdigest(), before)
            with open(res["dest"], "rb") as fh:
                got = _scripts(fh.read(), CAB_A)
        obj = json.loads(got["en.json"])
        self.assertEqual(list(obj.keys()), list(EN.keys()))
        self.assertEqual(obj["hello"], "Xin chao")

    def test_streaming_round_trip_via_apply_rebuild(self):
        sf = _sf([(1001, "en.json", EN)])
        data = _bundle(sf, CAB_A, compression="lz4")
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "big.unity3d")
            with open(src, "wb") as fh:
                fh.write(data)
            before = hashlib.sha256(open(src, "rb").read()).hexdigest()
            res = rb.apply_rebuild(
                data, source_name="big.unity3d",
                translations={"hello": "Xin chao", "play": "Choi"},
                dry_run=False, confirm=True, src_path=src,
                force_streaming=True, out_dir=os.path.join(d, "out"),
                zip_path=os.path.join(d, "r.zip"))
            self.assertTrue(res["units"][0]["streaming"])
            self.assertEqual(hashlib.sha256(open(src, "rb").read()).hexdigest(),
                             before)
            got = _scripts(open(res["dest"], "rb").read(), CAB_A)
        obj = json.loads(got["en.json"])
        self.assertEqual(list(obj.keys()), list(EN.keys()))
        self.assertEqual(obj, {"hello": "Xin chao", "play": "Choi",
                               "quit": "Quit"})

    def test_streaming_over_64mb_single_none_block(self):
        # Hoi quy: bundle >64MB voi 1 block none duy nhat — duong
        # streaming phai liet ke/lap plan theo entry (gioi han 128MB),
        # khong duoc E_TOO_LARGE o parse_bundle nhu ban dau.
        sf = _sf([(1001, "en.json", EN)])
        entries = [(CAB_A, 4, sf)]
        for i in range(13):
            entries.append(("filler-%02d" % i, 0,
                            os.urandom(5 * 1024 * 1024)))
        data = build_bundle(version=7, entries=entries, compression="none")
        self.assertGreater(len(data), 64 * 1024 * 1024)
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "big.unity3d")
            with open(src, "wb") as fh:
                fh.write(data)
            before = hashlib.sha256(open(src, "rb").read()).hexdigest()
            res = rb.apply_rebuild(
                data, source_name="big.unity3d",
                translations={"hello": "Xin chao", "play": "Choi"},
                dry_run=False, confirm=True, src_path=src,
                force_streaming=True, out_dir=os.path.join(d, "out"),
                zip_path=os.path.join(d, "r.zip"))
            self.assertEqual(res["refused"], [])
            self.assertTrue(res["units"][0]["streaming"])
            self.assertEqual(res["units"][0]["size_before"], len(data))
            self.assertEqual(hashlib.sha256(open(src, "rb").read()).hexdigest(),
                             before)
            got = _scripts(open(res["dest"], "rb").read(), CAB_A)
        obj = json.loads(got["en.json"])
        self.assertEqual(obj["hello"], "Xin chao")
        self.assertEqual(obj["play"], "Choi")

    def test_v9_standalone_rebuilds(self):
        # [ĐÃ ĐO THẬT] bundle v9 độc lập nay ghi được (M-formats):
        # apply_rebuild áp bản dịch, không còn refused E_UNSUPPORTED_FORMAT.
        sf = _sf([(1001, "en.json", EN)])
        v9 = build_bundle(version=9, entries=[(CAB_A, 4, sf)],
                          compression="none")
        with tempfile.TemporaryDirectory() as d:
            res = rb.apply_rebuild(
                v9, source_name="v9.unity3d",
                translations={"hello": "Xin chao"},
                dry_run=False, confirm=True,
                out_dir=os.path.join(d, "out"),
                zip_path=os.path.join(d, "r.zip"))
            self.assertEqual(res["refused"], [])
            self.assertEqual(len(res["units"]), 1)
            with open(res["dest"], "rb") as fh:
                got = _scripts(fh.read(), CAB_A)
            out = open(res["dest"], "rb").read()
        obj = json.loads(got["en.json"])
        self.assertEqual(obj["hello"], "Xin chao")
        be = u.UnityBackend("NATIVE")
        self.assertEqual(u.parse_bundle(out, be, Limits()).version, 9)

    def test_encrypted_standalone_is_refused(self):
        # Hoi quy: bundle ma hoa doc lap phai vao refused co ma ro
        # rang, khong duoc im lang thanh key "not_found" voi units=[].
        sf = _sf([(1001, "en.json", EN)])
        enc = build_bundle(version=7, entries=[(CAB_A, 4, sf)],
                           compression="none", extra_data_flags=0x200)
        res = rb.apply_rebuild(
            enc, source_name="enc.unity3d",
            translations={"hello": "Xin chao"})
        self.assertEqual(res["units"], [])
        self.assertEqual(len(res["refused"]), 1)
        self.assertEqual(res["refused"][0]["code"], "E_ENCRYPTED")

    def test_encrypted_bundle_in_zip_scan_all_is_refused(self):
        # Hoi quy (sua-loi 2b): bundle ma hoa trong ZIP o buoc liet ke
        # quet-tat-ca truoc day bi bo qua im lang (refused=[]); nay phai
        # co refused E_ENCRYPTED, trong khi don vi tot van ap binh thuong.
        sf = _sf([(1001, "en.json", EN)])
        good = _bundle(sf, CAB_A)
        enc = build_bundle(version=7, entries=[(CAB_A, 4, sf)],
                           compression="none", extra_data_flags=0x200)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("good.bundle", good)
            zf.writestr("secret.bundle", enc)
        res = rb.apply_rebuild(
            buf.getvalue(), source_name="game.zip",
            translations={"hello": "Xin chao"})
        self.assertEqual(len(res["units"]), 1)
        self.assertIn("good.bundle", res["units"][0]["unit"])
        self.assertEqual(len(res["refused"]), 1)
        self.assertEqual(res["refused"][0]["unit"], "secret.bundle")
        self.assertEqual(res["refused"][0]["code"], "E_ENCRYPTED")
        self.assertEqual(res["totals"]["refused"], 1)

    def test_rebuild_all_entry_refused_other_unit_applies(self):
        # apply_plan: entry REBUILD_ALL bi tu choi rieng, entry khac van ap.
        bundle = _bundle(_sf([(1001, "en.json", EN)]), CAB_A)
        plan = pl.build_plan(bundle, {"hello": "Xin chao"},
                             source_bundle="x.unity3d", entry=CAB_A,
                             mode="REBUILD_SECTION")
        plan["entries"][0]["op"] = "REBUILD_ALL"
        res = pl.apply_plan(bundle, plan, dry_run=True)
        self.assertEqual(len(res["applied"]), 0)
        self.assertEqual(res["errors"][0]["code"], "E_WRITE_DISABLED")
        # _apply_unit_plan cung tu choi REBUILD_ALL (chot o tang rebuild).
        with self.assertRaises(PB3Error) as cm:
            rb._apply_unit_plan(bundle, plan, u.UnityBackend("NATIVE"),
                                Limits())
        self.assertEqual(cm.exception.code, "E_WRITE_DISABLED")

    def test_dry_run_false_without_confirm_writes_nothing(self):
        bundle = _bundle(_sf([(1001, "en.json", EN)]), CAB_A)
        with tempfile.TemporaryDirectory() as d:
            out_dir = os.path.join(d, "out")
            with self.assertRaises(PB3Error) as cm:
                rb.apply_rebuild(bundle, source_name="x.unity3d",
                                 translations={"hello": "Xin chao"},
                                 dry_run=False, confirm=False,
                                 out_dir=out_dir,
                                 zip_path=os.path.join(d, "r.zip"))
            self.assertEqual(cm.exception.code, "E_CONFIG_INVALID")
            self.assertFalse(os.path.exists(out_dir))
            self.assertFalse(os.path.exists(os.path.join(d, "r.zip")))

    def test_plan_progress_fn_called(self):
        bundle = _bundle(_sf([(1001, "en.json", EN)]), CAB_A)
        plan = pl.build_plan(bundle, {"hello": "Xin chao", "play": "Choi"},
                             source_bundle="x.unity3d", entry=CAB_A,
                             mode="REBUILD_SECTION")
        calls = []
        pl.apply_plan(bundle, plan, dry_run=True,
                      progress_fn=lambda a, b: calls.append((a, b)))
        self.assertTrue(calls)
        self.assertEqual(calls[-1], (1, 1))


if __name__ == "__main__":
    unittest.main()
