"""Dịch vụ công cụ font 4 chế độ (đợt tổng cuối, nhóm font).

- `font scan <path>`: quét font trong file/thư mục/ZIP/IPA/bundle Unity,
  báo font nào thiếu ký tự tiếng Việt (đọc bảng cmap thật).
- `font rebuild <path> [--font TÊN] [--donor FILE]`: (1) không có --font:
  gộp glyph tiếng Việt còn thiếu từ font nguồn vào MỌI font thiếu;
  (2) có --font: dựng đúng 1 font chính rồi dùng đúng 1 font này cho
  toàn bộ game (thay mọi font khác bằng font chính đã dựng).
- `font replace <path> [--font TÊN] --with FILE`: (3) thay font bằng
  font TTF có tiếng Việt do người dùng đưa vào (kiểm độ phủ trước).
- `font bundle <path> [--font TÊN]`: (4) thay font bằng font nhập sẵn
  của PB3 (Be Vietnam Pro, OFL, đủ 134/134 chữ cái tiếng Việt, 142 mục kiểm).

Ghi ngược: dry-run mặc định; chỉ ghi khi --allow-write. Không ghi đè
nguồn: file rời -> workspace/output/<Game>/, ZIP/IPA -> dựng lại
container mới (sdk.containers.rebuild_zip), thư mục -> copy cây sang
thư mục đích rồi thay trong bản copy.

GIỚI HẠN TRUNG THỰC (in ra khi chạy + ghi trong handoff/final_font.md):
- Chế độ (1) nặng nhất: gộp glyph + tái tạo atlas SDF của TextMeshPro
  trên điện thoại rất nặng; atlas SDF tái tạo được bằng tính năng thử
  nghiệm tmp_sdf_regen (sdk/sdf.py + bridge, đo MAE so với atlas gốc),
  gặp font SDF thì báo rõ + hướng dùng nút tái tạo SDF (chế độ 3/4 là
  phương án dự phòng).
- Font nhúng trong SerializedFile (Font object, class 128): đợt này chỉ
  liệt kê, KHÔNG sửa được bytes object; thay thế chỉ làm được với file
  .ttf rời hoặc entry .ttf trong container ZIP/IPA.
- Chỉ gộp được font TrueType có outline glyf; font CFF (.otf) đọc được
  cmap nhưng không gộp được; font collection (.ttc) chưa hỗ trợ.

Tiến độ theo luật #152–153: % thật + ETA thật, tăng dần từng 1%
(SteadyProgress, cùng mẫu một dòng với thanh Overall; màu qua
core/color.py, dấu tiếng Việt đầy đủ).
"""
from __future__ import annotations

import os
import shutil
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from core import safe_io
from core.color import paint, status, title, wrap_text
from core.errors import EXIT_OK, PB3Error
from core.limits import Limits
from core.progress import SteadyProgress, step_pcts
from sdk import containers as cont
from sdk import font as font_sdk
from ui.screens import default_print, term_width

#: Đuôi file font rời được quét.
FONT_EXTENSIONS = (".ttf", ".otf")
#: Entry container lớn hơn ngưỡng này thì bỏ qua khi quét (không đọc hết RAM).
SCAN_MAX_ENTRY_BYTES = 32 * 1024 * 1024


@dataclass
class FontFound:
    """Một font tìm thấy khi quét."""
    display: str            # tên hiển thị (đường dẫn/vpath)
    kind: str               # 'file' | 'zip-entry' | 'unity-embedded'
    family: str = ""
    font_kind: str = ""     # 'ttf' | 'otf' | 'ttc' | 'unknown' | 'embedded'
    num_glyphs: int = 0
    total: int = 0
    covered: int = 0
    missing: List[int] = field(default_factory=list)
    data: Optional[bytes] = None   # bytes font (chỉ khi kind != unity-embedded)
    path_id: Optional[int] = None  # Font object trong .assets (nếu có)
    note: str = ""

    @property
    def is_full(self) -> bool:
        return not self.missing

    def summary_vi(self) -> str:
        if self.kind == "unity-embedded":
            base = "%s: font nhúng trong Unity (Font object%s)" % (
                self.display, (" path_id=%d" % self.path_id) if self.path_id else "")
            if self.family:
                base += " — tên %s" % self.family
            return base + " — " + self.note
        if self.font_kind == "otf":
            return ("%s: font CFF (.otf, %s) — đọc được cmap, KHÔNG gộp được "
                    "(chỉ thay thế bằng chế độ 3/4)" % (self.display, self.family))
        rep = font_sdk.CoverageReport(family=self.family or self.display,
                                      kind=self.font_kind, num_glyphs=self.num_glyphs,
                                      total=self.total, covered=self.covered,
                                      missing=self.missing, where=self.display)
        return rep.summary_vi()


