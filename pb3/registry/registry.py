"""Registry: chỉ mục plugin theo loại file, engine, capability, định dạng (đặc tả 4.5).

- Chỉ nhận PluginRecord do loader tạo (đã qua kiểm manifest/api/requires/khởi tạo): `add()` từ chối record
  state=ok mà thiếu instance/manifest. Plugin không qua kiểm tra không bao giờ "active".
- `set_enabled()` chỉ bật được plugin state=ok; plugin lỗi/thiếu dependency giữ nguyên lý do.
- `candidates_for()` trả các plugin ĐÁNG THỬ cho một đường dẫn theo thứ tự 4.5: (1) magic tin cậy, (2) extension,
  (3) tên, (4) profile từ KB, (5) true_cost=heavy cuối cùng. Chỉ plugin có tín hiệu khớp mới được trả về (không thử mọi
  plugin cho mọi file). Kết quả này CHƯA phải lựa chọn: đưa tiếp vào arbitration.
"""
from __future__ import annotations

import copy
import posixpath
import re
from functools import lru_cache
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

from core.errors import PB3Error
from registry import compatibility as compat
from registry.loader import LoadReport, PluginRecord, SCOPE_BUILTIN, SCOPE_USER

MAX_NAME_CHARS = 512        # cắt đường dẫn trước khi áp regex của plugin (giảm rủi ro ReDoS)
MAGIC_RELIABLE_BYTES = 4    # magic dài >= 4 byte coi là tin cậy (1.0); ngắn hơn chỉ 0.5 (Phụ lục C)

STAGE_MAGIC, STAGE_EXT, STAGE_NAME, STAGE_PROFILE, STAGE_HEAVY = 1, 2, 3, 4, 5


@dataclass
class MatchInfo:
    """Tín hiệu do HOST tính từ manifest (plugin không tự khai điểm). Đầu vào cho arbitration."""
    record: PluginRecord
    magic_match: float = 0.0
    ext_match: float = 0.0
    name_signal: float = 0.0
    container_match: bool = False
    profile: bool = False
    stage: int = 0
    reasons: List[str] = field(default_factory=list)

    @property
    def plugin_id(self) -> str:
        return self.record.plugin_id


def _ext_of(name: str) -> str:
    return posixpath.splitext(name.lower())[1]


def magic_signal(magic: Sequence[Dict[str, Any]], head: bytes) -> float:
    """1.0: một magic dài >= 4 byte khớp hoàn toàn; 0.5: khớp một phần (magic ngắn, hoặc mới khớp phần đầu
    vì `head` ngắn); 0.0: không khớp."""
    best = 0.0
    for m in magic:
        want = bytes.fromhex(m["hex"])
        off = int(m["offset"])
        got = head[off:off + len(want)]
        if got == want:
            best = max(best, 1.0 if len(want) >= MAGIC_RELIABLE_BYTES else 0.5)
        elif got and want.startswith(got) and len(got) < len(want):
            best = max(best, 0.5)
    return best


@lru_cache(maxsize=128)
def _compiled_regex(pattern: str):
    """Regex đã biên dịch của plugin (cache: candidates_for gọi lại nhiều lần cho mỗi file)."""
    try:
        return re.compile(pattern)
    except re.error:
        return None


def name_signal(pattern: str, path: str) -> float:
    """1.0 nếu regex khớp tên file; 0.6 nếu chỉ khớp phần thư mục; 0 nếu không (hoặc regex lỗi)."""
    rx = _compiled_regex(pattern)
    if rx is None:
        return 0.0
    base = posixpath.basename(path)[:MAX_NAME_CHARS]
    if rx.search(base):
        return 1.0
    if rx.search(path[:MAX_NAME_CHARS]):
        return 0.6
    return 0.0


def match_info(record: PluginRecord, path: str, head: bytes = b"", container_kind: Optional[str] = None,
               profile_ids: Iterable[str] = ()) -> MatchInfo:
    m = record.manifest
    info = MatchInfo(record)
    if m is None:
        return info
    mt = m.match
    if mt.get("magic"):
        info.magic_match = magic_signal(mt["magic"], head)
        if info.magic_match:
            info.reasons.append("magic %s" % ("khớp" if info.magic_match >= 1.0 else "khớp một phần"))
    if mt.get("extensions"):
        low = path.lower()
        if any(low.endswith(e) for e in mt["extensions"]):
            info.ext_match = 1.0
            info.reasons.append("đuôi file %s" % _ext_of(path))
    if mt.get("name_regex"):
        info.name_signal = name_signal(mt["name_regex"], path)
        if info.name_signal:
            info.reasons.append("tên khớp regex (%.1f)" % info.name_signal)
    ck = mt.get("container_kind")
    if ck and container_kind and ck == container_kind:
        info.container_match = True
        info.reasons.append("container_kind=%s" % ck)
    if record.plugin_id in set(profile_ids):
        info.profile = True
        info.reasons.append("profile từ KB")
    if info.magic_match >= 1.0:
        info.stage = STAGE_MAGIC
    elif info.ext_match or info.container_match or info.magic_match:
        info.stage = STAGE_EXT
    elif info.name_signal:
        info.stage = STAGE_NAME
    elif info.profile:
        info.stage = STAGE_PROFILE
    if info.stage and m.true_cost == "heavy":
        info.stage = STAGE_HEAVY
    return info


