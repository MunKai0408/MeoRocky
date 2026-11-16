"""Test đợt tối ưu cuối (final_optimize, hạng mục b + f).

- Tối ưu đo được: read_text_asset_head, detect_locale, engine_signals,
  find_referenced_paths, placeholders_ok, cache plan v2 (gọn + đọc v1),
  TranslationCache gọn.
- Tiến độ thật (luật #152/#153): SteppedReporter bước từng 1% không nhảy
  cục, _inspect_progress + container list/read, progress_fn của pipeline
  trích xuất (theo bước, số lượng thật), adapter extract.
"""
import io
import json
import os
import re
import struct
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.progress import SteppedReporter, step_pcts
from sdk import discovery as disc
from sdk import exporters as exp
from sdk import extraction as ext
from sdk import translate as tr
from sdk import unity as u
from services import container_tool as ct
from services import language_tool as lt
from services import unity_tool as ut
from tests.helpers import ROOT, TmpCase

FX = ROOT / "tests" / "fixtures"
GAME1 = "tests/fixtures/extract/game1.zip"


# ------------------------------------------------------------------ fake app/overall
class FakeOv:
    """Thanh Overall giả: chỉ ghi lại các lần cập nhật (không vẽ)."""

    def __init__(self, enabled=True):
        self.enabled = enabled
        self.fracs = []
        self.notes = []
        self.renders = 0
        self.stages = []

    def update(self, frac=None, *, done=None, total=None, note=None):
        if frac is None and done is not None and total:
            frac = done / total
        if frac is not None:
            self.fracs.append(float(frac))
        if note is not None:
            self.notes.append(note)

    def begin(self, name, expected_s=None):
        self.stages.append(name)

    def render(self):
        self.renders += 1


def fake_app(ov=None):
    app = SimpleNamespace()
    app.overall = ov
    seen = []
    app._seen = seen

    def report_progress(frac=None, note=None):
        if ov is not None:
            ov.update(frac, note=note)
        seen.append((frac, note))

    app.report_progress = report_progress
    return app


def _mono_pct(fracs):
    """Các mốc % nguyên từ dãy frac (làm tròn như format_progress)."""
    return [int(f * 100 + 1e-9) for f in fracs]


# ------------------------------------------------------------------ (b) head
class TestReadTextAssetHeadFast(TmpCase):
    """read_text_asset_head mới (unpack_from) == bản đọc đủ + bản _Reader cũ."""

    def _head_old(self, data, endian="<"):
        from core.errors import error_from_exception
        r = u._Reader(data, endian, where="TextAsset")
        name = r.aligned_string()
        size = r.i32()
        if size < 0 or size > r.remaining():
            raise AssertionError("lỗi giả lập không khớp")
        raw = r.read(size)
        r.align(4)
        if raw.isascii():
            return name, size
        try:
            return name, len(raw.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise error_from_exception(exc, where="TextAsset")

    def _asset(self, name, script):
        nb = name.encode("utf-8")
        sb = script.encode("utf-8")
        out = struct.pack("<i", len(nb)) + nb
        out += b"\x00" * ((-len(out)) % 4)
        out += struct.pack("<i", len(sb)) + sb
        out += b"\x00" * ((-len(out)) % 4)
        return out

    def test_matches_full_read(self):
        cases = [
            ("hello", "Xin chào 9 Kings!"),
            ("tên có dấu", "Tiếng Việt có dấu: ồ ễ ệ ộ ơ ư đ Đ 🎮"),
            ("", ""),
            ("x", "a" * 5000),
            ("dài", "é" * 100),
        ]
        for name, script in cases:
            data = self._asset(name, script)
            got = u.read_text_asset_head(data)
            want = u.read_text_asset_bytes(data)
            self.assertEqual(got, (want[0], len(want[1])))
            self.assertEqual(got, self._head_old(data))

    def test_error_codes_match_old(self):
        # script không phải UTF-8; tên không phải UTF-8; thiếu đệm cuối;
        # cắt cụt giữa chừng; độ dài âm; thiếu byte đầu.
        bad_script = struct.pack("<i", 1) + b"a" + b"\x00" * 3 + struct.pack("<i", 1) + b"\xff" + b"\x00" * 3
        bad_name = struct.pack("<i", 1) + b"\xff" + b"\x00" * 3 + struct.pack("<i", 0)
        trunc = self._asset("ok", "hello")[:-2]
        neg = struct.pack("<i", -1)
        short = b"\x01\x00"
        for data in (bad_script, bad_name, trunc, neg, short):
            with self.assertRaises(Exception) as cm_new:
                u.read_text_asset_head(data)
            with self.assertRaises(Exception) as cm_old:
                self._head_old(data)
            self.assertEqual(str(cm_new.exception), str(cm_old.exception))

    def test_plan_assets_rows_uses_head(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "bf2", str(FX / "unity" / "build_fixtures.py"))
        bf = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bf)
        objs = [(1000 + i, 0, bf.text_asset_data("t%04d" % i, "Hello %d" % i))
                for i in range(50)]
        data = bf.build_serialized_file(version=23, objects=objs, types=[(49, None)])
        sf = u.parse_serialized(data)
        rows = []
        u._plan_assets_rows(sf, "c", "e", rows)
        self.assertEqual(len(rows), 50)
        self.assertTrue(all(r.object_type == "TextAsset" for r in rows))


