"""Plugin API (đặc tả 4.2, 4.4): api_version, lớp cơ sở Plugin, PluginContext.

Plugin kế thừa `Plugin`, đặt `MANIFEST` (dict, Phụ lục A). Hook đúng chữ ký 4.2:
    detect(ctx) / list_candidates(ctx) / probe(ctx, cand) / parse(ctx, cand) -> LocTable
    build(ctx, table, base_bytes) -> bytes / verify(ctx, base_bytes, new_bytes) / selftest(ctx)
Không hook nào được ghi vào file nguồn: VFS chỉ đọc, `build` TRẢ bytes cho planner/patcher (mục 6) ghi ra.

Hai hook mở rộng tối thiểu (đặc tả có capability `container`/`hint` nhưng không nêu chữ ký; ghi ở
handoff/decisions.md để Claude 03-04 thống nhất): open_container(ctx, path) -> VFS, hints(ctx) -> dict.

Quy tắc "không giả vờ": capability khai báo mà thiếu hook tương ứng -> E_PLUGIN_INVALID khi khởi tạo;
gọi build/verify trên plugin không có 'write' -> E_WRITE_DISABLED. Việc ép timeout/guard là của registry
(Claude 03); SDK chỉ cung cấp ctx với limits đã hạ theo manifest và VFS chỉ đọc.
"""
from __future__ import annotations

import os
import tempfile
from typing import Any, Callable, Dict, List, Optional

from core.errors import MAX_DETAIL_CHARS, PB3Error, sanitize_detail
from core.limits import Limits
from sdk.manifest import API_MIN_VERSION, API_VERSION, Manifest, parse_manifest
from sdk.types import Candidate, Diagnostic, Evidence, LocTable, SourceRef
from sdk.vfs import VFS, LimitedVFS

api_version = API_VERSION          # Phụ lục B: `from sdk.api import Plugin, api_version`

MAX_MESSAGES = 1000
MAX_KB_KEY = 128


class LocaleRequest:
    """ctx.locale: locale người dùng yêu cầu + ánh xạ (4.4). code=None nghĩa là không yêu cầu locale."""

    def __init__(self, code: Optional[str] = None, mapping: Optional[Dict[str, str]] = None):
        if code is not None and (not isinstance(code, str) or not code):
            raise PB3Error("E_PLUGIN_INVALID", "LocaleRequest.code phải là chuỗi không rỗng hoặc None")
        self.code = code
        self.mapping = dict(mapping or {})

    def __repr__(self) -> str:
        return "LocaleRequest(%r)" % (self.code,)


def effective_limits(host: Limits, manifest: Manifest) -> Limits:
    """Limits của plugin = min(host, manifest). Plugin không được tự nới (Phụ lục A)."""
    return host.with_overrides(
        max_entry_bytes=min(host.max_entry_bytes, manifest.max_entry_bytes),
        hook_timeout_s=min(host.hook_timeout(manifest.true_cost == "heavy"), manifest.hook_timeout_s))