class Registry:
    def __init__(self) -> None:
        self._records: Dict[str, PluginRecord] = {}     # id -> record đang dùng
        self._rejected: List[PluginRecord] = []         # record lỗi/bị ghi đè (để hiển thị lý do)
        self.warnings: List[str] = []
        self._index: Optional[Dict[str, Dict[str, List[str]]]] = None

    # ---- đăng ký ----
    def add(self, rec: PluginRecord) -> None:
        if rec.state == compat.OK and (rec.instance is None or rec.manifest is None):
            raise PB3Error("E_PLUGIN_INVALID", "record state=ok phải có instance và manifest", plugin_id=rec.plugin_id)
        self._index = None
        cur = self._records.get(rec.plugin_id)
        if cur is None:
            if rec.state == compat.OK or rec.manifest is not None:
                self._records[rec.plugin_id] = rec
            else:
                self._rejected.append(rec)               # manifest hỏng: id chỉ là '?tên' -> chỉ để hiển thị
            return
        # Trùng id: loader đã xử lý trùng theo phạm vi (user thắng, decisions #36) nhưng
        # `report.records` vẫn theo thứ tự khám phá (builtin trước), nên bản thắng có thể
        # đến SAU bản thua. Không giữ bản đến trước một cách mù quáng: bản ok thắng bản
        # không ok; nếu cả hai đều không ok thì bản user thắng bản builtin (4.5). Bản bị
        # thay vẫn giữ nguyên lý do và chuyển sang `_rejected` để hiển thị.
        wins = ((rec.state == compat.OK and cur.state != compat.OK)
                or (rec.state != compat.OK and cur.state != compat.OK
                    and rec.scope == SCOPE_USER and cur.scope == SCOPE_BUILTIN))
        if wins:
            self._rejected.append(cur)
            self._records[rec.plugin_id] = rec
            return
        # trùng id khi đăng ký thủ công: không thay thế âm thầm (loader đã xử lý trùng theo phạm vi)
        raise PB3Error("E_PLUGIN_INVALID", "id '%s' đã được đăng ký" % rec.plugin_id, plugin_id=rec.plugin_id)

    def add_report(self, report: LoadReport) -> "Registry":
        self.warnings.extend(report.warnings)
        for rec in report.records:
            try:
                self.add(rec)
            except PB3Error as e:
                # KHÔNG sửa record gốc (bên gọi còn giữ nó); bản sao vào _rejected,
                # giữ nguyên lý do sẵn có (vd "trùng id...") thay vì ghi đè bằng lỗi đăng ký.
                bad = copy.copy(rec)
                bad.state, bad.code = compat.DISABLED, e.code
                if not bad.reason:
                    bad.reason = e.detail
                self._rejected.append(bad)
        return self

    @classmethod
    def from_report(cls, report: LoadReport) -> "Registry":
        return cls().add_report(report)

    # ---- trạng thái ----
    def get(self, plugin_id: str) -> Optional[PluginRecord]:
        return self._records.get(plugin_id)

    def records(self) -> List[PluginRecord]:
        """Tất cả record (kể cả lỗi), sắp theo id để hiển thị ổn định."""
        return sorted(list(self._records.values()) + self._rejected, key=lambda r: (r.plugin_id, r.scope))

    def active(self) -> List[PluginRecord]:
        return sorted((r for r in self._records.values() if r.active), key=lambda r: r.plugin_id)

    def set_enabled(self, plugin_id: str, enabled: bool) -> PluginRecord:
        rec = self._records.get(plugin_id)
        if rec is None:
            raise PB3Error("E_PLUGIN_INVALID", "không có plugin '%s'" % plugin_id, plugin_id=plugin_id)
        if enabled and rec.state != compat.OK:
            raise PB3Error("E_PLUGIN_INVALID", "không bật được: %s" % (rec.reason or rec.state), plugin_id=plugin_id)
        rec.enabled = bool(enabled)
        self._index = None
        return rec

    def rows(self) -> List[Dict[str, Any]]:
        """Bảng cho `plugin list`: id, kind, engine, maturity, trạng thái, lý do (4.8)."""
        return [r.to_row() for r in self.records()]

    # ---- chỉ mục ----
    def _build(self) -> Dict[str, Dict[str, List[str]]]:
        idx: Dict[str, Dict[str, List[str]]] = {"kind": {}, "capability": {}, "engine": {}, "extension": {},
                                                "container_kind": {}}
        for r in self.active():
            m = r.manifest
            assert m is not None
            idx["kind"].setdefault(m.kind, []).append(r.plugin_id)
            for c in m.capabilities:
                idx["capability"].setdefault(c, []).append(r.plugin_id)
            for e in m.engines:
                idx["engine"].setdefault(e.lower(), []).append(r.plugin_id)
            for x in m.match.get("extensions", []):
                idx["extension"].setdefault(x.lower(), []).append(r.plugin_id)
            if m.match.get("container_kind"):
                idx["container_kind"].setdefault(m.match["container_kind"], []).append(r.plugin_id)
        return idx

    def _look(self, table: str, key: str) -> List[PluginRecord]:
        if self._index is None:
            self._index = self._build()
        return [self._records[i] for i in self._index[table].get(key, [])]

    def by_kind(self, kind: str) -> List[PluginRecord]:
        return self._look("kind", kind)

    def by_capability(self, cap: str) -> List[PluginRecord]:
        return self._look("capability", cap)

    def by_engine(self, engine: str) -> List[PluginRecord]:
        """Plugin KHAI engine này. `engines` chỉ là gợi ý (Phụ lục A): plugin generic vẫn chạy khi engine lạ."""
        return self._look("engine", engine.lower())

    def by_extension(self, ext: str) -> List[PluginRecord]:
        return self._look("extension", ext.lower())

    def by_container_kind(self, ck: str) -> List[PluginRecord]:
        return self._look("container_kind", ck)

    # ---- ứng viên cho một đường dẫn ----
    def candidates_for(self, path: str, head: bytes = b"", container_kind: Optional[str] = None,
                       profile_ids: Iterable[str] = (), kinds: Optional[Sequence[str]] = None,
                       capability: Optional[str] = None) -> List[MatchInfo]:
        """Plugin đáng thử cho `path`, theo thứ tự 4.5 rồi tie-break xác định (maturity, priority, id)."""
        profiles = list(profile_ids)
        out = []
        for r in self.active():
            m = r.manifest
            assert m is not None
            if kinds is not None and m.kind not in kinds:
                continue
            if capability is not None and capability not in m.capabilities:
                continue
            info = match_info(r, path, head, container_kind, profiles)
            if info.stage:
                out.append(info)
        out.sort(key=lambda i: (i.stage, -i.record.maturity_rank_effective,
                                -i.record.manifest.priority, i.plugin_id))        # type: ignore[union-attr]
        return out


