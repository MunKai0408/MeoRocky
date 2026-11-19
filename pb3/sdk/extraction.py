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
import tempfile
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
from sdk import text_data
from sdk import unity as unity_sdk
from sdk.api import LocaleRequest, PluginContext
from sdk.containers import NestedVFS, open_vfs
from sdk.ir import IRDocument, from_tables
from sdk.types import LocEntry, LocTable, SourceRef
from sdk.vfs import MemoryVFS, SingleFileVFS

MAX_PROBE_CANDIDATES = 400  # tăng từ 200 (đợt EK2 2026-11-16: 515 ứng viên thô,
# file Unity lớn nhất chưa từng được probe vì xếp sau theo thứ tự chữ cái).
# Thứ tự probe ưu tiên file Unity (TextAsset/MonoBehaviour/StringTable ảo)
# trước (xem _probe_priority) nên file text game luôn được probe dù tổng
# ứng viên vượt giới hạn.
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


def _probe_priority(full: str) -> Tuple[int, str]:
    """Thứ tự probe: file Unity trước, rồi tên gợi ý ngôn ngữ, rồi còn lại.

    (tier, đường_dẫn): tier 0 = file ảo từ Unity (TextAsset/MonoBehaviour/
    StringTable: "...assets::..." / "...bundle::..."), tier 1 = file Unity
    trực tiếp, tier 2 = tên/đường dẫn gợi ý file ngôn ngữ, tier 3 = còn lại.
    Trong cùng tier xếp theo chữ cái (ổn định, dễ đoán). Trước đây probe
    xếp thuần theo chữ cái nên file Unity lớn (vd sharedassets0.assets của
    EK2) nằm sau hàng trăm file config nhỏ và rơi ngoài giới hạn 200."""
    low = full.lower()
    if "::" in full and (".assets::" in low or ".bundle::" in low
                         or ".unity3d::" in low):
        tier = 0
    elif low.endswith(_UNITY_SOURCE_EXTS):
        tier = 1
    elif _NAME_SIGNAL_RE.search(full):
        tier = 2
    else:
        tier = 3
    return tier, full


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
    merge_one_file: bool = False     # Gộp thành 1 file mỗi ngôn ngữ (Joseph 23:35): khi True, các
    # outcome ngôn ngữ thuộc tập lọc được gộp thành đúng 1 FileOutcome mỗi
    # locale (xuất en.json/zh-CN.json/zh-TW.json), giữ thứ tự nguồn + thứ
    # tự key gốc, trùng key giữ bản đầu + liệt kê xung đột vào report/sidecar,
    # provenance từng key giữ cho (E). BUG#2: khi True, output CHỈ chứa các
    # file locale đã gộp — file config/plist (locale None), file rác,
    # provenance/checkpoint/report/conflicts đều KHÔNG ghi ra đĩa nữa.
    # Mặc định False để mọi hành vi cũ/test/harness cũ không đổi; bản web
    # bật bằng checkbox (mặc định tick) ở tab Trích xuất.


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
    # BUG#2: nguồn gốc nội dung của outcome — language_source (bảng ngôn
    # ngữ có cấu trúc) | string_table (StringTable gộp nhóm) | generic_flat
    # (quét phẳng MonoBehaviour / dò chuỗi thô, key vị trí .s/.p) |
    # monobehaviour_lang (MonoBehaviour generic ĐÃ TÁCH NGÔN NGỮ thành
    # công: 1 file mỗi locale, key ổn định .p<path_id> — xem
    # merge-mb-generic) | text_asset (file locale có cấu trúc khác:
    # TextAsset, CSV, .po...) | other (không phải text localizable:
    # plist, abi, config...). Merge một file chỉ gộp language_source +
    # string_table + text_asset, CỘNG monobehaviour_lang khi game không
    # có nguồn có cấu trúc nào (I2/ST thắng trước).
    # Mặc định "text_asset" để outcome tổng hợp trong test + plugin chưa
    # phân loại giữ hành vi cũ; pipeline luôn gắn rõ ở lúc tạo outcome.
    origin_kind: str = "text_asset"
    kv: Dict[str, str] = field(default_factory=dict)
    sha256: str = ""                   # sha256 file nguồn (để khóa checkpoint)
    merged: bool = False               # True = outcome gộp 1 file (merge_one_file): kv là hợp
    # của nhiều outcome cùng locale; merged_from liệt kê các file tách
    # per-unit đã gộp, merge_sources cho biết từng key lấy từ nguồn nào (cho (E)).
    merged_from: List[str] = field(default_factory=list)
    merge_sources: Dict[str, str] = field(default_factory=dict)


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
    merged_files: List[Dict[str, Any]] = field(default_factory=list)
    # BUG#2: bản kê theo từng cột của bảng ngôn ngữ (mLanguages) — mỗi mục
    # {unit, code, keys, empty, status}: status exported = cột đã xuất
    # thành file locale; filtered = cột bị lọc theo yêu cầu; not_exported =
    # cột có trong game nhưng không xuất được (kèm lý do ở skipped/log).
    # Để không còn cột "mất tích" không dấu vết.
    language_columns: List[Dict[str, Any]] = field(default_factory=list)
    # Thống kê gộp 1 file (merge_one_file), mỗi locale một mục: {locale,
    # file, keys, entries_before, sources, duplicates_identical, conflicts}
    # — conflicts liệt kê key trùng KHÁC giá trị (giữ bản đầu) kèm nguồn
    # từng bản; xem merge_language_outcomes().
    # Shared index {collection: {key: m_Id}} từ mọi SharedTableData gặp lúc
    # trích xuất — ghi vào sidecar provenance + trả trong response extract
    # để (E) phân loại key StringTable ở đơn vị khác; không có thì {}.
    shared_index: Dict[str, Dict[str, int]] = field(default_factory=dict)


# File MonoBehaviour generic đã tách ngôn ngữ thành công (xem _expand_unity
# + merge-mb-generic): 1 file ảo mỗi locale, key ổn định (.p<path_id>,
# không hậu tố #N phụ thuộc thứ tự). Định nghĩa ở đây (trước _expand_unity)
# vì _expand_unity gắn nhãn này lúc dựng lớp virtual.
MB_LANG_ORIGIN = "monobehaviour_lang"


# ------------------------------------------------------------------ lớp VFS
class _Layer:
    """Một lớp VFS để quét ứng viên: (tiền tố vpath, vfs)."""

    def __init__(self, prefix: str, vfs, kind: str,
                 origin_map: Optional[Dict[str, str]] = None):
        self.prefix = prefix
        self.vfs = vfs
        self.kind = kind
        # BUG#2: origin_kind của từng file ảo trong lớp (chỉ lớp virtual
        # mới có): {vpath: language_source | string_table | generic_flat |
        # monobehaviour_lang | text_asset} — để run_extraction gắn thẻ
        # nguồn cho FileOutcome.
        self.origin_map: Dict[str, str] = dict(origin_map or {})


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


def _expand_serialized(sf, assets_name: str, vpath: str):
    """TextAsset + text MonoBehaviour của 1 SerializedFile -> (out, mb, notes,
    n_text_assets, n_mb, n_mb_strings). Dùng chung cho _expand_unity (bundle
    đã nạp trọn vào RAM) và đường streaming (từng entry giải nén một — file
    .unity3d lớn không ôm cả payload)."""
    out: Dict[str, bytes] = {}
    notes: List[str] = []
    mb: Dict[str, Any] = {"string_tables": [], "shared_tables": [], "generic": [],
                          "language_sources": [], "text_data_lists": []}
    tas = sf.text_assets()
    n_text_assets = len(tas)
    for _pid, name, script in tas:
        safe = _sanitize_segment(name)
        base = vpath + ("::" + assets_name if assets_name else "")
        out[base + "::" + safe] = script.encode("utf-8")
    warns: List[str] = []
    n_mb = 0
    n_mb_strings = 0
    for item in sf.monobehaviour_texts(warnings=warns):
        n_mb += 1
        kind = item.get("kind")
        if kind == "string_table":
            mb["string_tables"].append(item)
        elif kind == "shared_table":
            mb["shared_tables"].append(item)
        elif kind == "locale":
            pass  # đối tượng Locale: chỉ có mã/tên locale, không phải text dịch
        elif kind == "language_source":
            # Bảng ngôn ngữ có cấu trúc: KHÔNG đi qua đường quét chuỗi phẳng
            # (đường đó bỏ chuỗi rỗng -> lệch cột). Đếm chuỗi = số term.
            mb["language_sources"].append((assets_name, item))
            n_mb_strings += len(item.get("terms") or [])
        elif kind == "text_data_list":
            # Bảng text dạng danh sách (sdk/text_data): KHÔNG đi qua
            # đường quét chuỗi phẳng (mất cấu trúc cột/key). Đếm chuỗi =
            # số giá trị (entry × cột).
            mb["text_data_lists"].append((assets_name, item))
            n_mb_strings += sum(len(e.get("values") or [])
                                for e in item.get("entries") or [])
        else:
            mb["generic"].append((assets_name, item))
            fields = item.get("fields")
            strings = item.get("strings")
            n_mb_strings += len(fields) if isinstance(fields, dict) else len(strings or [])
    notes.extend(warns)
    return out, mb, notes, n_text_assets, n_mb, n_mb_strings


def _expand_unity(data: bytes, vpath: str, backend, limits) -> Tuple[Dict[str, bytes], List[str], Dict[str, Any]]:
    """TextAsset + text MonoBehaviour trong một entry Unity -> (vpath ảo, notes, mb).

    Bundle: mở từng entry .assets bên trong rồi đọc TextAsset + MonoBehaviour;
    file .assets độc lập: đọc thẳng. TextAsset hỏng bị bỏ qua (kèm ghi chú
    trong warnings của SerializedFile, không làm hỏng cả file). MonoBehaviour
    hỏng cũng vậy (ghi chú trong notes).

    mb = {"string_tables": [item], "shared_tables": [item],
          "generic": [(assets_name, item)],
          "language_sources": [(assets_name, item)]} — item theo
    SerializedFile.monobehaviour_texts(): StringTable (entries {m_Id: chuỗi},
    locale từ m_Code), SharedTableData (entries {m_Id: key}), generic
    (fields {đường_dẫn: chuỗi} nếu có type tree, strings [...] nếu không),
    language_source (languages + terms theo chỉ số cột, giữ chuỗi rỗng).
    StringTable/SharedTableData join theo m_Id ở _build_layers (chúng có thể
    nằm ở 2 bundle khác nhau trong cùng IPA).

    KHÔNG BAO GIỜ bỏ qua lặng lẽ (lỗi en.json 2026-11-16: bundle
    localization biến mất khỏi report vì mọi đường lỗi đều `return {}`
    im lặng): mọi vấn đề đều ghi vào `notes` — parse lỗi (kèm mã),
    entry không phải SerializedFile, bundle cho 0 TextAsset — để caller
    ghi vào skipped/log thay vì để bundle biến mất."""
    out: Dict[str, bytes] = {}
    notes: List[str] = []
    mb: Dict[str, Any] = {"string_tables": [], "shared_tables": [], "generic": [],
                          "language_sources": [], "text_data_lists": []}
    assets: List[Tuple[str, Any]] = []
    det = unity_sdk.detect_bundle(data)
    if det is not None:
        try:
            bundle = unity_sdk.parse_bundle(data, backend, limits, where=vpath)
        except PB3Error as exc:
            notes.append("parse bundle lỗi [%s]: %s"
                         % (exc.code, exc.message_vi or exc.detail))
            return out, notes, mb
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
            return out, notes, mb
    n_text_assets = 0
    n_mb = 0
    n_mb_strings = 0
    for assets_name, sf in assets:
        got, mb_i, notes_i, n_ta, n_m, n_s = _expand_serialized(sf, assets_name, vpath)
        out.update(got)
        for k in ("string_tables", "shared_tables", "generic", "language_sources",
                  "text_data_lists"):
            mb[k].extend(mb_i[k])
        notes.extend(notes_i)
        n_text_assets += n_ta
        n_mb += n_m
        n_mb_strings += n_s
    if not out and not mb["string_tables"] and not mb["generic"] \
            and not mb["language_sources"] and not mb["text_data_lists"]:
        notes.append("không có TextAsset nào (%d SerializedFile đã mở, %d TextAsset, "
                     "%d MonoBehaviour, %d chuỗi)"
                     % (len(assets), n_text_assets, n_mb, n_mb_strings))
    return out, notes, mb


def _expand_unity_stream(path: str, vpath: str, backend, limits):
    """Đọc bundle UnityFS độc lập LỚN theo từng entry (streaming) -> (vpath ảo,
    notes, mb) — cùng định dạng trả về như _expand_unity.

    Dùng khi file vượt giới hạn entry (vd data.unity3d 590MB): parse_bundle
    ôm cả payload ~996MB vào RAM (chết tab), còn đây giải nén từng block,
    giữ cửa sổ trượt, parse từng entry .assets rồi bỏ ngay (RAM đỉnh ~2×
    entry lớn nhất; đo trên data.unity3d: 236 entry/3,8s, đỉnh ~610MB hệ
    thống gồm cả parse SerializedFile). Entry không phải SerializedFile /
    parse lỗi ghi notes như _expand_unity, không làm hỏng cả file."""
    out: Dict[str, bytes] = {}
    notes: List[str] = []
    mb: Dict[str, Any] = {"string_tables": [], "shared_tables": [], "generic": [],
                          "language_sources": [], "text_data_lists": []}
    n_sf = 0
    n_text_assets = 0
    n_mb = 0
    n_mb_strings = 0
    try:
        stream = unity_sdk.stream_bundle_entries(path, backend, limits, where=vpath)
    except PB3Error as exc:
        notes.append("parse bundle (streaming) lỗi [%s]: %s"
                     % (exc.code, exc.message_vi or exc.detail))
        return out, notes, mb
    for entry_name, entry_data in stream:
        try:
            sf = unity_sdk.parse_serialized(entry_data, limits, where=entry_name)
        except PB3Error as exc:
            notes.append("entry %s không phải SerializedFile [%s]: %s"
                         % (entry_name, exc.code, exc.message_vi or exc.detail))
            continue
        n_sf += 1
        got, mb_i, notes_i, n_ta, n_m, n_s = _expand_serialized(sf, entry_name, vpath)
        out.update(got)
        for k in ("string_tables", "shared_tables", "generic", "language_sources",
                  "text_data_lists"):
            mb[k].extend(mb_i[k])
        notes.extend(notes_i)
        n_text_assets += n_ta
        n_mb += n_m
        n_mb_strings += n_s
    if not out and not mb["string_tables"] and not mb["generic"] \
            and not mb["language_sources"] and not mb["text_data_lists"]:
        notes.append("không có TextAsset nào (%d SerializedFile đã mở, %d TextAsset, "
                     "%d MonoBehaviour, %d chuỗi)"
                     % (n_sf, n_text_assets, n_mb, n_mb_strings))
    return out, notes, mb