class PluginContext:
    """Môi trường host cấp cho plugin (4.4). Plugin chỉ dùng các thuộc tính/hàm công khai dưới đây."""

    def __init__(self, vfs: VFS, limits: Optional[Limits] = None, locale: Any = None, order: Optional[int] = None,
                 tempdir: Optional[str] = None, dry_run: bool = False, settings: Optional[Dict[str, Any]] = None,
                 kb: Any = None, plugin_id: str = "", cancel: Optional[Callable[[], bool]] = None,
                 sink: Optional[Callable[[str, str], None]] = None):
        if not isinstance(vfs, VFS):
            raise PB3Error("E_PLUGIN_INVALID", "PluginContext cần một VFS")
        if order is not None and (isinstance(order, bool) or not isinstance(order, int)):
            raise PB3Error("E_PLUGIN_INVALID", "ctx.order phải là int hoặc None")
        self.limits: Limits = limits or Limits()
        self._raw_vfs = vfs
        self.vfs: VFS = LimitedVFS(vfs, self.limits)
        self.locale = locale if isinstance(locale, LocaleRequest) else LocaleRequest(locale)
        self.order = order
        self.tempdir = tempdir
        self.dry_run = bool(dry_run)
        self.settings: Dict[str, Any] = dict(settings or {})
        self.plugin_id = plugin_id
        self.messages: List[tuple] = []          # (mức, nội dung) đã làm sạch, tối đa MAX_MESSAGES
        self.diagnostics: List[Diagnostic] = []
        self._kb = kb
        self._cancel = cancel
        self._sink = sink

    # ---- dẫn xuất ----
    def for_plugin(self, manifest: Manifest) -> "PluginContext":
        """ctx riêng cho một plugin: limits hạ theo manifest, settings/kb/diagnostics tách biệt.
        LocaleRequest được SAO CHÉP (plugin sửa mapping cũng không ảnh hưởng ctx gốc)."""
        locale_copy = LocaleRequest(self.locale.code, dict(self.locale.mapping))
        return PluginContext(self._raw_vfs, effective_limits(self.limits, manifest), locale_copy, self.order,
                             self.tempdir, self.dry_run, dict(self.settings), self._kb, manifest.id, self._cancel,
                             self._sink)

    # ---- log / diagnostics ----
    def _emit(self, level: str, msg: Any) -> None:
        text = sanitize_detail(msg, MAX_DETAIL_CHARS)   # không để nội dung file dài/ký tự điều khiển lọt vào log
        if len(self.messages) < MAX_MESSAGES:
            self.messages.append((level, text))
        if self._sink is not None:
            try:
                self._sink(level, text)
            except Exception:                  # sink hỏng không được làm hỏng plugin
                pass

    def log(self, msg: Any) -> None:
        self._emit("info", msg)

    def warn(self, msg: Any) -> None:
        self._emit("warning", msg)

    def diag(self, code: str, message_vi: str = "", detail: str = "", severity: str = "error",
             where: Optional[SourceRef] = None) -> Diagnostic:
        d = Diagnostic(code, severity, message_vi, sanitize_detail(detail, MAX_DETAIL_CHARS) if detail else "",
                       self.plugin_id, where)
        self.diagnostics.append(d)
        self._emit(severity, "%s %s" % (code, d.detail))
        return d

    # ---- file tạm ----
    def tempfile(self, name: str = "tmp", suffix: str = "") -> str:
        """Tạo file tạm TRONG tempdir của ctx (plugin không tự chọn đường dẫn
        ngoài). Trả đường dẫn; file đã tồn tại, trống."""
        if not self.tempdir:
            raise PB3Error("E_PLUGIN_INVALID", "ctx không có tempdir", plugin_id=self.plugin_id or None)
        os.makedirs(str(self.tempdir), exist_ok=True)
        fd, path = tempfile.mkstemp(prefix=str(name) + "-", suffix=suffix, dir=str(self.tempdir))
        os.close(fd)
        return path

    # ---- hủy ----
    def check_cancel(self) -> None:
        if self._cancel is not None and self._cancel():
            raise PB3Error("E_CANCELLED", "người dùng hủy", plugin_id=self.plugin_id or None)

    # ---- kho tri thức (qua API có kiểm soát; tên khóa tự gắn plugin_id) ----
    def _kb_key(self, key: Any) -> str:
        if not isinstance(key, str) or not key or len(key) > MAX_KB_KEY or "\x00" in key:
            raise PB3Error("E_PLUGIN_INVALID", "khóa KB phải là chuỗi 1..%d ký tự" % MAX_KB_KEY,
                           plugin_id=self.plugin_id or None)
        return key

    def kb_read(self, key: str, default: Any = None) -> Any:
        k = self._kb_key(key)
        if self._kb is None:
            return default
        v = self._kb.get(self.plugin_id, k)
        return default if v is None else v

    def kb_write(self, key: str, value: Any) -> bool:
        """True nếu đã ghi. dry_run hoặc không có KB -> False (không ghi, có log)."""
        k = self._kb_key(key)
        if self.dry_run:
            self.log("dry-run: bỏ qua kb_write(%s)" % k)
            return False
        if self._kb is None:
            self.warn("không có kho tri thức: bỏ qua kb_write(%s)" % k)
            return False
        self._kb.put(self.plugin_id, k, value)
        return True


_HOOK_FOR_CAP = {"read": "parse", "write": "build", "container": "open_container", "hint": "hints"}


class Plugin:
    """Lớp cơ sở (4.2). Lớp con đặt MANIFEST và ghi đè đúng các hook mà capability của nó cần."""
    MANIFEST: Dict[str, Any] = {}

    def __init__(self) -> None:
        self.manifest: Manifest = parse_manifest(self.MANIFEST)
        for cap in self.manifest.capabilities:
            hook = _HOOK_FOR_CAP[cap]
            if getattr(type(self), hook) is getattr(Plugin, hook):
                raise PB3Error("E_PLUGIN_INVALID", "khai capability '%s' nhưng không cài %s()" % (cap, hook),
                               plugin_id=self.manifest.id)

    @property
    def id(self) -> str:
        return self.manifest.id

    # ---- nhận diện ----
    def detect(self, ctx: PluginContext) -> List[Evidence]:
        return []

    def list_candidates(self, ctx: PluginContext) -> List[Candidate]:
        return []

    # ---- đọc ----
    def probe(self, ctx: PluginContext, cand: Candidate) -> Dict[str, Any]:
        return {}

    def parse(self, ctx: PluginContext, cand: Candidate) -> LocTable:
        raise PB3Error("E_NOT_IMPLEMENTED", "parse", plugin_id=self.id)

    # ---- ghi (chỉ khi capability 'write') ----
    def build(self, ctx: PluginContext, table: LocTable, base_bytes: bytes) -> bytes:
        raise PB3Error("E_WRITE_DISABLED", "plugin chỉ đọc", plugin_id=self.id)

    def verify(self, ctx: PluginContext, base_bytes: bytes, new_bytes: bytes) -> Dict[str, Any]:
        if not self.manifest.can_write:
            raise PB3Error("E_WRITE_DISABLED", "plugin chỉ đọc", plugin_id=self.id)
        return {"ok": None, "checked": False, "reason": "plugin chưa cài verify()"}

    # ---- tự kiểm tra ----
    def selftest(self, ctx: PluginContext) -> Dict[str, Any]:
        return {"ok": None, "skipped": True, "reason": "plugin chưa cài selftest()"}

    # ---- mở rộng theo capability 'container' / 'hint' ----
    def open_container(self, ctx: PluginContext, path: str) -> VFS:
        raise PB3Error("E_NOT_IMPLEMENTED", "open_container", plugin_id=self.id)

    def hints(self, ctx: PluginContext) -> Dict[str, Any]:
        raise PB3Error("E_NOT_IMPLEMENTED", "hints", plugin_id=self.id)
