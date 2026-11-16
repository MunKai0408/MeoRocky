"""Kiểu dữ liệu IR của Plugin SDK (đặc tả mục 3): SourceRef, LocEntry, LocTable, Candidate, Diagnostic.

Evidence/confidence/SupportLevel định nghĩa ở core/model.py (Project cũng dùng) và được xuất lại ở đây để
plugin chỉ cần `from sdk.types import ...` như Phụ lục B. Mọi lớp có to_dict/from_dict; dữ liệu sai -> E_CORRUPT.
Chỉ thư viện chuẩn, Python >= 3.8.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sdk.vfs import check_vpath, vpath_split

from core.errors import ERROR_SPECS, PB3Error
from core.model import Evidence, Locale, SupportLevel, confidence  # noqa: F401  (xuất lại)
from core.model import _Base as ModelBase
from core.model import _corrupt

KEY_ORIGINS = ("native", "derived", "generated", "rowindex")          # 3.2
ENTRY_STATUSES = ("ok", "empty", "conflict", "truncated", "unparsed")  # 3.2
SEVERITIES = ("info", "warning", "error")


@dataclass
class SourceRef(ModelBase):
    """3.1. container_stack đi từ ngoài vào trong; mỗi phần tử là đường dẫn ảo (có thể chứa '::')."""
    container_stack: List[str] = field(default_factory=list)
    rel_path: str = ""
    entry_offset: Optional[int] = None
    entry_size: Optional[int] = None
    encoding: str = "utf-8"
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_vpath(cls, vpath: str, *, encoding: str = "utf-8") -> "SourceRef":
        """'a.ipa::b/c.unity3d::resources.assets' -> container_stack=[a.ipa,
        b/c.unity3d], rel_path=resources.assets."""
        parts = vpath_split(vpath)
        return cls(container_stack=parts[:-1], rel_path=parts[-1], encoding=encoding)

    def __post_init__(self):
        if not isinstance(self.container_stack, list) or any(
                not isinstance(x, str) or not x for x in self.container_stack):
            raise _corrupt("SourceRef.container_stack phải là list chuỗi không rỗng")
        if not isinstance(self.rel_path, str):
            raise _corrupt("SourceRef.rel_path phải là chuỗi")
        for n in ("entry_offset", "entry_size"):
            v = getattr(self, n)
            if v is not None and (isinstance(v, bool) or not isinstance(v, int) or v < 0):
                raise _corrupt("SourceRef.%s phải là int >= 0 hoặc None" % n)
        if not isinstance(self.encoding, str) or not self.encoding:
            raise _corrupt("SourceRef.encoding rỗng")
        if not isinstance(self.extra, dict):
            raise _corrupt("SourceRef.extra phải là dict")


@dataclass
class LocEntry(ModelBase):
    """3.2. `notes` chỉ dùng cho báo cáo, không đi vào JSON xuất."""
    _NESTED = {"source": (SourceRef, "one")}
    key: str
    value: str
    locale: Optional[str] = None
    key_origin: str = "native"
    status: str = "ok"
    source: Optional[SourceRef] = None
    notes: List[str] = field(default_factory=list)

    def __post_init__(self):
        if not isinstance(self.key, str) or not isinstance(self.value, str):
            raise _corrupt("LocEntry.key/value phải là chuỗi")
        if self.locale is not None and not isinstance(self.locale, str):
            raise _corrupt("LocEntry.locale phải là chuỗi hoặc None")
        if self.key_origin not in KEY_ORIGINS:
            raise _corrupt("LocEntry.key_origin không hợp lệ: %r" % (self.key_origin,))
        if self.status not in ENTRY_STATUSES:
            raise _corrupt("LocEntry.status không hợp lệ: %r" % (self.status,))
        if not isinstance(self.source, SourceRef):
            raise _corrupt("LocEntry.source bắt buộc là SourceRef (nguồn gốc của bản ghi)")
        if not isinstance(self.notes, list) or any(not isinstance(n, str) for n in self.notes):
            raise _corrupt("LocEntry.notes phải là list chuỗi")


@dataclass
class LocTable(ModelBase):
    """3.3. `entries` giữ nguyên thứ tự nguồn; key trùng được báo trong `conflicts`, không tự gộp."""
    _NESTED = {"entries": (LocEntry, "list")}
    entries: List[LocEntry] = field(default_factory=list)
    locale: Optional[str] = None
    adapter_id: str = ""
    schema_hint: Dict[str, Any] = field(default_factory=dict)
    stats: Dict[str, Any] = field(default_factory=dict)
    conflicts: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self):
        if self.locale is not None and not isinstance(self.locale, str):
            raise _corrupt("LocTable.locale phải là chuỗi hoặc None")
        if not isinstance(self.adapter_id, str):
            raise _corrupt("LocTable.adapter_id phải là chuỗi")
        for n in ("conflicts", "warnings"):
            v = getattr(self, n)
            if not isinstance(v, list) or any(not isinstance(x, dict) for x in v):
                raise _corrupt("LocTable.%s phải là list dict" % n)
        for n in ("schema_hint", "stats"):
            if not isinstance(getattr(self, n), dict):
                raise _corrupt("LocTable.%s phải là dict" % n)

    def count_by_status(self) -> Dict[str, int]:
        out = {s: 0 for s in ENTRY_STATUSES}
        for e in self.entries:
            out[e.status] += 1
        return out

    def duplicate_keys(self) -> List[str]:
        """Các key xuất hiện > 1 lần (cùng locale). Chỉ báo cáo, không gộp."""
        seen, dup = set(), []
        for e in self.entries:
            k = (e.locale, e.key)
            if k in seen and e.key not in dup:
                dup.append(e.key)
            seen.add(k)
        return dup

    def validate(self) -> List[str]:
        """Kiểm tra bảng: mỗi entry phải có source, key/value là chuỗi,
        adapter_id không rỗng. Trả danh sách vấn đề (rỗng = hợp lệ)."""
        probs: List[str] = []
        if not self.adapter_id:
            probs.append("LocTable.adapter_id rỗng")
        for e in self.entries:
            if not isinstance(e.source, SourceRef):
                probs.append("entry %r thiếu source" % e.key)
            if not isinstance(e.key, str) or not isinstance(e.value, str):
                probs.append("entry %r: key/value phải là chuỗi" % (e.key,))
        return probs

    def to_key_values(self, on_conflict: str = "last") -> Dict[str, str]:
        """JSON key-value đầu ra (đặc tả mục 0: chỉ cặp key-value, không metadata).
        on_conflict: 'last' (mặc định, giữ giá trị sau — hành vi cũ), 'first'
        (giữ giá trị đầu), 'error' (raise E_CORRUPT khi key trùng)."""
        if on_conflict not in ("last", "first", "error"):
            raise PB3Error("E_PLUGIN_INVALID", "on_conflict phải là last|first|error")
        out: Dict[str, str] = {}
        for e in self.entries:
            if e.key in out:
                if on_conflict == "error":
                    raise PB3Error("E_CORRUPT", "key trùng: %r" % e.key)
                if on_conflict == "first":
                    continue
            out[e.key] = e.value
        return out

    def to_key_values_strict(self) -> Dict[str, str]:
        """Như to_key_values(on_conflict='error'): key trùng -> E_CORRUPT."""
        return self.to_key_values(on_conflict="error")

    @classmethod
    def from_key_values(cls, data: Dict[str, str], adapter_id: str = "", locale: Optional[str] = None,
                        source: Optional[SourceRef] = None) -> "LocTable":
        """Dựng bảng từ dict key-value (ngược với to_key_values). Mỗi entry
        dùng chung `source` (mặc định SourceRef rỗng '<key-values>')."""
        src = source if source is not None else SourceRef([], "<key-values>")
        entries = [LocEntry(str(k), str(v), locale, "generated", "ok", src)
                   for k, v in data.items()]
        return cls(entries, locale, adapter_id)


@dataclass
class Candidate(ModelBase):
    """Ứng viên file/bảng cho pipeline ngôn ngữ. `score` thuộc [0,1] (Phụ lục C)."""
    _NESTED = {"evidence": (Evidence, "list")}
    path: str
    adapter_id: str
    score: float = 0.0
    reasons: List[str] = field(default_factory=list)
    evidence: List[Evidence] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def for_path(cls, path: str, adapter_id: str, score: float = 0.0,
                 reasons: Optional[List[str]] = None,
                 evidence: Optional[List[Evidence]] = None) -> "Candidate":
        """Helper dựng Candidate (kiểm vpath + score qua __post_init__)."""
        check_vpath(path)
        return cls(path, adapter_id, score, list(reasons or []),
                   list(evidence or []))

    def __post_init__(self):
        if not isinstance(self.path, str) or not self.path:
            raise _corrupt("Candidate.path rỗng")
        if not isinstance(self.adapter_id, str) or not self.adapter_id:
            raise _corrupt("Candidate.adapter_id rỗng")
        if isinstance(self.score, bool) or not isinstance(self.score, (int, float)) \
                or not (0.0 <= self.score <= 1.0):
            raise _corrupt("Candidate.score phải thuộc [0,1]: %r" % (self.score,))
        self.score = float(self.score)
        if not isinstance(self.reasons, list) or any(not isinstance(r, str) for r in self.reasons):
            raise _corrupt("Candidate.reasons phải là list chuỗi")
        if not isinstance(self.extra, dict):
            raise _corrupt("Candidate.extra phải là dict")


@dataclass
class Diagnostic(ModelBase):
    """3.6. `code` thuộc Phụ lục D (ERROR_SPECS). message_vi để trống -> lấy câu chuẩn của mã."""
    _NESTED = {"where": (SourceRef, "one")}
    code: str
    severity: str = "error"
    message_vi: str = ""
    detail: str = ""
    plugin_id: str = ""
    where: Optional[SourceRef] = None

    def __post_init__(self):
        if self.code not in ERROR_SPECS:
            raise PB3Error("E_PLUGIN_INVALID", "Diagnostic.code ngoài Phụ lục D: %r" % (self.code,))
        if self.severity not in SEVERITIES:
            raise PB3Error("E_PLUGIN_INVALID", "Diagnostic.severity sai: %r" % (self.severity,))
        if not self.message_vi:
            self.message_vi = ERROR_SPECS[self.code].message_vi
        if self.where is not None and not isinstance(self.where, SourceRef):
            raise PB3Error("E_PLUGIN_INVALID", "Diagnostic.where phải là SourceRef")

    @classmethod
    def from_error(cls, e: PB3Error, plugin_id: str = "", where: Optional[SourceRef] = None,
                   severity: str = "error") -> "Diagnostic":
        w = where
        if w is None and e.where:
            w = SourceRef(rel_path=e.where)
        return cls(e.code, severity, e.message_vi, e.detail, plugin_id or e.plugin_id or "", w)