def _expand_unity_stream_entry(nested, entry_path, backend, limits):
    """Mở 1 entry Unity lớn TRONG container bằng spool-to-temp + streaming.

    (caps-po) Entry Unity > stream_entry_bytes (vd Tom data.unity3d 78 MB
    trong IPA) không nạp trọn vào RAM rồi parse_bundle (payload giải nén
    171 MB sẽ thành nhiều bản copy lớn cùng lúc) mà spool từng chunk 1 MB
    qua open_stream ra temp file rồi _expand_unity_stream
    (stream_bundle_entries, cửa sổ trượt). Entry trong IPA thường DEFLATED
    nên slice ngẫu nhiên phải giải nén lại từ đầu mỗi lần (O(n^2)) —
    spool tuyến tính 1 lần là đường đúng. Temp file xóa trong finally
    (TMPDIR của hệ thống). VFS không có open_stream (E_NOT_IMPLEMENTED)
    thì fallback về đọc trọn + _expand_unity (đường cũ). Trả cùng định
    dạng như _expand_unity.
    """
    try:
        stream = nested.open_stream(entry_path)
    except PB3Error as exc:
        if exc.code == "E_NOT_IMPLEMENTED":
            data = bytes(nested.read(entry_path, None))
            return _expand_unity(data, entry_path, backend, limits)
        raise
    fd, tmp_path = tempfile.mkstemp(prefix="pb3-unity-", suffix=".bundle")
    try:
        with os.fdopen(fd, "wb") as fh:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                fh.write(chunk)
        try:
            stream.close()
        except Exception:  # noqa: BLE001 - đóng stream lỗi không được hỏng trích xuất
            pass
        return _expand_unity_stream(tmp_path, entry_path, backend, limits)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _mb_string_is_noise(s: str) -> bool:
    """Chuỗi MonoBehaviour nào KHÔNG phải text game (để lọc khỏi JSON xuất).

    Bỏ: quá ngắn, GUID 32 hex (có/không có gạch nối), "[None]" (tham chiếu
    null của Unity), số phiên bản (1.1.0), đường dẫn asset
    (Assets/.../Plugins/.../đuôi .png/.mat/.prefab/...), chuỗi không có chữ
    cái nào (dãy số/ký tự của font, vd "32 - 126, 160, 8203"). Bỏ thêm rác
    nội bộ của Unity [ĐÃ ĐO THẬT trên resources.assets của 3 game thật]:
    tên kiểu kèm assembly ("UnityEngine.…", "Unity.…"), tên class USS của
    UI Toolkit (toàn chuỗi là "unity-…"), namespace chấm 3+ đoạn
    ("MGS.Debugging.UILogHandler"), tài liệu UXML, đường dẫn .uxml/.uss.
    KHÔNG bỏ tên CamelCase trần ("PromoCodeUI", "Aniara"): đo được
    1971/396/566 chuỗi như vậy, lẫn cả text thật — lọc sẽ mất text. Giữ mọi
    chuỗi có chữ cái còn lại (kể cả key kiểu "sim_pause", mã, câu)."""
    if not s or len(s) < 2:
        return True
    if s == "[None]" or _MB_GUID_RE.match(s) or _MB_GUID_DASHED_RE.match(s) \
            or _MB_VERSION_RE.match(s):
        return True
    if s.startswith("Assets/") or "/Plugins/" in s:
        return True
    low = s.lower()
    if any(low.endswith(e) for e in _MB_NOISE_EXTS):
        return True
    if "UnityEngine." in s or _MB_UNITY_NS_RE.search(s):
        return True
    if _MB_USS_CLASS_RE.match(s):
        return True
    if _MB_DOTTED_NS_RE.match(s):
        return True
    stripped = s.strip()
    if (stripped.startswith("<UXML") or stripped.startswith("<ui:")
            or "xmlns:ui=" in stripped):
        return True
    return not any(ch.isalpha() for ch in s)


_MB_GUID_RE = re.compile(r"^[0-9a-fA-F]{32}$")
_MB_GUID_DASHED_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                                r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_MB_VERSION_RE = re.compile(r"^\d+(\.\d+)+$")
_MB_NOISE_EXTS = (".png", ".jpg", ".jpeg", ".mat", ".prefab", ".ttf", ".otf",
                  ".shader", ".asset", ".controller", ".anim", ".fbx", ".exr",
                  ".wav", ".mp3", ".ogg", ".dll", ".cs", ".uxml", ".uss")
_MB_UNITY_NS_RE = re.compile(r"\bUnity\.[A-Z]")
_MB_USS_CLASS_RE = re.compile(r"^\.?unity-[a-z0-9_-]+$")
_MB_DOTTED_NS_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]{1,}(\.[A-Z][A-Za-z0-9]+){2,}$")


_TERM_LIKE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_/.\-]*$")


def _is_term_like(s: str) -> bool:
    """Chuỗi có phải key term (định danh) không — vd 'Achievement_HisWinningness',
    'Buttons/Play'. Dùng làm ranh giới nhóm term trong object bảng ngôn ngữ
    (1 term + N bản dịch lặp lại theo thứ tự ngôn ngữ cố định)."""
    return (isinstance(s, str) and len(s) >= 3
            and ("_" in s or "/" in s) and _TERM_LIKE_RE.match(s) is not None)


def _split_term_chunks(pairs):
    """Chia list (suffix, value) đã giữ của 1 object thành nhóm term.

    Nhóm = [term, bản dịch 1..N]; term là chuỗi term-like đầu nhóm. Nhóm
    đầu không có term (object bắt đầu giữa chừng) thì has_term=False.
    Trả [(has_term, [(suffix, value), ...]), ...]."""
    chunks = []
    cur = None
    cur_has_term = False
    for pair in pairs:
        v = pair[1]
        if _is_term_like(v):
            if cur:
                chunks.append((cur_has_term, cur))
            cur = [pair]
            cur_has_term = True
        else:
            if cur is None:
                cur = [pair]
                cur_has_term = False
            else:
                cur.append(pair)
    if cur:
        chunks.append((cur_has_term, cur))
    return chunks


def _resplit_long_chunks(chunks, modal_len):
    """Tách lại chunk dài hơn modal_len (term bị bỏ lọt do không giống
    định danh, vd 'Tutorial1', 'PressToContinue' — CamelCase không có
    '_' hay '/').

    Chỉ tách tại vị trí là bội số của modal_len mà chuỗi ở đó là định
    danh một token (^[A-Za-z_][A-Za-z0-9_.\\-/]*$, dài >= 3) VÀ có dấu
    hiệu định danh (chứa '_' hay '/' hay chữ số, hoặc >= 2 chữ hoa —
    vd 'Tutorial1', 'PressToContinue', 'AvailableOn9Kings'); tên riêng
    một từ viết hoa đầu ('Escudeiro', 'English', 'Danish', 'Purchase')
    KHÔNG tách — đó thường là bản dịch (tên ngôn ngữ, tên gọi) chứ
    không phải term. Vị trí bội số mà chuỗi là câu (có khoảng trắng)
    thì KHÔNG tách (không tin được ranh giới đó).
    Chunk vẫn dài sau khi tách (vd 59 chuỗi, ranh giới là câu tiếng Anh)
    thì giữ nguyên — các chuỗi trong đó mất vị trí học, rơi về phát hiện
    từng chuỗi (ghi rõ trong báo cáo, không đoán mò).
    Trả list chunk mới [(has_term, [(suffix, value), ...]), ...].
    """
    if modal_len < 2:
        return chunks
    loose = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-/]*$")
    def _looks_like_term(v):
        if len(v) < 3 or not loose.match(v):
            return False
        if "_" in v or "/" in v or any(ch.isdigit() for ch in v):
            return True
        return sum(1 for ch in v if ch.isupper()) >= 2
    out = []
    for has_term, c in chunks:
        if not has_term or len(c) <= modal_len:
            out.append((has_term, c))
            continue
        # Vị trí tách: bội số modal_len, chuỗi tại đó giống định danh.
        cuts = [0]
        for i in range(modal_len, len(c), modal_len):
            if _looks_like_term(c[i][1]):
                cuts.append(i)
        cuts.append(len(c))
        for a, b in zip(cuts, cuts[1:]):
            out.append((True, c[a:b]))
    return out


def _mb_generic_kv(items, split_languages=False, key_map_out=None):
    """Danh sách item MonoBehaviour generic -> dict kết quả.

    Key = "<tên object đã sanitize>.s<n>" (n là thứ tự chuỗi trong object;
    object có type tree thì key là tên field). Thứ tự key GIỮ NGUYÊN thứ tự
    trong game (không sắp xếp lại — ý 7).

    key_map_out (dict tùy chọn, truyền vào sẽ được điền): {key trích xuất
    cuối cùng: [(path_id, locator)]} — locator là ("scan", j) với object
    không type tree (j = chỉ số slot theo thứ tự quét của
    _scan_monobehaviour_slots) hoặc ("field", field_path) với object có
    type tree. Dùng cho đường ghi generic: writer tìm lại đúng slot/field
    theo key trích xuất. Điền key_map_out KHÔNG đổi bất kỳ output nào đã có
    (kv/by_lang/kept/dropped/stats giữ nguyên). Một key có thể ánh xạ >1
    locator (trùng key giữa các ngôn ngữ ở split=True) — bên gọi phải từ
    chối khi mơ hồ, không đoán.

    split_languages=False (mặc định, tương thích ngược): trả {"kv": dict gộp,
    "by_lang": None, ...} như cũ — key trùng thêm hậu tố "#2", "#3"...
    split_languages=True (trích xuất có lọc ngôn ngữ): tách theo từng chuỗi
    -> {"kv": {}, "by_lang": {lang: {key: value}}, ...}:
      - Mỗi chuỗi được gán đúng 1 ngôn ngữ: phát hiện mạnh theo chuỗi
        (script/dấu/từ chức năng, sdk.langdetect) thắng; chuỗi yếu thì dùng
        vị trí đã học trong nhóm lặp (term + N bản dịch, hoặc 1 key lặp ở
        N object mỗi object một ngôn ngữ — thứ tự ngôn ngữ cố định của
        game, học từ phiếu mạnh, không đoán mò); còn lại mặc định "en"
        (Latin trơn — heuristic có ghi rõ độ tin cậy).
      - Key sạch không hậu tố #N: dedup theo từng ngôn ngữ (cùng key ở 2
        ngôn ngữ khác nhau không còn đụng nhau); key trùng THẬT trong cùng
        ngôn ngữ (2 object khác tên giống nhau) thì thêm hậu tố ổn định
        ".p<path_id>" — chỉ đúng object nguồn, để (E) ép lại được, thay vì
        "#N" phụ thuộc thứ tự duyệt.
      - Object có cấu trúc [term + N bản dịch] hệ thống (vd bảng ngôn ngữ của game):
        bản dịch lấy TERM làm key (vd 'Achievement_HisWinningness' ->
        'His Winningness'), giống file en.json của Joseph; term ở slot 0
        được tiêu thụ làm key, không xuất thành giá trị.
    Trả {"kv", "by_lang", "kept", "dropped", "stats"}; stats ghi số chuỗi
    theo từng cơ chế (strong/vị trí/mặc định) + số key hậu tố ổn định.
    Lưu ý: kept đếm cả term (được giữ lại dưới dạng key); tổng per_lang =
    kept - số term đã dùng làm key.
    """
    from collections import Counter
    from sdk import langdetect as _ld

    kept_pairs = []  # (base_key, value, path_id, item_index)
    pair_loc = []  # song song kept_pairs: (chỉ số trong pairs, field_path thô|None)
    dropped = 0
    for item_idx, item in enumerate(items):
        base = _sanitize_segment(item.get("name") or "") or ("mb_%d" % item.get("path_id", 0))
        pairs = []
        fields = item.get("fields")
        if isinstance(fields, dict):
            # Thứ tự chèn của dict = thứ tự trong type tree = thứ tự gốc
            # của game — KHÔNG sorted() lại (ý 7).
            for fp, v in fields.items():
                pairs.append((re.sub(r"[^A-Za-z0-9_]+", "_", str(fp)).strip("_") or "f", v, fp))
        else:
            for j, v in enumerate(item.get("strings") or []):
                pairs.append(("s%d" % (j + 1), v, None))
        for j, (suffix, v, raw_fp) in enumerate(pairs):
            if not isinstance(v, str) or _mb_string_is_noise(v):
                dropped += 1
                continue
            kept_pairs.append(("%s.%s" % (base, suffix), v,
                               item.get("path_id", 0), item_idx))
            pair_loc.append((j, raw_fp))

    kept = len(kept_pairs)
    stats: Dict[str, Any] = {"by_mechanism": {"strong": 0, "position": 0,
                                              "default": 0},
                             "per_lang": {}, "stable_suffix_keys": 0,
                             "x_suffix_keys": 0}
    if not split_languages:
        kv: Dict[str, str] = {}
        used = set()
        for pi, (key, v, pid, _idx) in enumerate(kept_pairs):
            if key in used:
                n = 2
                while "%s#%d" % (key, n) in used:
                    n += 1
                key = "%s#%d" % (key, n)
            used.add(key)
            kv[key] = v
            if key_map_out is not None:
                j, raw_fp = pair_loc[pi]
                loc = ("field", raw_fp) if raw_fp is not None else ("scan", j)
                key_map_out.setdefault(key, []).append((pid, loc))
        return {"kv": kv, "by_lang": None, "kept": kept, "dropped": dropped,
                "stats": stats}

    # -- Tách theo ngôn ngữ: phát hiện từng chuỗi + học vị trí. ----------
    # Phát hiện mạnh/yếu cho mọi chuỗi trước (vị trí chỉ lấp chỗ yếu).
    detected = [_ld.detect_language(v) for _k, v, _p, _i in kept_pairs]

    # (a) Vị trí trong nhóm term của từng object (chuỗi): nhóm = [term, N bản
    # dịch]; chỉ học từ nhóm có độ dài modal L (ít nhất 5 nhóm, >= 50% số
    # nhóm có term) — nhóm lệch độ dài (thiếu bản dịch) thì không học.
    # Học CHỈ trên các bản dịch (bỏ term ở slot 0 — term là định danh,
    # không phải ngôn ngữ; học nó sẽ gán nhầm 'en' cho khe term).
    chunk_pos: Dict[int, Optional[str]] = {}  # chỉ số kept_pairs -> lang vị trí
    term_key: Dict[int, str] = {}  # chỉ số kept_pairs -> term của nhóm
    term_pos: set = set()  # chỉ số kept_pairs là term của nhóm >= 2 chuỗi
    per_item_pairs: Dict[int, List[int]] = {}
    for pi, (_k, _v, _p, item_idx) in enumerate(kept_pairs):
        per_item_pairs.setdefault(item_idx, []).append(pi)
    for item_idx, pis in per_item_pairs.items():
        item_pairs = [(kept_pairs[pi][0], kept_pairs[pi][1]) for pi in pis]
        chunks = _split_term_chunks(item_pairs)
        term_chunks = [c for has_term, c in chunks if has_term and len(c) >= 4]
        if len(term_chunks) < 5:
            continue
        lens = Counter(len(c) for c in term_chunks)
        modal_len, modal_n = lens.most_common(1)[0]
        if modal_n < 5 or modal_n * 2 < len(term_chunks):
            continue
        # Tách lại chunk dài (term CamelCase không '_'/'/' bị bỏ lọt,
        # vd 'Tutorial1') tại bội số modal_len, rồi tính lại modal.
        chunks = _resplit_long_chunks(chunks, modal_len)
        term_chunks = [c for has_term, c in chunks if has_term and len(c) >= 4]
        lens = Counter(len(c) for c in term_chunks)
        modal_len, modal_n = lens.most_common(1)[0]
        if modal_n < 5 or modal_n * 2 < len(term_chunks):
            continue
        modal_groups = [[v for _s, v in c[1:]] for c in term_chunks
                        if len(c) == modal_len]
        pos_langs = _ld.infer_positions(modal_groups)
        if not any(pos_langs):
            continue
        # Object này có cấu trúc [term + N bản dịch] hệ thống -> dùng term
        # làm key cho các bản dịch (giống file en.json của Joseph: key là
        # tên term, vd 'Achievement_HisWinningness'); term được "tiêu thụ"
        # làm key, không xuất thành giá trị. Chỉ áp khi học vị trí thành
        # công (chứng tỏ đúng cấu trúc bảng term), object khác giữ key đường dẫn.
        # Chunk DÀI hơn modal_len là chunk gộp nhiều term (tách lại không
        # được — ranh giới là câu/bản dịch): term của nó KHÔNG đáng tin
        # cho cả chunk nên KHÔNG dùng term làm key (giữ key đường dẫn
        # '<object>.s<n>' cho mọi chuỗi trong chunk, kể cả term đầu).
        offset = 0
        for has_term, c in chunks:
            if has_term and 2 <= len(c) <= modal_len:
                term_pos.add(pis[offset])
                stats["term_keyed"] = stats.get("term_keyed", 0) + 1
                for j in range(1, len(c)):
                    term_key[pis[offset + j]] = c[0][1]
            offset += len(c)
        # Ghi vị trí đã học cho từng chuỗi trong nhóm modal: duyệt chunks
        # theo thứ tự, đếm offset tuyệt đối trong danh sách cặp của object.
        # pos_langs[j] ứng với bản dịch thứ j (term ở slot 0 đã bỏ) nên
        # chỉ số kept_pairs là offset + 1 + j. Nhóm ngắn hơn modal
        # (thiếu 1-3 bản dịch) thì CĂN vào thứ tự modal bằng anchor mạnh
        # (align_to_modal) thay vì gán thẳng theo chỉ số (chỉ số sau chỗ
        # thiếu đã lệch đi).
        offset = 0
        for has_term, c in chunks:
            if has_term and len(c) == modal_len:
                for j in range(len(c) - 1):
                    if j < len(pos_langs) and pos_langs[j]:
                        chunk_pos[pis[offset + 1 + j]] = pos_langs[j]
            elif has_term and 4 <= len(c) < modal_len:
                strong_codes = [
                    detected[pis[offset + 1 + j]][0]
                    if detected[pis[offset + 1 + j]][1] == "strong" else None
                    for j in range(len(c) - 1)]
                aligned = _ld.align_to_modal(strong_codes, pos_langs)
                if aligned:
                    stats["aligned_chunks"] = stats.get("aligned_chunks", 0) + 1
                    for j, lang in enumerate(aligned):
                        if lang:
                            chunk_pos[pis[offset + 1 + j]] = lang
            offset += len(c)

    # (b) Vị trí lần xuất hiện của cùng key ở nhiều object (vd 1 object hội
    # thoại lặp lại ở N ngôn ngữ): học từ các key có đúng M lần xuất hiện
    # (M = số lần phổ biến nhất trong 2..40, ít nhất 20 key như vậy).
    dup_pos: Dict[int, Optional[str]] = {}
    by_base: Dict[str, List[int]] = {}
    for pi, (k, _v, _p, _i) in enumerate(kept_pairs):
        by_base.setdefault(k, []).append(pi)
    multi = {k: v for k, v in by_base.items() if 2 <= len(v) <= 40}
    if len(multi) >= 20:
        sizes = Counter(len(v) for v in multi.values())
        modal_m, modal_cnt = sizes.most_common(1)[0]
        if modal_cnt >= 20:
            modal_groups = [[kept_pairs[pi][1] for pi in multi[k]]
                            for k in sorted(multi)
                            if len(multi[k]) == modal_m]
            pos_langs = _ld.infer_positions(modal_groups)
            if any(pos_langs):
                for k in sorted(multi):
                    if len(multi[k]) != modal_m:
                        continue
                    for j, pi in enumerate(multi[k]):
                        if j < len(pos_langs) and pos_langs[j]:
                            dup_pos[pi] = pos_langs[j]
                # Nhóm ngắn hơn modal (thiếu 1-3 ngôn ngữ): căn vào thứ tự
                # modal bằng anchor mạnh, như nhóm term ở trên.
                for k in sorted(multi):
                    pis_k = multi[k]
                    if len(pis_k) >= modal_m or len(pis_k) < 2:
                        continue
                    strong_codes = [
                        detected[pi][0] if detected[pi][1] == "strong" else None
                        for pi in pis_k]
                    aligned = _ld.align_to_modal(strong_codes, pos_langs)
                    if aligned:
                        stats["aligned_groups"] = stats.get("aligned_groups", 0) + 1
                        for j, lang in enumerate(aligned):
                            if lang:
                                dup_pos[pis_k[j]] = lang

    # Gán ngôn ngữ cuối: strong thắng -> vị trí (chunk rồi duplicate) -> mặc định.
    # Key xuất: bản dịch trong nhóm term hệ thống lấy TERM làm key (giống
    # file en.json của Joseph); term ở slot 0 được tiêu thụ làm key nên bỏ
    # qua (không xuất thành giá trị); còn lại key đường dẫn "<object>.s<n>".
    by_lang: Dict[str, Dict[str, str]] = {}
    used_lang: Dict[str, set] = {}
    mech = stats["by_mechanism"]
    for pi, (key, v, pid, _idx) in enumerate(kept_pairs):
        if pi in term_pos:
            continue
        code, conf = detected[pi]
        if conf == "strong" and code:
            lang = code
            mech["strong"] += 1
        elif pi in chunk_pos and chunk_pos[pi]:
            lang = chunk_pos[pi]
            mech["position"] += 1
        elif pi in dup_pos and dup_pos[pi]:
            lang = dup_pos[pi]
            mech["position"] += 1
        else:
            lang = code or "en"
            mech["default"] += 1
        bucket = by_lang.setdefault(lang, {})
        used = used_lang.setdefault(lang, set())
        out_key = term_key.get(pi, key)
        if out_key in used:
            # Cùng key + cùng giá trị (vd tên riêng 'Odyssey' giống nhau
            # ở nhiều ngôn ngữ, đều rơi về en yếu): giữ 1 bản, đếm số
            # bản trùng đã gộp — KHÔNG mất chuỗi (giá trị giống hệt nhau
            # từng byte), chỉ khỏi phình file bằng hậu tố .p vô ích.
            if bucket.get(out_key) == v:
                stats["identical_dropped"] = stats.get("identical_dropped", 0) + 1
                continue
            out_key = "%s.p%d" % (out_key, pid)
            stats["stable_suffix_keys"] += 1
            n = 2
            while out_key in used:
                if bucket.get(out_key) == v:
                    stats["identical_dropped"] = stats.get("identical_dropped", 0) + 1
                    out_key = None
                    break
                out_key = "%s.p%d.x%d" % (term_key.get(pi, key), pid, n)
                stats["x_suffix_keys"] += 1
                n += 1
            if out_key is None:
                continue
        used.add(out_key)
        bucket[out_key] = v
        if key_map_out is not None:
            j, raw_fp = pair_loc[pi]
            loc = ("field", raw_fp) if raw_fp is not None else ("scan", j)
            key_map_out.setdefault(out_key, []).append((pid, loc))
    stats["per_lang"] = {lang: len(kv) for lang, kv in sorted(by_lang.items())}
    return {"kv": {}, "by_lang": by_lang, "kept": kept, "dropped": dropped,
            "stats": stats}


