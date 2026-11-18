"""Quan ly patch (unified diff) — Anti-Wave, Phase 4.

Patch la cau noi chuyen code giua VM va GitHub khi song EPERM chan ghi
local: agent tao patch tu thay doi, luu len GitHub qua Bear API
(github_sync.save_patch), agent khac (hoac chinh agent do sau khi song
tat) tai ve va ap patch de khoi phuc dung noi dung — khong mat mat.

Tai dung, khong tao lai:
  * github_sync.validate_patch_text — kiem tra cau truc + quet bi mat.
  * github_sync.PATCH_MAX_BYTES — gioi han 1 MiB cho patch text.
  * integrity.sha256_bytes — checksum tung file.
  * core.safe_io.write_bytes_guarded — ghi an toan (probe thu muc truoc,
    EPERM -> E_WRITE_WAVE ngay, khong de lai file 0 byte).

Mot patch gom 2 lop:
  1. patch_text — unified diff thuan (git-style), luu len GitHub qua
     GitHubSync.save_patch, kiem tra duoc bang scripts/validate_patch.py.
  2. metadata — base_commit_sha, test_status, files [{path, sha256,
     bytes, action}]; checksum sha256 cua JSON chuan hoa (nhu
     checkpoint.py). Metadata di kem checkpoint (changed_files) hoac luu
     local dang envelope JSON (dumps_patch/loads_patch).

Ap patch fail-closed: checksum sai / duong dan tuyet doi / co bi mat /
ngu canh khong khop / sha256 sau khi ap khong khop -> raise PB3Error
(E_CORRUPT / E_UNSAFE_PATH / E_CONFIG_INVALID), KHONG ghi gi ca.
Moi file duoc tinh toan trong RAM truoc, verify xong moi ghi.

Chi ho tro file text UTF-8 (code nguon). File nhi phan -> tu choi
(E_CORRUPT) thay vi doan.

Chi stdlib, Python 3.9 tuong thich.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from anti_wave.github_sync import PATCH_MAX_BYTES, validate_patch_text
from anti_wave.integrity import sha256_bytes
from core.errors import PB3Error, error_from_exception
from core.safe_io import write_bytes_guarded

PATCH_SCHEMA_VERSION = 1

ACTIONS = ("added", "modified", "deleted")

_DIFF_GIT_RE = re.compile(r"^diff --git a/(.*) b/(.*)$")
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_NO_NEWLINE = "\\ No newline at end of file"

# Dong vo nghia trong section diff (git metadata) — bo qua khi parse.
_IGNORED_PREFIXES = ("new file mode ", "deleted file mode ", "index ",
                     "similarity index ", "rename from ", "rename to ",
                     "old mode ", "new mode ", "Binary files ")


def _canonical(payload: Dict[str, Any]) -> bytes:
    """JSON chuan hoa de bam checksum — PHAI KHOP checkpoint.py::_canonical."""
    return json.dumps(payload, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def patch_checksum(patch: Dict[str, Any]) -> str:
    """sha256 hex cua patch KHONG tinh truong `checksum`."""
    clean = {k: v for k, v in patch.items() if k != "checksum"}
    return hashlib.sha256(_canonical(clean)).hexdigest()


def _path_problem(path: str) -> Optional[str]:
    """Tra ve mo ta van de neu duong dan patch khong an toan, None neu ok.

    Chi chap nhan duong dan tuong doi trong repo: khong tuyet doi, khong
    `..`, khong rong, khong NUL. (Symlink thoat root khong kiem tra o day
    — nguoi goi phai dam bao root dang tin; ghi chi tao file thuong.)
    """
    if not isinstance(path, str) or not path:
        return "duong dan rong"
    if "\x00" in path:
        return "duong dan chua NUL: %r" % path
    if path.startswith("/") or path.startswith("\\") or re.match(r"^[A-Za-z]:", path):
        return "duong dan tuyet doi: %s" % path
    parts = path.replace("\\", "/").split("/")
    if ".." in parts:
        return "duong dan thoat repo (..): %s" % path
    return None


def _check_rel_path(path: str) -> None:
    problem = _path_problem(path)
    if problem is not None:
        raise PB3Error("E_UNSAFE_PATH", problem, where="anti_wave.patch_manager")


def _decode_utf8(data: bytes, where: str) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise PB3Error("E_CORRUPT",
                       "file %s khong phai UTF-8 — patch chi ho tro file text"
                       % where, where="anti_wave.patch_manager")


def _split_text(text: str) -> Tuple[List[str], bool]:
    """text -> (lines khong chua '\\n', co ket thuc bang newline hay khong)."""
    if text == "":
        return [], True
    ends = text.endswith("\n")
    lines = text.split("\n")
    if ends:
        lines.pop()
    return lines, ends


def _join_text(lines: List[str], ends_with_newline: bool) -> str:
    if not lines:
        return ""
    return "\n".join(lines) + ("\n" if ends_with_newline else "")


def _diff_lines(old: Optional[bytes], new: Optional[bytes], path: str) -> List[str]:
    """Unified diff (difflib) cho 1 file; old/new None = file chua co / bi xoa."""
    old_text = _decode_utf8(old, path) if old is not None else None
    new_text = _decode_utf8(new, path) if new is not None else None
    old_lines = old_text.splitlines(keepends=True) if old_text is not None else []
    new_lines = new_text.splitlines(keepends=True) if new_text is not None else []
    if old is None:
        fromfile, tofile = "/dev/null", "b/" + path
    elif new is None:
        fromfile, tofile = "a/" + path, "/dev/null"
    else:
        fromfile, tofile = "a/" + path, "b/" + path
    out = ["diff --git a/%s b/%s\n" % (path, path)]
    # difflib KHONG emit marker no-newline (da do that 3.12: dong cuoi
    # khong co '\n' duoc yield tran, khong co marker) — neu join tran thi
    # 2 dong body se dinh vao nhau ("-y+z"). Them marker theo chuan git
    # cho moi dong body khong ket thuc bang newline.
    for line in difflib.unified_diff(old_lines, new_lines, fromfile=fromfile,
                                     tofile=tofile, n=3):
        if line.endswith("\n"):
            out.append(line)
        else:
            out.append(line + "\n" + _NO_NEWLINE + "\n")
    return out


def build_patch(base_files: Dict[str, Optional[bytes]],
                new_files: Dict[str, Optional[bytes]],
                base_commit_sha: str, *, test_status: str = "") -> Dict[str, Any]:
    """Tao patch tu 2 anh chup {rel_path: bytes|None} (None = file chua co).

    Tra ve dict envelope {schema_version, base_commit_sha, test_status,
    files [{path, sha256, bytes, action}], patch_text, checksum}.
    File khong doi duoc bo qua. Patch text vuot PATCH_MAX_BYTES ->
    PB3Error E_TOO_LARGE. File khong phai UTF-8 -> E_CORRUPT.
    """
    if not base_commit_sha:
        raise PB3Error("E_CONFIG_INVALID", "build_patch thieu base_commit_sha",
                       where="anti_wave.patch_manager")
    files_meta: List[Dict[str, Any]] = []
    chunks: List[str] = []
    for path in sorted(set(base_files) | set(new_files)):
        _check_rel_path(path)
        old = base_files.get(path)
        new = new_files.get(path)
        if old == new:
            continue
        if old is None:
            action = "added"
            digest, size = sha256_bytes(new), len(new)
        elif new is None:
            action = "deleted"
            digest, size = None, 0
        else:
            action = "modified"
            digest, size = sha256_bytes(new), len(new)
        files_meta.append({"path": path, "sha256": digest, "bytes": size,
                           "action": action})
        chunks.extend(_diff_lines(old, new, path))
    patch_text = "".join(chunks)
    if len(patch_text.encode("utf-8")) > PATCH_MAX_BYTES:
        raise PB3Error("E_TOO_LARGE",
                       "patch vuot %d byte (%d byte)"
                       % (PATCH_MAX_BYTES, len(patch_text.encode("utf-8"))),
                       where="anti_wave.patch_manager")
    patch = {
        "schema_version": PATCH_SCHEMA_VERSION,
        "base_commit_sha": base_commit_sha,
        "test_status": test_status,
        "files": files_meta,
        "patch_text": patch_text,
    }
    patch["checksum"] = patch_checksum(patch)
    return patch


def validate_patch(patch: Any) -> List[str]:
    """Kiem tra envelope patch (cau truc + checksum + diff + bi mat).

    Tra ve danh sach van de (rong = hop le). Kiem tra TINH, khong ap dung.
    """
    problems: List[str] = []
    if not isinstance(patch, dict):
        return ["patch khong phai JSON object"]
    if patch.get("schema_version") != PATCH_SCHEMA_VERSION:
        problems.append("schema_version khong phai v%d" % PATCH_SCHEMA_VERSION)
    if not patch.get("base_commit_sha"):
        problems.append("thieu base_commit_sha")
    if not isinstance(patch.get("files"), list):
        problems.append("files khong phai list")
    else:
        for entry in patch["files"]:
            if not isinstance(entry, dict):
                problems.append("files co muc khong phai object")
                break
            if not {"path", "sha256", "bytes", "action"} <= set(entry):
                problems.append("files co muc thieu path/sha256/bytes/action")
                break
            if entry.get("action") not in ACTIONS:
                problems.append("action khong hop le: %s" % entry.get("action"))
                break
            prob = _path_problem(entry.get("path", ""))
            if prob is not None:
                problems.append(prob)
                break
            if entry.get("action") != "deleted":
                sha = entry.get("sha256")
                if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha or ""):
                    problems.append("sha256 khong hop le cho %s" % entry.get("path"))
                    break
    patch_text = patch.get("patch_text")
    if not isinstance(patch_text, str):
        problems.append("patch_text khong phai string")
    else:
        problems.extend(validate_patch_text(patch_text))
    if patch.get("checksum") != patch_checksum(patch):
        problems.append("checksum patch khong khop (patch bi sua/hong)")
    return problems


def dumps_patch(patch: Dict[str, Any]) -> bytes:
    """Envelope patch -> bytes JSON (validate + bam lai checksum)."""
    problems = validate_patch(patch)
    if problems:
        raise PB3Error("E_CORRUPT", "patch khong hop le: %s" % "; ".join(problems),
                       where="anti_wave.patch_manager")
    cp = dict(patch)
    cp["checksum"] = patch_checksum(cp)
    return _canonical(cp) + b"\n"


def loads_patch(data: bytes, where: str = "<memory>") -> Dict[str, Any]:
    """Bytes JSON -> envelope patch (validate day du; sai -> E_CORRUPT)."""
    try:
        payload = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise PB3Error("E_CORRUPT", "patch khong phai JSON hop le: %s"
                       % type(exc).__name__, where=where)
    problems = validate_patch(payload)
    if problems:
        raise PB3Error("E_CORRUPT", "patch khong hop le: %s" % "; ".join(problems),
                       where=where)
    return payload


def files_for_checkpoint(patch: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Metadata file cua patch dang checkpoint changed_files [{path, sha256, bytes}].

    File bi xoa: sha256 None, bytes 0 (khoa van co du de checkpoint._validate
    chap nhan).
    """
    return [{"path": e["path"], "sha256": e["sha256"], "bytes": e["bytes"]}
            for e in patch.get("files", [])]


