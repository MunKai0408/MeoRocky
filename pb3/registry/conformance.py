"""Cổng vào conformance (đặc tả 4.8): `run_conformance(plugin | PluginRecord | lớp Plugin, fixture=...)`.

Logic kiểm nằm ở `sdk/testing.py`; tầng sdk không được import registry nên bộ chạy hook có guard
(`registry.isolation.run_hook`) được TIÊM vào từ đây. Dùng hàm này trong test plugin và trong `plugin test <id>`.
Guard là best effort, không phải sandbox (registry/isolation.py).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.errors import PB3Error
from core.limits import Limits
from registry import isolation
from registry.loader import PluginRecord, load_plugin_class
from sdk.api import Plugin
from sdk.testing import ConformanceReport, run_checks, unloadable_report


def _notes() -> List[str]:
    out: List[str] = []
    missing = sorted(k for k, v in isolation.guard_coverage().items() if not v)
    if missing:
        out.append("guard không vá được trên máy này: " + ", ".join(missing))
    n = isolation.abandoned_count()
    if n:
        out.append("%d luồng hook bị bỏ lại (plugin không chịu dừng); nên khởi động lại PB3" % n)
    out.append("guard là best effort, không phải sandbox")
    return out


def run_conformance(target: Any, fixture: Optional[str] = None, files: Optional[Dict[str, bytes]] = None,
                    limits: Optional[Limits] = None, hook_timeout: Optional[float] = None,
                    max_candidates: int = 3) -> ConformanceReport:
    """Chạy conformance. `target`: PluginRecord (từ loader/registry), instance Plugin hoặc lớp Plugin.
    Record không nạp được (state != ok) -> manifest FAIL kèm lý do, các mục khác SKIP."""
    if isinstance(target, PluginRecord):
        rec = target
    elif isinstance(target, Plugin):
        return run_checks(target, isolation.run_hook, fixture, files, limits, hook_timeout, max_candidates, _notes)
    elif isinstance(target, type) and issubclass(target, Plugin):
        rec = load_plugin_class(target)
    else:
        raise PB3Error("E_PLUGIN_INVALID", "run_conformance cần PluginRecord, Plugin hoặc lớp Plugin")
    if rec.instance is None or rec.state != "ok":
        return unloadable_report(rec.plugin_id, "[%s] %s" % (rec.code, rec.reason or "không nạp được"))
    return run_checks(rec.instance, isolation.run_hook, fixture, files, limits, hook_timeout, max_candidates, _notes)