def generic_write_targets(sf):
    """Target ghi cho MonoBehaviour generic (không phải nguồn ngôn ngữ).

    Sống ở extraction (không phải unity) vì dùng _mb_generic_kv của
    extraction; unity KHÔNG import extraction (tránh import vòng
    unity <-> extraction — extraction đã import unity). Bên gọi
    monobehaviour_write_targets truyền hàm này qua tham số generic_fn.

    Dùng đúng cùng item list (monobehaviour_texts, chỉ kind "generic") +
    cùng _mb_generic_kv mà extraction dùng — chạy CẢ split=False lẫn
    split=True (web mặc định split=True, CLI mặc định split=False) rồi
    gộp bản đồ key -> locator — nên key trích xuất ánh xạ đúng slot/field
    cần ghi, kể cả hậu tố ổn định ".p<path_id>" (bóc theo path_id của
    locator). Trả list {path_id, kind "generic", name, key, field_path,
    before}: field_path = "scan[j]" (không type tree, j = chỉ số slot
    theo thứ tự _scan_monobehaviour_slots) hoặc field_path thật (có type
    tree). Key ánh xạ >1 locator KHÁC NHAU -> BỎ QUA (mơ hồ, không đoán —
    vd term-key xuất hiện ở nhiều ngôn ngữ trong cùng 1 object); key ánh
    xạ 1 locator duy nhất (kể cả trùng lặp giống hệt giữa 2 lần chạy)
    -> target. Object nguồn ngôn ngữ (I2/StringTable/Shared/Locale)
    không có ở đây — chúng có target riêng."""
    items = [it for it in sf.monobehaviour_texts()
             if it.get("kind") == "generic"]
    if not items:
        return []
    by_pid = {}
    for it in items:
        by_pid.setdefault(it.get("path_id"), it)
    key_map = {}
    for split in (False, True):
        _mb_generic_kv(items, split_languages=split,
                       key_map_out=key_map)
    out = []
    for key, locs in key_map.items():
        uniq = []
        for loc in locs:
            if loc not in uniq:
                uniq.append(loc)
        if len(uniq) != 1:
            continue  # mơ hồ: >1 vị trí khác nhau cho cùng 1 key
        pid, loc = uniq[0]
        item = by_pid.get(pid)
        if item is None:
            continue
        kind, val = loc
        if kind == "scan":
            strings = item.get("strings") or []
            if not (0 <= val < len(strings)):
                continue
            before = strings[val]
            fp = "scan[%d]" % val
        else:
            fields = item.get("fields") or {}
            if val not in fields:
                continue
            before = fields[val]
            fp = val
        if not isinstance(before, str):
            continue
        out.append({"path_id": pid, "kind": "generic",
                    "name": item.get("name") or "", "key": key,
                    "field_path": fp, "before": before})
    return out


def _string_table_collection(name, loc):
    """Tên collection suy từ tên bảng bỏ hậu tố "_<locale>" (vd "Buildings_en")."""
    if loc and name.lower().endswith("_" + loc):
        return name[:-len(loc) - 1]
    return name


def _st_key_sort_id(key, key_to_id):
    """m_Id để sắp key StringTable (khớp thứ tự file mẫu P0-3).

    File mẫu xếp key trong mỗi collection theo m_Id tăng dần của
    SharedTableData (đo thật trên 9 Kings v1.0.6: đúng cả 6 collection;
    2 collection phân biệt là Buildings và Spells — thứ tự asset của
    StringTable lẫn SharedTableData đều khác mẫu, chỉ sort theo m_Id
    mới khớp). Key không có trong SharedTableData (dạng "id_<m_Id>")
    sắp theo số m_Id trong tên; key không rõ m_Id xếp cuối, giữ thứ
    tự cũ (sort ổn định). Quy tắc chung theo dữ liệu asset, không
    hardcode game/build nào."""
    mid = key_to_id.get(key)
    if isinstance(mid, int):
        return (0, mid, "")
    if isinstance(key, str) and key.startswith("id_"):
        try:
            return (0, int(key[3:]), "")
        except ValueError:
            pass
    return (1, 0, key)


def _sort_st_bucket(bucket, key_to_id):
    """Trả dict mới với key trong bucket xếp theo m_Id tăng dần."""
    return dict(sorted(bucket.items(),
                       key=lambda kv: _st_key_sort_id(kv[0], key_to_id)))


def _invert_shared_keys(shared_keys):
    """{collection: {key: m_Id}} từ {collection: {m_Id: key}} (giữ m_Id đầu)."""
    out = {}
    for coll, keys in (shared_keys or {}).items():
        inv = {}
        for i, k in keys.items():
            inv.setdefault(k, i)
        out[coll] = inv
    return out


def join_string_tables_grouped(tables, shared):
    """Join StringTable + SharedTableData theo m_Id, giữ riêng từng collection.

    Trả (grouped, notes, conflicts, shared_keys):
    - grouped: {(bundle, locale, collection): {key: value}} — key là key thật
      trong SharedTableData, hoặc "id_<m_Id>" khi entry không có key.
    - notes: cùng ý nghĩa với _join_string_tables (bảng không rõ locale /
      thiếu SharedTableData / entry không có key).
    - conflicts: cùng (bundle, locale, collection, key) mà giá trị khác nhau
      (giữ bản đầu, ghi rõ cả hai giá trị + nguồn), và cùng (collection,
      m_Id) mà SharedTableData cho key khác nhau. Không bỏ âm thầm.
    - shared_keys: {collection: {m_Id: key}} — vũ trụ key từ SharedTableData
      (để caller biết key nào thiếu entry ở một locale: thiếu -> "").
    Dùng cho xuất locale dạng key "table/key"; pipeline gộp chung vẫn dùng
    _join_string_tables (giữ nguyên hành vi + thứ tự cũ)."""
    shared_map: Dict[str, Dict[int, str]] = {}
    shared_src: Dict[Tuple[str, int], str] = {}
    conflicts: List[Dict[str, Any]] = []
    for item in shared:
        coll = item.get("collection") or ""
        if not coll:
            continue
        bucket = shared_map.setdefault(coll, {})
        for i, k in (item.get("entries") or {}).items():
            if i in bucket and bucket[i] != k:
                conflicts.append({
                    "key": "%s/id_%d" % (coll, i),
                    "locale": None,
                    "kind": "shared_key",
                    "kept": {"value": bucket[i],
                             "source": shared_src.get((coll, i), "")},
                    "dropped": {"value": k, "source": item.get("name") or ""},
                })
            else:
                bucket.setdefault(i, k)
                shared_src.setdefault((coll, i), item.get("name") or "")
    grouped: Dict[Tuple[str, str, str], Dict[str, str]] = {}
    key_src: Dict[Tuple[str, str, str, str], str] = {}
    notes: List[str] = []
    for bundle_path, item in tables:
        loc = (item.get("locale") or "").strip().lower()
        if not loc:
            notes.append("bảng StringTable %r không rõ locale, bỏ qua"
                         % (item.get("name") or "?"))
            continue
        name = item.get("name") or ""
        coll = _string_table_collection(name, loc)
        keys = shared_map.get(coll)
        bucket = grouped.setdefault((bundle_path, loc, coll), {})
        src = "%s::%s" % (bundle_path, name)
        n_id = 0
        for i, v in (item.get("entries") or {}).items():
            key = keys.get(i) if keys else None
            if key is None:
                key = "id_%d" % i
                n_id += 1
            if key in bucket:
                if bucket[key] != v:
                    conflicts.append({
                        "key": "%s/%s" % (coll, key),
                        "locale": loc,
                        "kind": "value",
                        "kept": {"value": bucket[key],
                                 "source": key_src.get((bundle_path, loc, coll, key), "")},
                        "dropped": {"value": v, "source": src},
                    })
            else:
                bucket[key] = v
                key_src[(bundle_path, loc, coll, key)] = src
        if keys is None:
            notes.append("bảng %r (locale %s): không tìm thấy SharedTableData "
                         "cho collection %r, dùng id làm key (%d chuỗi)"
                         % (name, loc, coll, len(item.get("entries") or {})))
        elif n_id:
            notes.append("bảng %r (locale %s): %d/%d entry không có key trong "
                         "SharedTableData, dùng id làm key"
                         % (name, loc, n_id, len(item.get("entries") or {})))
    # Thứ tự key trong mỗi bucket: theo m_Id tăng dần của SharedTableData
    # (khớp file mẫu P0-3) — KHÔNG giữ thứ tự asset của bảng StringTable
    # từng locale (thứ tự đó khác mẫu ở Buildings/Spells trên 9K v1.0.6,
    # đo thật 2026-11-17). Xem _st_key_sort_id.
    key_to_id = _invert_shared_keys(shared_map)
    grouped = {gkey: _sort_st_bucket(bucket, key_to_id.get(gkey[2]) or {})
               for gkey, bucket in grouped.items()}
    return grouped, notes, conflicts, shared_map


def _join_string_tables(tables, shared):
    """Join StringTable + SharedTableData theo m_Id -> {(bundle, locale): {key: value}}.

    shared: {tên collection: {m_Id: key}} gộp từ mọi bundle trong IPA.
    Bảng không tìm được SharedTableData thì key rơi về "id_<m_Id>" (vẫn giữ
    chuỗi, không mất text) + ghi chú để caller log. Tên collection suy từ
    tên bảng bỏ hậu tố "_<locale>" (vd "Buildings_en" -> "Buildings")."""
    shared_map: Dict[str, Dict[int, str]] = {}
    for item in shared:
        coll = item.get("collection") or ""
        if not coll:
            continue
        bucket = shared_map.setdefault(coll, {})
        for i, k in (item.get("entries") or {}).items():
            bucket.setdefault(i, k)
    merged: Dict[Tuple[str, str], Dict[str, str]] = {}
    notes: List[str] = []
    for bundle_path, item in tables:
        loc = (item.get("locale") or "").strip().lower()
        if not loc:
            notes.append("bảng StringTable %r không rõ locale, bỏ qua"
                         % (item.get("name") or "?"))
            continue
        name = item.get("name") or ""
        coll = name[:-len(loc) - 1] if name.lower().endswith("_" + loc) else name
        keys = shared_map.get(coll)
        bucket = merged.setdefault((bundle_path, loc), {})
        n_id = 0
        for i, v in (item.get("entries") or {}).items():
            key = keys.get(i) if keys else None
            if key is None:
                key = "id_%d" % i
                n_id += 1
            if key not in bucket:
                bucket[key] = v
        if keys is None:
            notes.append("bảng %r (locale %s): không tìm thấy SharedTableData "
                         "cho collection %r, dùng id làm key (%d chuỗi)"
                         % (name, loc, coll, len(item.get("entries") or {})))
        elif n_id:
            notes.append("bảng %r (locale %s): %d/%d entry không có key trong "
                         "SharedTableData, dùng id làm key"
                         % (name, loc, n_id, len(item.get("entries") or {})))
    return merged, notes


