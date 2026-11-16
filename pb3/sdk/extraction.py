"""Pipeline trích xuất ngôn ngữ (Claude 07, đặc tả mục 7.2).

7 bước, log từng bước:
  1) Inventory (sdk.discovery): liệt kê entry, lọc thô, chấm text.
  2) Engine hints: detect_engine (nhiều dấu hiệu, không kết luận chắc
     chỉ từ tên file).
  3) Candidate generation: plugin định dạng (Claude 06) qua list_candidates
     trên từng lớp VFS (gốc, container lồng, TextAsset Unity ảo) + ứng viên
     theo tên/đường dẫn (7.3) + tham chiếu manifest/index.
  4) Probe: đọc phần đầu mỗi ứng viên, xác định schema.
  5) Scoring + arbitration (sdk.arbitration, Phụ lục C): chọn file
     theo từng locale; ngang điểm -> hỏi người dùng (không tự đoán).
  6) Locale resolution: tách bảng đa locale theo cột/entry.locale.
  7) Parse -> IR -> normalize -> export + verify (sdk.exporters).

KHÔNG viết lại parser: mọi parse/build đều gọi qua plugin Claude 06
(parse/build/verify của SDK). `generic_strings` chỉ là lưới cuối, kèm
cảnh báo false positive. Plugin/định dạng con chỉ đọc (sqlite_loc,
generic_strings, plist nhị phân, MO) từ chối ghi bằng E_WRITE_DISABLED;
entry unparsed (XLIFF/RESX markup) bỏ qua kèm warning; key trùng chỉ ghi
bản ghi đầu.

Checkpoint/resume: extraction_checkpoint.json trong thư mục out, khóa theo
hash nguồn; chạy lại với --resume thì bỏ qua file đã xong (hash khớp).
Ghi ngược: apply_edits đọc JSON đã sửa + extraction_report.json để tìm
nguồn, gọi plugin build/verify, ghi versioned vào workspace/output
(dry-run mặc định, không ghi đè nguồn).

Chỉ thư viện chuẩn, Python >= 3.8. Không import UnityPy/lz4.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from core import VERSION
from core.errors import PB3Error
from core.limits import Limits
from core.model import Provenance, SourcePath
from sdk import arbitration as arb
from sdk import discovery as disc
from sdk import exporters as exp
from sdk import unity as unity_sdk
from sdk.api import LocaleRequest, PluginContext
from sdk.containers import NestedVFS, open_vfs
from sdk.ir import IRDocument, from_tables
from sdk.types import LocEntry, LocTable, SourceRef
from sdk.vfs import MemoryVFS

MAX_PROBE_CANDIDATES = 200
CHECKPOINT_FILENAME = "extraction_checkpoint.json"
CHECKPOINT_VERSION = 1
REPORT_JSON = "extraction_report.json"
REPORT_TXT = "extraction_report.txt"

# Mẫu tên/đường dẫn gợi ý file ngôn ngữ (đặc tả 7.3, không phân biệt hoa thường).
_NAME_SIGNAL_RE = re.compile(
    r"(language|locali[sz]ation|locale|translation|i18n|l10n|lang|"
    r"text|strings?|stringtable)", re.I)

# Magic -> plugin (chỉ để chấm điểm magic_match, không phải parser).
_MAGIC_FOR_PLUGIN = {
    "json_text": (lambda h: h.lstrip(b"\xef\xbb\xbf \t\r\n")[:1] in (b"{", b"["),),
    "xml_strings": (lambda h: h.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"<?xml") or
                    h.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"<resources"),),
    "apple_plist": (lambda h: h.startswith(b"bplist00") or
                    h.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"<?xml"),),
    "sqlite_loc": (lambda h: h.startswith(b"SQLite format 3"),),
    "po_mo": (lambda h: h.startswith(b"msgid") or h.startswith(b"\xde\x12\x04\x95") or
              h.startswith(b"\x95\x04\x12\xde"),),
    "xliff": (lambda h: b"<xliff" in h[:512],),
    "resx": (lambda h: b"<root" in h[:512] and b"resx" in h[:512].lower(),),
}


def _sanitize_segment(name: str) -> str:
    """Tên TextAsset -> một đoạn vpath an toàn (không ':', không '/')."""
    safe = re.sub(r"[:/\\]+", "_", str(name or "")).strip()
    return safe or "textasset"


def _game_name(source: Any) -> str:
    """Tên game từ đường dẫn đầu vào (như unity_tool._default_output_rel)."""
    if isinstance(source, (bytes, bytearray)):
        return "game"
    stem = os.path.splitext(os.path.basename(str(source).rstrip("/")))[0]
    game = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in stem)
    return game or "game"


def _source_hash(source: Any, vfs) -> str:
    """Hash nguồn để khóa checkpoint: file -> sha256 bytes; thư mục -> hash
    danh sách (đường dẫn, kích thước); bytes -> sha256."""
    if isinstance(source, (bytes, bytearray)):
        return hashlib.sha256(bytes(source)).hexdigest()
    path = str(source)
    if os.path.isfile(path):
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    listing = []
    for name in sorted(vfs.iter_entries()):
        try:
            listing.append((name, vfs.stat(name).size))
        except PB3Error:
            listing.append((name, -1))
    return hashlib.sha256(json.dumps(listing, ensure_ascii=False).encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ dữ liệu
@dataclass
class ExtractionRequest:
    source: Any
    registry: Any = None
    limits: Optional[Limits] = None
    locale: Optional[str] = None       # None = preset en+zh (rồi mọi locale còn lại); "all" = tất cả; mã cụ thể = một locale
    pin_file: Optional[str] = None     # vpath ép chọn làm file nguồn
    out_dir: Optional[str] = None      # thư mục ghi output (None = chỉ chạy, không ghi)
    dry_run: bool = False
    resume: bool = False
    preset: Optional[Dict[str, Any]] = None   # {"file": vpath, "locale": code}
    ask_fn: Optional[Callable[[List[Dict[str, Any]]], Optional[int]]] = None
    log_fn: Optional[Callable[[str], None]] = None
    progress_fn: Optional[Callable[[str, int, Optional[int]], None]] = None
    # progress_fn(bước, đã_xong, tổng): báo tiến độ THẬT từng bước pipeline
    # (luật #152). bước = "inventory" (tổng None: tổng chưa biết trước, chỉ
    # báo số entry đã liệt kê) | "layers" | "candidates" | "sniff" |
    # "probe" | "export". None = không báo (mặc định, giữ nguyên hành vi cũ).
    tempdir: Optional[str] = None
    root: Optional[str] = None         # gốc dự án (đọc config/locales.toml)
    source_paths: Optional[List[str]] = None  # vpath chỉ trích xuất các entry này (None/rỗng = tự động, xét hết như cũ); entry con "w::..." của một entry đã chọn cũng được xét
    locales: Optional[set] = None      # lọc mềm nhiều locale ở vòng export (vd {"en", "zh"}); None = không lọc. Khác req.locale (lọc cứng 1 locale + E_LOCALE_NOT_FOUND khi vắng): lọc mềm KHÔNG fail cả đợt khi một locale vắng mặt.


@dataclass
class FileOutcome:
    path: str
    plugin: str
    locale: Optional[str]
    source: str = ""                   # nguồn gốc (đơn vị Unity chứa TextAsset, hoặc chính path với file thường) — để báo `output ← source`
    entries: int = 0
    ok: int = 0
    empty: int = 0
    conflicts: int = 0
    unparsed: int = 0
    warnings: List[str] = field(default_factory=list)
    exported: Optional[str] = None
    editable: bool = False
    source_kind: str = "verified"      # verified | raw_scan
    kv: Dict[str, str] = field(default_factory=dict)
    sha256: str = ""                   # sha256 file nguồn (để khóa checkpoint)


@dataclass
class ExtractionResult:
    ok: bool
    decision: str                      # done | ask | none
    engine: str = "unknown"
    engine_level: str = "CHƯA ĐỦ BẰNG CHỨNG"
    engine_confidence: float = 0.0
    engine_reasons: List[str] = field(default_factory=list)
    files: List[FileOutcome] = field(default_factory=list)
    skipped: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[Dict[str, Any]] = field(default_factory=list)
    generated_keys: List[str] = field(default_factory=list)
    total_entries: int = 0
    elapsed_s: float = 0.0
    candidates: List[Dict[str, Any]] = field(default_factory=list)
    checkpoint_used: bool = False
    out_dir: Optional[str] = None
    report: Dict[str, Any] = field(default_factory=dict)
    ir: Optional[IRDocument] = None


# ------------------------------------------------------------------ lớp VFS
class _Layer:
    """Một lớp VFS để quét ứng viên: (tiền tố vpath, vfs)."""

    def __init__(self, prefix: str, vfs, kind: str):
        self.prefix = prefix
        self.vfs = vfs
        self.kind = kind


_UNITY_SOURCE_EXTS = (".assets", ".unity3d", ".bundle", ".resS")


def _source_of(path: str) -> str:
    """Nguồn gốc của 1 file trích xuất (để báo `output ← source`).

    - TextAsset Unity: đơn vị chứa nó — phần trước "::" mà kết thúc bằng
      đuôi Unity (.assets/.unity3d/.bundle), vd
      `.../localization-locales_assets_all.bundle::CAB-...::en.json`
      -> `.../localization-locales_assets_all.bundle`;
      file .assets nằm trong ZIP: `game.zip::Data/x.assets::en`
      -> `game.zip::Data/x.assets`.
    - File thường: chính đường dẫn đó.
    - Bundle không có đuôi Unity (chỉ nhận bằng magic UnityFS) thì trả
      nguyên đường dẫn ảo (vẫn nhận ra nguồn, chỉ dài hơn)."""
    parts = str(path).split("::")
    if len(parts) >= 2:
        for i in range(1, len(parts)):
            prefix = "::".join(parts[:i])
            if prefix.lower().endswith(_UNITY_SOURCE_EXTS):
                return prefix
    return str(path)


def _expand_unity(data: bytes, vpath: str, backend, limits) -> Tuple[Dict[str, bytes], List[str]]:
    """TextAsset trong một entry Unity -> ({vpath ảo: script UTF-8}, notes).

    Bundle: mở từng entry .assets bên trong rồi đọc TextAsset; file
    .assets độc lập: đọc thẳng. TextAsset hỏng bị bỏ qua (kèm ghi chú
    trong warnings của SerializedFile, không làm hỏng cả file).

    KHÔNG BAO GIỜ bỏ qua lặng lẽ (lỗi en.json 2026-11-16: bundle
    localization biến mất khỏi report vì mọi đường lỗi đều `return {}`
    im lặng): mọi vấn đề đều ghi vào `notes` — parse lỗi (kèm mã),
    entry không phải SerializedFile, bundle cho 0 TextAsset — để caller
    ghi vào skipped/log thay vì để bundle biến mất."""
    out: Dict[str, bytes] = {}
    notes: List[str] = []
    assets: List[Tuple[str, Any]] = []
    det = unity_sdk.detect_bundle(data)
    if det is not None:
        try:
            bundle = unity_sdk.parse_bundle(data, backend, limits, where=vpath)
        except PB3Error as exc:
            notes.append("parse bundle lỗi [%s]: %s"
                         % (exc.code, exc.message_vi or exc.detail))
            return out, notes
        for name in bundle.entry_names():
            try:
                entry_data = bundle.get_entry(name)
            except PB3Error as exc:
                notes.append("không đọc được entry %s trong bundle [%s]"
                             % (name, exc.code))
                continue
            try:
                assets.append((name, unity_sdk.parse_serialized(entry_data, limits, where=name)))
            except PB3Error as exc:
                notes.append("entry %s không phải SerializedFile [%s]: %s"
                             % (name, exc.code, exc.message_vi or exc.detail))
                continue
    else:
        try:
            assets.append(("", unity_sdk.parse_serialized(data, limits, where=vpath)))
        except PB3Error as exc:
            notes.append("parse SerializedFile lỗi [%s]: %s"
                         % (exc.code, exc.message_vi or exc.detail))
            return out, notes
    n_text_assets = 0
    for assets_name, sf in assets:
        tas = sf.text_assets()
        n_text_assets += len(tas)
        for _pid, name, script in tas:
            safe = _sanitize_segment(name)
            base = vpath + ("::" + assets_name if assets_name else "")
            out[base + "::" + safe] = script.encode("utf-8")
    if not out:
        notes.append("không có TextAsset nào (%d SerializedFile đã mở, %d TextAsset)"
                     % (len(assets), n_text_assets))
    return out, notes


def _build_layers(root_vfs, inv: List[disc.InventoryEntry], limits, backend, log,
                  progress_fn: Optional[Callable[[str, int, Optional[int]], None]] = None,
                  skipped: Optional[List[Dict[str, Any]]] = None
                  ) -> List[_Layer]:
    """Các lớp VFS: gốc + container lồng + TextAsset Unity ảo (MemoryVFS).

    progress_fn("layers", đã_xong, tổng): báo sau mỗi entry đã xử lý xong
    (lag 1 entry: báo số entry trước đó để % luôn là việc ĐÃ xong).

    KHÔNG BAO GIỜ bỏ qua lặng lẽ (lỗi en.json 2026-11-16): container/
    file Unity nào đọc lỗi, quá giới hạn, hoặc cho 0 TextAsset đều ghi
    vào `skipped` (nếu truyền vào) kèm lý do + đường dẫn đầy đủ, và log.
    Giới hạn 64MB/entry vẫn giữ (bảo vệ RAM tab iPhone; bundle text
    localization >64MB là không tưởng) — nhưng vượt giới hạn thì phải
    BÁO RÕ để lần chạy sau trên IPA thật đọc được lý do, thay vì bundle
    biến mất như trước."""
    layers = [_Layer("", root_vfs, "root")]
    nested = NestedVFS(root_vfs, limits=limits)
    virtual: Dict[str, bytes] = {}
    total = len(inv)

    def _add_skipped(path: str, reason: str) -> None:
        if skipped is None:
            return
        if any(s.get("path") == path for s in skipped):
            return
        skipped.append({"path": path, "reason": reason})

    for i, entry in enumerate(inv, 1):
        if i > 1 and progress_fn is not None:
            try:
                progress_fn("layers", i - 1, total)
            except Exception:
                pass
        if entry.kind == "container":
            try:
                data = bytes(nested.read(entry.path, None))
            except PB3Error as exc:
                _add_skipped(entry.path, "không đọc được container [%s]: %s"
                             % (exc.code, exc.message_vi or exc.detail))
                log("container %s: không đọc được [%s]" % (entry.path, exc.code))
                continue
            if len(data) > limits.max_entry_bytes:
                _add_skipped(entry.path, "container %d byte > giới hạn entry %d byte"
                             % (len(data), limits.max_entry_bytes))
                log("container %s: %d byte > giới hạn %d, bỏ qua"
                    % (entry.path, len(data), limits.max_entry_bytes))
                continue
            try:
                child = open_vfs(data, limits=limits, source_name=entry.path)
            except PB3Error as exc:
                _add_skipped(entry.path, "không mở được container [%s]: %s"
                             % (exc.code, exc.message_vi or exc.detail))
                log("container %s: không mở được [%s]" % (entry.path, exc.code))
                continue
            layers.append(_Layer(entry.path + "::", child, "nested"))
        elif entry.kind == "unity":
            if entry.size > limits.max_entry_bytes:
                _add_skipped(entry.path, "file Unity %d byte > giới hạn entry %d byte — "
                             "không mở bundle/.assets (cần nâng giới hạn Unity nếu "
                             "bundle thật vượt ngưỡng này)" % (entry.size, limits.max_entry_bytes))
                log("Unity: %s: %d byte > giới hạn %d, bỏ qua"
                    % (entry.path, entry.size, limits.max_entry_bytes))
                continue
            try:
                data = bytes(nested.read(entry.path, None))
            except PB3Error as exc:
                _add_skipped(entry.path, "không đọc được file Unity [%s]: %s"
                             % (exc.code, exc.message_vi or exc.detail))
                log("Unity: %s: không đọc được [%s]" % (entry.path, exc.code))
                continue
            got, notes = _expand_unity(data, entry.path, backend, limits)
            if got:
                virtual.update(got)
                log("Unity: %s -> %d TextAsset" % (entry.path, len(got)))
                for n in notes:
                    log("Unity: %s: %s" % (entry.path, n))
            else:
                reason = "; ".join(notes) if notes else "không có TextAsset nào"
                _add_skipped(entry.path, reason)
                log("Unity: %s -> 0 TextAsset (%s)" % (entry.path, reason))
    if progress_fn is not None:
        try:
            progress_fn("layers", total, total)
        except Exception:
            pass
    if virtual:
        layers.append(_Layer("", MemoryVFS(virtual, limits=limits), "virtual"))
    return layers


def _find_layer(layers: List[_Layer], full_path: str) -> Tuple[Any, str]:
    """Tìm lớp VFS chứa full_path; trả (vfs, đường dẫn tương đối trong lớp)."""
    for layer in layers:
        if layer.prefix and not full_path.startswith(layer.prefix):
            continue
        rel = full_path[len(layer.prefix):]
        try:
            if layer.vfs.exists(rel):
                return layer.vfs, rel
        except PB3Error:
            continue
    raise PB3Error("E_INPUT_NOT_FOUND", "không tìm thấy %s trong các lớp VFS" % full_path,
                   where=full_path)


# ------------------------------------------------------------------ pipeline
def run_extraction(req: ExtractionRequest) -> ExtractionResult:
    """Chạy pipeline 7 bước (đặc tả 7.2). Không raise với lỗi từng file:
    lỗi được ghi vào result.errors/skipped, pipeline chạy tiếp."""
    t0 = time.monotonic()
    limits = req.limits or Limits()
    logs: List[str] = []
    # Lọc mềm nhiều locale: chuẩn hóa về set chữ thường; rỗng = không lọc.
    locales = ({str(c).lower() for c in req.locales} if req.locales else None)
    wanted_txt = "/".join(sorted(locales)) if locales else ""

    def log(msg: str) -> None:
        logs.append(msg)
        if req.log_fn is not None:
            try:
                req.log_fn(msg)
            except Exception:
                pass

    def _progress(phase: str, done: int, total: Optional[int]) -> None:
        """Báo tiến độ thật cho req.progress_fn (nếu có). Tiến độ không bao
        giờ được làm hỏng pipeline: lỗi trong callback thì bỏ qua."""
        fn = req.progress_fn
        if fn is not None:
            try:
                fn(phase, done, total)
            except Exception:
                pass

    def _report_lag(phase: str, index_1based: int, total: int) -> None:
        """Báo số mục ĐÃ xử lý xong (lag 1: ở đầu mục thứ i thì báo i-1 đã
        xong; gọi _report_done(total) sau vòng lặp để chốt 100%)."""
        if index_1based > 1:
            _progress(phase, index_1based - 1, total)

    result = ExtractionResult(ok=False, decision="none", out_dir=req.out_dir)
    if req.registry is None:
        result.errors.append({"path": "", "code": "E_PLUGIN_INVALID",
                              "message": "pipeline cần registry (plugin định dạng)"})
        result.report = _build_report(req, result, logs, t0)
        return result
    if req.preset:
        if req.preset.get("file") and not req.pin_file:
            req.pin_file = str(req.preset["file"])
        if req.preset.get("locale") and not req.locale:
            req.locale = str(req.preset["locale"])

    # -- bước 1: inventory -------------------------------------------------
    try:
        root_vfs = disc.open_source_vfs(req.source, limits)
    except PB3Error as exc:
        result.errors.append({"path": str(req.source), "code": exc.code,
                              "message": exc.message_vi or exc.detail})
        result.report = _build_report(req, result, logs, t0)
        return result
    try:
        inv = disc.inventory(
            root_vfs, limits,
            on_entry=lambda done, name: _progress("inventory", done, None))
    except PB3Error as exc:
        result.errors.append({"path": str(req.source), "code": exc.code,
                              "message": exc.message_vi or exc.detail})
        result.report = _build_report(req, result, logs, t0)
        return result
    log("inventory: %d entry (%d file, %d container, %d unity, %d loại trừ)"
        % (len(inv), sum(1 for e in inv if e.kind == "file"),
           sum(1 for e in inv if e.kind == "container"),
           sum(1 for e in inv if e.kind == "unity"),
           sum(1 for e in inv if e.kind == "excluded")))
    for e in inv:
        if e.kind == "excluded":
            result.skipped.append({"path": e.path, "reason": e.excluded_reason or "loại trừ"})

    # Chọn file (UI: source_paths): chỉ xét các entry đã chọn (+ entry con
    # "w::..." của một entry đã chọn, vd file trong ZIP đã chọn). Entry
    # không được chọn ghi skipped kèm lý do, không biến mất lặng lẽ.
    # Rỗng/None = tự động (xét hết như cũ — tool tự tìm đúng file).
    if req.source_paths:
        wanted_paths = {str(p) for p in req.source_paths}
        kept_inv = []
        for e in inv:
            if e.kind == "excluded":
                kept_inv.append(e)
                continue
            if e.path in wanted_paths or any(
                    e.path.startswith(w + "::") for w in wanted_paths):
                kept_inv.append(e)
                continue
            result.skipped.append({"path": e.path,
                                   "reason": "không được chọn (chỉ trích xuất file đã chọn)"})
            log("bỏ qua %s: không được chọn" % e.path)
        inv = kept_inv

    # -- bước 2: engine hints ----------------------------------------------
    names = [e.path for e in inv]
    heads: Dict[str, bytes] = {}
    for e in inv:
        # Container cũng lấy mẫu đầu: magic của .pck (GDPC) nằm ở đầu file,
        # detect_engine cần nó (Claude 08); chỉ đọc 64 byte, không mở con.
        if "::" in e.path or e.kind not in ("unity", "file", "container") or e.size <= 0 or len(heads) >= 200:
            continue
        try:
            heads[e.path] = bytes(root_vfs.slice(e.path, 0, 64))
        except Exception:
            pass
    guess = disc.detect_engine(names, heads)
    result.engine = guess.engine
    result.engine_level = guess.level
    result.engine_confidence = guess.confidence
    result.engine_reasons = [ev.reason for ev in guess.evidence]
    log("engine: %s (%s, tin cậy %.2f)" % (guess.engine, guess.level, guess.confidence))

    # -- bước 3: candidate generation ---------------------------------------
    backend = unity_sdk.UnityBackend("AUTO", limits)
    layers = _build_layers(root_vfs, inv, limits, backend, log,
                           progress_fn=_progress, skipped=result.skipped)
    records = [r for r in req.registry.active()
               if r.manifest is not None and r.manifest.kind in ("format", "extractor")]
    log("plugin định dạng: %d (%s)"
        % (len(records), ", ".join(sorted(r.plugin_id for r in records))))
    locale_table = disc.load_locale_table(req.root)
    # Tham chiếu manifest/index (7.3): file JSON nào trỏ tới bảng khác.
    referenced: set = set()
    for entry in inv:
        if entry.kind != "file" or not entry.path.lower().endswith(".json"):
            continue
        if entry.size > 1024 * 1024:
            continue
        try:
            vfs_for, rel = _find_layer(layers, entry.path)
            data = bytes(vfs_for.read(rel, None))
        except PB3Error:
            continue
        for ref in disc.find_referenced_paths(vfs_for, data, rel):
            referenced.add(ref)
    if referenced:
        log("manifest/index tham chiếu %d file: %s"
            % (len(referenced), ", ".join(sorted(referenced)[:5])))

    # Ứng viên theo từng plugin/lớp.
    raw: Dict[Tuple[str, str], Dict[str, Any]] = {}
    excluded_paths = {e.path: (e.excluded_reason or "loại trừ") for e in inv if e.kind == "excluded"}
    score_by_path = {e.path: e.text_score for e in inv}
    _skipped_paths = {s.get("path") for s in result.skipped}

    def _add_skipped(path: str, reason: str) -> None:
        if path in _skipped_paths:
            return
        _skipped_paths.add(path)
        result.skipped.append({"path": path, "reason": reason})

    _cand_total = len(records) * len(layers)
    _cand_i = 0
    for rec in records:
        plugin = rec.instance
        for layer in layers:
            _cand_i += 1
            _report_lag("candidates", _cand_i, _cand_total)
            ctx = PluginContext(layer.vfs, limits, tempdir=req.tempdir,
                                dry_run=req.dry_run).for_plugin(rec.manifest)
            try:
                cands = plugin.list_candidates(ctx)
            except PB3Error as exc:
                result.errors.append({"path": layer.prefix or "<gốc>", "code": exc.code,
                                      "message": "list_candidates %s lỗi: %s"
                                                 % (rec.plugin_id, exc.message_vi)})
                continue
            # Lưới cuối (generic_strings): giữ tối đa 25 ứng viên/lớp, ưu
            # tiên file có khả năng chứa text cao nhất (giới hạn tài nguyên);
            # phần còn lại ghi rõ lý do, không bỏ im lặng.
            if rec.plugin_id == "generic_strings" and len(cands) > 25:
                cands = sorted(cands, key=lambda c: -score_by_path.get(layer.prefix + c.path, 0.5))[:25]
                _add_skipped(layer.prefix or "<gốc>",
                             "lưới cuối giới hạn 25 ứng viên/lớp")
            for cand in cands:
                full = layer.prefix + cand.path
                if full in excluded_paths:
                    _add_skipped(full, excluded_paths[full])
                    continue
                key = (rec.plugin_id, full)
                if key in raw:
                    continue
                raw[key] = {"plugin": rec, "candidate": cand, "full": full,
                            "layer": layer, "head": b""}
    _progress("candidates", _cand_total, _cand_total)
    # Sniff pass: file không có đuôi khớp (vd TextAsset Unity không đuôi)
    # nhưng nội dung giống một định dạng -> ứng viên theo _sniff của plugin.
    # File đã có plugin định dạng nhận (không phải lưới cuối) thì bỏ qua;
    # file chỉ có generic_strings nhận thì vẫn sniff tiếp (trọng tài sẽ
    # chọn giữa định dạng thật và dò chuỗi thô). Chỉ xét mẫu đầu gần như
    # toàn text (tỉ lệ ký tự in được >= 0.9, hoặc BOM UTF-16) để khỏi sniff
    # cả file nhị phân; mỗi file chỉ đọc 4KB đầu.
    claimed_by_format = {v["full"] for v in raw.values()
                         if v["plugin"].plugin_id != "generic_strings"}
    claimed_paths = {v["full"] for v in raw.values()}
    _sniff_lists: List[Tuple[Any, List[str]]] = []
    for layer in layers:
        try:
            _sniff_lists.append((layer, list(layer.vfs.iter_entries())))
        except PB3Error:
            continue
    _sniff_total = sum(len(fs) for _l, fs in _sniff_lists)
    _sniff_i = 0
    for layer, layer_files in _sniff_lists:
        for rel in layer_files:
            _sniff_i += 1
            _report_lag("sniff", _sniff_i, _sniff_total)
            full = layer.prefix + rel
            if full in claimed_by_format or full in excluded_paths:
                continue
            try:
                head = bytes(layer.vfs.slice(rel, 0, 4096))
            except Exception:
                try:
                    head = bytes(layer.vfs.read(rel, max_bytes=4096))
                except PB3Error:
                    continue
            if not (head.startswith((b"\xff\xfe", b"\xfe\xff"))
                    or disc.head_text_ratio(head) >= 0.9):
                continue
            for rec in records:
                plugin = rec.instance
                sniff = getattr(plugin, "_sniff", None)
                if not callable(sniff):
                    continue
                try:
                    s = float(sniff(head, rel) or 0.0)
                except Exception:
                    continue
                if s < 0.5:
                    continue
                score_fn = getattr(plugin, "_score", None)
                try:
                    score = float(score_fn(head, rel)) if callable(score_fn) else 0.5
                except Exception:
                    score = 0.5
                from sdk.types import Candidate
                cand = Candidate(rel, rec.plugin_id, score,
                                 ["nội dung giống %s (sniff %.2f)" % (rec.plugin_id, s)])
                raw[(rec.plugin_id, full)] = {"plugin": rec, "candidate": cand,
                                              "full": full, "layer": layer, "head": head,
                                              "via_sniff": True, "sniff": s}
                claimed_paths.add(full)
    _progress("sniff", _sniff_total, _sniff_total)
    # Ứng viên theo tên/đường dẫn (7.3) mà chưa plugin nào nhận.
    claimed = {v["full"] for v in raw.values()}
    for entry in inv:
        if entry.kind != "file" or entry.path in claimed:
            continue
        if not _NAME_SIGNAL_RE.search(entry.path) or entry.text_score < 0.5:
            continue
        rec = next((r for r in records if r.plugin_id == "generic_strings"), None)
        if rec is None:
            continue
        raw[("generic_strings", entry.path)] = {
            "plugin": rec, "candidate": None, "full": entry.path,
            "layer": layers[0], "head": b"", "heuristic": True}
    log("ứng viên thô: %d" % len(raw))

    # -- bước 4: probe -------------------------------------------------------
    probed: List[Dict[str, Any]] = []
    _probe_keys = sorted(raw, key=lambda k: (k[1], k[0]))
    _probe_total = len(_probe_keys)
    for _probe_i, key in enumerate(_probe_keys, 1):
        _report_lag("probe", _probe_i, _probe_total)
        item = raw[key]
        rec = item["plugin"]
        plugin = rec.instance
        layer = item["layer"]
        cand = item["candidate"]
        if cand is None:
            from sdk.types import Candidate
            cand = Candidate(item["full"], rec.plugin_id, 0.4,
                             ["tên/đường dẫn gợi ý file ngôn ngữ"])
            item["candidate"] = cand
        ctx = PluginContext(layer.vfs, limits, tempdir=req.tempdir,
                            dry_run=req.dry_run).for_plugin(rec.manifest)
        try:
            head = bytes(layer.vfs.read(cand.path, max_bytes=4096))
        except PB3Error:
            head = b""
        item["head"] = head
        # File index/catalog (JSON trỏ tới >= 2 bảng ngôn ngữ khác) thì
        # không phải bảng dịch: bỏ qua làm ứng viên, giữ lại tham chiếu.
        if cand.path.lower().endswith(".json"):
            try:
                data = bytes(layer.vfs.read(cand.path, max_bytes=1024 * 1024))
                refs = disc.find_referenced_paths(layer.vfs, data, cand.path)
                if len(refs) >= 2:
                    result.skipped.append(
                        {"path": item["full"],
                         "reason": "file index/catalog, tham chiếu tới %d file (%s)"
                                   % (len(refs), ", ".join(refs[:3]))})
                    continue
            except PB3Error:
                pass
        try:
            probe = plugin.probe(ctx, cand)
        except PB3Error as exc:
            result.skipped.append({"path": item["full"],
                                   "reason": "probe %s lỗi [%s]" % (rec.plugin_id, exc.code)})
            continue
        except Exception as exc:  # noqa: BLE001 - probe (kể cả plugin ghi đè) không được crash extract
            result.skipped.append({"path": item["full"],
                                   "reason": "probe %s lỗi [E_INTERNAL: %s]"
                                             % (rec.plugin_id, type(exc).__name__)})
            continue
        if not probe.get("ok"):
            result.skipped.append({"path": item["full"],
                                   "reason": "probe %s: %s" % (rec.plugin_id, probe.get("reason", "?"))})
            continue
        item["probe"] = probe
        probed.append(item)
        if len(probed) >= MAX_PROBE_CANDIDATES:
            result.skipped.append({"path": "",
                                   "reason": "quá %d ứng viên probe được, chỉ xét %d đầu"
                                             % (MAX_PROBE_CANDIDATES, MAX_PROBE_CANDIDATES)})
            break
    if _probe_total:
        # Chốt đúng số đã probe xong (vòng lặp có thể break sớm ở giới hạn
        # MAX_PROBE_CANDIDATES thì không báo đủ tổng).
        _progress("probe", _probe_i, _probe_total)
    log("probe ok: %d ứng viên" % len(probed))
    # Lọc mềm nhiều locale (req.locales, vd {"en", "zh"}): bỏ ứng viên có
    # locale phát hiện ĐÃ RÕ mà không thuộc tập lọc — ghi skipped, không
    # để nhóm locale bị loại gây ASK rồi làm hỏng cả đợt. Entry chưa rõ
    # locale (None) vẫn đi tiếp; vòng export mới quyết theo locale thật
    # của từng entry (vd CSV đa locale không có locale trong tên).
    if locales is not None:
        kept_probed = []
        _locale_skip_seen = set()
        for item in probed:
            code, _conf, _ev = disc.detect_locale(item["full"], locale_table)
            if code is not None and code not in locales:
                # Cùng đường dẫn có thể được probe bởi nhiều plugin: ghi
                # skipped 1 lần cho 1 đường dẫn, khỏi trùng lặp.
                if item["full"] not in _locale_skip_seen:
                    _locale_skip_seen.add(item["full"])
                    result.skipped.append({"path": item["full"],
                                           "reason": "locale %s không phải %s"
                                                     % (code, wanted_txt)})
                continue
            kept_probed.append(item)
        probed = kept_probed
    if not probed:
        result.decision = "none"
        result.report = _build_report(req, result, logs, t0)
        return result

    # -- bước 5: scoring + arbitration (theo từng locale) -------------------
    # Nhóm theo locale; file không rõ locale thì mỗi file một nhóm riêng
    # (không gom chung rồi chỉ chọn một).
    groups: Dict[Any, List[Dict[str, Any]]] = {}
    for item in probed:
        code, _conf, _ev = disc.detect_locale(item["full"], locale_table)
        key: Any = code if code else ("__file__", item["full"])
        groups.setdefault(key, []).append(item)
    chosen: List[Dict[str, Any]] = []
    ask_groups: List[Dict[str, Any]] = []
    # --file/preset: ép chọn đúng file đó (đã probe ok), bỏ qua trọng tài.
    if req.pin_file:
        pinned = [it for it in probed if it["full"] == req.pin_file]
        if not pinned:
            result.errors.append({"path": req.pin_file, "code": "E_INPUT_NOT_FOUND",
                                  "message": "file ép chọn không có trong ứng viên đã probe"})
            result.decision = "none"
            result.report = _build_report(req, result, logs, t0)
            return result
        pinned[0]["group_locale"], _c, _e = disc.detect_locale(req.pin_file, locale_table)
        chosen = pinned
        log("ép chọn %s theo --file/preset" % req.pin_file)
    else:
        chosen, ask_groups, rejected = _arbitrate_groups(groups, referenced, locale_table, log)
        result.skipped.extend(rejected)
    if ask_groups and req.ask_fn is not None:
        answered = []
        for grp in ask_groups:
            idx = req.ask_fn(grp["options"])
            if idx is None:
                continue          # bỏ qua nhóm này: giữ lại để báo "ask"
            opt = grp["options"][idx]
            pick = next(it for it in probed
                        if it["plugin"].plugin_id == opt["plugin"] and it["full"] == opt["path"])
            pick["group_locale"] = grp["locale"]
            chosen.append(pick)
            answered.append(grp)
            log("người dùng chọn %s cho locale %s" % (pick["full"], grp["locale"] or "?"))
        ask_groups = [g for g in ask_groups if g not in answered]
    if ask_groups and not chosen:
        result.decision = "ask"
        result.candidates = ask_groups
        result.report = _build_report(req, result, logs, t0)
        return result
    if not chosen:
        result.decision = "none"
        result.report = _build_report(req, result, logs, t0)
        return result

    # -- bước 6+7: locale resolution, parse -> IR -> export + verify -------
    checkpoint = _load_checkpoint(req)
    source_hash = _source_hash(req.source, root_vfs) if req.out_dir else ""
    tables: List[LocTable] = []
    all_locales: set = set()   # mọi locale có trong game (trước khi lọc --locale)
    _exp_total = len(chosen)
    for _exp_i, item in enumerate(chosen, 1):
        _report_lag("export", _exp_i, _exp_total)
        rec = item["plugin"]
        plugin = rec.instance
        layer = item["layer"]
        cand = item["candidate"]
        ctx = PluginContext(layer.vfs, limits, tempdir=req.tempdir,
                            dry_run=req.dry_run).for_plugin(rec.manifest)
        try:
            base_bytes = bytes(layer.vfs.read(cand.path, None))
        except PB3Error as exc:
            result.errors.append({"path": item["full"], "code": exc.code,
                                  "message": "không đọc được file nguồn"})
            continue
        file_sha = hashlib.sha256(base_bytes).hexdigest()
        reused = None
        if req.resume and checkpoint and checkpoint.get("source_hash") == source_hash:
            reused = (checkpoint.get("files") or {}).get(item["full"])
            if reused and reused.get("sha256") == file_sha:
                result.checkpoint_used = True
                log("checkpoint: dùng lại %s" % item["full"])
        if reused is not None:
            for loc, kv in (reused.get("locales") or {}).items():
                loc_code = loc if loc != "" else None
                all_locales.add(loc_code)
                # IR vẫn đủ: dựng lại bảng từ kv đã lưu (nguồn ghi rõ).
                src = SourceRef.from_vpath(item["full"])
                tables.append(LocTable.from_key_values(
                    dict(kv), adapter_id=rec.plugin_id,
                    locale=loc_code, source=src))
                if locales is not None and loc_code not in locales:
                    result.skipped.append({"path": item["full"],
                                           "reason": "locale %s không phải %s"
                                                     % (loc_code or "không xác định", wanted_txt)})
                    continue
                if req.locale not in (None, "all") and loc_code != req.locale:
                    continue
                outcome = FileOutcome(item["full"], rec.plugin_id,
                                      loc_code,
                                      entries=len(kv), ok=len(kv), kv=dict(kv),
                                      editable=bool(rec.manifest.can_write),
                                      source_kind="raw_scan" if rec.plugin_id == "generic_strings"
                                      else "verified",
                                      warnings=["dùng checkpoint, không parse lại"],
                                      sha256=file_sha,
                                      source=_source_of(item["full"]))
                outcome.exported = reused.get("exported", {}).get(loc)
                result.files.append(outcome)
                result.total_entries += len(kv)
            continue
        try:
            table = plugin.parse(ctx, cand)
        except PB3Error as exc:
            result.errors.append({"path": item["full"], "code": exc.code,
                                  "message": "parse %s lỗi: %s" % (rec.plugin_id, exc.message_vi)})
            continue
        _normalize_table(table, rec, item["full"])
        tables.append(table)
        # Tách bảng đa locale theo entry.locale (CSV cột en/zh, .xcstrings...).
        # Bảng không mang locale thì dùng locale đã nhận diện từ tên file.
        by_locale: Dict[Optional[str], List[LocEntry]] = {}
        for e in table.entries:
            by_locale.setdefault(e.locale or table.locale or item.get("group_locale"), []).append(e)
        for loc, entries in sorted(by_locale.items(), key=lambda kv: (kv[0] or "")):
            all_locales.add(loc)
            # Lọc mềm nhiều locale (req.locales): chỉ trích xuất entry có
            # locale thuộc tập lọc; locale khác hoặc không xác định được thì
            # bỏ qua kèm lý do — KHÔNG fail cả đợt khi một locale vắng mặt
            # (khác req.locale cứng ở dưới).
            if locales is not None and loc not in locales:
                result.skipped.append({"path": item["full"],
                                       "reason": "locale %s không phải %s"
                                                 % (loc or "không xác định", wanted_txt)})
                continue
            if req.locale not in (None, "all") and loc != req.locale:
                continue
            outcome = _outcome_for(table, rec, item["full"], loc, entries)
            outcome.sha256 = file_sha
            result.files.append(outcome)
            result.total_entries += len(outcome.kv)
            result.generated_keys.extend(
                e.key for e in entries if e.key_origin in ("generated", "rowindex"))
    if _exp_total:
        _progress("export", _exp_total, _exp_total)
    if req.locale not in (None, "all"):
        wanted = req.locale
        have = all_locales
        if wanted not in have:
            result.errors.append({"path": "", "code": "E_LOCALE_NOT_FOUND",
                                  "message": "game không có locale %s; có sẵn: %s"
                                             % (wanted, ", ".join(sorted(h or "?" for h in have)) or "không có")})
            result.ok = False
            result.decision = "none"
            result.report = _build_report(req, result, logs, t0)
            return result
    if not result.files:
        result.decision = "none"
        result.report = _build_report(req, result, logs, t0)
        return result

    # IR: chuẩn hóa toàn bộ bảng (giữ key/value/locale/status/source).
    result.ir = from_tables(
        *tables,
        producer=Provenance(plugin_id="extraction", plugin_version=VERSION,
                            method="sdk.extraction.run_extraction",
                            source=SourcePath(path=str(req.source))))
    # Bỏ qua khỏi danh sách "skipped" các ứng viên bị loại của một file ĐÃ
    # trích xuất — file đó không bị bỏ qua, chỉ có plugin thua trọng tài.
    exported_paths = {f.path for f in result.files}
    result.skipped = [s for s in result.skipped if s["path"] not in exported_paths]
    if ask_groups:
        # Có nhóm người dùng bỏ qua: đã trích phần đã chọn, còn nhóm này
        # chờ chọn (ghi vào candidates để report/CLI hiển thị).
        result.candidates = ask_groups
    result.decision = "done"
    result.ok = True
    result.elapsed_s = time.monotonic() - t0
    _export(req, result, source_hash, logs)
    result.report = _build_report(req, result, logs, t0)
    return result


def _arbitrate_groups(groups: Dict[Any, List[Dict[str, Any]]], referenced: set,
                      locale_table: List[Dict[str, Any]], log) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Trọng tài theo từng nhóm locale (Phụ lục C).

    - Nhóm chỉ có MỘT ứng viên: tự chọn nếu điểm >= ask_min (0.55) — hỏi
      là vô nghĩa khi không có gì để chọn; dưới ngưỡng thì bỏ qua kèm lý do.
    - Nhóm nhiều ứng viên: arbitrate() đầy đủ (auto >= 0.80, ask, none).
    Trả (chosen, ask_groups, rejected)."""
    chosen: List[Dict[str, Any]] = []
    ask_groups: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    for key, items in sorted(groups.items(), key=lambda kv: str(kv[0])):
        code = key if isinstance(key, str) else None
        if len(items) == 1:
            item = items[0]
            cont = _contender(item, referenced, locale_table)
            s = arb.score(cont.signals, arb.Weights())
            if s >= arb.DEFAULT_ASK_MIN - arb.EPS:
                item["group_locale"] = code
                chosen.append(item)
                log("chọn %s cho locale %s (%s, %.2f; chỉ có một ứng viên)"
                    % (item["full"], code or "?", item["plugin"].plugin_id, s))
            else:
                rejected.append({"path": item["full"],
                                 "reason": "điểm %.2f dưới ngưỡng %.2f"
                                           % (s, arb.DEFAULT_ASK_MIN)})
                log("bỏ qua %s: điểm %.2f dưới ngưỡng %.2f"
                    % (item["full"], s, arb.DEFAULT_ASK_MIN))
            continue
        contenders = [_contender(it, referenced, locale_table) for it in items]
        decision = arb.arbitrate(contenders)
        if decision.status == arb.AUTO:
            pick = next(it for it in items
                        if it["plugin"].plugin_id == decision.chosen.adapter_id
                        and it["full"] == decision.chosen.path)
            pick["group_locale"] = code
            chosen.append(pick)
            log("chọn %s cho locale %s (%s, %.2f)"
                % (pick["full"], code or "?", pick["plugin"].plugin_id, decision.chosen.score))
            for r in decision.rejected:
                rejected.append({"path": r.path, "reason": r.reason})
        elif decision.status == arb.ASK:
            if len(decision.options) == 1:
                # Chỉ có một lựa chọn đạt ngưỡng: không có gì để hỏi, tự chọn.
                only = decision.options[0]
                pick = next(it for it in items
                            if it["plugin"].plugin_id == only.adapter_id
                            and it["full"] == only.path)
                pick["group_locale"] = code
                chosen.append(pick)
                log("chọn %s cho locale %s (%s, %.2f; chỉ có một lựa chọn đạt ngưỡng)"
                    % (pick["full"], code or "?", pick["plugin"].plugin_id, only.score))
                for r in decision.rejected:
                    rejected.append({"path": r.path, "reason": r.reason})
                continue
            ask_groups.append({"locale": code,
                               "options": [{"plugin": r.adapter_id, "path": r.path,
                                            "score": round(r.score, 2),
                                            "reasons": r.reasons} for r in decision.options],
                               "reason": decision.reason_vi})
        else:
            log("locale %s: %s" % (code or "?", decision.reason_vi))
            for r in decision.rejected:
                rejected.append({"path": r.path, "reason": r.reason})
    return chosen, ask_groups, rejected


