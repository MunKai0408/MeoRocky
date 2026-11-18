"""Chạy hàng loạt (Claude 09, đặc tả §9.1): manifest CSV/JSON, checkpoint,
resume, timeout từng game, tiến độ thật, không bao giờ đóng gói lại IPA.

- Đầu vào: manifest CSV (header bắt buộc: path; tùy chọn locale, mode,
  old, new, file, object, profile_override) hoặc JSON (list object cùng khóa).
- Mỗi game cô lập: lỗi một game không dừng cả lô; kết quả từng game ghi vào
  thư mục con `game_<nn>_<slug>/` (result.json + file đã xuất), kèm index.csv,
  summary.json, failures.txt trong thư mục lô.
- Checkpoint (`checkpoint.json`, ghi nguyên tử sau mỗi game xong): khóa mỗi
  game = sha256 của (path, locale, mode, old, new). resume=True thì bỏ qua
  game đã xong (status ok) trong checkpoint.
- Timeout từng game là BEST-EFFORT (ghi rõ): thread Python 3.9 không kill
  được, nên game quá giờ bị đánh lỗi E_TIMEOUT, thread nền hoàn thành sau
  thì output của nó bị bỏ — mỗi game ghi vào staging riêng
  (`.staging/<slug>/`) rồi mới chuyển vào thư mục game khi thành công, nên
  output muộn không bao giờ lọt vào kết quả lô.
- Song song: jobs=1 mặc định (tuần tự); jobs>1 dùng ThreadPoolExecutor, tối
  đa 4 worker. Trên iSH nên giữ jobs=1 (GIL + RAM 4GB; mỗi worker có thể
  giữ cả bundle trong bộ nhớ).
- Tiến độ: SteadyProgress trên tổng số game (mỗi game xong bước 1 mốc;
  % = game đã xong/tổng, ETA theo thời gian thực), tuân luật #152–153
  (không nhảy quá 1% mỗi lần cập nhật, không báo láo).
- KB (sdk/kb.py, nhóm KB viết song song): import lười; kb=None vẫn chạy
  được. Nếu kb có profile hợp lệ và không profile_override thì dùng preset
  {file, locale} từ profile; sau khi trích xong thì kb.remember +
  kb.record_run + record_plugin_stats (lỗi KB không làm hỏng game).

Chỉ thư viện chuẩn, Python >= 3.8. Không gọi mạng.
"""
from __future__ import annotations

import concurrent.futures
import csv
import hashlib
import io
import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core import safe_io
from core.errors import PB3Error, error_from_exception
from core.limits import Limits
from core.progress import SteadyProgress

CHECKPOINT_FILENAME = "checkpoint.json"
CHECKPOINT_VERSION = 1
SUMMARY_FILENAME = "summary.json"
INDEX_FILENAME = "index.csv"
FAILURES_FILENAME = "failures.txt"
RESULT_FILENAME = "result.json"

MAX_JOBS = 4
# Ngân sách §9.2 (giây): trích 1 locale game thường <= 10s; game lớn <= 90s.
# Game lớn = đầu vào > 512MB. Mod chưa có ngân sách trong §9.2 -> không đặt số.
EXTRACT_BUDGET_S = 10.0
EXTRACT_BUDGET_LARGE_S = 90.0
LARGE_GAME_BYTES = 512 * 1024 * 1024

_MODES = ("extract", "mod")
_BOOL_TRUE = {"true", "1", "yes", "y", "có"}
_BOOL_FALSE = {"", "false", "0", "no", "n", "không"}