@dataclass
class ScanResult:
    fonts: List[FontFound] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    sdf_suspects: List[str] = field(default_factory=list)


class _ProgressAdapter:
    """progress_fn(done, total): bước qua thanh Overall (nếu có), không thì SteadyProgress.

    Cùng mẫu với services/translate_tool.py::_ProgressAdapter (luật #152–153):
    tăng dần từng 1%, ghi chú hiện số thật đã xong/tổng.
    """

    def __init__(self, app, label: str = "font"):
        self._label = label
        self._ov = getattr(app, "overall", None) if app is not None else None
        self._steady = None
        self._shown_pct = 0

    def __call__(self, done: int, total: int) -> None:
        if total <= 0:
            return
        new_pct = int(min(1.0, max(0.0, done / total)) * 100)
        if self._ov is not None:
            for pct in step_pcts(self._shown_pct, new_pct):
                interp = int(round(pct / 100.0 * total))
                self._ov.update(frac=pct / 100.0,
                                note="%s %d/%d" % (self._label, interp, total))
                self._ov.render()
            self._shown_pct = new_pct
            return
        if self._steady is None:
            self._steady = SteadyProgress(self._label, total)
        self._steady.update(done, note=self._label)

    def finish(self, note: Optional[str] = None) -> None:
        if self._steady is not None:
            self._steady.finish(note)


# ------------------------------------------------------------------ đường dẫn
def bundled_font_path(root) -> Path:
    """Đường dẫn font nhập sẵn (chế độ 4) theo gốc dự án."""
    path = Path(str(root)) / "fonts" / font_sdk.BUNDLED_FONT_NAME
    if not path.is_file():
        raise PB3Error("E_INPUT_NOT_FOUND",
                       "thiếu font nhập sẵn: %s (chưa có trong gói)" % path,
                       where=str(path))
    return path


def bundled_license_path(root) -> Path:
    path = Path(str(root)) / "fonts" / font_sdk.BUNDLED_FONT_LICENSE
    if not path.is_file():
        raise PB3Error("E_INPUT_NOT_FOUND", "thiếu file giấy phép font nhập sẵn: %s" % path,
                       where=str(path))
    return path


def _game_name(path: str) -> str:
    stem = Path(str(path)).stem or "game"
    return "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in stem) or "game"


def _default_out_dir(app, source: str) -> str:
    out_root = getattr(getattr(app, "paths", None), "output", None)
    if out_root is None:
        return os.path.join(os.getcwd(), "workspace", "output", _game_name(source))
    return str(Path(str(out_root)) / _game_name(source))


def _exc_text(exc: PB3Error) -> str:
    """Chuỗi lỗi ngắn cho người dùng: detail riêng (nếu có), không thì message chung."""
    return exc.detail or exc.message_vi


def _format_exc(exc: PB3Error) -> str:
    return "[%s] %s" % (exc.code, _exc_text(exc))


def _parse_chars_arg(raw: Optional[str]) -> Optional[List[int]]:
    """--chars '1EA0-1EF9,103,111' -> [codepoints]. None khi không truyền."""
    if not raw:
        return None
    out: List[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a, 16), int(b, 16) + 1))
        else:
            out.append(int(part, 16))
    return sorted(set(out))


# ------------------------------------------------------------------ quét
def _check_font_bytes(data: bytes, display: str, kind: str) -> FontFound:
    found = FontFound(display=display, kind=kind)
    fk = font_sdk.detect_font_kind(data)
    found.font_kind = fk
    if fk == "ttc":
        found.note = "font collection (.ttc) chưa hỗ trợ — tách từng font con ra file .ttf riêng"
        return found
    if fk == "unknown":
        raise PB3Error("E_CORRUPT", "không phải font TrueType/OpenType (magic lạ)",
                       where=display)
    info = font_sdk.parse_font(data, where=display)
    rep = font_sdk.coverage(info)
    found.family = info.family
    found.num_glyphs = info.num_glyphs
    found.total = rep.total
    found.covered = rep.covered
    found.missing = rep.missing
    found.data = bytes(data)
    if font_sdk.is_sdf_name(display):
        found.note = ("tên gợi ý atlas SDF (TextMeshPro) — " + font_sdk.SDF_LIMIT_MSG)
    return found


