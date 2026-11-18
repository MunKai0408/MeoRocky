"""Job handoff web -> Colab -> web cho file IPA lớn (Colab handoff).

Một "job" là file JSON nhỏ mô tả việc cần làm trên Colab: game nào
(Drive file ID), trích locale nào, có dịch sau khi trích không, dịch
bằng worker nào. Web tạo job, Colab đọc job (không cần gõ tay ID),
trích/dịch xong ghi manifest để web biết khi nào xong rồi nhập kết quả.

Nguồn job trong notebook (theo thứ tự ưu tiên, xem `resolve_job`):
1. chuỗi JOB_JSON dán trực tiếp vào cell job (sao chép từ web);
2. file `/content/meorocky-job.json` (tải job.json từ web rồi upload
   vào khung Files của Colab);
3. `MeoRocky/jobs/latest.json` trên Drive (web ghi qua Drive API);
4. `MeoRocky/jobs/<job_id>.json` trên Drive;
5. không có job nào -> notebook dùng biến điền tay cũ trong cell
   Bước 1 (hành vi cũ, giữ nguyên).

Chỉ thư viện chuẩn + core.errors. Python >= 3.9. Không gọi mạng.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from core.errors import PB3Error

JOB_SCHEMA_VERSION = 1
JOB_DIR_NAME = "MeoRocky/jobs"
JOB_LATEST_NAME = "latest.json"
OUTPUT_ROOT_NAME = "MeoRocky-output"
MANIFEST_NAME = "handoff-manifest.json"
JOB_UPLOAD_NAME = "meorocky-job.json"
CREATED_BY = "MeoRocky web"

# Locale notebook hỗ trợ trích (Bước 2 gọi bridge.extract với
# locales=['en', 'zh']); locale khác fail-closed với thông báo rõ.
SUPPORTED_LOCALES = ("en", "zh")

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,}$")
_BARE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{25,}$")
_FILE_LINK_RE = re.compile(r"/file/d/([A-Za-z0-9_-]{10,})")
_QUERY_ID_RE = re.compile(r"[?&]id=([A-Za-z0-9_-]{10,})")
_FOLDER_LINK_RE = re.compile(r"/folders/([A-Za-z0-9_-]{10,})")
_LANG_RE = re.compile(r"^[a-z]{2,3}$")
_JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_HTTP_URL_RE = re.compile(r"^https://\S+$")


def _err(code, message, hint=""):
    return PB3Error(code, message, hint=hint)


def parse_drive_file_id(text):
    """Lấy Drive file ID từ ID trần hoặc link Drive.

    Nhận: ID trần (vd ``1DfcaEn0hEfs88qv2pkO4ed6n4546TsP1``), link
    ``.../file/d/<id>/view``, ``open?id=<id>``, ``uc?id=<id>``.
    Link thư mục (``/folders/<id>``) bị từ chối rõ ràng — cần link của
    FILE .ipa, không phải thư mục chứa nó. Lỗi -> PB3Error
    E_CONFIG_INVALID với thông báo tiếng Việt.
    """
    raw = str(text or "").strip()
    if not raw:
        raise _err("E_CONFIG_INVALID",
                   "Chưa nhập Drive file ID hoặc link Drive.",
                   "Mở file .ipa trên drive.google.com rồi copy link, "
                   "hoặc copy đoạn ID giữa 'file/d/' và '/view'.")
    if _FOLDER_LINK_RE.search(raw):
        raise _err("E_CONFIG_INVALID",
                   "Đây là link thư mục Drive, không phải link file.",
                   "Mở chính file .ipa (không phải thư mục chứa nó) rồi "
                   "copy link của file.")
    for rx in (_FILE_LINK_RE, _QUERY_ID_RE):
        m = rx.search(raw)
        if m:
            return m.group(1)
    if _BARE_ID_RE.match(raw):
        return raw
    if _ID_RE.match(raw):
        # Chuỗi ngắn giống ID nhưng không đủ dài để tin: có thể là
        # tên file hoặc ID cắt dở — báo rõ thay vì gửi lên Drive.
        raise _err("E_CONFIG_INVALID",
                   "ID Drive quá ngắn, có thể đã copy thiếu.",
                   "Copy lại toàn bộ đoạn ID (thường dài 33 ký tự).")
    raise _err("E_CONFIG_INVALID",
               "Không nhận ra ID/link Drive: %r." % raw[:80],
               "Dán nguyên link file trên drive.google.com (dạng "
               "'.../file/d/<ID>/view') hoặc chỉ dán ID.")


def slugify_game(name):
    """Tên game -> slug an toàn cho job_id ('Dead Cells 3.5.15 VH' ->
    'dead-cells-3-5-15-vh'; rỗng -> 'game'). Dấu tiếng Việt bị lược
    (NFKD + bỏ ký tự không ASCII)."""
    text = unicodedata.normalize("NFKD", str(name or "").strip().lower())
    text = text.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:40].strip("-")
    return slug or "game"


def new_job_id(game):
    """job_id mặc định: <slug>-<YYYYMMDD-HHMMSS UTC>."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return "%s-%s" % (slugify_game(game), stamp)


