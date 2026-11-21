#!/usr/bin/env python3
"""Sinh fixture SQLite localization cho test_format_sqlite_loc.py (F6).

Chay:  python3 pb3/tests/fixtures/sqlite_loc/build_fixture.py
        (hoac: python3 build_fixture.py ngay trong thu muc nay)

Tao (ghi de) file `a.sqlite` ngay trong thu muc nay — file NHO (~8 KB),
khong phai nhi phan lon, duoc commit cung repo nhu cac fixture khac.
KHONG commit file .sqlite lon: neu can mo rong fixture, them bang/hang
o day roi chay lai script thay vi sua tay file .sqlite.

Noi dung (khoa theo test_format_sqlite_loc.py::test_fixture_localization_table_and_skipped_table):
- Bang `strings` (key TEXT PRIMARY KEY, en TEXT, vi TEXT), 4 hang:
  hello / Hello / Xin chao; bye / Goodbye / Tam biet;
  welcome / Welcome, %@! / Chao mung, %@!;
  emoji / Congratulations (kem emoji U+1F389) / Chuc mung (kem emoji).
  -> plugin doc 8 entry, stats tables=1, rows=4.
- Bang `scores` (id INTEGER PRIMARY KEY, points INTEGER), 1 hang (1, 100):
  khong phai bang localization nen plugin bo qua kem canh bao nhac
  "scores" -> stats tables_total=2.
- Thu tu tao bang co dinh (strings truoc, scores sau) + VACUUM de file
  sinh lai giong nhau tren cung phien ban SQLite.

Chi thu vien chuan, Python >= 3.9.
"""
from __future__ import annotations

import os
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "a.sqlite")

ROWS_STRINGS = [
    ("hello", "Hello", "Xin chào"),
    ("bye", "Goodbye", "Tạm biệt"),
    ("welcome", "Welcome, %@!", "Chào mừng, %@!"),
    ("emoji", "Congratulations 🎉", "Chúc mừng 🎉"),
]


def build(path=OUT):
    """Tao moi a.sqlite (xoa file cu neu co) va tra duong dan file."""
    if os.path.exists(path):
        os.unlink(path)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE strings (key TEXT PRIMARY KEY, en TEXT, vi TEXT)")
        conn.executemany(
            "INSERT INTO strings VALUES (?, ?, ?)", ROWS_STRINGS)
        conn.execute(
            "CREATE TABLE scores (id INTEGER PRIMARY KEY, points INTEGER)")
        conn.execute("INSERT INTO scores VALUES (1, 100)")
        conn.commit()
        conn.execute("VACUUM")
        conn.commit()
    finally:
        conn.close()
    return path


def main():
    path = build()
    print("da sinh %s (%d byte)" % (path, os.path.getsize(path)))


if __name__ == "__main__":
    main()
