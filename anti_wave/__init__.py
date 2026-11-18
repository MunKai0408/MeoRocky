"""Anti-Wave Hybrid — goi chong song EPERM (Phase 2 + Phase 3 da gop).

Dat o goc repo (khong phai pb3/) vi day la cong cu van hanh cho agent/CI,
khong phai code web chay tren Pyodide: khong can precache, khong nam trong
files.json/tests.json/sw.js. Python 3.9 tuong thich, chi stdlib.

File nay la HOP NHAT NOI DUNG cua 2 ban (le MASTER 12:43(3) — khong thay
mot chieu):
  - Ban Phase 2 (main fc1c0a50, nhanh feat/anti-wave-fallback): export
    checkpoint / healthcheck / integrity / logger / controller.
  - Ban Phase 3 (commit 3284ad2c, nhanh feat/anti-wave-phase3-prep):
    export github_sync + import thu cac module Phase 2 trong try/except.
  Hop nhat giu nguyen ca hai: github_sync import thang; cac module Phase 2
  van trong try/except voi co _PHASE2_PRESENT; bo sung 2 ten ma ban Phase 2
  export nhung ban prep thieu (CHECKPOINT_VERSION tu checkpoint,
  REDACTED tu logger) — ca hai cung gia tri voi ban github_sync
  (CHECKPOINT_VERSION = 1; REDACTED = "[REDACTED]") nen khong doi hanh vi.

Thanh phan Phase 2:
  healthcheck.py — probe_wave(), classify_error(), WaveState
  checkpoint.py  — checkpoint JSON v1 (10 truong v1.1: 9 bat buoc +
                   current_commit_sha tuy chon), save/load qua AtomicWriter
  integrity.py   — sha256/snapshot/verify, phat hien truncate/0 byte
  logger.py      — log an toan, redact token/secret
  controller.py  — may trang thai NORMAL / WAVE / RECOVERY (skeleton; AUTO la Phase 5)

Thanh phan Phase 3:
  github_sync.py — dong bo checkpoint + patch qua Bear API (GitHub REST),
                   dispatch/poll workflow, doc artifact, fail-set tu junit XML.
                   Doc lap: khong phu thuoc module Phase 2.

Thanh phan Phase 4:
  patch_manager.py — tao / kiem tra / ap patch (unified diff) de chuyen
                     code giua VM va GitHub khong mat mat; tai dung
                     github_sync.validate_patch_text + safe_io.
  recovery.py      — quy trinh RECOVERY 12 buoc khi song tat (5 probe
                     lang cach nhau 30s, kiem tra toan ven, doi chieu
                     SHA/checkpoint, chi dong bo phan thieu, xung dot
                     thi dung); tai dung healthcheck/integrity/checkpoint.
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
        REDACTED,
        RedactingFilter,
        get_logger,
        log_event,
        redact,
    )
    from anti_wave.patch_manager import (  # noqa: E402,F401
        PATCH_SCHEMA_VERSION,
        apply_patch,
        apply_summary,
        build_patch,
        dumps_patch,
        files_for_checkpoint,
        load_patch_text_from_github,
        loads_patch,
        save_patch_to_github,
        validate_patch,
    )
    from anti_wave.recovery import (  # noqa: E402,F401
        STEP_NAMES,
        Recovery,
        RecoveryConflict,
        check_conflicts,
        ready_for_normal,
        recovery_summary,
        verify_calm,
    )
    _PHASE2_PRESENT = True
    _PHASE4_PRESENT = True
except ImportError:
    _PHASE2_PRESENT = False
    _PHASE4_PRESENT = False

# Luu y: CHECKPOINT_VERSION (checkpoint.py) va REDACTED (logger.py) cua
# Phase 2 cung gia tri voi ban github_sync (1 va "[REDACTED]") — import
# sau (Phase 2) ghi de import truoc (github_sync) nhung khong doi hanh vi.

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
if _PHASE4_PRESENT:
    __all__ += [
        "PATCH_SCHEMA_VERSION",
        "STEP_NAMES",
        "Recovery",
        "RecoveryConflict",
        "apply_patch",
        "apply_summary",
        "build_patch",
        "check_conflicts",
        "dumps_patch",
        "files_for_checkpoint",
        "load_patch_text_from_github",
        "loads_patch",
        "ready_for_normal",
        "recovery_summary",
        "save_patch_to_github",
        "validate_patch",
        "verify_calm",
    ]