def _utc_now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def validate_job(data):
    """Kiểm tra + chuẩn hóa job (dict) -> bản copy đã điền mặc định.

    Bắt buộc: game (chuỗi không rỗng), drive_file_id (ID/link hợp lệ,
    chuẩn hóa về ID trần). Tùy chọn: locales (mặc định ["en", "zh"],
    chỉ nhận en/zh), from_lang ("en"), to_lang ("vi"), translate
    (True), worker_url ("" — nếu có phải https://), game_file_name
    (""), job_id (tự sinh từ game nếu thiếu), version (phải bằng 1).
    Trường lạ được giữ nguyên (tương thích xuôi). Lỗi -> PB3Error
    E_CONFIG_INVALID.
    """
    if not isinstance(data, dict):
        raise _err("E_CONFIG_INVALID", "Job phải là một object JSON.",
                   "Tạo lại job từ web (nút 'Sao chép job' / 'Tải job.json').")
    if data.get("version", JOB_SCHEMA_VERSION) != JOB_SCHEMA_VERSION:
        raise _err("E_CONFIG_INVALID",
                   "Phiên bản job không hỗ trợ: %r (cần version %d)."
                   % (data.get("version"), JOB_SCHEMA_VERSION),
                   "Tạo lại job từ web bản mới nhất.")
    game = str(data.get("game") or "").strip()
    if not game:
        raise _err("E_CONFIG_INVALID", "Job thiếu tên game.",
                   "Nhập tên game trên web rồi tạo lại job.")
    if len(game) > 120:
        raise _err("E_CONFIG_INVALID", "Tên game quá dài (tối đa 120 ký tự).",
                   "Rút ngắn tên game rồi tạo lại job.")
    file_id = parse_drive_file_id(data.get("drive_file_id") or "")
    locales = data.get("locales", ["en", "zh"])
    if isinstance(locales, str):
        locales = [s.strip() for s in locales.split(",") if s.strip()]
    if not isinstance(locales, list) or not locales:
        raise _err("E_CONFIG_INVALID", "Job thiếu danh sách locale.",
                   "Chọn ít nhất một locale (en, zh).")
    for loc in locales:
        if loc not in SUPPORTED_LOCALES:
            raise _err("E_CONFIG_INVALID",
                       "Locale chưa hỗ trợ trong job Colab: %r (chỉ có %s)."
                       % (loc, ", ".join(SUPPORTED_LOCALES)),
                       "Chọn lại locale en và/hoặc zh.")
    from_lang = str(data.get("from_lang") or "en").strip().lower()
    to_lang = str(data.get("to_lang") or "vi").strip().lower()
    for label, code in (("ngôn ngữ nguồn", from_lang),
                        ("ngôn ngữ đích", to_lang)):
        if not _LANG_RE.match(code):
            raise _err("E_CONFIG_INVALID",
                       "Mã %s không hợp lệ: %r." % (label, code),
                       "Mã ngôn ngữ là 2–3 chữ cái thường (vd en, vi, zh).")
    if from_lang == to_lang:
        raise _err("E_CONFIG_INVALID",
                   "Ngôn ngữ nguồn và đích trùng nhau: %r." % from_lang,
                   "Chọn ngôn ngữ đích khác nguồn.")
    translate = data.get("translate", True)
    if not isinstance(translate, bool):
        raise _err("E_CONFIG_INVALID", "Trường 'translate' phải là true/false.",
                   "Tạo lại job từ web.")
    worker_url = str(data.get("worker_url") or "").strip()
    if worker_url and not _HTTP_URL_RE.match(worker_url):
        raise _err("E_CONFIG_INVALID",
                   "WORKER_URL phải bắt đầu bằng https.",
                   "Dán lại URL worker đầy đủ (gồm https, vd xxx.workers.dev).")
    job_id = str(data.get("job_id") or "").strip()
    if job_id and not _JOB_ID_RE.match(job_id):
        raise _err("E_CONFIG_INVALID",
                   "job_id không hợp lệ: %r." % job_id[:40],
                   "Để trống job_id để web tự sinh.")
    job = dict(data)  # giữ trường lạ để tương thích xuôi
    job.update({
        "version": JOB_SCHEMA_VERSION,
        "job_id": job_id or new_job_id(game),
        "game": game,
        "drive_file_id": file_id,
        "game_file_name": str(data.get("game_file_name") or "").strip(),
        "locales": list(locales),
        "from_lang": from_lang,
        "to_lang": to_lang,
        "translate": translate,
        "worker_url": worker_url,
        "created_at": str(data.get("created_at") or _utc_now_iso()),
        "created_by": str(data.get("created_by") or CREATED_BY),
    })
    return job