def _contender(item: Dict[str, Any], referenced: set,
               locale_table: List[Dict[str, Any]]) -> arb.Contender:
    """Tín hiệu chấm điểm cho một ứng viên (Phụ lục C)."""
    rec = item["plugin"]
    plugin = rec.instance
    head = item.get("head", b"")
    low = item["full"].lower()
    magic = 0.0
    for fn in _MAGIC_FOR_PLUGIN.get(rec.plugin_id, ()):
        try:
            if fn(head):
                magic = 1.0
                break
        except Exception:
            pass
    # Độ tin cậy nội dung của chính plugin (_sniff) cũng là tín hiệu magic:
    # CSV/XML/... không có magic byte cố định nhưng nội dung vẫn nhận ra được.
    sniff = getattr(plugin, "_sniff", None)
    if callable(sniff):
        try:
            magic = max(magic, float(sniff(head, item["full"]) or 0.0))
        except Exception:
            pass
    exts = (rec.manifest.match or {}).get("extensions", [])
    if item.get("via_sniff"):
        # File không có đuôi khớp nhưng định dạng đã xác định bằng nội dung
        # (sniff >= 0.7): tính như đã khớp định dạng — đuôi file không phải
        # là bằng chứng duy nhất về định dạng (đặc biệt với TextAsset Unity
        # không có đuôi). Sniff 0.5-0.7 chỉ tính một nửa.
        sniff_v = float(item.get("sniff", 0.0))
        ext_match = 1.0 if sniff_v >= 0.7 else 0.5
    else:
        ext_match = 1.0 if any(low.endswith(e.lower()) for e in exts) else 0.0
    name_signal = 1.0 if _NAME_SIGNAL_RE.search(item["full"]) else 0.0
    code, conf, _ev = disc.detect_locale(item["full"], locale_table)
    locale_ev = conf if code else 0.0
    manifest_ref = 1.0 if (item["full"] in referenced
                           or item["candidate"].path in referenced) else 0.0
    probe = item.get("probe", {})
    content_fit = 1.0 if probe.get("entries", 0) > 0 else 0.5
    signals = arb.Signals(magic_match=magic, ext_match=ext_match, name_signal=name_signal,
                          schema_fit=float(item["candidate"].score), locale_evidence=locale_ev,
                          manifest_ref=manifest_ref, content_lang_fit=content_fit,
                          kb_agreement=0.0)
    reasons = list(item["candidate"].reasons or [])
    if manifest_ref:
        reasons.append("được index/catalog tham chiếu")
    if name_signal:
        reasons.append("tên/đường dẫn gợi ý file ngôn ngữ")
    return arb.Contender(adapter_id=rec.plugin_id, path=item["full"], signals=signals,
                         maturity=rec.maturity_effective, priority=rec.manifest.priority,
                         reasons=reasons)


