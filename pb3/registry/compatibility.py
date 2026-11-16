"""Kiểm tra tương thích plugin (đặc tả 4.5, 4.9): api_version và thư viện `requires`.

- api_version lệch (cũ hơn tối thiểu HOẶC mới hơn lõi) -> `disabled` kèm lý do (decisions #25b).
- Thiếu thư viện khai trong `requires` -> `unavailable` (host KHÔNG tự cài; iSH-only, offline).
- Kiểm thư viện bằng find_spec, KHÔNG import: có mặt != chạy được. Bằng chứng import thật thuộc
  `core/ish_compat` (--deep). Registry không tuyên bố một thư viện "tương thích iSH" chỉ vì find_spec thấy nó.
"""
from __future__ import annotations

import importlib.util
import re
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional, Tuple

from sdk.api import API_MIN_VERSION, API_VERSION
from sdk.manifest import Manifest, api_compat

OK = "ok"
DISABLED = "disabled"          # manifest/api/import hỏng, bị ghi đè, hoặc người dùng tắt
UNAVAILABLE = "unavailable"    # thiếu dependency
STATES = (OK, DISABLED, UNAVAILABLE)

_REQ_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9_.\-]*)")
# tên gói -> tên module khi khác nhau (chỉ liệt kê các gói dự án dùng)
MODULE_ALIASES = {"unitypy": "UnityPy", "pyyaml": "yaml", "pillow": "PIL", "pycryptodome": "Crypto"}


@dataclass(frozen=True)
class Compat:
    state: str
    code: str                      # "E_OK" hoặc "E_PLUGIN_INVALID"
    reason: str                    # tiếng Việt, rỗng khi ok
    missing: Tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.state == OK


OK_COMPAT = Compat(OK, "E_OK", "")


def module_for_requirement(req: str) -> Optional[str]:
    """'lz4>=3' -> 'lz4'; 'unitypy' -> 'UnityPy'. None nếu tên không hợp lệ. Chỉ lấy module cấp cao nhất."""
    m = _REQ_NAME.match(req if isinstance(req, str) else "")
    if not m:
        return None
    name = m.group(1)
    mod = MODULE_ALIASES.get(name.lower(), name.replace("-", "_"))
    return mod.split(".")[0]


def default_finder(modname: str) -> bool:
    try:
        return importlib.util.find_spec(modname) is not None
    except (ImportError, ValueError, AttributeError):
        return False


def check_api(api_version) -> Compat:
    st = api_compat(api_version)
    if st == "ok":
        return OK_COMPAT
    if st == "invalid":
        return Compat(DISABLED, "E_PLUGIN_INVALID", "api_version phải là số nguyên (lõi dùng %d)" % API_VERSION)
    if st == "too_old":
        return Compat(DISABLED, "E_PLUGIN_INVALID",
                      "api_version %s cũ hơn mức tối thiểu %d của lõi: cần cập nhật plugin" % (api_version, API_MIN_VERSION))
    return Compat(DISABLED, "E_PLUGIN_INVALID",
                  "api_version %s mới hơn lõi (%d): cần cập nhật PB3 hoặc dùng plugin cũ hơn" % (api_version, API_VERSION))


def check_requires(requires: Iterable[str], finder: Optional[Callable[[str], bool]] = None) -> Compat:
    find = finder or default_finder
    missing: List[str] = []
    for req in requires:
        mod = module_for_requirement(req)
        if mod is None:
            return Compat(DISABLED, "E_PLUGIN_INVALID", "requires có tên không hợp lệ: %r" % (str(req)[:40],))
        if not find(mod):
            missing.append(str(req))
    if missing:
        return Compat(UNAVAILABLE, "E_PLUGIN_INVALID",
                      "thiếu thư viện: %s (khai trong requires; PB3 không tự cài, cài thủ công trong iSH)" % ", ".join(missing),
                      tuple(missing))
    return OK_COMPAT


def check_manifest(manifest: Manifest, finder: Optional[Callable[[str], bool]] = None) -> Compat:
    """Kết hợp api_version + requires (api lệch ưu tiên hơn thiếu thư viện)."""
    c = check_api(manifest.api_version)
    if not c.ok:
        return c
    return check_requires(manifest.requires, finder)