def _language_source_all_columns(item):
    """Mọi cột ngôn ngữ trong bảng ngôn ngữ (BUG#2 — thay cho
    _language_source_base_columns cũ gộp biến thể về mã gốc).

    Trả (columns, n_nonlocale): columns {mã_đầy_đủ: chỉ_số_cột} với mã
    chuẩn hóa ("zh_CN" -> "zh-cn", giữ nguyên từng biến thể zh-CN/zh-TW
    riêng — KHÔNG gộp về "zh"); n_nonlocale = số cột không phải ngôn ngữ
    (mã rỗng / mã gốc không phải chữ cái: Notes, Status...). Quy tắc
    chung, không theo game nào: mỗi cột ngôn ngữ được 1 file ảo riêng ở
    _language_source_virtual_files; xin "zh" thì ra cả zh-CN + zh-TW.
    Cột trùng mã (hiếm) giữ cột đầu, cột sau đếm vào n_nonlocale để khỏi
    ghi đè im lặng."""
    columns: Dict[str, int] = {}
    n_nonlocale = 0
    for idx, lang in enumerate(item.get("languages") or []):
        code = (lang.get("code") or "").strip()
        if not code:
            n_nonlocale += 1
            continue
        norm = code.replace("_", "-").lower()
        base = norm.split("-")[0]
        if not base.isalpha() or not 2 <= len(base) <= 3:
            n_nonlocale += 1
            continue
        if norm in columns:
            n_nonlocale += 1
            continue
        columns[norm] = idx
    return columns, n_nonlocale


def _language_source_base_columns(item):
    """[TƯƠNG THÍCH NGƯỢC — giữ cho test/harness cũ] Chọn 1 cột cho mỗi
    mã ngôn ngữ GỐC (gộp biến thể về mã gốc như trước BUG#2). Code mới
    dùng _language_source_all_columns; hàm này chỉ còn để test cũ +
    script ngoài repo gọi vẫn chạy, không dùng trong pipeline."""
    columns, n_nonlocale = _language_source_all_columns(item)
    picked: Dict[str, Tuple[str, int]] = {}
    skipped_variants: List[str] = []
    for full, idx in columns.items():
        base = full.split("-")[0]
        if base not in picked:
            picked[base] = (full, idx)
        elif picked[base][0] != base and full == base:
            skipped_variants.append(picked[base][0])
            picked[base] = (full, idx)
        else:
            skipped_variants.append(full)
    return picked, skipped_variants, n_nonlocale


def _language_source_virtual_files(mb_lang_sources, log):
    """File JSON ảo cho bảng ngôn ngữ có cấu trúc: {(vpath): bytes}.

    Mỗi (đơn vị, mã ĐẦY ĐỦ) một file, key = tên term thật, GIỮ chuỗi rỗng.
    BUG#2: mỗi biến thể (zh-CN, zh-TW...) có file riêng — không gộp về mã
    gốc nữa. Tên file có đoạn ".langtable" để không đụng file generic cùng
    đơn vị + ngôn ngữ (dict ảo ghi đè sẽ mất bảng, lỗi đã gặp khi kiểm
    chứng 2026-11-17); hậu tố ".monobehaviour.<lang>.json" giữ nguyên để
    nhóm __mono__ + đặt tên xuất vẫn nhận ra. Nhiều bảng trong cùng đơn vị
    gộp keep-first theo thứ tự gặp, xung đột đếm + log (không lặng lẽ)."""

    def _lang_log(columns, n_nonloc):
        return ", ".join(sorted(columns))

    ls_units: Dict[Tuple[str, str, str], Dict[str, str]] = {}
    ls_order: List[Tuple[str, str, str]] = []
    ls_conflicts = 0
    for unit_path, assets_name, item in mb_lang_sources:
        columns, n_nonloc = _language_source_all_columns(item)
        unit_label = unit_path + ("::" + assets_name if assets_name else "")
        log("Unity: %s -> bảng ngôn ngữ %r: %d term, %d cột (mã đầy đủ: %s; "
            "cột không phải ngôn ngữ: %d)"
            % (unit_label, item.get("name") or "?",
               len(item.get("terms") or []),
               len(item.get("languages") or []),
               _lang_log(columns, n_nonloc),
               n_nonloc))
        for code, col in sorted(columns.items()):
            ukey = (unit_path, assets_name, code)
            bucket = ls_units.setdefault(ukey, {})
            if ukey not in ls_order:
                ls_order.append(ukey)
            for term in item.get("terms") or []:
                tname = term.get("term")
                vals = term.get("values") or []
                if tname is None or col >= len(vals):
                    continue
                if tname not in bucket:
                    bucket[tname] = vals[col]
                elif bucket[tname] != vals[col]:
                    # Nhiều bảng trong cùng đơn vị, cùng term khác giá
                    # trị: giữ bản đầu + đếm (log tổng ở dưới), không
                    # lặng lẽ — xung đột liên file vẫn ghi ở bước gộp.
                    ls_conflicts += 1
    if ls_conflicts:
        log("Unity: bảng ngôn ngữ: %d xung đột cùng term trong đơn vị "
            "(giữ bản đầu)" % ls_conflicts)
    out: Dict[str, bytes] = {}
    for unit_path, assets_name, code in ls_order:
        kv = ls_units[(unit_path, assets_name, code)]
        if not kv:
            continue
        if assets_name:
            stem = _sanitize_segment(os.path.splitext(os.path.basename(assets_name))[0]
                                     or "assets")
            vpath = "%s::%s::%s.langtable.monobehaviour.%s.json" % (unit_path, assets_name, stem, code)
        else:
            stem = _sanitize_segment(os.path.splitext(os.path.basename(unit_path))[0]
                                     or "assets")
            vpath = "%s::%s.langtable.monobehaviour.%s.json" % (unit_path, stem, code)
        out[vpath] = json.dumps(kv, ensure_ascii=False, indent=1).encode("utf-8")
        log("Unity: %s -> bảng ngôn ngữ [%s]: %d term (xuất %s)"
            % (unit_path + ("::" + assets_name if assets_name else ""),
               code, len(kv), vpath))
    return out


def _text_data_list_virtual_files(mb_text_data_lists, log):
    """File JSON ảo cho bảng text dạng danh sách: {(vpath): bytes}.

    Mỗi (đơn vị, mã locale ĐẦY ĐỦ) một file, key = key thật của entry,
    GIỮ nguyên thứ tự entry trong game; nhóm fileName chỉ ghi log
    (metadata, không làm bẩn key). Tên file có đoạn ".textdata" để
    không đụng file generic cùng đơn vị + ngôn ngữ
    ("...monobehaviour.<lang>.json" — đụng tên thì dict ảo ghi đè mất
    bảng); hậu tố ".monobehaviour.<lang>.json" giữ nguyên để nhóm
    __mono__ + đặt tên xuất vẫn nhận ra. Mỗi biến thể (zh-CN, zh-TW...)
    có file riêng — cột "zhs"/"zht" của game đã được ánh xạ về
    zh-CN/zh-TW ở sdk/text_data (xin "zh" thì ra cả 2).

    KIỂM CHỨNG 2 CHIỀU — bước 5 phương pháp Duy (fail-closed, raise
    PB3Error E_CORRUPT, không ghi file nửa vời):
      - 0 key trùng trong bảng (key trùng thì file key–value phẳng
        không xác định được giá trị nào là đúng);
      - 0 giá trị rỗng trong mọi cột (giá trị rỗng = bản dịch thiếu,
        phải thấy rõ thay vì lọt vào file locale).
    Nhiều bảng trong cùng đơn vị gộp keep-first theo thứ tự gặp, xung
    đột đếm + log (không lặng lẽ)."""

    td_units: Dict[Tuple[str, str, str], Dict[str, str]] = {}
    td_order: List[Tuple[str, str, str]] = []
    td_conflicts = 0
    for unit_path, assets_name, item in mb_text_data_lists:
        entries = item.get("entries") or []
        columns = item.get("columns") or []
        unit_label = unit_path + ("::" + assets_name if assets_name else "")
        groups = item.get("groups") or {}
        log("Unity: %s -> bảng text-data %r: %d entry, %d cột (%s; nhóm: %s)"
            % (unit_label, item.get("name") or "?",
               len(entries), len(columns), ", ".join(columns),
               ", ".join("%s:%d" % kv for kv in sorted(groups.items())) or "—"))
        seen = set()
        dups = []
        for e in entries:
            key = e.get("key")
            if key in seen:
                dups.append(key)
            seen.add(key)
        if dups:
            raise PB3Error("E_CORRUPT",
                           "bảng text-data %r (%s) có %d key trùng (%s...) — "
                           "không trích được file key–value phẳng"
                           % (item.get("name") or "?", unit_label, len(dups),
                              ", ".join(repr(k) for k in dups[:5])),
                           where=unit_label)
        empty: Dict[str, List[str]] = {}
        for code in columns:
            bad = [e.get("key") for e in entries
                   if not (e.get("values") or {}).get(code)]
            if bad:
                empty[code] = bad
        if empty:
            detail = "; ".join(
                "%s: %d rỗng (%s...)" % (c, len(ks),
                                         ", ".join(repr(k) for k in ks[:3]))
                for c, ks in sorted(empty.items()))
            raise PB3Error("E_CORRUPT",
                           "bảng text-data %r (%s) có giá trị rỗng: %s — "
                           "không trích được file locale đầy đủ"
                           % (item.get("name") or "?", unit_label, detail),
                           where=unit_label)
        for code in columns:
            ukey = (unit_path, assets_name, code)
            bucket = td_units.setdefault(ukey, {})
            if ukey not in td_order:
                td_order.append(ukey)
            for e in entries:
                key = e.get("key")
                value = (e.get("values") or {}).get(code, "")
                if key not in bucket:
                    bucket[key] = value
                elif bucket[key] != value:
                    td_conflicts += 1
    if td_conflicts:
        log("Unity: bảng text-data: %d xung đột cùng key trong đơn vị "
            "(giữ bản đầu)" % td_conflicts)
    out: Dict[str, bytes] = {}
    for unit_path, assets_name, code in td_order:
        kv = td_units[(unit_path, assets_name, code)]
        if not kv:
            continue
        # Ten file dung ma chu thuong (nhu bang ngon ngu) de detect_locale
        # + ban ke cot nhan dung; log van dung ma canonical (zh-CN).
        file_code = code.lower()
        if assets_name:
            stem = _sanitize_segment(os.path.splitext(os.path.basename(assets_name))[0]
                                     or "assets")
            vpath = "%s::%s::%s.textdata.monobehaviour.%s.json" % (unit_path, assets_name, stem, file_code)
        else:
            stem = _sanitize_segment(os.path.splitext(os.path.basename(unit_path))[0]
                                     or "assets")
            vpath = "%s::%s.textdata.monobehaviour.%s.json" % (unit_path, stem, file_code)
        out[vpath] = json.dumps(kv, ensure_ascii=False, indent=1).encode("utf-8")
        log("Unity: %s -> bảng text-data [%s]: %d key (xuất %s)"
            % (unit_path + ("::" + assets_name if assets_name else ""),
               code, len(kv), vpath))
    return out


def _fill_string_table_universe(grouped, shared_keys):
    """Điền "" cho key trong universe SharedTableData mà bucket chưa có (BUG#2).

    grouped: {(bundle, locale, collection): {key: value}} từ
    join_string_tables_grouped; shared_keys: {collection: {m_Id: key}}.
    Trả (dict_mới, số_key_đã_điền); không sửa dict gốc. Key thiếu ở một
    locale nhưng có trong SharedTableData là bản dịch còn thiếu — giữ key
    với giá trị rỗng để tập key giống hệt nhau giữa các locale (đo thật
    trên IPA 9 Kings v1.0.6: universe 102 key; en chỉ có 100 key thật,
    điền 2 key "" — đúng số rỗng 2 của file mẫu en.json; zh chỉ có 26 key
    thật, điền 76 key "" — 76 + 30 rỗng langtable = 106, đúng số rỗng của
    file mẫu zh-TW.json).
    Điền cả bucket collection CHƯA CÓ cho (bundle, locale) đã có ít nhất
    một bucket StringTable: vd zh chỉ có bảng Policies (26 key) thì vẫn
    tạo đủ Buildings/CommonTexts/Kings/Spells/Troops toàn "" — nếu không
    zh sẽ thiếu 54 key so với en (lỗi gặp khi kiểm chứng 2026-11-17).
    Locale hoàn toàn không có StringTable nào thì không tạo (không có
    bằng chứng game có StringTable cho locale đó)."""
    filled = 0
    out: Dict[Tuple[str, str, str], Dict[str, str]] = {
        k: dict(v) for k, v in grouped.items()}
    pairs = {(bundle, loc) for (bundle, loc, _coll) in grouped}
    for (bundle, loc) in sorted(pairs):
        for coll, keys in shared_keys.items():
            bucket = out.setdefault((bundle, loc, coll), {})
            for key in keys.values():
                if key not in bucket:
                    bucket[key] = ""
                    filled += 1
    # Sắp lại thứ tự sau khi điền (đo thật 2026-11-17, 9K v1.0.6):
    # (a) key trong mỗi bucket theo m_Id tăng dần — key điền "" phải
    #     nằm đúng vị trí m_Id của nó, không dồn cuối bucket (mẫu để
    #     Spells/Cemetery_Desc giữa khối, không phải cuối);
    # (b) thứ tự collection theo lần gặp đầu trong SharedTableData
    #     (Policies, Buildings, ... — khớp file mẫu), không xếp theo
    #     chữ cái và không theo thứ tự gặp bảng StringTable.
    key_to_id = _invert_shared_keys(shared_keys)
    coll_order = {coll: idx for idx, coll in enumerate(shared_keys)}
    groups: Dict[Tuple[str, str], List[Tuple[str, str, str]]] = {}
    for t in out:
        groups.setdefault((t[0], t[1]), []).append(t)
    reordered: Dict[Tuple[str, str, str], Dict[str, str]] = {}
    for (bundle, loc), keys in groups.items():
        # Sort ổn định: collection ngoài SharedTableData giữ thứ tự gặp
        # đầu, xếp sau các collection đã biết.
        keys.sort(key=lambda t: coll_order.get(t[2], len(coll_order)))
        for t in keys:
            reordered[t] = _sort_st_bucket(out[t], key_to_id.get(t[2]) or {})
    return reordered, filled


