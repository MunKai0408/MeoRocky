"""Test trích xuất ngôn ngữ (Claude 07): discovery, trọng tài, export,
checkpoint, ghi ngược (cả Unity bundle), qua SDK trực tiếp (tầng fast)."""
import io
import json
import os
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from core.app import bootstrap
from core.errors import PB3Error
from core.limits import Limits
from sdk import discovery as disc
from sdk import exporters as exp
from sdk import extraction as ext
from services import language_tool
from tests.helpers import TmpCase, copy_project, register_tmp_dir, unregister_tmp_dir
from tests.helpers import pytest  # noqa: F401 - marker needs_fixture (stub trên iSH)

GAME1 = "tests/fixtures/extract/game1.zip"
AMBIG = "tests/fixtures/extract/ambig.zip"
UNITY = "tests/fixtures/unity/sample.unity3d"
XLIFF = "tests/fixtures/xliff/sample.xlf"


def _zip_bytes(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in sorted(entries.items()):
            zf.writestr(name, data)
    return buf.getvalue()


class ExtractionCase(TmpCase):
    _shared_proj = None
    _shared_td = None

    @classmethod
    def setUpClass(cls):
        cls._shared_td = tempfile.mkdtemp(prefix="pb3-extract-")
        register_tmp_dir(cls._shared_td)
        cls._shared_proj = copy_project(Path(cls._shared_td))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._shared_td, ignore_errors=True)
        unregister_tmp_dir(cls._shared_td)

    def setUp(self):
        super().setUp()
        self.app = bootstrap(self._shared_proj)
        self.addCleanup(self.app.close)
        self.reg = language_tool._registry(self.app)
        self.out = self.tmp / "out"

    def fix(self, rel):
        return str(self._shared_proj / rel)

    def run_ext(self, source, **kw):
        kw.setdefault("registry", self.reg)
        kw.setdefault("tempdir", str(self.app.paths.temp))
        kw.setdefault("root", str(self._shared_proj))
        kw.setdefault("out_dir", str(self.out))
        return ext.run_extraction(ext.ExtractionRequest(source=source, **kw))

    def extract_game1(self, **kw):
        return self.run_ext(self.fix(GAME1), **kw)

    def read_out(self, name):
        return (self.out / name).read_text(encoding="utf-8")

    def read_out_json(self, name):
        return json.loads(self.read_out(name))


class TestDiscovery(ExtractionCase):
    def test_locale_table_has_common_locales(self):
        table = disc.load_locale_table(str(self._shared_proj))
        by_code = {loc["code"]: loc for loc in table}
        self.assertEqual(by_code["en"]["display"], "English")
        self.assertEqual(by_code["zh"]["display"], "Chinese")
        self.assertIn("vi", by_code)
        self.assertIn("zh-Hans", by_code)

    def test_detect_locale_from_filename(self):
        table = disc.load_locale_table(str(self._shared_proj))
        code, conf, _ev = disc.detect_locale("localization/en.json", table)
        self.assertEqual(code, "en")
        self.assertGreater(conf, 0.0)
        code, _c, _e = disc.detect_locale("localization/zh-Hans.json", table)
        self.assertEqual(code, "zh-Hans")
        code, _c, _e = disc.detect_locale("localization/strings.csv", table)
        self.assertIsNone(code)

    def test_detect_locale_from_content(self):
        table = disc.load_locale_table(str(self._shared_proj))
        code, _c, _e = disc.detect_locale('{"lang": "vi", "title": "9 Kings"}', table)
        self.assertEqual(code, "vi")

    @pytest.mark.needs_fixture("unity/sample.unity3d")
    def test_detect_engine_unity_and_unknown(self):
        data = Path(self.fix(UNITY)).read_bytes()
        g = disc.detect_engine([UNITY], {UNITY: data[:4096]})
        self.assertEqual(g.engine, "unity")
        self.assertEqual(g.level, "CHẮC CHẮN")
        g2 = disc.detect_engine(["game1.zip"], {"game1.zip": b"PK\x03\x04"})
        self.assertEqual(g2.engine, "unknown")

    def test_inventory_excludes_and_scores(self):
        root = disc.open_source_vfs(self.fix(GAME1), Limits())
        inv = disc.inventory(root, Limits())
        by_path = {e.path: e for e in inv}
        self.assertEqual(by_path["data/font.ttf"].kind, "excluded")
        self.assertEqual(by_path["logs/debug.log"].kind, "excluded")
        self.assertGreaterEqual(by_path["localization/en.json"].text_score, 0.9)
        self.assertEqual(by_path["inner.zip"].kind, "container")

    def test_score_text_likelihood(self):
        self.assertGreaterEqual(disc.score_text_likelihood("a.json", b'{"k": "v"}', 9), 0.9)
        self.assertLessEqual(disc.score_text_likelihood("a.bin", b"\x00\x01\x02\xff", 4), 0.3)