def _scan_unity_bytes(data: bytes, display: str, result: ScanResult) -> None:
    """Liệt kê Font object nhúng trong .assets/bundle (chỉ liệt kê, không sửa)."""
    from sdk import unity as uni
    try:
        if uni.detect_bundle(data) is not None:
            bundle = uni.parse_bundle(data, where=display)
            for entry in bundle.entries:
                if not entry.is_serialized:
                    continue
                try:
                    payload = bundle.get_entry(entry.name)
                except PB3Error:
                    continue
                _scan_unity_bytes(payload, display + "::" + entry.name, result)
            return
        sf = uni.parse_serialized(data, where=display)
    except PB3Error as exc:
        result.warnings.append("%s: không đọc được Unity (%s)" % (display, exc.code))
        return
    for path_id, name in font_sdk.list_unity_font_objects(sf):
        note = ("font nhúng trong SerializedFile — đợt này KHÔNG sửa được "
                "bytes Font object; " + font_sdk.SDF_LIMIT_MSG
                if font_sdk.is_sdf_name(name or "")
                else "font nhúng trong SerializedFile — đợt này KHÔNG sửa được "
                     "bytes Font object (chưa có parser/writer cho Font object); "
                     "dùng chế độ (3)/(4) khi trích được file .ttf rời")
        result.fonts.append(FontFound(display=display, kind="unity-embedded",
                                      family=name or "", font_kind="embedded",
                                      path_id=path_id, note=note))


def scan_fonts(source: str, *, required: Optional[List[int]] = None,
               progress_fn: Optional[Callable[[int, int], None]] = None) -> ScanResult:
    """Quét font trong file/thư mục/ZIP/IPA/bundle Unity. Không ghi gì."""
    result = ScanResult()
    src = str(source)
    if not os.path.exists(src):
        raise PB3Error("E_INPUT_NOT_FOUND", "không có file/thư mục: %s" % src, where=src)
    candidates: List[Tuple[str, str, Optional[bytes]]] = []  # (display, kind, data|None)
    if os.path.isdir(src):
        for dirpath, _dirs, files in os.walk(src):
            for fn in sorted(files):
                full = os.path.join(dirpath, fn)
                low = fn.lower()
                if low.endswith(FONT_EXTENSIONS):
                    candidates.append((full, "file", None))
                elif low.endswith((".assets", ".unity3d")):
                    candidates.append((full, "unity", None))
    elif os.path.isfile(src):
        low = src.lower()
        if low.endswith(FONT_EXTENSIONS):
            candidates.append((src, "file", None))
        elif low.endswith((".assets", ".unity3d")):
            candidates.append((src, "unity", None))
        elif zipfile.is_zipfile(src):
            vfs = cont.open_vfs(src)
            for name in vfs.iter_entries():
                nlow = name.lower()
                if nlow.endswith(FONT_EXTENSIONS):
                    candidates.append((name, "zip-entry", None))
                elif nlow.endswith((".assets", ".unity3d")):
                    candidates.append((name, "unity-zip", None))
            # Giữ vfs để đọc entry sau (đọc ngay cho gọn).
            for i, (display, kind, _d) in enumerate(list(candidates)):
                if kind == "zip-entry":
                    try:
                        data = vfs.read(display)
                    except PB3Error as exc:
                        result.warnings.append("%s: không đọc được (%s)" % (display, exc.code))
                        candidates[i] = (display, "skip", None)
                        continue
                    if len(data) > SCAN_MAX_ENTRY_BYTES:
                        result.warnings.append(
                            "%s: quá lớn (%d byte), bỏ qua" % (display, len(data)))
                        candidates[i] = (display, "skip", None)
                        continue
                    candidates[i] = (display, kind, data)
                elif kind == "unity-zip":
                    try:
                        data = vfs.read(display)
                    except PB3Error as exc:
                        result.warnings.append("%s: không đọc được (%s)" % (display, exc.code))
                        candidates[i] = (display, "skip", None)
                        continue
                    candidates[i] = (display, kind, data)
        else:
            raise PB3Error("E_INPUT_NOT_FOUND",
                           "không nhận diện được nguồn font: %s (cần .ttf/.otf, thư mục, "
                           "ZIP/IPA, .assets/.unity3d)" % src, where=src)
    total = len(candidates)
    done = 0
    for display, kind, data in candidates:
        try:
            if kind == "skip":
                pass
            elif kind in ("unity", "unity-zip"):
                if data is not None:
                    raw = data
                else:
                    with open(display, "rb") as fh:
                        raw = fh.read()
                _scan_unity_bytes(raw, display, result)
            else:
                if data is not None:
                    raw = data
                else:
                    with open(display, "rb") as fh:
                        raw = fh.read()
                found = _check_font_bytes(raw, display, kind)
                if found.note and font_sdk.is_sdf_name(display):
                    result.sdf_suspects.append(display)
                result.fonts.append(found)
        except PB3Error as exc:
            result.warnings.append("%s: %s" % (display, _format_exc(exc)))
        done += 1
        if progress_fn is not None:
            progress_fn(done, total)
    if progress_fn is not None and total == 0:
        progress_fn(0, 0)
    return result