# ------------------------------------------------------------- parse diff

def _parse_patch(patch_text: str) -> List[Dict[str, Any]]:
    """Parse unified diff thanh cac section file.

    Moi section: {old_path, new_path, action, hunks: [{old_start,
    old_count, new_start, new_count, lines: [(tag, text, no_newline)]}]}.
    tag: ' ' context, '-' xoa, '+' them. Sai cau truc -> E_CORRUPT.
    """
    raw = patch_text.split("\n")
    if raw and raw[-1] == "":
        raw.pop()
    sections: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    i = 0
    n = len(raw)
    while i < n:
        line = raw[i]
        m = _DIFF_GIT_RE.match(line)
        if m:
            cur = {"old_path": m.group(1), "new_path": m.group(2),
                   "action": "modified", "hunks": []}
            sections.append(cur)
            i += 1
            continue
        if line.startswith("--- ") and cur is None:
            # Diff khong co header "diff --git" (validate_patch_text chap
            # nhan dang ---/+++): bat dau section moi tu day.
            cur = {"old_path": None, "new_path": None, "action": "modified",
                   "hunks": []}
            sections.append(cur)
            # roi xuong xu ly --- o duoi (khong tang i).
        if cur is None:
            raise PB3Error("E_CORRUPT", "patch co dong ngoai section diff: %r"
                           % line[:60], where="anti_wave.patch_manager")
        if line.startswith("--- "):
            old = line[4:].strip().split("\t")[0].strip()
            cur["old_path"] = None if old == "/dev/null" else old[2:] if old.startswith("a/") else old
            i += 1
            if i >= n or not raw[i].startswith("+++ "):
                raise PB3Error("E_CORRUPT", "thieu dong +++ sau --- cho %s"
                               % (cur["old_path"] or "?"),
                               where="anti_wave.patch_manager")
            new = raw[i][4:].strip().split("\t")[0].strip()
            cur["new_path"] = None if new == "/dev/null" else new[2:] if new.startswith("b/") else new
            if cur["old_path"] is None:
                cur["action"] = "added"
            elif cur["new_path"] is None:
                cur["action"] = "deleted"
            i += 1
            continue
        if line.startswith("+++ "):
            raise PB3Error("E_CORRUPT", "dong +++ khong co --- truoc do",
                           where="anti_wave.patch_manager")
        hm = _HUNK_RE.match(line)
        if hm:
            old_start = int(hm.group(1))
            old_count = int(hm.group(2)) if hm.group(2) is not None else 1
            new_start = int(hm.group(3))
            new_count = int(hm.group(4)) if hm.group(4) is not None else 1
            hunk = {"old_start": old_start, "old_count": old_count,
                    "new_start": new_start, "new_count": new_count,
                    "lines": []}
            cur["hunks"].append(hunk)
            i += 1
            while i < n:
                bl = raw[i]
                if bl.startswith("@@ ") or bl.startswith("diff --git ") \
                        or bl.startswith("--- "):
                    break
                if bl == _NO_NEWLINE:
                    if not hunk["lines"]:
                        raise PB3Error("E_CORRUPT",
                                       "marker no-newline khong co dong truoc do",
                                       where="anti_wave.patch_manager")
                    tag, text, _ = hunk["lines"][-1]
                    hunk["lines"][-1] = (tag, text, True)
                    i += 1
                    continue
                if bl[:1] in (" ", "-", "+"):
                    hunk["lines"].append((bl[0], bl[1:], False))
                    i += 1
                    continue
                raise PB3Error("E_CORRUPT", "dong hunk khong hop le: %r" % bl[:60],
                               where="anti_wave.patch_manager")
            old_seen = sum(1 for t, _, _ in hunk["lines"] if t in (" ", "-"))
            new_seen = sum(1 for t, _, _ in hunk["lines"] if t in (" ", "+"))
            if old_seen != old_count or new_seen != new_count:
                raise PB3Error("E_CORRUPT",
                               "hunk @@ -%d +%d sai so dong (thay %d/%d)"
                               % (old_start, new_start, old_seen, new_seen),
                               where="anti_wave.patch_manager")
            continue
        if line.startswith(_IGNORED_PREFIXES):
            if line.startswith("Binary files "):
                raise PB3Error("E_CORRUPT",
                               "patch chua file nhi phan — khong ho tro",
                               where="anti_wave.patch_manager")
            i += 1
            continue
        raise PB3Error("E_CORRUPT", "dong patch khong hop le: %r" % line[:60],
                       where="anti_wave.patch_manager")
    for sec in sections:
        if not sec["hunks"]:
            raise PB3Error("E_CORRUPT", "section diff khong co hunk @@ nao",
                           where="anti_wave.patch_manager")
        path = sec["new_path"] if sec["action"] == "added" else sec["old_path"]
        if path is None:
            raise PB3Error("E_CORRUPT", "section diff thieu duong dan",
                           where="anti_wave.patch_manager")
        sec["path"] = path
        _check_rel_path(path)
    return sections


