#!/usr/bin/env python3
"""Kiem tra tinh muc (E) rebuild [P0-1]: sdk/rebuild.py chi stdlib +
Python 3.9, khong UnityPy/lz4, khong expose rebuild_zip; manifest
(files.json 100, tests.json 174); sw.js precache 125 muc co
sdk/rebuild.py, khong wheel chet, CACHE_VERSION v37-20261117; bridge co
op rebuild_apply; plan.py co progress_fn; unity.py co
rebuild_bundle_streaming + khoa REBUILD_ALL con nguyen; UI du id (E);
2 cho innerHTML f.source da duoc esc + code (E) moi esc du lieu game.
In JSON {"fails": [...]}.
"""
import ast
import json
import os
import re
import sys
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent  # goc repo, khong co site/
fails = []

# 1. rebuild.py: chi import stdlib/core/sdk, khong cu phap 3.10+.
src = (SITE / "pb3" / "sdk" / "rebuild.py").read_text(encoding="utf-8")
tree = ast.parse(src)
STDLIB = {"__future__", "hashlib", "io", "json", "os", "re", "shutil",
          "typing", "zipfile"}
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        for a in node.names:
            if a.name.split(".")[0] not in STDLIB:
                fails.append("rebuild.py import khong phai stdlib: %s"
                             % a.name)
    elif isinstance(node, ast.ImportFrom):
        root = (node.module or "").split(".")[0]
        if root not in STDLIB and root not in ("core", "sdk"):
            fails.append("rebuild.py import-from la: %s" % node.module)
    elif isinstance(node, ast.Match):
        fails.append("rebuild.py dung match (Python 3.10+)")
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        fails.append("rebuild.py dung X|Y (Python 3.10+)")
    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
            and node.func.id in ("eval", "exec"):
        fails.append("rebuild.py dung %s" % node.func.id)
try:
    compile(src, "rebuild.py", "exec", flags=0, dont_inherit=True)
except SyntaxError as e:
    fails.append("rebuild.py loi cu phap: %s" % e)

# 2. Khong UnityPy/lz4, khong goi mang, khong expose rebuild_zip.
for bad in ("unitypy", "UnityPy", "import lz4", "from lz4", "urllib",
            "requests", "socket", "http.client", "https://", "http://",
            "pip install", "curl ", "wget ", "/home/", "/mnt/", "/Users/",
            "/storage/", "/sdcard", "I2Languages", "sm2_json", "datapack",
            "android", "Space Marshals"):
    if bad in src:
        fails.append("rebuild.py chua %r (cam)" % bad)
if re.search(r"\bapk\b", src):
    fails.append("rebuild.py chua tu 'apk' (cam theo luat iSH-only)")
if "containers.rebuild_zip" in src or "from sdk.containers" in src \
        or "import containers" in src:
    fails.append("rebuild.py goi/expose rebuild_zip (decision #70 cam)")
for need in ("def group_by_unit(", "def resolve_conflict_choices(",
             "def build_rebuild_preview(", "def apply_rebuild(",
             "MERGE_CONFLICTS_CAP", "beartea-merge-provenance/1",
             "beartea-translation-provenance/1", "monobehaviour",
             "STREAMING_MIN_BYTES"):
    if need not in src:
        fails.append("rebuild.py thieu %r" % need)
# Chong ghi de file goc: code moi phai co chot dest != source.
if "samefile" not in src and "realpath" not in src:
    fails.append("rebuild.py thieu chot dest != source (samefile/realpath)")

# 3. Manifest.
files = json.loads((SITE / "pb3" / "files.json").read_text())["files"]
if len(files) != 100:
    fails.append("files.json co %d file, ky vong 100" % len(files))
if "pb3/sdk/rebuild.py" not in files:
    fails.append("files.json thieu pb3/sdk/rebuild.py")
tests = json.loads((SITE / "pb3" / "tests.json").read_text())["files"]
if len(tests) != 174:
    fails.append("tests.json co %d file, ky vong 174" % len(tests))
if "pb3/tests/test_rebuild.py" not in tests:
    fails.append("tests.json thieu pb3/tests/test_rebuild.py")

# 4. sw.js: precache 125 muc, co sdk/rebuild.py, khong wheel chet, v20.
sw = (SITE / "sw.js").read_text(encoding="utf-8")
if '"pb3/sdk/rebuild.py"' not in sw:
    fails.append("sw.js precache thieu pb3/sdk/rebuild.py")
if 'CACHE_VERSION = "v37-20261117"' not in sw:
    fails.append("sw.js CACHE_VERSION khong phai v37-20261117")
m = re.search(r"const PRECACHE_URLS = \[(.*?)\];", sw, re.S)
if not m:
    fails.append("sw.js khong tim thay PRECACHE_URLS")
else:
    listed = re.findall(r'"([^"]+)"', m.group(1))
    if len(listed) != 125:
        fails.append("sw.js precache co %d muc, ky vong 125" % len(listed))
    dead = [u for u in listed if u != "./"
            and not (SITE / u).exists()]
    if dead:
        fails.append("sw.js precache co muc chet (khong co tren dia): %s"
                     % dead[:5])
    for wheel in ("pytest-9.0.2", "setuptools-82.0.1", "micropip-0.11.1",
                  "six-1.17.0", "pluggy-1.6.0"):
        if wheel in sw:
            fails.append("sw.js van precache wheel chet %s" % wheel)