class TestExtractionPipeline(ExtractionCase):
    def test_game1_full_extract(self):
        r = self.extract_game1()
        self.assertEqual(r.decision, "done")
        self.assertEqual(r.engine, "unknown")
        self.assertEqual(r.total_entries, 21)
        self.assertEqual(len(r.files), 6)
        by_path = {f.path: f for f in r.files}
        self.assertEqual(by_path["localization/strings.csv"].plugin, "csv_table")
        self.assertEqual(by_path["localization/en.json"].plugin, "json_text")
        self.assertEqual(by_path["localization/app.properties"].plugin, "properties")
        self.assertEqual(by_path["localization/ui.strings"].plugin, "apple_strings")

    def test_game1_export_files_exist(self):
        r = self.extract_game1()
        self.assertTrue(r.ok)
        for name in ("English.json", "Chinese.json", "Chinese (Simplified).json",
                     "English.en.json", "app.json", "ui.json",
                     "extraction_report.json", "extraction_report.txt",
                     "extraction_checkpoint.json"):
            self.assertTrue((self.out / name).exists(), name)

    def test_game1_skipped_reasons(self):
        r = self.extract_game1()
        by_path = {s["path"]: s["reason"] for s in r.skipped}
        self.assertIn("raw.bin", by_path)
        self.assertIn("dưới ngưỡng", by_path["raw.bin"])
        self.assertIn("index/catalog", by_path["localization/index.json"])
        self.assertIn("font/atlas", by_path["data/font.ttf"])
        self.assertIn("log/cache", by_path["logs/debug.log"])
        self.assertIn("điểm thấp hơn", by_path["inner.zip::en.json"])

    def test_multi_locale_csv_splits(self):
        r = self.extract_game1()
        en = self.read_out_json("English.json")
        zh = self.read_out_json("Chinese.json")
        self.assertEqual(en, {"play": "Play", "quit": "Quit"})
        self.assertEqual(zh, {"play": "开始", "quit": "退出"})

    def test_csv_collision_resolved_by_stem(self):
        # English.json đã bị CSV chiếm -> en.json xuất thành English.en.json.
        r = self.extract_game1()
        en_json = self.read_out_json("English.en.json")
        self.assertEqual(en_json["hello"], "Hello")
        self.assertEqual(len(en_json), 6)

    def test_duplicate_keys_keep_first(self):
        r = self.extract_game1()
        app_json = self.read_out_json("app.json")
        self.assertEqual(app_json["dup"], "first")
        self.assertEqual(app_json["title"], "My Game")
        f = next(f for f in r.files if f.path.endswith("app.properties"))
        self.assertEqual(f.entries, 4)

    def test_placeholders_and_unicode_preserved(self):
        r = self.extract_game1()
        en_json = self.read_out_json("English.en.json")
        self.assertEqual(en_json["welcome"], "Welcome, {name}!")
        self.assertEqual(en_json["cafe"], "cafe" + "\u0301")  # fixture dùng dấu kết hợp
        zh_json = self.read_out_json("Chinese (Simplified).json")
        self.assertEqual(zh_json["cafe"], "咖啡馆")
        problems = exp.check_placeholders(
            {"welcome": "Welcome, {name}!"}, {"welcome": en_json["welcome"]})
        self.assertEqual(problems, [])

    def test_locale_filter_en_only(self):
        r = self.extract_game1(locale="en")
        self.assertEqual(r.decision, "done")
        locales = {f.locale for f in r.files}
        self.assertEqual(locales, {"en"})
        self.assertEqual(r.total_entries, 8)
        self.assertFalse((self.out / "Chinese.json").exists())

    def test_locale_not_found_error(self):
        r = self.extract_game1(locale="xx")
        self.assertEqual(r.decision, "none")
        self.assertTrue(any(e["code"] == "E_LOCALE_NOT_FOUND" for e in r.errors))
        msg = next(e["message"] for e in r.errors if e["code"] == "E_LOCALE_NOT_FOUND")
        self.assertIn("en", msg)

    def test_unknown_engine_still_extracts(self):
        r = self.extract_game1()
        self.assertEqual(r.engine, "unknown")
        self.assertEqual(r.engine_level, "CHƯA ĐỦ BẰNG CHỨNG")
        self.assertEqual(r.decision, "done")

    def test_ir_document_built(self):
        r = self.extract_game1()
        self.assertIsNotNone(r.ir)
        # 22 entry bảng (gồm cả bản ghi trùng "dup" thứ hai); tổng 21 là số
        # key duy nhất sau khi gộp trùng.
        self.assertEqual(len(r.ir.entries()), 22)
        keys = {e.key for e in r.ir.entries()}
        self.assertIn("hello", keys)
        self.assertIn("play", keys)

    def test_export_verify_roundtrip(self):
        r = self.extract_game1()
        data = (self.out / "English.json").read_bytes()
        check = exp.verify_export_bytes(data, self.read_out_json("English.json"))
        self.assertTrue(check["ok"], check["problems"])

    def test_generated_keys_deterministic(self):
        # Key hàng (row_000001...) ổn định giữa các lần chạy; key do dò
        # chuỗi thô sinh ra (off_...) cũng vậy (xem test_pin_file_*).
        data = _zip_bytes({"list.json": b'["mot", "hai", "ba"]'})
        src = self.tmp / "list.zip"
        src.write_bytes(data)
        r1 = self.run_ext(str(src))
        self.assertEqual(r1.decision, "done")
        keys1 = sorted(self.read_out_json("list.json").keys())
        out2 = self.tmp / "out2"
        r2 = self.run_ext(str(src), out_dir=str(out2))
        keys2 = sorted(json.loads((out2 / "list.json").read_text(encoding="utf-8")).keys())
        self.assertEqual(keys1, ["row_000001", "row_000002", "row_000003"])
        self.assertEqual(keys2, keys1)

    def test_empty_source_no_candidates(self):
        src = self.tmp / "empty.zip"
        src.write_bytes(_zip_bytes({}))
        r = self.run_ext(str(src))
        self.assertEqual(r.decision, "none")
        self.assertEqual(r.total_entries, 0)

    def test_garbage_file_no_fake_json(self):
        src = self.tmp / "garbage.zip"
        src.write_bytes(_zip_bytes({"data.bin": b"\x00\x01\x02\xff" * 64}))
        r = self.run_ext(str(src))
        self.assertEqual(r.decision, "none")
        self.assertEqual(r.files, [])
        self.assertFalse((self.out / "English.json").exists())

    def test_dry_run_writes_nothing(self):
        r = self.extract_game1(dry_run=True)
        self.assertEqual(r.decision, "done")
        self.assertFalse(self.out.exists())

    def test_report_json_matches_outcome(self):
        r = self.extract_game1()
        rep = self.read_out_json("extraction_report.json")
        self.assertEqual(rep["decision"], "done")
        self.assertEqual(rep["total_entries"], 21)
        self.assertEqual(len(rep["files"]), 6)
        self.assertEqual(rep["source"], self.fix(GAME1))
        txt = self.read_out("extraction_report.txt")
        self.assertIn("21 entry", txt)
        self.assertIn("English.json", txt)


