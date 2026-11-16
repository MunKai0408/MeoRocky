"""Isolation cho plugin (đặc tả 4.7; Claude 03 đợt 2, nhiệm vụ A).

ĐÂY LÀ GUARD "BEST EFFORT", KHÔNG PHẢI SANDBOX. Plugin vẫn là mã chạy trong tiến trình PB3. Thứ làm được:

  * `run_hook(plugin, name, ctx, ...)`: chạy một hook trong luồng riêng, có timeout (`ctx.limits.hook_timeout_s`),
    kết quả/ngoại lệ -> `HookResult` (kèm `Diagnostic` mã Phụ lục D). Pipeline không bao giờ nhận ngoại lệ của plugin.
  * Guard (chỉ áp cho luồng đang chạy hook/import, luồng khác của PB3 không bị ảnh hưởng):
      - chặn tạo socket / phân giải tên (socket.socket, create_connection, getaddrinfo, gethostbyname*),
      - chặn tạo tiến trình (subprocess.Popen, os.popen, các hàm shell/exec/spawn của os, os.spawn*, os.fork, posix_spawn*),
      - chặn nạp thư viện ngoài qua ctypes (CDLL, LibraryLoader.LoadLibrary),
      - chặn GHI/xóa/đổi tên ngoài thư mục cho phép (mặc định chỉ `ctx.tempdir`): open/io.open ở chế độ ghi, os.open có cờ ghi,
        os.remove/unlink/rename/replace/mkdir/rmdir/symlink/link/truncate.
    Vi phạm -> PB3Error E_PLUGIN_FORBIDDEN tại chỗ, VÀ được ghi nhớ: dù plugin nuốt ngoại lệ (`except Exception`),
    `run_hook` vẫn trả thất bại E_PLUGIN_FORBIDDEN.

KHÔNG làm được (ghi rõ để không tuyên bố an toàn quá mức):
  * Không giết được vòng lặp bị kẹt trong mã C hay hook không chịu dừng: luồng bị bỏ lại (daemon), guard của nó vẫn giữ
    cho đến khi nó tự kết thúc; `abandoned_count()` cho biết số luồng như vậy. Hook nên gọi `ctx.check_cancel()` định kỳ.
  * Guard là vá tạm hàm thư viện chuẩn: plugin cố tình lách được (import `_socket`, `posix`, `os.write` lên fd mở sẵn,
    pathlib trên Python 3.8 dùng io.open đã gắn sẵn, `ctypes.pythonapi`, mở file ở chế độ đọc để đọc ngoài ctx...).
  * Việc ĐỌC ngoài ctx.vfs không bị chặn (chỉ ghi/mạng/tiến trình/ctypes).
  * Mã host chạy trong luồng hook (sink log, KB) được bọc `trusted()`. VFS của host mà có ghi file tạm phải dùng
    `isolation.trusted()` hoặc ghi vào `ctx.tempdir`.

Dùng: `loader.discover(dirs, import_guard=isolation.import_guard)` để import/khởi tạo plugin cũng được bọc.
Không `import socket`/`subprocess` ở đầu file (luật iSH-only): module cần vá được nạp lúc cài guard, bằng tên.
"""
from __future__ import annotations

import copy
import importlib
import io
import os
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence

from core.errors import PB3Error
from sdk.types import Diagnostic

HOOKS = ("detect", "list_candidates", "probe", "parse", "build", "verify", "selftest", "open_container", "hints")
IMPORT_TIMEOUT_S = 20.0          # import/khởi tạo plugin (best effort, xem _Watchdog)
CANCEL_GRACE_S = 1.0             # sau timeout, chờ thêm chừng này để hook hợp tác dừng
POLL_S = 0.1
MAX_ABANDONED = 8                # quá số này thì từ chối chạy hook mới (tránh rò luồng vô hạn)
MAX_VIOLATIONS = 20

# ---------------------------------------------------------------------------- trạng thái guard (toàn tiến trình)
_tls = threading.local()
_lock = threading.RLock()
_installed = 0
_saved: List[tuple] = []          # (đối_tượng, tên_thuộc_tính, bản_gốc)
_coverage: Dict[str, bool] = {}   # tên mục đã vá -> True/False (vá được hay không trên máy này)
_abandoned: List[threading.Thread] = []


