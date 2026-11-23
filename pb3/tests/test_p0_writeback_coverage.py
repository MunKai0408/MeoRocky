"""P0 writeback coverage (brief #2) — do phu ghi nguoc M1 tren game that.

Thiet ke: ~/workspace/goals/meorocky-repo-editing/hidden_files/p0-design/plan.md
(D1-D8). Chi DO + KHOA, khong viet lai writer: M1 (apply_monobehaviour_patch
+ apply_rebuild) la writer cua P0; module nay la harness do + regression test.

Quy uoc:
- Ban dich gia theo D7: "[VI] " + goc (goc "" giu "", dem unchanged).
- Chi chay tren BAN COPY cua IPA trong tmp cua test; file goc trong
  ~/workspace/test-ipas/ KHONG BAO GIO bi ghi — moi test assert sha256
  file goc truoc/sau giong nhau.
- Moi test IPA that dung @slow + skipUnless file ton tai (tien le
  test_mb_write_rest.py). timeout_s = 1800 rieng cho lop game that
  (runner run_tests.py doc thuoc tinh nay; PB3/EK2 ap day du vuot 180s).
- Kiem chung doc lap (D4 lop 4): sau khi ap, doc lai tung don vi da
  patch va so byte truc tiep — moi byte ngoai vung object da doi phai
  giong het file goc; moi key da ghi doc lai dung "[VI] ...", key khong
  ghi doc lai dung goc. So key doi doc lap phai khop totals["changed"]
  ma apply_rebuild bao (neu lech -> loi, test do).
- Moi test in 1 dong "P0-COVERAGE <game> {...}" de ghep bang do vao
  exp/p0_writeback_results.md. So trong dong nay la [DA DO THAT] khi
  test chay xong tren may nay; test skip thi khong co so.

Chi stdlib + core/sdk (Python 3.9, Pyodide-compatible). Khong mang.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sys
import unittest
import zipfile
from collections import Counter
from pathlib import Path

from core.errors import PB3Error  # noqa: E402
from core.limits import Limits  # noqa: E402
from sdk import csvutil as _csv  # noqa: E402
from sdk import extraction as _ext  # noqa: E402
from sdk import plan as _plan  # noqa: E402
from sdk import rebuild as _rb  # noqa: E402
from sdk import unity as u  # noqa: E402
from tests.helpers import TmpCase  # noqa: E402
from tests.tiering import slow  # noqa: E402

LIM = Limits()
BE = u.UnityBackend("AUTO", LIM)

TEST_IPAS = Path.home() / "workspace" / "test-ipas"
NINEKINGS_IPA = TEST_IPAS / "9kings-v1.0.6.ipa"
PB3_IPA = TEST_IPAS / "pb3-v1.1.7.ipa"
EK2_IPA = TEST_IPAS / "ek2-v0.0.18.ipa"
NINEKINGS_EN = Path.home() / "workspace" / "user" / "files" / "en.json"
PB3_EN = TEST_IPAS / "pb3-extract" / "en.json"
EK2_EN = TEST_IPAS / "ek2-extract" / "en.json"
# Bundle StringTable tieng Anh cua 9K + entry CAB cua no (tien le
# test_mb_write_rest.py:585).
NINEKINGS_ST_BUNDLE = ("Payload/9Kings.app/Data/Raw/aa/iOS/"
                       "localization-string-tables-english(en)_assets_all"
                       ".bundle")
NINEKINGS_ST_ENTRY = "CAB-7f58a1d076a0579bc99ac19d9e397afa"


def _sha256_file(path):
    h = hashlib.sha256()
    with open(str(path), "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_bytes(data):
    return hashlib.sha256(bytes(data)).hexdigest()


def _fake_vi(en):
    """Ban dich gia D7: "[VI] " + goc; goc "" giu "" (khong doi)."""
    return {k: ("[VI] " + v if v else "") for k, v in en.items()}


def _unit_values(unit_bytes, kind, req_entry, mb_index, vpath=None):
    """{(path_id, kind, key): value} + {path_id: object_bytes} cua 1 don vi.

    MB targets (I2 / I2-tt / ST / generic) qua monobehaviour_write_targets;
    TextAsset JSON qua doc script; TextAsset CSV qua csvutil.csv_values.
    Don vi CSV doc lap (kind "csv"): toan bo gia tri lay tu csv_values,
    khong co object (lop 4 byte-diff o day chinh la so toan bo o).
    """
    if kind == "csv":
        name = (vpath or req_entry or "csv").rsplit("/", 1)[-1]
        values = {(0, "csv", k): v for k, v in
                  _csv.csv_values(bytes(unit_bytes), name, "en").items()}
        return values, {}
    if kind == "bundle":
        bundle = u.parse_bundle(unit_bytes, BE, LIM, where="bundle")
        sf = u.parse_serialized(bundle.get_entry(req_entry), LIM,
                                where=req_entry)
    else:
        sf = u.parse_serialized(unit_bytes, LIM, where="assets")
    values = {}
    targets, _notes = u.monobehaviour_write_targets(
        sf, "en", mb_index, generic_fn=_ext.generic_write_targets)
    for t in targets:
        key = t.get("key")
        if isinstance(key, str):
            values[(int(t["path_id"]), t["kind"], key)] = t["before"]
    tas, _warn = _plan._read_unit_text_assets(kind, unit_bytes, req_entry,
                                              BE, LIM)
    for pid, name, script in tas:
        try:
            obj = json.loads(script)
        except (ValueError, UnicodeDecodeError):
            try:
                for k, v in _csv.csv_values(script.encode("utf-8"), name,
                                            "en").items():
                    values[(int(pid), "csv", k)] = v
            except Exception:
                continue
            continue
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, str):
                    values[(int(pid), "text_asset", k)] = v
    obj_bytes = {}
    for o in sf.objects:
        try:
            obj_bytes[int(o.path_id)] = sf.object_bytes(int(o.path_id))
        except PB3Error:
            continue
    return values, obj_bytes


def _check_unit_bytes(before_unit_bytes, after_unit_bytes, kind, req_entry,
                      changed_pids):
    """D4 lop 4 (sua 23/11 theo verify Minh phan 2): so byte truc tiep.

    PATCH_INPLACE: moi byte ngoai vung object da doi phai giong het goc.
    REBUILD_SECTION: nhu tren NHUNG loai tru object table (byte_start /
    byte_size thay doi hop le theo D3). Harness nay so object_bytes
    theo tung path_id (chu KHONG so byte tho toan file) nen object table
    khong bao gio bi so — dung luat REBUILD_SECTION; dinh dang / thu tu
    object / data_offset cua object table do lop 1 (_verify_assets
    trong writer) kiem. Voi PATCH_INPLACE thi luat chat hon can so ca
    metadata ngoai object — chua co test nao chay PATCH_INPLACE o quy
    mo game (plan §6: follow-up), nen duong chat do chua duoc do o day.
    Bundle: moi entry khac entry da doi byte-identical. SF: moi object
    khac path_id da doi byte-identical; tap path_id giu nguyen.
    Tra (n_entries_checked, n_objects_checked). Don vi CSV doc lap: khong
    co cau truc object — lop 4 o day la so toan bo o (thuc hien o
    _unit_values: moi o deu co trong dict gia tri, o khong doi nao lech
    cung lo ra o changed_ids), nen tra (0, 0).
    """
    if kind == "csv":
        return 0, 0
    n_entries = 0
    if kind == "bundle":
        bb = u.parse_bundle(before_unit_bytes, BE, LIM, where="bundle::truoc")
        ab = u.parse_bundle(after_unit_bytes, BE, LIM, where="bundle::sau")
        b_names = {e.name for e in bb.entries}
        a_names = {e.name for e in ab.entries}
        if b_names != a_names:
            raise AssertionError(
                "tap entry bundle doi: truoc %d, sau %d"
                % (len(b_names), len(a_names)))
        for name in b_names:
            if name == req_entry:
                continue
            n_entries += 1
            if bb.get_entry(name) != ab.get_entry(name):
                raise AssertionError(
                    "entry bundle %r doi byte ngoai vung patch" % name)
        before_sf = bb.get_entry(req_entry)
        after_sf = ab.get_entry(req_entry)
    else:
        before_sf, after_sf = before_unit_bytes, after_unit_bytes
    bsf = u.parse_serialized(before_sf, LIM, where="sf::truoc")
    asf = u.parse_serialized(after_sf, LIM, where="sf::sau")
    b_pids = {int(o.path_id) for o in bsf.objects}
    a_pids = {int(o.path_id) for o in asf.objects}
    if b_pids != a_pids:
        raise AssertionError(
            "tap path_id doi: truoc %d, sau %d" % (len(b_pids), len(a_pids)))
    n_objects = 0
    for pid in b_pids:
        if pid in changed_pids:
            continue
        n_objects += 1
        if bsf.object_bytes(pid) != asf.object_bytes(pid):
            raise AssertionError(
                "object path_id=%d doi byte ngoai vung patch" % pid)
    return n_entries, n_objects


class _P0Base(TmpCase):
    """Dung chung cho T1-T3: copy IPA -> preview (tuy chon) -> ap that ->
    kiem chung doc lap -> bao bang do."""

    timeout_s = 1800

    def _p0_cleanup_workdir(self, work_dir):
        # Chi don khi test DAT (self._p0_ok do test method dat o cuoi):
        # test FAIL thi giu lai ban copy + output de dieu tra.
        if getattr(self, "_p0_ok", False):
            shutil.rmtree(str(work_dir), ignore_errors=True)

    def _run_apply(self, game, ipa_path, en_path, with_preview=False):
        ipa_path = Path(ipa_path)
        en_path = Path(en_path)
        if not ipa_path.is_file():
            self.skipTest("thieu IPA that: %s" % ipa_path)
        if not en_path.is_file():
            self.skipTest("thieu en.json that: %s" % en_path)
        sha_before = _sha256_file(ipa_path)
        # Plan §6/D8 (sua 23/11): ban copy IPA/bundle de trong workspace
        # (~/workspace/p0-implementation/work/), KHONG dung /tmp (tmpfs
        # 512 MB; 3 IPA ~945 MB + ban patch). out_dir/zip cung o day.
        work_dir = (Path.home() / "workspace" / "p0-implementation"
                    / "work" / game / self._testMethodName)
        work_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(self._p0_cleanup_workdir, work_dir)
        work = work_dir / ("copy-" + ipa_path.name)
        shutil.copyfile(str(ipa_path), str(work))
        self.assertEqual(_sha256_file(work), sha_before,
                         "ban copy khac file goc")
        ipa_bytes = work.read_bytes()
        en = json.loads(en_path.read_text(encoding="utf-8"))
        self.assertTrue(en, "en.json rong")
        vi = _fake_vi(en)
        # Quet nhe toan bo IPA (giong apply_rebuild): mb_index + tap hop
        # MOI gia tri goc da biet cua tung key. Mot key co the co nhieu
        # gia tri goc khac nhau o nhieu noi (vd PB3: key UI_SANDBOX_STYLE
        # = 'Style {0} of {1}' trong CSV nhung = 'Style' trong MB;
        # 189 key PB3 co >1 gia tri goc trong mb_values) — en.json chi
        # giu 1 bien the (CSV thang truoc), nen kiem chung "truoc" phai
        # chap nhan bat ky bien the nao da biet, khong phai chi en[key].
        scan = _rb._scan_units_derived(ipa_bytes, BE, LIM)
        mb_index = scan["mb_index"]
        known_values = {}
        for k, vs in (scan["mb_values"] or {}).items():
            for v in (vs or []):
                if isinstance(v, str):
                    known_values.setdefault(k, set()).add(v)
        for k, v in (scan["text_values"] or {}).items():
            # TextAsset JSON co the co gia tri khong phai chuoi (list/dict)
            # — chi nhan chuoi vi kiem chung chi so chuoi.
            if isinstance(v, str):
                known_values.setdefault(k, set()).add(v)
        # CSV: hop tat ca file CSV doc lap (collect_csv_values chi
        # first-wins theo file).
        try:
            units_all = _rb._enumerate_units(ipa_bytes, BE, LIM)
        except PB3Error:
            units_all = []
        for (vpath, ukind) in units_all:
            if ukind != "csv":
                continue
            try:
                _k, csv_bytes, _req, _label = _plan._resolve_unit(
                    ipa_bytes, vpath, BE, LIM)
                name = (vpath or "csv").rsplit("/", 1)[-1]
                for k, v in _csv.csv_values(csv_bytes, name,
                                            "en").items():
                    known_values.setdefault(k, set()).add(v)
            except Exception:
                continue
        out_dir = work_dir / "out"
        zip_path = work_dir / "out.zip"
        preview = None
        if with_preview:
            preview = _rb.build_rebuild_preview(
                ipa_bytes, source_name=ipa_path.name, translations=vi,
                source_locale="en", mode="REBUILD_SECTION",
                mb_index=mb_index)
        res = _rb.apply_rebuild(
            ipa_bytes, source_name=ipa_path.name, translations=vi,
            dry_run=False, confirm=True, mode="REBUILD_SECTION",
            out_dir=str(out_dir), zip_path=str(zip_path),
            source_locale="en", mb_index=mb_index)
        tot = res["totals"]
        self.assertEqual(tot["refused"], 0, str(res.get("refused")))
        self.assertGreater(tot["changed"], 0,
                           "phu ghi = 0: %s" % (tot,))
        if preview is not None:
            self.assertEqual(tot, preview["totals"],
                             "preview != apply: %s vs %s"
                             % (preview["totals"], tot))
        # Kiem chung doc lap tung don vi da patch.
        kind_counter = Counter()
        mode_counter = Counter()
        unit_rows = []
        diff_changed_total = 0
        checked_entries = 0
        checked_objects = 0
        all_changed_ids = set()
        for unit in res["units"]:
            vpath = unit["unit"]
            mode_counter[unit["mode"]] += 1
            kind, before_unit, req_entry, _label = _plan._resolve_unit(
                ipa_bytes, vpath, BE, LIM)
            out_name = unit.get("output")
            self.assertTrue(out_name, "don vi %s khong co output" % vpath)
            after_unit = (out_dir / out_name).read_bytes()
            before_vals, _b_obj = _unit_values(before_unit, kind, req_entry,
                                               mb_index, vpath=vpath)
            after_vals, _a_obj = _unit_values(after_unit, kind, req_entry,
                                              mb_index, vpath=vpath)
            changed_ids = {k for k in set(before_vals) | set(after_vals)
                           if before_vals.get(k) != after_vals.get(k)}
            all_changed_ids |= changed_ids
            changed_pids = {pid for (pid, _k, _key) in changed_ids}
            unit_kind_counter = Counter()
            # Moi key doi phai doc lai dung "[VI] ..." va goc dung en.
            for (pid, vkind, key) in sorted(changed_ids):
                self.assertIn(key, vi, "key doi %r khong co trong ban dich"
                                % key)
                self.assertEqual(after_vals.get((pid, vkind, key)), vi[key],
                                 "key %r doc lai sai" % key)
                if (pid, vkind, key) in before_vals:
                    self.assertIn(
                        before_vals[(pid, vkind, key)],
                        known_values.get(key, {en[key]}),
                        "gia tri goc cua key %r khong nam trong cac bien "
                        "the da biet %s"
                        % (key, sorted(known_values.get(key,
                                                        {en[key]}))[:4]))
                kind_counter[vkind] += 1
                unit_kind_counter[vkind] += 1
            # Khong key nao bien mat; so doi doc lap khop so apply bao.
            for k in before_vals:
                self.assertIn(k, after_vals, "key %r bien mat sau khi ap"
                              % (k,))
            self.assertEqual(len(changed_ids), unit["changes"],
                             "don vi %s: doc lap dem %d != apply bao %d"
                             % (vpath, len(changed_ids), unit["changes"]))
            diff_changed_total += len(changed_ids)
            n_e, n_o = _check_unit_bytes(before_unit, after_unit, kind,
                                         req_entry, changed_pids)
            checked_entries += n_e
            checked_objects += n_o
            unit_rows.append({
                "unit": vpath, "kind": kind, "changes": unit["changes"],
                "mode": unit["mode"],
                "size_before": unit["size_before"],
                "size_after": unit["size_after"],
                "output": out_name,
                "changed_by_kind": dict(sorted(unit_kind_counter.items())),
            })
        self.assertEqual(diff_changed_total, tot["changed"],
                         "tong doi doc lap %d != apply bao %d"
                         % (diff_changed_total, tot["changed"]))
        # File goc khong doi (chot cuoi: khong ghi de file goc).
        self.assertEqual(_sha256_file(ipa_path), sha_before,
                         "file goc %s da doi!" % ipa_path)
        reasons = Counter(x["reason"] for x in res["unmatched"])
        # T4(b) (sua 23/11 theo verify Minh phan 2): key ST ky vong =
        # MOI key "Collection/Key" trong ban dich ma collection co trong
        # mb_index (SharedTableData) va gia tri goc khong rong — khong
        # ghi cung con so 100 (so do gan voi dry-run Build 68, chua chac
        # dung voi ban dich gia). Key ST da doi thuc te = tap key kind
        # string_table trong changed_ids doc lap.
        st_expected_keys = sorted(
            k for k, v in en.items()
            if "/" in k and k.split("/", 1)[0] in mb_index and v != "")
        st_changed_keys = sorted(
            key for (_pid, vkind, key) in all_changed_ids
            if vkind == "string_table")
        cov = {
            "game": game, "ipa": ipa_path.name,
            "ipa_sha256": sha_before[:12],
            "keys": tot["keys"],
            "changed": tot["changed"], "unchanged": tot["unchanged"],
            "unmatched": tot["unmatched"], "refused": tot["refused"],
            "units": tot["units"],
            "changed_by_kind": dict(sorted(kind_counter.items())),
            "unmatched_by_reason": dict(sorted(reasons.items())),
            "modes": dict(sorted(mode_counter.items())),
            "bytes_checked_entries": checked_entries,
            "bytes_checked_objects": checked_objects,
            "st_expected_keys": st_expected_keys,
            "st_changed_keys": st_changed_keys,
            "unit_rows": unit_rows,
        }
        print("P0-COVERAGE %s %s"
              % (game, json.dumps(cov, ensure_ascii=False, sort_keys=True)))
        return res, cov

    def _count_ambiguous_generic(self, game, ipa_path, expected=None):
        """T6: dem key generic mo ho (>1 locator khac nhau) tren game that.

        Lap dung logic cua generic_write_targets (2 luot split False/True
        vao chung 1 key_map): key co >1 locator khac nhau thi writer bo
        qua, khong doan. Dem theo cap (don vi, key). expected = (so don
        vi quet, tong key generic, so cap mo ho, so don vi co key mo ho)
        [DA DO THAT 23/11] — chot so lan dau, doi thi phai dieu tra.
        """
        ipa_path = Path(ipa_path)
        if not ipa_path.is_file():
            self.skipTest("thieu IPA that: %s" % ipa_path)
        ipa_bytes = ipa_path.read_bytes()
        units = _rb._enumerate_units(ipa_bytes, BE, LIM)
        amb_pairs = set()
        units_with = 0
        n_generic_keys = set()
        for (vpath, _kind) in units:
            try:
                kind, unit_bytes, req_entry, _label = _plan._resolve_unit(
                    ipa_bytes, vpath, BE, LIM)
            except PB3Error:
                continue
            if kind not in ("bundle", "assets"):
                continue
            try:
                if kind == "bundle":
                    bundle = u.parse_bundle(unit_bytes, BE, LIM,
                                            where="bundle")
                    sf = u.parse_serialized(bundle.get_entry(req_entry),
                                            LIM, where=req_entry)
                else:
                    sf = u.parse_serialized(unit_bytes, LIM, where="assets")
            except PB3Error:
                continue
            try:
                items = [it for it in sf.monobehaviour_texts()
                         if it.get("kind") == "generic"]
            except PB3Error:
                continue
            if not items:
                continue
            key_map = {}
            for split in (False, True):
                _ext._mb_generic_kv(items, split_languages=split,
                                    key_map_out=key_map)
            found = 0
            for key, locs in key_map.items():
                n_generic_keys.add((vpath, key))
                uniq = []
                for loc in locs:
                    if loc not in uniq:
                        uniq.append(loc)
                if len(uniq) > 1:
                    amb_pairs.add((vpath, key))
                    found += 1
            if found:
                units_with += 1
        cov = {
            "game": game, "units_scanned": len(units),
            "generic_keys_total": len(n_generic_keys),
            "ambiguous_unit_key_pairs": len(amb_pairs),
            "units_with_ambiguous": units_with,
        }
        if expected is not None:
            self.assertEqual(
                (cov["units_scanned"], cov["generic_keys_total"],
                 cov["ambiguous_unit_key_pairs"],
                 cov["units_with_ambiguous"]),
                expected, str(cov))
        print("P0-AMBIGUOUS %s %s"
              % (game, json.dumps(cov, ensure_ascii=False, sort_keys=True)))
        return cov


@slow
class TestP0Writeback9K(_P0Base):
    """T1/T4/T5/T6 tren 9 Kings v1.0.6."""

    def test_t1_9k_full_ipa_apply(self):
        # [DA DO THAT] khi chay: preview + ap that tren ban copy IPA 9K.
        # So co so de doi chieu (dry-run Build 68 + translated.vi.json
        # 2188 key): 1985 doi / 103 giu nguyen / 100 ST don vi khac /
        # 0 refused (test_mb_write_rest.py:527). O day dung en.json 2252
        # key + ban dich gia "[VI] " nen so se khac doi chut.
        _res, cov = self._run_apply("9k", NINEKINGS_IPA, NINEKINGS_EN,
                                    with_preview=True)
        by_kind = cov["changed_by_kind"]
        # [DA DO THAT 23/11, IPA 9K v1.0.6, en.json 2252 key] — chot so
        # do lan dau (log t1.log): neu doi thi writer hoac du lieu that
        # da doi, phai dieu tra truoc khi doi so.
        self.assertEqual((cov["changed"], cov["unchanged"],
                          cov["unmatched"], cov["refused"], cov["units"]),
                         (2250, 2, 0, 0, 2), str(cov))
        # T4b (fold vao T1; sua 23/11 theo verify Minh phan 2): MOI key
        # string_table_other_unit phai roi dung vao bundle ST, 0 key ro
        # sang resources.assets — khong ghi cung con so 100.
        st_units = [r for r in cov["unit_rows"]
                    if "localization-string-tables" in r["unit"]]
        self.assertEqual(len(st_units), 1, str(cov["unit_rows"]))
        self.assertEqual(cov["st_changed_keys"], cov["st_expected_keys"],
                         "key ST khong roi het vao bundle ST: doi %d, "
                         "ky vong %d (thieu %s, thua %s)"
                         % (len(cov["st_changed_keys"]),
                            len(cov["st_expected_keys"]),
                            sorted(set(cov["st_expected_keys"])
                                 - set(cov["st_changed_keys"]))[:5],
                            sorted(set(cov["st_changed_keys"])
                                 - set(cov["st_expected_keys"]))[:5]))
        res_units = [r for r in cov["unit_rows"]
                     if r["unit"].endswith("resources.assets")]
        self.assertEqual(len(res_units), 1, str(cov["unit_rows"]))
        self.assertEqual(
            res_units[0]["changed_by_kind"].get("string_table", 0), 0,
            "key ST ro sang resources.assets: %s" % (res_units[0],))
        self.assertGreater(by_kind.get("language_source", 0)
                           + by_kind.get("language_source_tt", 0), 0,
                           "phai co key I2 duoc ghi: %s" % (by_kind,))
        self._p0_ok = True

    def test_t4a_st_bundle_without_mb_index(self):
        # T4a: bundle ST 9K KHONG co mb_index -> 0 key ST duoc ghi,
        # khong crash (fail-closed). Bieu hien o tang plan: build_plan
        # khong lap duoc entry nao (ValueError — apply_rebuild bat loi
        # nay va bo qua don vi lang le); o tang preview: changed = 0,
        # refused = 0.
        if not NINEKINGS_IPA.is_file():
            self.skipTest("thieu IPA that: %s" % NINEKINGS_IPA)
        zf = zipfile.ZipFile(io.BytesIO(NINEKINGS_IPA.read_bytes()))
        bdata = zf.read(NINEKINGS_ST_BUNDLE)
        en = json.loads(NINEKINGS_EN.read_text(encoding="utf-8"))
        vi = _fake_vi(en)
        try:
            plan = _plan.build_plan(
                bdata, vi, source_bundle=NINEKINGS_ST_BUNDLE,
                entry=NINEKINGS_ST_ENTRY, mode="REBUILD_SECTION",
                mb_index=None)
        except ValueError as exc:
            self.assertIn("Khong lap duoc entry nao", str(exc))
            plan = None
        if plan is not None:
            st_changes = [c for e in plan["entries"] for c in e["changes"]
                          if e["asset_type"] == "MonoBehaviour"
                          and (c.get("field_path", "").startswith(
                              "m_TableData")
                              or c.get("field_path") == "m_TableData[+]")]
            self.assertEqual(st_changes, [],
                             "khong mb_index ma van ghi ST: %s"
                             % (st_changes[:2],))
        preview = _rb.build_rebuild_preview(
            bdata, source_name=NINEKINGS_ST_BUNDLE.rsplit("/", 1)[-1],
            translations=vi, source_locale="en", mode="REBUILD_SECTION",
            mb_index=None)
        self.assertEqual(preview["totals"]["changed"], 0,
                         "khong mb_index ma preview van doi: %s"
                         % (preview["totals"],))
        self.assertEqual(preview["totals"]["refused"], 0,
                         str(preview["totals"]))

    def test_t5_longer_translation_escalates(self):
        # T5: term I2 that trong resources.assets 9K, ban dich DAI HON
        # goc, mode PATCH_INPLACE -> leo thang REBUILD_SECTION, verify
        # xanh, doc lai dung gia tri moi.
        if not NINEKINGS_IPA.is_file():
            self.skipTest("thieu IPA that: %s" % NINEKINGS_IPA)
        zf = zipfile.ZipFile(io.BytesIO(NINEKINGS_IPA.read_bytes()))
        assets = zf.read("Payload/9Kings.app/Data/resources.assets")
        sf = u.parse_serialized(assets, LIM, where="resources.assets")
        targets, _notes = u.monobehaviour_write_targets(sf, "en")
        ls_targets = [t for t in targets
                      if t.get("kind") == "language_source"
                      and len(t["before"]) >= 10]
        self.assertTrue(ls_targets, "khong co target I2 nao")
        pick = ls_targets[0]
        new_val = "[VI] " + pick["before"] + " " + ("X" * 64)
        self.assertGreater(len(new_val.encode("utf-8")),
                           len(pick["before"].encode("utf-8")))
        plan = _plan.build_plan(assets, {pick["key"]: new_val},
                                source_bundle="resources.assets",
                                entry=None, mode="PATCH_INPLACE")
        self.assertEqual(len(plan["entries"]), 1)
        entry = plan["entries"][0]
        self.assertEqual(entry["op"], "REBUILD_SECTION",
                         "ban dich dai hon phai leo thang, nhan %r"
                         % entry["op"])
        self.assertTrue(any("leo thang" in (w or "") for w in
                            entry.get("warnings") or []),
                        "thieu canh bao leo thang: %s" % (entry.get("warnings"),))
        out, mode = _plan._apply_entry_changes(
            "assets", assets, None, entry, BE, LIM)
        self.assertEqual(mode, "REBUILD_SECTION")
        # REBUILD_SECTION dung lai data section (can 8): file co the NHO
        # HON goc do nen padding giua cac object — khong phai mat du lieu.
        # [DA DO THAT, probe-t5-size.py]: chi object da doi dai them dung
        # 72 byte (so byte them vao), 19753 object khac byte-identical,
        # file giam 78744 byte do nen padding (632197 -> 553381 byte
        # overhead). Kiem lai dung 3 dieu do thay vi so do dai file.
        sf_before = u.parse_serialized(assets, LIM, where="before")
        sf_after = u.parse_serialized(out, LIM, where="after")
        added = len(new_val.encode("utf-8")) - len(pick["before"].encode("utf-8"))
        # Chuoi trong object can 4 byte (giong _astr): tang that = added
        # lam tron len boi cua 4.
        expected_growth = (added + 3) // 4 * 4
        self.assertEqual(len(sf_after.object_bytes(pick["path_id"]))
                         - len(sf_before.object_bytes(pick["path_id"])),
                         expected_growth,
                         "object da doi phai dai them dung so byte them (can 4)")
        n_e, n_o = _check_unit_bytes(assets, out, "assets", None,
                                     {int(pick["path_id"])})
        self.assertEqual(n_e, 0)
        self.assertEqual(n_o, len(list(sf_before.objects)) - 1,
                         "moi object khac phai byte-identical")
        sf2 = u.parse_serialized(out, LIM, where="after")
        info = u.parse_language_source_bytes(
            sf2.object_bytes(pick["path_id"]), "<")
        got = [t for t in info["terms"] if t["term"] == pick["key"]]
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["values"][0], new_val)

    def test_t6_ambiguous_generic_count_9k(self):
        # [DA DO THAT 23/11] 31 don vi, 18603 key generic, 9 cap mo ho,
        # 8 don vi co key mo ho (log t6-9k.log).
        self._count_ambiguous_generic("9k", NINEKINGS_IPA,
                                      expected=(31, 18603, 9, 8))


@slow
class TestP0WritebackPB3(_P0Base):
    """T2/T6 tren PB3 v1.1.7 (gom ca CSV — M3 writer)."""

    timeout_s = 1800

    def test_t2_pb3_full_ipa_apply(self):
        _res, cov = self._run_apply("pb3", PB3_IPA, PB3_EN,
                                    with_preview=False)
        by_kind = cov["changed_by_kind"]
        # [DA DO THAT 23/11, IPA PB3 v1.1.7, en.json 2348 key] — chot so
        # do lan dau (log t2.log): 4696 doi = 2348 key x 2 noi ghi (CSV
        # + bang I2 trong resources.assets).
        self.assertEqual((cov["changed"], cov["unchanged"],
                          cov["unmatched"], cov["refused"], cov["units"]),
                         (4696, 0, 0, 0, 2), str(cov))
        # PB3 co 4700 key CSV (kiem ke 17/11); so key CSV ghi duoc dem
        # o day — neu = 0 thi la PHAT HIEN (M3 khong toi duoc du lieu
        # that), phai dieu tra truoc khi ket luan P0.
        print("P0-PB3-KIND %s" % json.dumps(by_kind, ensure_ascii=False,
                                            sort_keys=True))
        self._p0_ok = True

    def test_t6_ambiguous_generic_count_pb3(self):
        # [DA DO THAT 23/11] 307 don vi, 24736 key generic, 3 cap mo ho,
        # 2 don vi co key mo ho (log t6-pb3.log).
        self._count_ambiguous_generic("pb3", PB3_IPA,
                                      expected=(307, 24736, 3, 2))


@slow
class TestP0WritebackEK2(_P0Base):
    """T3/T6 tren EK2 v0.0.18."""

    timeout_s = 1800

    def test_t3_ek2_full_ipa_apply(self):
        _res, cov = self._run_apply("ek2", EK2_IPA, EK2_EN,
                                    with_preview=False)
        # [DA DO THAT 23/11, IPA EK2 v0.0.18, en.json 1344 key] — chot so
        # do lan dau (log t3.log): 1325 doi + 19 giu nguyen (19 key co
        # gia tri goc ""), nho fix fallback locale en -> en-GB.
        self.assertEqual((cov["changed"], cov["unchanged"],
                          cov["unmatched"], cov["refused"], cov["units"]),
                         (1325, 19, 0, 0, 1), str(cov))
        print("P0-EK2-KIND %s" % json.dumps(cov["changed_by_kind"],
                                            ensure_ascii=False,
                                            sort_keys=True))
        self._p0_ok = True

    def test_t6_ambiguous_generic_count_ek2(self):
        # [DA DO THAT 23/11] 112 don vi, 25669 key generic, 6 cap mo ho,
        # 5 don vi co key mo ho (log t6-ek2.log).
        self._count_ambiguous_generic("ek2", EK2_IPA,
                                      expected=(112, 25669, 6, 5))


if __name__ == "__main__":
    unittest.main()