class TestArbitration(ExtractionCase):
    def test_ambig_asks_with_two_options(self):
        r = self.run_ext(self.fix(AMBIG))
        self.assertEqual(r.decision, "ask")
        self.assertEqual(len(r.candidates), 1)
        grp = r.candidates[0]
        self.assertEqual(grp["locale"], "en")
        self.assertEqual(len(grp["options"]), 2)
        self.assertEqual({o["path"] for o in grp["options"]}, {"a/en.json", "b/en.json"})
        self.assertEqual(r.files, [])

    def test_ambig_ask_fn_picks_second(self):
        r = self.run_ext(self.fix(AMBIG), ask_fn=lambda opts: 1)
        self.assertEqual(r.decision, "done")
        self.assertEqual([(f.path, f.locale) for f in r.files], [("b/en.json", "en")])
        self.assertEqual(self.read_out_json("English.json")["hello"], "Hello B")

    def test_ambig_ask_fn_none_skips(self):
        r = self.run_ext(self.fix(AMBIG), ask_fn=lambda opts: None)
        self.assertEqual(r.decision, "ask")
        self.assertEqual(r.files, [])

    def test_partial_answer_extracts_answered_keeps_pending(self):
        data = _zip_bytes({
            "a/en.json": json.dumps({"hello": "Hello A"}).encode("utf-8"),
            "b/en.json": json.dumps({"hello": "Hello B"}).encode("utf-8"),
            "c/fr.json": json.dumps({"hello": "Bonjour C"}).encode("utf-8"),
            "d/fr.json": json.dumps({"hello": "Bonjour D"}).encode("utf-8"),
        })
        src = self.tmp / "two-ask.zip"
        src.write_bytes(data)
        answers = iter([0, None])

        def ask_fn(opts):
            return next(answers)

        r = self.run_ext(str(src), ask_fn=ask_fn)
        self.assertEqual(r.decision, "done")
        self.assertEqual([(f.path, f.locale) for f in r.files], [("a/en.json", "en")])
        self.assertEqual(len(r.candidates), 1)
        self.assertEqual(r.candidates[0]["locale"], "fr")

    @pytest.mark.needs_fixture("unity/sample.unity3d")
    def test_pin_file_bypasses_arbitration(self):
        r = self.run_ext(self.fix(UNITY),
                         pin_file="sample.unity3d::CAB-0123456789abcdef0123456789abcdef::greeting")
        self.assertEqual(r.decision, "done")
        self.assertEqual(r.total_entries, 1)
        f = r.files[0]
        self.assertEqual(f.plugin, "generic_strings")
        self.assertEqual(f.source_kind, "raw_scan")
        self.assertTrue(any("dò chuỗi thô" in w for w in f.warnings))
        self.assertEqual(self.read_out_json("greeting.json"),
                         {"off_00000000": "Xin chao 9 Kings!"})
        self.assertIn("off_00000000", r.generated_keys)

    def test_pin_missing_file_is_none(self):
        r = self.run_ext(self.fix(GAME1), pin_file="khong-co.json")
        self.assertEqual(r.decision, "none")
        self.assertTrue(any(e["code"] == "E_INPUT_NOT_FOUND" for e in r.errors))

    @pytest.mark.needs_fixture("unity/sample.unity3d")
    def test_single_option_ask_auto_selects(self):
        # config (TextAsset JSON không đuôi) chỉ có một lựa chọn đạt ngưỡng.
        r = self.run_ext(self.fix(UNITY))
        self.assertEqual(r.decision, "done")
        self.assertEqual([f.path for f in r.files],
                         ["sample.unity3d::CAB-0123456789abcdef0123456789abcde0::config"])