def _normalize_table(table: LocTable, rec, full_path: str) -> None:
    """Ghi chú phân loại vào bảng: sửa được/chỉ đọc, verified/raw_scan."""
    editable = bool(rec.manifest.can_write)
    source_kind = "raw_scan" if rec.plugin_id == "generic_strings" else "verified"
    hint = dict(table.schema_hint or {})
    hint["pb3"] = {"editable": editable, "source_kind": source_kind,
                   "source": full_path, "plugin": rec.plugin_id,
                   "version": rec.manifest.version}
    table.schema_hint = hint
    if source_kind == "raw_scan":
        for e in table.entries:
            if not any("dò chuỗi thô" in n for n in e.notes):
                e.notes.append("dò chuỗi thô, chưa xác minh — có thể không phải chuỗi localization")


def _outcome_for(table: LocTable, rec, full_path: str, locale: Optional[str],
                 entries: List[LocEntry]) -> FileOutcome:
    """Một locale trong một file nguồn -> FileOutcome (kv xuất, đếm status)."""
    counts = {"ok": 0, "empty": 0, "conflicts": 0, "unparsed": 0}
    kv: Dict[str, str] = {}
    warnings: List[str] = []
    for e in entries:
        if e.status == "unparsed":
            counts["unparsed"] += 1
            warnings.append("bỏ qua entry chưa parse: %s" % e.key)
            continue
        if e.status == "conflict":
            counts["conflicts"] += 1
            continue            # chỉ ghi bản ghi đầu (đã có trong kv)
        if e.status == "empty":
            counts["empty"] += 1
        else:
            counts["ok"] += 1
        if e.key not in kv:
            kv[e.key] = e.value
    for w in table.warnings:
        if isinstance(w, dict) and w.get("msg"):
            warnings.append(str(w["msg"]))
        elif isinstance(w, dict) and w.get("code"):
            warnings.append(str(w["code"]))
    return FileOutcome(full_path, rec.plugin_id, locale,
                       entries=len(entries), ok=counts["ok"], empty=counts["empty"],
                       conflicts=counts["conflicts"], unparsed=counts["unparsed"],
                       warnings=sorted(set(warnings)),
                       editable=bool(rec.manifest.can_write),
                       source_kind="raw_scan" if rec.plugin_id == "generic_strings" else "verified",
                       kv=kv, source=_source_of(full_path))


