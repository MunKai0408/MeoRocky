"""Conformance kit cho plugin (đặc tả 4.8; Claude 03 đợt 2, nhiệm vụ B1).

`run_checks(plugin, runner, fixture=...)` chạy các hook của plugin trên một VFS fixture NHỎ và trả
`ConformanceReport`: bảng PASS/FAIL/SKIP, mỗi dòng có lý do. `runner` là bộ chạy hook được TIÊM vào (thực tế:
`registry.isolation.run_hook` = timeout + guard best effort), nên plugin treo/mở socket/ghi ngoài tempdir bị báo FAIL thay
vì làm hỏng bộ kiểm. Tầng sdk không import registry (luật kiến trúc, tests/test_registry.py), vì vậy cổng vào dùng thực tế là
`registry.conformance.run_conformance(plugin|record|lớp, fixture=...)`, hàm đó nạp/giải quyết plugin và gắn guard.
Không có runner thì KHÔNG có mục guard hợp lệ: không được chạy hook trần rồi báo PASS.

Các mục (theo thứ tự):
  manifest      manifest hợp lệ, id/kind/api_version đúng, record nạp được
  readonly      plugin KHÔNG khai 'write': build/verify phải trả E_WRITE_DISABLED (không giả vờ ghi được)
  detect        không crash, trả list[Evidence]
  candidates    list_candidates trả list[Candidate] trỏ tới path CÓ trong VFS, adapter_id khớp, score thuộc [0,1]
  probe         trả dict
  parse         trả LocTable đúng schema (to_dict/from_dict round-trip), adapter_id khớp, mỗi entry có source
  write         (chỉ plugin 'write') build -> bytes, verify chạy được và ok=True
  container     (capability 'container') open_container trả VFS
  hints         (capability 'hint') hints trả dict
  selftest      manifest khai selftest=true thì selftest phải trả ok=True (None/False = FAIL: không giả PASS)
  guard         không hook nào vi phạm guard (E_PLUGIN_FORBIDDEN) hay quá thời gian (E_PLUGIN_TIMEOUT)

Quy ước trạng thái: PASS = đã chạy và đạt; FAIL = đã chạy và không đạt (hoặc plugin không nạp được);
SKIP = không có gì để chạy (kèm lý do, ví dụ fixture rỗng). KẾT QUẢ chung là FAIL nếu có ít nhất một FAIL;
SKIP không biến thành PASS ở mức từng mục - đọc cột lý do. Fixture rỗng nghĩa là parse/write/probe CHƯA được kiểm.

Giới hạn trung thực: guard là best effort (xem registry/isolation.py), không phải sandbox. Bộ kiểm chỉ chứng minh
plugin đạt hợp đồng SDK trên fixture đã cho, không chứng minh đúng trên game thật.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core import safe_io
from core.color import paint
from core.errors import PB3Error, sanitize_detail
from core.limits import Limits
from core.progress import display_width, truncate_display
from sdk.api import Plugin, PluginContext
from sdk.types import Candidate, Evidence, LocTable, SourceRef
from sdk.vfs import VFS, MemoryVFS, check_vpath

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
CHECK_NAMES = ("manifest", "readonly", "detect", "candidates", "probe", "parse", "write", "container", "hints",
               "selftest", "guard")

MAX_FIXTURE_FILES = 200
MAX_FIXTURE_FILE_BYTES = 8 * 1024 * 1024
MAX_FIXTURE_TOTAL_BYTES = 32 * 1024 * 1024


@dataclass
class Check:
    name: str
    status: str
    reason: str = ""


@dataclass
class ConformanceReport:
    plugin_id: str
    checks: List[Check] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)       # giới hạn/điều kiện chạy (fixture, guard, luồng bị bỏ lại)
    fixture_files: int = 0
    untested_candidates: int = 0   # ứng viên có mà chưa kiểm (vượt max_candidates)

    def add(self, name: str, status: str, reason: str = "") -> Check:
        c = Check(name, status, sanitize_detail(reason, 300) if reason else "")
        self.checks.append(c)
        return c

    def get(self, name: str) -> Optional[Check]:
        for c in self.checks:
            if c.name == name:
                return c
        return None

    def counts(self) -> Dict[str, int]:
        out = {PASS: 0, FAIL: 0, SKIP: 0}
        for c in self.checks:
            out[c.status] += 1
        return out

    @property
    def ok(self) -> bool:
        return self.counts()[FAIL] == 0

    @property
    def verdict(self) -> str:
        return PASS if self.ok else FAIL

    @property
    def data_tested(self) -> bool:
        """True nếu `parse` thật sự chạy (PASS) trên dữ liệu fixture. SKIP (fixture rỗng, không ứng viên, plugin
        không khai 'read') = CHƯA kiểm bằng dữ liệu, dù KẾT QUẢ chung có thể vẫn là PASS."""
        c = self.get("parse")
        return c is not None and c.status == PASS

    @property
    def verdict_label(self) -> str:
        """Nhãn hiển thị: PASS mà chưa kiểm dữ liệu thì nói rõ, để không bị đọc thành "parse đúng"."""
        if self.verdict == PASS and not self.data_tested:
            return "PASS (CHƯA KIỂM DỮ LIỆU)"
        return self.verdict

    def failed(self) -> List[str]:
        return [c.name for c in self.checks if c.status == FAIL]

    def to_dict(self) -> Dict[str, Any]:
        return {"plugin_id": self.plugin_id, "verdict": self.verdict, "data_tested": self.data_tested, "counts": self.counts(),
                "fixture_files": self.fixture_files, "untested_candidates": self.untested_candidates,
                "notes": list(self.notes),
                "checks": [{"name": c.name, "status": c.status, "reason": c.reason} for c in self.checks]}

    def format(self, width: int = 48) -> str:
        """Dạng khối cho màn hình iSH hẹp: mỗi mục 1-2 dòng, lý do thụt vào."""
        out = ["conformance: %s" % self.plugin_id]
        _role = {"PASS": "pass", "FAIL": "fail", "SKIP": "skip"}
        for c in self.checks:
            out.append(paint("[%s] %s" % (c.status, c.name), _role.get(c.status, "info")))
            if c.reason:
                limit = width + 60
                shown = c.reason if display_width(c.reason) <= limit else truncate_display(c.reason, limit)
                out.append("    " + shown)
        n = self.counts()
        _verdict_role = "fail" if n[FAIL] else ("pass" if n[PASS] else "skip")
        out.append(paint("KẾT QUẢ: %s (%d PASS, %d FAIL, %d SKIP; fixture: %d file)"
                         % (self.verdict_label, n[PASS], n[FAIL], n[SKIP], self.fixture_files),
                         _verdict_role))
        for s in self.notes:
            out.append("ghi chú: " + s)
        return "\n".join(out)


# ---------------------------------------------------------------------------- fixture
def load_fixture(directory: str, limits: Optional[Limits] = None,
                   notes: Optional[List[str]] = None) -> Dict[str, bytes]:
    """Đọc thư mục fixture thành {đường_dẫn_ảo: bytes}. Bỏ file ẩn (tên bắt đầu '.'), symlink, tên không an toàn;
    quá giới hạn kích thước/số file -> E_TOO_LARGE (không cắt cụt im lặng). Thư mục không tồn tại -> {}.
    Tên bị bỏ qua được ghi vào `notes` (nếu có) để báo cáo không im lặng."""
    files: Dict[str, bytes] = {}
    if not directory or not os.path.isdir(directory):
        return files
    total = 0
    for base, dirs, names in os.walk(directory, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and not os.path.islink(os.path.join(base, d)))
        for n in sorted(names):
            full = os.path.join(base, n)
            if n.startswith(".") or os.path.islink(full) or not os.path.isfile(full):
                continue
            rel = os.path.relpath(full, directory).replace(os.sep, "/")
            try:
                check_vpath(rel)
            except PB3Error:
                if notes is not None:
                    notes.append("bỏ qua fixture %s (tên không an toàn)" % rel)
                continue
            size = os.path.getsize(full)
            total += size
            if (size > MAX_FIXTURE_FILE_BYTES or total > MAX_FIXTURE_TOTAL_BYTES
                    or len(files) >= MAX_FIXTURE_FILES):
                raise PB3Error("E_TOO_LARGE", "fixture quá lớn (tối đa %d file, %d MiB/file)"
                               % (MAX_FIXTURE_FILES, MAX_FIXTURE_FILE_BYTES // (1024 * 1024)), where=rel)
            with open(full, "rb") as fh:
                files[rel] = fh.read()
    return files


# ---------------------------------------------------------------------------- tiện ích
def _why(res: Any) -> str:
    d = res.diagnostic
    return ("%s: %s" % (d.code, d.detail)) if d is not None else "lỗi không rõ"


class _FailedCall:
    """Kết quả giả khi chính runner ném ngoại lệ: r.ok False để mục kiểm thành FAIL."""

    def __init__(self, hook: str, detail: str):
        self.hook = hook
        self.ok = False
        self.value = None
        self.code = "E_INTERNAL"
        self.diagnostic = Diagnostic("E_INTERNAL", "error", "", detail, "")


class _Runner:
    def __init__(self, plugin: Plugin, ctx: PluginContext, tempdir: str, timeout: Optional[float], runner: Any):
        self.plugin, self.ctx, self.tempdir, self.timeout, self._runner = plugin, ctx, tempdir, timeout, runner
        self.results: List[Any] = []

    def call(self, name: str, *args: Any) -> Any:
        try:
            res = self._runner(self.plugin, name, self.ctx, *args, timeout=self.timeout, write_roots=[self.tempdir])
        except Exception as exc:   # noqa: BLE001 - runner tự ném: thành FAIL, không crash bộ kiểm
            res = _FailedCall(name, "runner ném %s" % type(exc).__name__)
        res.hook = name
        self.results.append(res)
        return res


def unloadable_report(plugin_id: str, reason: str) -> ConformanceReport:
    """Plugin không nạp được: manifest FAIL kèm lý do, các mục còn lại SKIP."""
    rep = ConformanceReport(plugin_id)
    rep.add("manifest", FAIL, reason)
    for n in CHECK_NAMES[1:]:
        rep.add(n, SKIP, "plugin không nạp được")
    return rep


def run_checks(plugin: Plugin, runner: Any, fixture: Optional[str] = None, files: Optional[Dict[str, bytes]] = None,
               limits: Optional[Limits] = None, hook_timeout: Optional[float] = None, max_candidates: int = 3,
               notes: Optional[Any] = None) -> ConformanceReport:
    """Chạy conformance cho một Plugin đã khởi tạo. Không bao giờ raise vì lỗi của plugin.

    runner: `(plugin, tên_hook, ctx, *args, timeout=, write_roots=) -> kết quả có .ok .value .diagnostic .code`
            (thực tế registry.isolation.run_hook). notes: hàm không đối số trả list ghi chú thêm (trạng thái guard).
    fixture: thư mục file mẫu (tests/fixtures/<id>/); files: hoặc dict {vpath: bytes} dựng sẵn.
    hook_timeout: ghi đè timeout từng hook (giây); mặc định theo manifest (limits.hook_timeout_s).
    """
    if not callable(runner):
        raise PB3Error("E_PLUGIN_INVALID", "run_checks cần runner (registry.isolation.run_hook); không chạy hook trần")
    manifest = plugin.manifest
    pid = manifest.id
    skipped_notes: List[str] = []
    try:
        # KHÔNG copy dict fixture (MemoryVFS tự copy tham chiếu vào dict riêng);
        # chỉ đọc thư mục khi không có files dựng sẵn.
        data = files if files is not None else load_fixture(fixture or "", limits, notes=skipped_notes)
    except PB3Error as e:
        rep = unloadable_report(pid, "fixture không đọc được: [%s] %s" % (e.code, e.detail))
        for c in rep.checks[1:]:
            c.reason = "fixture không đọc được"
        return rep
    rep = ConformanceReport(pid)
    rep.fixture_files = len(data)
    rep.notes.extend(skipped_notes)
    if not data:
        rep.notes.append("fixture rỗng: probe/parse/write CHƯA được kiểm bằng dữ liệu; chỉ có selftest của plugin (nếu có)")
    vfs: VFS = MemoryVFS(data, limits)
    tempdir = str(safe_io.make_tempdir(tempfile.gettempdir(), prefix="pb3conf-"))
    try:
        ctx = PluginContext(vfs, limits, None, None, tempdir, True, None, None, pid).for_plugin(manifest)
        _run_checks(rep, plugin, ctx, vfs, tempdir, hook_timeout, max_candidates, runner)
    finally:
        shutil.rmtree(tempdir, ignore_errors=True)
    if notes is not None:
        rep.notes.extend(str(x) for x in notes())
    return rep


# ---------------------------------------------------------------------------- các mục kiểm
def _run_checks(rep: ConformanceReport, plugin: Plugin, ctx: PluginContext, vfs: VFS, tempdir: str,
                timeout: Optional[float], max_cand: int, runner: Any) -> None:
    m = plugin.manifest
    run = _Runner(plugin, ctx, tempdir, timeout, runner)
    caps = set(m.capabilities)
    reads = "read" in caps
    cands: List[Candidate] = []
    tables: List[Any] = []                       # [(Candidate, LocTable)]

    # 1. manifest
    try:
        from sdk.manifest import parse_manifest
        again = parse_manifest(type(plugin).MANIFEST)
        if again.to_dict() != m.to_dict():
            raise PB3Error("E_PLUGIN_INVALID", "manifest của instance khác MANIFEST của lớp")
        rep.add("manifest", PASS, "id=%s kind=%s maturity=%s caps=%s" % (m.id, m.kind, m.maturity, ",".join(m.capabilities)))
    except PB3Error as e:
        rep.add("manifest", FAIL, "[%s] %s" % (e.code, e.detail or e.message_vi))

    # 2. readonly
    if m.can_write:
        rep.add("readonly", SKIP, "plugin khai 'write'")
    else:
        bad = []
        for hook, args in (("build", (LocTable([], None, m.id), b"")), ("verify", (b"", b""))):
            r = run.call(hook, *args)
            if r.ok or r.code != "E_WRITE_DISABLED":
                bad.append("%s %s" % (hook, "chạy được" if r.ok else "trả " + r.code))
        rep.add("readonly", FAIL if bad else PASS,
                ("plugin chỉ đọc nhưng " + "; ".join(bad) + " (phải E_WRITE_DISABLED)") if bad
                else "build/verify bị từ chối E_WRITE_DISABLED")

    # 3. detect
    r = run.call("detect")
    if not r.ok:
        rep.add("detect", FAIL, _why(r))
    elif not isinstance(r.value, list) or any(not isinstance(x, Evidence) for x in r.value):
        rep.add("detect", FAIL, "phải trả list[Evidence]")
    else:
        rep.add("detect", PASS, "%d bằng chứng" % len(r.value))

    # 4. candidates
    if caps <= {"hint"}:
        rep.add("candidates", SKIP, "kind=%s không có bảng ngôn ngữ" % m.kind)
    else:
        _check_candidates(rep, run.call("list_candidates"), vfs, m.id, cands)

    # 5. probe
    if caps <= {"hint"}:
        rep.add("probe", SKIP, "kind=%s không probe" % m.kind)
    elif not cands:
        rep.add("probe", SKIP, "không có ứng viên (fixture rỗng hoặc không khớp)")
    else:
        bad = []
        for c in cands[:max_cand]:
            r = run.call("probe", c)
            if not r.ok:
                bad.append("%s: %s" % (c.path, _why(r)))
            elif not isinstance(r.value, dict):
                bad.append("%s: probe phải trả dict" % c.path)
        rep.add("probe", FAIL if bad else PASS, "; ".join(bad) if bad else "%d ứng viên" % min(len(cands), max_cand))

    # 6. parse
    if not reads:
        rep.add("parse", SKIP, "plugin không khai 'read'")
    elif not cands:
        rep.add("parse", SKIP, "không có ứng viên (fixture rỗng hoặc không khớp)")
    else:
        bad, n_entries = [], 0
        for c in cands[:max_cand]:
            r = run.call("parse", c)
            if not r.ok:
                bad.append("%s: %s" % (c.path, _why(r)))
                continue
            problem = _check_table(r.value, m.id)
            if problem:
                bad.append("%s: %s" % (c.path, problem))
            else:
                n_entries += len(r.value.entries)
                tables.append((c, r.value))
        rep.add("parse", FAIL if bad else PASS, "; ".join(bad) if bad else "%d bảng, %d entry" % (len(tables), n_entries))

    # 7. write
    if not m.can_write:
        rep.add("write", SKIP, "plugin chỉ đọc")
    elif not tables:
        rep.add("write", SKIP, "không có bảng đã parse để build/verify (fixture rỗng?)")
    else:
        _check_write(rep, run, vfs, tables[0])

    # 8. container
    if "container" not in caps:
        rep.add("container", SKIP, "plugin không khai 'container'")
    elif not cands:
        rep.add("container", SKIP, "không có ứng viên container trong fixture")
    else:
        r = run.call("open_container", cands[0].path)
        if not r.ok:
            rep.add("container", FAIL, _why(r))
        elif not isinstance(r.value, VFS):
            rep.add("container", FAIL, "open_container phải trả VFS")
        else:
            try:
                names = list(r.value.iter_entries())
            except PB3Error as e:
                rep.add("container", FAIL, "iter_entries lỗi: [%s] %s" % (e.code, e.detail or e.message_vi))
                names = None
            if names is not None:
                if not names:
                    rep.add("container", FAIL, "VFS trả về rỗng (không liệt kê được entry nào)")
                else:
                    try:
                        data = r.value.read(names[0], None)
                        rep.add("container", PASS, "mở %s: %d entry, đọc được %s (%d byte)"
                                % (cands[0].path, len(names), names[0], len(data)))
                    except PB3Error as e:
                        rep.add("container", FAIL, "đọc %s lỗi: [%s] %s"
                                % (names[0], e.code, e.detail or e.message_vi))

    # 9. hints
    if "hint" not in caps:
        rep.add("hints", SKIP, "plugin không khai 'hint'")
    else:
        r = run.call("hints")
        rep.add("hints", PASS if r.ok and isinstance(r.value, dict) else FAIL,
                "dict %d khóa" % len(r.value) if r.ok and isinstance(r.value, dict)
                else (_why(r) if not r.ok else "hints phải trả dict"))

    # 10. selftest
    if not m.selftest:
        rep.add("selftest", SKIP, "manifest khai selftest=false")
    else:
        r = run.call("selftest")
        if not r.ok:
            rep.add("selftest", FAIL, _why(r))
        elif not isinstance(r.value, dict):
            rep.add("selftest", FAIL, "selftest phải trả dict")
        elif r.value.get("ok") is True:
            rep.add("selftest", PASS, "selftest ok")
        else:
            rep.add("selftest", FAIL, "manifest khai selftest=true nhưng kết quả ok=%r (%s)"
                    % (r.value.get("ok"), sanitize_detail(r.value.get("reason", ""), 120)))

    # 10b. ứng viên chưa kiểm (vượt max_candidates): báo rõ, không im lặng
    if len(cands) > max_cand:
        rep.untested_candidates = len(cands) - max_cand
        rep.notes.append("ứng viên chưa kiểm: %d (chỉ kiểm tối đa %d ứng viên đầu)"
                         % (rep.untested_candidates, max_cand))

    # 11. guard (gộp mọi hook đã chạy)
    bad = []
    for r in run.results:
        if r.code in ("E_PLUGIN_FORBIDDEN", "E_PLUGIN_TIMEOUT"):
            bad.append("%s: %s" % (r.hook, _why(r)))
    rep.add("guard", FAIL if bad else PASS,
            "; ".join(bad) if bad else "%d hook chạy dưới guard, 0 vi phạm" % len(run.results))


def _check_candidates(rep: ConformanceReport, r: Any, vfs: VFS, pid: str, out: List[Candidate]) -> None:
    if not r.ok:
        rep.add("candidates", FAIL, _why(r))
        return
    if not isinstance(r.value, list) or any(not isinstance(c, Candidate) for c in r.value):
        rep.add("candidates", FAIL, "phải trả list[Candidate]")
        return
    bad = []
    for c in r.value:
        if c.adapter_id != pid:
            bad.append("%s: adapter_id=%r khác id plugin" % (c.path, c.adapter_id))
            continue
        try:
            vfs.stat(c.path)
        except PB3Error as e:
            bad.append("%s: không có trong VFS (%s)" % (c.path, e.code))
            continue
        out.append(c)
    rep.add("candidates", FAIL if bad else PASS, "; ".join(bad[:3]) if bad else "%d ứng viên" % len(r.value))


def _check_table(table: Any, pid: str) -> str:
    """Chuỗi mô tả vấn đề, rỗng nếu đạt."""
    if not isinstance(table, LocTable):
        return "parse phải trả LocTable, nhận %s" % type(table).__name__
    try:
        back = LocTable.from_dict(table.to_dict())
    except PB3Error as e:
        return "LocTable không qua to_dict/from_dict: [%s] %s" % (e.code, e.detail)
    if back.to_dict() != table.to_dict():
        return "LocTable không bền qua to_dict/from_dict"
    if table.adapter_id != pid:
        return "LocTable.adapter_id=%r khác id plugin" % table.adapter_id
    problems = table.validate()
    if problems:
        return "; ".join(problems)
    for e in table.entries:
        if not isinstance(e.source, SourceRef):
            return "entry %r thiếu source (mỗi bản ghi phải biết nguồn gốc)" % e.key
    try:
        table.to_key_values()
    except Exception as e:                       # noqa: BLE001 - kiểu dữ liệu sai bất kỳ
        return "to_key_values lỗi: %s" % type(e).__name__
    return ""


def _check_write(rep: ConformanceReport, run: _Runner, vfs: VFS, item: Any) -> None:
    cand, table = item
    try:
        base = vfs.read(cand.path, None)
    except PB3Error as e:
        rep.add("write", FAIL, "không đọc được %s: %s" % (cand.path, e.code))
        return
    b = run.call("build", table, base)
    if not b.ok:
        rep.add("write", FAIL, "build: " + _why(b))
        return
    v = run.call("verify", base, b.value)
    if not v.ok:
        rep.add("write", FAIL, "verify: " + _why(v))
    elif not isinstance(v.value, dict):
        rep.add("write", FAIL, "verify phải trả dict")
    elif v.value.get("ok") is True:
        # kiểm âm: lật 1 byte trong bản build rồi verify PHẢI thất bại
        # (verify chấp nhận dữ liệu đã sửa = không xác minh được gì).
        if len(b.value) > 0:
            tampered = bytearray(b.value)
            tampered[len(tampered) // 2] ^= 0xFF
            neg = run.call("verify", base, bytes(tampered))
            if neg.ok and isinstance(neg.value, dict) and neg.value.get("ok") is True:
                rep.add("write", FAIL, "verify chấp nhận dữ liệu đã lật 1 byte (kiểm âm thất bại)")
                return
        rep.add("write", PASS, "build %d byte, verify ok + kiểm âm (lật 1 byte bị từ chối)"
                % len(b.value))
    else:
        rep.add("write", FAIL, "verify ok=%r: plugin khai 'write' mà chưa xác minh được (%s)"
                % (v.value.get("ok"), sanitize_detail(v.value.get("reason", ""), 120)))