@pytest.mark.needs_fixture("unity/sample.unity3d")
class TestUnityExtraction(ExtractionCase):
    def test_unity_engine_certain(self):
        r = self.run_ext(self.fix(UNITY))
        self.assertEqual(r.engine, "unity")
        self.assertEqual(r.engine_level, "CHẮC CHẮN")
        self.assertEqual(r.decision, "done")

    def test_unity_config_auto_extracts_via_sniff(self):
        r = self.run_ext(self.fix(UNITY))
        self.assertEqual(r.total_entries, 3)
        f = r.files[0]
        self.assertEqual(f.plugin, "json_text")
        self.assertEqual(f.exported, "config.json")
        self.assertEqual(self.read_out_json("config.json"),
                         {"level": "1", "lang": "vi", "title": "9 Kings"})

    def test_unity_raw_strings_skipped_by_default(self):
        r = self.run_ext(self.fix(UNITY))
        skipped = {s["path"]: s["reason"] for s in r.skipped}
        self.assertIn("sample.unity3d::CAB-0123456789abcdef0123456789abcdef::greeting", skipped)
        self.assertIn("dưới ngưỡng", skipped[
            "sample.unity3d::CAB-0123456789abcdef0123456789abcdef::longtext"])

    def test_unity_target_info(self):
        root = disc.open_source_vfs(self.fix(UNITY), Limits())
        info = ext._unity_target_info(
            "sample.unity3d::CAB-0123456789abcdef0123456789abcde0::config", root, Limits())
        self.assertEqual(info, ("bundle", "sample.unity3d",
                                "CAB-0123456789abcdef0123456789abcde0", "config"))
        self.assertIsNone(ext._unity_target_info("localization/en.json", root, Limits()))
        self.assertIsNone(ext._unity_target_info("inner.zip::en.json", root, Limits()))


class TestCheckpoint(ExtractionCase):
    def test_checkpoint_saved_and_reused(self):
        r1 = self.extract_game1(resume=False)
        self.assertFalse(r1.checkpoint_used)
        self.assertTrue((self.out / "extraction_checkpoint.json").exists())
        r2 = self.extract_game1(resume=True)
        self.assertTrue(r2.checkpoint_used)
        self.assertEqual(r2.total_entries, r1.total_entries)
        self.assertEqual([f.exported for f in r2.files], [f.exported for f in r1.files])

    def test_checkpoint_rebuilds_ir(self):
        self.extract_game1()
        r2 = self.extract_game1(resume=True)
        self.assertIsNotNone(r2.ir)
        # IR dựng lại từ checkpoint chỉ có key duy nhất (21): bản ghi trùng
        # "dup" thứ hai không lưu trong checkpoint kv (không ảnh hưởng dịch
        # vì bản đầu đã thắng); lần parse mới có đủ 22.
        self.assertEqual(len(r2.ir.entries()), 21)

    def test_checkpoint_corrupt_falls_back(self):
        self.extract_game1()
        (self.out / "extraction_checkpoint.json").write_text("{hong", encoding="utf-8")
        r2 = self.extract_game1(resume=True)
        self.assertFalse(r2.checkpoint_used)
        self.assertEqual(r2.decision, "done")
        self.assertEqual(r2.total_entries, 21)


