#!/usr/bin/env python3
"""Kiem tra tinh muc 8: sdk/plan.py chi stdlib + Python 3.9; manifest
(files.json 89, tests.json 158); sw.js precache co sdk/plan.py va
CACHE_VERSION v11-20261116 (C3 them fixtures/selftest.zip); bridge co 3 op plan_*. In JSON {"fails": [...]}.
"""
import ast
import json
import sys
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent / "site"
fails = []

# 1. plan.py: chi import stdlib, khong co cu phap 3.10+ (match/case, X|Y).
plan_src = (SITE / "pb3" / "sdk" / "plan.py").read_text(encoding="utf-8")
tree = ast.parse(plan_src)
STDLIB = {"__future__", "datetime", "hashlib", "json", "os", "re", "shutil",
          "typing", "zipfile"}
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        for a in node.names:
            if a.name.split(".")[0] not in STDLIB:
                fails.append("plan.py import khong phai stdlib: %s" % a.name)
    elif isinstance(node, ast.ImportFrom):
        root = (node.module or "").split(".")[0]
        if root not in STDLIB and root not in ("core", "sdk"):
            fails.append("plan.py import-from la: %s" % node.module)
    elif isinstance(node, ast.Match):
        fails.append("plan.py dung match (Python 3.10+)")
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        fails.append("plan.py dung X|Y (Python 3.10+)")
try:
    compile(plan_src, "plan.py", "exec", flags=0, dont_inherit=True)
except SyntaxError as e:
    fails.append("plan.py loi cu phap: %s" % e)

# 2. Manifest.
files = json.loads((SITE / "pb3" / "files.json").read_text())["files"]
if len(files) != 89:
    fails.append("files.json co %d file, ky vong 89" % len(files))
if "pb3/sdk/plan.py" not in files:
    fails.append("files.json thieu pb3/sdk/plan.py")
tests = json.loads((SITE / "pb3" / "tests.json").read_text())["files"]
if len(tests) != 158:
    fails.append("tests.json co %d file, ky vong 158" % len(tests))
if "pb3/tests/test_plan.py" not in tests:
    fails.append("tests.json thieu pb3/tests/test_plan.py")

# 3. sw.js precache + version.
sw = (SITE / "sw.js").read_text(encoding="utf-8")
if '"pb3/sdk/plan.py"' not in sw:
    fails.append("sw.js precache thieu pb3/sdk/plan.py")
if 'CACHE_VERSION = "v11-20261116"' not in sw:
    fails.append("sw.js CACHE_VERSION khong phai v11-20261116")

# 4. bridge co 3 op + khong goi mang trong plan.py.
bridge = (SITE / "pb3" / "bridge.py").read_text(encoding="utf-8")
for fn in ("def plan_build(", "def plan_apply(", "def plan_undo("):
    if fn not in bridge:
        fails.append("bridge.py thieu %s" % fn)
for bad in ("urllib", "requests", "socket", "http.client"):
    if bad in plan_src:
        fails.append("plan.py chua %r (khong duoc goi mang)" % bad)

# 5. UI: index.html + app.js co du id/handler plan, tieng Viet co dau.
html = (SITE / "index.html").read_text(encoding="utf-8")
for i in ("plan-translations", "plan-entry", "plan-assets", "plan-rebuild-all",
          "btn-plan-build", "btn-plan-select-all", "btn-plan-deselect-all",
          "btn-plan-download", "btn-plan-import", "plan-file", "plan-list",
          "btn-plan-dryrun", "btn-plan-apply", "btn-plan-resume",
          "btn-plan-undo", "plan-result"):
    if ('id="%s"' % i) not in html:
        fails.append("index.html thieu id %s" % i)
app = (SITE / "app.js").read_text(encoding="utf-8")
for s in ("plan_build", "plan_apply", "plan_undo", "renderPlanList",
          "REBUILD_ALL"):
    if s not in app:
        fails.append("app.js thieu %r" % s)

print(json.dumps({"fails": fails}, ensure_ascii=False))
sys.exit(1 if fails else 0)