class _Policy:
    """Chính sách áp cho MỘT luồng trong lúc hook/import chạy."""

    def __init__(self, plugin_id: str, write_roots: Sequence[str]):
        self.plugin_id = plugin_id
        self.write_roots = [os.path.realpath(r) for r in write_roots if r]
        self.violations: List[str] = []
        self.suspended = 0

    def deny(self, what: str) -> None:
        if len(self.violations) < MAX_VIOLATIONS:
            self.violations.append(what)
        raise PB3Error("E_PLUGIN_FORBIDDEN", what, plugin_id=self.plugin_id or None)

    def check_write(self, what: str, path: Any) -> None:
        if isinstance(path, int):                       # fd đã mở sẵn: không kiểm được ở đây
            return
        try:
            p = os.path.realpath(os.fsdecode(path))
        except (TypeError, ValueError):
            self.deny("%s: đường dẫn không hợp lệ" % what)
            return
        if p == os.path.realpath(os.devnull):
            return
        for r in self.write_roots:
            try:
                if os.path.commonpath([r, p]) == r:
                    return
            except ValueError:
                continue
        self.deny("%s ngoài thư mục tạm của plugin" % what)


def _policy() -> Optional[_Policy]:
    pol = getattr(_tls, "policy", None)
    if pol is None or pol.suspended > 0:
        return None
    return pol


def _raw_policy() -> Optional[_Policy]:
    return getattr(_tls, "policy", None)


@contextmanager
def trusted() -> Iterator[None]:
    """Mã HOST chạy trong luồng hook (log, KB, VFS ghi tạm) tạm tắt guard của luồng này."""
    pol = _raw_policy()
    if pol is None:
        yield
        return
    pol.suspended += 1
    try:
        yield
    finally:
        pol.suspended -= 1


# ---------------------------------------------------------------------------- vá hàm thư viện
def _deny_wrapper(original: Callable[..., Any], what: str) -> Callable[..., Any]:
    def wrapper(*a: Any, **k: Any) -> Any:
        pol = _policy()
        if pol is not None:
            pol.deny("bị chặn: " + what)
        return original(*a, **k)
    wrapper.__name__ = getattr(original, "__name__", "pb3_guard")
    return wrapper


_WRITE_FLAGS = ("O_WRONLY", "O_RDWR", "O_APPEND", "O_CREAT", "O_TRUNC")


def _flags_write(flags: int) -> bool:
    mask = 0
    for n in _WRITE_FLAGS:
        mask |= getattr(os, n, 0)
    return bool(flags & mask)


def _open_wrapper(original: Callable[..., Any], what: str) -> Callable[..., Any]:
    def wrapper(file: Any, mode: str = "r", *a: Any, **k: Any) -> Any:
        pol = _policy()
        if pol is not None:
            m = k.get("mode", mode)
            if isinstance(m, str) and any(c in m for c in "wax+"):
                pol.check_write("ghi file (%s)" % what, file)
        return original(file, mode, *a, **k)
    wrapper.__name__ = getattr(original, "__name__", "open")
    return wrapper


def _osopen_wrapper(original: Callable[..., Any]) -> Callable[..., Any]:
    def wrapper(path: Any, flags: int, *a: Any, **k: Any) -> Any:
        pol = _policy()
        if pol is not None and _flags_write(flags):
            pol.check_write("os.open ghi", path)
        return original(path, flags, *a, **k)
    wrapper.__name__ = "open"
    return wrapper


def _path_wrapper(original: Callable[..., Any], what: str, n_paths: int) -> Callable[..., Any]:
    def wrapper(*a: Any, **k: Any) -> Any:
        pol = _policy()
        if pol is not None:
            for p in a[:n_paths]:
                pol.check_write(what, p)
            for key in ("path", "src", "dst"):   # os.rename(src=..., dst=...) từng lách qua vì chỉ kiểm args
                if key in k:
                    pol.check_write(what, k[key])
        return original(*a, **k)
    wrapper.__name__ = getattr(original, "__name__", what)
    return wrapper


def _patch(obj: Any, attr: str, make: Callable[[Callable[..., Any]], Callable[..., Any]], label: str) -> None:
    try:
        orig = getattr(obj, attr)
        setattr(obj, attr, make(orig))
        _saved.append((obj, attr, orig))
        _coverage[label] = True
    except (AttributeError, TypeError):
        _coverage[label] = False


def _load(name: str) -> Any:
    try:
        return importlib.import_module(name)
    except Exception:                       # thiếu trên máy này (vd ctypes ở iSH): ghi nhận, không vá
        return None