class TestApplyEdits(ExtractionCase):
    def _extract(self, source, **kw):
        return self.run_ext(source, **kw)

    def _edit_json(self, name, **changes):
        data = self.read_out_json(name)
        data.update(changes)
        edited = self.tmp / name
        edited.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(edited)

    def _apply(self, edited, report="extraction_report.json", **kw):
        kw.setdefault("registry", self.reg)
        kw.setdefault("out_dir", str(self.tmp / "apply-out"))
        kw.setdefault("tempdir", str(self.app.paths.temp))
        kw.setdefault("source", self.fix(GAME1))
        return ext.apply_edits(edited_json=edited,
                               report_path=str(self.out / report), **kw)

    def test_apply_plain_csv_only_matching_locale(self):
        self.extract_game1()
        edited = self._edit_json("English.json", play="Chơi", khong_co="bỏ qua")
        res = self._apply(edited, allow_write=True)
        self.assertEqual(res["changed"], 1)
        self.assertEqual(res["skipped_keys"], ["khong_co"])
        self.assertIsNotNone(res["written"])
        content = Path(res["written"]).read_text(encoding="utf-8")
        self.assertIn("play,Chơi,开始", content)   # cột zh giữ nguyên
        self.assertIn("quit,Quit,退出", content)

    def test_apply_dry_run_writes_nothing(self):
        self.extract_game1()
        edited = self._edit_json("English.json", play="Chơi")
        res = self._apply(edited, allow_write=False)
        self.assertTrue(res["dry_run"])
        self.assertEqual(res["changed"], 1)
        self.assertIsNone(res["written"])
        self.assertFalse((self.tmp / "apply-out").exists())

    @pytest.mark.needs_fixture("unity/sample.unity3d")
    def test_apply_unity_bundle_roundtrip(self):
        r = self._extract(self.fix(UNITY))
        edited = self._edit_json("config.json", title="9 Kings VIET NAM")
        res = ext.apply_edits(
            edited_json=edited, report_path=str(self.out / "extraction_report.json"),
            registry=self.reg, out_dir=str(self.tmp / "apply-out"),
            allow_write=True, tempdir=str(self.app.paths.temp),
            source=self.fix(UNITY))
        self.assertEqual(res["changed"], 1)
        self.assertEqual(res["write_mode_used"], "REBUILD_SECTION")  # dài hơn -> leo thang
        self.assertEqual(res["unit"], "bundle")
        self.assertTrue(res["written"].endswith("sample/sample.unity3d"))
        # Đọc lại bundle đã patch: config đổi, greeting/longtext nguyên.
        from sdk import unity as unity_sdk
        data = Path(res["written"]).read_bytes()
        bundle = unity_sdk.parse_bundle(data, unity_sdk.UnityBackend("AUTO", Limits()), Limits())
        scripts = {}
        for name in bundle.entry_names():
            sf = unity_sdk.parse_serialized(bundle.get_entry(name), Limits())
            for _pid, nm, script in sf.text_assets():
                scripts[nm] = script
        self.assertIn("9 Kings VIET NAM", scripts["config"])
        self.assertEqual(scripts["greeting"], "Xin chao 9 Kings!")

    @pytest.mark.needs_fixture("unity/sample.unity3d")
    def test_apply_unity_shorter_title_still_patches(self):
        # Title ngắn hơn nhưng bản build của plugin (pretty-print) vẫn dài
        # hơn script gốc (compact) của fixture -> leo thang REBUILD_SECTION;
        # quan trọng là patch đúng và các TextAsset khác nguyên vẹn.
        self._extract(self.fix(UNITY))
        edited = self._edit_json("config.json", title="9K")
        res = ext.apply_edits(
            edited_json=edited, report_path=str(self.out / "extraction_report.json"),
            registry=self.reg, out_dir=str(self.tmp / "apply-out"),
            allow_write=True, tempdir=str(self.app.paths.temp),
            source=self.fix(UNITY))
        self.assertEqual(res["changed"], 1)
        self.assertEqual(res["write_mode_used"], "REBUILD_SECTION")
        from sdk import unity as unity_sdk
        data = Path(res["written"]).read_bytes()
        bundle = unity_sdk.parse_bundle(data, unity_sdk.UnityBackend("AUTO", Limits()), Limits())
        scripts = {}
        for name in bundle.entry_names():
            sf = unity_sdk.parse_serialized(bundle.get_entry(name), Limits())
            for _pid, nm, script in sf.text_assets():
                scripts[nm] = script
        self.assertIn('"title": "9K"', scripts["config"])
        self.assertEqual(scripts["greeting"], "Xin chao 9 Kings!")

    @pytest.mark.needs_fixture("unity/sample.unity3d")
    def test_apply_unity_dry_run(self):
        self._extract(self.fix(UNITY))
        edited = self._edit_json("config.json", title="9 Kings VIET NAM")
        res = ext.apply_edits(
            edited_json=edited, report_path=str(self.out / "extraction_report.json"),
            registry=self.reg, out_dir=str(self.tmp / "apply-out"),
            allow_write=False, tempdir=str(self.app.paths.temp),
            source=self.fix(UNITY))
        self.assertTrue(res["dry_run"])
        self.assertIsNone(res["written"])
        self.assertEqual(res["write_mode_used"], "REBUILD_SECTION")

    def test_apply_readonly_plugin_refuses(self):
        self.extract_game1()
        rep = self.read_out_json("extraction_report.json")
        rep["files"] = [{"path": "x.db", "plugin": "sqlite_loc", "locale": "en",
                         "exported": "English.json"}]
        rp = self.tmp / "rep2.json"
        rp.write_text(json.dumps(rep), encoding="utf-8")
        edited = self._edit_json("English.json", play="Chơi")
        with self.assertRaises(PB3Error) as cm:
            ext.apply_edits(edited_json=edited, report_path=str(rp), registry=self.reg,
                            out_dir=str(self.tmp / "apply-out"), allow_write=True,
                            tempdir=str(self.app.paths.temp), source=self.fix(GAME1))
        self.assertEqual(cm.exception.code, "E_WRITE_DISABLED")

    def test_apply_missing_report_entry(self):
        self.extract_game1()
        edited = self.tmp / "khong-co.json"
        edited.write_text('{"a": "b"}', encoding="utf-8")
        with self.assertRaises(PB3Error) as cm:
            self._apply(str(edited), allow_write=True)
        self.assertEqual(cm.exception.code, "E_INPUT_NOT_FOUND")

    def test_apply_unparsed_entry_kept(self):
        data = _zip_bytes({"ui.xlf": Path(self.fix(XLIFF)).read_bytes()})
        src = self.tmp / "xliff.zip"
        src.write_bytes(data)
        r = self.run_ext(str(src))
        self.assertEqual(r.decision, "done")
        f = r.files[0]
        self.assertEqual(f.entries, 6)
        exported = self.read_out_json(f.exported)
        # Entry unparsed (markup <g>) bị BỎ QUA khỏi file xuất theo đặc tả.
        self.assertNotIn("styled", exported)
        self.assertEqual(exported["greeting"], "Xin chào")  # key = resname
        edited = self.tmp / f.exported
        edited.write_text(json.dumps({**exported, "greeting": "Chào"}, ensure_ascii=False),
                          encoding="utf-8")
        res = ext.apply_edits(
            edited_json=str(edited), report_path=str(self.out / "extraction_report.json"),
            registry=self.reg, out_dir=str(self.tmp / "apply-out"),
            allow_write=True, tempdir=str(self.app.paths.temp), source=str(src))
        self.assertEqual(res["changed"], 1)
        content = Path(res["written"]).read_bytes()
        self.assertIn(b"<g", content)      # markup unparsed giữ nguyên trong file
        self.assertIn("Chào".encode("utf-8"), content)