# ------------------------------------------------------------------ (b) discovery
class TestDetectLocaleFast(unittest.TestCase):
    """detect_locale mới == bản cũ (regex biên dịch mỗi lần) trên corpus."""

    def _old(self, text, table):
        low = (text or "").lower()
        best = (None, 0.0, [])
        for loc in table:
            code = loc["code"]
            conf = 0.0
            ev = []
            if re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(code.lower()), low):
                conf = 0.9
            else:
                for alias in loc["aliases"]:
                    if disc._alias_hit(low, alias):
                        conf = max(conf, 0.7)
                        break
            if conf > best[1] or (conf == best[1] and best[0] is not None
                                  and len(code) > len(best[0])):
                best = (code, conf, ev)
        return best

    def test_same_results(self):
        corpus = [
            "localization/en.json", "Data/zh-Hans/strings.csv", "en_US/text.txt",
            "English.json", "tiếng việt.json", "game/vi/level.json",
            "strings.json", "Data/ja-JP/ui.po", "fr-FR.xml", "pt-BR.strings",
            "en", "english", "EN.json", "x/en-1/y", "zh-Hant.json",
            "Data/ko-KR/a.yml", "ru.txt", "de-DE/b.ini", "th-TH/c.resx",
        ]
        for text in corpus:
            old = self._old(text, disc.LOCALE_BUILTIN)
            new = disc.detect_locale(text, disc.LOCALE_BUILTIN)
            self.assertEqual((new[0], new[1]), (old[0], old[1]), text)


class TestEngineSignalsFast(unittest.TestCase):
    """engine_signals mới == bản cũ (_name_hit từng tên) trên corpus."""

    def test_same_results(self):
        names = ["Payload/Game.app/Data/level.unity3d", "project.godot", "game.pck",
                 "data.pak", "Content/a.locres", "project.json", "main.js",
                 "res/file.gd", "game.project", "data.win", "a.yyp", "b.yy",
                 "c.gml", "d.uasset", "e.umap", "f.ccb", "g.fire", "h.tscn",
                 "res/", "settings/x", "options/y", "datafiles/z", "i.archive"]
        for engine in disc._ENGINE_PRIORITY:
            if engine == "unity":
                continue
            old_ev, old_conf, old_magic = [], 0.0, False
            sigs = disc._ENGINE_NAME_SIGS[engine]
            for weight, group in ((0.3, sigs["strong"]), (0.15, sigs["weak"])):
                for sig in group:
                    for name in names:
                        if disc._name_hit(str(name).lower(), sig):
                            old_ev.append(sig)
                            break
            new_ev, new_conf, new_magic = disc.engine_signals(engine, names)
            self.assertEqual([e.value for e in new_ev], old_ev, engine)
            self.assertEqual(new_magic, old_magic, engine)

    def test_detect_engine_unchanged(self):
        names = ["Payload/Game.app/Data/level.unity3d",
                 "Payload/Game.app/Data/globalgamemanagers",
                 "Payload/Game.app/Data/resources.assets"]
        g = disc.detect_engine(names)
        self.assertEqual(g.engine, "unity")
        self.assertEqual(g.level, "CÓ KHẢ NĂNG")