def _apply_hunks(old_lines: List[str], old_eol: bool,
                 hunks: List[Dict[str, Any]], path: str) -> Tuple[List[str], bool]:
    """Ap cac hunk len old_lines; tra ve (new_lines, new_eol).

    Ngu canh / dong xoa phai khop CHINH XAC — lech la E_CORRUPT, khong
    doan. Hunk chong len nhau hoac vuot cuoi file cung E_CORRUPT.
    """
    new_lines: List[str] = []
    pos = 0  # chi so dong cu tiep theo chua xu ly
    # Marker "\ No newline at end of file" chi hop le neu dong do la dong
    # CUOI file o phia do (' '/'-' = phia cu, ' '/'+' = phia moi). Dong
    # sau marker ma con tieu thu dong cung phia -> diff hong. Dong khac
    # phia sau marker van hop le (vd '-y' co marker roi '+z' — phia moi
    # tiep tuc, phia cu da het).
    marker_old = False
    marker_new = False
    for hunk in hunks:
        start = hunk["old_start"] - 1 if hunk["old_start"] > 0 else 0
        if start < pos:
            raise PB3Error("E_CORRUPT", "hunk chong len nhau o %s" % path,
                           where="anti_wave.patch_manager")
        if start > len(old_lines):
            raise PB3Error("E_CORRUPT", "hunk vuot cuoi file %s" % path,
                           where="anti_wave.patch_manager")
        new_lines.extend(old_lines[pos:start])
        pos = start
        for tag, text, no_nl in hunk["lines"]:
            if tag in (" ", "-") and marker_old:
                raise PB3Error("E_CORRUPT",
                               "marker no-newline giua file %s" % path,
                               where="anti_wave.patch_manager")
            if tag in (" ", "+") and marker_new:
                raise PB3Error("E_CORRUPT",
                               "marker no-newline giua file %s" % path,
                               where="anti_wave.patch_manager")
            if tag == " ":
                if pos >= len(old_lines) or old_lines[pos] != text:
                    raise PB3Error("E_CORRUPT",
                                   "ngu canh khong khop o %s (dong %d)"
                                   % (path, pos + 1),
                                   where="anti_wave.patch_manager")
                new_lines.append(text)
                pos += 1
                if no_nl:
                    marker_old = marker_new = True
            elif tag == "-":
                if pos >= len(old_lines) or old_lines[pos] != text:
                    raise PB3Error("E_CORRUPT",
                                   "dong xoa khong khop o %s (dong %d)"
                                   % (path, pos + 1),
                                   where="anti_wave.patch_manager")
                pos += 1
                if no_nl:
                    marker_old = True
            else:  # "+"
                new_lines.append(text)
                if no_nl:
                    marker_new = True
    new_lines.extend(old_lines[pos:])
    # Marker phia cu chi hop le neu dong duoc danh dau la dong cuoi file
    # cu (khong con dong cu nao chua xu ly sau no).
    if marker_old and pos < len(old_lines):
        raise PB3Error("E_CORRUPT", "marker no-newline giua file %s" % path,
                       where="anti_wave.patch_manager")
    if marker_old and old_eol:
        raise PB3Error("E_CORRUPT",
                       "file %s ket thuc bang newline nhung patch noi khong"
                       % path, where="anti_wave.patch_manager")
    if not marker_old and not old_eol and old_lines:
        raise PB3Error("E_CORRUPT",
                       "file %s khong ket thuc bang newline nhung patch noi co"
                       % path, where="anti_wave.patch_manager")
    new_eol = not marker_new if new_lines else True
    return new_lines, new_eol


