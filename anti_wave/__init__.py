"""Anti-Wave Hybrid — goi chong song EPERM (Phase 3: GITHUB INTEGRATION).

Dat o goc repo (khong phai pb3/) vi day la cong cu van hanh cho agent/CI,
khong phai code web chay tren Pyodide: khong can precache, khong nam trong
files.json/tests.json/sw.js. Python 3.9 tuong thich, chi stdlib.

Thanh phan Phase 3 (nhanh prep feat/anti-wave-phase3-prep):
  github_sync.py — dong bo checkpoint + patch qua Bear API (GitHub REST),
                   dispatch/poll workflow, doc artifact, fail-set tu junit XML.
                   Doc lap: khong phu thuoc module Phase 2.

Thanh phan Phase 2 (nhanh feat/anti-wave-fallback, gop sau):
  healthcheck.py, checkpoint.py, integrity.py, logger.py, controller.py
  (skeleton). Khi hai nhanh gop, file __init__.py nay PHAI GOP VOI ban
  cua Phase 2 (them export cua checkpoint/healthcheck/integrity/logger/
  controller) — khong ghi de mot chieu. Ban nay import thu cac module
  Phase 2 trong try/except de cung 1 file chay duoc ca tren nhanh prep
  (chi co github_sync) lan tren cay da gop.
"""
from __future__ import annotations

import os as _os
import sys as _sys

# Cho phep `import anti_wave` tu goc repo ma khong can pb3/ co san trong
# sys.path (cac module khac trong goi dung `core.*` theo quy uoc pb3/tests).
_HERE = _os.path.dirname(_os.path.abspath(__file__))
_PB3 = _os.path.join(_os.path.dirname(_HERE), "pb3")
if _os.path.isdir(_PB3) and _PB3 not in _sys.path:
    _sys.path.insert(0, _PB3)

from anti_wave.github_sync import (  # noqa: E402
    CHECKPOINTS_DIR,
    CHECKPOINT_MAX_BYTES,
    CHECKPOINT_REQUIRED_FIELDS,
    CHECKPOINT_VERSION,
    DEFAULT_TOKEN_ENV,
    PATCH_MAX_BYTES,
    REDACTED,
    REPO,
    GitHubSync,
    SyncError,
    failset_from_junitxml,
    validate_checkpoint,
    validate_patch_text,
)

# Module Phase 2 — chi co tren cay da gop; tren nhanh prep thi bo qua.
try:
    from anti_wave.checkpoint import (  # noqa: E402,F401
        REQUIRED_FIELDS,
        build_checkpoint,
        checksum_of,
        dumps_checkpoint,
        load,
        loads_checkpoint,
        resume_summary,
        save,
    )
    from anti_wave.controller import Controller, NORMAL, RECOVERY, WAVE  # noqa: E402,F401
    from anti_wave.healthcheck import (  # noqa: E402,F401
        WAVE_OFF,
        WAVE_ON,
        ErrorClass,
        ProbeResult,
        WaveState,
        classify_error,
        probe_wave,
    )
    from anti_wave.integrity import (  # noqa: E402,F401
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
    from anti_wave.logger import (  # noqa: E402,F401
        RedactingFilter,
        get_logger,
        log_event,
        redact,
    )
    _PHASE2_PRESENT = True
except ImportError:
    _PHASE2_PRESENT = False

__all__ = [
    "CHECKPOINTS_DIR",
    "CHECKPOINT_MAX_BYTES",
    "CHECKPOINT_REQUIRED_FIELDS",
    "CHECKPOINT_VERSION",
    "DEFAULT_TOKEN_ENV",
    "PATCH_MAX_BYTES",
    "REDACTED",
    "REPO",
    "GitHubSync",
    "SyncError",
    "failset_from_junitxml",
    "validate_checkpoint",
    "validate_patch_text",
]
if _PHASE2_PRESENT:
    __all__ += [
        "REQUIRED_FIELDS",
        "WAVE_OFF",
        "WAVE_ON",
        "NORMAL",
        "RECOVERY",
        "WAVE",
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