def _build_layers(root_vfs, inv: List[disc.InventoryEntry], limits, backend, log,
                  progress_fn: Optional[Callable[[str, int, Optional[int]], None]] = None,
                  skipped: Optional[List[Dict[str, Any]]] = None,
                  split_mono_languages: bool = False
                  ) -> Tuple[List[_Layer], Dict[str, Dict[str, int]]]:
    """Các lớp VFS: gốc + container lồng + TextAsset Unity ảo (MemoryVFS).

    split_mono_languages=True (trích xuất có lọc ngôn ngữ, mục A): file
    MonoBehaviour generic tách thành 1 file ảo MỖI NGÔN NGỮ
    ("<đơn vị>.monobehaviour.<lang>.json", key sạch, 1 ngôn ngữ/file) thay
    vì 1 file gộp trộn mọi ngôn ngữ; file gộp cũ chỉ còn khi không lọc
    (tương thích ngược).

    Trả (layers, shared_index) — shared_index {collection: {key: m_Id}} từ
    mọi SharedTableData gặp trong lúc quét (để sidecar provenance mang
    theo cho (E): phân loại key StringTable ở đơn vị khác + chen key
    thiếu; file không có SharedTableData thì {}).
    progress_fn("layers", đã_xong, tổng): báo sau mỗi entry đã xử lý xong
    (lag 1 entry: báo số entry trước đó để % luôn là việc ĐÃ xong).

    KHÔNG BAO GIỜ bỏ qua lặng lẽ (lỗi en.json 2026-11-16): container/
    file Unity nào đọc lỗi, quá giới hạn, hoặc cho 0 TextAsset đều ghi
    vào `skipped` (nếu truyền vào) kèm lý do + đường dẫn đầy đủ, và log.
    Giới hạn entry 256 MiB (caps-po, trước là 64 MiB) vẫn giữ để bảo vệ
    RAM tab iPhone; entry Unity trong trần nhưng > stream_entry_bytes
    (32 MiB) thì mở bằng spool-to-temp + streaming thay vì nạp trọn —
    còn vượt trần cứng thì phải BÁO RÕ để lần chạy sau trên IPA thật đọc
    được lý do, thay vì bundle biến mất như trước."""
    layers = [_Layer("", root_vfs, "root")]
    nested = NestedVFS(root_vfs, limits=limits)
    virtual: Dict[str, bytes] = {}
    # BUG#2: origin_kind của từng file ảo (xem _Layer.origin_map).
    virtual_origin: Dict[str, str] = {}
    # MonoBehaviour: StringTable/SharedTableData có thể nằm ở 2 bundle khác
    # nhau trong cùng IPA nên gom hết rồi join sau vòng lặp (theo m_Id);
    # generic gom theo từng đơn vị (.assets / entry CAB trong bundle).
    mb_tables: List[Tuple[str, Dict[str, Any]]] = []
    mb_shared: List[Dict[str, Any]] = []
    mb_generic_units: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    mb_lang_sources: List[Tuple[str, str, Dict[str, Any]]] = []
    mb_text_data_lists: List[Tuple[str, str, Dict[str, Any]]] = []
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
            # File Unity vượt giới hạn entry (256 MiB, caps-po): nếu là file
            # độc lập trên đĩa (SingleFileVFS — vd data.unity3d 590MB) thì
            # đọc streaming từng entry thay vì bỏ qua (trước đây bỏ qua lặng
            # lẽ bằng lý do giới hạn, còn trình duyệt thì chết tab vì
            # open_source_vfs đã nạp trọn file vào MemoryVFS trước đó).
            # Entry trong ZIP/IPA vượt trần cứng vẫn skip có lý do.
            # Entry Unity lớn nhưng trong trần (stream_entry_bytes < size <=
            # max_entry_bytes, vd Tom data.unity3d 78 MB trong IPA) thì
            # spool qua stream ra temp file rồi parse streaming (caps-po) —
            # không ôm cả entry + payload giải nén vào RAM cùng lúc.
            if entry.size > limits.max_entry_bytes:
                if isinstance(root_vfs, SingleFileVFS) and entry.path == root_vfs.name:
                    got, notes, mb = _expand_unity_stream(root_vfs.source_path,
                                                          entry.path, backend, limits)
                else:
                    _add_skipped(entry.path, "file Unity %d byte > giới hạn entry %d byte — "
                                 "không mở bundle/.assets (cần nâng giới hạn Unity nếu "
                                 "bundle thật vượt ngưỡng này)" % (entry.size, limits.max_entry_bytes))
                    log("Unity: %s: %d byte > giới hạn %d, bỏ qua"
                        % (entry.path, entry.size, limits.max_entry_bytes))
                    continue
            elif entry.size > limits.stream_entry_bytes:
                # (2026-11-18, fix-two-reds; goc tu merge-mb-generic
                # 9ca285d) Chi entry UnityFS bundle moi di duong streaming:
                # _expand_unity_stream chi biet doc bundle — file .assets
                # tho (SerializedFile, vd PvZ resources.assets 34 MB >
                # tran 32 MiB) se E_UNSUPPORTED_FORMAT va ca file bi skip
                # mat sach. Sniff magic bang slice 64 byte dau (re, chi
                # giai nen 64 byte): khong phai bundle thi ve duong cu
                # (doc tron + _expand_unity, <= 256 MiB). Sniff loi thi
                # mac dinh ve duong cu cho an toan. LUU Y: 16 byte KHONG
                # du — detect_bundle can ca chuoi player/revision
                # (sample v9 can 32 byte; Tom v8 can 32 byte); sniff 16
                # byte tra None ca voi bundle that, lam mat streaming.
                _is_bundle = False
                try:
                    _head = bytes(nested.slice(entry.path, 0, 64))
                    _is_bundle = unity_sdk.detect_bundle(_head) is not None
                except PB3Error as exc:
                    log("Unity: %s: khong sniff duoc magic [%s], doc tron"
                        % (entry.path, exc.code))
                if _is_bundle:
                    try:
                        got, notes, mb = _expand_unity_stream_entry(
                            nested, entry.path, backend, limits)
                    except PB3Error as exc:
                        _add_skipped(entry.path, "không mở được file Unity (streaming) [%s]: %s"
                                     % (exc.code, exc.message_vi or exc.detail))
                        log("Unity: %s: không mở được (streaming) [%s]" % (entry.path, exc.code))
                        continue
                else:
                    try:
                        data = bytes(nested.read(entry.path, None))
                    except PB3Error as exc:
                        _add_skipped(entry.path, "không đọc được file Unity [%s]: %s"
                                     % (exc.code, exc.message_vi or exc.detail))
                        log("Unity: %s: không đọc được [%s]" % (entry.path, exc.code))
                        continue
                    got, notes, mb = _expand_unity(data, entry.path, backend, limits)
            else:
                try:
                    data = bytes(nested.read(entry.path, None))
                except PB3Error as exc:
                    _add_skipped(entry.path, "không đọc được file Unity [%s]: %s"
                                 % (exc.code, exc.message_vi or exc.detail))
                    log("Unity: %s: không đọc được [%s]" % (entry.path, exc.code))
                    continue
                got, notes, mb = _expand_unity(data, entry.path, backend, limits)
            for item in mb["string_tables"]:
                mb_tables.append((entry.path, item))
            mb_shared.extend(mb["shared_tables"])
            for assets_name, item in mb["generic"]:
                mb_generic_units.setdefault((entry.path, assets_name), []).append(item)
            for assets_name, item in mb["language_sources"]:
                mb_lang_sources.append((entry.path, assets_name, item))
            for assets_name, item in mb["text_data_lists"]:
                mb_text_data_lists.append((entry.path, assets_name, item))
            n_st = len(mb["string_tables"])
            n_gen = len(mb["generic"])
            n_ls = len(mb["language_sources"])
            n_td = len(mb["text_data_lists"])
            n_td_entries = sum(len(_it.get("entries") or [])
                               for _an, _it in mb["text_data_lists"])
            n_ls_terms = sum(len(_it.get("terms") or [])
                             for _an, _it in mb["language_sources"])
            n_gen_str = 0
            for _an, _it in mb["generic"]:
                _f = _it.get("fields")
                _s = _it.get("strings")
                n_gen_str += len(_f) if isinstance(_f, dict) else len(_s or [])
            if got or n_st or n_gen or n_ls or n_td:
                virtual.update(got)
                for _vp in got:
                    # TextAsset Unity: file locale có cấu trúc (JSON/CSV/
                    # .po...) khi parse được bằng plugin định dạng.
                    virtual_origin[_vp] = "text_asset"
                # Giữ nguyên tiền tố log cũ "-> N TextAsset" (harness cũ
                # kiểm tra đúng chuỗi này) rồi thêm số liệu MonoBehaviour.
                log("Unity: %s -> %d TextAsset, %d StringTable, %d MonoBehaviour (%d chuỗi)%s%s"
                    % (entry.path, len(got), n_st, n_gen, n_gen_str,
                       (", %d bảng ngôn ngữ (%d term)" % (n_ls, n_ls_terms))
                       if n_ls else "",
                       (", %d bảng text-data (%d entry)" % (n_td, n_td_entries))
                       if n_td else ""))
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
    # MonoBehaviour -> file JSON ảo (để plugin json_text nhận bằng đuôi .json):
    # - StringTable: gộp mọi bảng cùng (bundle, locale) thành 1 file
    #   "<bundle>::<locale>.json" (vd "...bundle::en.json" -> xuất English.json),
    #   key đã join qua SharedTableData, dạng "Collection/Key" (BUG#2:
    #   join_string_tables_grouped thay cho _join_string_tables phẳng cũ),
    #   key thiếu ở locale nhưng có trong SharedTableData thì điền "" để
    #   tập key giống hệt nhau giữa các locale; nguồn bundle hiện ở
    #   FileOutcome.source.
    # - Bảng ngôn ngữ có cấu trúc: LUÔN 1 file mỗi mã ĐẦY ĐỦ (BUG#2)
    #   ("<đơn vị>.langtable.monobehaviour.<code>.json", key = tên term thật,
    #   giữ chuỗi rỗng) — không bao giờ trộn các cột vào 1 file, không đi qua
    #   đường quét chuỗi phẳng (đường đó bỏ chuỗi rỗng -> lệch cột). Đoạn
    #   ".langtable" để KHÔNG đụng tên file generic cùng đơn vị + ngôn ngữ
    #   ("...monobehaviour.<lang>.json" — đụng tên thì dict ảo ghi đè mất
    #   bảng, lỗi đã gặp khi kiểm chứng 2026-11-17); hậu tố
    #   ".monobehaviour.<lang>.json" giữ nguyên để nhóm __mono__ + đặt tên
    #   xuất vẫn nhận ra. Mỗi biến thể (zh-CN, zh-TW...) có file riêng theo
    #   _language_source_all_columns — không gộp về mã gốc nữa.
    # - Bảng text dạng danh sách (sdk/text_data): LUÔN 1 file mỗi mã ĐẦY
    #   ĐỦ ("<đơn vị>.textdata.monobehaviour.<code>.json", key = key thật
    #   của entry, giữ nguyên thứ tự entry); nhóm fileName chỉ ghi log
    #   (metadata, không làm bẩn key). Trích fail-closed: 0 key trùng +
    #   0 giá trị rỗng mọi cột, sai thì raise (xem
    #   _text_data_list_virtual_files).
    # - Generic: 1 file "<đơn vị>.monobehaviour.json" cho mỗi .assets / CAB
    #   (vd "...resources.assets::resources.monobehaviour.json"); khi tách
    #   ngôn ngữ thì 1 file mỗi ngôn ngữ ("...monobehaviour.<lang>.json").
    #   Đường quét phẳng này CHỈ còn cho object không phải nguồn ngôn ngữ
    #   (không validate được cấu trúc bảng); xóa hẳn sẽ mất text thật không
    #   gắn nguồn trong prefab/UI — xem README/HANDOVER. Output locale mới
    #   (locales/) không dùng đường này nên không có key hậu tố .sN/.pN/.xN.
    #   BUG#2: file generic CHƯA TÁCH (1 file gộp, key #N) gắn origin_kind
    #   "generic_flat" nên merge một file LOẠI khỏi file locale (rác vứt
    #   hết theo quyết định người dùng). MERGE-MB-GENERIC: file generic
    #   ĐÃ TÁCH NGÔN NGỮ (1 file mỗi locale, key ổn định .p<path_id>)
    #   gắn "monobehaviour_lang" — merge chỉ nhận chúng khi game KHÔNG
    #   có nguồn ngôn ngữ có cấu trúc nào (I2/ST thắng trước, xem
    #   merge_language_outcomes); game có I2/ST giữ nguyên output.
    # Thứ tự key GIỮ NGUYÊN thứ tự gốc trong game (ý 7): dict Python giữ
    # thứ tự chèn, KHÔNG sort_keys ở đây (cũng không sorted() key ở các
    # bước sau — xem _mb_generic_kv, export_key_value_json). Riêng khối
    # StringTable: thứ tự key trong mỗi collection theo m_Id tăng dần và
    # thứ tự collection theo lần gặp đầu trong SharedTableData (khớp file
    # mẫu P0-3 — đo thật 2026-11-17, xem _fill_string_table_universe và
    # _st_key_sort_id); vì vậy vòng duyệt grouped dưới đây KHÔNG sorted()
    # lại theo chữ cái (sorted() cũ xếp collection Buildings trước
    # Policies, lệch mẫu). Chỉ sorted() ở vòng duyệt FILE/bundle là thứ
    # tự duyệt file, không ảnh hưởng thứ tự key trong file.
    if mb_tables:
        grouped, join_notes, join_conflicts, shared_keys = \
            join_string_tables_grouped(mb_tables, mb_shared)
        grouped, n_filled = _fill_string_table_universe(grouped, shared_keys)
        per_file: Dict[Tuple[str, str], Dict[str, str]] = {}
        for (bundle_path, loc, coll), kv in grouped.items():
            bucket = per_file.setdefault((bundle_path, loc), {})
            for k, v in kv.items():
                bucket["%s/%s" % (coll, k)] = v
        for (bundle_path, loc), kv in sorted(per_file.items()):
            if not kv:
                continue
            vpath = "%s::%s.json" % (bundle_path, loc)
            virtual[vpath] = json.dumps(kv, ensure_ascii=False, indent=1).encode("utf-8")
            virtual_origin[vpath] = "string_table"
            log("Unity: %s -> StringTable %s: %d chuỗi (gộp nhóm Collection/Key, xuất %s)"
                % (bundle_path, loc, len(kv), vpath))
        for n in join_notes:
            log("Unity: StringTable: %s" % n)
        if join_conflicts:
            log("Unity: StringTable: %d xung đột gộp nhóm (giữ bản đầu, xem log gộp)"
                % len(join_conflicts))
        if n_filled:
            log("Unity: StringTable: điền %d key rỗng từ SharedTableData "
                "(bản dịch thiếu, giữ tập key giống nhau giữa các locale)"
                % n_filled)
    if mb_lang_sources:
        for _vp, _b in _language_source_virtual_files(mb_lang_sources, log).items():
            virtual[_vp] = _b
            virtual_origin[_vp] = "language_source"
    if mb_text_data_lists:
        for _vp, _b in _text_data_list_virtual_files(mb_text_data_lists, log).items():
            virtual[_vp] = _b
            virtual_origin[_vp] = text_data.ORIGIN
    for (unit_path, assets_name), items in sorted(mb_generic_units.items()):
        res = _mb_generic_kv(items, split_languages=split_mono_languages)
        kept, dropped = res["kept"], res["dropped"]
        unit_label = unit_path + ("::" + assets_name if assets_name else "")
        if split_mono_languages:
            by_lang = res["by_lang"] or {}
            stats = res["stats"]
            for lang, lkv in sorted(by_lang.items()):
                if not lkv:
                    continue
                if assets_name:
                    stem = _sanitize_segment(os.path.splitext(os.path.basename(assets_name))[0]
                                             or "assets")
                    vpath = "%s::%s::%s.monobehaviour.%s.json" % (unit_path, assets_name, stem, lang)
                else:
                    stem = _sanitize_segment(os.path.splitext(os.path.basename(unit_path))[0]
                                             or "assets")
                    vpath = "%s::%s.monobehaviour.%s.json" % (unit_path, stem, lang)
                virtual[vpath] = json.dumps(lkv, ensure_ascii=False, indent=1).encode("utf-8")
                virtual_origin[vpath] = MB_LANG_ORIGIN
                log("Unity: %s -> MonoBehaviour [%s]: %d chuỗi (xuất %s)"
                    % (unit_label, lang, len(lkv), vpath))
            log("Unity: %s -> MonoBehaviour tách ngôn ngữ: %d chuỗi "
                "(lọc %d chuỗi rác; theo ngôn ngữ %s; key hậu tố ổn định .p<path_id>: %d; "
                "gán theo: mạnh %d, vị trí %d, mặc định %d; nhóm căn chỉnh: %d; "
                "term dùng làm key: %d)"
                % (unit_label, kept, dropped,
                   ", ".join("%s:%d" % kv for kv in sorted(stats["per_lang"].items())),
                   stats["stable_suffix_keys"],
                   stats["by_mechanism"]["strong"], stats["by_mechanism"]["position"],
                   stats["by_mechanism"]["default"],
                   stats.get("aligned_chunks", 0) + stats.get("aligned_groups", 0),
                   stats.get("term_keyed", 0)))
            continue
        kv = res["kv"]
        if not kv:
            continue
        if assets_name:
            stem = _sanitize_segment(os.path.splitext(os.path.basename(assets_name))[0]
                                     or "assets")
            vpath = "%s::%s::%s.monobehaviour.json" % (unit_path, assets_name, stem)
        else:
            stem = _sanitize_segment(os.path.splitext(os.path.basename(unit_path))[0]
                                     or "assets")
            vpath = "%s::%s.monobehaviour.json" % (unit_path, stem)
        virtual[vpath] = json.dumps(kv, ensure_ascii=False, indent=1).encode("utf-8")
        virtual_origin[vpath] = "generic_flat"
        log("Unity: %s -> MonoBehaviour: %d chuỗi (lọc %d chuỗi rác, xuất %s)"
            % (unit_label, kept, dropped, vpath))
    if virtual:
        layers.append(_Layer("", MemoryVFS(virtual, limits=limits), "virtual",
                             origin_map=virtual_origin))
    # Shared index {collection: {key: m_Id}} tu moi SharedTableData da gap
    # (cho sidecar provenance -> (E)); khong co thi {}.
    shared_index: Dict[str, Dict[str, int]] = {}
    for item in mb_shared:
        coll = item.get("collection") or ""
        if not coll:
            continue
        d = shared_index.setdefault(coll, {})
        for mid, key in (item.get("entries") or {}).items():
            if isinstance(key, str) and isinstance(mid, int):
                d[key] = mid
    return layers, shared_index


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