# ------------------------------------------------------------------ export
def _unique_filename(base: str, stem: str, used: set) -> str:
    """Tên file xuất không trùng: trùng thì chèn stem nguồn vào giữa
    (English.json -> English.strings.json), rồi đánh số nếu vẫn trùng."""
    if base not in used:
        return base
    name, ext = os.path.splitext(base)
    cand = "%s.%s%s" % (name, stem, ext)
    n = 2
    while cand in used:
        cand = "%s.%s.%d%s" % (name, stem, n, ext)
        n += 1
    return cand


def _source_stem(path: str) -> str:
    """Stem của tên file; với đường dẫn ảo (bundle::asset::TextAsset)
    lấy phần sau "::" cuối — tên TextAsset — chứ không phải tên bundle."""
    leaf = path.split("::")[-1]
    return os.path.splitext(os.path.basename(leaf))[0] or "strings"


def _export(req: ExtractionRequest, result: ExtractionResult, source_hash: str,
            logs: List[str]) -> None:
    """Ghi JSON key-value + report (trừ dry-run / không có out_dir)."""
    if req.out_dir is None or req.dry_run:
        return
    import os as _os
    _os.makedirs(req.out_dir, exist_ok=True)
    game = _game_name(req.source)
    checkpoint_files: Dict[str, Any] = {}
    used_names: set = set()
    for outcome in result.files:
        if not outcome.kv:
            continue
        if outcome.locale:
            fname = disc.export_filename(outcome.locale)
        else:
            fname = _sanitize_segment(_source_stem(outcome.path)) + ".json"
        # Hai nguồn cùng locale -> tên file trùng: thêm stem nguồn vào tên
        # (English.json + English.strings.json), không ghi đè im lặng.
        fname = _unique_filename(fname, _sanitize_segment(
            _source_stem(outcome.path)), used_names)
        used_names.add(fname)
        data = exp.export_key_value_json(outcome.kv)
        check = exp.verify_export_bytes(data, outcome.kv)
        if not check["ok"]:
            result.errors.append({"path": outcome.path, "code": "E_VERIFY_FAILED",
                                  "message": "verify file xuất lỗi: " + "; ".join(check["problems"][:3])})
            continue
        dest = os.path.join(req.out_dir, fname)
        try:
            with open(dest, "wb") as fh:
                fh.write(data)
        except OSError as exc:
            result.errors.append({"path": outcome.path, "code": "E_IO",
                                  "message": "không ghi được %s: %s" % (fname, exc)})
            continue
        outcome.exported = fname
        ent = checkpoint_files.setdefault(outcome.path, {"sha256": outcome.sha256,
                                                         "locales": {}, "exported": {}})
        ent["locales"][outcome.locale or ""] = dict(outcome.kv)
        ent["exported"][outcome.locale or ""] = fname
    _save_checkpoint(req, source_hash, checkpoint_files)
    _write_reports(req, result, logs)