class TestFindReferencedPathsFast(unittest.TestCase):
    """find_referenced_paths mới == bản cũ, kể cả biên: '.json', '..', '\\'."""

    def test_same_results(self):
        from sdk.vfs import MemoryVFS
        from core.limits import Limits
        files = {
            "Data/en/file.json": json.dumps({
                "a": "other.json", "b": "sub/deep.po", "c": "Hello world",
                "d": "..\\secret.json", "e": ".json", "f": "noext",
                "g": "UPPER.XML", "h": "a.b.c.properties", "i": "x.",
                "j": "a/b/c", "k": "trailing. ",
            }).encode(),
            "Data/en/other.json": b"{}",
            "Data/en/sub/deep.po": b"",
            "Data/en/UPPER.XML": b"",
            "Data/en/a.b.c.properties": b"",
        }
        vfs = MemoryVFS(files, limits=Limits())
        data = files["Data/en/file.json"]
        got = disc.find_referenced_paths(vfs, data, "Data/en/file.json")
        # bản cũ (splitext) cũng cho đúng các ref tồn tại thật trong VFS
        self.assertEqual(got, ["Data/en/UPPER.XML", "Data/en/a.b.c.properties",
                               "Data/en/other.json", "Data/en/sub/deep.po"])


# ------------------------------------------------------------------ (b) translate/cache
class TestPlaceholdersOkFast(unittest.TestCase):
    def test_same_as_check(self):
        cases = [
            ("Có {0} xu", "Có {0} xu", True),
            ("Có {0} xu", "Có xu", False),
            ("A\nB", "A\nB", True),
            ("A\nB", "A B", False),
            ("Có %s", "Có %d", False),
            ("<b>x</b> {name} %1$s", "<b>x</b> {name} %1$s", True),
            ("<b>x</b> {name}", "<b>x</b> {other}", False),
        ]
        for src, dst, want in cases:
            self.assertEqual(tr.placeholders_ok(src, dst), want, (src, dst))
            # Khi số xuống dòng bằng nhau, placeholders_ok == phép so
            # placeholder của check_placeholders; khác xuống dòng thì
            # placeholders_ok False thêm do kiểm tra "\n" riêng.
            if src.count("\n") == dst.count("\n"):
                self.assertEqual(
                    tr.placeholders_ok(src, dst),
                    not exp.check_placeholders({"k": src}, {"k": dst}))

    def test_translate_table_uses_it(self):
        kv = {"a": "Hello {0} world", "b": "Press %s to start"}
        res = tr.translate_table(dict(kv), source_lang="en", target_lang="vi",
                                 provider=tr.FakeProvider(), cache=None)
        self.assertEqual(res.total, 2)
        self.assertEqual(res.done, 2)