# 5. bridge: op rebuild_apply + co gate confirm + khong expose rebuild_zip.
bridge = (SITE / "pb3" / "bridge.py").read_text(encoding="utf-8")
if "def rebuild_apply(" not in bridge:
    fails.append("bridge.py thieu def rebuild_apply(")
for need in ("dry_run=True", "confirm=False", "_check_size",
             "source_sha256", "E_CONFIG_INVALID", "rebuild",
             "zip_path", "filename"):
    if need not in bridge:
        fails.append("bridge.py rebuild_apply thieu %r" % need)
if "rebuild_zip" in bridge:
    fails.append("bridge.py expose rebuild_zip (decision #70 cam)")

# 6. plan.py: progress_fn tuy chon; unity.py: streaming + khoa REBUILD_ALL.
plan_src = (SITE / "pb3" / "sdk" / "plan.py").read_text(encoding="utf-8")
for need in ("progress_fn=None", "def apply_plan(",
             "def _apply_entry_changes("):
    if need not in plan_src:
        fails.append("plan.py thieu %r" % need)
unity_src = (SITE / "pb3" / "sdk" / "unity.py").read_text(encoding="utf-8")
for need in ("def rebuild_bundle_streaming(",
             "def _read_bundle_layout(", 'MATURITY = "M2"',
             "E_WRITE_DISABLED", "E_VERIFY_FAILED"):
    if need not in unity_src:
        fails.append("unity.py thieu %r" % need)

# 7. UI: index.html du id (E); app.js co handler + esc du lieu game.
html = (SITE / "index.html").read_text(encoding="utf-8")
for i in ("btn-translate-goto-rebuild", "rebuild-source",
          "btn-rebuild-import-json", "btn-rebuild-import-sidecar",
          "rebuild-json-file", "rebuild-sidecar-file",
          "rebuild-source-info", "rebuild-file-info",
          "btn-rebuild-reselect-file", "btn-rebuild-preview",
          "btn-rebuild-apply", "btn-rebuild-reextract",
          "btn-rebuild-choose-all-kept", "btn-rebuild-choose-all-dropped",
          "rebuild-result"):
    if ('id="%s"' % i) not in html:
        fails.append("index.html thieu id %s" % i)
if "Ép lại vào file gốc (E)" not in html:
    fails.append("index.html thieu tieu de muc (E)")
app = (SITE / "app.js").read_text(encoding="utf-8")
for s in ("rebuild_apply", "state.rebuild", "rebuildResetState",
          "btn-rebuild-preview", "btn-rebuild-apply",
          "data-rebuild-conflict",
          "E_ENCRYPTED", "E_TOO_LARGE", "E_VERIFY_FAILED",
          "Ép lại vào file gốc", "không phải IPA"):
    if s not in app:
        fails.append("app.js thieu %r" % s)
for bad in ("Ep lai vao file goc", "Chua co ban dich"):
    if bad in app:
        fails.append("app.js co chu tieng Viet khong dau: %r" % bad)

# 8. 2 cho innerHTML f.source phai duoc esc (Buoc 0) + code (E) esc.
esc_count = app.count("esc(f.source")
if esc_count < 3:
    fails.append("app.js chi co %d cho esc(f.source, ky vong >= 3 "
                 "(1804 + 2 cho da va)" % esc_count)
for ln, line in enumerate(app.splitlines(), 1):
    if "+ f.source" in line and "esc(f.source" not in line:
        fails.append("app.js:%d con noi + f.source khong esc: %s"
                     % (ln, line.strip()[:80]))
    if "f.source +" in line and "esc(f.source" not in line:
        fails.append("app.js:%d con f.source + khong esc: %s"
                     % (ln, line.strip()[:80]))
# Code render (E) phai esc du lieu don vi/key/xung dot.
for s in ("esc(u.unit)", "esc(m.key)", "esc(c.key)"):
    if s not in app:
        fails.append("app.js render (E) thieu esc: %r" % s)

# 9. Hoi quy D2 (sua-loi 2026-11-17): reset khi doi/bo file KHONG duoc
# xoa ban dich + sidecar da nhap (state.rebuildFile) — chi huy preview
# cu; canh bao khac ten phai doc duoc sidecar da nhap ngay sau reselect.
_start = app.find("function rebuildResetState()")
_end = app.find("\nfunction ", _start + 1) if _start >= 0 else -1
_body = app[_start:_end] if _start >= 0 and _end > _start else ""
if not _body:
    fails.append("app.js khong tim thay than ham rebuildResetState")
elif "state.rebuildFile = null" in _body:
    fails.append("app.js rebuildResetState xoa state.rebuildFile "
                 "(loi D2 tai pham: reselect xoa ban dich + sidecar)")
if "rebuildEffectiveProvenance" not in app:
    fails.append("app.js thieu rebuildEffectiveProvenance "
                 "(canh bao khac ten sau reselect)")

print(json.dumps({"fails": fails}, ensure_ascii=False))
sys.exit(1 if fails else 0)