def build_registry(root, cfg=None, disabled_ids: Iterable[str] = (), finder=None, import_guard=None,
                 only_ids: Optional[Iterable[str]] = None) -> Registry:
    """Quét plugins/ (builtin) và plugins_user/ (user) theo cấu hình, nạp và dựng Registry.
    `root` = thư mục gốc dự án (Paths.root). Thư mục không tồn tại -> bỏ qua (không lỗi).
    `only_ids`: chỉ import các id này (B2); None = nạp đủ."""
    from core.paths import resolve_inside
    from registry.loader import CACHE_FILENAME, SCOPE_BUILTIN, SCOPE_USER, discover
    b = cfg.get("paths.plugins") if cfg is not None else "plugins"
    u = cfg.get("paths.plugins_user") if cfg is not None else "plugins_user"
    dirs = [(SCOPE_BUILTIN, str(resolve_inside(root, b))), (SCOPE_USER, str(resolve_inside(root, u)))]
    corpus = str(resolve_inside(root, "tests/corpus"))        # bằng chứng M3/M4 (R5, decisions #51)
    off = list(disabled_ids)
    if cfg is not None:                                       # plugin người dùng đã tắt (C3, decisions #57)
        off += [x for x in cfg.get("plugins.disabled") if x not in off]
    cache_file = None
    if cfg is None or cfg.get("registry.discovery_cache"):    # B1: cache duyệt manifest tĩnh (mặc định bật)
        ws = cfg.get("paths.workspace") if cfg is not None else "workspace"
        cache_file = str(resolve_inside(root, ws) / "cache" / CACHE_FILENAME)
    return Registry.from_report(discover(dirs, off, finder, import_guard, corpus_dir=corpus,
                                        only_ids=only_ids, cache_file=cache_file))