class TestPlanCacheV2(TmpCase):
    def _app(self):
        (self.tmp / "cache").mkdir()
        return SimpleNamespace(paths=SimpleNamespace(cache=self.tmp / "cache"))

    def test_round_trip_compact(self):
        plan = u.UnityPlan(
            verdict=u.EngineVerdict(engine="unity", level="CHẮC CHẮN", reasons=["x"]),
            rows=[u.PlanRow("c", "e%d" % i, "assets", "TextAsset", i, i, 10, "text", "n%d" % i)
                  for i in range(100)],
            warnings=[], decisions=["AUTO"], partial=False)
        d = ut._plan_to_dict(plan)
        self.assertTrue(all(isinstance(r, list) for r in d["rows"]))
        back = ut._plan_from_dict(d)
        self.assertEqual(ut._plan_to_dict(back), d)

    def test_v1_dict_rows_still_load(self):
        d1 = {"verdict": {"engine": "unity", "level": "CHẮC CHẮN", "reasons": ["x"]},
              "rows": [{"container": "c", "entry": "e", "kind": "assets",
                        "object_type": "TextAsset", "path_id": 7, "offset": 0,
                        "size": 5, "guess": "text", "name": "n"}],
              "warnings": [], "decisions": [], "partial": False}
        plan = ut._plan_from_dict(d1)
        self.assertEqual(len(plan.rows), 1)
        self.assertEqual(plan.rows[0].path_id, 7)
        # File cache v1 trên đĩa vẫn đọc được (không bắt ghi lại).
        app = self._app()
        blob = json.dumps({"version": 1, "entries": {"k": {"size": 1, "backend": "AUTO",
                        "pb3_version": "3.1", "last_used": 0.0, "plan": d1}}},
                        ensure_ascii=False).encode()
        (self.tmp / "cache" / ut.PLAN_CACHE_FILENAME).write_bytes(blob)
        entries = ut._load_plan_cache(app)
        self.assertIn("k", entries)
        self.assertEqual(ut._plan_from_dict(entries["k"]["plan"]).rows[0].name, "n")

    def test_compact_blob_smaller(self):
        rows = [u.PlanRow("Payload/Game.app/Data", "level%d.unity3d" % (i % 50),
                          "assets", "TextAsset", i, i * 128, 64, "text", "t%d" % i)
                for i in range(5000)]
        plan = u.UnityPlan(verdict=u.EngineVerdict(engine="unity", level="CHẮC CHẮN",
                                                   reasons=["x"]),
                           rows=rows, warnings=[], decisions=["AUTO"], partial=False)
        new = json.dumps(ut._plan_to_dict(plan), ensure_ascii=False).encode()
        old_rows = [{"container": r.container, "entry": r.entry, "kind": r.kind,
                     "object_type": r.object_type, "path_id": r.path_id,
                     "offset": r.offset, "size": r.size, "guess": r.guess,
                     "name": r.name} for r in rows]
        old = json.dumps({"rows": old_rows}, ensure_ascii=False).encode()
        self.assertLess(len(new), len(old) * 0.7)


class TestTranslationCacheCompact(TmpCase):
    def test_save_load_round_trip_and_old_file_loads(self):
        from sdk.translate import TranslationCache
        path = str(self.tmp / "tc.json")
        c = TranslationCache(path)
        c.put("Hello world", "en", "vi", "Xin chào thế giới")
        c.save()
        self.assertNotIn("\n", (self.tmp / "tc.json").read_text(encoding="utf-8").strip())
        c2 = TranslationCache(path)
        self.assertEqual(c2.get("Hello world", "en", "vi"), "Xin chào thế giới")
        # File cache cũ (indent=1) vẫn đọc được.
        old = json.dumps({"version": 1, "entries": {c.key("Hi", "en", "vi"): "Chào"}},
                         ensure_ascii=False, indent=1)
        (self.tmp / "tc.json").write_text(old, encoding="utf-8")
        c3 = TranslationCache(path)
        self.assertEqual(c3.get("Hi", "en", "vi"), "Chào")


