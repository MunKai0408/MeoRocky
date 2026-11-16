"""Phát hiện môi trường + kiểm tra tương thích iSH (đặc tả 18.4).

TRUNG THỰC là yêu cầu cứng: một module chỉ 'có mặt' (find_spec) KHÔNG được báo là
'hoạt động'. Trạng thái module:
  absent           - không tìm thấy
  present_untested - có file/gói, CHƯA import thử (import UnityPy trên iSH mất ~1 phút)
  import_ok        - import thành công trong tiến trình con có giới hạn thời gian
  import_failed    - import lỗi/crash/quá thời gian (kèm lý do)
import_ok cũng chỉ nghĩa là import được, KHÔNG chứng minh đọc/ghi Unity đúng; việc
đó thuộc compatibility probe của Claude 05.
Chỉ test trực tiếp trong iSH mới là bằng chứng tương thích iSH: báo cáo luôn ghi
is_ish và nêu rõ khi chạy ngoài iSH.
"""
from __future__ import annotations

import importlib.util
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from . import MIN_PYTHON, VERSION, limits as _limits
from .paths import Paths
from .progress import display_width, fmt_bytes, truncate_display

# Module đích của Unity backend (không import khi khởi động; chỉ báo cáo trung thực)
TARGET_MODULES = ("lz4", "UnityPy")


@dataclass
class ModuleProbe:
    name: str
    status: str                 # absent | present_untested | import_ok | import_failed
    detail: str = ""
    version: Optional[str] = None


@dataclass
class EnvReport:
    pb3_version: str
    is_ish: bool
    ish_evidence: List[str]
    python: str
    python_ok: bool
    implementation: str
    system: str
    release: str
    machine: str
    alpine: Optional[str]
    cpu_count: Optional[int]
    stdout_encoding: str
    term_columns: int
    root: str
    root_is_expected: bool
    workspace_writable: bool
    workspace_detail: str
    disk_free: Optional[int]
    mem: Dict[str, int]
    peak_mem_kb: Optional[int]
    tomllib: bool
    mounts: List[str]
    modules: List[ModuleProbe] = field(default_factory=list)
    disabled_features: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


def detect_ish() -> (bool, List[str]):  # type: ignore[valid-type]
    ev: List[str] = []
    rel = platform.release()
    if "ish" in rel.lower():
        ev.append("kernel release '%s' chứa 'ish'" % rel)
    if os.path.isdir("/proc/ish"):
        ev.append("có /proc/ish")
    return (len(ev) > 0), ev


def _alpine() -> Optional[str]:
    try:
        return Path("/etc/alpine-release").read_text(encoding="ascii", errors="replace").strip() or None
    except OSError:
        return None