def _apply_patches() -> None:
    _saved.clear()
    _coverage.clear()
    sock = _load("socket")
    if sock is not None:
        _patch(sock.socket, "__init__", lambda o: _deny_wrapper(o, "mở socket"), "socket.socket")
        for n in ("create_connection", "getaddrinfo", "gethostbyname", "gethostbyname_ex"):
            if hasattr(sock, n):
                _patch(sock, n, lambda o, n=n: _deny_wrapper(o, "mạng: socket." + n), "socket." + n)
    sp = _load("subprocess")
    if sp is not None:
        _patch(sp.Popen, "__init__", lambda o: _deny_wrapper(o, "tạo tiến trình con"), "subprocess.Popen")
    if sock is not None:
        for n in ("socketpair", "fromfd"):
            if hasattr(sock, n):
                _patch(sock, n, lambda o, n=n: _deny_wrapper(o, "mạng: socket." + n), "socket." + n)
    for n in ("kill", "killpg"):   # host chỉ dùng os.kill(pid, 0) ở core/runstate.py (luồng chính, không phải hook)
        if hasattr(os, n):
            _patch(os, n, lambda o, n=n: _deny_wrapper(o, "tín hiệu tiến trình: os." + n), "os." + n)
    pt = _load("pty")
    if pt is not None:
        for n in ("spawn", "fork"):
            if hasattr(pt, n):
                _patch(pt, n, lambda o, n=n: _deny_wrapper(o, "tạo tiến trình: pty." + n), "pty." + n)
    proc_names = ["sys" + "tem", "popen", "fork", "forkpty", "posix_spawn", "posix_spawnp", "startfile"]
    proc_names += [p + s for p in ("exec", "spawn") for s in ("l", "le", "lp", "lpe", "v", "ve", "vp", "vpe")]
    for n in proc_names:
        if hasattr(os, n):
            _patch(os, n, lambda o, n=n: _deny_wrapper(o, "tạo tiến trình: os." + n), "os." + n)
    ct = _load("ctypes")
    if ct is not None:
        _patch(ct.CDLL, "__init__", lambda o: _deny_wrapper(o, "nạp thư viện ngoài (ctypes)"), "ctypes.CDLL")
        _patch(ct.LibraryLoader, "LoadLibrary", lambda o: _deny_wrapper(o, "nạp thư viện ngoài (ctypes)"),
               "ctypes.LoadLibrary")
    import builtins
    _patch(builtins, "open", lambda o: _open_wrapper(o, "open"), "builtins.open")
    _patch(io, "open", lambda o: _open_wrapper(o, "io.open"), "io.open")
    _patch(os, "open", _osopen_wrapper, "os.open")
    for n, k in (("remove", 1), ("unlink", 1), ("mkdir", 1), ("rmdir", 1), ("truncate", 1),
                 ("rename", 2), ("replace", 2), ("symlink", 2), ("link", 2)):
        if hasattr(os, n):
            _patch(os, n, lambda o, n=n, k=k: _path_wrapper(o, "os." + n, k), "os." + n)


def _restore_patches() -> None:
    for obj, attr, orig in reversed(_saved):
        try:
            setattr(obj, attr, orig)
        except (AttributeError, TypeError):
            pass
    _saved.clear()


def _install() -> None:
    global _installed
    with _lock:
        if _installed == 0:
            _apply_patches()
        _installed += 1


def _uninstall() -> None:
    global _installed
    with _lock:
        _installed -= 1
        if _installed <= 0:
            _installed = 0
            _restore_patches()


def guard_coverage() -> Dict[str, bool]:
    """Mục nào vá được trên máy này (để báo cáo trung thực). Rỗng nếu guard chưa từng cài."""
    with _lock:
        return dict(_coverage)


def guard_installed() -> bool:
    with _lock:
        return _installed > 0


@contextmanager
def active(plugin_id: str, write_roots: Sequence[str] = ()) -> Iterator[_Policy]:
    """Bật guard cho LUỒNG HIỆN TẠI. Ghi ngoài `write_roots` bị chặn. Có thể lồng nhau."""
    pol = _Policy(plugin_id, write_roots)
    prev = _raw_policy()
    _install()
    _tls.policy = pol
    try:
        yield pol
    finally:
        _tls.policy = prev
        _uninstall()


# ---------------------------------------------------------------------------- import guard (cho loader)
class PluginTimeout(Exception):
    """Được tiêm vào luồng import khi hết giờ (best effort)."""


