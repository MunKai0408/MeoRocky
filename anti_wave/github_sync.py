"""Dong bo checkpoint + patch qua Bear API (GitHub REST) — Anti-Wave Phase 3.

Muc dich: khi VM gap song EPERM (ghi file local loi), agent van luu/duoc
checkpoint va patch len GitHub qua Bear API — do la LUU, khong phai THUC THI.
Module nay KHONG chay test, KHONG viet file local, KHONG mo terminal.

Quy tac an toan (bat buoc, theo spec MASTER v1.1/v1.2):
  * Token KHONG bao gio co trong code, log, checkpoint hay patch. Token chi
    doc tu bien moi truong (mac dinh GITHUB_BEAR_TOKEN) hoac truyen truc tiep
    vao constructor; moi thong bao loi deu redact token truoc khi raise.
  * Tren VM nay, agent viet GitHub qua helper github_bear.py (connector
    custom.github-bear tu xu ly credential); module nay dung cho script/CI
    hoac agent nao da co token hop le trong env. Module KHONG tu doc
    credential cua connector, KHONG thao tac surrogate.
  * Ghi file len nhanh: LUON kiem tra head SHA cua nhanh truoc khi ghi
    (expected_base_sha); nhanh da dich -> fail-closed, khong ghi de.
  * Checkpoint RIENG tung agent: anti_wave/checkpoints/<agent_id>.json.
    Khong dung 1 file chung cho nhieu agent (nguy co ghi de).
  * Dispatch workflow: trung thuc ve luat GitHub — workflow_dispatch CHI
    dispatch duoc khi file workflow DA CO TREN main (default branch). Neu
    workflow chua len main, API tra 404; module bao ro dieu nay trong hint
    chu khong bao "da dispatch duoc". Bang chung quyen hien tai la 403->404,
    chua phai 204 (xem docs/ACTIONS_VERIFY.md).

Chi stdlib, Python 3.9 tuong thich. Khong phu thuoc pb3.core (de chay
doc lap trong scripts/ va tren runner CI).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

REPO = "MunKai0408/MeoRocky"
API_BASE = "https://api.github.com"
DEFAULT_TOKEN_ENV = "GITHUB_BEAR_TOKEN"

CHECKPOINTS_DIR = "anti_wave/checkpoints"
CHECKPOINT_VERSION = 1
CHECKPOINT_MAX_BYTES = 1024 * 1024  # 1 MiB — checkpoint la JSON nho
PATCH_MAX_BYTES = 1024 * 1024  # 1 MiB — patch vuot la bat thuong

# 9 truong bat buoc cua checkpoint v1 (spec MASTER v1.1; truong thu 10
# current_commit_sha la tuy chon). Phai khop anti_wave/checkpoint.py.
CHECKPOINT_REQUIRED_FIELDS = (
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

REDACTED = "[REDACTED]"

# Mau bi mat — heuristic, khong phai chung minh. Patch chua bat ky mau nao
# thi bi tu choi (fail-closed); khong co mau khong co nghia la an toan tuyet
# doi, chi la lop bao ve co ban.
_SECRET_PATTERNS = (
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"gho_[A-Za-z0-9]{20,}"),
    re.compile(r"hsurr:[A-Za-z0-9+/=_.-]+"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?i)(?:api[_-]?key|access[_-]?token|password)\s*=\s*['\"][^'\"]{8,}['\"]"),
)

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@", re.MULTILINE)


class SyncError(Exception):
    """Loi dong bo qua Bear API. `code` la ma ngan, `hint` la goi y tieng Viet.

    Thong diep KHONG bao gio chua token (moi chuoi di qua _redact truoc khi
    dua vao exception).
    """

    def __init__(self, code: str, message: str, hint: str = ""):
        super().__init__(message)
        self.code = code
        self.hint = hint


def _redact(text: str, token: Optional[str]) -> str:
    """Thay moi lan xuat hien cua token trong text bang [REDACTED]."""
    if not text:
        return text
    if token and token in text:
        text = text.replace(token, REDACTED)
    return text


def _canonical(payload: Dict[str, Any]) -> bytes:
    """JSON chuan hoa de bam checksum — PHAI KHOP anti_wave/checkpoint.py::_canonical
    (sap khoa, khong khoang trang thua, giu Unicode). Doi mot trong hai cho
    la checksum cua checkpoint cu se khong khop nua."""
    return json.dumps(payload, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def _checkpoint_checksum(payload: Dict[str, Any]) -> str:
    """sha256 hex cua payload KHONG tinh truong `checksum`."""
    clean = {k: v for k, v in payload.items() if k != "checksum"}
    return hashlib.sha256(_canonical(clean)).hexdigest()


def validate_checkpoint(payload: Any) -> Dict[str, Any]:
    """Kiem tra checkpoint v1 (cau truc + checksum); sai -> SyncError E_VALIDATION.

    Day la ban dung doc lap cua anti_wave/checkpoint.py::_validate (module
    nay khong import checkpoint de chay duoc ca khi checkpoint.py chua co
    tren nhanh — vd nhanh prep chi co github_sync.py).
    """
    if not isinstance(payload, dict):
        raise SyncError("E_VALIDATION", "checkpoint khong phai JSON object")
    if payload.get("schema_version") != CHECKPOINT_VERSION:
        raise SyncError(
            "E_VALIDATION",
            "checkpoint schema_version khong phai v%d" % CHECKPOINT_VERSION,
            hint="Checkpoint nay khong phai dinh dang v1 cua Anti-Wave; "
                 "kiem tra lai file hoac phien ban tao checkpoint.")
    missing = [f for f in CHECKPOINT_REQUIRED_FIELDS if f not in payload]
    if missing:
        raise SyncError(
            "E_VALIDATION",
            "checkpoint thieu truong: %s" % ", ".join(missing),
            hint="Checkpoint phai du 9 truong bat buoc theo spec MASTER v1.1.")
    if not isinstance(payload["changed_files"], list):
        raise SyncError("E_VALIDATION", "changed_files khong phai list")
    for entry in payload["changed_files"]:
        if not isinstance(entry, dict) or not {"path", "sha256", "bytes"} <= set(entry):
            raise SyncError(
                "E_VALIDATION", "changed_files co muc thieu path/sha256/bytes")
    if payload.get("checksum") != _checkpoint_checksum(payload):
        raise SyncError(
            "E_VALIDATION",
            "checksum checkpoint khong khop (file bi sua/hong)",
            hint="Khong dung checkpoint nay de tiep tuc; bao MASTER kiem tra "
                 "ban goc tren nhanh.")
    return payload


def validate_patch_text(patch_text: str) -> List[str]:
    """Kiem tra cau truc patch (unified diff) + quet bi mat.

    Tra ve danh sach van de (rong = hop le). Day la kiem tra tinh, khong
    ap dung patch — ap dung that la viec cua Phase 4 (patch_manager.py).
    """
    problems: List[str] = []
    if not isinstance(patch_text, str) or not patch_text.strip():
        return ["patch rong"]
    if len(patch_text.encode("utf-8")) > PATCH_MAX_BYTES:
        problems.append("patch vuot %d byte" % PATCH_MAX_BYTES)
    has_diff_header = patch_text.startswith("diff --git ") or (
        "\n--- " in patch_text and "\n+++ " in patch_text)
    if not has_diff_header:
        problems.append("patch khong co header diff (--git hoac ---/+++)")
    if not _HUNK_RE.search(patch_text):
        problems.append("patch khong co hunk @@ nao hop le")
    for line in patch_text.splitlines():
        if line.startswith(("--- ", "+++ ")):
            path = line[4:].strip().split("\t")[0].strip()
            if path in ("-", "/dev/null"):
                continue
            if path.startswith("/") or ".." in path.split("/"):
                problems.append("duong dan patch khong hop le: %s" % path)
                break
    for pattern in _SECRET_PATTERNS:
        if pattern.search(patch_text):
            problems.append("patch chua gia tri giong bi mat/token — tu choi")
            break
    return problems


def failset_from_junitxml(xml_bytes: bytes) -> Dict[str, Any]:
    """Parse junit XML (pytest --junitxml) thanh fail-set.

    Tra ve {tests, failures, errors, skipped, failed: [id...],
    error_ids: [id...]}. Id dang "classname::name". XML hong ->
    SyncError E_VALIDATION.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise SyncError("E_VALIDATION", "junit XML hong: %s" % exc)
    failed: List[str] = []
    error_ids: List[str] = []
    tests = failures = errors = skipped = 0
    for suite in root.iter("testsuite"):
        tests += int(suite.get("tests", 0) or 0)
        failures += int(suite.get("failures", 0) or 0)
        errors += int(suite.get("errors", 0) or 0)
        skipped += int(suite.get("skipped", 0) or 0)
        for case in suite.iter("testcase"):
            cid = "%s::%s" % (case.get("classname", "?"), case.get("name", "?"))
            if case.find("failure") is not None:
                failed.append(cid)
            if case.find("error") is not None:
                error_ids.append(cid)
    return {
        "tests": tests,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
        "failed": failed,
        "error_ids": error_ids,
    }