def build_job(game, drive_file_id, *, locales=("en", "zh"), from_lang="en",
              to_lang="vi", translate=True, worker_url="", game_file_name="",
              job_id=None, created_at=None):
    """Tạo job mới (dict đã validate). `drive_file_id` nhận cả link."""
    data = {
        "version": JOB_SCHEMA_VERSION,
        "game": game,
        "drive_file_id": drive_file_id,
        "locales": list(locales),
        "from_lang": from_lang,
        "to_lang": to_lang,
        "translate": translate,
        "worker_url": worker_url,
        "game_file_name": game_file_name,
    }
    if job_id:
        data["job_id"] = job_id
    if created_at:
        data["created_at"] = created_at
    return validate_job(data)


def job_to_json(job):
    """Job (dict đã validate) -> chuỗi JSON (UTF-8, indent 2, newline
    cuối — để tải về / sao chép / ghi lên Drive)."""
    return json.dumps(validate_job(job), ensure_ascii=False, indent=2) + "\n"


def job_file_name(job):
    """Tên file job trên máy/Drive: 'meorocky-job-<job_id>.json'."""
    return "meorocky-job-%s.json" % validate_job(job)["job_id"]


def job_drive_paths(job):
    """Đường dẫn job trên Drive (tương đối từ MyDrive):
    {'job_path': 'MeoRocky/jobs/<job_id>.json',
     'latest_path': 'MeoRocky/jobs/latest.json'}."""
    j = validate_job(job)
    return {
        "job_path": "%s/%s.json" % (JOB_DIR_NAME, j["job_id"]),
        "latest_path": "%s/%s" % (JOB_DIR_NAME, JOB_LATEST_NAME),
    }


def load_job_from_text(text):
    """Đọc job từ chuỗi JSON (chuỗi dán trong cell notebook). JSON hỏng
    -> E_CORRUPT; schema sai -> E_CONFIG_INVALID."""
    if not isinstance(text, str) or not text.strip():
        raise _err("E_CONFIG_INVALID", "Chuỗi job rỗng.",
                   "Sao chép lại job từ web (nút 'Sao chép job').")
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise _err("E_CORRUPT", "Job JSON hỏng: %s." % exc,
                   "Sao chép lại job từ web, đừng sửa tay nội dung JSON.")
    return validate_job(data)


