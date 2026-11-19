"""Test gộp thành 1 file mỗi ngôn ngữ (Joseph 23:35, sdk/extraction.py).

Chế độ xuất "Gộp thành 1 file": các outcome ngôn ngữ thuộc tập lọc được
gộp thành đúng 1 FileOutcome mỗi locale (xuất en.json/zh.json), giữ thứ
tự nguồn + thứ tự key gốc, không sort_keys, không hậu tố #N; trùng key
giữ bản đầu + liệt kê xung đột; provenance từng key giữ cho (E).
File config/plist (locale None) giữ nguyên, không gộp.

Test này chỉ khóa hành vi của riêng bước gộp (hàm thuần
merge_language_outcomes + _export); số đo trên game thật (trùng key,
xung đột từng game, đối chiếu mẫu 2.086 key) nằm ở harness
exp/verify_analysis_merge_one_file.mjs — xem
exp/analysis_merge_one_file_results.md.
"""
import json
import os
import tempfile
import unittest

from sdk.extraction import (
    ExtractionRequest,
    ExtractionResult,
    FileOutcome,
    _apply_merge,
    _export,
    merge_language_outcomes,
)


def _out(path, locale, kv, plugin="json_text", source=None, exported=None):
    f = FileOutcome(path, plugin, locale, entries=len(kv), ok=len(kv),
                    kv=dict(kv), source=source or path)
    if exported:
        f.exported = exported
    return f