class TestGroupedStringTableJoin(unittest.TestCase):
    def _st(self, name, locale, entries):
        return {"name": name, "locale": locale, "entries": dict(entries)}

    def _shared(self, name, coll, entries):
        return {"name": name, "collection": coll, "entries": dict(entries)}

    def test_grouped_keeps_collections_separate(self):
        shared = [self._shared("B Shared", "Buildings", {1: "Farm", 2: "Castle"}),
                  self._shared("K Shared", "Kings", {1: "Nothing"})]
        tables = [("bun", self._st("Buildings_en", "en", {1: "Farm", 2: "Castle"})),
                  ("bun", self._st("Kings_en", "en", {1: "King of Nothing"}))]
        grouped, notes, conflicts, universe = ext.join_string_tables_grouped(tables, shared)
        self.assertEqual(notes, [])
        self.assertEqual(conflicts, [])
        self.assertEqual(grouped[("bun", "en", "Buildings")],
                         {"Farm": "Farm", "Castle": "Castle"})
        self.assertEqual(grouped[("bun", "en", "Kings")],
                         {"Nothing": "King of Nothing"})
        self.assertEqual(universe["Buildings"], {1: "Farm", 2: "Castle"})

    def test_grouped_value_conflict_recorded_keep_first(self):
        shared = [self._shared("B Shared", "Buildings", {1: "Farm"})]
        tables = [("bun", self._st("Buildings_en", "en", {1: "Farm"})),
                  ("bun", self._st("Buildings_en", "en", {1: "FARM!" }))]
        grouped, _notes, conflicts, _u = ext.join_string_tables_grouped(tables, shared)
        self.assertEqual(grouped[("bun", "en", "Buildings")], {"Farm": "Farm"})
        self.assertEqual(len(conflicts), 1)
        c = conflicts[0]
        self.assertEqual(c["key"], "Buildings/Farm")
        self.assertEqual(c["locale"], "en")
        self.assertEqual(c["kept"]["value"], "Farm")
        self.assertEqual(c["dropped"]["value"], "FARM!")
        self.assertIn("Buildings_en", c["dropped"]["source"])

    def test_grouped_shared_key_conflict_recorded(self):
        shared = [self._shared("B Shared", "Buildings", {1: "Farm"}),
                  self._shared("B Shared 2", "Buildings", {1: "Barn"})]
        tables = [("bun", self._st("Buildings_en", "en", {1: "Farm"}))]
        _g, _n, conflicts, universe = ext.join_string_tables_grouped(tables, shared)
        self.assertEqual(universe["Buildings"], {1: "Farm"})
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["kind"], "shared_key")
        self.assertEqual(conflicts[0]["kept"]["value"], "Farm")
        self.assertEqual(conflicts[0]["dropped"]["value"], "Barn")

    def test_grouped_id_fallback_and_notes(self):
        shared = [self._shared("B Shared", "Buildings", {1: "Farm"})]
        tables = [("bun", self._st("Buildings_en", "en", {1: "Farm", 9: "Orphan"})),
                  ("bun", self._st("NoColl_en", "en", {1: "X"})),
                  ("bun", self._st("Mystery", None, {1: "Y"}))]
        grouped, notes, _c, _u = ext.join_string_tables_grouped(tables, shared)
        self.assertEqual(grouped[("bun", "en", "Buildings")]["id_9"], "Orphan")
        self.assertEqual(grouped[("bun", "en", "NoColl")], {"id_1": "X"})
        self.assertEqual(len(notes), 3)

    def test_grouped_merged_values_match_flat_join(self):
        shared = [self._shared("B Shared", "Buildings", {1: "Farm", 2: "Castle"}),
                  self._shared("K Shared", "Kings", {1: "Nothing"})]
        tables = [("bun", self._st("Buildings_en", "en", {1: "Farm", 2: "Castle"})),
                  ("bun", self._st("Kings_en", "en", {1: "King of Nothing"})),
                  ("bun", self._st("Buildings_zh", "zh", {1: "农场"}))]
        flat, _n = ext._join_string_tables(tables, shared)
        grouped, _n2, _c, _u = ext.join_string_tables_grouped(tables, shared)
        merged = {}
        for (_b, loc, _coll), kv in grouped.items():
            bucket = merged.setdefault(("bun", loc), {})
            for k, v in kv.items():
                bucket.setdefault(k, v)
        self.assertEqual(merged, flat)