def load_job_from_file(path):
    """Đọc job từ file (đường dẫn trên Colab/Drive đã gắn). File không
    có -> FileNotFoundError (để caller thử nguồn khác); JSON/schema
    sai -> PB3Error như `load_job_from_text`."""
    with open(path, encoding="utf-8") as fh:
        return load_job_from_text(fh.read())


def resolve_job(*, job_json_text=None, upload_path=None,
                drive_jobs_dir=None, job_id=None):
    """Tìm job theo thứ tự ưu tiên, trả (job, nguồn) với nguồn là
    'pasted' / 'upload' / 'drive-latest' / 'drive-id'; không tìm thấy
    nguồn nào -> (None, None). File job CÓ nhưng hỏng/schema sai thì
    BÁO LỖI LUÔN (không lặng lẽ rơi về biến tay — job hỏng mà chạy tiếp
    bằng biến cũ sẽ trích nhầm game)."""
    if job_json_text and str(job_json_text).strip():
        return load_job_from_text(job_json_text), "pasted"
    if upload_path and os.path.isfile(upload_path):
        return load_job_from_file(upload_path), "upload"
    if drive_jobs_dir:
        latest = os.path.join(drive_jobs_dir, JOB_LATEST_NAME)
        if os.path.isfile(latest):
            return load_job_from_file(latest), "drive-latest"
        if job_id:
            by_id = os.path.join(drive_jobs_dir, "%s.json" % job_id)
            if os.path.isfile(by_id):
                return load_job_from_file(by_id), "drive-id"
    return None, None


def job_output_dir(game_file_name, game):
    """Thư mục kết quả trên Drive (tương đối từ MyDrive), khớp cách
    notebook đặt OUT_DIR: theo tên file IPA (bỏ đuôi) nếu biết, nếu
    không thì theo tên game. Vd 'MeoRocky-output/Dead Cells 3.5.15 VH'."""
    stem = ""
    if game_file_name:
        stem = os.path.splitext(os.path.basename(game_file_name))[0].strip()
    return "%s/%s" % (OUTPUT_ROOT_NAME, stem or game.strip())


def manifest_file_entry(path):
    """Một file locale JSON -> entry manifest {name, keys, md5, bytes}.
    File không phải dict JSON -> E_CORRUPT."""
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise _err("E_CORRUPT", "File locale hỏng (%s): %s."
                   % (os.path.basename(path), exc),
                   "Chạy lại bước trích xuất trên Colab.")
    if not isinstance(data, dict):
        raise _err("E_CORRUPT",
                   "File locale không phải object JSON: %s."
                   % os.path.basename(path),
                   "Chạy lại bước trích xuất trên Colab.")
    return {
        "name": os.path.basename(path),
        "keys": len(data),
        "md5": hashlib.md5(raw).hexdigest(),
        "bytes": len(raw),
    }


def build_manifest(job, status, files, translated=None):
    """Manifest handoff: notebook ghi sau bước trích (status
    'extracted') và cập nhật sau bước dịch ('done'); lỗi -> 'failed'.
    `files`/`translated` là list entry từ `manifest_file_entry`."""
    if status not in ("extracted", "done", "failed"):
        raise _err("E_CONFIG_INVALID",
                   "Trạng thái manifest không hợp lệ: %r." % status,
                   "Dùng 'extracted', 'done' hoặc 'failed'.")
    j = validate_job(job)
    return {
        "version": JOB_SCHEMA_VERSION,
        "job_id": j["job_id"],
        "game": j["game"],
        "status": status,
        "locales": list(j["locales"]),
        "files": list(files),
        "translated": list(translated or []),
        "out_dir": job_output_dir(j["game_file_name"], j["game"]),
        "updated_at": _utc_now_iso(),
    }