# ------------------------------------------------------------------ (f) SteppedReporter
class TestSteppedReporter(unittest.TestCase):
    def test_steps_one_percent_with_overall(self):
        ov = FakeOv(enabled=True)
        app = fake_app(ov)
        rep = SteppedReporter(app, "quét")
        # 3 entry: mỗi entry nhảy ~33% -> phải quét dần từng 1%.
        for i in (1, 2, 3):
            rep.report(i, 3, note="entry%d" % i)
        pcts = _mono_pct(ov.fracs)
        self.assertEqual(pcts[0], 1)
        self.assertEqual(pcts[-1], 100)
        self.assertEqual(pcts, sorted(pcts))
        for a, b in zip(pcts, pcts[1:]):
            self.assertLessEqual(b - a, 1, "nhảy cục %d -> %d" % (a, b))
        self.assertGreater(ov.renders, 0)

    def test_never_over_100_and_never_back(self):
        ov = FakeOv(enabled=True)
        app = fake_app(ov)
        rep = SteppedReporter(app, "quét")
        rep.report(5, 3, note="vượt tổng")
        self.assertEqual(_mono_pct(ov.fracs)[-1], 100)
        rep.report(1, 3, note="lùi")
        self.assertEqual(_mono_pct(ov.fracs)[-1], 100)

    def test_total_zero_reports_nothing(self):
        ov = FakeOv(enabled=True)
        app = fake_app(ov)
        SteppedReporter(app, "quét").report(1, 0)
        self.assertEqual(ov.fracs, [])

    def test_fallback_steady_when_no_overall(self):
        app = fake_app(None)
        err = io.StringIO()
        import sys
        from unittest import mock
        rep = SteppedReporter(app, "quét")
        with mock.patch.object(sys, "stderr", err):
            rep.report(1, 2)
            rep.report(2, 2)
            rep.finish()
        out = err.getvalue()
        self.assertIn("100%", out)
        self.assertIn("2/2", out)

    def test_disabled_overall_falls_back_to_steady(self):
        ov = FakeOv(enabled=False)
        app = fake_app(ov)
        err = io.StringIO()
        import sys
        from unittest import mock
        rep = SteppedReporter(app, "quét")
        with mock.patch.object(sys, "stderr", err):
            rep.report(2, 2)
            rep.finish()
        self.assertIn("100%", err.getvalue())


class TestInspectProgressStepped(unittest.TestCase):
    def test_with_stepper_steps_one_percent(self):
        ov = FakeOv(enabled=True)
        app = fake_app(ov)
        stepper = SteppedReporter(app, "quét")
        for i in (1, 2, 3):
            ut._inspect_progress(app, i, 3, "entry%d" % i, stepper=stepper)
        pcts = _mono_pct(ov.fracs)
        self.assertEqual(pcts[-1], 100)
        for a, b in zip(pcts, pcts[1:]):
            self.assertLessEqual(b - a, 1)

    def test_without_stepper_single_call(self):
        # Hành vi cũ giữ nguyên cho đường gọi không có stepper.
        seen = []
        app = SimpleNamespace(report_progress=lambda f, note=None: seen.append((f, note)),
                              overall=None)
        err = io.StringIO()
        import sys
        from unittest import mock
        with mock.patch.object(sys, "stderr", err):
            ut._inspect_progress(app, 3, 27, "level4")
        self.assertEqual(seen, [(3 / 27, "level4")])
        self.assertEqual(err.getvalue(), "quét 3/27: level4\n")


# ------------------------------------------------------------------ (f) container
class TestContainerProgress(TmpCase):
    def test_list_steps_one_percent(self):
        ov = FakeOv(enabled=True)
        app = fake_app(ov)
        app.limits = None
        out = io.StringIO()
        import sys
        from unittest import mock
        with mock.patch.object(sys, "stdout", out):
            rc = ct.cmd_list(app, str(FX / "zip" / "sample.ipa"))
        self.assertEqual(rc, 0)
        pcts = _mono_pct(ov.fracs)
        self.assertEqual(pcts[-1], 100)
        for a, b in zip(pcts, pcts[1:]):
            self.assertLessEqual(b - a, 1)

    def test_read_reports_bytes_and_writes_exact(self):
        app = fake_app(None)
        app.limits = None
        dest = str(self.tmp / "level.unity3d")
        err = io.StringIO()
        out = io.StringIO()
        import sys
        from unittest import mock
        with mock.patch.object(sys, "stderr", err):
            with mock.patch.object(sys, "stdout", out):
                rc = ct.cmd_read(app, str(FX / "zip" / "sample.ipa"),
                                 "Payload/Game.app/Data/level.unity3d", dest)
        self.assertEqual(rc, 0)
        self.assertTrue(Path(dest).exists())
        self.assertIn("100%", err.getvalue())