# ------------------------------------------------------------------ gộp 1 file
MERGE_CONFLICTS_CAP = 500  # số xung đột tối đa ghi chi tiết vào report/sidecar
# mỗi locale (còn lại chỉ đếm tổng; bản bị loại nào cũng đã liệt kê nguồn
# trong merge_sources của key được giữ — (E) ép theo bản giữ).
# BUG#2: chỉ outcome có nguồn CÓ CẤU TRÚC mới được gộp vào file locale.
# generic_flat (quét phẳng MonoBehaviour, key vị trí .s/.p) và other
# (plist/abi/config...) KHÔNG gộp — chúng là rác theo quyết định người dùng.
# MERGE-MB-GENERIC (2026-11-18): ngoại lệ DUY NHẤT là origin
# "monobehaviour_lang" — file MonoBehaviour generic ĐÃ TÁCH NGÔN NGỮ
# thành công (1 file mỗi locale, key ổn định .p<path_id>, không hậu tố
# #N phụ thuộc thứ tự) — và CHỈ khi game KHÔNG có nguồn ngôn ngữ có
# cấu trúc nào (language_source / string_table): I2/ST thắng trước, nên
# game đã có I2/ST (9K/PB3/EK2) không có MB nào lọt vào, chuẩn md5 giữ
# nguyên; game chỉ có text trong MB generic (vd PvZ Fusion) mới được
# gộp (xem _mb_admissible trong merge_language_outcomes).
# TEXT-DATA-LIST (2026-11-19): bảng text dạng danh sách (sdk/text_data)
# là nguồn có cấu trúc như I2/ST — xuất file ảo riêng mỗi locale, được
# gộp vào file locale, và sự hiện diện của nó chặn MB-generic.
MERGE_ORIGINS = frozenset({"language_source", "string_table", "text_asset",
                           "text_data_list"})
# Nguồn ngôn ngữ CÓ CẤU TRÚC — sự hiện diện của chúng chặn MB-generic.
STRUCTURED_ORIGINS = frozenset({"language_source", "string_table",
                                "text_data_list"})


def _mb_admissible(origin_kind: str, has_structured: bool) -> bool:
    """Outcome MB tách ngôn ngữ có được gộp không (merge-mb-generic).

    Chỉ nhận origin monobehaviour_lang VÀ game không có nguồn có cấu
    trúc nào. generic_flat (file gộp chưa tách, key #N; dò chuỗi thô)
    không bao giờ đủ điều kiện — rác vẫn bị loại như cũ."""
    return origin_kind == MB_LANG_ORIGIN and not has_structured


def _is_mb_lang_file(full_path: str, layer) -> bool:
    """Đường dẫn có phải file ảo MB tách ngôn ngữ không (theo origin_map
    của lớp virtual mà _build_layers đã gắn)."""
    origin_map = getattr(layer, "origin_map", None) if layer is not None else None
    return bool(origin_map) and origin_map.get(full_path) == MB_LANG_ORIGIN


def _untagged_rescue_target(files: List[FileOutcome],
                            requested) -> Optional[str]:
    """Locale gộp cứu cho game KHÔNG CÓ locale gắn nhãn nào.

    Vd file JSON trần trong zip (game.json 3 key, locale None): nếu không
    cứu thì merge bị bỏ qua (_expand_merge_targets ra tập rỗng) + _export
    ở merge mode bỏ outcome chưa gộp + bridge lọc `f.exported` -> ra
    0 file dù total_entries = 3 (hồi quy e2e mod_search 2026-11-17: ngày
    2026-11-16 PASS "3 entry · 1 file · dropdown game.json", nay
    "3 entry · 0 file", dropdown rỗng, #btn-mod-load-extract không làm gì).

    Chỉ cứu khi KHÔNG có outcome nào gắn locale (game thật có locale
    gắn nhãn thì không đổi hành vi — đã đo: 9K/PB3/EK2 đều có outcome
    gắn locale, và outcome untagged của chúng là rác/config/animation
    vd RuntimeInitializeOnLoads 1806 entry, EK_Avatar_Animation 22313
    entry, tuyệt đối không được gộp bừa vào en.json) và có outcome chưa
    gắn locale thuộc MERGE_ORIGINS còn kv. Trả locale đầu tiên yêu cầu
    (sorted, vd "en" trong {"en","zh"}), hoặc None nếu không cứu."""
    if not requested:
        return None
    if any(f.locale is not None for f in files):
        return None
    if not any(f.locale is None and f.kv
               and f.origin_kind in MERGE_ORIGINS for f in files):
        return None
    return sorted(str(c).lower() for c in requested)[0]


def merge_language_outcomes(files: List[FileOutcome],
                            target_locales) -> Tuple[List[FileOutcome], List[Dict[str, Any]]]:
    """Gộp các outcome ngôn ngữ thành ĐÚNG 1 outcome mỗi locale (Joseph 23:35).

    BUG#2 (2026-11-17) — hợp đồng mới (cũ: gộp mọi outcome theo locale):
      - Chỉ gộp outcome có origin_kind thuộc MERGE_ORIGINS
        (language_source / string_table / text_asset / text_data_list).
        Outcome generic_flat hoặc other KHÔNG gộp, không lọt vào file locale.
      - MERGE-MB-GENERIC (2026-11-18): outcome monobehaviour_lang (MB
        generic đã tách ngôn ngữ thành công) CŨNG được gộp — nhưng CHỈ
        khi trong danh sách outcome KHÔNG có nguồn có cấu trúc nào
        (language_source / string_table): I2/ST thắng trước. Game đã có
        I2/ST (9K/PB3/EK2) giữ nguyên output byte-identical; game chỉ
        có text MB generic (vd PvZ Fusion) mới ra file locale từ MB.
        Outcome gộp toàn từ MB mang origin_kind monobehaviour_lang
        (nhãn nguồn riêng); thống kê mỗi locale có thêm "mb_keys" (số
        key lấy từ MB). File MB gộp chưa tách (generic_flat, key #N)
        và dò chuỗi thô (generic_flat) vẫn KHÔNG gộp như cũ.
      - Mỗi biến thể là 1 target riêng (zh-CN, zh-TW...). Outcome mang mã
        GỐC ("zh") được gộp làm fallback SAU các outcome biến thể của cùng
        target (vd bảng StringTable chỉ có cột "zh" thì cả zh-CN lẫn zh-TW
        đều lấy giá trị đó); biến thể luôn thắng mã gốc khi trùng key.
      - Trùng key, GIỐNG hệt giá trị: gộp 1 bản, đếm duplicates_identical
        (không mất chuỗi — giá trị giống hệt nhau từng byte).
      - Trùng key, bản giữ là "" (bản dịch thiếu, đã điền từ SharedTableData
        hoặc bảng ngôn ngữ) mà bản sau có giá trị thật: LẤY bản thật
        (đếm filled_empty) — "" không phải bản dịch, không được giữ để rồi
        che mất giá trị thật ở bundle khác. Ngược lại (bản giữ thật, bản
        sau "") thì bỏ qua bản sau, không tính xung đột.
      - Trùng key, KHÁC giá trị (cả hai đều thật): KHÔNG ghi đè im lặng —
        giữ bản xuất hiện đầu tiên, liệt kê toàn bộ bản xung đột (key +
        giá trị + nguồn của từng bản) vào conflicts của mục thống kê.
      - Outcome không thuộc target (vd file config/plist locale None) giữ
        nguyên, không gộp, không đổi thứ tự tương đối — việc loại chúng
        khỏi OUTPUT là trách nhiệm của _export ở chế độ merge_one_file,
        không phải của hàm gộp thuần này.
    Trả (danh_sách_outcome_mới, thống_kê_mỗi_locale). Outcome gộp có
    path "<merged:<lang>>" (lang dạng canonical, vd "zh-CN"), plugin
    "merge_one_file", merged=True, merged_from = tên file tách đã gộp
    (exported nếu có, không thì path), merge_sources = {key: nguồn của
    key đó} cho (E) ép ngược.
    """
    targets = {str(c).lower() for c in (target_locales or set())}
    if not targets:
        return list(files), []
    # MERGE-MB-GENERIC: I2/ST thắng trước — chỉ khi KHÔNG có outcome có
    # cấu trúc nào (kv rỗng không tính) thì MB tách ngôn ngữ mới được gộp.
    has_structured = any(
        f.origin_kind in STRUCTURED_ORIGINS and f.kv for f in files)

    def _admissible(f):
        if f.origin_kind in MERGE_ORIGINS:
            return True
        return _mb_admissible(f.origin_kind, has_structured)

    # Nhóm theo target: outcome biến thể trước, outcome mã gốc (fallback) sau.
    by_lang: Dict[str, List[FileOutcome]] = {}
    for f in files:
        if f.locale is None or not f.kv or not _admissible(f):
            continue
        loc = f.locale.lower()
        if loc in targets:
            by_lang.setdefault(loc, []).append(f)
    for target in sorted(targets):
        base = disc.locale_base(target)
        if base == target:
            continue
        for f in files:
            if (f.locale is not None and f.locale.lower() == base and f.kv
                    and _admissible(f)):
                by_lang.setdefault(target, []).append(f)
    if not by_lang:
        # Game không có locale gắn nhãn nào: gộp text chưa gắn locale
        # (thuộc MERGE_ORIGINS) vào locale đầu tiên yêu cầu thay vì bỏ
        # hết (xem _untagged_rescue_target). Game có locale thì nhánh này
        # không chạy (by_lang đã có nhóm) nên không đổi hành vi.
        _rescue = _untagged_rescue_target(files, targets)
        if _rescue:
            by_lang[_rescue] = [f for f in files
                                if f.locale is None and f.kv
                                and f.origin_kind in MERGE_ORIGINS]
    if not by_lang:
        return list(files), []
    merged_outcomes: Dict[str, FileOutcome] = {}
    stats: List[Dict[str, Any]] = []
    for lang in sorted(by_lang):
        group = by_lang[lang]
        canonical = disc.canonical_locale_code(lang)
        kv: Dict[str, str] = {}
        sources: Dict[str, str] = {}
        key_from_mb: Dict[str, bool] = {}
        dups = 0
        filled_empty = 0
        conflicts: List[Dict[str, Any]] = []
        entries_before = 0
        for f in group:
            entries_before += len(f.kv)
            for key, value in f.kv.items():
                if key not in kv:
                    kv[key] = value
                    sources[key] = f.source or f.path
                    key_from_mb[key] = f.origin_kind == MB_LANG_ORIGIN
                elif kv[key] == value:
                    dups += 1
                elif kv[key] == "" and value != "":
                    # Bản giữ chỉ là chỗ trống đã điền, bản sau có giá trị
                    # thật: lấy bản thật, không tính xung đột.
                    kv[key] = value
                    sources[key] = f.source or f.path
                    key_from_mb[key] = f.origin_kind == MB_LANG_ORIGIN
                    filled_empty += 1
                elif value == "":
                    # Bản sau chỉ là chỗ trống: bỏ qua, giữ bản thật.
                    continue
                else:
                    conflicts.append({
                        "key": key,
                        "kept": {"value": kv[key], "source": sources[key]},
                        "dropped": {"value": value, "source": f.source or f.path},
                    })
        # Nhãn nguồn riêng: file gộp toàn từ MB tách ngôn ngữ mang
        # origin monobehaviour_lang; file gộp toàn từ bảng text-data
        # mang origin text_data_list; gộp lẫn (vd TextAsset + MB, hoặc
        # StringTable + text-data) giữ text_asset như trước (không thể
        # lẫn I2/ST với MB-generic vì MB đã bị chặn khi có nguồn có
        # cấu trúc — xem has_structured ở trên).
        if all(f.origin_kind == MB_LANG_ORIGIN for f in group):
            merged_origin = MB_LANG_ORIGIN
        elif all(f.origin_kind == text_data.ORIGIN for f in group):
            merged_origin = text_data.ORIGIN
        else:
            merged_origin = "text_asset"
        out = FileOutcome("<%s:%s>" % ("merged", canonical), "merge_one_file", canonical,
                          entries=len(kv), ok=len(kv), kv=kv,
                          source=group[0].source or group[0].path,
                          merged=True,
                          merged_from=[(f.exported or f.path) for f in group],
                          merge_sources=sources,
                          origin_kind=merged_origin)
        merged_outcomes[lang] = out
        stats.append({
            "locale": canonical,
            "file": "%s.json" % canonical,
            "keys": len(kv),
            "entries_before": entries_before,
            "sources": [(f.exported or f.path) for f in group],
            "duplicates_identical": dups,
            "filled_empty": filled_empty,
            "mb_keys": sum(1 for v in key_from_mb.values() if v),
            "conflicts": conflicts[:MERGE_CONFLICTS_CAP],
            "conflicts_total": len(conflicts),
            "conflicts_truncated": len(conflicts) > MERGE_CONFLICTS_CAP,
        })
    # Outcome mới: giữ vị trí của outcome đầu tiên của mỗi nhóm gộp; các
    # outcome trong nhóm sau đó bị thay bằng outcome gộp (chỉ 1 lần). Một
    # outcome mã gốc có thể là fallback cho NHIỀU biến thể (vd bảng
    # StringTable "zh" cho cả zh-CN lẫn zh-TW) — khi tới vị trí của nó thì
    # đặt TẤT CẢ outcome gộp dùng nó làm đầu nhóm, theo thứ tự locale.
    firsts: Dict[int, List[str]] = {}
    for lang, group in by_lang.items():
        firsts.setdefault(id(group[0]), []).append(lang)
    consumed = {id(f) for group in by_lang.values() for f in group}
    placed: set = set()
    new_files: List[FileOutcome] = []
    for f in files:
        if id(f) in firsts:
            for lang in sorted(firsts[id(f)]):
                if lang not in placed:
                    placed.add(lang)
                    new_files.append(merged_outcomes[lang])
            continue
        if id(f) in consumed:
            continue
        new_files.append(f)
    return new_files, stats


def _expand_merge_targets(requested, present_locales, mb_locales=None) -> set:
    """Mở rộng tập locale cần gộp theo biến thể có thật trong dữ liệu (BUG#2).

    requested: tập mã chữ thường người dùng xin (vd {"en", "zh"}).
    present_locales: tập mã chữ thường có trong result.files.
    mb_locales: tập mã chữ thường có trên outcome MB tách ngôn ngữ
        (monobehaviour_lang) — chỉ truyền vào khi game KHÔNG có nguồn
        có cấu trúc (bên gọi đã kiểm tra); các locale này thành target
        dù không nằm trong requested (lọc mềm không chặn MB — xem
        run_extraction), để game chỉ có text MB ra đủ 1 file mỗi locale
        có dữ liệu (vd PvZ: en/vi/ro/fr/pt/zh).
    Quy tắc: mã xin là mã GỐC ("zh") mà dữ liệu có biến thể của nó
    ("zh-CN", "zh-TW") thì target là các biến thể đó (KHÔNG ra file
    "zh.json" nữa); chỉ khi dữ liệu không có biến thể nào thì target mới
    là chính mã gốc (vd game chỉ có bảng "zh" thì ra zh.json). Mã xin đã
    là biến thể ("zh-CN") thì chỉ target đúng mã đó.
    Ngoại lệ MB (merge-mb-generic): MB "zh" KHÔNG rõ biến thể nên ra CẢ
    zh-CN lẫn zh-TW (theo chuẩn của tool: xin zh thì ra cả 2 biến thể),
    không ra zh.json — outcome "zh" làm fallback cho cả 2 target như
    bảng StringTable "zh" (xem merge_language_outcomes)."""
    targets = set()
    present = {str(p).lower() for p in (present_locales or set())}
    for r in (requested or set()):
        r = str(r).lower()
        variants = {p for p in present
                    if p != r and disc.locale_base(p) == r}
        if variants:
            targets.update(variants)
        elif r in present:
            targets.add(r)
    for m in sorted({str(x).lower() for x in (mb_locales or set())}):
        if m == "zh":
            variants = {p for p in present
                        if p != "zh" and disc.locale_base(p) == "zh"}
            if variants:
                targets.update(variants)
            else:
                targets.update({"zh-cn", "zh-tw"})
            targets.discard("zh")
        elif m in present:
            targets.add(m)
    return targets


