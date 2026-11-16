"""pytest: gắn nhãn tầng (fast/slow/meta) cho mọi test bằng tests/tiering.py (cùng logic với run_tests.py).

  python3 -m pytest tests --tier smoke       vài giây, test then chốt
  python3 -m pytest tests --tier fast        chỉ bộ nhanh (hằng ngày)
  python3 -m pytest tests --tier ish         test chỉ có nghĩa trên máy thật
  python3 -m pytest tests --tier fast,ish    ghép nhiều tầng (phân cách bằng dấu phẩy)
  python3 -m pytest tests -m "not slow"      tương đương cách dùng nhãn
  python3 -m pytest tests                    ĐẦY ĐỦ (mặc định, không bỏ gì âm thầm)

Fixture thiếu (CI web): 8 file nhị phân/UTF-16 đã lên main, 20 file còn
lại KHÔNG commit thêm (quyết định của Joseph) nên test nào mở 1 trong
20 file đó được gắn @pytest.mark.needs_fixture("đường-dẫn-tương-đối-từ-
tests/fixtures/") — thiếu file thì test SKIP kèm lý do (xem bằng
`pytest -rs`), không làm đỏ CI. Đặt STRICT_FIXTURES=1 để biến skip
thành lỗi (chỉ dùng khi cây đã đủ file, để kiểm chứng không có test
nào bị skip oan). Test cần 1 trong 8 file đã lên main thì KHÔNG gắn
marker, phải chạy thật.

Test phụ thuộc ngày: fixture `fixed_now` / `fixed_today` (đóng băng
datetime). Cách bền nhất vẫn là cho hàm nhận tham số `now=None`;
fixed_today chỉ là cầu nối tạm cho code cũ dùng `import datetime`.
"""
import datetime as _dt
import os
import pathlib
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tests import tiering  # noqa: E402

# --- needs_fixture (hợp nhất từ gói web-upgrade của Joseph) ---
FIXTURES = pathlib.Path(__file__).parent / "fixtures"
STRICT = os.environ.get("STRICT_FIXTURES") == "1"

FIXED_NOW = _dt.datetime(2026, 1, 15, 12, 0, 0)


@pytest.fixture
def fixture_path():
    """Trả về đường dẫn file mẫu: fixture_path('x.bundle')."""
    def _get(name):
        p = FIXTURES / name
        if not p.exists():
            pytest.skip(f"thiếu fixture: {name}")
        return p
    return _get


@pytest.fixture
def fixed_now():
    """Thời điểm cố định, truyền vào code cần `now`."""
    return FIXED_NOW


@pytest.fixture
def fixed_today(monkeypatch):
    """Đóng băng datetime.date.today()/datetime.datetime.now() cho module dùng `import datetime`."""
    class FrozenDatetime(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return FIXED_NOW if tz is None else FIXED_NOW.replace(tzinfo=tz)

        @classmethod
        def utcnow(cls):
            return FIXED_NOW

    class FrozenDate(_dt.date):
        @classmethod
        def today(cls):
            return FIXED_NOW.date()

    monkeypatch.setattr(_dt, "datetime", FrozenDatetime)
    monkeypatch.setattr(_dt, "date", FrozenDate)
    return FIXED_NOW


def pytest_addoption(parser):
    parser.addoption("--tier", action="store", default="full",
                     help="tầng cần chạy: %s | full (mặc định, tất cả); ghép bằng dấu phẩy" % " | ".join(tiering.TIER_NAMES))


def pytest_configure(config):
    for name, _kind, _daily, desc in tiering.TIERS:
        config.addinivalue_line("markers", "%s: %s" % (name, desc))
    config.addinivalue_line("markers", "needs_fixture(*names): cần file nhị phân trong tests/fixtures/")
    try:
        config._pb3_tiers = tiering.parse_tiers(config.getoption("--tier"))
    except ValueError as e:
        raise pytest.UsageError(str(e))


def pytest_collection_modifyitems(config, items):
    # (1) needs_fixture: thiếu file -> skip kèm lý do (trừ khi STRICT=1).
    # Chạy TRƯỚC lọc tầng để test bị skip vì thiếu fixture không bị lẫn
    # với test bị loại vì tầng.
    for item in items:
        m = item.get_closest_marker("needs_fixture")
        if not m:
            continue
        missing = [n for n in m.args if not (FIXTURES / n).exists()]
        if missing and not STRICT:  # STRICT: để test tự lỗi vì thiếu file
            item.add_marker(pytest.mark.skip(reason="thiếu fixture: " + ", ".join(missing)))
    # (2) Lọc tầng (giữ nguyên logic cũ).
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