class GitHubSync:
    """Client nho goi GitHub REST qua Bear API (token qua env, khong hardcode)."""

    def __init__(self, repo: str = REPO, token: Optional[str] = None,
                 token_env: str = DEFAULT_TOKEN_ENV,
                 api_base: str = API_BASE, timeout: int = 30):
        self.repo = repo
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout
        self._token = token or os.environ.get(token_env)
        if not self._token:
            raise SyncError(
                "E_CONFIG",
                "thieu token GitHub (bien moi truong %s)" % token_env,
                hint="Dat token vao bien %s truoc khi chay; KHONG viet gia tri "
                     "token vao code, file hay log. Tren VM nay, ghi GitHub "
                     "qua helper github_bear.py cua connector custom.github-bear." % token_env)

    # -- lop thap: HTTP -------------------------------------------------
    def _request(self, method: str, path: str,
                 body: Optional[Dict[str, Any]] = None) -> Tuple[int, Any]:
        """Tra ve (status, json|None). Loi HTTP -> SyncError (da redact)."""
        url = self.api_base + path
        data = None
        headers = {
            "Authorization": "Bearer %s" % self._token,
            "Accept": "application/vnd.github+json",
            "User-Agent": "meorocky-anti-wave-sync",
        }
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                if not raw:
                    return resp.status, None
                return resp.status, json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", "replace")
            except Exception:
                detail = ""
            raise self._http_error(exc.code, path, detail)
        except urllib.error.URLError as exc:
            raise SyncError(
                "E_NETWORK", "khong goi duoc GitHub API: %s" % exc.reason,
                hint="Kiem tra mang / api.github.com co toi duoc khong.")

    def _http_error(self, status: int, path: str, detail: str) -> SyncError:
        detail = _redact(detail, self._token)
        path = _redact(path, self._token)
        if status == 401:
            return SyncError(
                "E_AUTH", "GitHub 401 o %s: token sai / het han / bi thu hoi" % path,
                hint="Kiem tra key cobonla con han khong; khong tao key moi "
                     "khi chua co lenh MASTER.")
        if status == 403:
            return SyncError(
                "E_FORBIDDEN",
                "GitHub 403 o %s: token thieu quyen hoac bi policy chan" % path,
                hint="Neu la dispatch workflow: can quyen Actions Read and "
                     "write tren token (da cap cho cobonla 12:17 +07). Neu la "
                     "ghi file workflow: can quyen Workflows Read and write.")
        if status == 404:
            return SyncError(
                "E_NOT_FOUND",
                "GitHub 404 o %s: khong tim thay resource" % path,
                hint="Kiem tra repo/branch/path. Neu la dispatch workflow: "
                     "file workflow phai DA CO TREN main (luat GitHub) — "
                     "xem docs/ACTIONS_VERIFY.md.")
        if status == 422:
            return SyncError(
                "E_VALIDATION",
                "GitHub 422 o %s: du lieu khong hop le (sai ref/input, hoac "
                "workflow khong co workflow_dispatch)" % path,
                hint="Kiem tra lai ref ton tai, input khop khai bao trong file YAML.")
        if status == 429:
            return SyncError(
                "E_RATE_LIMIT", "GitHub 429 o %s: bi gioi han tan suat" % path,
                hint="Cho mot luc roi thu lai; giam tan suat poll.")
        return SyncError("E_HTTP_%d" % status,
                         "GitHub %d o %s: %s" % (status, path, detail[:300]))

    # -- nhanh / file ----------------------------------------------------
    def get_branch_head(self, branch: str) -> str:
        """SHA commit hien tai cua nhanh (GET /git/ref/heads/<branch> — so it)."""
        status, result = self._request(
            "GET", "/repos/%s/git/ref/heads/%s" % (self.repo, branch))
        if status != 200 or not isinstance(result, dict):
            raise SyncError("E_NOT_FOUND", "khong doc duoc nhanh %s" % branch)
        return result["object"]["sha"]

    def get_file(self, branch: str, path: str) -> Optional[Tuple[str, bytes]]:
        """(blob_sha, noi dung) cua file tren nhanh; None neu file chua co (404)."""
        try:
            status, result = self._request(
                "GET", "/repos/%s/contents/%s?ref=%s" % (self.repo, path, branch))
        except SyncError as exc:
            if exc.code == "E_NOT_FOUND":
                return None
            raise
        if status != 200 or not isinstance(result, dict) or "content" not in result:
            raise SyncError("E_NOT_FOUND", "khong doc duoc file %s" % path)
        content = base64.b64decode(result["content"])
        return result["sha"], content

    def create_or_update_file(self, branch: str, path: str, content: bytes,
                              message: str,
                              expected_base_sha: Optional[str] = None) -> Dict[str, str]:
        """Tao hoac cap nhat 1 file tren nhanh qua Contents API.

        Kiem tra head SHA truoc khi ghi neu co expected_base_sha (nhanh da
        dich -> fail-closed, khong ghi de thay doi cua agent khac). Verify
        bang read-back + sha256 sau khi ghi. Tra ve {path, blob_sha,
        commit_sha, sha256}.
        """
        if expected_base_sha is not None:
            head = self.get_branch_head(branch)
            if head != expected_base_sha:
                raise SyncError(
                    "E_CONFLICT",
                    "nhanh %s da dich (%s != %s) — dung ghi de"
                    % (branch, head[:12], expected_base_sha[:12]),
                    hint="Doc lai head + checkpoint moi nhat truoc khi ghi; "
                         "neu xung dot thi DUNG va bao MASTER.")
        existing = self.get_file(branch, path)
        body: Dict[str, Any] = {
            "message": message,
            "content": base64.b64encode(content).decode("ascii"),
            "branch": branch,
        }
        if existing is not None:
            body["sha"] = existing[0]
        status, result = self._request(
            "PUT", "/repos/%s/contents/%s" % (self.repo, path), body)
        if status not in (200, 201) or not isinstance(result, dict):
            raise SyncError("E_HTTP_%d" % status, "ghi file %s that bai" % path)
        # Verify bang read-back: noi dung doc lai phai khop sha256.
        back = self.get_file(branch, path)
        if back is None:
            raise SyncError("E_VALIDATION", "ghi xong nhung doc lai khong thay %s" % path)
        digest = hashlib.sha256(content).hexdigest()
        if hashlib.sha256(back[1]).hexdigest() != digest:
            raise SyncError(
                "E_VALIDATION",
                "read-back %s khong khop sha256 (ghi loi / file bi sua giua chung)" % path)
        return {
            "path": path,
            "blob_sha": result["content"]["sha"],
            "commit_sha": result["commit"]["sha"],
            "sha256": digest,
        }

    def create_branch(self, name: str, from_sha: str) -> str:
        """Tao nhanh moi tu commit SHA (POST /git/refs). Tra ve SHA head."""
        status, result = self._request(
            "POST", "/repos/%s/git/refs" % self.repo,
            {"ref": "refs/heads/%s" % name, "sha": from_sha})
        if status != 201 or not isinstance(result, dict):
            raise SyncError("E_HTTP_%d" % status, "tao nhanh %s that bai" % name)
        return result["object"]["sha"]

    # -- checkpoint / patch ----------------------------------------------
    def checkpoint_path(self, agent_id: str) -> str:
        return "%s/%s.json" % (CHECKPOINTS_DIR, agent_id)

    def patch_path(self, agent_id: str) -> str:
        return "%s/%s.patch" % (CHECKPOINTS_DIR, agent_id)

    def save_checkpoint(self, agent_id: str, checkpoint: Dict[str, Any],
                        branch: str,
                        expected_base_sha: Optional[str] = None) -> Dict[str, str]:
        """Luu checkpoint JSON v1 len nhanh (validate truoc khi ghi)."""
        validate_checkpoint(checkpoint)
        data = _canonical(checkpoint) + b"\n"
        if len(data) > CHECKPOINT_MAX_BYTES:
            raise SyncError("E_VALIDATION", "checkpoint vuot %d byte" % CHECKPOINT_MAX_BYTES)
        return self.create_or_update_file(
            branch, self.checkpoint_path(agent_id), data,
            "anti-wave: checkpoint %s (%s)" % (agent_id, checkpoint.get("task_id", "?")),
            expected_base_sha=expected_base_sha)

    def load_checkpoint(self, agent_id: str, branch: str) -> Dict[str, Any]:
        """Doc + validate checkpoint cua 1 agent (sai checksum -> fail-closed)."""
        found = self.get_file(branch, self.checkpoint_path(agent_id))
        if found is None:
            raise SyncError(
                "E_NOT_FOUND",
                "chua co checkpoint cho agent %s tren nhanh %s" % (agent_id, branch))
        try:
            payload = json.loads(found[1].decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise SyncError("E_VALIDATION", "checkpoint cua %s hong (khong phai JSON)" % agent_id)
        return validate_checkpoint(payload)

    def list_checkpoints(self, branch: str) -> List[str]:
        """Danh sach agent_id da co checkpoint tren nhanh (404 -> [])."""
        try:
            status, result = self._request(
                "GET", "/repos/%s/contents/%s?ref=%s" % (self.repo, CHECKPOINTS_DIR, branch))
        except SyncError as exc:
            if exc.code == "E_NOT_FOUND":
                return []
            raise
        if status != 200 or not isinstance(result, list):
            return []
        return sorted(e["name"][:-5] for e in result
                      if e.get("type") == "file" and e["name"].endswith(".json"))

    def save_patch(self, agent_id: str, patch_text: str, branch: str,
                   expected_base_sha: Optional[str] = None) -> Dict[str, str]:
        """Luu patch (unified diff) kem checkpoint. Kiem tra tinh truoc khi ghi."""
        problems = validate_patch_text(patch_text)
        if problems:
            raise SyncError(
                "E_VALIDATION", "patch khong hop le: %s" % "; ".join(problems))
        return self.create_or_update_file(
            branch, self.patch_path(agent_id), patch_text.encode("utf-8"),
            "anti-wave: patch %s" % agent_id,
            expected_base_sha=expected_base_sha)

    # -- Actions: dispatch / poll / artifact -------------------------------
    def dispatch_workflow(self, workflow_file: str, ref: str,
                          inputs: Optional[Dict[str, str]] = None) -> bool:
        """Dispatch workflow_dispatch. True neu 204 (da nhan lenh).

        404 o day thuong la workflow chua co tren main (luat GitHub), khong
        phai sai quyen — xem hint. 403 moi la thieu quyen Actions.
        """
        body: Dict[str, Any] = {"ref": ref}
        if inputs:
            body["inputs"] = inputs
        status, _ = self._request(
            "POST",
            "/repos/%s/actions/workflows/%s/dispatches" % (self.repo, workflow_file),
            body)
        if status == 204:
            return True
        raise SyncError("E_HTTP_%d" % status, "dispatch %s that bai" % workflow_file)

    def list_workflow_runs(self, workflow_file: str,
                           per_page: int = 5) -> List[Dict[str, Any]]:
        """Run moi nhat cua 1 workflow (moi nhat truoc)."""
        status, result = self._request(
            "GET",
            "/repos/%s/actions/workflows/%s/runs?per_page=%d"
            % (self.repo, workflow_file, per_page))
        if status != 200 or not isinstance(result, dict):
            raise SyncError("E_NOT_FOUND", "khong doc duoc runs cua %s" % workflow_file)
        runs = []
        for run in result.get("workflow_runs", []):
            runs.append({
                "id": run.get("id"),
                "status": run.get("status"),
                "conclusion": run.get("conclusion"),
                "head_branch": run.get("head_branch"),
                "head_sha": run.get("head_sha"),
                "created_at": run.get("created_at"),
                "html_url": run.get("html_url"),
            })
        return runs

    def get_workflow_run(self, run_id: int) -> Dict[str, Any]:
        status, result = self._request(
            "GET", "/repos/%s/actions/runs/%d" % (self.repo, run_id))
        if status != 200 or not isinstance(result, dict):
            raise SyncError("E_NOT_FOUND", "khong doc duoc run %d" % run_id)
        return result

    def list_run_artifacts(self, run_id: int) -> List[Dict[str, Any]]:
        status, result = self._request(
            "GET", "/repos/%s/actions/runs/%d/artifacts" % (self.repo, run_id))
        if status != 200 or not isinstance(result, dict):
            raise SyncError("E_NOT_FOUND", "khong doc duoc artifacts cua run %d" % run_id)
        return [{
            "id": a.get("id"),
            "name": a.get("name"),
            "size_in_bytes": a.get("size_in_bytes"),
            "expired": a.get("expired"),
        } for a in result.get("artifacts", [])]

    def download_artifact_bytes(self, artifact_id: int) -> bytes:
        """Tai artifact (zip) ve RAM — caller tu quyet dinh ghi di dau
        (ghi local trong song EPERM co the loi; ghi qua Bear thi dung
        create_or_update_file)."""
        url = "/repos/%s/actions/artifacts/%d/zip" % (self.repo, artifact_id)
        req = urllib.request.Request(
            self.api_base + url,
            headers={"Authorization": "Bearer %s" % self._token,
                     "Accept": "application/vnd.github+json",
                     "User-Agent": "meorocky-anti-wave-sync"},
            method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            raise self._http_error(exc.code, url, "")
        except urllib.error.URLError as exc:
            raise SyncError("E_NETWORK", "tai artifact that bai: %s" % exc.reason)

    def poll_workflow_run(self, run_id: int, timeout_s: int = 1800,
                          interval_s: int = 30) -> Dict[str, Any]:
        """Poll run cho toi khi completed hoac het timeout. Tra ve run cuoi.

        Timeout -> SyncError E_VALIDATION (khong phai that bai cua run).
        """
        deadline = time.time() + timeout_s
        last: Dict[str, Any] = {}
        while True:
            last = self.get_workflow_run(run_id)
            if last.get("status") == "completed":
                return last
            if time.time() >= deadline:
                raise SyncError(
                    "E_VALIDATION",
                    "poll run %d het %ds van chua xong (status=%s)"
                    % (run_id, timeout_s, last.get("status")),
                    hint="Run van dang chay — poll lai sau, hoac xem truc tiep "
                         "tren tab Actions cua repo.")
            time.sleep(interval_s)