def _write_reports(req: ExtractionRequest, result: ExtractionResult,
                   logs: List[str]) -> None:
    """Ghi extraction_report.txt/.json vào out_dir (kể cả khi có lỗi).

    LƯU Ý (lỗi en.json 2026-11-16): trước đây hàm này gọi
    `_build_report(req, result, [], 0.0)` — truyền logs RỖNG — nên file
    report trên đĩa LUÔN có `"log": []` dù `result.report` trong bộ nhớ
    có log thật. Nay truyền `logs` thật qua `_export`."""
    if req.out_dir is None:
        return
    report = _build_report(req, result, logs, 0.0)
    result.report = report
    try:
        with open(os.path.join(req.out_dir, REPORT_TXT), "w", encoding="utf-8") as fh:
            fh.write(exp.report_text(report))
        with open(os.path.join(req.out_dir, REPORT_JSON), "wb") as fh:
            fh.write(exp.report_json(report))
    except OSError:
        pass


def _build_report(req: ExtractionRequest, result: ExtractionResult, logs: List[str],
                  t0: float) -> Dict[str, Any]:
    """Báo cáo máy đọc (đặc tả 7.6): engine, nguồn, plugin, locale, số liệu,
    lỗi/bỏ qua, key trùng/sinh, lý do chọn file, maturity, thời gian."""
    if not result.elapsed_s:
        result.elapsed_s = time.monotonic() - t0 if t0 else 0.0
    return {
        "schema": "pb3.extraction_report",
        "version": 1,
        "source": str(req.source),
        "game": _game_name(req.source),
        "engine": result.engine,
        "engine_level": result.engine_level,
        "engine_confidence": round(result.engine_confidence, 2),
        "engine_reasons": list(result.engine_reasons),
        "locale_requested": req.locale,
        "locales_requested": sorted(req.locales) if req.locales else None,
        "source_paths": list(req.source_paths) if req.source_paths else None,
        "decision": result.decision,
        "files": [{"path": f.path, "plugin": f.plugin, "locale": f.locale,
                   "source": f.source or _source_of(f.path),
                   "entries": f.entries, "ok": f.ok, "empty": f.empty,
                   "conflicts": f.conflicts, "unparsed": f.unparsed,
                   "warnings": list(f.warnings), "exported": f.exported,
                   "editable": f.editable, "source_kind": f.source_kind}
                  for f in result.files],
        "skipped": list(result.skipped),
        "errors": list(result.errors),
        "generated_keys": list(result.generated_keys),
        "total_entries": result.total_entries,
        "elapsed_s": round(result.elapsed_s, 1),
        "checkpoint_used": result.checkpoint_used,
        "candidates": list(result.candidates),
        "log": list(logs),
    }