class MergeOutcomesCase(unittest.TestCase):
    def test_single_language_merges_to_one(self):
        files = [
            _out("b1::resources.monobehaviour.en.json", "en",
                 {"A": "one", "B": "two"}, source="b1::resources.assets",
                 exported="resources.monobehaviour.en.json"),
            _out("b1::en.json", "en", {"C": "three"}, source="b1::bundle",
                 exported="English.json"),
            _out("b1::config.json", None, {"X": "1"}),
        ]
        merged, stats = merge_language_outcomes(files, {"en"})
        self.assertEqual(len(merged), 2)  # 1 file gộp en + 1 file config
        en = merged[0]
        self.assertTrue(en.merged)
        self.assertEqual(en.locale, "en")
        self.assertEqual(list(en.kv), ["A", "B", "C"])  # thứ tự nguồn, không sort
        self.assertEqual(en.kv["C"], "three")
        self.assertEqual(en.merged_from,
                         ["resources.monobehaviour.en.json", "English.json"])
        self.assertEqual(en.merge_sources,
                         {"A": "b1::resources.assets", "B": "b1::resources.assets",
                          "C": "b1::bundle"})
        self.assertEqual(merged[1].locale, None)  # config giữ nguyên
        self.assertEqual(len(stats), 1)
        self.assertEqual(stats[0]["file"], "en.json")
        self.assertEqual(stats[0]["keys"], 3)
        self.assertEqual(stats[0]["entries_before"], 3)
        self.assertEqual(stats[0]["duplicates_identical"], 0)
        self.assertEqual(stats[0]["conflicts_total"], 0)

    def test_both_languages_give_two_files(self):
        files = [
            _out("u::a.en.json", "en", {"A": "one"}, exported="a.en.json"),
            _out("u::a.zh.json", "zh", {"A": "yi"}, exported="a.zh.json"),
            _out("u::b.en.json", "en", {"B": "two"}, exported="b.en.json"),
            _out("u::b.zh.json", "zh", {"B": "er"}, exported="b.zh.json"),
        ]
        merged, stats = merge_language_outcomes(files, {"en", "zh"})
        self.assertEqual(len(merged), 2)
        self.assertEqual([f.locale for f in merged], ["en", "zh"])
        self.assertEqual(list(merged[0].kv), ["A", "B"])
        self.assertEqual(list(merged[1].kv), ["A", "B"])
        self.assertEqual({s["locale"] for s in stats}, {"en", "zh"})

    def test_identical_duplicate_merged_once(self):
        # Cùng key + cùng giá trị ở 2 nguồn (vd tên riêng): gộp 1 bản,
        # đếm duplicates_identical, không mất chuỗi.
        files = [
            _out("u::a.en.json", "en", {"Same": "Odyssey", "A": "one"},
                 exported="a.en.json"),
            _out("u::b.en.json", "en", {"Same": "Odyssey", "B": "two"},
                 exported="b.en.json"),
        ]
        merged, stats = merge_language_outcomes(files, {"en"})
        self.assertEqual(len(merged), 1)
        self.assertEqual(list(merged[0].kv), ["Same", "A", "B"])
        self.assertEqual(stats[0]["duplicates_identical"], 1)
        self.assertEqual(stats[0]["entries_before"], 4)
        self.assertEqual(stats[0]["keys"], 3)

    def test_conflict_keeps_first_and_lists_all(self):
        # Trùng key KHÁC giá trị: giữ bản đầu, liệt kê xung đột kèm nguồn.
        files = [
            _out("u::a.en.json", "en", {"K": "first"}, source="src-a",
                 exported="a.en.json"),
            _out("u::b.en.json", "en", {"K": "second"}, source="src-b",
                 exported="b.en.json"),
            _out("u::c.en.json", "en", {"K": "third"}, source="src-c",
                 exported="c.en.json"),
        ]
        merged, stats = merge_language_outcomes(files, {"en"})
        self.assertEqual(merged[0].kv["K"], "first")
        self.assertEqual(merged[0].merge_sources["K"], "src-a")
        self.assertEqual(stats[0]["conflicts_total"], 2)
        self.assertEqual(stats[0]["conflicts"][0]["key"], "K")
        self.assertEqual(stats[0]["conflicts"][0]["kept"],
                         {"value": "first", "source": "src-a"})
        self.assertEqual(stats[0]["conflicts"][0]["dropped"],
                         {"value": "second", "source": "src-b"})
        self.assertEqual(stats[0]["conflicts"][1]["dropped"]["value"], "third")

    def test_non_target_locale_kept(self):
        files = [
            _out("u::a.en.json", "en", {"A": "one"}, exported="a.en.json"),
            _out("u::a.de.json", "de", {"A": "eins"}, exported="a.de.json"),
        ]
        merged, stats = merge_language_outcomes(files, {"en"})
        self.assertEqual(len(merged), 2)
        self.assertTrue(merged[0].merged)
        self.assertFalse(merged[1].merged)
        self.assertEqual(merged[1].kv, {"A": "eins"})

    def test_no_targets_returns_unchanged(self):
        files = [_out("u::a.en.json", "en", {"A": "one"})]
        merged, stats = merge_language_outcomes(files, set())
        self.assertEqual(merged, files)
        self.assertEqual(stats, [])

    def test_empty_kv_outcome_not_merged_away(self):
        files = [
            _out("u::a.en.json", "en", {"A": "one"}, exported="a.en.json"),
            _out("u::b.en.json", "en", {}, exported="b.en.json"),
        ]
        merged, _stats = merge_language_outcomes(files, {"en"})
        self.assertEqual(len(merged), 2)
        self.assertTrue(merged[0].merged)
        self.assertFalse(merged[1].merged)

    def test_apply_merge_updates_total(self):
        res = ExtractionResult(ok=True, decision="done", total_entries=5)
        res.files = [
            _out("u::a.en.json", "en", {"A": "one", "B": "two"},
                 exported="a.en.json"),
            _out("u::b.en.json", "en", {"B": "two", "C": "three"},
                 exported="b.en.json"),
            _out("u::cfg.json", None, {"X": "1"}),
        ]
        _apply_merge(res, {"en"})
        self.assertEqual(len(res.files), 2)
        # BUG#2 (2026-11-17): total chỉ đếm key của file locale ĐÃ GỘP
        # (3 key en); file config passthrough (1 key) không nằm trong
        # output ở chế độ gộp nên không đếm nữa (trước đây: 3 + 1 = 4).
        self.assertEqual(res.total_entries, 3)
        self.assertEqual(len(res.merged_files), 1)
        self.assertEqual(res.merged_files[0]["duplicates_identical"], 1)

    def test_request_defaults_merge_off(self):
        req = ExtractionRequest(source="game.ipa")
        self.assertFalse(req.merge_one_file)

    def test_untagged_only_rescued_into_first_target(self):
        # Hồi quy e2e mod_search 2026-11-17: game chỉ có file JSON trần
        # không gắn locale (game.json 3 key) thì merge bị bỏ qua +
        # _export bỏ outcome chưa gộp + bridge lọc f.exported -> ra
        # 0 file dù total_entries = 3. Nay gộp vào locale đầu tiên
        # yêu cầu ("en") thay vì bỏ hết.
        game = _out("game.json", None,
                    {"setting": "Cài đặt", "play": "Chơi", "quit": "Thoát"},
                    source="game.json", exported=None)
        junk = _out("u::anim.json", None, {"a": "1"})
        junk.origin_kind = "generic_flat"
        merged, stats = merge_language_outcomes([game, junk], {"en", "zh"})
        self.assertEqual(len(merged), 2)  # 1 file gộp en + 1 file generic
        en = merged[0]
        self.assertTrue(en.merged)
        self.assertEqual(en.locale, "en")
        self.assertEqual(list(en.kv), ["setting", "play", "quit"])
        self.assertEqual(en.merged_from, ["game.json"])
        self.assertEqual(len(stats), 1)
        self.assertEqual(stats[0]["file"], "en.json")
        self.assertEqual(stats[0]["keys"], 3)
        self.assertFalse(merged[1].merged)  # generic_flat không được cứu
        self.assertEqual(merged[1].locale, None)

    def test_untagged_not_rescued_when_tagged_exists(self):
        # Guard: game CÓ locale gắn nhãn (ở đây chỉ có zh) thì text chưa
        # gắn locale KHÔNG được gộp bừa vào en — chỉ cứu khi game không
        # có outcome gắn locale nào. Đã đo trên game thật: 9K/PB3/EK2
        # đều có outcome untagged là rác/config/animation (vd
        # EK_Avatar_Animation 22313 entry) nên tuyệt đối không gộp bừa.
        files = [
            _out("u::a.zh.json", "zh", {"A": "yi"}, exported="a.zh.json"),
            _out("u::cfg.json", None, {"X": "1"}),
        ]
        merged, stats = merge_language_outcomes(files, {"en", "zh"})
        self.assertEqual(len(merged), 2)
        self.assertTrue(merged[0].merged)
        self.assertEqual(merged[0].locale, "zh")
        self.assertEqual(list(merged[0].kv), ["A"])
        self.assertFalse(merged[1].merged)  # config giữ nguyên, không gộp
        self.assertEqual(merged[1].locale, None)
        self.assertEqual(len(stats), 1)
        self.assertEqual(stats[0]["locale"], "zh")