# ------------------------------------------------------------------ (f) extraction
class TestExtractionProgress(unittest.TestCase):
    def test_phases_report_real_counts(self):
        from registry.registry import build_registry
        reg = build_registry(str(ROOT))
        seen = []

        def progress_fn(phase, done, total):
            seen.append((phase, done, total))

        req = ext.ExtractionRequest(
            source=str(FX / "extract" / "game1.zip"), registry=reg,
            out_dir=str(self._tmp_out()), progress_fn=progress_fn)
        result = ext.run_extraction(req)
        self.assertEqual(result.decision, "done")
        phases = [p for p, _d, _t in seen]
        for want in ("inventory", "probe", "export"):
            self.assertIn(want, phases, "thiếu bước %s trong %s" % (want, sorted(set(phases))))
        # Đơn điệu theo từng bước; done không vượt tổng khi tổng đã biết.
        per_phase = {}
        for phase, done, total in seen:
            per_phase.setdefault(phase, []).append((done, total))
        for phase, items in per_phase.items():
            dones = [d for d, _t in items]
            self.assertEqual(dones, sorted(dones), "bước %s lùi: %s" % (phase, dones))
            for done, total in items:
                if total is not None:
                    self.assertLessEqual(done, total)
        # Probe/export chốt đúng 100% (done == total ở lần cuối của bước).
        for phase in ("probe", "export"):
            items = per_phase.get(phase, [])
            if items:
                done, total = items[-1]
                self.assertEqual(done, total, phase)

    def _tmp_out(self):
        import tempfile
        d = tempfile.mkdtemp(prefix="pb3-fo-")
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        return d

    def test_adapter_steps_one_percent(self):
        ov = FakeOv(enabled=True)
        app = fake_app(ov)
        ad = lt._ExtractProgressAdapter(app)
        ad("probe", 1, 3)
        ad("probe", 3, 3)
        self.assertEqual(ov.stages, ["kiểm tra ứng viên"])
        pcts = _mono_pct(ov.fracs)
        self.assertEqual(pcts[-1], 100)
        for a, b in zip(pcts, pcts[1:]):
            self.assertLessEqual(b - a, 1)
        self.assertTrue(all("kiểm tra ứng viên" in n for n in ov.notes))

    def test_adapter_inventory_counts_without_percent(self):
        ov = FakeOv(enabled=True)
        app = fake_app(ov)
        ad = lt._ExtractProgressAdapter(app)
        ad("inventory", 5, None)
        ad("inventory", 42, None)
        # Không có % giả: không gọi update(frac) cho bước chưa biết tổng.
        self.assertEqual(ov.fracs, [])
        self.assertTrue(any("42 entry" in n for n in ov.notes))
        self.assertEqual(ov.stages, ["liệt kê file"])

    def test_adapter_no_overall_uses_steady(self):
        app = fake_app(None)
        err = io.StringIO()
        import sys
        from unittest import mock
        ad = lt._ExtractProgressAdapter(app)
        with mock.patch.object(sys, "stderr", err):
            ad("probe", 1, 2)
            ad("probe", 2, 2)
            ad.finish()
        self.assertIn("100%", err.getvalue())


class TestListCandidatesReadsHeadOnce(TmpCase):
    """list_candidates chỉ đọc head mỗi ứng viên 1 lần (tích hợp đợt tổng cuối)."""

    def test_head_read_once_per_candidate(self):
        from core.limits import Limits
        from plugins.json_text.plugin import JsonPlugin
        from sdk.api import PluginContext
        from sdk.vfs import MemoryVFS

        class CountingVFS(MemoryVFS):
            def __init__(self, files):
                super().__init__(files)
                self.reads = []

            def read(self, path, max_bytes=None):
                self.reads.append((path, max_bytes))
                return super().read(path, max_bytes=max_bytes)

        files = {"a.json": b'{"hello": "Xin chao"}', "b.json": b'{"bye": "Tam biet"}'}
        vfs = CountingVFS(files)
        ctx = PluginContext(vfs, Limits(), None, None, str(self.tmp), True, None, None, "json_text")
        plug = JsonPlugin()
        cands = plug.list_candidates(ctx.for_plugin(plug.manifest))
        self.assertEqual(len(cands), 2)
        # Trước đây _evidence_for đọc lại head lần 2 → 4 lần đọc; nay 2.
        self.assertEqual(len(vfs.reads), 2)


if __name__ == "__main__":
    unittest.main()