class _Watchdog:
    """Hết giờ -> tiêm PluginTimeout vào luồng đích (PyThreadState_SetAsyncExc). Chỉ hiệu lực giữa các bytecode Python;
    không ngắt được mã C đang chạy. Không có ctypes -> không có timeout (guard khác vẫn hoạt động)."""

    def __init__(self, timeout_s: float):
        self.timeout_s = timeout_s
        self.tid = threading.get_ident()
        self.done = threading.Event()
        self.fired = False
        self._lk = threading.Lock()
        self._api: Any = None
        ct = _load("ctypes")
        if ct is not None and timeout_s > 0:
            self._ct = ct
            self._api = getattr(ct, "pythonapi", None)
        self._t: Optional[threading.Thread] = None

    def _run(self) -> None:
        if self.done.wait(self.timeout_s):
            return
        with self._lk:
            if self.done.is_set():
                return
            self.fired = True
            self._api.PyThreadState_SetAsyncExc(self._ct.c_ulong(self.tid), self._ct.py_object(PluginTimeout))

    def start(self) -> None:
        if self._api is not None:
            self._t = threading.Thread(target=self._run, name="pb3-import-watchdog", daemon=True)
            self._t.start()

    def stop(self) -> None:
        with self._lk:
            self.done.set()
            if self.fired and self._api is not None:     # xóa ngoại lệ chưa kịp giao
                self._api.PyThreadState_SetAsyncExc(self._ct.c_ulong(self.tid), None)


def make_import_guard(timeout_s: float = IMPORT_TIMEOUT_S) -> Callable[[str], Any]:
    @contextmanager
    def guard(plugin_id: str) -> Iterator[None]:
        wd = _Watchdog(timeout_s)
        with active(plugin_id, ()) as pol:
            wd.start()
            try:
                try:
                    yield
                finally:
                    wd.stop()
            except PluginTimeout:
                raise PB3Error("E_PLUGIN_TIMEOUT", "import/khởi tạo quá %.0f s" % timeout_s, plugin_id=plugin_id)
            if pol.violations:
                raise PB3Error("E_PLUGIN_FORBIDDEN", "; ".join(pol.violations[:3]), plugin_id=plugin_id)
    return guard


import_guard = make_import_guard()


# ---------------------------------------------------------------------------- chạy hook
@dataclass
class HookResult:
    ok: bool
    value: Any = None
    diagnostic: Optional[Diagnostic] = None
    elapsed_s: float = 0.0
    timed_out: bool = False
    violations: List[str] = field(default_factory=list)
    traceback: str = ""            # chỉ để ghi vào logs/, không hiển thị cho người dùng

    @property
    def code(self) -> str:
        return "E_OK" if self.ok else (self.diagnostic.code if self.diagnostic else "E_INTERNAL")


class _TrustedKB:
    """Bọc KB của host: mã host chạy dưới trusted()."""

    def __init__(self, kb: Any):
        self._kb = kb

    def get(self, *a: Any, **k: Any) -> Any:
        with trusted():
            return self._kb.get(*a, **k)

    def put(self, *a: Any, **k: Any) -> Any:
        with trusted():
            return self._kb.put(*a, **k)


def abandoned_count() -> int:
    with _lock:
        _abandoned[:] = [t for t in _abandoned if t.is_alive()]
        return len(_abandoned)


def _fail(code: str, detail: str, pid: str, t0: float, **kw: Any) -> HookResult:
    return HookResult(False, None, Diagnostic(code, "error", "", detail, pid), time.monotonic() - t0, **kw)


def _hook_ctx(ctx: Any, flag: threading.Event) -> Any:
    """Bản sao nông của ctx cho một lần gọi hook: dùng chung messages/diagnostics, riêng cờ hủy
    (timeout của hook này không làm hỏng ctx của hook sau)."""
    h = copy.copy(ctx)
    orig_cancel = getattr(ctx, "_cancel", None)
    orig_sink = getattr(ctx, "_sink", None)
    orig_kb = getattr(ctx, "_kb", None)

    def cancel() -> bool:
        if flag.is_set():
            return True
        if orig_cancel is None:
            return False
        with trusted():
            return bool(orig_cancel())

    h._cancel = cancel
    if orig_sink is not None:
        def sink(level: str, text: str) -> None:
            with trusted():
                orig_sink(level, text)
        h._sink = sink
    if orig_kb is not None:
        h._kb = _TrustedKB(orig_kb)
    return h


