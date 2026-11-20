#!/usr/bin/env python3
"""Kiem tra tinh muc C9: worker pool batch cau hinh duoc 1-2 (mac dinh 2,
tu dong 1 khi RAM thiet bi <= 4 GB, luu localStorage) + sua cau sai ve
song song (pool chay song song THAT: moi worker 1 thread he dieu hanh
that; chi ben trong 1 worker thi Python tuan tu vi Pyodide khong co
pthreads). Manifest (files.json 112, tests.json 201) sau phan D; sw.js
CACHE_VERSION v67-20261121; khong them file site nao.
In JSON {"fails": [...]}.
"""
import json
import sys
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent  # P0-1: goc repo (Q2)
fails = []

app = (SITE / "app.js").read_text(encoding="utf-8")
html = (SITE / "index.html").read_text(encoding="utf-8")
readme = (SITE / "README.md").read_text(encoding="utf-8")
pool = (SITE / "worker-pool.js").read_text(encoding="utf-8")
sw = (SITE / "sw.js").read_text(encoding="utf-8")

# 1. Co che cau hinh N trong app.js.
for s in ("POOL_SIZE_LS_KEY", "pb3_pool_size", "defaultPoolSize",
          "deviceMemoryGB", "__PB3_DEVICE_MEMORY", "POOL_SIZE_LOW_MEMORY_GB",
          "clampPoolSize", "loadPoolSize", "savePoolSize", "getPoolSize",
          "poolLabel", "POOL_SIZE_MIN", "POOL_SIZE_MAX",
          "batch-pool-size", "batch-pool-size-note", "syncPoolSizeUI",
          "__PB3_POOL_SIZE", "áp dụng từ đợt batch sau",
          "giữ nguyên pool cũ"):
    if s not in app:
        fails.append("app.js thieu %r" % s)
# Tu dong 1 khi deviceMemory <= 4.
if "mem <= POOL_SIZE_LOW_MEMORY_GB" not in app:
    fails.append("app.js khong co dieu kien tu dong 1 khi deviceMemory <= 4")
if "POOL_SIZE_LOW_MEMORY_GB = 4" not in app:
    fails.append("app.js nguong RAM thap khong phai 4 GB")
# Gioi han 1-2.
if "POOL_SIZE_MIN = 1" not in app or "POOL_SIZE_MAX = 2" not in app:
    fails.append("app.js gioi han worker khong phai 1-2")
# Pool ton trong N: makePoolExecutor dung getPoolSize, executor nho poolSize,
# ensureBatchExecutor tao lai pool khi doi N.
if "const n = getPoolSize();" not in app:
    fails.append("makePoolExecutor khong dung getPoolSize()")
if "poolSize: n" not in app and "poolSize:" not in app:
    fails.append("executor pool khong luu poolSize")
if "state.executor.poolSize !== getPoolSize()" not in app:
    fails.append("ensureBatchExecutor khong tao lai pool khi doi N")
if "jobs: br.executorKind === \"pool\" ? (br.poolSize || 2) : 1" not in app:
    fails.append("combinedSummary khong dung br.poolSize cho jobs")
# Khong con hang so POOL_SIZE = 2 hay chuoi dong cu.
if "const POOL_SIZE = 2" in app:
    fails.append("app.js van con const POOL_SIZE = 2")
for old in ('state.executorNote = "pool 2 worker"',
            '"Chạy batch (pool 2 worker)"',
            '(s.jobs === 2 ? "pool 2 worker" : "1 worker")',
            "để khởi động pool 2 worker).",
            '"Pool 2 worker (mỗi worker 1 Pyodide + PB3 riêng, ~2x RAM so với 1 worker):"',
            "KB của 2 worker không gộp được"):
    if old in app:
        fails.append("app.js van con chuoi cu: %r" % old)

# 2. worker-pool.js: bao ve runOn slot khong ton tai + ghi chu C9.
if "slotIdx >= this.size" not in pool:
    fails.append("worker-pool.js khong co bao ve runOn slot ngoai pham vi")
if "C9" not in pool:
    fails.append("worker-pool.js khong ghi chu C9")

# 3. UI: select + note + chu song song dung trong index.html.
for i in ('id="batch-pool-size"', 'id="batch-pool-size-note"',
          '<option value="1">1 worker', '<option value="2" selected>2 worker'):
    if i not in html:
        fails.append("index.html thieu %r" % i)
for s in ("Pool worker song song thật", "thread hệ điều hành thật",
          "Pyodide không có pthreads", "RAM thiết bị ≤ 4 GB",
          "Dùng pool worker"):
    if s not in html:
        fails.append("index.html thieu %r" % s)
for old in ("chạy song song bằng pool 2 worker (không dùng threading)",
            "Pool 2 worker song song",
            "Dùng pool 2 worker",
            "để khởi động pool 2 worker)."):
    if old in html:
        fails.append("index.html van con cau cu: %r" % old)

# 4. README: cau dung ve song song that + N cau hinh duoc.
for s in ("pool worker song song thật", "thread hệ điều hành thật",
          "song song thật (batch 2 game", "chọn được 1–2"):
    if s not in readme:
        fails.append("README.md thieu %r" % s)
for old in ("pool 2 worker song song",
            "không có threading nên song song đi bằng 2 worker"):
    if old in readme:
        fails.append("README.md van con cau cu: %r" % old)

# 5. Manifest sau phan D (them sdk/font_list.py + tests/test_font_unify.py).
files = json.loads((SITE / "pb3" / "files.json").read_text())["files"]
if len(files) != 112:
    fails.append("files.json co %d file, ky vong 112" % len(files))
tests = json.loads((SITE / "pb3" / "tests.json").read_text())["files"]
if len(tests) != 201:
    fails.append("tests.json co %d file, ky vong 201" % len(tests))

# 6. sw.js CACHE_VERSION v19.
if 'CACHE_VERSION = "v67-20261121"' not in sw:
    fails.append("sw.js CACHE_VERSION khong phai v67-20261121")
if 'CACHE_VERSION = "v13-20261116"' in sw:
    fails.append("sw.js van con CACHE_VERSION v13")

# 7. Tieng Viet moi du dau (khong co ban khong dau cua cac chu moi).
for bad in ("So worker", "Tu dong", "song song that", "nhe RAM",
            "ap dung tu dot", "thiet bi nay bao RAM", "luu lai qua lan tai"):
    if bad in app or bad in html:
        fails.append("co chu tieng Viet khong dau: %r" % bad)

print(json.dumps({"fails": fails}, ensure_ascii=False))
sys.exit(1 if fails else 0)
