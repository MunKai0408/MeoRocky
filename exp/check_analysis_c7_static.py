#!/usr/bin/env python3
"""Kiem tra tinh muc C7: sdk/patch_pack.py chi stdlib + Python 3.9;
manifest (files.json 112, tests.json 202); sw.js precache co
sdk/patch_pack.py va CACHE_VERSION v76-20261122; bridge co op patch_pack;
UI co 2 nut xuat pack; khong co chuoi cam theo luat iSH-only trong
patch_pack.py (ca chuoi template cua script ap). In JSON {"fails": [...]}.
"""
import ast
import json
import re
import sys
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent  # P0-1: goc repo (Q2)
fails = []

# 1. patch_pack.py: chi import stdlib/core/sdk, khong co cu phap 3.10+.
src = (SITE / "pb3" / "sdk" / "patch_pack.py").read_text(encoding="utf-8")
tree = ast.parse(src)
STDLIB = {"__future__", "copy", "datetime", "hashlib", "json", "os",
          "re", "shutil", "typing", "zipfile"}
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        for a in node.names:
            if a.name.split(".")[0] not in STDLIB:
                fails.append("patch_pack.py import khong phai stdlib: %s"
                             % a.name)
    elif isinstance(node, ast.ImportFrom):
        root = (node.module or "").split(".")[0]
        if root not in STDLIB and root not in ("core", "sdk"):
            fails.append("patch_pack.py import-from la: %s" % node.module)
    elif isinstance(node, ast.Match):
        fails.append("patch_pack.py dung match (Python 3.10+)")
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        fails.append("patch_pack.py dung X|Y (Python 3.10+)")
    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
            and node.func.id in ("eval", "exec"):
        fails.append("patch_pack.py dung %s" % node.func.id)
try:
    compile(src, "patch_pack.py", "exec", flags=0, dont_inherit=True)
except SyntaxError as e:
    fails.append("patch_pack.py loi cu phap: %s" % e)

# 2. Khong co chuoi cam theo luat iSH-only (quyet dinh ca chuoi template
# cua script ap + README nam trong file nay).
for bad in ("https://", "http://", "pip install", "curl ", "wget ",
            "/home/", "/mnt/", "/Users/", "/storage/", "/sdcard",
            "/root/pb3", "I2Languages", "sm2_json", "datapack", "android",
            "Space Marshals"):
    if bad in src:
        fails.append("patch_pack.py chua %r (cam theo luat iSH-only)" % bad)
if re.search(r"\bapk\b", src):
    fails.append("patch_pack.py chua tu 'apk' (cam theo luat iSH-only)")

# 3. Script ap trong template: parse duoc, chi stdlib + core/sdk.
m = re.search(r"APPLY_SCRIPT = r'''(.+?)'''", src, re.S)
if not m:
    fails.append("khong tim thay APPLY_SCRIPT trong patch_pack.py")
else:
    try:
        stree = ast.parse(m.group(1))
    except SyntaxError as e:
        fails.append("APPLY_SCRIPT loi cu phap: %s" % e)
        stree = None
    if stree is not None:
        simps = set()
        for node in ast.walk(stree):
            if isinstance(node, ast.Import):
                simps.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                simps.add((node.module or "").split(".")[0])
        if not simps <= {"__future__", "argparse", "hashlib", "json", "os",
                         "shutil", "sys", "pathlib", "core", "sdk"}:
            fails.append("APPLY_SCRIPT import la: %s" % sorted(simps))
        # "chi doc" không còn trong danh sách từ chối của template: bundle
        # v9 ghi được từ M-formats (2026-11-17), chỉ còn "ma hoa" là trường
        # hợp từ chối vì không đọc/ghi được.
        for need in ("--allow-write", "before_sha", "source_sha256",
                     "DRY-RUN", "backup-goc", "REBUILD_ALL",
                     "ma hoa"):
            if need not in m.group(1):
                fails.append("APPLY_SCRIPT thieu %r" % need)

# 4. Manifest.
files = json.loads((SITE / "pb3" / "files.json").read_text())["files"]
if len(files) != 112:
    fails.append("files.json co %d file, ky vong 112" % len(files))
if "pb3/sdk/patch_pack.py" not in files:
    fails.append("files.json thieu pb3/sdk/patch_pack.py")
tests = json.loads((SITE / "pb3" / "tests.json").read_text())["files"]
if len(tests) != 202:
    fails.append("tests.json co %d file, ky vong 202" % len(tests))
if "pb3/tests/test_patch_pack.py" not in tests:
    fails.append("tests.json thieu pb3/tests/test_patch_pack.py")

# 5. sw.js precache + version.
sw = (SITE / "sw.js").read_text(encoding="utf-8")
if '"pb3/sdk/patch_pack.py"' not in sw:
    fails.append("sw.js precache thieu pb3/sdk/patch_pack.py")
if 'CACHE_VERSION = "v76-20261122"' not in sw:
    fails.append("sw.js CACHE_VERSION khong phai v76-20261122")

# 6. bridge co op patch_pack + khong goi mang trong patch_pack.py.
bridge = (SITE / "pb3" / "bridge.py").read_text(encoding="utf-8")
if "def patch_pack(" not in bridge:
    fails.append("bridge.py thieu def patch_pack(")
if "patch_pack.zip" not in bridge or "patch-pack-" not in bridge:
    fails.append("bridge.py patch_pack thieu zip_path/filename")
for bad in ("urllib", "requests", "socket", "http.client"):
    if bad in src:
        fails.append("patch_pack.py chua %r (khong duoc goi mang)" % bad)

# 7. UI: index.html + app.js co du id/handler xuat pack, tieng Viet co dau.
html = (SITE / "index.html").read_text(encoding="utf-8")
for i in ("btn-mod-patch-pack", "btn-plan-patch-pack"):
    if ('id="%s"' % i) not in html:
        fails.append("index.html thieu id %s" % i)
app = (SITE / "app.js").read_text(encoding="utf-8")
for s in ("patch_pack", "btn-mod-patch-pack", "btn-plan-patch-pack",
          "state.lastMod", "Xuất patch pack"):
    if s not in app:
        fails.append("app.js thieu %r" % s)
# Op patch_pack phai truyen duong dan file dau tien (nhu moi op khac).
for call in ('[inputPath(), "mod"', '[inputPath(), "plan"'):
    if call not in app:
        fails.append("app.js goi patch_pack thieu inputPath() dau tien: %r"
                     % call)
for bad in ("Xuat patch pack", "Chua co ket qua Mod thu"):
    if bad in app:
        fails.append("app.js co chu tieng Viet khong dau: %r" % bad)

print(json.dumps({"fails": fails}, ensure_ascii=False))
sys.exit(1 if fails else 0)