class ExportMergedCase(unittest.TestCase):
    def test_export_writes_en_json_and_sidecar(self):
        with tempfile.TemporaryDirectory() as tmp:
            req = ExtractionRequest(source="game.ipa", out_dir=tmp)
            res = ExtractionResult(ok=True, decision="done")
            res.files = [
                _out("u::a.en.json", "en", {"B": "two", "A": "one"},
                     source="u::resources.assets", exported="a.en.json"),
                _out("u::b.en.json", "en", {"C": "three"},
                     source="u::sharedassets0.assets", exported="b.en.json"),
                _out("u::cfg.json", None, {"X": "1"}),
            ]
            _apply_merge(res, {"en"})
            _export(req, res, "hash", [])
            # File gộp đúng tên en.json, giữ thứ tự gốc, không sort_keys.
            merged_path = os.path.join(tmp, "en.json")
            self.assertTrue(os.path.isfile(merged_path))
            with open(merged_path, encoding="utf-8") as fh:
                text = fh.read()
            doc = json.loads(text)
            self.assertEqual(list(doc), ["B", "A", "C"])
            self.assertFalse(os.path.exists(os.path.join(tmp, "a.en.json")))
            # File config vẫn xuất riêng.
            self.assertTrue(os.path.isfile(os.path.join(tmp, "cfg.json")))
            # Sidecar provenance: từng key lấy từ nguồn nào.
            with open(os.path.join(tmp, "en.provenance.json"),
                      encoding="utf-8") as fh:
                side = json.load(fh)
            self.assertEqual(side["format"], "beartea-merge-provenance/1")
            self.assertEqual(side["file"], "en.json")
            self.assertEqual(side["keys"],
                             {"B": "u::resources.assets",
                              "A": "u::resources.assets",
                              "C": "u::sharedassets0.assets"})
            self.assertEqual(side["merged_from"], ["a.en.json", "b.en.json"])
            # Report ghi mục gộp.
            with open(os.path.join(tmp, "extraction_report.json"),
                      encoding="utf-8") as fh:
                rep = json.load(fh)
            self.assertEqual(len(rep["merged_files"]), 1)
            self.assertEqual(rep["merged_files"][0]["file"], "en.json")
            self.assertEqual(rep["merged_files"][0]["keys"], 3)
            # Checkpoint KHÔNG chứa bản gộp (dẫn xuất, không tái dùng).
            with open(os.path.join(tmp, "extraction_checkpoint.json"),
                      encoding="utf-8") as fh:
                ckpt = json.load(fh)
            self.assertNotIn("<merged:en>", ckpt.get("files", {}))


