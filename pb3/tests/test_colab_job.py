"""Test job handoff web -> Colab -> web (colab-handoff).

- Job build/validate: mặc định, link Drive -> ID, locale sai / trùng
  ngôn ngữ / worker http / job_id xấu đều fail-closed rõ ràng.
- Nguồn job theo thứ tự ưu tiên (dán > upload > Drive latest >
  Drive theo id > không có); file job hỏng thì BÁO LỖI, không lặng
  lẽ rơi về biến tay.
- Manifest: build/write/read round-trip, entry đếm key + md5.
- Notebook: cell job exec được local với JOB_JSON dán (không cần
  Colab, không mạng); cell Bước 1 không ghi đè biến từ cell job;
  cell trích/dịch ghi + cập nhật manifest.

Fixture TỰ DỰNG (dict/file tạm); không dùng mạng. Test notebook đọc
file .ipynb trong repo (skip nếu thiếu).
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

import bridge  # noqa: E402

bridge.WEB_ROOT = str(ROOT)
json.loads(bridge.boot())

from core.errors import PB3Error  # noqa: E402
from sdk import colab_job as cj  # noqa: E402

NOTEBOOK = ROOT.parent / "colab" / "extract-large-ipa.ipynb"
JS = ROOT.parent / "colab_handoff.js"

DEAD_CELLS_ID = "18zGyS-P9VHY-sOCdOHOw70msSzm6_eqM"
DST_ID = "1DfcaEn0hEfs88qv2pkO4ed6n4546TsP1"


def _job(**kw):
    base = {"game": "Dead Cells", "drive_file_id": DEAD_CELLS_ID}
    base.update(kw)
    return cj.build_job(**base)


class TestParseDriveFileId(unittest.TestCase):
    def test_bare_id(self):
        self.assertEqual(cj.parse_drive_file_id(DEAD_CELLS_ID), DEAD_CELLS_ID)

    def test_file_link(self):
        link = ("https://drive.google.com/file/d/%s/view?usp=sharing"
                % DST_ID)
        self.assertEqual(cj.parse_drive_file_id(link), DST_ID)

    def test_open_and_uc_links(self):
        self.assertEqual(
            cj.parse_drive_file_id(
                "https://drive.google.com/open?id=%s" % DST_ID), DST_ID)
        self.assertEqual(
            cj.parse_drive_file_id(
                "https://drive.google.com/uc?id=%s&export=download" % DST_ID),
            DST_ID)

    def test_whitespace_trimmed(self):
        self.assertEqual(cj.parse_drive_file_id("  %s\n" % DST_ID), DST_ID)

    def test_folder_link_rejected(self):
        with self.assertRaises(PB3Error) as ctx:
            cj.parse_drive_file_id(
                "https://drive.google.com/drive/folders/1abcDEFghiJKL")
        self.assertEqual(ctx.exception.code, "E_CONFIG_INVALID")
        # Thông báo cụ thể nằm ở .detail (message_vi là text chung của mã).
        self.assertIn("thư mục", ctx.exception.detail)

    def test_garbage_and_empty_rejected(self):
        for bad in ("", "   ", "hello world", "Dead Cells 3.5.15 VH.ipa",
                    "abc", "https://example.com/file"):
            with self.assertRaises(PB3Error, msg=bad):
                cj.parse_drive_file_id(bad)

    def test_short_bare_rejected(self):
        with self.assertRaises(PB3Error):
            cj.parse_drive_file_id("1abcDEFghi")


class TestSlugifyAndJobId(unittest.TestCase):
    def test_slug(self):
        self.assertEqual(cj.slugify_game("Dead Cells 3.5.15 VH"),
                         "dead-cells-3-5-15-vh")
        self.assertEqual(cj.slugify_game("9 Kings"), "9-kings")
        self.assertEqual(cj.slugify_game(""), "game")
        self.assertEqual(cj.slugify_game("   "), "game")

    def test_vietnamese_diacritics_stripped(self):
        self.assertEqual(cj.slugify_game("Đừng Starve"), "ung-starve")

    def test_job_id_prefix(self):
        jid = cj.new_job_id("Dead Cells")
        self.assertTrue(jid.startswith("dead-cells-"), jid)
        self.assertRegex(jid, r"^dead-cells-\d{8}-\d{6}$")


class TestBuildJob(unittest.TestCase):
    def test_defaults(self):
        job = _job()
        self.assertEqual(job["version"], 1)
        self.assertEqual(job["locales"], ["en", "zh"])
        self.assertEqual(job["from_lang"], "en")
        self.assertEqual(job["to_lang"], "vi")
        self.assertTrue(job["translate"])
        self.assertEqual(job["worker_url"], "")
        self.assertEqual(job["game_file_name"], "")
        self.assertTrue(job["job_id"].startswith("dead-cells-"))

    def test_link_normalized_to_bare_id(self):
        job = _job(drive_file_id="https://drive.google.com/file/d/%s/view"
                   % DST_ID)
        self.assertEqual(job["drive_file_id"], DST_ID)

    def test_full_options(self):
        job = _job(locales=["en"], from_lang="en", to_lang="de",
                   translate=False, worker_url="https://xxx.workers.dev",
                   game_file_name="Dead Cells 3.5.15 VH.ipa",
                   job_id="dead-cells-test-1")
        self.assertEqual(job["locales"], ["en"])
        self.assertEqual(job["to_lang"], "de")
        self.assertFalse(job["translate"])
        self.assertEqual(job["job_id"], "dead-cells-test-1")

    def test_locales_from_string(self):
        job = cj.validate_job({"game": "Tom", "drive_file_id": DST_ID,
                               "locales": "en"})
        self.assertEqual(job["locales"], ["en"])

    def test_bad_locale_rejected(self):
        with self.assertRaises(PB3Error):
            _job(locales=["fr"])

    def test_same_lang_rejected(self):
        with self.assertRaises(PB3Error):
            _job(from_lang="vi", to_lang="vi")

    def test_http_worker_rejected(self):
        with self.assertRaises(PB3Error):
            _job(worker_url="http://xxx.workers.dev")

    def test_bad_job_id_rejected(self):
        with self.assertRaises(PB3Error):
            _job(job_id="dead cells!")

    def test_missing_game_and_bad_version_rejected(self):
        with self.assertRaises(PB3Error):
            cj.build_job("", DEAD_CELLS_ID)
        with self.assertRaises(PB3Error):
            cj.validate_job({"version": 2, "game": "Tom",
                             "drive_file_id": DST_ID})

    def test_unknown_fields_kept(self):
        job = cj.validate_job({"game": "Tom", "drive_file_id": DST_ID,
                               "future_field": "x"})
        self.assertEqual(job["future_field"], "x")


class TestJobJsonRoundTrip(unittest.TestCase):
    def test_to_json_and_back(self):
        job = _job(job_id="dead-cells-20261118-090000")
        text = cj.job_to_json(job)
        self.assertTrue(text.endswith("\n"))
        back = cj.load_job_from_text(text)
        self.assertEqual(back["job_id"], job["job_id"])
        self.assertEqual(back["drive_file_id"], DEAD_CELLS_ID)
        self.assertEqual(back["locales"], ["en", "zh"])

    def test_job_file_name_and_drive_paths(self):
        job = _job(job_id="dead-cells-20261118-090000")
        self.assertEqual(cj.job_file_name(job),
                         "meorocky-job-dead-cells-20261118-090000.json")
        paths = cj.job_drive_paths(job)
        self.assertEqual(paths["job_path"],
                         "MeoRocky/jobs/dead-cells-20261118-090000.json")
        self.assertEqual(paths["latest_path"], "MeoRocky/jobs/latest.json")

    def test_corrupt_json_rejected(self):
        with self.assertRaises(PB3Error) as ctx:
            cj.load_job_from_text("{not json")
        self.assertEqual(ctx.exception.code, "E_CORRUPT")

    def test_empty_text_rejected(self):
        with self.assertRaises(PB3Error):
            cj.load_job_from_text("  ")

    def test_output_dir(self):
        self.assertEqual(
            cj.job_output_dir("Dead Cells 3.5.15 VH.ipa", "Dead Cells"),
            "MeoRocky-output/Dead Cells 3.5.15 VH")
        self.assertEqual(cj.job_output_dir("", "Tom"), "MeoRocky-output/Tom")


class TestResolveJob(unittest.TestCase):
    def _write(self, directory, name, text):
        path = os.path.join(directory, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_priority_pasted_over_upload_over_drive(self):
        with tempfile.TemporaryDirectory() as tmp:
            jobs_dir = os.path.join(tmp, "jobs")
            os.makedirs(jobs_dir)
            pasted = _job(job_id="pasted-1")
            uploaded = _job(job_id="upload-1")
            latest = _job(job_id="latest-1")
            upload = self._write(tmp, "meorocky-job.json",
                                 cj.job_to_json(uploaded))
            self._write(jobs_dir, "latest.json", cj.job_to_json(latest))
            job, src = cj.resolve_job(
                job_json_text=cj.job_to_json(pasted),
                upload_path=upload, drive_jobs_dir=jobs_dir)
            self.assertEqual((job["job_id"], src), ("pasted-1", "pasted"))
            job, src = cj.resolve_job(upload_path=upload,
                                      drive_jobs_dir=jobs_dir)
            self.assertEqual((job["job_id"], src), ("upload-1", "upload"))
            job, src = cj.resolve_job(drive_jobs_dir=jobs_dir)
            self.assertEqual((job["job_id"], src), ("latest-1", "drive-latest"))

    def test_drive_id_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            jobs_dir = os.path.join(tmp, "jobs")
            os.makedirs(jobs_dir)
            by_id = _job(job_id="by-id-1")
            self._write(jobs_dir, "by-id-1.json", cj.job_to_json(by_id))
            job, src = cj.resolve_job(drive_jobs_dir=jobs_dir,
                                      job_id="by-id-1")
            self.assertEqual((job["job_id"], src), ("by-id-1", "drive-id"))

    def test_none_when_nothing_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            job, src = cj.resolve_job(
                upload_path=os.path.join(tmp, "nope.json"),
                drive_jobs_dir=os.path.join(tmp, "jobs"))
            self.assertEqual((job, src), (None, None))

    def test_corrupt_job_file_raises_not_silent_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = self._write(tmp, "meorocky-job.json", "{hong")
            with self.assertRaises(PB3Error):
                cj.resolve_job(upload_path=bad)
            jobs_dir = os.path.join(tmp, "jobs")
            os.makedirs(jobs_dir)
            self._write(jobs_dir, "latest.json", "{hong")
            with self.assertRaises(PB3Error):
                cj.resolve_job(drive_jobs_dir=jobs_dir)


class TestManifest(unittest.TestCase):
    def test_entry_and_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = {"hello": "xin chào", "bye": "tạm biệt"}
            path = os.path.join(tmp, "en.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(src, fh, ensure_ascii=False)
            entry = cj.manifest_file_entry(path)
            self.assertEqual(entry["name"], "en.json")
            self.assertEqual(entry["keys"], 2)
            self.assertEqual(entry["bytes"], os.path.getsize(path))
            self.assertEqual(entry["md5"], hashlib.md5(
                open(path, "rb").read()).hexdigest())
            job = _job(job_id="dead-cells-20261118-090000",
                       game_file_name="Dead Cells 3.5.15 VH.ipa")
            man = cj.build_manifest(job, "extracted", [entry])
            self.assertEqual(man["status"], "extracted")
            self.assertEqual(man["out_dir"],
                             "MeoRocky-output/Dead Cells 3.5.15 VH")
            self.assertEqual(man["translated"], [])
            mpath = os.path.join(tmp, "out", "handoff-manifest.json")
            self.assertEqual(cj.write_manifest(mpath, man), mpath)
            back = cj.read_manifest(mpath)
            self.assertEqual(back["job_id"], job["job_id"])
            self.assertEqual(back["files"][0]["keys"], 2)

    def test_entry_rejects_non_dict(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bad.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("[1, 2]")
            with self.assertRaises(PB3Error):
                cj.manifest_file_entry(path)

    def test_bad_status_rejected(self):
        with self.assertRaises(PB3Error):
            cj.build_manifest(_job(), "running", [])

    def test_read_missing_raises_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            cj.read_manifest("/tmp/khong-co-manifest-nay.json")

    def test_read_corrupt_manifest_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "handoff-manifest.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write('{"version": 99}')
            with self.assertRaises(PB3Error):
                cj.read_manifest(path)

    def test_write_extract_and_update_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = os.path.join(tmp, "out")
            os.makedirs(out_dir)
            for name, kv in (("en.json", {"a": "1", "b": "2"}),
                             ("zh-CN.json", {"a": "甲"})):
                with open(os.path.join(out_dir, name), "w",
                          encoding="utf-8") as fh:
                    json.dump(kv, fh, ensure_ascii=False)
            job = _job(job_id="dead-cells-20261118-090000")
            man = cj.write_extract_manifest(
                out_dir, [{"file": "en.json", "keys": 2},
                          {"file": "zh-CN.json", "keys": 1}], job=job)
            self.assertEqual(man["status"], "extracted")
            self.assertEqual([e["name"] for e in man["files"]],
                             ["en.json", "zh-CN.json"])
            self.assertEqual(man["files"][0]["keys"], 2)
            # Dịch xong: cập nhật manifest cũ, giữ file đã trích.
            tr_path = os.path.join(out_dir, "translated.vi.json")
            with open(tr_path, "w", encoding="utf-8") as fh:
                json.dump({"a": "một", "b": "hai"}, fh, ensure_ascii=False)
            done = cj.update_manifest_after_translate(out_dir, tr_path,
                                                      job=job)
            self.assertEqual(done["status"], "done")
            self.assertEqual(len(done["files"]), 2)
            self.assertEqual(done["translated"][0]["name"],
                             "translated.vi.json")
            self.assertEqual(done["translated"][0]["keys"], 2)
            self.assertEqual(done["job_id"], job["job_id"])

    def test_update_manifest_without_old_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = os.path.join(tmp, "out")
            os.makedirs(out_dir)
            with open(os.path.join(out_dir, "en.json"), "w",
                      encoding="utf-8") as fh:
                json.dump({"a": "1"}, fh)
            tr_path = os.path.join(out_dir, "translated.vi.json")
            with open(tr_path, "w", encoding="utf-8") as fh:
                json.dump({"a": "một"}, fh, ensure_ascii=False)
            done = cj.update_manifest_after_translate(
                out_dir, tr_path,
                game="Dead Cells", drive_file_id=DEAD_CELLS_ID)
            self.assertEqual(done["status"], "done")
            self.assertEqual(len(done["files"]), 1)
            self.assertEqual(done["files"][0]["name"], "en.json")


def _notebook_cells():
    if not NOTEBOOK.is_file():
        raise unittest.SkipTest("thiếu notebook: %s" % NOTEBOOK)
    return json.loads(NOTEBOOK.read_text(encoding="utf-8"))["cells"]


def _cell_source(cell):
    return "".join(cell.get("source", []))


class TestNotebookJobCell(unittest.TestCase):
    def test_job_cell_executes_locally_with_pasted_job(self):
        cells = _notebook_cells()
        job_cells = [c for c in cells
                     if "resolve_job" in _cell_source(c)
                     and "JOB_JSON" in _cell_source(c)]
        self.assertEqual(len(job_cells), 1,
                         "notebook phải có đúng 1 cell job "
                         "(resolve_job + JOB_JSON)")
        src = _cell_source(job_cells[0])
        # Cell job không được import google.colab (phải exec được local).
        self.assertNotIn("google.colab", src)
        job = _job(job_id="dead-cells-20261118-090000",
                   game_file_name="Dead Cells 3.5.15 VH.ipa",
                   worker_url="https://xxx.workers.dev")
        # Mô phỏng đúng thao tác của người dùng: dán chuỗi job vào cell
        # (thay dòng JOB_JSON = "" bằng literal).
        self.assertIn('JOB_JSON = ""', src)
        src = src.replace('JOB_JSON = ""',
                          "JOB_JSON = " + repr(cj.job_to_json(job)), 1)
        ns = {"REPO_DIR": str(ROOT.parent)}
        exec(compile(src, "<cell job>", "exec"), ns)  # noqa: S102
        self.assertEqual(ns["DRIVE_FILE_ID"], DEAD_CELLS_ID)
        self.assertEqual(ns["GAME_FILE_NAME"], "Dead Cells 3.5.15 VH.ipa")
        self.assertEqual(ns["FROM_LANG"], "en")
        self.assertEqual(ns["TO_LANG"], "vi")
        self.assertTrue(ns["TRANSLATE_AFTER_EXTRACT"])
        self.assertEqual(ns["WORKER_URL"], "https://xxx.workers.dev")
        self.assertEqual(ns["JOB_ID"], "dead-cells-20261118-090000")
        self.assertEqual(ns["JOB_SOURCE"], "pasted")

    def test_step1_cell_respects_job_vars(self):
        cells = _notebook_cells()
        step1 = [c for c in cells if "find_ipa_by_name" in _cell_source(c)]
        self.assertEqual(len(step1), 1, "notebook phải có 1 cell Bước 1")
        src = _cell_source(step1[0])
        for var in ("GAME_FILE_NAME", "DRIVE_FILE_ID", "FROM_LANG",
                    "TO_LANG", "TRANSLATE_AFTER_EXTRACT", "WORKER_URL"):
            # Cell dùng nháy kép: globals().get("VAR", ...).
            self.assertIn('globals().get("%s"' % var, src,
                          "cell Bước 1 phải tôn trọng %s từ cell job" % var)
        self.assertNotIn('GAME_FILE_NAME = ""', src)
        self.assertNotIn('DRIVE_FILE_ID = ""', src)

    def test_extract_and_translate_cells_write_manifest(self):
        cells = _notebook_cells()
        extract = [c for c in cells if "bridge.extract" in _cell_source(c)]
        self.assertEqual(len(extract), 1)
        # Tên file manifest nằm trong colab_job.MANIFEST_NAME, cell chỉ
        # gọi helper (không hardcode tên file).
        self.assertIn("write_extract_manifest", _cell_source(extract[0]))
        self.assertIn("merged_files", _cell_source(extract[0]))
        translate = [c for c in cells
                     if "translate_json_file" in _cell_source(c)]
        self.assertEqual(len(translate), 1)
        self.assertIn("update_manifest_after_translate",
                      _cell_source(translate[0]))
        self.assertIn("status=done", _cell_source(translate[0]))

    def test_job_cell_comes_before_step1(self):
        cells = _notebook_cells()
        job_idx = next(i for i, c in enumerate(cells)
                       if "resolve_job" in _cell_source(c))
        step1_idx = next(i for i, c in enumerate(cells)
                         if "find_ipa_by_name" in _cell_source(c))
        self.assertLess(job_idx, step1_idx,
                        "cell job phải đứng trước cell Bước 1")

    def test_js_drive_regex_matches_python(self):
        # Chống lệch regex parse link Drive giữa JS và Python. Trong file
        # JS các pattern nằm trong regex literal nên dấu / được escape
        # (\/file\/d\/, \/folders\/); còn [?&]id= thì nguyên dạng.
        self.assertTrue(JS.is_file(), "thiếu colab_handoff.js")
        js = JS.read_text(encoding="utf-8")
        for pat in (r"\/file\/d\/", "[?&]id=", r"\/folders\/"):
            self.assertIn(pat, js, "colab_handoff.js thiếu pattern %r" % pat)


if __name__ == "__main__":
    unittest.main()