class TestLanguageSourceBaseColumns(unittest.TestCase):
    def _item(self, codes):
        return {"languages": [{"name": "L%d" % i, "code": c}
                              for i, c in enumerate(codes)],
                "terms": []}

    def test_first_variant_wins_without_exact(self):
        picked, skipped, n_nonloc = ext._language_source_base_columns(
            self._item(["en", "zh-TW", "zh-CN"]))
        self.assertEqual(picked["zh"], ("zh-tw", 1))
        self.assertEqual(skipped, ["zh-cn"])
        self.assertEqual(n_nonloc, 0)

    def test_exact_base_code_wins_over_variant(self):
        picked, skipped, _n = ext._language_source_base_columns(
            self._item(["zh-CN", "zh", "en"]))
        self.assertEqual(picked["zh"], ("zh", 1))
        self.assertEqual(skipped, ["zh-cn"])
        picked2, _s2, _n2 = ext._language_source_base_columns(
            self._item(["en-GB", "en-US"]))
        self.assertEqual(picked2["en"], ("en-gb", 0))

    def test_empty_and_bad_codes_counted_nonlocale(self):
        _p, _s, n = ext._language_source_base_columns(
            self._item(["en", "", "xx!!"]))
        self.assertEqual(n, 2)  # "" và "xx!!" (mã gốc không phải chữ cái)
        picked, _s2, n2 = ext._language_source_base_columns(
            self._item(["en", ""]))
        self.assertEqual(n2, 1)
        self.assertEqual(list(picked), ["en"])


