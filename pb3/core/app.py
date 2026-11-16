"""Bootstrap vòng đời ứng dụng: gom Paths + Config + Logging + Limits + môi trường.

bootstrap() KHÔNG raise vì cấu hình/log/môi trường có vấn đề: mọi vấn đề được gom vào
App.problems (hiển thị trên menu + ghi log) để menu luôn khởi động được hoặc báo
lỗi môi trường chính xác. Chỉ ném PB3Error khi không thể chạy tiếp một cách an toàn
(ví dụ Python quá cũ - nhưng menu.py chặn trường hợp đó trước khi import).
"""
from __future__ import annotations

import builtins
import contextlib

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from . import VERSION, config as _config, ish_compat, safe_io
from . import logging as pb3log
from .errors import PB3Error
from .limits import Limits
from .paths import Paths
from .keepalive import KeepAlive
from .overall import OverallProgress
from .progress import CancelToken
from .runstate import RunInfo, RunMarker

STALE_QUARANTINE_DIR = "_stale"


@dataclass
class App:
    paths: Paths
    config: "_config.Config"
    limits: Limits
    logsetup: "pb3log.LogSetup"
    session_id: str
    cancel: CancelToken
    run_info: RunInfo
    marker: RunMarker
    problems: List[str] = field(default_factory=list)      # cảnh báo khởi động (tiếng Việt)
    stale_temps: List[Path] = field(default_factory=list)  # temp dở dang đã phát hiện
    env: Optional["ish_compat.EnvReport"] = None
    keepalive: Optional[KeepAlive] = None
    overall: Optional[OverallProgress] = None
    _tempdirs: List[Path] = field(default_factory=list)
    _closed: bool = False

    @property
    def log(self):
        return pb3log.get_logger("app")

    @property
    def debug(self) -> bool:
        return bool(self.config.get("app.debug"))

    # ---- thanh tiến độ tổng (0-100% + còn lại)
    def make_overall(self, job: str, stages) -> OverallProgress:
        """Tạo thanh tiến độ TỔNG cho một việc nhiều bước: stages = [(tên, giây dự kiến), ...].
        Dùng: `with app.make_overall("job", [("Đọc", 5), ("Ghi", 3)]) as ov: ov.begin("Đọc"); ...`.
        Không tty / progress.overall=false / PB3_PROGRESS=0 -> không vẽ gì (vẫn tính được)."""
        ov = OverallProgress(job, stages, width=int(self.config.get("progress.width")),
                             enabled=bool(self.config.get("progress.overall")),
                             history_path=self.paths.cache / "overall_times.json",
                             heartbeat_s=float(self.config.get("progress.heartbeat_s")),
                             cancel=self.cancel)
        return ov

    def pausing_input(self, fn):
        """Bọc hàm nhập: thanh tiến độ tự ẩn trong lúc chờ gõ rồi hiện lại."""
        def ask(prompt=""):
            ov = self.overall
            if ov is not None:
                ov.pause()
            try:
                return fn(prompt)
            finally:
                if ov is not None:
                    ov.resume()
        return ask

    @contextlib.contextmanager
    def job(self, name: str, stages=None):
        """Bọc MỘT việc bất kỳ (chức năng menu, lệnh CLI, dịch vụ mới) dưới thanh tiến độ tổng.
        Dịch vụ KHÔNG cần tự gắn: menu/CLI đã bọc sẵn; dịch vụ chỉ gọi `app.report_progress(...)`
        hoặc `app.overall.begin("Bước")` để làm thanh chính xác hơn. Khi đã có thanh (lồng nhau)
        thì dùng lại thanh ngoài. `input()` trong khối tự ẩn thanh khi chờ gõ."""
        if self.overall is not None:
            yield self.overall
            return
        ov = self.make_overall(name, stages or [("Chạy", 3.0)])   # mặc định 3.0s như DEFAULT_STAGE_S
        self.overall = ov
        real_input = builtins.input
        builtins.input = self.pausing_input(real_input)
        ok = False
        try:
            with ov.guard_stdout():
                ov.start()
                if stages is None:
                    ov.begin("Chạy")
                yield ov
            ok = True
        finally:
            builtins.input = real_input
            self.overall = None
            if ok:
                ov.finish()
            else:
                ov.abort()

    def report_progress(self, frac: Optional[float] = None, note: Optional[str] = None) -> None:
        """Dịch vụ gọi để báo tiến độ thật của bước hiện tại (không làm gì nếu không có thanh)."""
        ov = self.overall
        if ov is not None:
            ov.update(frac, note=note)

    # ---- chạy nền qua vị trí
    def start_keepalive(self):
        if self.keepalive is None:
            self.keepalive = KeepAlive(str(self.config.get("keepalive.device")))
        st = self.keepalive.start()
        if st.state == "starting":
            self.keepalive.wait_running(1.5)
            st = self.keepalive.status()
        self.log.info("keepalive: %s", st.vi())
        return st

    def stop_keepalive(self):
        if self.keepalive is None:
            return None
        st = self.keepalive.stop()
        self.log.info("keepalive: %s", st.vi())
        return st

    def make_tempdir(self, prefix: str = "job-") -> Path:
        """Thư mục tạm riêng của lần chạy (workspace/temp/run-<session>/...); tự dọn khi close()."""
        base = self.paths.temp / ("run-" + self.session_id)
        d = safe_io.make_tempdir(base, prefix)
        self._tempdirs.append(d)
        return d

    def quarantine_stale(self) -> List[Path]:
        """Di chuyển (KHÔNG xóa) temp dở dang vào workspace/temp/_stale/<session>/."""
        moved = safe_io.quarantine(self.stale_temps,
                                   self.paths.temp / STALE_QUARANTINE_DIR / self.session_id)
        done = {src for src, _ in moved}
        self.stale_temps = [p for p in self.stale_temps if p not in done]
        self.log.info("đã cách ly %d temp dở dang", len(moved))
        return [dst for _, dst in moved]

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if not self.debug:        # --debug giữ lại để điều tra (đặc tả 13)
                base = self.paths.temp / ("run-" + self.session_id)
                if base.exists():
                    safe_io.remove_tree_inside(self.paths.temp, base)
        except Exception as exc:       # dọn dẹp không bao giờ được làm hỏng thoát
            self.log.warning("không dọn được thư mục tạm: %s", type(exc).__name__)
        try:
            if self.overall is not None:
                self.overall.abort()
            if self.keepalive is not None:
                self.keepalive.stop()
        except Exception:
            pass
        self.marker.release()
        self.log.info("kết thúc phiên %s", self.session_id)
        self.logsetup.close()

    def __enter__(self) -> "App":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def bootstrap(root=None, *, debug: Optional[bool] = None, detect_env: bool = True,
              keepalive: Optional[bool] = None) -> App:
    paths = Paths(root)
    problems: List[str] = []
    for d, why in paths.ensure_dirs():
        problems.append("không tạo được thư mục %s: %s" % (paths.rel(d), why))

    cfg = _config.load_config(paths)
    problems.extend(cfg.warnings)
    if debug is not None:
        cfg._v["app.debug"] = bool(debug)
    # load_config có thể đổi đường dẫn (workspace/logs...) -> đảm bảo thư mục tồn tại
    for d, why in paths.ensure_dirs():
        msg = "không tạo được thư mục %s: %s" % (paths.rel(d), why)
        if msg not in problems:
            problems.append(msg)

    session_id = pb3log.new_session_id()
    ls = pb3log.setup_logging(
        paths.logs, session_id=session_id, level=cfg.get("log.level"),
        max_bytes=cfg.get("log.max_bytes"), backups=cfg.get("log.backups"),
        keep_sessions=cfg.get("log.keep_sessions"),
        max_message_chars=cfg.get("log.max_message_chars"),
        debug_console=bool(cfg.get("app.debug")))
    problems.extend(ls.problems)

    limits = Limits.from_config(cfg)
    marker = RunMarker(paths.temp, session_id)
    run_info = marker.acquire()
    if run_info.state == "interrupted":
        problems.append("lần chạy trước bị ngắt (iOS dừng iSH hoặc thoát đột ngột); "
                        "output dở dang nếu có đã được giữ nguyên, không tự xóa")
    elif run_info.state == "other_running":
        problems.append("có vẻ một phiên PB3 khác đang chạy (pid %s); cẩn thận khi dùng chung thư mục tạm"
                        % (run_info.previous or {}).get("pid", "?"))

    stale = safe_io.find_stale_temps([paths.output, paths.temp, paths.cache, paths.reports])
    if stale:
        problems.append("phát hiện %d tệp tạm dở dang (.pb3part-*); xem Chẩn đoán để cách ly" % len(stale))

    app = App(paths=paths, config=cfg, limits=limits, logsetup=ls, session_id=session_id,
              cancel=CancelToken(), run_info=run_info, marker=marker, problems=problems,
              stale_temps=stale)
    if detect_env:
        app.env = ish_compat.detect_environment(paths)
    lg = app.log
    lg.info("PB3 %s phiên %s python=%s ish=%s root=%s config=%s", VERSION, session_id,
            sys.version.split()[0], app.env.is_ish if app.env else "?", paths.root, _config.BACKEND)
    want_ka = bool(cfg.get("keepalive.enabled")) if keepalive is None else bool(keepalive)
    if want_ka:
        st = app.start_keepalive()
        if not st.ok:
            problems.append("chạy nền qua vị trí: " + st.vi())
    for p in problems:
        lg.warning("khởi động: %s", p)
    return app
