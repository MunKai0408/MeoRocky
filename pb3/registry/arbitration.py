"""Trọng tài (đặc tả 4.6, Phụ lục C) — re-export từ sdk.arbitration.

Logic chấm điểm thuần túy nằm ở SDK để pipeline trích xuất (Claude 07)
dùng được mà không vi phạm tầng kiến trúc (sdk không import registry).
Mọi import cũ (`from registry import arbitration`) vẫn chạy.
"""
from __future__ import annotations

from sdk.arbitration import *  # noqa: F401,F403
from sdk.arbitration import (  # noqa: F401  (giữ tên dùng trong test/conformance)
    AUTO, ASK, NONE, MAX_OPTIONS, EPS, SIGNAL_NAMES, Weights, Signals,
    Contender, Ranked, Rejected, Decision, breakdown, score, rank,
    arbitrate, thresholds_from_config, contender_from_record,
    contender_from_candidate)
