"""Kiểm bằng chứng maturity M3/M4 (đặc tả Phụ lục A; decisions #51, người dùng chốt hướng 1 cho R5).

Plugin tự khai `maturity` trong manifest, nhưng M3 (WRITE) và M4 (VERIFIED) phải có bằng chứng test. Loader KHÔNG
vô hiệu hóa plugin thiếu bằng chứng; nó tính `maturity hiệu lực` (thấp hơn hoặc bằng mức khai) để xếp hạng/trọng tài
và hiển thị, còn manifest giữ nguyên. Có bằng chứng thì tự lên lại, không phải sửa manifest.

Bằng chứng = file `tests/corpus/<id>.json` (đọc TĨNH bằng json, không chạy mã), dạng:
    {"id": "<id plugin>", "results": {"read": "PASS", "write": "PASS", "verify": "PASS"}}
    (tùy chọn) "version": "<version trong manifest của plugin>" — bằng chứng ghi version khác manifest
    thì KHÔNG tính (bằng chứng cũ, của bản plugin khác); không ghi version thì vẫn chấp nhận (tương thích
    cũ) nhưng kèm ghi chú nhắc ghi version. X16.
Mức hiệu lực: M3 cần read+write đều "PASS"; M4 cần thêm verify "PASS"; không đủ thì hạ về mức cao nhất được chứng minh,
sàn là M2. Plugin khai M0-M2 không bị kiểm.

Giới hạn phải nói thật: file này vẫn do người viết plugin tạo, nên đây chỉ kiểm HÌNH THỨC (tồn tại, đúng id, đúng
kết quả), không chứng minh test đã chạy thật. Việc chạy lại để đối chiếu thuộc SUPPORT_MATRIX (Claude 09).
"""
from __future__ import annotations

import json
import os
from typing import Tuple

from core.paths import is_within
from core.model import SupportLevel

MAX_EVIDENCE_BYTES = 64 * 1024
FLOOR = SupportLevel.M2
NEED = {SupportLevel.M3: ("read", "write"), SupportLevel.M4: ("read", "write", "verify")}


def evidence_path(corpus_dir: str, plugin_id: str) -> str:
    return os.path.join(corpus_dir, plugin_id + ".json")


def _proven_level(corpus_dir: str, plugin_id: str, manifest_version=None) -> Tuple[str, str, str]:
    """(mức được chứng minh, lý do nếu không có/không hợp lệ, ghi chú version).
    version khác manifest -> FLOOR (bằng chứng cũ); không ghi version -> chấp nhận nhưng ghi chú.
    Không bao giờ raise."""
    path = evidence_path(corpus_dir, plugin_id)
    rel = "tests/corpus/%s.json" % plugin_id
    if not os.path.isfile(path):
        return FLOOR, "thiếu %s" % rel, ""
    if not is_within(corpus_dir, path):
        return FLOOR, "%s thoát khỏi tests/corpus (symlink?)" % rel, ""
    try:
        if os.path.getsize(path) > MAX_EVIDENCE_BYTES:
            return FLOOR, "%s quá lớn (> %d byte)" % (rel, MAX_EVIDENCE_BYTES), ""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:   # OSError/ValueError + RecursionError (JSON lồng quá sâu): chỉ hạ mức, không crash
        return FLOOR, "%s không đọc được (%s)" % (rel, type(exc).__name__), ""
    if not isinstance(data, dict) or data.get("id") != plugin_id or not isinstance(data.get("results"), dict):
        return FLOOR, "%s sai dạng (cần id khớp và results là object)" % rel, ""
    ev_ver = data.get("version")
    version_note = ""
    if manifest_version is not None and ev_ver is not None and str(ev_ver) != str(manifest_version):
        return FLOOR, "%s ghi version %r khác manifest (%r) — bằng chứng cũ, không tính" % (rel, ev_ver, manifest_version), ""
    if manifest_version is not None and ev_ver is None:
        version_note = "%s không ghi version — chấp nhận theo tương thích cũ, nên ghi version khớp manifest (%s)" % (rel, manifest_version)
    passed = {k for k, v in data["results"].items() if isinstance(k, str) and v == "PASS"}
    level = FLOOR
    for lv in (SupportLevel.M3, SupportLevel.M4):
        if set(NEED[lv]) <= passed:
            level = lv
        else:
            break
    if level == FLOOR:
        return FLOOR, "%s chưa có PASS đủ cho M3 (cần read và write)" % rel, version_note
    return level, "", version_note


def effective_maturity(corpus_dir: str, plugin_id: str, declared: str, manifest_version=None) -> Tuple[str, str]:
    """(maturity hiệu lực, ghi chú). Ghi chú rỗng khi không phải hạ và bằng chứng ghi version khớp."""
    if SupportLevel.rank(declared) <= SupportLevel.rank(FLOOR):
        return declared, ""
    proven, why, version_note = _proven_level(corpus_dir, plugin_id, manifest_version)
    if SupportLevel.rank(proven) >= SupportLevel.rank(declared):
        return declared, version_note
    if not why:
        why = "%s chỉ chứng minh tới %s (cần %s)" % ("tests/corpus/%s.json" % plugin_id, proven,
                                                       "+".join(NEED[declared]))
    note = "khai %s nhưng hiệu lực %s: %s" % (declared, proven, why)
    if version_note:
        note += "; " + version_note
    return proven, note