# ------------------------------------------------------------------ checkpoint
def _checkpoint_path(req: ExtractionRequest) -> Optional[str]:
    if not req.out_dir:
        return None
    return os.path.join(req.out_dir, CHECKPOINT_FILENAME)


def _load_checkpoint(req: ExtractionRequest) -> Optional[Dict[str, Any]]:
    path = _checkpoint_path(req)
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict) or data.get("version") != CHECKPOINT_VERSION:
        return None
    return data


def _save_checkpoint(req: ExtractionRequest, source_hash: str,
                     files: Dict[str, Any]) -> None:
    path = _checkpoint_path(req)
    if not path or req.dry_run:
        return
    from core import safe_io
    blob = json.dumps({"version": CHECKPOINT_VERSION, "source_hash": source_hash,
                       "files": files}, ensure_ascii=False, indent=1).encode("utf-8")
    try:
        safe_io.atomic_write_bytes(path, blob)
    except Exception:
        pass


# ------------------------------------------------------------------ ghi ngược
def _unity_target_info(target_path: str, root_vfs, limits):
    """Xác định đơn vị Unity chứa một TextAsset ảo.

    Trả (kind, unit_vpath, entry, asset_name) hoặc None nếu không phải
    TextAsset Unity. kind "bundle": vpath::CAB::asset; "assets": vpath::asset
    (file .assets độc lập, có thể nằm trong container lồng)."""
    parts = target_path.split("::")
    if len(parts) < 2:
        return None
    nested = NestedVFS(root_vfs, limits=limits)
    # Thử file .assets độc lập trước: đơn vị = phần trước "::" cuối.
    unit_vpath = "::".join(parts[:-1])
    try:
        unit_bytes = bytes(nested.read(unit_vpath, None))
    except PB3Error:
        unit_bytes = None
    if unit_bytes is not None and unity_sdk.detect_bundle(unit_bytes) is None:
        try:
            unity_sdk.parse_serialized(unit_bytes, limits, where=unit_vpath)
            return ("assets", unit_vpath, None, parts[-1])
        except PB3Error:
            pass
    # Thử bundle: đơn vị = phần trước 2 "::" cuối, entry = phần kế cuối.
    if len(parts) >= 3:
        unit_vpath = "::".join(parts[:-2])
        try:
            unit_bytes = bytes(nested.read(unit_vpath, None))
        except PB3Error:
            return None
        if unity_sdk.detect_bundle(unit_bytes) is not None:
            return ("bundle", unit_vpath, parts[-2], parts[-1])
    return None