class ConflictsJsonCase(unittest.TestCase):
    def test_export_writes_conflicts_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            req = ExtractionRequest(source="game.ipa", out_dir=tmp)
            res = ExtractionResult(ok=True, decision="done")
            res.files = [
                _out("u::a.en.json", "en", {"A": "one", "B": "two"},
                     source="u::resources.assets", exported="a.en.json"),
                _out("u::b.en.json", "en", {"A": "uno"},
                     source="u::sharedassets0.assets", exported="b.en.json"),
            ]
            _apply_merge(res, {"en"})
            _export(req, res, "hash", [])
            path = os.path.join(tmp, "conflicts.json")
            self.assertTrue(os.path.isfile(path))
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
            self.assertEqual(doc["format"], "beartea-merge-conflicts/1")
            self.assertEqual(doc["conflicts_total"], 1)
            self.assertEqual(len(doc["conflicts"]), 1)
            c = doc["conflicts"][0]
            self.assertEqual(c["locale"], "en")
            self.assertEqual(c["file"], "en.json")
            self.assertEqual(c["key"], "A")
            self.assertEqual(c["kept"], {"value": "one",
                                         "source": "u::resources.assets"})
            self.assertEqual(c["dropped"], {"value": "uno",
                                            "source": "u::sharedassets0.assets"})
            self.assertEqual(c["rule"], "keep-first")

    def test_export_no_conflicts_json_when_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            req = ExtractionRequest(source="game.ipa", out_dir=tmp)
            res = ExtractionResult(ok=True, decision="done")
            res.files = [
                _out("u::a.en.json", "en", {"A": "one"},
                     source="u::resources.assets", exported="a.en.json"),
                _out("u::b.en.json", "en", {"B": "two"},
                     source="u::sharedassets0.assets", exported="b.en.json"),
            ]
            _apply_merge(res, {"en"})
            _export(req, res, "hash", [])
            self.assertFalse(os.path.exists(os.path.join(tmp, "conflicts.json")))


if __name__ == "__main__":
    unittest.main()
