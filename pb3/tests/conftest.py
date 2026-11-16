"""pytest: gắn nhãn tầng (fast/slow/meta) cho mọi test bằng tests/tiering.py (cùng logic với run_tests.py).

  python3 -m pytest tests --tier smoke       vài giây, test then chốt
  python3 -m pytest tests --tier fast        chỉ bộ nhanh (hằng ngày)
  python3 -m pytest tests --tier ish         test chỉ có nghĩa trên máy thật
  python3 -m pytest tests --tier fast,ish    ghép nhiều tầng (phân cách bằng dấu phẩy)
  python3 -m pytest tests -m "not slow"      tương đương cách dùng nhãn
  python3 -m pytest tests                    ĐẦY ĐỦ (mặc định, không bỏ gì âm thầm)
"""
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tests import tiering  # noqa: E402


def pytest_addoption(parser):
    parser.addoption("--tier", action="store", default="full",
                     help="tầng cần chạy: %s | full (mặc định, tất cả); ghép bằng dấu phẩy" % " | ".join(tiering.TIER_NAMES))


def pytest_configure(config):
    for name, _kind, _daily, desc in tiering.TIERS:
        config.addinivalue_line("markers", "%s: %s" % (name, desc))
    try:
        config._pb3_tiers = tiering.parse_tiers(config.getoption("--tier"))
    except ValueError as e:
        raise pytest.UsageError(str(e))


def pytest_collection_modifyitems(config, items):
    wanted, keep, dropped = config._pb3_tiers, [], []
    for item in items:
        tier = tiering.classify_id(item.nodeid)
        sm = tiering.is_smoke_id(item.nodeid)
        if tier != tiering.FAST:
            item.add_marker(getattr(pytest.mark, tier))
        if sm:
            item.add_marker(pytest.mark.smoke)
        (keep if tiering.is_selected(wanted, tier, sm) else dropped).append(item)
    if dropped:
        config.hook.pytest_deselected(items=dropped)
        items[:] = keep


def pytest_sessionfinish(session, exitstatus):
    # Xả bộ nhớ đệm _Facts (phân tích file test) xuống đĩa cho lần chạy sau.
    tiering.flush_facts_cache()