def apply_edits(*, edited_json: str, report_path: str, registry, limits=None,
                out_dir: Optional[str] = None, allow_write: bool = False,
                tempdir: Optional[str] = None, source: Any = None,
                progress_fn: Optional[Callable[[str, int, Optional[int]], None]] = None
                ) -> Dict[str, Any]:
    """Ghi ngược JSON đã sửa vào file nguồn (đặc tả 7.6 + 6.7).

    - edited_json: file JSON key-value đã sửa (vd English.json đã dịch).
    - report_path: extraction_report.json cùng đợt trích xuất (để tìm nguồn
      + plugin + locale của file đã xuất).
    - Dry-run mặc định: chỉ báo sẽ đổi gì; --allow-write mới ghi thật,
      versioned vào workspace/output/<GameName>/, không ghi đè nguồn.
    - Plugin/định dạng con chỉ đọc -> E_WRITE_DISABLED; entry unparsed và
      key không có trong file gốc -> bỏ qua kèm warning; key trùng chỉ ghi
      bản ghi đầu. Build xong verify lại trước khi ghi."""
    lim = limits or Limits()
    try:
        with open(report_path, "r", encoding="utf-8") as fh:
            report = json.load(fh)
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise PB3Error("E_INPUT_NOT_FOUND", "không đọc được report: %s" % type(exc).__name__,
                       where=report_path)
    try:
        with open(edited_json, "r", encoding="utf-8") as fh:
            edited = json.load(fh)
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise PB3Error("E_CORRUPT", "JSON đã sửa hỏng: %s" % type(exc).__name__, where=edited_json)
    if not isinstance(edited, dict):
        raise PB3Error("E_CORRUPT", "JSON đã sửa phải là object key-value", where=edited_json)
    base_name = os.path.basename(edited_json)
    target = None
    for f in report.get("files", []):
        if f.get("exported") == base_name:
            target = f
            break
    if target is None:
        raise PB3Error("E_INPUT_NOT_FOUND",
                       "không tìm thấy %s trong %s (cần report cùng đợt trích xuất)"
                       % (base_name, os.path.basename(report_path)),
                       hint="Chạy extract trước, rồi sửa file JSON đã xuất và ghi ngược.")
    rec = registry.get(target["plugin"])
    if rec is None or not rec.active:
        raise PB3Error("E_INPUT_NOT_FOUND", "không có plugin %s" % target["plugin"])
    if not rec.manifest.can_write:
        raise PB3Error("E_WRITE_DISABLED",
                       "plugin %s chỉ đọc (định dạng con chỉ đọc)" % target["plugin"])
    src = source if source is not None else report.get("source")
    root_vfs = disc.open_source_vfs(src, lim)
    _ap = progress_fn

    def _ap_safe(phase: str, done: int, total: Optional[int]) -> None:
        # Tiến độ không bao giờ được làm hỏng ghi ngược.
        if _ap is not None:
            try:
                _ap(phase, done, total)
            except Exception:
                pass

    inv = disc.inventory(
        root_vfs, lim,
        on_entry=lambda done, name: _ap_safe("inventory", done, None))
    backend = unity_sdk.UnityBackend("AUTO", lim)
    layers = _build_layers(root_vfs, inv, lim, backend, lambda m: None,
                           progress_fn=_ap_safe)
    vfs_for, rel = _find_layer(layers, target["path"])
    ctx = PluginContext(vfs_for, lim, tempdir=tempdir, dry_run=not allow_write).for_plugin(rec.manifest)
    base_bytes = bytes(vfs_for.read(rel, None))
    from sdk.types import Candidate
    cand = Candidate(rel, rec.plugin_id, 1.0, ["ghi ngược"])
    table = rec.instance.parse(ctx, cand)
    base_keys = {e.key for e in table.entries}
    changed = 0
    skipped_keys: List[str] = []
    new_entries: List[LocEntry] = []
    for e in table.entries:
        if e.status == "unparsed":
            new_entries.append(e)
            continue
        if e.status == "conflict":
            new_entries.append(e)      # key trùng: chỉ ghi bản ghi đầu, bản sau giữ nguyên
            continue
        # Chỉ sửa entry đúng locale của file đã xuất: JSON en sửa vào bảng
        # đa locale (CSV en/zh) mà không lọc sẽ ghi đè cả cột zh bằng
        # giá trị en. Entry không mang locale thì vẫn sửa (nó nằm trong
        # file đã xuất qua locale của bảng/nhóm).
        target_locale = target.get("locale")
        if (target_locale is not None and e.locale is not None
                and e.locale != target_locale):
            new_entries.append(e)
            continue
        if e.key in edited and e.value != str(edited[e.key]):
            new_entries.append(LocEntry(e.key, str(edited[e.key]), e.locale, e.key_origin,
                                        e.status, e.source, list(e.notes)))
            changed += 1
        else:
            new_entries.append(e)
    for key in edited:
        if key not in base_keys:
            skipped_keys.append(key)
    new_table = LocTable(new_entries, table.locale, table.adapter_id,
                         dict(table.schema_hint), dict(table.stats),
                         list(table.conflicts), list(table.warnings))
    new_bytes = rec.instance.build(ctx, new_table, base_bytes)
    verify = rec.instance.verify(ctx, base_bytes, new_bytes)
    if not verify.get("ok"):
        raise PB3Error("E_VERIFY_FAILED", "verify sau khi ghi lỗi: %s" % verify.get("reason", "?"))
    # TextAsset Unity: ghi ngược = patch script mới vào bundle/.assets
    # (sdk.unity.apply_patch, C05) rồi xuất chính đơn vị đó (6.6) — không
    # ghi file script rời. File thường: xuất file đã build lại.
    unity_info = _unity_target_info(target["path"], root_vfs, lim)
    game = _game_name(src)
    result: Dict[str, Any] = {"source": target["path"], "plugin": target["plugin"],
                              "changed": changed, "skipped_keys": skipped_keys,
                              "size_before": len(base_bytes), "size_after": len(new_bytes),
                              "written": None, "dry_run": not allow_write}
    if unity_info is not None:
        kind, unit_vpath, entry, asset_name = unity_info
        nested = NestedVFS(root_vfs, limits=lim)
        unit_bytes = bytes(nested.read(unit_vpath, None))
        if kind == "bundle":
            bundle = unity_sdk.parse_bundle(unit_bytes, backend, lim, where=unit_vpath)
            sf = unity_sdk.parse_serialized(bundle.get_entry(entry), lim, where=entry)
        else:
            sf = unity_sdk.parse_serialized(unit_bytes, lim, where=unit_vpath)
        pid = None
        for _pid, _name, _script in sf.text_assets():
            if _sanitize_segment(_name) == asset_name:
                pid = _pid
                break
        if pid is None:
            raise PB3Error("E_INPUT_NOT_FOUND",
                           "không tìm thấy TextAsset %r trong %s" % (asset_name, unit_vpath))
        try:
            old_script = base_bytes.decode("utf-8")
            new_script = new_bytes.decode("utf-8")
        except UnicodeDecodeError:
            raise PB3Error("E_CORRUPT", "script TextAsset không phải UTF-8",
                           where=target["path"])
        patch = unity_sdk.apply_patch(unity_sdk.PatchRequest(
            data=unit_bytes, kind=kind, entry=entry, path_id=pid,
            old_text=old_script, new_text=new_script,
            write_mode="PATCH_INPLACE", allow_escalate=True,
            backend=backend, limits=lim, temp_dir=tempdir,
            from_container="::" in unit_vpath))
        unit_name = unit_vpath.split("::")[-1].split("/")[-1]
        rel_out = "%s/%s" % (game, unit_name)
        result.update({"unit": patch.unit, "write_mode_used": patch.write_mode_used,
                       "size_before": len(unit_bytes),
                       "size_after": len(patch.output_bytes),
                       "verify": patch.verify_details})
        if not allow_write:
            return result
        dest = unity_sdk.write_patch_output(out_dir, rel_out, patch.output_bytes)
        result["written"] = str(dest)
        return result
    rel_out = "%s/%s" % (game, os.path.basename(target["path"]).replace("::", "_"))
    if not allow_write:
        return result
    dest = unity_sdk.write_patch_output(out_dir, rel_out, new_bytes)
    result["written"] = str(dest)
    return result