def _build_language_columns(layers, result: ExtractionResult, locales) -> None:
    """Bản kê theo từng cột bảng ngôn ngữ (BUG#2, tiêu chí 8).

    Đọc từ file ảo langtable trong lớp virtual: mỗi cột một mục {unit,
    code, keys, empty, status}. status: "exported" nếu có outcome đã gộp
    cho mã đó trong result.files; "filtered" nếu mã không thuộc tập lọc
    yêu cầu; "not_exported" nếu còn lại. Không đoán số — đếm thật từ file
    ảo (keys = số term, empty = số giá trị rỗng).
    MERGE-MB-GENERIC: liệt kê thêm cột của file MB tách ngôn ngữ
    (origin monobehaviour_lang, ".monobehaviour.<code>.json") — để cột
    MB đã gộp (vd PvZ vi/ro/fr/pt) không "mất tích" khỏi bản kê. Mã "zh"
    tính là exported khi có outcome gộp cho biến thể của nó (zh-CN /
    zh-TW); cột MB KHÔNG bị tính "filtered" khi đang được miễn lọc mềm
    (game không có nguồn có cấu trúc — xem run_extraction)."""
    merged_locales = {str(f.locale).lower() for f in result.files if f.merged}
    mb_merged_locales = {str(f.locale).lower() for f in result.files
                         if f.merged and f.origin_kind == MB_LANG_ORIGIN}
    has_structured = any(
        o in STRUCTURED_ORIGINS
        for layer in layers
        for o in (getattr(layer, "origin_map", None) or {}).values())

    def _col_exported(code, merged):
        """Cot da gop chua: dung ma locale (vd "en"), HOAC la cot GOC
        cua mot locale bien the da gop (vd cot "zh" lam fallback cho
        zh-CN/zh-TW — xem merge_language_outcomes). Chieu nguoc KHONG
        tinh: bien the (vd "en-gb") khong bao gio cap cho target goc
        "en", nen locale_in_wanted (khop ca 2 chieu) la sai o day — EK2
        en-gb/en-us tung bi danh "exported" nham vi the."""
        c = code.lower()
        if c in merged:
            return True
        return any(c == disc.locale_base(m) and c != m for m in merged)

    seen = set()
    for layer in layers:
        if layer.kind != "virtual":
            continue
        for vpath, origin in sorted(layer.origin_map.items()):
            if origin == "language_source":
                m = re.search(r"\.langtable\.monobehaviour\.(.+)\.json$", vpath)
            elif origin == text_data.ORIGIN:
                m = re.search(r"\.textdata\.monobehaviour\.(.+)\.json$", vpath)
            elif origin == MB_LANG_ORIGIN:
                # Chi liet ke cot MB khi game KHONG co nguon co cau truc
                # (MB la nguon chinh); game co I2/ST thi ban ke giu
                # nguyen nhu cu (khong them hang MB "filtered").
                if has_structured:
                    continue
                m = re.search(r"\.monobehaviour\.(.+)\.json$", vpath)
            else:
                continue
            if not m:
                continue
            code = m.group(1)
            unit = vpath.split("::")[0]
            key = (unit, code)
            if key in seen:
                continue
            seen.add(key)
            try:
                kv = json.loads(bytes(layer.vfs.read(vpath, None)).decode("utf-8"))
                if not isinstance(kv, dict):
                    kv = {}
            except Exception:
                kv = {}
            empty = sum(1 for v in kv.values() if v == "")
            if origin == MB_LANG_ORIGIN:
                exported = _col_exported(code, mb_merged_locales)
            else:
                exported = _col_exported(code, merged_locales)
            if exported:
                status = "exported"
            elif (locales is not None
                    and not disc.locale_in_wanted(code, locales)
                    and not (origin == MB_LANG_ORIGIN
                             and not has_structured)):
                status = "filtered"
            else:
                status = "not_exported"
            result.language_columns.append({
                "unit": unit, "code": code, "keys": len(kv),
                "empty": empty, "status": status,
            })


def _apply_merge(result: ExtractionResult, target_locales) -> None:
    """Áp merge_language_outcomes lên result.files (chạy trước _export).

    BUG#2: total_entries chỉ đếm key của các file locale ĐÃ GỘP (không đếm
    file rác passthrough — vd config/plist — vì chúng không nằm trong
    output ở chế độ gộp nữa)."""
    new_files, stats = merge_language_outcomes(result.files, target_locales)
    result.files = new_files
    result.merged_files = stats
    result.total_entries = sum(len(f.kv) for f in result.files if f.merged)


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
    wanted_paths = {str(p) for p in req.source_paths} if req.source_paths else None
    if wanted_paths:
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

    def _is_selected(full: str) -> bool:
        """Đường dẫn ứng viên có nằm trong phạm vi đã chọn không.

        Lỗi 2026-11-16 (phát hiện khi sửa lọc locale): lọc `inv` ở trên
        chỉ loại entry khỏi inventory, nhưng vòng sinh ứng viên vẫn quét
        lớp gốc (toàn bộ ZIP) nên file thường KHÔNG được chọn vẫn lọt vào
        làm ứng viên (vd chọn chỉ bundle vẫn ra settings.json — trước đây
        bị lọc locale che mất vì settings không rõ locale). Chặn ở đây:
        chỉ giữ ứng viên đúng bằng đường dẫn đã chọn, hoặc là entry con
        `w::...` của đường dẫn đã chọn (TextAsset trong bundle đã chọn,
        file trong container đã chọn). Lớp Unity ảo chỉ dựng từ `inv` đã
        lọc nên TextAsset của bundle không chọn không tới được đây.
        """
        if wanted_paths is None:
            return True
        return any(full == w or full.startswith(w + "::") for w in wanted_paths)

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
    # Tách MonoBehaviour theo từng chuỗi (mục A) khi có lọc ngôn ngữ (mềm
    # locales hoặc cứng req.locale): file ảo tách theo ngôn ngữ, không còn
    # file gộp trộn mọi ngôn ngữ. Không lọc -> file gộp cũ (tương thích ngược).
    split_mono = bool(locales) or (req.locale not in (None, "all"))
    layers, shared_index = _build_layers(root_vfs, inv, limits, backend, log,
                                         progress_fn=_progress, skipped=result.skipped,
                                         split_mono_languages=split_mono)
    result.shared_index = dict(shared_index or {})
    # MERGE-MB-GENERIC: game có nguồn ngôn ngữ có cấu trúc (I2/ST) trong
    # lớp virtual thì MB-generic KHÔNG được miễn lọc mềm, KHÔNG được gộp
    # (I2/ST thắng trước — giữ chuẩn 9K/PB3/EK2 byte-identical). Chỉ game
    # không có nguồn có cấu trúc nào mới miễn lọc cho file MB tách ngôn
    # ngữ, để merge gộp đủ mọi locale có dữ liệu (vd PvZ: vi/ro/fr/pt).
    _has_structured_unity = any(
        origin in STRUCTURED_ORIGINS
        for layer in layers
        for origin in (getattr(layer, "origin_map", None) or {}).values())
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
                # Chọn file: ứng viên ngoài phạm vi đã chọn thì loại (xem
                # _is_selected — lọc inv không đủ vì lớp gốc quét cả ZIP).
                if not _is_selected(full):
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
            if not _is_selected(full):
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
    # Thứ tự ưu tiên file Unity trước (xem _probe_priority): file text game
    # luôn được probe dù tổng ứng viên vượt giới hạn.
    probed: List[Dict[str, Any]] = []
    _probe_keys = sorted(raw, key=lambda k: (_probe_priority(k[1])[0], k[1], k[0]))
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
            if code is not None and not disc.locale_in_wanted(code, locales):
                # MERGE-MB-GENERIC: file MB tách ngôn ngữ của game KHÔNG
                # có nguồn có cấu trúc được MIỄN lọc mềm — locale của nó
                # là dữ liệu của game (vd PvZ có sẵn cột vi trong MB),
                # không phải lựa chọn của user; giữ lại để merge gộp đủ
                # 1 file mỗi locale có dữ liệu. Game có I2/ST thì lọc
                # như cũ (không miễn).
                if (not _has_structured_unity
                        and _is_mb_lang_file(item["full"], item.get("layer"))):
                    kept_probed.append(item)
                    continue
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
        # KHÔNG BAO GIỜ trả ok=False mà errors rỗng (lỗi 2026-11-16:
        # banner web chỉ đọc parsed.error/code nên hiện "Lỗi không rõ").
        n_loc = sum(1 for s in (result.skipped or [])
                    if "không phải" in (s.get("reason") or ""))
        if locales is not None:
            result.errors.append({"path": "", "code": "E_NO_CANDIDATE",
                                  "message": "không còn ứng viên nào sau bước lọc: "
                                             "bộ lọc ngôn ngữ %s đã loại %d mục "
                                             "(xem skipped trong báo cáo); thử tắt bộ lọc "
                                             "ngôn ngữ rồi trích xuất lại"
                                             % (wanted_txt, n_loc)})
        else:
            result.errors.append({"path": "", "code": "E_NO_CANDIDATE",
                                  "message": "không tìm thấy ứng viên trích xuất nào "
                                             "trong file (đã bỏ qua %d mục; xem skipped/log "
                                             "trong báo cáo)" % len(result.skipped or [])})
        result.decision = "none"
        result.report = _build_report(req, result, logs, t0)
        return result

    # -- bước 5: scoring + arbitration (theo từng locale) -------------------
    # Nhóm theo locale; file không rõ locale thì mỗi file một nhóm riêng
    # (không gom chung rồi chỉ chọn một).
    groups: Dict[Any, List[Dict[str, Any]]] = {}
    for item in probed:
        code, _conf, _ev = disc.detect_locale(item["full"], locale_table)
        if ".monobehaviour." in item["full"]:
            # File MonoBehaviour đã tách theo ngôn ngữ (mục A): mỗi file là
            # một nhóm riêng, KHÔNG tranh cử theo locale với TextAsset hay
            # StringTable cùng locale — vd TextAsset "en-gb" của Passpartout
            # từng làm cả nhóm en rơi vào ASK (0.71 hòa) rồi mất trắng trên
            # web (không có ask_fn). Ngôn ngữ đã tách giữ ở _mono_locale
            # để _arbitrate_groups gắn vào group_locale.
            key = ("__mono__", item["full"])
            item["_mono_locale"] = code
        else:
            key = code if code else ("__file__", item["full"])
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
        # Web không có ask_fn nên kẹt ASK cũng phải báo rõ lý do, không
        # được trả ok=False mà errors rỗng (banner "Lỗi không rõ").
        need = ", ".join(sorted(str(g.get("locale") or "?") for g in ask_groups))
        result.errors.append({"path": "", "code": "E_ASK_NEEDED",
                              "message": "cần chọn tay: %d nhóm chưa quyết được vì các "
                                         "định dạng/plugin ngang điểm (locale: %s); bản web "
                                         "chưa hỗ trợ chọn tay ở bước này — thử chọn đúng "
                                         "file ở chế độ Chọn tay"
                                         % (len(ask_groups), need)})
        result.decision = "ask"
        result.candidates = ask_groups
        result.report = _build_report(req, result, logs, t0)
        return result
    if not chosen:
        result.errors.append({"path": "", "code": "E_NO_CANDIDATE",
                              "message": "không ứng viên nào vượt ngưỡng trọng tài "
                                         "(xem skipped trong báo cáo)"})
        result.decision = "none"
        result.report = _build_report(req, result, logs, t0)
        return result

    # -- bước 6+7: locale resolution, parse -> IR -> export + verify -------
    checkpoint = _load_checkpoint(req)
    # SPEED-EXTRACT (2026-11-18): source_hash chỉ phục vụ checkpoint/resume
    # (khớp hash ở dưới + _save_checkpoint trong _export khi KHÔNG gộp).
    # Chế độ web mặc định (merge_one_file=True, resume=False) không dùng tới
    # mà _source_hash vẫn băm TOÀN BỘ file nguồn (9K 173 MB: 0,51 s [ĐÃ ĐO
    # THẬT] bằng profile; PB3 482 MB ~1,4 s) — bỏ qua khi không cần.
    source_hash = ""
    if req.out_dir and (req.resume or not req.merge_one_file):
        source_hash = _source_hash(req.source, root_vfs)
    tables: List[LocTable] = []
    all_locales: set = set()   # mọi locale có trong game (trước khi lọc --locale)
    # Outcome không gắn locale (loc None) bị lọc cứng (--locale của batch)
    # loại: giữ lại TẠM, quyết định sau vòng lặp — nếu game không có locale
    # gắn nhãn nào (toàn bộ text không gắn locale, vd chuỗi MonoBehaviour)
    # thì giữ lại làm text mặc định; nếu game CÓ locale gắn nhãn thì bỏ
    # (giữ đúng test test_locale_filter_en_only: batch en chỉ ra file en).
    deferred_untagged: List[Tuple] = []
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
                # Lọc mềm nhiều locale (req.locales): chỉ loại entry có
                # locale ĐÃ XÁC ĐỊNH mà không thuộc tập lọc; entry không rõ
                # locale (loc_code None — vd file config/plist) vẫn GIỮ
                # LẠI (lỗi 2026-11-16: trước đây `None not in {en,zh}`
                # loại cả file không có ngôn ngữ, khiến IPA thật chỉ còn
                # config thì ra 0 file + banner "Lỗi không rõ").
                # MERGE-MB-GENERIC: entry MB tách ngôn ngữ của game không
                # có nguồn có cấu trúc được MIỄN lọc mềm (như ở bước
                # probe) — lọc cứng req.locale ở dưới vẫn giữ nguyên.
                _okind = _origin_kind_for(
                    item["full"], item["layer"], rec.plugin_id)
                if (locales is not None and loc_code is not None
                        and not disc.locale_in_wanted(loc_code, locales)
                        and not (_okind == MB_LANG_ORIGIN
                                 and not _has_structured_unity)):
                    result.skipped.append({"path": item["full"],
                                           "reason": "locale %s không phải %s"
                                                     % (loc_code or "không xác định", wanted_txt)})
                    continue
                outcome = FileOutcome(item["full"], rec.plugin_id,
                                      loc_code,
                                      entries=len(kv), ok=len(kv), kv=dict(kv),
                                      editable=bool(rec.manifest.can_write),
                                      source_kind="raw_scan" if rec.plugin_id == "generic_strings"
                                      else "verified",
                                      origin_kind=_okind,
                                      warnings=["dùng checkpoint, không parse lại"],
                                      sha256=file_sha,
                                      source=_source_of(item["full"]))
                outcome.exported = reused.get("exported", {}).get(loc)
                if req.locale not in (None, "all") and loc_code != req.locale:
                    # Lọc cứng loại entry không gắn locale: hoãn quyết định
                    # (cứu lại sau vòng lặp nếu game không có locale gắn
                    # nhãn nào); entry gắn nhãn khác locale yêu cầu thì bỏ.
                    if loc_code is None:
                        deferred_untagged.append(("reused", outcome))
                    continue
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
            # locale thuộc tập lọc; locale ĐÃ XÁC ĐỊNH mà khác thì bỏ qua
            # kèm lý do — KHÔNG fail cả đợt khi một locale vắng mặt (khác
            # req.locale cứng ở dưới). Entry KHÔNG XÁC ĐỊNH được locale
            # (loc None — vd file config/plist) vẫn GIỮ LẠI: file không có
            # ngôn ngữ không thể gọi là "không phải en/zh" (lỗi 2026-11-16:
            # trước đây lọc loại cả config, IPA thật chỉ còn config thì ra
            # 0 file + banner "Lỗi không rõ").
            # MERGE-MB-GENERIC: entry MB tách ngôn ngữ của game không có
            # nguồn có cấu trúc được MIỄN lọc mềm (như ở bước probe);
            # lọc cứng req.locale ở dưới vẫn giữ nguyên.
            _okind = _origin_kind_for(
                item["full"], item["layer"], rec.plugin_id,
                flavor=(table.schema_hint or {}).get("flavor"))
            if (locales is not None and loc is not None
                    and not disc.locale_in_wanted(loc, locales)
                    and not (_okind == MB_LANG_ORIGIN
                             and not _has_structured_unity)):
                result.skipped.append({"path": item["full"],
                                       "reason": "locale %s không phải %s"
                                                 % (loc or "không xác định", wanted_txt)})
                continue
            if req.locale not in (None, "all") and loc != req.locale:
                # Như nhánh checkpoint ở trên: entry không gắn locale thì
                # hoãn quyết định tới sau vòng lặp (cứu lại nếu game không
                # có locale gắn nhãn nào); entry gắn nhãn khác thì bỏ.
                if loc is None:
                    deferred_untagged.append(("parsed", (table, rec, item, loc, entries, file_sha)))
                continue
            outcome = _outcome_for(table, rec, item["full"], loc, entries)
            outcome.sha256 = file_sha
            outcome.origin_kind = _okind
            result.files.append(outcome)
            result.total_entries += len(outcome.kv)
            result.generated_keys.extend(
                e.key for e in entries if e.key_origin in ("generated", "rowindex"))
    if _exp_total:
        _progress("export", _exp_total, _exp_total)
    if req.locale not in (None, "all"):
        wanted = req.locale
        tagged = {h for h in all_locales if h is not None}
        if not tagged:
            # Game không có locale gắn nhãn nào: toàn bộ text không gắn
            # locale (vd chuỗi MonoBehaviour của Unity) — giữ lại các file
            # đã hoãn làm text mặc định của game thay vì bỏ hết rồi fail
            # E_LOCALE_NOT_FOUND (lỗi batch 2026-11-16: batch extract en
            # trên data.unity3d fail dù trích xuất đơn — lọc mềm — ra 85 file).
            for kind, payload in deferred_untagged:
                if kind == "reused":
                    outcome = payload
                    result.files.append(outcome)
                    result.total_entries += len(outcome.kv)
                else:
                    table, rec, item, loc, entries, file_sha = payload
                    outcome = _outcome_for(table, rec, item["full"], loc, entries)
                    outcome.sha256 = file_sha
                    outcome.origin_kind = _origin_kind_for(
                        item["full"], item["layer"], rec.plugin_id,
                        flavor=(table.schema_hint or {}).get("flavor"))
                    result.files.append(outcome)
                    result.total_entries += len(outcome.kv)
                    result.generated_keys.extend(
                        e.key for e in entries if e.key_origin in ("generated", "rowindex"))
            if deferred_untagged:
                log("locale %s: game không có locale gắn nhãn, giữ %d file không gắn locale"
                    % (wanted, len(deferred_untagged)))
        elif wanted not in tagged:
            result.errors.append({"path": "", "code": "E_LOCALE_NOT_FOUND",
                                  "message": "game không có locale %s; có sẵn: %s"
                                             % (wanted, ", ".join(sorted(tagged)))})
            result.ok = False
            result.decision = "none"
            result.report = _build_report(req, result, logs, t0)
            return result
    if not result.files:
        # 0 file KHÔNG phải lỗi khó hiểu: ghi rõ vì sao + gợi ý sửa
        # (lỗi 2026-11-16: nhánh này return mà không append error nào
        # nên banner web hiện "Lỗi không rõ").
        n_sk = len(result.skipped or [])
        n_loc = sum(1 for s in (result.skipped or [])
                    if "không phải" in (s.get("reason") or ""))
        hint = ("; thử tắt bộ lọc ngôn ngữ rồi trích xuất lại"
                if n_loc else "; xem danh sách đã bỏ qua / log trong kết quả trích xuất")
        filt = ("; trong đó %d mục do bộ lọc ngôn ngữ %s"
                % (n_loc, wanted_txt)) if locales is not None else ""
        result.errors.append({"path": "", "code": "E_NO_FILES",
                              "message": "không trích xuất được file nào (quyết định: "
                                         "none; đã bỏ qua %d mục%s%s)"
                                         % (n_sk, filt, hint)})
        result.decision = "none"
        result.report = _build_report(req, result, logs, t0)
        return result

    # Gộp thành 1 file mỗi ngôn ngữ (merge_one_file, Joseph 23:35): chỉ khi
    # người dùng bật + có tập locale cần gộp (lọc mềm req.locales hoặc lọc
    # cứng req.locale). Không lọc ngôn ngữ thì không gộp (không có locale
    # đích để gộp theo). Chạy TRƯỚC _export để file ghi ra đĩa đã là file
    # gộp; IR/report dùng result.files sau gộp.
    # BUG#2: target mở rộng theo biến thể có thật (xin "zh" -> zh-CN +
    # zh-TW, không ra zh.json), tính từ locale thật của outcome.
    if req.merge_one_file:
        _merge_targets = locales
        if _merge_targets is None and req.locale not in (None, "all"):
            _merge_targets = {str(req.locale).lower()}
        _requested = set(_merge_targets) if _merge_targets else None
        if _merge_targets:
            _present = {str(f.locale).lower() for f in result.files if f.locale}
            # MERGE-MB-GENERIC: thêm mọi locale có trên outcome MB tách
            # ngôn ngữ (đã miễn lọc mềm ở trên) — CHỈ khi game không có
            # nguồn có cấu trúc nào; merge_language_outcomes kiểm tra
            # lại quyền ưu tiên này một lần nữa trước khi gộp.
            _mb_locales = {str(f.locale).lower() for f in result.files
                           if f.origin_kind == MB_LANG_ORIGIN and f.locale and f.kv}
            _has_structured_files = any(
                f.origin_kind in STRUCTURED_ORIGINS and f.kv
                for f in result.files)
            _mb_arg = (_mb_locales if _mb_locales and not _has_structured_files
                       else None)
            _merge_targets = _expand_merge_targets(
                _merge_targets, _present, _mb_arg)
        if not _merge_targets:
            # Game không có locale gắn nhãn nào (vd chỉ có file JSON trần):
            # _expand_merge_targets ra tập rỗng vì không có locale nào
            # trong dữ liệu -> cứu bằng cách gộp text chưa gắn locale vào
            # locale đầu tiên yêu cầu (xem _untagged_rescue_target).
            _rescue = _untagged_rescue_target(result.files, _requested)
            if _rescue:
                _merge_targets = {_rescue}
        if _merge_targets:
            _apply_merge(result, _merge_targets)
            for _ms in result.merged_files:
                log("gộp 1 file %s: %d key từ %d file (trùng giống hệt đã gộp: %d; "
                    "xung đột giữ bản đầu: %d)"
                    % (_ms["file"], _ms["keys"], len(_ms["sources"]),
                       _ms["duplicates_identical"], _ms["conflicts_total"]))

    # BUG#2: bản kê theo từng cột bảng ngôn ngữ (để không còn cột "mất
    # tích" không dấu vết). Tính sau khi gộp để status "exported" khớp
    # đúng file locale cuối cùng.
    _build_language_columns(layers, result, locales)

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
                # Nhóm "__mono__": locale lấy từ ngôn ngữ đã tách (mục A),
                # không phải từ key nhóm (key là tuple nên code None).
                item["group_locale"] = code if code is not None else item.get("_mono_locale")
                chosen.append(item)
                log("chọn %s cho locale %s (%s, %.2f; chỉ có một ứng viên)"
                    % (item["full"], item["group_locale"] or "?",
                       item["plugin"].plugin_id, s))
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
            # (fix strings-arbitration 2026-11-17): nhóm hỏi mà ứng viên
            # xếp đầu (theo tie-break: app chính trước, Localizable
            # trước) là file .strings của app chính thì chọn luôn — không
            # hạ ngưỡng auto chung (0.80) và không đổi điểm. Lý do: file
            # .strings dạng text chỉ đạt 0.77 vì sniff text 0.8 < magic
            # bplist 0.9, không phải vì sai nội dung; trên web không có
            # người để hỏi nên để nhóm rơi sẽ mất text của chính game.
            # Chỉ áp khi KHÔNG ứng viên nào đạt auto (nhóm ASK) — không
            # ghi đè quyết định AUTO; và chỉ khi ứng viên đầu là file
            # app chính (không trong .framework/.bundle/.appex/PlugIns).
            top = decision.options[0]
            if (top.path.lower().endswith(".strings")
                    and arb.is_main_app_path(top.path)):
                pick = next(it for it in items
                            if it["plugin"].plugin_id == top.adapter_id
                            and it["full"] == top.path)
                pick["group_locale"] = code
                chosen.append(pick)
                log("chọn %s cho locale %s (%s, %.2f; nhóm hỏi nhưng ứng viên đầu là .strings của app chính)"
                    % (pick["full"], code or "?", pick["plugin"].plugin_id, top.score))
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


