"""Kiểm tra tính toàn vẹn file (Anti-Wave, Phase 2).

So sánh sha256 + kích thước trước/sau để phát hiện file bị truncate,
0 byte, mất, hoặc thay đổi nội dung. Dùng cho:
  - checkpoint: ghi lại sha256 từng file đã đổi, đối chiếu khi khôi phục.
  - RECOVERY (Phase 4): quét file bị truncate/0 byte sau khi sóng tắt.
  - verify_snapshot: đối chiếu 1 loạt file sau khi đồng bộ từ Bear.

Chi stdlib, Python 3.9 tương thích. Không ghi gì lên đĩa (chỉ đọc).
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.errors import error_from_exception

CHUNK_SIZE = 1024 * 1024

STATUS_OK = "ok"
STATUS_MISSING = "missing"
STATUS_CHANGED = "changed"
STATUS_TRUNCATED = "truncated"
STATUS_ZERO_BYTE = "zero_byte"


def sha256_bytes(data: bytes) -> str:
    """sha256 hex của bytes (pure, không chạm đĩa)."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path, *, chunk_size: int = CHUNK_SIZE) -> str:
    """sha256 file (streaming từng chunk). File không tồn tại/không đọc
    được -> PB3Error qua error_from_exception(op="read")."""
    p = Path(path)
    h = hashlib.sha256()
    try:
        with open(str(p), "rb") as f:
            while True:
                b = f.read(chunk_size)
                if not b:
                    break
                h.update(b)
    except OSError as exc:
        raise error_from_exception(exc, where=str(p))
    return h.hexdigest()


def file_record(path) -> Dict[str, Any]:
    """Bản ghi 1 file: {path, sha256, bytes, exists}.

    File không tồn tại (hoặc không phải file thường) -> exists False,
    sha256/bytes None. Lỗi đọc khác (vd không có quyền) -> PB3Error.
    """
    p = Path(path)
    try:
        st = os.stat(str(p))
    except FileNotFoundError:
        return {"path": str(p), "sha256": None, "bytes": None, "exists": False}
    except OSError as exc:
        raise error_from_exception(exc, where=str(p))
    if not os.path.isfile(str(p)):
        return {"path": str(p), "sha256": None, "bytes": None, "exists": False}
    return {"path": str(p), "sha256": sha256_file(p), "bytes": st.st_size,
            "exists": True}


def snapshot(paths) -> Dict[str, Dict[str, Any]]:
    """Chụp sha256 + kích thước cho nhiều file: {str(path): record}."""
    out: Dict[str, Dict[str, Any]] = {}
    for p in paths:
        rec = file_record(p)
        out[rec["path"]] = rec
    return out


def classify_change(expected: Dict[str, Any], actual: Dict[str, Any]) -> str:
    """So sánh 2 bản ghi (pure, không chạm đĩa) -> 1 trong 5 trạng thái.

      missing    — file hiện tại không tồn tại (hoặc không phải file thường).
      zero_byte  — hiện tại 0 byte trong khi trước đó > 0 byte.
      truncated  — hiện tại 0 < bytes < trước đó.
      changed    — sha256 khác (hoặc kích thước khác khi không có sha để so).
      ok         — sha256 + kích thước khớp.
    """
    if not actual.get("exists"):
        return STATUS_MISSING
    exp_bytes = expected.get("bytes")
    act_bytes = actual.get("bytes")
    if act_bytes == 0 and exp_bytes not in (None, 0):
        return STATUS_ZERO_BYTE
    if exp_bytes is not None and act_bytes is not None and 0 < act_bytes < exp_bytes:
        return STATUS_TRUNCATED
    if expected.get("sha256") is not None and actual.get("sha256") is not None:
        return STATUS_OK if expected["sha256"] == actual["sha256"] else STATUS_CHANGED
    if exp_bytes is not None and act_bytes is not None and exp_bytes != act_bytes:
        return STATUS_CHANGED
    return STATUS_OK


def verify_snapshot(snap: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Đối chiếu snapshot với đĩa hiện tại.

    Trả list {path, status, expected_sha256, actual_sha256,
    expected_bytes, actual_bytes} — giữ nguyên thứ tự snapshot.
    """
    results: List[Dict[str, Any]] = []
    for path, expected in snap.items():
        actual = file_record(path)
        results.append({
            "path": path,
            "status": classify_change(expected, actual),
            "expected_sha256": expected.get("sha256"),
            "actual_sha256": actual.get("sha256"),
            "expected_bytes": expected.get("bytes"),
            "actual_bytes": actual.get("bytes"),
        })
    return results


def detect_truncate(path, *, expected_bytes: Optional[int] = None,
                    expected_sha256: Optional[str] = None) -> Dict[str, Any]:
    """Kiểm tra 1 file có bị truncate/0 byte không.

    Trả {path, truncated, status, actual_bytes, actual_sha256}.
    truncated=True khi status là truncated hoặc zero_byte.
    """
    actual = file_record(path)
    expected = {"path": str(path), "sha256": expected_sha256,
                "bytes": expected_bytes, "exists": True}
    status = classify_change(expected, actual)
    return {
        "path": str(path),
        "truncated": status in (STATUS_TRUNCATED, STATUS_ZERO_BYTE),
        "status": status,
        "actual_bytes": actual.get("bytes"),
        "actual_sha256": actual.get("sha256"),
    }


def verify_file(path, expected_sha256: str,
                expected_bytes: Optional[int] = None) -> Dict[str, Any]:
    """Verify 1 file khớp sha256 (+ kích thước nếu cho).

    Trả {path, ok, status, expected_sha256, actual_sha256,
    expected_bytes, actual_bytes}. ok=True chỉ khi status == "ok".
    """
    actual = file_record(path)
    expected = {"path": str(path), "sha256": expected_sha256,
                "bytes": expected_bytes, "exists": True}
    status = classify_change(expected, actual)
    return {
        "path": str(path),
        "ok": status == STATUS_OK,
        "status": status,
        "expected_sha256": expected_sha256,
        "actual_sha256": actual.get("sha256"),
        "expected_bytes": expected_bytes,
        "actual_bytes": actual.get("bytes"),
    }


def scan_for_zero_bytes(root, *, recursive: bool = True) -> List[Path]:
    """Quét cây thư mục (CHỈ ĐỌC) tìm file 0 byte.

    Bỏ qua __pycache__ và .git. Thư mục không tồn tại/không đọc được
    -> []. Không bao giờ raise vì 1 file/thư mục lỗi (bỏ qua mục đó).
    """
    root_p = Path(root)
    found: List[Path] = []
    if not os.path.isdir(str(root_p)):
        return found
    if recursive:
        for dirpath, dirnames, filenames in os.walk(str(root_p), followlinks=False):
            dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".git")]
            for fn in filenames:
                fp = Path(dirpath) / fn
                try:
                    if fp.stat().st_size == 0:
                        found.append(fp)
                except OSError:
                    continue
    else:
        try:
            entries = list(os.scandir(str(root_p)))
        except OSError:
            return found
        for e in entries:
            try:
                if e.is_file(follow_symlinks=False) and e.stat().st_size == 0:
                    found.append(Path(e.path))
            except OSError:
                continue
    return sorted(found)