def run_hook(plugin: Any, name: str, ctx: Any, *args: Any, timeout: Optional[float] = None,
             write_roots: Optional[Sequence[str]] = None, **kwargs: Any) -> HookResult:
    """Chạy `plugin.<name>(ctx, *args)` dưới timeout + guard. Không raise vì lỗi của plugin.

    `ctx` nên là `ctx.for_plugin(plugin.manifest)` (limits đã hạ theo manifest). `timeout` mặc định
    `ctx.limits.hook_timeout_s`. Ghi file chỉ được trong `write_roots` (mặc định: `ctx.tempdir`).
    Với `build`: giá trị trả về phải là bytes (bytearray được đổi sang bytes), nếu không -> E_PLUGIN_INVALID.
    """
    t0 = time.monotonic()
    pid = str(getattr(getattr(plugin, "manifest", None), "id", "") or getattr(ctx, "plugin_id", "") or "")
    if name not in HOOKS:
        return _fail("E_PLUGIN_INVALID", "tên hook lạ: %r" % (name,), pid, t0)
    fn = getattr(plugin, name, None)
    if not callable(fn):
        return _fail("E_PLUGIN_INVALID", "plugin không có hook %s" % name, pid, t0)
    if abandoned_count() >= MAX_ABANDONED:
        return _fail("E_INTERNAL", "quá nhiều hook bị bỏ lại (%d); khởi động lại PB3" % MAX_ABANDONED, pid, t0)
    limit = float(timeout if timeout is not None else ctx.limits.hook_timeout_s)
    roots = list(write_roots) if write_roots is not None else ([ctx.tempdir] if getattr(ctx, "tempdir", None) else [])

    flag = threading.Event()
    done = threading.Event()
    box: Dict[str, Any] = {}
    hctx = _hook_ctx(ctx, flag)
    host_cancel = getattr(ctx, "_cancel", None)

    def worker() -> None:
        try:
            with active(pid, roots) as pol:
                try:
                    box["value"] = fn(hctx, *args, **kwargs)
                finally:
                    box["violations"] = list(pol.violations)
        except BaseException as e:                       # kể cả SystemExit/KeyboardInterrupt trong luồng hook
            box["exc"] = e
            box["tb"] = traceback.format_exc()
        finally:
            done.set()

    th = threading.Thread(target=worker, name="pb3-hook-%s-%s" % (pid, name), daemon=True)
    th.start()
    deadline = t0 + limit
    host_cancelled = False
    try:
        while not done.wait(POLL_S):
            if host_cancel is not None and not host_cancelled and host_cancel():
                host_cancelled = True
                flag.set()
            if time.monotonic() >= deadline:
                break
    except BaseException:                                # Ctrl-C ở luồng chính: báo hook dừng rồi ném lại
        flag.set()
        raise
    if not done.is_set():
        flag.set()
        if not done.wait(CANCEL_GRACE_S):
            with _lock:
                _abandoned.append(th)
        return _fail("E_PLUGIN_TIMEOUT", "hook %s quá %.1f s" % (name, limit), pid, t0, timed_out=True,
                     violations=list(box.get("violations", [])))

    elapsed = time.monotonic() - t0
    viol = list(box.get("violations", []))
    exc = box.get("exc")
    tb = box.get("tb", "")
    if viol:                                             # plugin có nuốt ngoại lệ cũng vẫn thất bại
        return HookResult(False, None, Diagnostic("E_PLUGIN_FORBIDDEN", "error", "", "; ".join(viol[:3]), pid),
                          elapsed, violations=viol, traceback=tb)
    if exc is not None:
        if isinstance(exc, PB3Error):
            d = Diagnostic.from_error(exc, plugin_id=pid)
        elif isinstance(exc, (KeyboardInterrupt, SystemExit)):
            d = Diagnostic("E_INTERNAL", "error", "", "hook thoát bằng %s" % type(exc).__name__, pid)
        else:
            d = Diagnostic("E_INTERNAL", "error", "", "ngoại lệ trong hook %s: %s" % (name, type(exc).__name__), pid)
        return HookResult(False, None, d, elapsed, traceback=tb)
    value = box.get("value")
    if name == "build":
        if isinstance(value, bytearray):
            value = bytes(value)
        if not isinstance(value, bytes):
            return _fail("E_PLUGIN_INVALID", "build phải trả bytes, nhận %s" % type(value).__name__, pid, t0)
    return HookResult(True, value, None, elapsed)