class TestUnityJunkFilter(unittest.TestCase):
    def test_unity_type_names_are_noise(self):
        for s in ("UnityEngine.UIElements.VisualElement",
                  "UnityEngine.Object, UnityEngine",
                  "UnityEngine.Rendering.UI.DebugUIHandlerButton, Unity.RenderPipelines.Core",
                  "Unity.RenderPipelines.Core.Runtime"):
            self.assertTrue(ext._mb_string_is_noise(s), s)

    def test_uss_class_names_are_noise(self):
        for s in ("unity-base-slider", "unity-button", ".unity-button",
                  "unity-base-slider__tracker"):
            self.assertTrue(ext._mb_string_is_noise(s), s)

    def test_dotted_namespaces_and_uxml_are_noise(self):
        for s in ("MGS.Debugging.UILogHandler",
                  "<UXML xmlns:ui=\"x\">",
                  "<ui:UXML>",
                  "styles.uss",
                  "panel.uxml"):
            self.assertTrue(ext._mb_string_is_noise(s), s)

    def test_real_text_is_not_noise(self):
        for s in ("Fire", "Unity is strength", "Dr. No", "Noto Sans",
                  "PromoCodeUI", "Start Simulation", "U.S.A", "a.b",
                  "King of War", "按继续。"):
            self.assertFalse(ext._mb_string_is_noise(s), s)


class TestExpandSerializedLanguageSource(unittest.TestCase):
    def test_routes_language_source_separately(self):
        ls_item = {"path_id": 7, "name": "LS", "kind": "language_source",
                   "languages": [{"name": "English", "code": "en"}],
                   "terms": [{"term": "T1", "values": ["Hi"]}],
                   "trailing_bytes": 0}
        gen_item = {"path_id": 8, "name": "G", "kind": "generic",
                    "locale": None, "strings": ["hello"]}

        class FakeSF:
            def text_assets(self):
                return []

            def monobehaviour_texts(self, warnings=None):
                return [ls_item, gen_item]

        _out, mb, _notes, n_ta, n_mb, n_str = ext._expand_serialized(
            FakeSF(), "", "v")
        self.assertEqual(n_ta, 0)
        self.assertEqual(n_mb, 2)
        self.assertEqual(len(mb["language_sources"]), 1)
        self.assertEqual(mb["language_sources"][0][1], ls_item)
        self.assertEqual(len(mb["generic"]), 1)
        self.assertEqual(n_str, 2)  # 1 term + 1 chuỗi generic


class TestLanguageSourceVirtualFiles(unittest.TestCase):
    def _item(self, terms, codes, name="LS"):
        return {"path_id": 1, "name": name, "kind": "language_source",
                "languages": [{"name": "L%d" % i, "code": c}
                              for i, c in enumerate(codes)],
                "terms": [{"term": t, "values": v} for t, v in terms],
                "trailing_bytes": 0}

    def test_vpath_distinct_from_generic_and_keeps_empty(self):
        item = self._item([("T1", ["Hi", "你好", "你好"]),
                           ("T2", ["", "X", "Y"])],
                          ["en", "zh-TW", "zh-CN"])
        logs = []
        out = ext._language_source_virtual_files(
            [("u/resources.assets", "", item)], logs.append)
        self.assertEqual(
            sorted(out),
            ["u/resources.assets::resources.langtable.monobehaviour.en.json",
             "u/resources.assets::resources.langtable.monobehaviour.zh.json"])
        # Ten file khac han file generic cung don vi + ngon ngu (khong ghi de).
        for vpath in out:
            self.assertNotIn("::resources.monobehaviour.", vpath)
            self.assertIn(".monobehaviour.", vpath)  # nhom __mono__ van nhan
        en = json.loads(out["u/resources.assets::resources.langtable.monobehaviour.en.json"])
        self.assertEqual(en, {"T1": "Hi", "T2": ""})  # chuoi rong giu lai
        zh = json.loads(out["u/resources.assets::resources.langtable.monobehaviour.zh.json"])
        self.assertEqual(zh, {"T1": "你好", "T2": "X"})  # zh -> bien the dau (zh-TW)

    def test_cab_unit_path_and_two_tables_keep_first(self):
        item1 = self._item([("T1", ["One", "Uno"])], ["en", "es"])
        item2 = self._item([("T1", ["1st", "Primero"]), ("T2", ["Two", "Dos"])],
                           ["en", "es"])
        logs = []
        out = ext._language_source_virtual_files(
            [("u/b.bundle", "CAB-abc", item1), ("u/b.bundle", "CAB-abc", item2)],
            logs.append)
        self.assertEqual(
            sorted(out),
            ["u/b.bundle::CAB-abc::CAB-abc.langtable.monobehaviour.en.json",
             "u/b.bundle::CAB-abc::CAB-abc.langtable.monobehaviour.es.json"])
        en = json.loads(out["u/b.bundle::CAB-abc::CAB-abc.langtable.monobehaviour.en.json"])
        self.assertEqual(en, {"T1": "One", "T2": "Two"})  # giu ban dau
        self.assertTrue(any("xung đột" in m for m in logs))


if __name__ == "__main__":
    unittest.main()
