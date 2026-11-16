"""Mô hình dữ liệu trung tâm PB3 (Claude 02, sửa bởi 02b theo đặc tả 3.4-3.6, 10.1). Chỉ thư viện chuẩn, Python >= 3.8.

Phân biệt 3 loại đường dẫn: SourcePath (nguồn, tuyệt đối được), InnerPath (bên trong container,
tương đối, đầu '/'), OutputPath (tương đối trong thư mục output). Mã lỗi dùng ERROR_SPECS dùng chung.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, fields, MISSING
from typing import Any, Dict, List, Optional

from core.errors import PB3Error

MODEL_VERSION = 2


class SupportLevel:
    """Mức trưởng thành plugin/định dạng (đặc tả 10.1): M0 PLANNED .. M4 VERIFIED. Có thứ tự."""
    M0, M1, M2, M3, M4 = "M0", "M1", "M2", "M3", "M4"
    ALL = ("M0", "M1", "M2", "M3", "M4")
    NAMES = {"M0": "PLANNED", "M1": "DETECT", "M2": "EXTRACT", "M3": "WRITE", "M4": "VERIFIED"}

    @classmethod
    def valid(cls, v: Any) -> bool:
        return isinstance(v, str) and v in cls.ALL

    @classmethod
    def rank(cls, v: str) -> int:
        if not cls.valid(v):
            raise PB3Error("E_PLUGIN_INVALID", "SupportLevel không hợp lệ: %r" % (v,))
        return cls.ALL.index(v)

    @classmethod
    def label(cls, v: str) -> str:
        return "%s %s" % (v, cls.NAMES[v]) if cls.valid(v) else "?"


SUPPORT = SupportLevel.ALL


def _corrupt(detail, where=None):
    return PB3Error("E_CORRUPT", detail, where=where)


def _rel_ok(p: str, what: str) -> str:
    if not isinstance(p, str) or not p:
        raise PB3Error("E_UNSAFE_PATH", what + ": rỗng hoặc không phải chuỗi")
    if "\x00" in p or "\\" in p or p.startswith("/") or (len(p) > 1 and p[1] == ":"):
        raise PB3Error("E_UNSAFE_PATH", what + ": tuyệt đối, đầu '\\\\' hoặc ký tự lạ", where=p)
    for seg in p.split("/"):
        if seg in ("", ".", ".."):
            raise PB3Error("E_UNSAFE_PATH", what + ": đoạn rỗng/'.'/'..'", where=p)
    return p


def _enc(v: Any) -> Any:
    if hasattr(v, "to_dict"):
        return v.to_dict()
    if isinstance(v, (list, tuple)):
        return [_enc(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _enc(x) for k, x in v.items()}
    return v


_SCALAR = {"str": str, "int": int, "bool": bool, "float": (int, float)}


@dataclass
class _Base:
    _NESTED = {}            # tên trường -> (lớp, "one" | "list")

    def to_dict(self) -> Dict[str, Any]:
        return {f.name: _enc(getattr(self, f.name)) for f in fields(self)}

    @classmethod
    def from_dict(cls, d: Any):
        name = cls.__name__
        if not isinstance(d, dict):
            raise _corrupt(name + ": cần object/dict")
        fs = {f.name: f for f in fields(cls)}
        extra = sorted(set(d) - set(fs))
        if extra:
            raise _corrupt(name + ": trường lạ " + ",".join(extra))
        kw = {}
        for n, f in fs.items():
            if n not in d:
                if f.default is MISSING and f.default_factory is MISSING:    # type: ignore
                    raise _corrupt(name + ": thiếu trường " + n)
                continue
            v = d[n]
            if n in cls._NESTED:
                sub, mode = cls._NESTED[n]
                if mode == "list":
                    if not isinstance(v, list):
                        raise _corrupt(name + "." + n + ": cần list")
                    v = [sub.from_dict(x) for x in v]
                elif v is not None:
                    v = sub.from_dict(v)
            else:
                t = f.type.replace("Optional[", "").rstrip("]")
                if t in _SCALAR and v is not None:
                    ok = isinstance(v, _SCALAR[t]) and not (t != "bool" and isinstance(v, bool))
                    if not ok:
                        raise _corrupt(name + "." + n + ": sai kiểu, cần " + t)
                if t.startswith(("Dict", "dict")) and not isinstance(v, dict):
                    raise _corrupt(name + "." + n + ": cần dict")
                if t.startswith(("List", "list")) and not isinstance(v, list):
                    raise _corrupt(name + "." + n + ": cần list")
            kw[n] = v
        return cls(**kw)


@dataclass
class SourcePath(_Base):
    path: str
    def __post_init__(self):
        if not isinstance(self.path, str) or not self.path or "\x00" in self.path:
            raise PB3Error("E_UNSAFE_PATH", "SourcePath rỗng hoặc có NUL")


@dataclass
class InnerPath(_Base):
    container: str
    path: str
    def __post_init__(self):
        _rel_ok(self.path, "InnerPath")
        if not self.container:
            raise _corrupt("InnerPath: thiếu container")
        _rel_ok(self.container, "InnerPath.container")   # container cũng không được traversal


@dataclass
class OutputPath(_Base):
    path: str
    def __post_init__(self):
        _rel_ok(self.path, "OutputPath")


@dataclass
class Evidence(_Base):
    """Bằng chứng có trọng số (đặc tả 3.5): ("magic", "UnityFS", 1.00, "byte 0..8 = UnityFS")."""
    kind: str
    value: str = ""
    weight: float = 0.0
    reason: str = ""

    def __post_init__(self):
        if not isinstance(self.kind, str) or not self.kind:
            raise _corrupt("Evidence.kind rỗng/không phải chuỗi")
        if isinstance(self.weight, bool) or not isinstance(self.weight, (int, float)) \
                or not (0.0 <= self.weight <= 1.0):
            raise _corrupt("Evidence.weight phải là số trong [0,1]: %r" % (self.weight,))
        self.weight = float(self.weight)


def confidence(evidence: Any) -> float:
    """Confidence = tổng trọng số, chặn trần 1.0 (đặc tả 3.5)."""
    return min(1.0, sum(e.weight for e in evidence))


@dataclass
class Provenance(_Base):
    _NESTED = {"source": (SourcePath, "one"), "inner": (InnerPath, "one")}
    plugin_id: str = ""
    plugin_version: str = ""
    method: str = ""
    source: Optional[SourcePath] = None
    inner: Optional[InnerPath] = None
    note: str = ""


@dataclass
class _Node(_Base):
    """Phần chung: id, trạng thái hỗ trợ, bằng chứng, metadata."""
    id: str = ""
    support: str = "M0"
    evidence: List[Evidence] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    def _chk(self):
        if not self.id:
            raise _corrupt(type(self).__name__ + ": thiếu id")
        if not SupportLevel.valid(self.support):
            raise _corrupt(type(self).__name__ + ".support không hợp lệ: %r" % (self.support,))


@dataclass
class Locale(_Base):
    """Locale (đặc tả 3.4). Thêm locale mới = thêm entry config/locales.toml, không sửa code."""
    code: str
    aliases: List[str] = field(default_factory=list)
    script: str = ""
    display: str = ""
    detect_regex: str = ""
    numeric_id: Optional[int] = None

    def __post_init__(self):
        if not isinstance(self.code, str) or not self.code:
            raise _corrupt("Locale: thiếu code")
        if self.numeric_id is not None and (isinstance(self.numeric_id, bool)
                                            or not isinstance(self.numeric_id, int)):
            raise _corrupt("Locale.numeric_id phải là int")
        # Chuẩn hóa alias: strip + chữ thường + bỏ trùng (BCP 47 không phân biệt hoa/thường).
        if not isinstance(self.aliases, list) or any(not isinstance(a, str) for a in self.aliases):
            raise _corrupt("Locale.aliases phải là list chuỗi")
        seen, norm = set(), []
        for a in self.aliases:
            b = a.strip().lower()
            if b and b not in seen:
                seen.add(b)
                norm.append(b)
        self.aliases = norm

    def compile(self):
        """Biên dịch detect_regex (None nếu không khai); regex lỗi -> E_CORRUPT."""
        if not self.detect_regex:
            return None
        try:
            return re.compile(self.detect_regex)
        except re.error as exc:
            raise _corrupt("Locale %s: detect_regex lỗi: %s" % (self.code, str(exc)[:80]))

    def validate(self) -> List[str]:
        """Trả danh sách vấn đề (rỗng = hợp lệ): code/alias/script/display/detect_regex."""
        probs: List[str] = []
        if not isinstance(self.code, str) or not self.code:
            probs.append("Locale: thiếu code")
        if any(a != a.strip().lower() or not a for a in self.aliases):
            probs.append("Locale %s: alias chưa chuẩn hóa" % (self.code,))
        if self.numeric_id is not None and (isinstance(self.numeric_id, bool)
                                            or not isinstance(self.numeric_id, int)):
            probs.append("Locale %s: numeric_id phải là int" % (self.code,))
        if self.detect_regex:
            try:
                re.compile(self.detect_regex)
            except re.error as exc:
                probs.append("Locale %s: detect_regex lỗi: %s" % (self.code, str(exc)[:60]))
        return probs


@dataclass
class FileRef(_Node):
    _NESTED = {"source": (SourcePath, "one"), "evidence": (Evidence, "list")}
    source: Optional[SourcePath] = None
    size: int = 0
    sha256: str = ""
    kind: str = ""
    def __post_init__(self):
        self._chk()
        if self.source is None:
            raise _corrupt("FileRef: thiếu source")
        if isinstance(self.size, bool) or not isinstance(self.size, int) or self.size < 0:
            raise _corrupt("FileRef.size phải là int >= 0")
        if self.sha256 and (len(self.sha256) != 64
                            or not re.match(r"^[0-9a-f]{64}$", self.sha256)):
            raise _corrupt("FileRef.sha256 phải là hex thường 64 ký tự (hoặc rỗng)")


@dataclass
class Container(_Node):
    _NESTED = {"evidence": (Evidence, "list")}
    file_id: str = ""
    kind: str = ""
    parent_id: Optional[str] = None
    def __post_init__(self):
        self._chk()


@dataclass
class Asset(_Node):
    _NESTED = {"inner": (InnerPath, "one"), "evidence": (Evidence, "list")}
    container_id: str = ""
    inner: Optional[InnerPath] = None
    kind: str = ""
    size: int = 0
    def __post_init__(self):
        self._chk()
        if self.inner is None:
            raise _corrupt("Asset: thiếu inner")


@dataclass
class Resource(_Node):
    _NESTED = {"evidence": (Evidence, "list")}
    asset_id: str = ""
    kind: str = ""
    name: str = ""
    locale: Optional[str] = None
    def __post_init__(self):
        self._chk()


@dataclass
class Project(_Base):
    _NESTED = {"files": (FileRef, "list"), "containers": (Container, "list"),
               "assets": (Asset, "list"), "resources": (Resource, "list"),
               "locales": (Locale, "list"), "output": (OutputPath, "one")}
    name: str
    model_version: int = MODEL_VERSION
    files: List[FileRef] = field(default_factory=list)
    containers: List[Container] = field(default_factory=list)
    assets: List[Asset] = field(default_factory=list)
    resources: List[Resource] = field(default_factory=list)
    locales: List[Locale] = field(default_factory=list)
    output: Optional[OutputPath] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d):
        if isinstance(d, dict) and d.get("model_version", MODEL_VERSION) != MODEL_VERSION:
            raise _corrupt("Project.model_version %r không tương thích (cần %d)"
                           % (d.get("model_version"), MODEL_VERSION))
        return super(Project, cls).from_dict(d)

    def validate(self) -> List[str]:
        """Trả về danh sách vấn đề (rỗng = hợp lệ): id trùng, tham chiếu mồ côi,
        id trùng XUYÊN nhóm (cùng id vừa là file vừa là asset...)."""
        probs = []
        ids = {}
        cross: Dict[str, List[str]] = {}
        for group, nodes in (("file", self.files), ("container", self.containers),
                             ("asset", self.assets), ("resource", self.resources)):
            seen = set()
            for n in nodes:
                if n.id in seen:
                    probs.append("%s id trùng: %s" % (group, n.id))
                seen.add(n.id)
                cross.setdefault(n.id, [])
                if group not in cross[n.id]:
                    cross[n.id].append(group)
            ids[group] = seen
        for ident, groups in sorted(cross.items()):
            if len(groups) > 1:
                probs.append("id %r dùng ở nhiều nhóm: %s" % (ident, ", ".join(groups)))
        for c in self.containers:
            if c.file_id not in ids["file"]:
                probs.append("container %s tham chiếu file lạ: %r" % (c.id, c.file_id))
            if c.parent_id is not None and c.parent_id not in ids["container"]:
                probs.append("container %s có parent lạ: %r" % (c.id, c.parent_id))
        for a in self.assets:
            if a.container_id not in ids["container"]:
                probs.append("asset %s tham chiếu container lạ: %r" % (a.id, a.container_id))
        for r in self.resources:
            if r.asset_id not in ids["asset"]:
                probs.append("resource %s tham chiếu asset lạ: %r" % (r.id, r.asset_id))
        return probs


def dumps(obj) -> str:
    return json.dumps(obj.to_dict(), ensure_ascii=False, sort_keys=True, indent=1)


def loads(cls, text: str):
    try:
        data = json.loads(text)
    except ValueError as e:
        raise _corrupt("JSON hỏng: " + str(e)[:80])
    return cls.from_dict(data)
