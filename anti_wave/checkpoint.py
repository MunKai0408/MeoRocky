"""Checkpoint JSON v1 cho agent (Anti-Wave, Phase 2).

Mot checkpoint la anh chup "agent dang lam gi, o dau, file nao da doi" de
agent moi (hoac cung agent sau khi song tan) doc xong tiep tuc duoc ma khong
can hoi lai MASTER. 10 truong theo spec MASTER v1.1: 9 bat buoc +
`current_commit_sha` tuy chon (chi co khi agent da commit); them truong tuy
chon (step, next_action, notes) duoc phep. Checkpoint RIENG tung agent —
khong dung 1 file chung cho nhieu agent (nguy co ghi de).

Toan ven: checksum = sha256 cua JSON chuan hoa (sap xep khoa, khong khoang
trang thua) cua toan bo truong TRU `checksum`. load() verify checksum va
fail-closed (raise PB3Error E_CORRUPT) khi sai lech, thieu truong, hoac
JSON hong — khong bao gio tra checkpoint nua voi.

Ghi: LUON qua pb3.core.safe_io.AtomicWriter (temp cung thu muc -> verify
-> fsync -> os.replace) nen ghi do giua chung khong bao gio de lai file
0 byte hay checkpoint do: file cu con nguyen, temp bi don.

Chi stdlib, Python 3.9 tuong thich. Khong chua token/key trong checkpoint.
"""
from __future__ import annotations

import datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.errors import PB3Error
from core.safe_io import AtomicWriter, read_bytes_limited

CHECKPOINT_VERSION = 1
CHECKPOINT_MAX_BYTES = 1024 * 1024  # checkpoint la JSON nho; vuot 1 MiB -> E_TOO_LARGE

# 9 truong bat buoc (spec MASTER v1.1) + current_commit_sha tuy chon
# (truong thu 10, chi co khi agent da commit). Thu tu nay cung la thu tu
# hien trong resume_summary().
REQUIRED_FIELDS = (
    "agent_id",
    "task_id",
    "branch",
    "base_commit_sha",
    "changed_files",
    "patch_or_content",
    "test_status",
    "created_at",
    "checksum",
)

OPTIONAL_FIELDS = ("schema_version", "current_commit_sha", "step", "next_action", "notes")