def probe_module(name: str, *, deep: bool = False, timeout_s: float = 120.0) -> ModuleProbe:
    """Mặc định chỉ find_spec (nhanh, không chạy mã của gói). deep=True import trong tiến trình con."""
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, ValueError, AttributeError) as exc:
        return ModuleProbe(name, "absent", "find_spec lỗi: %s" % type(exc).__name__)
    if spec is None:
        return ModuleProbe(name, "absent", "không tìm thấy trong sys.path")
    if not deep:
        return ModuleProbe(name, "present_untested", "có mặt nhưng chưa import thử")
    code = ("import importlib,sys;m=importlib.import_module(%r);"
            "print(getattr(m,'__version__',''))" % name)
    try:
        r = subprocess.run([sys.executable, "-c", code], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return ModuleProbe(name, "import_failed", "import quá %ds" % int(timeout_s))
    except OSError as exc:
        return ModuleProbe(name, "import_failed", "không chạy được tiến trình con (%s)" % type(exc).__name__)
    if r.returncode == 0:
        v = r.stdout.decode("utf-8", "replace").strip() or None
        return ModuleProbe(name, "import_ok", "import thành công (chưa kiểm thử chức năng)", v)
    if r.returncode < 0:
        return ModuleProbe(name, "import_failed", "tiến trình bị tín hiệu %d (module native crash?)" % -r.returncode)
    last = (r.stderr.decode("utf-8", "replace").strip().splitlines() or ["?"])[-1]
    return ModuleProbe(name, "import_failed", last[:160])


def _writable(d: Path) -> (bool, str):  # type: ignore[valid-type]
    # Probe ghi bằng tempfile (tên duy nhất, try/finally dọn): tên cố định
    # theo pid từng để lại file rác khi crash giữa ghi và unlink.
    import tempfile
    fd = None
    probe = None
    try:
        d.mkdir(parents=True, exist_ok=True)
        fd, probe = tempfile.mkstemp(prefix=".pb3-write-probe-", dir=str(d))
        os.write(fd, b"x")
        os.close(fd)
        fd = None
        return True, "ghi/xóa thử thành công"
    except OSError as exc:
        return False, "%s (errno=%s)" % (type(exc).__name__, exc.errno)
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if probe is not None:
            try:
                os.unlink(probe)
            except OSError:
                pass


def detect_environment(paths: Paths, *, deep_modules: bool = False,
                       on_module: Optional[Callable[[str, int, int], None]] = None) -> EnvReport:
    """on_module(name, i, total): gọi trước mỗi module khi deep=True để
    bên gọi báo tiến độ từng module (diagnose --deep từng chạy 'mờ')."""
    is_ish, ev = detect_ish()
    ok_w, det_w = _writable(paths.workspace)
    try:
        free = shutil.disk_usage(str(paths.workspace if paths.workspace.exists() else paths.root)).free
    except OSError:
        free = None
    mounts: List[str] = []
    try:
        mounts = sorted(e.name for e in os.scandir("/mnt") if e.is_dir())[:20]
    except OSError:
        pass
    py_ok = sys.version_info[:2] >= MIN_PYTHON
    rep = EnvReport(
        pb3_version=VERSION, is_ish=is_ish, ish_evidence=ev,
        python=platform.python_version(), python_ok=py_ok,
        implementation=platform.python_implementation(),
        system=platform.system(), release=platform.release(), machine=platform.machine(),
        alpine=_alpine(), cpu_count=os.cpu_count(),
        stdout_encoding=getattr(sys.stdout, "encoding", None) or "?",
        term_columns=shutil.get_terminal_size((48, 24)).columns,
        root=str(paths.root), root_is_expected=paths.at_expected_root(),
        workspace_writable=ok_w, workspace_detail=det_w, disk_free=free,
        mem=_limits.read_meminfo(), peak_mem_kb=_limits.peak_memory_kb(),
        tomllib=importlib.util.find_spec("tomllib") is not None, mounts=mounts,
    )
    rep.modules = []
    for i, m in enumerate(TARGET_MODULES):
        if on_module is not None:
            on_module(m, i, len(TARGET_MODULES))
        rep.modules.append(probe_module(m, deep=deep_modules))
    if not py_ok:
        rep.disabled_features.append("toàn bộ PB3 (cần Python >= %d.%d)" % MIN_PYTHON)
    if not ok_w:
        rep.disabled_features.append("mọi thao tác ghi (workspace không ghi được)")
    if not is_ish:
        rep.notes.append("Không thấy dấu hiệu iSH: kết quả test ở đây KHÔNG tính là bằng chứng tương thích iSH.")
    if not rep.root_is_expected:
        rep.notes.append("Thư mục dự án khác /root/pb3 (thư mục chuẩn, decisions #40): vẫn chạy được, nhưng nên đặt vào /root/pb3.")
    if not rep.tomllib:
        rep.notes.append("Python không có tomllib: dùng bộ đọc TOML tối giản của PB3 (bình thường trên 3.9).")
    for m in rep.modules:
        if m.status == "absent":
            rep.disabled_features.append("backend %s chưa cài: phần Unity (Claude 05) sẽ chỉ có Python thuần" % m.name)
    return rep


_STATUS_VI = {"absent": "KHÔNG CÓ", "present_untested": "có mặt, CHƯA kiểm thử",
              "import_ok": "import được (chưa test chức năng)", "import_failed": "import LỖI"}


def format_report(rep: EnvReport, width: int = 44) -> str:
    """Báo cáo văn bản tiếng Việt; mọi dòng bọc theo `width` cột (iPhone/iSH hẹp)."""
    import textwrap
    lines: List[str] = []

    def add(label: str, value: str) -> None:
        text = "%s: %s" % (label, value)
        lines.extend(textwrap.wrap(text, width=width, subsequent_indent="  ",
                                   break_long_words=True) or [""])

    add("PB3", rep.pb3_version)
    add("Phát hiện iSH", ("CÓ (" + "; ".join(rep.ish_evidence) + ")") if rep.is_ish else "KHÔNG")
    add("Python", "%s %s %s" % (rep.implementation, rep.python, "OK" if rep.python_ok else "QUÁ CŨ"))
    add("Hệ thống", "%s %s %s" % (rep.system, rep.release, rep.machine))
    if rep.alpine:
        add("Alpine", rep.alpine)
    add("CPU", str(rep.cpu_count))
    add("Terminal", "%d cột, encoding %s" % (rep.term_columns, rep.stdout_encoding))
    add("Thư mục dự án", rep.root)
    add("Workspace ghi được", ("CÓ" if rep.workspace_writable else "KHÔNG") + " - " + rep.workspace_detail)
    if rep.disk_free is not None:
        add("Dung lượng trống", fmt_bytes(rep.disk_free))
    if rep.mem:
        add("RAM (MemTotal/Avail)", "%s / %s" % (
            fmt_bytes(rep.mem.get("MemTotal", 0) * 1024),
            fmt_bytes(rep.mem.get("MemAvailable", rep.mem.get("MemFree", 0)) * 1024)))
    if rep.peak_mem_kb:
        add("RAM đỉnh PB3", fmt_bytes(rep.peak_mem_kb * 1024))
    add("tomllib", "có" if rep.tomllib else "không (dùng bộ đọc tối giản)")
    if rep.mounts:
        add("Thư mục /mnt", ", ".join(rep.mounts))
    lines.append("Backend Unity:")
    for m in rep.modules:
        lines.extend(textwrap.wrap("- %s: %s%s" % (m.name, _STATUS_VI.get(m.status, m.status),
                                                   (" v" + m.version) if m.version else ""),
                                   width=width, subsequent_indent="    "))
        if m.status in ("import_failed",) and m.detail:
            lines.extend(textwrap.wrap("    " + m.detail, width=width, subsequent_indent="    "))
    if rep.disabled_features:
        lines.append("Tính năng bị vô hiệu:")
        for f in rep.disabled_features:
            lines.extend(textwrap.wrap("- " + f, width=width, subsequent_indent="  "))
    for n in rep.notes:
        lines.extend(textwrap.wrap("* " + n, width=width, subsequent_indent="  "))
    return "\n".join(lines)
