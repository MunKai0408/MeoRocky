"""Manifest plugin (đặc tả 4.3, Phụ lục A) + kiểm tra. Mọi lỗi manifest -> E_PLUGIN_INVALID (không crash).

Có thể khai báo bằng dict MANIFEST trong code hoặc plugin.toml. TOML của lõi (core/tomlmini) không có bảng nội
tuyến nên `match.magic` ở TOML viết dạng chuỗi "OFFSET:HEX" hoặc "OFFSET:HEX:mô tả" và được chuẩn hóa thành dict.
Trường lạ (plugin mới hơn) bị bỏ qua và liệt kê ở `Manifest.ignored_fields` (4.9), không làm hỏng việc nạp.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core import tomlmini
from core.errors import PB3Error
from core.model import SupportLevel
from core.model import _Base as ModelBase

# ---- hằng số hợp đồng ----
# Phiên bản API plugin (đặc tả Phụ lục B). Định nghĩa ở đây (không phải
# sdk/api.py) để sdk/manifest.py không phải import ngược sdk.api — vòng
# import sdk.api <-> sdk.manifest (C10, decisions #181): sdk/api.py import
# lại từ đây và re-export cho `from sdk.api import API_VERSION`.
API_VERSION = 3
API_MIN_VERSION = 3
KINDS = ("container", "format", "engine", "profile", "extractor")
CAPABILITIES = ("read", "write", "container", "hint")
TRUE_COSTS = ("cheap", "normal", "heavy")
WRITE_MODES = ("PATCH_INPLACE", "REBUILD_SECTION", "REBUILD_ALL")           # mục 6.5
MATCH_KEYS = ("extensions", "magic", "name_regex", "container_kind", "min_score_to_probe")
LOCALE_HINT_KEYS = ("filename_regex", "per_locale_file_expected")
LIMIT_KEYS = ("max_entry_bytes", "hook_timeout_s")
REQUIRED = ("id", "name", "version", "api_version", "kind", "capabilities", "maturity", "priority",
            "match", "limits", "entry", "selftest", "docs")

_ID = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
_ENTRY = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*$")
_VERSION = re.compile(r"^\d+\.\d+\.\d+([-+][0-9A-Za-z.\-+]+)?$")
_HEX = re.compile(r"^(?:[0-9A-Fa-f]{2})+$")
MAX_REGEX_LEN = 300
MAX_PRIORITY = 1000
MAX_MAGIC_OFFSET = 1024 * 1024   # magic.offset tối đa 1 MiB (quá thì chắc chắn là lỗi khai)
_REQ_SPEC = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-]*(\s*(>=|<=|==|!=|~=|>|<)\s*[0-9][0-9A-Za-z.]*\s*)?$")


def _bad(detail: str, pid: Optional[str] = None) -> PB3Error:
    return PB3Error("E_PLUGIN_INVALID", detail, plugin_id=pid)


@dataclass
class Manifest(ModelBase):
    id: str
    name: str
    version: str
    api_version: int
    kind: str
    capabilities: List[str]
    maturity: str
    priority: int
    match: Dict[str, Any]
    limits: Dict[str, Any]
    entry: str
    selftest: bool
    docs: str
    true_cost: str = "normal"
    engines: List[str] = field(default_factory=list)
    locale_hints: Dict[str, Any] = field(default_factory=dict)
    write_modes: List[str] = field(default_factory=list)
    requires: List[str] = field(default_factory=list)
    deprecated_for: Optional[str] = None
    ignored_fields: List[str] = field(default_factory=list)

    # ---- tiện ích cho registry/loader ----
    @property
    def can_read(self) -> bool:
        return "read" in self.capabilities

    @property
    def can_write(self) -> bool:
        return "write" in self.capabilities

    @property
    def read_only(self) -> bool:
        return not self.can_write

    @property
    def max_entry_bytes(self) -> int:
        return int(self.limits["max_entry_bytes"])

    @property
    def hook_timeout_s(self) -> float:
        return float(self.limits["hook_timeout_s"])

    @property
    def maturity_rank(self) -> int:
        return SupportLevel.rank(self.maturity)

    @property
    def min_score_to_probe(self) -> float:
        return float(self.match.get("min_score_to_probe", 0.0))


def api_compat(api_version: Any) -> str:
    """'ok' | 'too_old' | 'too_new' | 'invalid' — để loader phân biệt lý do vô hiệu hóa (4.5, 4.9)."""
    if isinstance(api_version, bool) or not isinstance(api_version, int):
        return "invalid"
    if api_version < API_MIN_VERSION:
        return "too_old"
    if api_version > API_VERSION:
        return "too_new"
    return "ok"


def check_api(api_version: Any, pid: Optional[str] = None) -> int:
    st = api_compat(api_version)
    if st == "invalid":
        raise _bad("api_version phải là số nguyên (đặc tả: 3), nhận %r" % (api_version,), pid)
    if st == "too_old":
        raise _bad("api_version %d cũ hơn mức tối thiểu %d của lõi" % (api_version, API_MIN_VERSION), pid)
    if st == "too_new":
        raise _bad("api_version %d mới hơn lõi (%d); plugin bị vô hiệu hóa" % (api_version, API_VERSION), pid)
    return api_version


def _str_list(v: Any, name: str, pid: str, nonempty_items: bool = True) -> List[str]:
    if not isinstance(v, list) or any(not isinstance(x, str) or (nonempty_items and not x) for x in v):
        raise _bad("%s phải là danh sách chuỗi không rỗng" % name, pid)
    return v


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _regex(v: Any, name: str, pid: str) -> str:
    if not isinstance(v, str) or not v or len(v) > MAX_REGEX_LEN:
        raise _bad("%s phải là chuỗi regex 1..%d ký tự" % (name, MAX_REGEX_LEN), pid)
    try:
        re.compile(v)
    except re.error as e:
        raise _bad("%s không phải regex hợp lệ: %s" % (name, str(e)[:60]), pid)
    return v


def _norm_magic(item: Any, pid: str) -> Dict[str, Any]:
    if isinstance(item, str):                                     # dạng TOML: "OFFSET:HEX[:mô tả]"
        parts = item.split(":", 2)
        if len(parts) < 2 or not parts[0].isdigit():
            raise _bad("magic dạng chuỗi phải là 'OFFSET:HEX[:mô tả]'", pid)
        item = {"offset": int(parts[0]), "hex": parts[1]}
        if len(parts) == 3:
            item["desc"] = parts[2]
    if not isinstance(item, dict) or set(item) - {"offset", "hex", "desc"}:
        raise _bad("magic phải có dạng {offset, hex, desc?}", pid)
    off, hx = item.get("offset"), item.get("hex")
    if isinstance(off, bool) or not isinstance(off, int) or off < 0 or off > MAX_MAGIC_OFFSET:
        raise _bad("magic.offset phải là int trong [0, %d]" % MAX_MAGIC_OFFSET, pid)
    if not isinstance(hx, str) or not _HEX.match(hx) or len(hx) > 128:
        raise _bad("magic.hex phải là hex chẵn ký tự, tối đa 64 byte", pid)
    if "desc" in item and not isinstance(item["desc"], str):
        raise _bad("magic.desc phải là chuỗi", pid)
    out = {"offset": off, "hex": hx.upper()}
    if "desc" in item:
        out["desc"] = item["desc"]
    return out


def _check_match(m: Any, pid: str) -> Dict[str, Any]:
    if not isinstance(m, dict):
        raise _bad("match phải là object", pid)
    extra = sorted(set(m) - set(MATCH_KEYS))
    if extra:
        raise _bad("match có khóa lạ: " + ",".join(extra), pid)
    out = dict(m)
    if "extensions" in out:
        exts = _str_list(out["extensions"], "match.extensions", pid)
        if any(not e.startswith(".") or len(e) < 2 or e != e.lower() or "/" in e for e in exts):
            raise _bad("match.extensions: dạng '.ext' chữ thường", pid)
        out["extensions"] = exts
    if "magic" in out:
        if not isinstance(out["magic"], list):
            raise _bad("match.magic phải là danh sách", pid)
        out["magic"] = [_norm_magic(x, pid) for x in out["magic"]]
    if "name_regex" in out:
        _regex(out["name_regex"], "match.name_regex", pid)
    if out.get("container_kind") is not None and (
            not isinstance(out["container_kind"], str) or not out["container_kind"]):
        raise _bad("match.container_kind phải là chuỗi hoặc null", pid)
    if "min_score_to_probe" in out:
        v = out["min_score_to_probe"]
        if not _num(v) or not 0.0 <= v <= 1.0:
            raise _bad("match.min_score_to_probe phải thuộc [0,1]", pid)
    return out


def _check_limits(lim: Any, pid: str) -> Dict[str, Any]:
    if not isinstance(lim, dict):
        raise _bad("limits phải là object", pid)
    missing = [k for k in LIMIT_KEYS if k not in lim]
    if missing:
        raise _bad("limits thiếu: " + ", ".join(missing), pid)
    # Khóa limits LẠ (plugin mới hơn) được BỎ QUA và ghi vào ignored_fields
    # (forward-compat, như trường manifest lạ) thay vì vô hiệu hóa plugin.
    b, t = lim["max_entry_bytes"], lim["hook_timeout_s"]
    if isinstance(b, bool) or not isinstance(b, int) or not 0 < b <= 2 ** 40:
        raise _bad("limits.max_entry_bytes phải là int trong (0, 2^40]", pid)
    if not _num(t) or not 0 < t <= 3600:
        raise _bad("limits.hook_timeout_s phải thuộc (0, 3600]", pid)
    return {k: lim[k] for k in LIMIT_KEYS}


def _check_locale_hints(h: Any, pid: str) -> Dict[str, Any]:
    if not isinstance(h, dict) or set(h) - set(LOCALE_HINT_KEYS):
        raise _bad("locale_hints chỉ gồm: " + ", ".join(LOCALE_HINT_KEYS), pid)
    if "filename_regex" in h:
        _regex(h["filename_regex"], "locale_hints.filename_regex", pid)
    if "per_locale_file_expected" in h and not isinstance(h["per_locale_file_expected"], bool):
        raise _bad("locale_hints.per_locale_file_expected phải là bool", pid)
    return dict(h)


def _check_consistency(m: Manifest) -> None:
    caps, kind = m.capabilities, m.kind
    if not caps or len(set(caps)) != len(caps) or any(c not in CAPABILITIES for c in caps):
        raise _bad("capabilities rỗng/trùng/lạ: %r (hợp lệ: %s)" % (caps, "|".join(CAPABILITIES)), m.id)
    if "write" in caps and "read" not in caps:
        raise _bad("capability 'write' đòi hỏi 'read' (build dựng từ LocTable đã parse)", m.id)
    if kind in ("engine", "profile") and set(caps) - {"hint"}:
        raise _bad("kind=%s chỉ được khai 'hint' (không parse dữ liệu)" % kind, m.id)
    if kind in ("engine", "profile") and "hint" not in caps:
        raise _bad("kind=%s phải khai capability 'hint'" % kind, m.id)
    if kind == "format" and "read" not in caps:
        raise _bad("kind=format phải khai 'read'", m.id)
    if kind == "container" and "container" not in caps:
        raise _bad("kind=container phải khai 'container'", m.id)
    if kind == "extractor" and (set(caps) != {"read"}):
        raise _bad("kind=extractor chỉ đọc: capabilities phải là ['read']", m.id)
    if kind not in ("engine", "profile") and "hint" in caps:
        raise _bad("capability 'hint' chỉ dành cho kind=engine/profile", m.id)
    if kind in ("format", "engine", "profile", "extractor") and "container" in caps:
        raise _bad("capability 'container' chỉ dành cho kind=container", m.id)
    # write_modes
    if "write" in caps:
        if not m.write_modes:
            raise _bad("capability 'write' đòi hỏi write_modes", m.id)
    elif m.write_modes:
        raise _bad("write_modes chỉ hợp lệ khi có capability 'write'", m.id)
    if len(set(m.write_modes)) != len(m.write_modes) or any(w not in WRITE_MODES for w in m.write_modes):
        raise _bad("write_modes phải thuộc %s, không trùng" % "|".join(WRITE_MODES), m.id)
    # maturity (10.1): M3/M4 là ghi ngược; extractor chỉ tới M2
    rank = SupportLevel.rank(m.maturity)
    if rank >= 3 and "write" not in caps:
        raise _bad("maturity %s (WRITE/VERIFIED) đòi hỏi capability 'write'" % m.maturity, m.id)
    if kind == "extractor" and rank > 2:
        raise _bad("extractor chỉ đọc: maturity tối đa M2", m.id)


def parse_manifest(data: Any) -> Manifest:
    pid = data.get("id") if isinstance(data, dict) and isinstance(data.get("id"), str) else None
    if not isinstance(data, dict):
        raise _bad("manifest phải là object/dict")
    missing = [k for k in REQUIRED if k not in data]
    if missing:
        raise _bad("manifest thiếu trường bắt buộc: " + ", ".join(missing), pid)
    known = set(REQUIRED) | {"true_cost", "engines", "locale_hints", "write_modes", "requires", "deprecated_for"}
    ignored = sorted(k for k in data if k not in known and k != "ignored_fields")
    clean = {k: v for k, v in data.items() if k in known}
    try:
        m = Manifest.from_dict(dict(clean, ignored_fields=ignored))
    except PB3Error as e:
        raise _bad("manifest: " + e.detail, pid)
    if not _ID.match(m.id):
        raise _bad("id phải snake_case khớp ^[a-z][a-z0-9_]{1,40}$", m.id)
    if not m.name.strip():
        raise _bad("thiếu name", m.id)
    if not _VERSION.match(m.version):
        raise _bad("version phải dạng MAJOR.MINOR.PATCH (ví dụ 1.0.0)", m.id)
    check_api(m.api_version, m.id)
    if isinstance(m.priority, bool) or not 0 <= m.priority <= MAX_PRIORITY:
        raise _bad("priority phải là int trong [0,%d]" % MAX_PRIORITY, m.id)
    if m.kind not in KINDS:
        raise _bad("kind phải thuộc %s, nhận %r" % ("|".join(KINDS), m.kind), m.id)
    if not SupportLevel.valid(m.maturity):
        raise _bad("maturity phải thuộc M0..M4, nhận %r" % (m.maturity,), m.id)
    if m.true_cost not in TRUE_COSTS:
        raise _bad("true_cost phải thuộc %s" % "|".join(TRUE_COSTS), m.id)
    if not _ENTRY.match(m.entry):
        raise _bad("entry phải dạng module:Class", m.id)
    if not m.docs.strip():
        raise _bad("docs không được rỗng (ghi giới hạn đã biết)", m.id)
    if m.deprecated_for is not None and not _ID.match(m.deprecated_for):
        raise _bad("deprecated_for phải là id plugin hoặc null", m.id)
    _str_list(m.capabilities, "capabilities", m.id)
    _str_list(m.engines, "engines", m.id)
    _str_list(m.write_modes, "write_modes", m.id)
    _str_list(m.requires, "requires", m.id)
    m.match = _check_match(m.match, m.id)
    raw_limits = m.limits if isinstance(m.limits, dict) else {}
    extra_limits = sorted(set(raw_limits) - set(LIMIT_KEYS))
    for k in extra_limits:
        if ("limits." + k) not in m.ignored_fields:
            m.ignored_fields.append("limits." + k)
    m.ignored_fields.sort()
    m.limits = _check_limits(m.limits, m.id)
    for r in m.requires:
        if not _REQ_SPEC.match(r):
            raise _bad("requires phải là tên module, kèm ràng buộc phiên bản tùy chọn (vd 'lz4>=3'), nhận %r" % (r,), m.id)
    m.locale_hints = _check_locale_hints(m.locale_hints, m.id)
    _check_consistency(m)
    return m


def parse_manifest_text(text: str) -> Manifest:
    try:
        data = tomlmini.loads(text)
    except ValueError as e:
        raise _bad("manifest TOML sai cú pháp: " + str(e)[:80])
    return parse_manifest(data)