# ------------------------------------------------------------------ manifest
@dataclass
class BatchItem:
    path: str
    locale: Optional[str] = None
    mode: str = "extract"
    old: Optional[str] = None
    new: Optional[str] = None
    file: Optional[str] = None       # vpath entry (mod) / file ép chọn (extract preset)
    object: Optional[str] = None    # path_id TextAsset (mod)
    profile_override: bool = False

    def key(self) -> str:
        """Khóa checkpoint: sha256 của (path, locale, mode, old, new)."""
        blob = json.dumps([self.path, self.locale, self.mode, self.old, self.new],
                          ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()


def _clean(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _parse_bool(value: Any, where: str) -> bool:
    s = str(value).strip().lower() if value is not None else ""
    if s in _BOOL_TRUE:
        return True
    if s in _BOOL_FALSE:
        return False
    raise PB3Error("E_CONFIG_INVALID",
                   "profile_override không hợp lệ ở %s: %r (dùng true/false)" % (where, value))


def _item_from_mapping(mapping: Dict[str, Any], where: str) -> BatchItem:
    norm = {}
    for k, v in mapping.items():
        if k is None:
            continue
        norm[str(k).strip().lower()] = v
    path = _clean(norm.get("path"))
    if not path:
        raise PB3Error("E_CONFIG_INVALID", "thiếu cột path ở %s" % where)
    mode = (_clean(norm.get("mode")) or "extract").lower()
    if mode not in _MODES:
        raise PB3Error("E_CONFIG_INVALID",
                       "mode lạ ở %s: %r (chỉ extract/mod)" % (where, mode))
    return BatchItem(
        path=path,
        locale=_clean(norm.get("locale")),
        mode=mode,
        old=_clean(norm.get("old")),
        new=_clean(norm.get("new")),
        file=_clean(norm.get("file")),
        object=_clean(norm.get("object")),
        profile_override=_parse_bool(norm.get("profile_override"), where),
    )


def parse_manifest(path_or_text) -> List[BatchItem]:
    """Đọc manifest từ đường dẫn file, hoặc từ chuỗi nội dung CSV/JSON.

    CSV: header bắt buộc có cột `path`. JSON: list object cùng khóa.
    Lỗi dạng manifest -> PB3Error E_CONFIG_INVALID. Đường dẫn không tồn
    tại (không phải nội dung manifest) -> E_INPUT_NOT_FOUND."""
    if isinstance(path_or_text, (bytes, bytearray)):
        text = bytes(path_or_text).decode("utf-8-sig")
    elif isinstance(path_or_text, Path):
        text = _read_manifest_file(str(path_or_text))
    elif isinstance(path_or_text, str):
        if not path_or_text.strip():
            text = path_or_text  # rỗng -> E_CONFIG_INVALID ("manifest rỗng")
        elif os.path.isfile(path_or_text):
            text = _read_manifest_file(path_or_text)
        elif "\n" in path_or_text or path_or_text.lstrip().startswith(("[", "{")) \
                or _looks_like_csv(path_or_text):
            # Chuỗi nhiều dòng / JSON / header CSV -> nội dung manifest
            # (lỗi dạng sẽ thành E_CONFIG_INVALID, không phải "không tìm thấy").
            text = path_or_text
        else:
            raise PB3Error("E_INPUT_NOT_FOUND",
                           "không tìm thấy manifest", where=path_or_text)
    else:
        raise PB3Error("E_CONFIG_INVALID",
                       "manifest phải là đường dẫn hoặc chuỗi nội dung")
    stripped = text.strip()
    if not stripped:
        raise PB3Error("E_CONFIG_INVALID", "manifest rỗng")
    if stripped.startswith("["):
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise PB3Error("E_CONFIG_INVALID", "JSON manifest sai dạng: %s" % exc)
        if not isinstance(data, list):
            raise PB3Error("E_CONFIG_INVALID", "JSON manifest phải là list object")
        items = []
        for i, entry in enumerate(data):
            if not isinstance(entry, dict):
                raise PB3Error("E_CONFIG_INVALID",
                               "mục %d trong JSON manifest phải là object" % (i + 1))
            items.append(_item_from_mapping(entry, "mục %d" % (i + 1)))
    else:
        try:
            rows = list(csv.DictReader(io.StringIO(text)))
        except csv.Error as exc:
            raise PB3Error("E_CONFIG_INVALID", "CSV manifest sai dạng: %s" % exc)
        if not rows:
            raise PB3Error("E_CONFIG_INVALID", "manifest rỗng")
        header = [str(h).strip().lower() for h in (rows[0].keys() or []) if h is not None]
        if "path" not in header:
            raise PB3Error("E_CONFIG_INVALID",
                           "CSV manifest thiếu cột path (header: %s)"
                           % ", ".join(header))
        items = []
        for i, row in enumerate(rows):
            if row is None:
                continue
            if all(v is None or str(v).strip() == "" for v in row.values()):
                continue  # dòng trống: bỏ qua
            items.append(_item_from_mapping(row, "dòng %d" % (i + 2)))
    if not items:
        raise PB3Error("E_CONFIG_INVALID", "manifest rỗng (không có game nào)")
    return items


def _looks_like_csv(text: str) -> bool:
    first = text.lstrip().splitlines()[0] if text.strip() else ""
    cols = [c.strip().lower() for c in first.split(",")]
    return "path" in cols


def _read_manifest_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return fh.read()
    except OSError as exc:
        raise error_from_exception(exc, where=path)


def _slug(path: str) -> str:
    stem = os.path.splitext(os.path.basename(str(path).rstrip("/")))[0]
    slug = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in stem)
    return (slug or "game")[:40]


# ------------------------------------------------------------------ kết quả
@dataclass
class BatchGameResult:
    index: int
    path: str
    mode: str = "extract"
    locale: Optional[str] = None
    status: str = "failed"          # ok | failed | skipped | incomplete
    error_code: Optional[str] = None
    error_message: str = ""
    elapsed_s: float = 0.0
    size_bytes: int = 0
    game_dir: Optional[str] = None
    files: List[str] = field(default_factory=list)
    kb_used: bool = False
    kb_note: str = ""
    note: str = ""
    budget_s: Optional[float] = None
    budget_ok: Optional[bool] = None
    key: str = ""                    # khóa checkpoint (sha256 path/locale/mode/old/new)

    def to_dict(self) -> Dict[str, Any]:
        return {"index": self.index, "path": self.path, "mode": self.mode,
                "locale": self.locale, "status": self.status,
                "error_code": self.error_code, "error_message": self.error_message,
                "elapsed_s": round(self.elapsed_s, 2), "size_bytes": self.size_bytes,
                "game_dir": self.game_dir, "files": list(self.files),
                "kb_used": self.kb_used, "kb_note": self.kb_note, "note": self.note,
                "budget_s": self.budget_s, "budget_ok": self.budget_ok}


@dataclass
class BatchSummary:
    total: int = 0
    ok: int = 0
    failed: int = 0
    skipped: int = 0
    incomplete: int = 0
    elapsed_s: float = 0.0
    out_dir: str = ""
    jobs: int = 1
    dry_run: bool = True
    budgets: Dict[str, Any] = field(default_factory=dict)
    games: List[BatchGameResult] = field(default_factory=list)
    resumed_from: Optional[str] = None  # thư mục lần chạy cũ khi --resume tìm thấy

    def to_dict(self) -> Dict[str, Any]:
        return {"schema": "pb3.batch_summary", "version": 1,
                "out_dir": self.out_dir, "total": self.total,
                "ok": self.ok, "failed": self.failed, "skipped": self.skipped,
                "incomplete": self.incomplete,
                "elapsed_s": round(self.elapsed_s, 2),
                "jobs": self.jobs, "dry_run": self.dry_run,
                "budgets": self.budgets,
                "resumed_from": self.resumed_from,
                "games": [g.to_dict() for g in self.games]}


def _extract_budget_s(size_bytes: int) -> float:
    """Ngân sách §9.2 cho job extract theo dung lượng đầu vào."""
    return EXTRACT_BUDGET_LARGE_S if size_bytes > LARGE_GAME_BYTES else EXTRACT_BUDGET_S


# ------------------------------------------------------------------ adapter Unity mức sdk (F2)
# Bản sao y hệt các helper của services.unity_tool, để _mod_job không còn
# import services. Xem ghi chú dài ở BatchRunner._mod_job: thông điệp lỗi
# giữ nguyên từng chữ (kể cả hint "Dùng: unity patch ..."), chỉ đổi nơi
# định nghĩa. Import sdk.* làm lười trong hàm theo phong cách sẵn có của
# file này (tránh mọi rủi ro vòng import ở mức module).
def _sdk_unity_usage(what, usage):
    return PB3Error("E_CONFIG_INVALID", what, hint="Dùng: " + usage,
                    message_vi="Tham số lệnh Unity không hợp lệ.")


def _sdk_make_backend(app):
    from sdk import unity as unity_sdk
    limits = getattr(app, "limits", None) or Limits()
    return unity_sdk.UnityBackend("AUTO", limits)


def _sdk_read_file_bytes(path, limits):
    p = safe_io.check_readable_input(path, limits, allow_dir=False)
    with open(str(p), "rb") as fh:
        return fh.read()


def _sdk_read_vpath_segment(data, seg, backend, limits):
    """Đọc một đoạn vpath từ bytes (container ZIP hoặc bundle UnityFS)."""
    from sdk import unity as unity_sdk
    from sdk.containers import open_vfs
    det = unity_sdk.detect_bundle(data)
    if det is not None:
        bundle = unity_sdk.parse_bundle(data, backend, limits, where="bundle")
        return bundle.get_entry(seg)
    vfs = open_vfs(data, limits=limits, source_name="<bytes>")
    try:
        return vfs.read(seg, None)
    finally:
        close = getattr(vfs, "close", None)
        if close is not None:
            close()


def _is_localization_csv(data, name):
    """True khi data la bang CSV localization (M3) — xem sdk.csvutil."""
    try:
        from sdk.csvutil import is_localization_csv
        return bool(is_localization_csv(data, name))
    except Exception:
        return False


def _sdk_resolve_vpath(file_bytes, entry, backend, limits):
    """Đi theo vpath 'a::b::c' từ bytes gốc.

    Trả (kind, data, req_entry, label):
    - kind "bundle": data = bytes bundle, req_entry = entry trong bundle.
    - kind "assets": data = bytes SerializedFile (độc lập hoặc trong container).
    - kind "csv" (M3): data = bytes file CSV/TSV localization (độc lập
      hoặc entry trong container), req_entry = None.
    """
    from sdk import unity as unity_sdk
    from sdk.vfs import vpath_split
    if not entry:
        det = unity_sdk.detect_bundle(file_bytes)
        if det is not None:
            return "bundle", file_bytes, None, "bundle"
        # M3: file CSV/TSV doc lap dua thang vao (E) — khong can SF.
        if _is_localization_csv(file_bytes, ""):
            return "csv", file_bytes, None, "csv"
        try:
            unity_sdk.parse_serialized(file_bytes, limits, where="assets")
            return "assets", file_bytes, None, "assets"
        except PB3Error:
            raise _sdk_unity_usage("file không phải bundle/assets; cần --entry chỉ tới "
                                   "bundle/assets bên trong",
                                   "unity patch <file> --entry <vpath> --object <id> ...")
    segments = vpath_split(entry)
    current = file_bytes
    label = ""
    for seg in segments[:-1]:
        nxt = _sdk_read_vpath_segment(current, seg, backend, limits)
        current = nxt
        label = seg
    last = segments[-1]
    det = unity_sdk.detect_bundle(current)
    if det is not None:
        return "bundle", current, last, label or "bundle"
    # current là container: đọc entry cuối
    try:
        data = _sdk_read_vpath_segment(current, last, backend, limits)
    except PB3Error as exc:
        if exc.code == "E_TOO_LARGE":
            raise  # giu nguyen ma loi that, dung doi thanh "khong tim thay"
        raise _sdk_unity_usage("không tìm thấy %r trong container/bundle" % last,
                               "unity patch <file> --entry <vpath> --object <id> ...")
    det_last = unity_sdk.detect_bundle(data)
    if det_last is not None:
        raise _sdk_unity_usage("entry %r là bundle UnityFS v%d; chỉ tới entry bên trong "
                               "bằng '%s::<tên entry>'" % (last, det_last["version"], last),
                               "unity patch <file> --entry '%s::<tên entry>' --object <id> ..."
                               % last)
    try:
        unity_sdk.parse_serialized(data, limits, where=last)
        return "assets", data, None, last
    except PB3Error:
        # M3: entry CSV/TSV localization trong container — don vi kind "csv".
        if _is_localization_csv(data, last):
            return "csv", data, None, last
        raise _sdk_unity_usage("entry %r không phải SerializedFile" % last,
                               "unity patch <file> --entry <vpath> --object <id> ...")


def _sdk_default_output_rel(input_path, kind, label):
    """Đường dẫn output mặc định (6.6): <GameName>/<tên đơn vị nhỏ nhất>.

    `label` là đoạn vpath chứa đơn vị cần ghi (vd "Payload/.../level.unity3d"
    cho bundle trong IPA, "resources.assets" cho assets trong container);
    với file độc lập thì dùng tên file gốc. Không bao giờ xuất cả IPA.
    """
    stem = Path(str(input_path)).stem
    game = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in stem) or "game"
    name = None
    # Label "csv" la placeholder cua resolver cho file CSV doc lap (khong
    # phai ten file) — nhu "bundle"/"assets", fallback ve ten file goc.
    if label and label not in ("bundle", "assets", "csv"):
        name = label.rsplit("/", 1)[-1]
    if not name:
        name = Path(str(input_path)).name
    return "%s/%s" % (game, name)


