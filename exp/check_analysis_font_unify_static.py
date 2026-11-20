#!/usr/bin/env python3
"""Kiem tra tinh phan (D): liet ke font + gom 1 font (sdk/font_list.py).

- sdk/font_list.py: chi stdlib + core/sdk; co scan_source, build_unify_plan,
  merge_standalone_ttfs; SCHEMA_PLAN font_unify_plan/1; DONOR du 142/142;
  LIMITS noi ro atlas SDF chi sinh duoc trong Unity Editor.
- bridge.py: co op font_list + font_unify + cache _LAST_FONT_SCAN.
- index.html + app.js: nut btn-font-list + btn-font-unify, handler goi
  dung op, hien tmp_candidates + font_unified.zip.
- Manifest: files.json 111 (co pb3/sdk/font_list.py), tests.json 196
  (co pb3/tests/test_font_unify.py); sw.js CACHE_VERSION v61-20261120
  + precache co pb3/sdk/font_list.py.
- Tieng Viet moi du dau.
In JSON {"fails": [...]}.
"""
import ast
import json
import sys
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent  # P0-1: goc repo (Q2)
fails = []

app = (SITE / "app.js").read_text(encoding="utf-8")
html = (SITE / "index.html").read_text(encoding="utf-8")
sw = (SITE / "sw.js").read_text(encoding="utf-8")
bridge = (SITE / "pb3" / "bridge.py").read_text(encoding="utf-8")
mod = (SITE / "pb3" / "sdk" / "font_list.py").read_text(encoding="utf-8")

# 1. Module chi stdlib + core/sdk.
tree = ast.parse(mod)
allowed_top = {"os", "zipfile", "typing"}
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        for a in node.names:
            if a.name.split(".")[0] not in allowed_top:
                fails.append("font_list.py import ngoai stdlib: %s" % a.name)
    elif isinstance(node, ast.ImportFrom):
        top = (node.module or "").split(".")[0]
        if top not in allowed_top | {"core", "sdk", "__future__"}:
            fails.append("font_list.py from-import ngoai: %s" % node.module)
for s in ("def scan_source", "def build_unify_plan",
          "def merge_standalone_ttfs", 'SCHEMA_PLAN = "font_unify_plan/1"',
          'DONOR_NAME = "Be Vietnam Pro"', "REQUIRED_VIETNAMESE"):
    if s not in mod and "REQUIRED_VIETNAMESE" not in mod:
        fails.append("font_list.py thieu %r" % s)
if "REQUIRED_VIETNAMESE" not in mod and "font_sdk.REQUIRED" not in mod:
    fails.append("font_list.py khong tai dung bo 142 muc cua sdk/font")
if "142" not in mod:
    fails.append("font_list.py khong nhac 142 muc")
for s in ("Atlas SDF", "Unity Editor", "merge_vietnamese", "tmp_fallback"):
    if s not in mod:
        fails.append("font_list.py thieu %r (ranh gioi/hanh dong)" % s)
if "msdf-atlas-gen" in mod:
    fails.append("font_list.py dung msdf-atlas-gen (khong duoc)")

# 2. Bridge op + cache.
for s in ("def font_list(path):", "def font_unify(path, donor_path):",
          "_LAST_FONT_SCAN", "_font_scan_cached", "font_unified.zip",
          "font_unify_plan.json"):
    if s not in bridge:
        fails.append("bridge.py thieu %r" % s)

# 3. UI: nut + handler.
for s in ("btn-font-list", "btn-font-unify"):
    if s not in html:
        fails.append("index.html thieu %r" % s)
for s in ("Liệt kê font của game", "Dùng chung 1 font Be Vietnam Pro"):
    if s not in html:
        fails.append("index.html thieu nhan nut %r" % s)
for s in ('"font_list"', '"font_unify"', "tmp_candidates",
          "font_unified.zip", "font_list.json"):
    if s not in app:
        fails.append("app.js thieu %r" % s)

# 4. Manifest + precache + cache version.
files = json.loads((SITE / "pb3" / "files.json").read_text())["files"]
if len(files) != 111:
    fails.append("files.json co %d file, ky vong 111" % len(files))
if "pb3/sdk/font_list.py" not in files:
    fails.append("files.json thieu pb3/sdk/font_list.py")
tests = json.loads((SITE / "pb3" / "tests.json").read_text())["files"]
if len(tests) != 196:
    fails.append("tests.json co %d file, ky vong 196" % len(tests))
if "pb3/tests/test_font_unify.py" not in tests:
    fails.append("tests.json thieu pb3/tests/test_font_unify.py")
if 'CACHE_VERSION = "v61-20261120"' not in sw:
    fails.append("sw.js CACHE_VERSION khong phai v61-20261120")
if '"pb3/sdk/font_list.py"' not in sw:
    fails.append("sw.js precache thieu pb3/sdk/font_list.py")

# 5. Tieng Viet moi du dau.
for bad in ("Liet ke font", "Dung chung 1 font", "khong doc duoc",
            "tam font", "gom ve 1 font"):
    if bad in app or bad in html:
        fails.append("co chu tieng Viet khong dau: %r" % bad)

print(json.dumps({"fails": fails}, ensure_ascii=False))
sys.exit(1 if fails else 0)
