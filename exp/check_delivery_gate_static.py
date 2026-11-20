#!/usr/bin/env python3
"""Kiem tra tinh cong kiem tra truoc khi giao (P0-2, buoc 6):
sdk/gate.py chi stdlib + Python 3.9, khong UnityPy/lz4, khong ghi file;
manifest (files.json 112, tests.json 201); sw.js precache 140 muc co
sdk/gate.py, khong muc chet, CACHE_VERSION v66-20261120; bridge co op
delivery_gate + rebuild_apply bat verdict cong khi duoc truyen; UI du
id cong + nut Ghi bi khoa boi verdict + render cong esc du lieu game.
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

# 1. gate.py: chi import stdlib/core/sdk, khong cu phap 3.10+.
src = (SITE / "pb3" / "sdk" / "gate.py").read_text(encoding="utf-8")
tree = ast.parse(src)
STDLIB = {"__future__", "collections", "hashlib", "io", "json", "os",
          "re", "struct", "typing", "zipfile"}
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        for a in node.names:
            if a.name.split(".")[0] not in STDLIB:
                fails.append("gate.py import khong phai stdlib: %s"
                             % a.name)
    elif isinstance(node, ast.ImportFrom):
        root = (node.module or "").split(".")[0]
        if root not in STDLIB and root not in ("core", "sdk"):
            fails.append("gate.py import-from la: %s" % node.module)
    elif isinstance(node, ast.Match):
        fails.append("gate.py dung match (Python 3.10+)")
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        fails.append("gate.py dung X|Y (Python 3.10+)")
    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
            and node.func.id in ("eval", "exec"):
        fails.append("gate.py dung %s" % node.func.id)
try:
    compile(src, "gate.py", "exec", flags=0, dont_inherit=True)
except SyntaxError as e:
    fails.append("gate.py loi cu phap: %s" % e)

# 2. Khong UnityPy/lz4, khong goi mang, KHONG GHI FILE (cong chi doc).
for bad in ("unitypy", "UnityPy", "import lz4", "from lz4", "urllib",
            "requests", "socket", "http.client", "https://", "http://",
            "pip install", "curl ", "wget ", "/home/", "/mnt/", "/Users/",
            "/storage/", "/sdcard", "shutil", "os.remove", "os.rename",
            "os.unlink", "os.makedirs", "rebuild_zip"):
    if bad in src:
        fails.append("gate.py chua %r (cam)" % bad)
if re.search(r"\bopen\s*\(", src):
    fails.append("gate.py dung open( — cong chi nhan bytes/dict, khong doc/ghi file)")
if re.search(r"\bapk\b", src):
    fails.append("gate.py chua tu 'apk' (cam theo luat iSH-only)")
for need in ("def check_translation_completeness(",
             "def check_placeholders(",
             "def check_font_coverage(",
             "def font_entry_from_bytes(",
             "def run_gate(",
             "def canonical_translations_sha(",
             "GATE_VERSION", "delivery_allowed",
             "translation_complete", "placeholders", "diffverify",
             "fonts", "integrity", "monobehaviour",
             "REQUIRED_VIETNAMESE", "group_by_unit", "build_plan",
             "after_sha"):
    if need not in src:
        fails.append("gate.py thieu %r" % need)

# 3. Manifest.
files = json.loads((SITE / "pb3" / "files.json").read_text())["files"]
if len(files) != 112:
    fails.append("files.json co %d file, ky vong 112" % len(files))
if "pb3/sdk/gate.py" not in files:
    fails.append("files.json thieu pb3/sdk/gate.py")
tests = json.loads((SITE / "pb3" / "tests.json").read_text())["files"]
if len(tests) != 201:
    fails.append("tests.json co %d file, ky vong 201" % len(tests))
if "pb3/tests/test_gate.py" not in tests:
    fails.append("tests.json thieu pb3/tests/test_gate.py")

# 4. sw.js: precache 140 muc, co sdk/gate.py, khong muc chet, v22.
sw = (SITE / "sw.js").read_text(encoding="utf-8")
if '"pb3/sdk/gate.py"' not in sw:
    fails.append("sw.js precache thieu pb3/sdk/gate.py")
if 'CACHE_VERSION = "v66-20261120"' not in sw:
    fails.append("sw.js CACHE_VERSION khong phai v66-20261120")
m = re.search(r"const PRECACHE_URLS = \[(.*?)\];", sw, re.S)
if not m:
    fails.append("sw.js khong tim thay PRECACHE_URLS")
else:
    listed = re.findall(r'"([^"]+)"', m.group(1))
    if len(listed) != 140:
        fails.append("sw.js precache co %d muc, ky vong 140" % len(listed))
    dead = [u for u in listed if u != "./"
            and not (SITE / u).exists()]
    if dead:
        fails.append("sw.js precache co muc chet (khong co tren dia): %s"
                     % dead[:5])

# 5. bridge: op delivery_gate + rebuild_apply bat verdict khi truyen.
bridge = (SITE / "pb3" / "bridge.py").read_text(encoding="utf-8")
if "def delivery_gate(" not in bridge:
    fails.append("bridge.py thieu def delivery_gate(")
for need in ("translations_json", "source_json", "provenance_json",
             "pressed_path", "allowlist_json", "font_path",
             "source_sha256", "_check_size", "run_gate",
             "font_entry_from_bytes", "_font_scan_cached"):
    if need not in bridge:
        fails.append("bridge.py delivery_gate thieu %r" % need)
if "gate_verdict_json" not in bridge:
    fails.append("bridge.py rebuild_apply thieu gate_verdict_json (P0-2)")
for need in ("canonical_translations_sha", "delivery_allowed",
             "pre_write"):
    if need not in bridge:
        fails.append("bridge.py bat verdict cong thieu %r" % need)
if "rebuild_zip" in bridge:
    fails.append("bridge.py expose rebuild_zip (decision #70 cam)")

# 6. UI: index.html du id cong; app.js co handler + khoa nut Ghi + esc.
html = (SITE / "index.html").read_text(encoding="utf-8")
for i in ("gate-source-mode", "btn-gate-import-source", "gate-source-file",
          "gate-source-info", "btn-gate-import-pressed", "gate-pressed-file",
          "btn-gate-clear-pressed", "gate-pressed-info", "gate-font-mode",
          "btn-gate-import-font", "gate-font-file", "gate-font-info",
          "gate-allowlist", "gate-partial", "btn-gate-run", "gate-result"):
    if ('id="%s"' % i) not in html:
        fails.append("index.html thieu id %s" % i)
if "Cổng kiểm tra trước khi giao (bước 6)" not in html:
    fails.append("index.html thieu tieu de muc cong (buoc 6)")
app = (SITE / "app.js").read_text(encoding="utf-8")
for s in ("delivery_gate", "state.gate", "gateKey", "gateInvalidate",
          "refreshRebuildApplyEnabled", "gateRenderResult", "gateRun",
          "btn-gate-run", "gate-result",
          "Chưa qua cổng kiểm tra", "CHƯA ĐẠT"):
    if s not in app:
        fails.append("app.js thieu %r" % s)
for bad in ("Cong kiem tra truoc khi giao", "Chua qua cong kiem tra"):
    if bad in app:
        fails.append("app.js co chu tieng Viet khong dau: %r" % bad)
# Render cong phai esc du lieu tu ket qua kiem tra (ten/key/font/loi).
for s in ("esc(c.name)", "esc(c.summary_vi", "esc(e)", "esc(f.name)",
          "esc(m.key)", "esc(parsed.note"):
    if s not in app:
        fails.append("app.js render cong thieu esc: %r" % s)
# Reset khi doi/bo file phai huy verdict cong (khoa nut Ghi lai).
_start = app.find("function rebuildResetState()")
_end = app.find("\nfunction ", _start + 1) if _start >= 0 else -1
_body = app[_start:_end] if _start >= 0 and _end > _start else ""
if "gateInvalidate" not in _body:
    fails.append("app.js rebuildResetState khong huy verdict cong "
                 "(verdict cu phai het han khi doi file)")
# rebuildRun(false) phai kiem verdict + truyen verdict sang bridge.
_start = app.find("async function rebuildRun(")
_end = app.find("\n$(", _start + 1) if _start >= 0 else -1
_body = app[_start:_end] if _start >= 0 and _end > _start else ""
if "state.gate.verdict" not in _body:
    fails.append("app.js rebuildRun khong kiem verdict cong truoc khi ghi")
if "state.gate.result" not in _body:
    fails.append("app.js rebuildRun khong truyen verdict cong sang bridge")

print(json.dumps({"fails": fails}, ensure_ascii=False))
sys.exit(1 if fails else 0)