# ------------------------------------------------------------------ runner
class BatchRunner:
    """Chạy một lô game theo manifest.

    job_runner(item, staging_dir, ctx) -> dict: hàm chạy một game, có thể
    thay bằng hàm giả trong test (vd job chậm để thử timeout). ctx có:
    runner, registry, app, limits, dry_run, tempdir, kb, item.
    Mặc định dùng _default_job (extract qua sdk.extraction, mod qua
    services.unity_tool + sdk.unity.apply_patch)."""

    def __init__(self, *, registry=None, app=None, limits: Optional[Limits] = None,
                 job_runner: Optional[Callable] = None,
                 clock: Callable[[], float] = time.monotonic,
                 stream=None):
        self._registry = registry
        self._app = app
        self._limits = limits or (getattr(app, "limits", None) if app is not None else None) \
            or Limits()
        self._job_runner = job_runner or self._default_job
        self._clock = clock
        self._stream = stream

    # -- registry ------------------------------------------------------
    def _get_registry(self):
        # F2 (đợt rà soát tổng cuối, xong ở bước tích hợp): registry được
        # TIÊM từ tầng services (services/batch_tool.py dựng BatchRunner
        # với registry=language_tool._registry(app)); sdk KHÔNG import
        # services cũng KHÔNG import registry (test kiến trúc
        # test_core_does_not_import_registry, tiền lệ #129/#175). Không có
        # registry được tiêm thì trả None — job extract sẽ lỗi
        # E_PLUGIN_INVALID rõ ràng thay vì tự dựng registry lén.
        return self._registry

    # -- chạy lô --------------------------------------------------------
    def run(self, manifest, *, out_dir=None, jobs: int = 1, resume: bool = False,
            dry_run: bool = True, limit: Optional[int] = None,
            only: Optional[str] = None, timeout_game: Optional[float] = None,
            kb=None, progress_cb: Optional[Callable] = None) -> BatchSummary:
        t0 = self._clock()
        if isinstance(manifest, (str, bytes, Path)):
            items = parse_manifest(manifest)
        else:
            items = list(manifest)
        for i, it in enumerate(items):
            it._batch_index = i + 1
        if limit is not None and limit < 0:
            raise PB3Error("E_CONFIG_INVALID", "--limit không được âm")
        jobs = max(1, min(int(jobs or 1), MAX_JOBS))
        resumed_from = None
        if out_dir is not None:
            out = Path(str(out_dir))
            if resume:
                resumed_from = str(out)
        elif resume:
            # --resume mà không chỉ --out: tự tìm lần chạy cũ gần nhất có
            # checkpoint khớp lô này (không có thì chạy mới, không báo láo).
            found = self._find_resume_dir({it.key() for it in items})
            if found is not None:
                out = found
                resumed_from = str(found)
            else:
                out = self._default_out_dir()
        else:
            out = self._default_out_dir()
        out.mkdir(parents=True, exist_ok=True)
        staging_root = out / ".staging"
        staging_root.mkdir(parents=True, exist_ok=True)

        # Lọc --only/--limit (không chạy -> skipped, ghi rõ lý do).
        results: List[BatchGameResult] = []
        kept = 0
        for i, item in enumerate(items):
            base = BatchGameResult(index=i + 1, path=item.path, mode=item.mode,
                                   locale=item.locale, key=item.key())
            if only and only.lower() not in item.path.lower():
                base.status = "skipped"
                base.note = "bị lọc bởi --only %r" % only
                results.append(base)
                continue
            if limit is not None and kept >= limit:
                base.status = "skipped"
                base.note = "bị lọc bởi --limit %d" % limit
                results.append(base)
                continue
            kept += 1

        # Checkpoint cũ (resume): game đã xong (status ok) thì bỏ qua.
        checkpoint = self._load_checkpoint(out)
        done_keys = {k for k, v in checkpoint.items()
                     if isinstance(v, dict) and v.get("status") == "ok"} if resume else set()
        # Ghép lại theo thứ tự manifest: resume-skip xen đúng vị trí.
        ordered: List[BatchGameResult] = []
        run_list: List[BatchItem] = []
        for i, item in enumerate(items):
            match = [r for r in results if r.index == i + 1]
            if match:
                ordered.append(match[0])
                continue
            if resume and item.key() in done_keys:
                prev = checkpoint[item.key()]
                ordered.append(BatchGameResult(
                    index=i + 1, path=item.path, mode=item.mode, locale=item.locale,
                    status="skipped", note="đã xong (checkpoint)",
                    elapsed_s=float(prev.get("elapsed_s") or 0.0),
                    game_dir=prev.get("game_dir"), key=item.key()))
                continue
            run_list.append(item)
        results = ordered

        total_run = len(run_list)
        prog = SteadyProgress("batch", total_run, stream=self._stream,
                              clock=self._clock)
        prog.__enter__()
        try:
            done = 0
            if jobs == 1:
                for item in run_list:
                    res = self._run_one(item, out, staging_root, kb, dry_run,
                                        timeout_game, executor=None)
                    results.append(res)
                    self._save_checkpoint(out, results)
                    done += 1
                    self._report_progress(prog, done, total_run, item, progress_cb)
            else:
                ex = concurrent.futures.ThreadPoolExecutor(max_workers=jobs)
                timed_out = False
                try:
                    futs = [(item, ex.submit(self._run_one_async, item, out,
                                              staging_root, kb, dry_run, timeout_game))
                            for item in run_list]
                    for item, fut in futs:
                        res = self._wait_future(item, fut, timeout_game, out,
                                                staging_root)
                        if res.error_code == "E_TIMEOUT":
                            timed_out = True
                        results.append(res)
                        self._save_checkpoint(out, results)
                        done += 1
                        self._report_progress(prog, done, total_run, item, progress_cb)
                finally:
                    # Có timeout: không chờ worker nền (xem _run_one).
                    ex.shutdown(wait=not timed_out)
        finally:
            prog.finish()
        results.sort(key=lambda r: r.index)
        summary = BatchSummary(
            total=len(items),
            ok=sum(1 for r in results if r.status == "ok"),
            failed=sum(1 for r in results if r.status == "failed"),
            skipped=sum(1 for r in results if r.status == "skipped"),
            incomplete=sum(1 for r in results if r.status == "incomplete"),
            elapsed_s=self._clock() - t0,
            out_dir=str(out), jobs=jobs, dry_run=dry_run,
            budgets={"extract_locale_s": EXTRACT_BUDGET_S,
                     "extract_locale_large_s": EXTRACT_BUDGET_LARGE_S,
                     "over_budget": [r.path for r in results if r.budget_ok is False]},
            games=results, resumed_from=resumed_from)
        if jobs > 1:
            summary.budgets["note"] = ("jobs=%d: trên iSH nên giữ jobs=1 "
                                       "(GIL + RAM); song song chỉ dùng khi đủ RAM." % jobs)
        self._write_outputs(out, summary)
        return summary

    def _report_progress(self, prog, done, total, item, progress_cb):
        prog.update(done)
        if self._app is not None:
            try:
                self._app.report_progress(done / total if total else 1.0,
                                          note=os.path.basename(item.path))
            except Exception:
                pass
        if progress_cb is not None:
            try:
                progress_cb(done, total, os.path.basename(item.path))
            except Exception:
                pass

    def _batch_root(self) -> Path:
        """Thư mục chứa các lần chạy batch (workspace/output/_batch)."""
        if self._app is not None:
            return Path(str(self._app.paths.output)) / "_batch"
        return Path("workspace") / "output" / "_batch"

    def _find_resume_dir(self, keys) -> Optional[Path]:
        """Thư mục lần chạy cũ gần nhất có checkpoint khớp ≥1 khóa của lô.

        Chỉ dùng khi --resume mà không có --out: để CLI tiếp tục đúng lần
        chạy cũ thay vì tạo thư mục mới. Không khớp thì None (chạy mới)."""
        root = self._batch_root()
        try:
            candidates = [p for p in root.iterdir() if p.is_dir()]
        except OSError:
            return None
        best = None
        best_mtime = -1.0
        for cand in candidates:
            ck = self._load_checkpoint(cand)
            if not (set(ck) & set(keys)):
                continue
            try:
                mtime = os.path.getmtime(str(cand / CHECKPOINT_FILENAME))
            except OSError:
                continue
            if mtime > best_mtime:
                best, best_mtime = cand, mtime
        return best

    def _default_out_dir(self) -> Path:
        ts = time.strftime("%Y%m%d-%H%M%S")
        return self._batch_root() / ts

    # -- một game --------------------------------------------------------
    def _run_one(self, item, out, staging_root, kb, dry_run, timeout_game, executor):
        """Chạy tuần tự một game (jobs=1): có timeout qua executor riêng.

        Khi timeout: KHÔNG shutdown(wait=True) — chờ worker thì timeout
        thành vô nghĩa; executor bị bỏ (shutdown(wait=False)), thread nền
        tự kết thúc sau, output của nó nằm ở staging riêng và bị bỏ."""
        if timeout_game is None:
            return self._execute(item, out, staging_root, kb, dry_run)
        ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        fut = ex.submit(self._execute, item, out, staging_root, kb, dry_run)
        try:
            res = fut.result(timeout=float(timeout_game))
            ex.shutdown(wait=True)
            return res
        except concurrent.futures.TimeoutError:
            ex.shutdown(wait=False)
            return self._timeout_result(item, timeout_game)

    def _run_one_async(self, item, out, staging_root, kb, dry_run, timeout_game):
        """Worker cho jobs>1 (timeout xử lý ở _wait_future)."""
        return self._execute(item, out, staging_root, kb, dry_run)

    def _wait_future(self, item, fut, timeout_game, out, staging_root):
        if timeout_game is None:
            return fut.result()
        try:
            return fut.result(timeout=float(timeout_game))
        except concurrent.futures.TimeoutError:
            return self._timeout_result(item, timeout_game)

    def _timeout_result(self, item, timeout_game) -> BatchGameResult:
        """Game quá giờ: đánh lỗi E_TIMEOUT. Thread nền không kill được
        (Python 3.9) nên nó có thể hoàn thành sau — output của nó nằm ở
        staging riêng và KHÔNG được chuyển vào thư mục game (bị bỏ)."""
        idx = self._index_of(item)
        return BatchGameResult(
            index=idx, path=item.path, mode=item.mode, locale=item.locale,
            status="failed", error_code="E_TIMEOUT",
            error_message="quá %s giây cho phép (timeout best-effort: "
                          "thread nền không dừng được, output muộn bị bỏ)" % timeout_game,
            size_bytes=self._size_of(item.path), key=item.key(),
            note="timeout best-effort: thread nền có thể còn chạy, "
                 "staging riêng chưa chuyển vào kết quả")

    def _index_of(self, item) -> int:
        return int(getattr(item, "_batch_index", 0) or 0)

    def _execute(self, item, out, staging_root, kb, dry_run) -> BatchGameResult:
        t0 = self._clock()
        idx = self._index_of(item)
        slug = "game_%02d_%s" % (idx, _slug(item.path))
        staging = staging_root / slug
        staging.mkdir(parents=True, exist_ok=True)
        final = out / slug
        size = self._size_of(item.path)
        res = BatchGameResult(index=idx, path=item.path, mode=item.mode,
                              locale=item.locale, size_bytes=size, key=item.key())
        if item.mode == "extract":
            res.budget_s = _extract_budget_s(size)
        ctx = {"runner": self, "registry": self._get_registry(), "app": self._app,
               "limits": self._limits, "dry_run": dry_run,
               "tempdir": str(staging), "kb": kb, "item": item}
        try:
            outcome = self._job_runner(item, staging, ctx)
            res.status = outcome.get("status", "ok")
            res.error_code = outcome.get("error_code")
            res.error_message = outcome.get("error_message", "")
            res.files = list(outcome.get("files") or [])
            res.kb_used = bool(outcome.get("kb_used"))
            res.kb_note = outcome.get("kb_note", "")
            res.note = outcome.get("note", "")
        except PB3Error as exc:
            res.status = "failed"
            res.error_code = exc.code
            res.error_message = exc.message_vi or exc.detail
            res.note = exc.detail or ""
        except Exception as exc:  # noqa: BLE001 - cô lập lỗi từng game
            err = error_from_exception(exc, where=item.path)
            res.status = "failed"
            res.error_code = err.code
            res.error_message = err.message_vi or err.detail
        res.elapsed_s = self._clock() - t0
        if res.budget_s is not None:
            res.budget_ok = res.elapsed_s <= res.budget_s
            if res.budget_ok is False:
                res.note = (res.note + "; " if res.note else "") + \
                    "chưa đạt ngân sách §9.2 (%.1fs > %.0fs)" % (res.elapsed_s, res.budget_s)
        # Ghi result.json vào staging, rồi chuyển staging -> thư mục game
        # (chỉ khi game chạy xong — kể cả failed/incomplete — để còn dấu vết;
        # riêng timeout thì _timeout_result không qua đây nên staging bỏ lại).
        try:
            blob = json.dumps(res.to_dict(), ensure_ascii=False, indent=1).encode("utf-8")
            safe_io.atomic_write_bytes(str(staging / RESULT_FILENAME), blob)
        except Exception:
            pass
        try:
            if final.exists():
                shutil.rmtree(str(final), ignore_errors=True)
            os.replace(str(staging), str(final))
            res.game_dir = str(final)
            if res.files:
                res.files = [os.path.basename(f) for f in res.files]
        except OSError:
            res.game_dir = str(staging)
        return res

    @staticmethod
    def _size_of(path: str) -> int:
        try:
            return os.path.getsize(path)
        except OSError:
            return 0

    # -- checkpoint / output ----------------------------------------------
    def _load_checkpoint(self, out: Path) -> Dict[str, Any]:
        path = out / CHECKPOINT_FILENAME
        try:
            with open(str(path), "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError, UnicodeDecodeError):
            return {}
        if not isinstance(data, dict) or data.get("version") != CHECKPOINT_VERSION:
            return {}
        games = data.get("games")
        return games if isinstance(games, dict) else {}

    def _save_checkpoint(self, out: Path, results: List[BatchGameResult]) -> None:
        blob = json.dumps(
            {"version": CHECKPOINT_VERSION,
             "games": {r.key: {"path": r.path, "mode": r.mode,
                               "locale": r.locale, "status": r.status,
                               "elapsed_s": round(r.elapsed_s, 2),
                               "game_dir": r.game_dir,
                               "error_code": r.error_code}
                       for r in results if r.key}},
            ensure_ascii=False, indent=1).encode("utf-8")
        try:
            safe_io.atomic_write_bytes(str(out / CHECKPOINT_FILENAME), blob)
        except Exception:
            pass

    def _write_outputs(self, out: Path, summary: BatchSummary) -> None:
        try:
            blob = json.dumps(summary.to_dict(), ensure_ascii=False, indent=1).encode("utf-8")
            safe_io.atomic_write_bytes(str(out / SUMMARY_FILENAME), blob)
        except Exception:
            pass
        try:
            with open(str(out / INDEX_FILENAME), "w", encoding="utf-8", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["game", "mode", "locale", "kết quả", "mã lỗi",
                            "thời gian (s)", "thư mục"])
                for g in summary.games:
                    name = os.path.basename(g.game_dir) if g.game_dir else \
                        os.path.basename(g.path.rstrip("/"))
                    w.writerow([name, g.mode, g.locale or "",
                                g.status, g.error_code or "",
                                "%.2f" % g.elapsed_s, g.game_dir or ""])
        except OSError:
            pass
        try:
            lines = []
            for g in summary.games:
                if g.status != "failed":
                    continue
                name = os.path.basename(g.game_dir) if g.game_dir else \
                    os.path.basename(g.path.rstrip("/"))
                lines.append("%s [%s] %s" % (name, g.error_code or "?",
                                             g.error_message or g.note or "lỗi"))
            safe_io.atomic_write_bytes(
                str(out / FAILURES_FILENAME),
                ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8"))
        except Exception:
            pass

    # -- job mặc định -------------------------------------------------------
    def _default_job(self, item: BatchItem, staging: Path, ctx: Dict[str, Any]) -> Dict[str, Any]:
        if item.mode == "mod":
            return self._mod_job(item, staging, ctx)
        return self._extract_job(item, staging, ctx)

    # -- extract ------------------------------------------------------------
    def _extract_job(self, item, staging, ctx) -> Dict[str, Any]:
        from sdk import extraction as ext
        registry = ctx.get("registry")
        if registry is None:
            raise PB3Error("E_PLUGIN_INVALID",
                           "batch extract cần registry (plugin định dạng)")
        kb = ctx.get("kb")
        preset = None
        kb_note = ""
        kb_used = False
        if kb is not None:
            preset, kb_note = self._kb_preset(kb, item)
            kb_used = preset is not None
        req = ext.ExtractionRequest(
            source=item.path, registry=registry, limits=ctx.get("limits") or Limits(),
            locale=item.locale, pin_file=item.file,
            out_dir=str(staging), dry_run=bool(ctx.get("dry_run")),
            resume=False, preset=preset, ask_fn=None,
            tempdir=str(ctx.get("tempdir") or staging),
            root=str(self._app.paths.root) if self._app is not None else None)
        result = ext.run_extraction(req)
        if result.decision == "ask":
            return {"status": "incomplete",
                    "error_message": "cần chọn ứng viên, chạy tay",
                    "note": "cần chọn ứng viên, chạy tay (nhiều ứng viên ngang điểm)",
                    "files": [], "kb_used": kb_used, "kb_note": kb_note}
        if not result.ok or result.decision != "done":
            code = "E_NO_CANDIDATE"
            msg = "không trích xuất được file nào"
            if result.errors:
                code = result.errors[0].get("code") or code
                msg = result.errors[0].get("message") or msg
            return {"status": "failed", "error_code": code, "error_message": msg,
                    "files": [], "kb_used": kb_used, "kb_note": kb_note}
        files = [f.exported for f in result.files if f.exported]
        if kb is not None:
            self._kb_remember_extract(kb, item, result)
        return {"status": "ok", "files": files, "kb_used": kb_used,
                "kb_note": kb_note,
                "note": "%d entry, %d file, %.1fs" % (
                    result.total_entries, len(result.files), result.elapsed_s)}

    def _kb_preset(self, kb, item):
        """Preset {file, locale} từ profile KB (nếu có và không override)."""
        if item.profile_override:
            return None, ""
        try:
            profile = kb.lookup(item.path)
        except Exception:
            return None, ""
        if not isinstance(profile, dict):
            return None, ""
        preset: Dict[str, Any] = {}
        files = profile.get("files") or []
        choices = profile.get("choices") or {}
        by_hash = {}
        for f in files:
            if isinstance(f, dict) and f.get("sha256"):
                by_hash[str(f["sha256"])] = f.get("rel_path")
        for file_hash in choices:
            rel = by_hash.get(str(file_hash))
            if rel:
                # Chỉ ép chọn file khi locale của file KHỚP locale của job
                # (lựa chọn ghi cho locale en không được ép sang job zh —
                # lỗi tích hợp C09: preset file làm job zh-Hans lỗi
                # E_LOCALE_NOT_FOUND sau khi job en đã ghi profile).
                # File chưa rõ locale (file_locale None — vd font, file
                # không mang locale) KHÔNG được preset khi job ép locale:
                # ghim file đó sẽ khiến extraction chỉ parse file không
                # locale rồi fail E_LOCALE_NOT_FOUND dù game CÓ locale đó
                # ở file khác (lỗi D6 mục 4: cùng file 2 lần, job 2 ép en).
                file_locale = None
                for loc in (profile.get("locales") or []):
                    if isinstance(loc, dict) and loc.get("entry_path") == rel:
                        file_locale = loc.get("locale")
                        break
                if item.locale is None or file_locale == item.locale:
                    preset["file"] = rel
                    break
        locales = profile.get("locales") or []
        if locales and not item.locale:
            first = locales[0]
            # KB thật trả locales là dict {entry_path, locale, ...}; bản
            # cũ/file tay có thể là chuỗi locale ("en").
            preset["locale"] = first.get("locale") if isinstance(first, dict) else first
        if not preset:
            return None, ""
        date = ""
        for key in ("updated_at", "updated", "date", "created_at"):
            if profile.get(key):
                date = str(profile[key])
                break
        return preset, "dùng tri thức từ %s" % (date or "KB")

    def _kb_remember_extract(self, kb, item, result):
        """Ghi nhớ profile + lần chạy vào KB (lỗi KB không hỏng game).

        remember() cần files dạng dict {rel_path, size, sha256, role} và
        locales dạng dict {entry_path, locale, ...}: files ở đây là
        inventory fingerprint (fp['files']), còn file đã trích nằm ở
        locales/choices. Ghi chuỗi đường dẫn vào files là lỗi tích hợp
        C09 (remember ném E_PLUGIN_INVALID rồi bị nuốt nên KB không bao
        giờ có files/locales/runs/thống kê)."""
        try:
            from sdk.kb import fingerprint_game
            fp = fingerprint_game(item.path)
        except Exception:
            return
        try:
            files = list(fp.get("files") or [])
            locales = [{"entry_path": f.path, "locale": f.locale,
                        "adapter_id": f.plugin,
                        "key_count": f.entries if f.entries else None}
                       for f in result.files if f.locale]
            choices = {f.sha256: f.plugin for f in result.files if f.sha256}
            game_id = kb.remember(fp, files, locales, choices)
            kb.record_run(game_id, "extract", "ok")
            for f in result.files:
                try:
                    kb.record_plugin_stats(f.plugin, "", True)
                except Exception:
                    pass
        except Exception:
            pass

    # -- mod -----------------------------------------------------------------
    # Adapter Unity mức SDK (F2, đợt rà soát tổng cuối): các helper dưới đây
    # là bản sao y hệt logic của services.unity_tool (_backend,
    # _read_file_bytes, _resolve_vpath/_read_segment, _default_output_rel),
    # kể cả từng chữ thông điệp lỗi _usage — để sdk/batch.py KHÔNG còn
    # import services.unity_tool mà hành vi batch/mod và thông điệp lỗi CLI
    # không đổi một byte. Giá phải trả là trùng lặp có chủ ý: nếu
    # services.unity_tool đổi logic/thông điệp, bản sao ở đây phải đổi theo
    # (test khóa tests/test_final_review.py pin cả hai phía); bước dọn đúng
    # là chuyển các helper này sang sdk/unity.py rồi để unity_tool delegate
    # — việc đó cần sửa file ngoài phạm vi đợt này, để coordinator xếp lịch.
    def _mod_job(self, item, staging, ctx) -> Dict[str, Any]:
        if item.old is None or item.new is None:
            raise PB3Error("E_CONFIG_INVALID",
                           "job mod thiếu old/new (cần cả hai để patch)",
                           where=item.path)
        if item.object is None:
            raise PB3Error("E_CONFIG_INVALID",
                           "job mod thiếu object (path_id TextAsset)",
                           where=item.path)
        try:
            obj_id = int(item.object)
        except ValueError:
            raise PB3Error("E_CONFIG_INVALID",
                           "object phải là số (path_id): %r" % item.object,
                           where=item.path)
        from sdk import unity as unity_sdk
        limits = ctx.get("limits") or Limits()
        app = ctx.get("app")
        if app is not None:
            backend = _sdk_make_backend(app)
            tempdir = str(app.paths.temp)
        else:
            backend = unity_sdk.UnityBackend("AUTO", limits)
            tempdir = str(staging)
        data = _sdk_read_file_bytes(item.path, limits)
        kind, unit_data, req_entry, label = _sdk_resolve_vpath(
            data, item.file, backend, limits)
        req = unity_sdk.PatchRequest(
            data=unit_data, kind=kind, entry=req_entry, path_id=obj_id,
            old_text=item.old, new_text=item.new,
            backend=backend, temp_dir=tempdir,
            from_container=item.file is not None
            and unity_sdk.detect_bundle(data) is None)
        result = unity_sdk.apply_patch(req)
        rep = result.report
        kb = ctx.get("kb")
        if kb is not None:
            self._kb_record_mod(kb, item, "ok")
        if ctx.get("dry_run"):
            return {"status": "ok", "files": [],
                    "note": "dry-run patch OK (%s): %s, chuỗi %d -> %d byte "
                            "(chưa ghi file)" % (
                                result.unit, rep.target, rep.old_script_len,
                                rep.new_script_len)}
        rel = _sdk_default_output_rel(item.path, kind, label)
        name = os.path.basename(rel)
        dest = staging / name
        safe_io.atomic_write_bytes(str(dest), result.output_bytes)
        return {"status": "ok", "files": [name],
                "note": "patch OK (%s): %s, đơn vị %d -> %d byte" % (
                    result.unit, rep.target, rep.size_before, rep.size_after)}

    def _kb_record_mod(self, kb, item, outcome: str):
        """Ghi lần chạy mod vào KB (không đụng profile đã có).

        record_run cần game_id int (remember trả về), không phải bundle_id
        chuỗi (lỗi tích hợp C09: record_run ném E_INPUT_NOT_FOUND rồi bị
        nuốt nên mod không bao giờ ghi được runs)."""
        try:
            from sdk.kb import fingerprint_game
            fp = fingerprint_game(item.path)
            game_id = None
            try:
                profile = kb.lookup(item.path)
            except Exception:
                profile = None
            if isinstance(profile, dict) and isinstance(profile.get("game"), dict) \
                    and profile["game"].get("id") is not None:
                game_id = int(profile["game"]["id"])
            else:
                game_id = kb.remember(fp, list(fp.get("files") or []), [], {})
            kb.record_run(game_id, "mod", outcome)
        except Exception:
            pass