# Plugin nào parse ra nội dung KHÔNG phải text localizable (cấu hình,
# metadata...): origin_kind "other" — không gộp vào file locale (BUG#2).
_NON_LOCALE_PLUGINS = frozenset({"apple_plist"})


def _origin_kind_for(full_path: str, layer, plugin_id: str,
                     flavor: Optional[str] = None) -> str:
    """origin_kind của một outcome (BUG#2).

    Ưu tiên bảng origin của lớp virtual (_build_layers đã gắn thẻ từng
    file ảo: language_source / string_table / generic_flat /
    monobehaviour_lang / text_asset).
    File thường (không nằm trong lớp virtual): generic_strings là dò chuỗi
    thô -> generic_flat; plugin cấu hình (apple_plist) -> other; còn lại
    là file locale có cấu trúc -> text_asset.

    Ngoại lệ (follow-up dò text 2026-11-17): file .strings dạng plist
    (flavor "strings-bplist" hoặc "strings-xml-plist") là chuỗi vỏ app
    iOS (quyền mic/camera, Game Center, IAP, App Tracking...), KHÔNG
    phải text game -> origin_kind "other", không gộp vào file locale
    chính. Nếu gộp sẽ làm vỡ chuẩn đã chốt (9K en.json 2252 key
    byte-identical vs mẫu sẽ thành 2253 key vì InfoPlist.strings có 1
    key; PB3 2348 -> 2349). File .strings dạng text giữ nguyên
    "text_asset" (vẫn gộp như trước, vd CSJ 32 key của Tom). flavor lấy
    từ table.schema_hint ở chỗ parse; đường checkpoint (resume) không
    có flavor nên giữ hành vi cũ ở đó.

    Ngoại lệ của ngoại lệ (fix strings-arbitration 2026-11-17): chỉ
    InfoPlist.strings mới là metadata vỏ app. Localizable.strings là
    bảng localization chính của app -> text game, được gộp (Tom:
    en.lproj/Localizable.strings bplist 64 key; 9K/PB3/EK2 có
    Localizable.strings bplist 0 key nên không đổi output). Bảng
    .strings bplist/XML khác (tên SDK/vendor: BGNativeAd, HyBid...) giữ
    "other" để text SDK không lọt vào file locale của game."""
    origin_map = getattr(layer, "origin_map", None) if layer is not None else None
    if origin_map and full_path in origin_map:
        return origin_map[full_path]
    if flavor in ("strings-bplist", "strings-xml-plist"):
        if full_path.rsplit("/", 1)[-1].lower() == "localizable.strings":
            return "text_asset"
        return "other"
    if plugin_id == "generic_strings":
        return "generic_flat"
    if plugin_id in _NON_LOCALE_PLUGINS:
        return "other"
    return "text_asset"


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
    """Ghi JSON key-value + report (trừ dry-run / không có out_dir).

    BUG#2: khi merge_one_file=True (chế độ web mặc định), out_dir CHỈ
    chứa các file locale đã gộp — outcome chưa gộp (file rác, config,
    plist...) bị bỏ qua; không ghi sidecar provenance, conflicts.json,
    checkpoint hay report vào out_dir (chúng vẫn có trong result.report
    nội bộ để debug)."""
    if req.out_dir is None or req.dry_run:
        return
    import os as _os
    _os.makedirs(req.out_dir, exist_ok=True)
    game = _game_name(req.source)
    checkpoint_files: Dict[str, Any] = {}
    used_names: set = set()
    merge_mode = bool(req.merge_one_file)
    for outcome in result.files:
        # BUG#2: chế độ gộp chỉ xuất outcome đã gộp (file locale); mọi
        # outcome khác (rác, config, plist...) vứt hết, không ghi file.
        if merge_mode and not outcome.merged:
            continue
        if not outcome.kv:
            continue
        if outcome.locale:
            fname = disc.export_filename(outcome.locale)
        else:
            fname = _sanitize_segment(_source_stem(outcome.path)) + ".json"
        # Gộp 1 file (merge_one_file): tên file gộp là đúng mã ngôn ngữ
        # CHUẨN (en.json / zh-CN.json / zh-TW.json — khớp file mẫu của
        # Joseph), KHÔNG phải tên hiển thị (English.json) và không lẫn
        # với file tách per-unit (resources.monobehaviour.en.json).
        if outcome.merged and outcome.locale:
            fname = "%s.json" % disc.canonical_locale_code(outcome.locale)
        # (A) File MonoBehaviour đã tách theo ngôn ngữ: tên xuất riêng theo
        # đơn vị + ngôn ngữ (vd resources.monobehaviour.en.json) thay vì
        # English.json — tên đó đã dùng cho file StringTable cùng locale
        # (English.json) và là 2 nguồn khác nhau, không được ghi đè lẫn
        # nhau. Chỉ áp cho file ảo tách ngôn ngữ (đuôi
        # ".monobehaviour.<lang>.json", lang khớp outcome.locale); file gộp
        # cũ (locale None) giữ nguyên tên như trước.
        # (A) ... Chỉ áp cho file tách chưa gộp: outcome đã gộp dùng tên
        # file gộp chuẩn ở trên (en.json / zh-CN.json), không ghi đè về
        # tên per-unit (path của outcome gộp là path của nguồn đầu tiên).
        if outcome.locale and not outcome.merged and ".monobehaviour." in outcome.path:
            m = re.search(r"\.monobehaviour\.([a-z]{2,3}(?:-[A-Za-z]+)?)\.json$",
                          outcome.path)
            if m and m.group(1).lower() == outcome.locale.lower():
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
        # Outcome gộp là dẫn xuất từ các file tách per-unit (không có file
        # nguồn thật để checkpoint khóa theo sha) nên KHÔNG ghi vào
        # checkpoint: chạy lại vẫn parse lại từ đầu rồi gộp lại, tránh
        # checkpoint phình vì chứa cả bản gộp (~MB) mà không bao giờ tái dùng.
        if not outcome.merged:
            ent = checkpoint_files.setdefault(outcome.path, {"sha256": outcome.sha256,
                                                             "locales": {}, "exported": {}})
            ent["locales"][outcome.locale or ""] = dict(outcome.kv)
            ent["exported"][outcome.locale or ""] = fname
    # BUG#2: chế độ gộp (merge_mode) KHÔNG ghi sidecar provenance,
    # conflicts.json, checkpoint hay report vào out_dir — output zip chỉ
    # chứa các file locale đã gộp. Xung đột vẫn nằm trong result.report
    # nội bộ (merged_files) để debug.
    if not merge_mode:
        # Sidecar provenance cho file gộp (cho (E) ép ngược): từng key lấy từ
        # nguồn nào (bundle/.assets), gộp từ những file tách nào, xung đột nào
        # đã giữ bản đầu. Format "beartea-merge-provenance/1"; file gộp chính
        # vẫn giữ dạng phẳng sạch như file en.json mẫu (không nhét provenance
        # vào làm bẩn key).
        for outcome in result.files:
            if not outcome.merged or not outcome.exported:
                continue
            stat = next((m for m in result.merged_files
                         if m.get("locale") == outcome.locale), None)
            sidecar = {
                "format": "beartea-merge-provenance/1",
                "game": game,
                "source": str(req.source),
                "locale": outcome.locale,
                "file": outcome.exported,
                "merged_from": list(outcome.merged_from),
                "keys": dict(outcome.merge_sources),
                "shared_index": dict(result.shared_index or {}),
                "duplicates_identical": (stat or {}).get("duplicates_identical", 0),
                "conflicts": (stat or {}).get("conflicts", []),
                "conflicts_total": (stat or {}).get("conflicts_total", 0),
            }
            try:
                with open(os.path.join(req.out_dir, "%s.provenance.json"
                                       % os.path.splitext(outcome.exported)[0]),
                          "wb") as fh:
                    fh.write(json.dumps(sidecar, ensure_ascii=False, indent=1).encode("utf-8"))
            except OSError:
                pass
        # conflicts.json: mọi xung đột key khi gộp (cùng key, giá trị khác nhau)
        # — key, cả hai giá trị + nguồn, quy tắc giữ bản đầu. Trước đây xung đột
        # chỉ nằm trong sidecar/report; file riêng này để không ai bỏ sót. Chỉ
        # ghi khi có xung đột (tránh thêm file rỗng vào mọi lần xuất).
        if req.out_dir is not None and result.merged_files:
            all_conflicts: List[Dict[str, Any]] = []
            total = 0
            truncated = False
            for stat in result.merged_files:
                total += stat.get("conflicts_total", 0)
                truncated = truncated or bool(stat.get("conflicts_truncated"))
                for c in stat.get("conflicts", []):
                    entry = {"locale": stat.get("locale"),
                             "file": stat.get("file"),
                             "key": c.get("key"),
                             "kept": c.get("kept"),
                             "dropped": c.get("dropped"),
                             "rule": "keep-first"}
                    all_conflicts.append(entry)
            if total > 0:
                try:
                    with open(os.path.join(req.out_dir, "conflicts.json"),
                              "wb") as fh:
                        fh.write(json.dumps(
                            {"format": "beartea-merge-conflicts/1",
                             "game": game,
                             "source": str(req.source),
                             "conflicts_total": total,
                             "conflicts_truncated": truncated,
                             "conflicts": all_conflicts},
                            ensure_ascii=False, indent=1).encode("utf-8"))
                except OSError:
                    pass
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
        "merged_files": list(result.merged_files),
        "language_columns": list(result.language_columns),
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
    layers, _shared_index = _build_layers(root_vfs, inv, lim, backend, lambda m: None,
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