def write_manifest(path, manifest):
    """Ghi manifest (tạo thư mục cha nếu cần, flush + fsync). Trả
    đường dẫn đã ghi. Manifest sai schema -> E_CONFIG_INVALID."""
    if not isinstance(manifest, dict) or manifest.get("version") != JOB_SCHEMA_VERSION:
        raise _err("E_CONFIG_INVALID", "Manifest sai phiên bản.",
                   "Tạo manifest bằng build_manifest rồi ghi lại.")
    if not manifest.get("job_id"):
        raise _err("E_CONFIG_INVALID", "Manifest thiếu job_id.",
                   "Tạo manifest bằng build_manifest rồi ghi lại.")
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    return path


def _job_or_minimal(job, game, drive_file_id):
    """Job cho manifest: dùng job đã có nếu có, nếu không dựng tối
    thiểu từ tên game + Drive ID (đường điền tay không có job thì
    build_job sẽ báo thiếu ID — manifest là phụ, cell sẽ in cảnh
    báo chứ không chặn bước chính)."""
    if job is not None:
        return validate_job(job)
    return build_job(game or "game", drive_file_id or "")


def write_extract_manifest(out_dir, merged_files, job=None, game=None,
                           drive_file_id=None):
    """Ghi manifest sau bước trích: entries từ danh sách file locale
    đã gộp (mỗi phần tử {'file': tên, ...} như bridge.extract trả
    trong res['merged_files']); status 'extracted'. Trả manifest."""
    j = _job_or_minimal(job, game, drive_file_id)
    entries = []
    for item in merged_files or []:
        name = item.get("file") if isinstance(item, dict) else None
        if not name:
            continue
        path = os.path.join(out_dir, name)
        if os.path.isfile(path):
            entries.append(manifest_file_entry(path))
    manifest = build_manifest(j, "extracted", entries)
    write_manifest(os.path.join(out_dir, MANIFEST_NAME), manifest)
    return manifest


def update_manifest_after_translate(out_dir, translated_path, job=None,
                                    game=None, drive_file_id=None):
    """Cập nhật manifest sau bước dịch: đọc manifest cũ nếu có (giữ
    job_id/game/files đã trích), nếu không có thì dựng mới từ file
    locale trong out_dir; status 'done', translated=[entry của file
    vừa dịch]. Trả manifest."""
    mpath = os.path.join(out_dir, MANIFEST_NAME)
    try:
        manifest = read_manifest(mpath)
        j = _job_or_minimal(job or {"job_id": manifest.get("job_id"),
                                    "game": manifest.get("game"),
                                    "drive_file_id": drive_file_id or ""},
                            game, drive_file_id)
        manifest["job_id"] = j["job_id"]
        manifest["game"] = j["game"]
    except (FileNotFoundError, PB3Error):
        j = _job_or_minimal(job, game, drive_file_id)
        old = []
        for name in sorted(os.listdir(out_dir)):
            if name.endswith(".json") and name != MANIFEST_NAME \
                    and not name.startswith("translated.") \
                    and not name.endswith(".provenance.json"):
                path = os.path.join(out_dir, name)
                if os.path.isfile(path):
                    try:
                        old.append(manifest_file_entry(path))
                    except PB3Error:
                        continue
        manifest = build_manifest(j, "extracted", old)
    manifest["status"] = "done"
    manifest["translated"] = [manifest_file_entry(translated_path)]
    manifest["updated_at"] = _utc_now_iso()
    write_manifest(mpath, manifest)
    return manifest


def read_manifest(path):
    """Đọc manifest (web dùng để poll theo job_id). File hỏng/thiếu
    trường -> E_CORRUPT."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        raise
    except ValueError as exc:
        raise _err("E_CORRUPT", "Manifest hỏng: %s." % exc,
                   "Chạy lại notebook trên Colab.")
    if not isinstance(data, dict) or data.get("version") != JOB_SCHEMA_VERSION \
            or not data.get("job_id"):
        raise _err("E_CORRUPT", "Manifest thiếu version/job_id.",
                   "Chạy lại notebook trên Colab.")
    return data