def _match_font(fonts: List[FontFound], wanted: str) -> FontFound:
    """Tìm font theo tên family hoặc đường dẫn/vpath (khớp đúng hoặc chứa)."""
    want = (wanted or "").strip().lower()
    for f in fonts:
        if f.display.lower() == want or f.family.lower() == want:
            return f
    hits = [f for f in fonts
            if want in f.display.lower() or want in f.family.lower()]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise PB3Error("E_INPUT_NOT_FOUND",
                       "không tìm thấy font %r trong game (có: %s)"
                       % (wanted, ", ".join(f.family or f.display for f in fonts) or "không có font"),
                       where=wanted)
    raise PB3Error("E_INPUT_NOT_FOUND",
                   "tên font %r khớp nhiều font: %s (ghi rõ hơn)"
                   % (wanted, ", ".join(f.display for f in hits)), where=wanted)


# ------------------------------------------------------------------ thao tác ghi
@dataclass
class FontOpResult:
    mode: str
    changed: List[Tuple[str, int, int, str]] = field(default_factory=list)
    skipped: List[Tuple[str, str]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    written: List[str] = field(default_factory=list)
    dry_run: bool = True


def _read_donor(donor_path: Optional[str], root) -> bytes:
    """Bytes font nguồn để gộp (mặc định: font nhập sẵn của PB3)."""
    path = donor_path or str(bundled_font_path(root))
    if not os.path.isfile(path):
        raise PB3Error("E_INPUT_NOT_FOUND", "không có file font nguồn: %s" % path, where=path)
    with open(path, "rb") as fh:
        return fh.read()


def _check_replacement_font(data: bytes, display: str,
                            required: List[int]) -> font_sdk.CoverageReport:
    """Kiểm font thay thế (chế độ 3/4): phải đủ ký tự tiếng Việt mới được thay."""
    info = font_sdk.parse_font(data, where=display)
    rep = font_sdk.CoverageReport(family=info.family, kind=info.kind,
                                  num_glyphs=info.num_glyphs, total=len(required),
                                  covered=len(required) - len(
                                      [c for c in required if c not in info.cmap]),
                                  missing=[c for c in required if c not in info.cmap],
                                  where=display)
    if rep.missing:
        raise PB3Error(
            "E_CORRUPT",
            "font thay thế %s thiếu %d ký tự tiếng Việt (%s) — chọn font khác đủ chữ"
            % (display, len(rep.missing),
               ", ".join(font_sdk.format_codepoint(c) for c in rep.missing[:8])),
            where=display)
    return rep


def _apply_to_source(source: str, replacements: Dict[str, bytes],
                     out_dir: str, allow_write: bool) -> List[str]:
    """Ghi các font đã thay vào đích (không ghi đè nguồn). Trả đường dẫn đã ghi."""
    src = str(source)
    written: List[str] = []
    if not allow_write:
        return written
    if os.path.isfile(src) and src.lower().endswith(FONT_EXTENSIONS):
        os.makedirs(out_dir, exist_ok=True)
        for _display, data in replacements.items():
            rel = os.path.basename(src)
            written.append(str(safe_io.write_output_bytes(out_dir, rel, data)))
        return written
    if os.path.isdir(src):
        # Thư mục đích: chưa tồn tại thì dùng đúng tên; đã có thì .v1/.v2...
        # (không makedirs trước — copytree tự tạo thư mục đích).
        dest = Path(out_dir)
        if dest.exists():
            dest = safe_io.versioned_path(dest)
        dest_dir = str(dest)
        shutil.copytree(src, dest_dir)
        for display, data in replacements.items():
            rel = os.path.relpath(display, src)
            target = os.path.join(dest_dir, rel)
            safe_io.atomic_write_bytes(target, data)
            written.append(target)
        return written
    if zipfile.is_zipfile(src):
        os.makedirs(out_dir, exist_ok=True)
        dest = str(safe_io.versioned_path(
            os.path.join(out_dir, os.path.basename(src))))
        cont.rebuild_zip(src, dest, replacements, verify=True)
        written.append(dest)
        return written
    raise PB3Error("E_NOT_IMPLEMENTED",
                   "chưa ghi ngược được nguồn dạng này (chỉ file .ttf rời, thư mục, ZIP/IPA)",
                   where=src)


def _closure_size(donor: bytes, donor_info: font_sdk.FontInfo,
                  cps: List[int]) -> int:
    """Số glyph sẽ copy khi gộp `cps` (kể cả component của glyph composite)."""
    seen = set()
    queue = [donor_info.cmap[cp] for cp in cps]
    while queue:
        gid = queue.pop(0)
        if gid == 0 or gid in seen:
            continue
        seen.add(gid)
        raw = font_sdk.glyph_data(donor, donor_info, gid)
        queue.extend(font_sdk.composite_components(raw))
    return len(seen)


def _out_dir_for(app, source: str, out_dir: Optional[str]) -> str:
    if out_dir:
        return str(out_dir)
    if app is not None:
        return _default_out_dir(app, source)
    return os.path.join(os.getcwd(), "workspace", "output", _game_name(source))


def _plan_targets(scan: ScanResult, font_name: Optional[str],
                  for_merge: bool) -> List[FontFound]:
    """Font rời sẽ thao tác: theo --font, hoặc toàn bộ font rời tìm thấy."""
    loose = [f for f in scan.fonts if f.kind in ("file", "zip-entry")]
    if font_name:
        return [_match_font(loose, font_name)]
    if for_merge:
        return [f for f in loose if f.font_kind == "ttf" and not f.is_full]
    return [f for f in loose if f.font_kind in ("ttf", "otf")]


def rebuild_fonts(source: str, *, font_name: Optional[str] = None,
                  donor_path: Optional[str] = None, donor_bytes: Optional[bytes] = None,
                  out_dir: Optional[str] = None, allow_write: bool = False,
                  required: Optional[List[int]] = None, app=None,
                  progress_fn: Optional[Callable[[int, int], None]] = None) -> FontOpResult:
    """Chế độ (1)/(2): gộp glyph tiếng Việt còn thiếu vào font.

    (1) font_name=None: gộp vào mọi font TTF thiếu chữ.
    (2) font_name=<tên>: dựng đúng 1 font chính, rồi dùng đúng 1 font này
    cho toàn bộ game (thay mọi font khác bằng font chính đã dựng).
    """
    req = required or font_sdk.required_vietnamese_chars()
    root = getattr(getattr(app, "paths", None), "root", None) or os.getcwd()
    out = _out_dir_for(app, source, out_dir)
    prog = _ProgressAdapter(app, "quét font") if progress_fn is None else None
    scan = scan_fonts(source, progress_fn=(prog or progress_fn))
    if prog is not None:
        prog.finish("quét font xong")
    result = FontOpResult(mode="rebuild-main" if font_name else "rebuild-all",
                          dry_run=not allow_write)
    result.warnings.extend(scan.warnings)
    donor = donor_bytes if donor_bytes is not None else _read_donor(donor_path, root)
    donor_info = font_sdk.parse_font(donor, where="font nguồn")
    if donor_info.kind != "ttf" or not donor_info.has_glyf:
        raise PB3Error("E_UNSUPPORTED_FORMAT",
                       "font nguồn không phải TrueType có outline glyf — không gộp được",
                       where="font nguồn")
    targets = _plan_targets(scan, font_name, for_merge=True)
    if font_name and targets:
        picked = targets[0]
        if picked.font_kind != "ttf":
            raise PB3Error("E_UNSUPPORTED_FORMAT",
                           "font %s là %s — chỉ gộp được font TrueType (.ttf); "
                           "dùng chế độ (3)/(4) để thay thế"
                           % (picked.display, picked.font_kind), where=picked.display)
        if picked.is_full:
            result.warnings.append("font %s đã đủ ký tự tiếng Việt — không cần gộp"
                                   % (picked.family or picked.display))
            return result
    if not targets:
        result.warnings.append("không có font TTF nào thiếu ký tự tiếng Việt — không cần gộp")
        for f in scan.fonts:
            if f.kind == "unity-embedded":
                result.skipped.append((f.display, "font nhúng Unity — không sửa được"))
        return result
    # Tổng số glyph sẽ copy (kể cả component composite) để thanh tiến độ
    # có tổng thật, luật #152.
    main = targets[0] if font_name else None
    merge_jobs: List[Tuple[FontFound, List[int]]] = []
    total_glyphs = 0
    for f in targets:
        todo = [cp for cp in f.missing if cp in donor_info.cmap]
        merge_jobs.append((f, todo))
        total_glyphs += _closure_size(donor, donor_info, todo)
    merge_prog = _ProgressAdapter(app, "gộp glyph") if progress_fn is None else None
    merged: Dict[str, bytes] = {}
    glyph_counter = [0]

    for f, todo in merge_jobs:
        if not todo:
            result.skipped.append((f.display, "font nguồn cũng thiếu ký tự cần gộp"))
            continue
        base = glyph_counter[0]

        def _on_glyph(done: int, _total: int, _base=base) -> None:
            cur = _base + done
            if merge_prog is not None:
                merge_prog(cur, max(total_glyphs, 1))
            elif progress_fn is not None:
                progress_fn(cur, max(total_glyphs, 1))

        before = len(f.data or b"")
        try:
            res = font_sdk.merge_glyphs(f.data, donor, todo, where=f.display,
                                        progress_fn=_on_glyph)
        except PB3Error as exc:
            result.skipped.append((f.display, _format_exc(exc)))
            continue
        glyph_counter[0] += res.glyphs_copied
        merged[f.display] = res.data
        result.warnings.extend("%s: %s" % (f.display, w) for w in res.warnings)
        result.changed.append((f.display, before, len(res.data),
                               "gộp +%d glyph (%s)" % (
                                   res.glyphs_copied,
                                   ", ".join(font_sdk.format_codepoint(c)
                                             for c in res.added[:3]))))
    if merge_prog is not None:
        merge_prog.finish("gộp glyph xong")
    if font_name and main is not None and main.display in merged:
        # Chế độ (2): mọi font khác dùng đúng 1 font chính đã dựng.
        main_bytes = merged[main.display]
        for f in scan.fonts:
            if f.kind not in ("file", "zip-entry") or f.display == main.display:
                continue
            if f.font_kind not in ("ttf", "otf"):
                continue
            merged[f.display] = main_bytes
            result.changed.append((f.display, len(f.data or b""), len(main_bytes),
                                   "dùng font chính %s" % (main.family or main.display)))
    for f in scan.fonts:
        if f.kind == "unity-embedded":
            result.skipped.append((f.display, "font nhúng Unity — không sửa được"))
    if merged:
        result.written = _apply_to_source(source, merged, out, allow_write)
    return result


def replace_fonts(source: str, *, font_name: Optional[str] = None,
                  new_font_bytes: Optional[bytes] = None,
                  new_font_path: Optional[str] = None,
                  out_dir: Optional[str] = None, allow_write: bool = False,
                  required: Optional[List[int]] = None,
                  app=None) -> FontOpResult:
    """Chế độ (3)/(4): thay font bằng font TTF đủ tiếng Việt (người dùng đưa / nhập sẵn)."""
    req = required or font_sdk.required_vietnamese_chars()
    out = _out_dir_for(app, source, out_dir)
    if new_font_bytes is None:
        if not new_font_path or not os.path.isfile(new_font_path):
            raise PB3Error("E_INPUT_NOT_FOUND",
                           "cần file font thay thế (--with FILE)", where=new_font_path or "")
        with open(new_font_path, "rb") as fh:
            new_font_bytes = fh.read()
        display_new = new_font_path
    else:
        display_new = "font thay thế"
    rep = _check_replacement_font(new_font_bytes, display_new, req)
    prog = _ProgressAdapter(app, "quét font")
    scan = scan_fonts(source, progress_fn=prog)
    prog.finish("quét font xong")
    result = FontOpResult(mode="replace", dry_run=not allow_write)
    result.warnings.extend(scan.warnings)
    targets = _plan_targets(scan, font_name, for_merge=False)
    if not targets:
        result.warnings.append("không tìm thấy font rời nào để thay "
                               "(font nhúng Unity không thay được trong đợt này)")
        for f in scan.fonts:
            if f.kind == "unity-embedded":
                result.skipped.append((f.display, "font nhúng Unity — không sửa được"))
        return result
    replacements = {f.display: bytes(new_font_bytes) for f in targets}
    for f in targets:
        result.changed.append((f.display, len(f.data or b""), len(new_font_bytes),
                               "thay bằng %s (%s, đủ %d/%d ký tự)"
                               % (rep.family, display_new, rep.covered, rep.total)))
    for f in scan.fonts:
        if f.kind == "unity-embedded":
            result.skipped.append((f.display, "font nhúng Unity — không sửa được"))
    result.written = _apply_to_source(source, replacements, out, allow_write)
    return result


def bundle_fonts(source: str, *, font_name: Optional[str] = None,
                 out_dir: Optional[str] = None, allow_write: bool = False,
                 app=None) -> FontOpResult:
    """Chế độ (4): thay font bằng font nhập sẵn của PB3 (Be Vietnam Pro, OFL)."""
    root = getattr(getattr(app, "paths", None), "root", None) or os.getcwd()
    path = bundled_font_path(root)
    with open(path, "rb") as fh:
        data = fh.read()
    result = replace_fonts(source, font_name=font_name, new_font_bytes=data,
                           new_font_path=str(path), out_dir=out_dir,
                           allow_write=allow_write, app=app)
    result.mode = "bundle"
    result.warnings.append("font nhập sẵn: Be Vietnam Pro (SIL OFL 1.1, "
                           "giấy phép kèm trong fonts/OFL.txt)")
    return result


# ------------------------------------------------------------------ in kết quả
def _print_scan(scan: ScanResult, out=print, width=None) -> None:
    w = width or term_width()
    if not scan.fonts:
        out(paint("Không tìm thấy font nào (file .ttf/.otf rời, hoặc Font object nhúng).", "warn"))
    for f in scan.fonts:
        for ln in wrap_text("  - " + f.summary_vi(), w):
            out(ln)
        if f.note and f.kind != "unity-embedded":
            for ln in wrap_text("      lưu ý: " + f.note, w):
                out(paint(ln, "warn"))
    for warn in scan.warnings:
        out(paint("  cảnh báo: " + warn, "warn"))
    if scan.sdf_suspects:
        out(paint("  Có %d font nghi là atlas SDF (TextMeshPro): %s — %s"
                  % (len(scan.sdf_suspects), ", ".join(scan.sdf_suspects),
                     font_sdk.SDF_LIMIT_MSG), "warn"))
    n_full = sum(1 for f in scan.fonts if f.kind != "unity-embedded" and f.is_full)
    n_loose = sum(1 for f in scan.fonts if f.kind != "unity-embedded")
    n_emb = sum(1 for f in scan.fonts if f.kind == "unity-embedded")
    out(status("pass" if n_loose else "fail",
               "tổng: %d font rời (%d đủ tiếng Việt), %d font nhúng Unity"
               % (n_loose, n_full, n_emb)))


def _print_result(result: FontOpResult, out=print, width=None) -> None:
    w = width or term_width()
    out(title("CÔNG CỤ FONT — chế độ %s" % result.mode))
    if not result.changed:
        out(paint("Không đổi font nào.", "warn"))
    for display, before, after, action in result.changed:
        for ln in wrap_text("  - %s: %d -> %d byte (%s)" % (display, before, after, action), w):
            out(ln)
    for display, reason in result.skipped:
        out(paint("  bỏ qua %s: %s" % (display, reason), "warn"))
    for warn in result.warnings:
        out(paint("  cảnh báo: " + warn, "warn"))
    if result.dry_run:
        out(paint("KHÔNG ghi file: đây là dry-run. Thêm --allow-write để ghi thật.", "warn"))
        return
    for path in result.written:
        out(status("pass", "đã ghi output: %s (không ghi đè nguồn)" % path))
    if not result.written and result.changed:
        out(paint("Chưa ghi gì (xem cảnh báo ở trên).", "warn"))


# ------------------------------------------------------------------ CLI
def run_cli(app, ns) -> int:
    action = str(getattr(ns, "action", "") or "")
    source = str(getattr(ns, "path", "") or "")
    if action not in ("scan", "rebuild", "replace", "bundle"):
        raise PB3Error("E_CONFIG_INVALID",
                       "chế độ font phải là scan|rebuild|replace|bundle (nhận %r)" % action,
                       where="font")
    if not source:
        raise PB3Error("E_CONFIG_INVALID", "cần đường dẫn nguồn (file/thư mục/ZIP/IPA)",
                       where="font")
    required = _parse_chars_arg(getattr(ns, "chars", None))
    out_dir = getattr(ns, "out", None)
    allow_write = bool(getattr(ns, "allow_write", False))
    font_name = getattr(ns, "font", None)
    if action == "scan":
        prog = _ProgressAdapter(app, "quét font")
        scan = scan_fonts(source, required=required, progress_fn=prog)
        prog.finish("quét font xong")
        _print_scan(scan)
        return EXIT_OK
    if action == "rebuild":
        result = rebuild_fonts(
            source, font_name=font_name, donor_path=getattr(ns, "donor", None),
            out_dir=out_dir, allow_write=allow_write,
            required=font_sdk.required_vietnamese_chars(required) if required else None,
            app=app)
        _print_result(result)
        return EXIT_OK
    if action == "replace":
        with_font = getattr(ns, "with_font", None)
        if not with_font:
            raise PB3Error("E_CONFIG_INVALID",
                           "chế độ replace cần --with FILE (font TTF có tiếng Việt)",
                           where="font replace")
        result = replace_fonts(source, font_name=font_name, new_font_path=with_font,
                               out_dir=out_dir, allow_write=allow_write,
                               required=font_sdk.required_vietnamese_chars(required)
                               if required else None, app=app)
        _print_result(result)
        return EXIT_OK
    result = bundle_fonts(source, font_name=font_name, out_dir=out_dir,
                          allow_write=allow_write, app=app)
    _print_result(result)
    return EXIT_OK


def add_arguments(subparser) -> None:
    """Cờ riêng cho `font` (gọi từ ui/cli.py khi đã có subparser `font`)."""
    subparser.add_argument("action", choices=["scan", "rebuild", "replace", "bundle"],
                           help="scan: quét độ phủ; rebuild: gộp glyph (1)/(2); "
                                "replace: thay bằng font người dùng đưa (3); "
                                "bundle: thay bằng font nhập sẵn của PB3 (4)")
    subparser.add_argument("path", help="file .ttf/.otf, thư mục, ZIP/IPA, .assets/.unity3d")
    subparser.add_argument("--font", default=None, metavar="TÊN",
                           help="font cần xử lý (tên family hoặc đường dẫn/vpath); "
                                "không ghi = toàn bộ font (rebuild: mọi font thiếu chữ)")
    subparser.add_argument("--donor", default=None, metavar="FILE",
                           help="font nguồn để gộp glyph khi rebuild (mặc định: "
                                "font nhập sẵn Be Vietnam Pro)")
    subparser.add_argument("--with", dest="with_font", default=None, metavar="FILE",
                           help="font TTF có tiếng Việt do người dùng đưa vào (chế độ replace)")
    subparser.add_argument("--out", default=None, metavar="DIR",
                           help="thư mục đích (mặc định workspace/output/<Game>)")
    subparser.add_argument("--allow-write", action="store_true", default=False,
                           help="ghi thật (mặc định chỉ dry-run)")
    subparser.add_argument("--chars", default=None, metavar="HEX",
                           help="ký tự bắt buộc thêm, dạng hex: '1EA0-1EF9,103,111'")


# ------------------------------------------------------------------ tương tác
def run_interactive(app, input_fn: Optional[Callable[[str], str]] = None,
                    say: Optional[Callable[[str], None]] = None):
    ask = input_fn if input_fn is not None else (lambda prompt="": input(prompt))
    out = say if say is not None else default_print
    out(title("CÔNG CỤ FONT (4 chế độ)"))
    out("quét/gộp/thay font để game hiển thị đúng tiếng Việt.")
    out("Giới hạn: atlas SDF của TextMeshPro tái tạo được bằng tính năng thử nghiệm (nút 'Tái tạo atlas SDF' ở mục TMP);")
    out("font nhúng trong bundle Unity chỉ liệt kê, chưa sửa được.")
    while True:
        out("  [1] Quét 1 loạt: font nào thiếu chữ thì dựng lại cho đầy đủ")
        out("  [2] Chọn 1 font chính, dựng nó rồi dùng cho toàn bộ game")
        out("  [3] Thay font bằng font TTF có tiếng Việt do bạn đưa vào")
        out("  [4] Thay bằng font nhập sẵn của PB3 (Be Vietnam Pro, OFL)")
        mode = ask("Chọn chế độ [1-4] (trống để thoát): ").strip()
        if not mode:
            return
        if mode not in ("1", "2", "3", "4"):
            out("Chế độ không hợp lệ, chọn 1-4.")
            continue
        path = safe_io.normalize_typed_path(
            ask("Đường dẫn file/thư mục/ZIP/IPA (trống để thoát): ").strip())
        if not path:
            return
        action = {"1": "rebuild", "2": "rebuild", "3": "replace", "4": "bundle"}[mode]
        font_name = None
        if mode in ("2", "3", "4"):
            font_name = ask("Font cần xử lý (tên family hoặc đường dẫn, trống = toàn bộ): "
                            ).strip() or None
        try:
            if action == "rebuild" and mode == "1":
                result = rebuild_fonts(path, app=app)
                _print_result(result, out)
                continue
            if action == "rebuild":
                result = rebuild_fonts(path, font_name=font_name, app=app)
                _print_result(result, out)
            elif action == "replace":
                with_font = safe_io.normalize_typed_path(
                    ask("Đường dẫn font TTF thay thế: ").strip())
                if not with_font:
                    out("Chưa có font thay thế.")
                    continue
                result = replace_fonts(path, font_name=font_name,
                                       new_font_path=with_font, app=app)
                _print_result(result, out)
            else:
                result = bundle_fonts(path, font_name=font_name, app=app)
                _print_result(result, out)
        except PB3Error as exc:
            out(_format_exc(exc))
            continue
        if not result.changed:
            continue
        ok = ask("Ghi thật? (y/N): ").strip().lower()
        if ok != "y":
            out("Đã hủy, chưa ghi gì.")
            continue
        try:
            if action == "rebuild":
                result = rebuild_fonts(path, font_name=font_name, app=app,
                                       allow_write=True)
            elif action == "replace":
                result = replace_fonts(path, font_name=font_name,
                                       new_font_path=with_font, app=app,
                                       allow_write=True)
            else:
                result = bundle_fonts(path, font_name=font_name, app=app,
                                      allow_write=True)
        except PB3Error as exc:
            out(_format_exc(exc))
            continue
        _print_result(result, out)
