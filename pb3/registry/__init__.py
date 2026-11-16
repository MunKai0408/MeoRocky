"""Hệ thống plugin (đặc tả 4.5-4.7): loader, compatibility, registry, arbitration.

Tầng: ui -> services -> registry -> sdk -> core. `core/` KHÔNG import `registry/` (có test kiểm).
Đã có (Claude 03): compatibility, loader, registry, arbitration (đợt 1); isolation (timeout/guard best effort),
conformance (`registry/conformance.py` + `sdk/testing.py`), template, `evidence.py` (maturity hiệu lực M3/M4, R5).
Chưa có: plugin nhiều file (decisions #35), lưu bật/tắt + menu quản lý plugin (C1, C3).
Loader có điểm cắm `import_guard` (isolation.py bọc việc import/khởi tạo plugin) và `corpus_dir` (bằng chứng M3/M4).
"""