# ------------------------------------------------------------- ap patch

def _resolve_under(root: Path, rel: str) -> Path:
    """Duong dan dich nam trong root (kiem tra lexical, chong traversal)."""
    target = (root / rel).resolve() if False else Path(os.path.normpath(str(root / rel)))
    root_norm = Path(os.path.normpath(str(root)))
    try:
        target.relative_to(root_norm)
    except ValueError:
        raise PB3Error("E_UNSAFE_PATH", "duong dan thoat root: %s" % rel,
                       where="anti_wave.patch_manager")
    return target


def apply_patch(patch, root, *, dry_run: bool = False,
                expected_base_sha: Optional[str] = None,
                expected_files: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Ap patch len cay thu muc `root`.

    patch: envelope dict (validate checksum + diff + bi mat) hoac str
    (diff thuan, chi validate diff). expected_base_sha: neu cho, base
    cua patch phai khop (lech -> E_CONFIG_INVALID). expected_files:
    metadata [{path, sha256, bytes}] de verify sha256 sau khi ap; mac
    dinh lay tu envelope (neu co).

    Moi file duoc tinh trong RAM truoc, verify het moi ghi. Ghi qua
    write_bytes_guarded (song EPERM -> E_WRITE_WAVE, khong de file
    0 byte). dry_run: chi tinh + verify, khong ghi gi.

    Tra ve {"dry_run", "applied": [{path, action, bytes}], "files",
    "bytes_written"}.
    """
    if isinstance(patch, dict):
        problems = validate_patch(patch)
        if problems:
            raise PB3Error("E_CORRUPT", "patch khong hop le: %s" % "; ".join(problems),
                           where="anti_wave.patch_manager")
        patch_text = patch["patch_text"]
        if expected_base_sha is not None and patch.get("base_commit_sha") != expected_base_sha:
            raise PB3Error("E_CONFIG_INVALID",
                           "base_commit_sha cua patch (%s) khong khop ky vong (%s)"
                           % ((patch.get("base_commit_sha") or "")[:12],
                              expected_base_sha[:12]),
                           where="anti_wave.patch_manager")
        if expected_files is None:
            expected_files = patch.get("files")
    elif isinstance(patch, str):
        problems = validate_patch_text(patch)
        if problems:
            raise PB3Error("E_CORRUPT", "patch khong hop le: %s" % "; ".join(problems),
                           where="anti_wave.patch_manager")
        patch_text = patch
    else:
        raise PB3Error("E_CORRUPT", "patch phai la dict envelope hoac str diff",
                       where="anti_wave.patch_manager")

    sections = _parse_patch(patch_text)
    want_sha = {e["path"]: e for e in (expected_files or []) if isinstance(e, dict)}

    root_p = Path(root)
    computed: List[Tuple[str, str, Optional[bytes]]] = []  # (path, action, new_bytes)
    for sec in sections:
        path = sec["path"]
        target = _resolve_under(root_p, path)
        if sec["action"] == "added":
            if target.exists():
                raise PB3Error("E_CORRUPT", "file %s da ton tai — khong ap patch them moi"
                               % path, where="anti_wave.patch_manager")
            old_lines, old_eol = [], True
            new_lines, new_eol = _apply_hunks(old_lines, old_eol, sec["hunks"], path)
            new_bytes = _join_text(new_lines, new_eol).encode("utf-8")
            computed.append((path, "added", new_bytes))
        elif sec["action"] == "deleted":
            if not target.is_file():
                raise PB3Error("E_CORRUPT", "file %s khong ton tai — khong ap patch xoa"
                               % path, where="anti_wave.patch_manager")
            try:
                old_bytes = target.read_bytes()
            except OSError as exc:
                raise error_from_exception(exc, where=str(target))
            old_text = _decode_utf8(old_bytes, path)
            old_lines, old_eol = _split_text(old_text)
            new_lines, new_eol = _apply_hunks(old_lines, old_eol, sec["hunks"], path)
            if new_lines:
                raise PB3Error("E_CORRUPT", "patch xoa %s nhung van con dong moi"
                               % path, where="anti_wave.patch_manager")
            computed.append((path, "deleted", None))
        else:
            if not target.is_file():
                raise PB3Error("E_CORRUPT", "file goc %s khong ton tai" % path,
                               where="anti_wave.patch_manager")
            try:
                old_bytes = target.read_bytes()
            except OSError as exc:
                raise error_from_exception(exc, where=str(target))
            old_text = _decode_utf8(old_bytes, path)
            old_lines, old_eol = _split_text(old_text)
            new_lines, new_eol = _apply_hunks(old_lines, old_eol, sec["hunks"], path)
            new_bytes = _join_text(new_lines, new_eol).encode("utf-8")
            computed.append((path, "modified", new_bytes))

    # Verify sha256 sau khi ap (neu co metadata) TRUOC khi ghi bat ky file nao.
    for path, action, new_bytes in computed:
        want = want_sha.get(path)
        if want is None:
            continue
        if action == "deleted":
            if want.get("sha256") is not None:
                raise PB3Error("E_CORRUPT",
                               "metadata %s noi file bi xoa nhung van co sha256" % path,
                               where="anti_wave.patch_manager")
            continue
        if new_bytes is None or sha256_bytes(new_bytes) != want.get("sha256"):
            raise PB3Error("E_CORRUPT",
                           "sha256 sau khi ap khong khop metadata cho %s" % path,
                           where="anti_wave.patch_manager")
        if want.get("bytes") is not None and len(new_bytes) != want["bytes"]:
            raise PB3Error("E_CORRUPT",
                           "kich thuoc sau khi ap khong khop metadata cho %s" % path,
                           where="anti_wave.patch_manager")

    applied: List[Dict[str, Any]] = []
    written = 0
    if not dry_run:
        for path, action, new_bytes in computed:
            target = _resolve_under(root_p, path)
            if action == "deleted":
                try:
                    os.unlink(str(target))
                except OSError as exc:
                    raise error_from_exception(exc, where=str(target))
                applied.append({"path": path, "action": action, "bytes": 0})
            else:
                write_bytes_guarded(target, new_bytes)
                written += len(new_bytes)
                applied.append({"path": path, "action": action, "bytes": len(new_bytes)})
    else:
        for path, action, new_bytes in computed:
            applied.append({"path": path, "action": action,
                            "bytes": 0 if new_bytes is None else len(new_bytes)})
    return {"dry_run": dry_run, "applied": applied, "files": len(applied),
            "bytes_written": 0 if dry_run else written}


def apply_summary(report: Dict[str, Any]) -> str:
    """Tom tat tieng Viet ket qua apply_patch."""
    lines = ["Áp patch: %d file (%s)." % (
        report.get("files", 0), "dry-run, chưa ghi" if report.get("dry_run")
        else "%d byte đã ghi" % report.get("bytes_written", 0))]
    for entry in report.get("applied", []):
        verb = {"added": "thêm mới", "modified": "sửa", "deleted": "xóa"}.get(
            entry.get("action"), entry.get("action"))
        lines.append("  - %s: %s (%s byte)" % (verb, entry.get("path"),
                                              entry.get("bytes")))
    return "\n".join(lines)


# ------------------------------------------------------------- GitHub

def save_patch_to_github(sync, agent_id: str, patch: Dict[str, Any],
                         branch: str,
                         expected_base_sha: Optional[str] = None) -> Dict[str, str]:
    """Luu patch len GitHub qua GitHubSync.save_patch (validate truoc khi ghi).

    sync: GitHubSync hoac fake co phuong thuc save_patch/patch_path.
    Tra ve ket qua cua save_patch {path, blob_sha, commit_sha, sha256}.
    """
    problems = validate_patch(patch)
    if problems:
        raise PB3Error("E_CORRUPT", "patch khong hop le: %s" % "; ".join(problems),
                       where="anti_wave.patch_manager")
    return sync.save_patch(agent_id, patch["patch_text"], branch,
                           expected_base_sha=expected_base_sha)


def load_patch_text_from_github(sync, agent_id: str, branch: str) -> str:
    """Tai patch text tu GitHub (sync.get_file + validate). Chua co patch
    hoac patch hong -> PB3Error E_CORRUPT (fail-closed)."""
    found = sync.get_file(branch, sync.patch_path(agent_id))
    if found is None:
        raise PB3Error("E_CORRUPT", "chua co patch cho agent %s tren nhanh %s"
                       % (agent_id, branch), where="anti_wave.patch_manager")
    try:
        text = found[1].decode("utf-8")
    except UnicodeDecodeError:
        raise PB3Error("E_CORRUPT", "patch cua %s khong phai UTF-8" % agent_id,
                       where="anti_wave.patch_manager")
    problems = validate_patch_text(text)
    if problems:
        raise PB3Error("E_CORRUPT", "patch cua %s khong hop le: %s"
                       % (agent_id, "; ".join(problems)),
                       where="anti_wave.patch_manager")
    return text
