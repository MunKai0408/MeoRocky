"""Anti-Wave Hybrid V1.0 — goi chong song EPERM (Phase 2: CHECKPOINT & HEALTHCHECK).

Dat o goc repo (khong phai pb3/) vi day la cong cu van hanh cho agent/CI,
khong phai code web chay tren Pyodide: khong can precache, khong nam trong
files.json/tests.json/sw.js. Python 3.9 tuong thich, chi stdlib.

Thanh phan Phase 2:
  healthcheck.py — probe_wave(), classify_error(), WaveState
  checkpoint.py  — checkpoint JSON v1 (10 truong v1.1: 9 bat buoc +
                   current_commit_sha tuy chon), save/load qua AtomicWriter
  integrity.py   — sha256/snapshot/verify, phat hien truncate/0 byte
  logger.py      — log an toan, redact token/secret
  controller.py  — may trang thai NORMAL / WAVE / RECOVERY (skeleton; AUTO la Phase 5)

Chua co o Phase 2 (la Phase 4): patch_manager.py, recovery.py.
"""
from __future__ import annotations

import os as _os
import sys as _sys

# Cho phep `import anti_wave` tu goc repo ma khong can pb3/ co san trong
# sys.path: checkpoint.py dung `core.*` theo quy uoc cua pb3/tests.
_HERE = _os.path.dirname(_os.path.abspath(__file__))
_PB3 = _os.path.join(_os.path.dirname(_HERE), "pb3")
if _os.path.isdir(_PB3) and _PB3 not in _sys.path:
    _sys.path.insert(0, _PB3)

from anti_wave.checkpoint import (  # noqa: E402
    CHECKPOINT_VERSION,
    REQUIRED_FIELDS,
    build_checkpoint,
    checksum_of,
    dumps_checkpoint,
    load,
    loads_checkpoint,
    resume_summary,
    save,
)
from anti_wave.controller import Controller, NORMAL, RECOVERY, WAVE  # noqa: E402
from anti_wave.healthcheck import (  # noqa: E402
    WAVE_OFF,
    WAVE_ON,
    ErrorClass,
    ProbeResult,
    WaveState,
    classify_error,
    probe_wave,
)
from anti_wave.integrity import (  # noqa: E402
    STATUS_CHANGED,
    STATUS_MISSING,
    STATUS_OK,
    STATUS_TRUNCATED,
    STATUS_ZERO_BYTE,
    classify_change,
    detect_truncate,
    file_record,
    scan_for_zero_bytes,
    sha256_bytes,
    sha256_file,
    snapshot,
    verify_file,
    verify_snapshot,
)
from anti_wave.logger import (  # noqa: E402
    REDACTED,
    RedactingFilter,
    get_logger,
    log_event,
    redact,
)

__all__ = [
    "CHECKPOINT_VERSION",
    "REQUIRED_FIELDS",
    "WAVE_OFF",
    "WAVE_ON",
    "NORMAL",
    "RECOVERY",
    "WAVE",
    "REDACTED",
    "STATUS_CHANGED",
    "STATUS_MISSING",
    "STATUS_OK",
    "STATUS_TRUNCATED",
    "STATUS_ZERO_BYTE",
    "Controller",
    "ErrorClass",
    "ProbeResult",
    "RedactingFilter",
    "WaveState",
    "build_checkpoint",
    "checksum_of",
    "classify_change",
    "classify_error",
    "detect_truncate",
    "dumps_checkpoint",
    "file_record",
    "get_logger",
    "load",
    "loads_checkpoint",
    "log_event",
    "probe_wave",
    "redact",
    "resume_summary",
    "save",
    "scan_for_zero_bytes",
    "sha256_bytes",
    "sha256_file",
    "snapshot",
    "verify_file",
    "verify_snapshot",
]
