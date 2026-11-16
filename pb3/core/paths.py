"""Đường dẫn dự án + kiểm tra đường dẫn an toàn.

- Gốc dự án được SUY RA từ vị trí của chính mã nguồn (cha của core/), không
  hard-code '/root/pb3' và không phụ thuộc thư mục làm việc hiện tại. Trên iSH
  thư mục chuẩn là /root/pb3 (xem EXPECTED_ROOT, chỉ dùng để ghi chú; decisions #40).
- Mọi đường dẫn con (workspace, logs...) lấy từ config, là đường dẫn TƯƠNG ĐỐI
  đã kiểm tra bằng safe_relpath/resolve_inside.
"""
from __future__ import annotations

import os
import unicodedata
from pathlib import Path
from typing import List, Optional, Tuple

from .errors import PB3Error

EXPECTED_ROOT = "/root/pb3"      # chỉ để cảnh báo, không dùng để dựng đường dẫn
MAX_COMPONENT_BYTES = 255
MAX_PATH_BYTES = 1024


def nfc(s: str) -> str:
    """Chuẩn hóa Unicode NFC (đặc tả 2.3): tên tiếng Việt/Trung so khớp ổn định."""
    return unicodedata.normalize("NFC", s)


def _unsafe(detail: str, where: Optional[str] = None) -> PB3Error:
    return PB3Error("E_UNSAFE_PATH", detail, where=where)


def check_component(name: str) -> str:
    """Kiểm tra 1 thành phần tên file; trả về tên NFC."""
    if name == "":
        raise _unsafe("thành phần rỗng")
    if "\x00" in name:
        raise _unsafe("chứa byte NUL")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in name):
        raise _unsafe("chứa ký tự điều khiển")
    if name in (".", ".."):
        raise _unsafe("thành phần '%s' bị cấm" % name)
    n = nfc(name)
    if len(n.encode("utf-8")) > MAX_COMPONENT_BYTES:
        raise _unsafe("tên quá dài (>%d byte)" % MAX_COMPONENT_BYTES)
    return n


def safe_relpath(rel: str) -> Tuple[str, ...]:
    """Tách đường dẫn TƯƠNG ĐỐI (dấu '\\' coi là '/' như tên entry zip từ Windows).

    Từ chối: tuyệt đối, ổ đĩa kiểu 'C:', '..', NUL, ký tự điều khiển, quá dài.
    """
    if not isinstance(rel, str) or rel == "":
        raise _unsafe("đường dẫn rỗng")
    if "\x00" in rel:
        raise _unsafe("chứa byte NUL")
    s = rel.replace("\\", "/")
    if s.startswith("/"):
        raise _unsafe("đường dẫn tuyệt đối không được phép")
    if len(s) >= 2 and s[1] == ":" and s[0].isalpha():
        raise _unsafe("đường dẫn kiểu ổ đĩa không được phép")
    if len(s.encode("utf-8", "surrogatepass")) > MAX_PATH_BYTES:
        raise _unsafe("đường dẫn quá dài (>%d byte)" % MAX_PATH_BYTES)
    parts = [p for p in s.split("/") if p not in ("", ".")]
    if not parts:
        raise _unsafe("đường dẫn không có thành phần hợp lệ")
    return tuple(check_component(p) for p in parts)


def is_within(base, target) -> bool:
    """True nếu realpath(target) nằm trong realpath(base) (kể cả khi target chưa tồn tại)."""
    b = os.path.realpath(str(base))
    t = os.path.realpath(str(target))
    try:
        return os.path.commonpath([b, t]) == b
    except ValueError:
        return False


def resolve_inside(base, rel: str) -> Path:
    """Ghép base + rel an toàn; chặn '..', tuyệt đối và thoát ra ngoài bằng symlink."""
    comps = safe_relpath(rel)
    base_p = Path(base)
    target = base_p.joinpath(*comps)
    if not os.path.lexists(str(base_p)):     # gốc chưa tạo: bên dưới chưa thể có symlink
        return target
    probe = target
    while not os.path.lexists(str(probe)):   # tổ tiên đầu tiên đã tồn tại (tối đa tới base)
        probe = probe.parent
    if not is_within(base_p, probe):
        raise _unsafe("thoát ra ngoài thư mục gốc (symlink?)", where=rel)
    return target


class Paths:
    """Bố cục thư mục dự án. Không tạo gì trong __init__ (xem ensure_dirs)."""

    def __init__(self, root=None, *, workspace: str = "workspace", logs: str = "logs",
                 reports: str = "reports", kb: str = "kb"):
        self.root = Path(root).resolve() if root is not None else self.discover_root()
        self.config_dir = self.root / "config"
        self.default_toml = self.config_dir / "default.toml"
        self.local_toml = self.config_dir / "local.toml"
        self.locales_toml = self.config_dir / "locales.toml"
        self.handoff = self.root / "handoff"
        self.configure(workspace=workspace, logs=logs, reports=reports, kb=kb)

    def configure(self, *, workspace: str, logs: str, reports: str, kb: str) -> None:
        """(Đặt lại) các thư mục con từ giá trị config; chặn đường dẫn bất thường."""
        self.workspace = resolve_inside(self.root, workspace)
        self.input = self.workspace / "input"
        self.temp = self.workspace / "temp"
        self.output = self.workspace / "output"
        self.cache = self.workspace / "cache"
        self.logs = resolve_inside(self.root, logs)
        self.reports = resolve_inside(self.root, reports)
        self.kb = resolve_inside(self.root, kb)

    @staticmethod
    def discover_root() -> Path:
        return Path(__file__).resolve().parent.parent

    def writable_dirs(self) -> List[Path]:
        return [self.workspace, self.input, self.temp, self.output, self.cache,
                self.logs, self.reports, self.kb]

    def ensure_dirs(self) -> List[Tuple[Path, str]]:
        """Tạo thư mục làm việc. Không raise: trả về danh sách (path, lý do) thất bại."""
        failed = []
        for d in self.writable_dirs():
            try:
                d.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                failed.append((d, "%s (errno=%s)" % (type(exc).__name__, exc.errno)))
        return failed

    def at_expected_root(self) -> bool:
        return str(self.root) == EXPECTED_ROOT

    def rel(self, p) -> str:
        """Đường dẫn tương đối so với gốc dự án (cho log/báo cáo); ngoài gốc -> giữ nguyên."""
        try:
            return str(Path(p).resolve().relative_to(self.root))
        except (ValueError, OSError):
            return str(p)
