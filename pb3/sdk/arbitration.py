"""Trọng tài (đặc tả 4.6, Phụ lục C): chấm điểm, ngưỡng, tie-break xác định, lý do chọn/loại.

Nằm ở SDK (chuyển từ registry/arbitration.py ở Claude 07) để pipeline trích
xuất dùng được mà không vi phạm tầng kiến trúc (sdk không import registry);
registry/arbitration.py chỉ re-export để tương thích ngược.

Nguyên tắc:
- Điểm chỉ dùng để CHỌN, không thay probe/validate (Phụ lục C). `Candidate.score` do plugin tự khai chỉ được
  đưa vào tín hiệu `schema_fit` (trọng số 0.18): một plugin KHÔNG thể tự ép mình lên >= 0.80.
- >= auto_select (0.80): chọn tự động. [ask_min, auto_select): hỏi người dùng, tối đa 5 ứng viên kèm lý do.
  < ask_min (0.55): không ứng viên (E_NO_CANDIDATE) kèm tối đa 5 ứng viên "gần nhất".
- Điểm bằng nhau (sai khác < 1e-9): tie-break cố định maturity cao > priority cao > đường dẫn ngắn > adapter_id
  theo alphabet. Không dùng random, không "thử đến khi parse được".
- `conflict_margin` (mặc định 0 = đúng đặc tả): nếu > 0 và ứng viên thứ hai (khác adapter/đường dẫn) kém top
  dưới margin thì hạ về "ask" thay vì tự chọn (xung đột -> không đoán bừa).
Module không import registry: dùng được độc lập (pipeline Claude 07 dùng trực tiếp).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from core.errors import PB3Error
from core.model import SupportLevel
from sdk.types import Diagnostic

AUTO, ASK, NONE = "auto", "ask", "none"
MAX_OPTIONS = 5
EPS = 1e-9
SIGNAL_NAMES = ("magic_match", "ext_match", "name_signal", "schema_fit", "locale_evidence",
                "manifest_ref", "content_lang_fit", "kb_agreement")


@dataclass(frozen=True)
class Weights:
    w_magic: float = 0.30
    w_ext: float = 0.10
    w_name: float = 0.12
    w_schema: float = 0.18
    w_locale: float = 0.10
    w_manifest: float = 0.08
    w_content: float = 0.07
    w_kb: float = 0.05

    @classmethod
    def from_config(cls, cfg: Any) -> "Weights":
        try:
            return cls(**{n: float(cfg.get("scoring." + n)) for n in cls.__dataclass_fields__})   # type: ignore[attr-defined]
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            raise PB3Error("E_CONFIG_INVALID",
                           "cấu hình scoring lỗi (%s); dùng trọng số mặc định" % type(e).__name__)

    def as_pairs(self) -> List[tuple]:
        return [("magic_match", self.w_magic), ("ext_match", self.w_ext), ("name_signal", self.w_name),
                ("schema_fit", self.w_schema), ("locale_evidence", self.w_locale),
                ("manifest_ref", self.w_manifest), ("content_lang_fit", self.w_content),
                ("kb_agreement", self.w_kb)]


def _unit(v: Any, name: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or (isinstance(v, float) and math.isnan(v)):
        raise PB3Error("E_PLUGIN_INVALID", "tín hiệu %s phải là số thuộc [0,1], nhận %r" % (name, v))
    return min(1.0, max(0.0, float(v)))


@dataclass
class Signals:
    magic_match: float = 0.0
    ext_match: float = 0.0
    name_signal: float = 0.0
    schema_fit: float = 0.0
    locale_evidence: float = 0.0
    manifest_ref: float = 0.0
    content_lang_fit: float = 0.0
    kb_agreement: float = 0.0

    def __post_init__(self) -> None:
        for n in SIGNAL_NAMES:
            setattr(self, n, _unit(getattr(self, n), n))


def breakdown(sig: Signals, w: Weights) -> Dict[str, float]:
    return {n: round(getattr(sig, n) * wt, 6) for n, wt in w.as_pairs()}


def score(sig: Signals, w: Weights) -> float:
    """Phụ lục C: tổng có trọng số, kẹp [0,1]."""
    return min(1.0, max(0.0, sum(getattr(sig, n) * wt for n, wt in w.as_pairs())))


@dataclass
class Contender:
    adapter_id: str
    path: str
    signals: Signals
    maturity: str = "M0"
    priority: int = 0
    reasons: List[str] = field(default_factory=list)


@dataclass
class Ranked:
    adapter_id: str
    path: str
    score: float
    maturity: str
    priority: int
    reasons: List[str]
    parts: Dict[str, float]

    def short(self) -> str:
        return "%s @ %s (%.2f)" % (self.adapter_id, self.path, self.score)


@dataclass
class Rejected:
    adapter_id: str
    path: str
    score: float
    reason: str


@dataclass
class Decision:
    status: str                          # auto | ask | none
    chosen: Optional[Ranked]
    options: List[Ranked]                # ask: các lựa chọn (<= 5); auto: [chosen]; none: []
    rejected: List[Rejected]
    nearest: List[Ranked]                # none: tối đa 5 ứng viên gần nhất
    reason_vi: str
    code: str                            # E_OK | E_AMBIGUOUS | E_NO_CANDIDATE
    used_tiebreak: bool = False

    def to_diagnostic(self, plugin_id: str = "") -> Optional[Diagnostic]:
        if self.status == AUTO:
            return None
        if self.status == ASK:
            return Diagnostic("E_AMBIGUOUS", "warning", detail=self.reason_vi, plugin_id=plugin_id)
        near = "; ".join(r.short() for r in self.nearest)
        return Diagnostic("E_NO_CANDIDATE", "error", detail=(self.reason_vi + (" Gần nhất: " + near if near else "")),
                          plugin_id=plugin_id)


def _rank_key(r: Ranked):
    return (-round(r.score, 9), -SupportLevel.rank(r.maturity), -r.priority, len(r.path), r.path, r.adapter_id)


def rank(contenders: Sequence[Contender], w: Weights) -> List[Ranked]:
    """Chấm điểm + sắp xếp xác định. Trùng (adapter_id, path): giữ bản điểm cao nhất."""
    best: Dict[tuple, Ranked] = {}
    for c in contenders:
        r = Ranked(c.adapter_id, c.path, score(c.signals, w), c.maturity, int(c.priority), list(c.reasons),
                   breakdown(c.signals, w))
        k = (c.adapter_id, c.path)
        if k not in best or r.score > best[k].score:
            best[k] = r
    return sorted(best.values(), key=_rank_key)


def _why_lost(win: Ranked, lose: Ranked) -> str:
    if abs(win.score - lose.score) >= EPS:
        return "điểm thấp hơn (%.2f < %.2f)" % (lose.score, win.score)
    if SupportLevel.rank(win.maturity) != SupportLevel.rank(lose.maturity):
        return "ngang điểm; thua tie-break maturity (%s < %s)" % (lose.maturity, win.maturity)
    if win.priority != lose.priority:
        return "ngang điểm; thua tie-break priority (%d < %d)" % (lose.priority, win.priority)
    if len(win.path) != len(lose.path):
        return "ngang điểm; thua tie-break đường dẫn (dài hơn)"
    return "ngang điểm; thua tie-break theo adapter_id/đường dẫn"


def arbitrate(contenders: Sequence[Contender], w: Optional[Weights] = None, auto_select: float = 0.80,
              ask_min: float = 0.55, max_options: int = MAX_OPTIONS, conflict_margin: float = 0.0) -> Decision:
    if not 0.0 <= ask_min <= auto_select <= 1.0:
        raise PB3Error("E_CONFIG_INVALID", "cần 0 <= ask_min <= auto_select <= 1")
    w = w or Weights()
    ranked = rank(contenders, w)
    nearest = ranked[:max_options]
    if not ranked or ranked[0].score < ask_min - EPS:
        why = "không có ứng viên đạt ngưỡng %.2f" % ask_min if ranked else "không có ứng viên nào"
        rej = [Rejected(r.adapter_id, r.path, r.score, "dưới ngưỡng %.2f" % ask_min) for r in ranked]
        return Decision(NONE, None, [], rej, nearest, why + ".", "E_NO_CANDIDATE")
    top = ranked[0]
    tied = [r for r in ranked[1:] if abs(r.score - top.score) < EPS]
    used_tb = bool(tied)
    second = ranked[1] if len(ranked) > 1 else None

    def _rejects(keep: List[Ranked]) -> List[Rejected]:
        kept = set((r.adapter_id, r.path) for r in keep)
        out = []
        for r in ranked:
            if (r.adapter_id, r.path) in kept:
                continue
            out.append(Rejected(r.adapter_id, r.path, r.score,
                                "dưới ngưỡng %.2f" % ask_min if r.score < ask_min - EPS else _why_lost(top, r)))
        return out

    conflict = (conflict_margin > 0 and second is not None and (top.score - second.score) < conflict_margin - EPS
                and second.score >= ask_min - EPS and not tied)
    if top.score >= auto_select - EPS and not conflict:
        msg = "chọn %s (điểm %.2f >= %.2f)" % (top.adapter_id, top.score, auto_select)
        if used_tb:
            msg += "; ngang điểm với %d ứng viên, tie-break xác định" % len(tied)
        return Decision(AUTO, top, [top], _rejects([top]), [], msg + ".", "E_OK", used_tb)
    band = [r for r in ranked if r.score >= ask_min - EPS][:max_options]
    if conflict:
        msg = "xung đột: %s và %s chênh dưới %.2f; cần người dùng chọn." % (top.adapter_id, second.adapter_id,  # type: ignore[union-attr]
                                                                         conflict_margin)
    else:
        msg = "điểm cao nhất %.2f trong khoảng [%.2f, %.2f): cần người dùng chọn." % (top.score, ask_min, auto_select)
    return Decision(ASK, None, band, _rejects(band), [], msg, "E_AMBIGUOUS", used_tb)


DEFAULT_AUTO_SELECT = 0.80
DEFAULT_ASK_MIN = 0.55


def thresholds_from_config(cfg: Any) -> "tuple":
    """Đọc cặp ngưỡng scoring từ cấu hình, kiểm chéo: cần 0 <= ask_min <
    auto_select <= 1. Cặp không hợp lệ -> CẢNH BÁO + dùng mặc định cho CẢ CẶP
    (không tự đoán một nửa). Trả (auto_select, ask_min, cảnh_báo_hoặc_None).
    `arbitrate()` vẫn raise với ngưỡng truyền trực tiếp sai (test-locked)."""
    try:
        auto = float(cfg.get("scoring.auto_select"))
        ask = float(cfg.get("scoring.ask_min"))
    except (KeyError, TypeError, ValueError) as exc:
        return DEFAULT_AUTO_SELECT, DEFAULT_ASK_MIN, \
            "ngưỡng scoring không đọc được (%s); dùng mặc định 0.80/0.55" % type(exc).__name__
    if not 0.0 <= ask < auto <= 1.0:
        return DEFAULT_AUTO_SELECT, DEFAULT_ASK_MIN, \
            "ngưỡng scoring %.2f/%.2f không hợp lệ (cần 0 <= ask_min < auto_select <= 1); dùng mặc định 0.80/0.55" % (ask, auto)
    return auto, ask, None


def contender_from_record(record: Any, cand: Any, match: Any, *, locale_evidence: float = 0.0,
                          manifest_ref: float = 0.0, content_lang_fit: float = 0.0,
                          kb_agreement: float = 0.0) -> Contender:
    """Như contender_from_candidate nhưng tự lấy maturity từ
    `PluginRecord.maturity_effective` (đã kiểm bằng chứng, R5) — bên gọi không
    cần nhớ truyền maturity, tránh dùng nhầm mức khai."""
    if getattr(record, "manifest", None) is None:
        raise PB3Error("E_PLUGIN_INVALID", "record chưa có manifest", plugin_id=getattr(record, "plugin_id", ""))
    return contender_from_candidate(record.manifest, cand, match,
                                    locale_evidence=locale_evidence, manifest_ref=manifest_ref,
                                    content_lang_fit=content_lang_fit, kb_agreement=kb_agreement,
                                    maturity=record.maturity_effective)


def contender_from_candidate(manifest: Any, cand: Any, match: Any, *, locale_evidence: float = 0.0,
                             manifest_ref: float = 0.0, content_lang_fit: float = 0.0,
                             kb_agreement: float = 0.0, maturity: Optional[str] = None) -> Contender:
    """`maturity`: truyền `PluginRecord.maturity_effective` (đã kiểm bằng chứng, R5); bỏ trống thì dùng mức khai.
    Ghép tín hiệu HOST (match_info: magic/ext/name) với tín hiệu nội dung. `cand.score` của plugin chỉ thành
    schema_fit (không cho plugin tự nâng điểm tổng)."""
    sig = Signals(magic_match=match.magic_match, ext_match=match.ext_match, name_signal=match.name_signal,
                  schema_fit=cand.score, locale_evidence=locale_evidence, manifest_ref=manifest_ref,
                  content_lang_fit=content_lang_fit, kb_agreement=kb_agreement)
    return Contender(manifest.id, cand.path, sig, maturity or manifest.maturity, manifest.priority,
                     list(match.reasons) + list(cand.reasons))