def _canonical(payload: Dict[str, Any]) -> bytes:
    """JSON chuan hoa de bam checksum: sap khoa, khong khoang trang thua, giu Unicode."""
    return json.dumps(payload, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def checksum_of(payload: Dict[str, Any]) -> str:
    """sha256 hex cua payload KHONG tinh truong `checksum` (neu co thi bo qua)."""
    clean = {k: v for k, v in payload.items() if k != "checksum"}
    return hashlib.sha256(_canonical(clean)).hexdigest()


def build_checkpoint(agent_id: str, task_id: str, branch: str,
                     base_commit_sha: str, changed_files: List[Dict[str, Any]],
                     patch_or_content: str, test_status: str, *,
                     current_commit_sha: Optional[str] = None,
                     step: Optional[str] = None, next_action: Optional[str] = None,
                     notes: Optional[str] = None,
                     created_at: Optional[str] = None) -> Dict[str, Any]:
    """Dung checkpoint day du 9 truong bat buoc + checksum (+ current_commit_sha
    tuy chon neu agent da commit).

    changed_files: list {path, sha256, bytes} (moi file agent da doi).
    patch_or_content: patch (diff) hoac noi dung thay doi / commit SHA da luu.
    test_status: chuoi tu do, vd "new-tests 12/12 pass; full-suite chua chay (song)".
    created_at: ISO UTC; mac dinh la thoi diem hien tai.
    """
    if not agent_id or not task_id or not branch or not base_commit_sha:
        raise PB3Error("E_CONFIG_INVALID", "checkpoint thieu dinh danh agent/task/branch/base")
    cp = {
        "schema_version": CHECKPOINT_VERSION,
        "agent_id": agent_id,
        "task_id": task_id,
        "branch": branch,
        "base_commit_sha": base_commit_sha,
        "changed_files": list(changed_files),
        "patch_or_content": patch_or_content,
        "test_status": test_status,
        "created_at": created_at or datetime.datetime.now(
            datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    if current_commit_sha is not None:
        cp["current_commit_sha"] = current_commit_sha
    if step is not None:
        cp["step"] = step
    if next_action is not None:
        cp["next_action"] = next_action
    if notes is not None:
        cp["notes"] = notes
    cp["checksum"] = checksum_of(cp)
    return cp


def _validate(payload: Any, where: str) -> Dict[str, Any]:
    """Kiem tra cau truc + checksum; sai -> PB3Error E_CORRUPT (fail-closed)."""
    if not isinstance(payload, dict):
        raise PB3Error("E_CORRUPT", "checkpoint khong phai JSON object", where=where)
    if payload.get("schema_version") != CHECKPOINT_VERSION:
        raise PB3Error("E_CORRUPT", "schema_version khong phai v%d" % CHECKPOINT_VERSION,
                       where=where)
    missing = [f for f in REQUIRED_FIELDS if f not in payload]
    if missing:
        raise PB3Error("E_CORRUPT", "checkpoint thieu truong: %s" % ", ".join(missing),
                       where=where)
    if not isinstance(payload["changed_files"], list):
        raise PB3Error("E_CORRUPT", "changed_files khong phai list", where=where)
    for entry in payload["changed_files"]:
        if not isinstance(entry, dict) or not {"path", "sha256", "bytes"} <= set(entry):
            raise PB3Error("E_CORRUPT", "changed_files co muc thieu path/sha256/bytes",
                           where=where)
    if payload.get("checksum") != checksum_of(payload):
        raise PB3Error("E_CORRUPT", "checksum checkpoint khong khop (file bi sua/hong)",
                       where=where)
    return payload


def dumps_checkpoint(checkpoint: Dict[str, Any]) -> bytes:
    """Checkpoint -> bytes JSON (da validate + bam lai checksum neu thieu)."""
    cp = dict(checkpoint)
    cp["checksum"] = checksum_of(cp)
    _validate(cp, where="<memory>")
    return (_canonical(cp) + b"\n")


def loads_checkpoint(data: bytes, where: str = "<memory>") -> Dict[str, Any]:
    """Bytes JSON -> checkpoint (validate day du; sai -> E_CORRUPT)."""
    try:
        payload = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise PB3Error("E_CORRUPT", "checkpoint khong phai JSON hop le: %s"
                       % type(exc).__name__, where=where)
    return _validate(payload, where)


def _verify_tmp(tmp: Path) -> None:
    """Verify cua AtomicWriter: doc file tam, kiem tra checksum TRUOC khi replace."""
    data = tmp.read_bytes()
    if len(data) > CHECKPOINT_MAX_BYTES:
        raise PB3Error("E_TOO_LARGE", "checkpoint vuot %d byte" % CHECKPOINT_MAX_BYTES,
                       where=str(tmp))
    loads_checkpoint(data, where=str(tmp))


def save(path, checkpoint: Dict[str, Any]) -> Path:
    """Ghi checkpoint qua AtomicWriter (co verify truoc khi replace).

    Ghi do giua chung (song EPERM, mat dien, huy): file dich cu con nguyen,
    file tam bi don. Tra duong dan file da ghi.
    """
    data = dumps_checkpoint(checkpoint)
    dest = Path(path)
    with AtomicWriter(dest, expected_size=len(data), verify=_verify_tmp) as w:
        w.write(data)
    return dest


def load(path) -> Dict[str, Any]:
    """Doc checkpoint tu file (verify checksum; sai -> E_CORRUPT)."""
    data = read_bytes_limited(path, CHECKPOINT_MAX_BYTES, where=str(path))
    return loads_checkpoint(data, where=str(path))


def resume_summary(checkpoint: Dict[str, Any]) -> str:
    """Tom tat tieng Viet de agent moi doc xong hanh dong ngay."""
    files = checkpoint.get("changed_files") or []
    lines = [
        "Checkpoint %s / %s" % (checkpoint.get("agent_id"), checkpoint.get("task_id")),
        "Nhanh: %s (base %s)" % (checkpoint.get("branch"),
                                 (checkpoint.get("base_commit_sha") or "")[:12]),
        "Patch/nội dung: %s" % (checkpoint.get("patch_or_content") or "chưa có"),
        "Trạng thái test: %s" % checkpoint.get("test_status"),
        "Tạo lúc: %s" % checkpoint.get("created_at"),
        "File đã đổi (%d):" % len(files),
    ]
    if checkpoint.get("current_commit_sha"):
        lines.insert(2, "Commit hiện tại: %s" % checkpoint["current_commit_sha"][:12])
    for entry in files:
        lines.append("  - %s (%d byte, sha256 %s...)"
                     % (entry.get("path"), entry.get("bytes"),
                        (entry.get("sha256") or "")[:12]))
    if checkpoint.get("step"):
        lines.append("Bước hiện tại: %s" % checkpoint["step"])
    if checkpoint.get("next_action"):
        lines.append("Việc tiếp theo: %s" % checkpoint["next_action"])
    if checkpoint.get("notes"):
        lines.append("Ghi chu: %s" % checkpoint["notes"])
    return "\n".join(lines)
