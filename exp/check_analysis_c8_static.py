#!/usr/bin/env python3
"""Kiem tra tinh muc C8: sdk/format_probe.py chi stdlib + core/sdk,
Python 3.9 (khong match, khong X|Y, khong eval/exec, khong goi mang);
manifest (files.json 109, tests.json 188); sw.js precache co
sdk/format_probe.py va CACHE_VERSION v49-20261118; bridge co op
format_probe; UI co nut probe + input file rieng + nut tai JSON;
bang ENGINE_SUPPORT trung thuc (moi engine deu co unsupported khong
rong; unknown co supported rong); tieng Viet moi du dau.
In JSON {"fails": [...]}.
"""
import ast
import json
import sys
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent  # P0-1: goc repo (Q2)
sys.path.insert(0, str(SITE / "pb3"))
fails = []

# 1. format_probe.py: chi import stdlib/core/sdk, khong co cu phap 3.10+.
src = (SITE / "pb3" / "sdk" / "format_probe.py").read_text(encoding="utf-8")
tree = ast.parse(src)
STDLIB = {"__future__", "hashlib", "re", "typing"}
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        for a in node.names:
            if a.name.split(".")[0] not in STDLIB:
                fails.append("format_probe.py import khong phai stdlib: %s"
                             % a.name)
    elif isinstance(node, ast.ImportFrom):
        root = (node.module or "").split(".")[0]
        if root not in STDLIB and root not in ("core", "sdk"):
            fails.append("format_probe.py import-from la: %s" % node.module)
    elif isinstance(node, ast.Match):
        fails.append("format_probe.py dung match (Python 3.10+)")
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        fails.append("format_probe.py dung X|Y (Python 3.10+)")
    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
            and node.func.id in ("eval", "exec"):
        fails.append("format_probe.py dung %s" % node.func.id)
try:
    compile(src, "format_probe.py", "exec", flags=0, dont_inherit=True)
except SyntaxError as e:
    fails.append("format_probe.py loi cu phap: %s" % e)

# 2. Khong goi mang, khong chuoi cam iSH-only.
for bad in ("urllib", "requests", "socket", "http.client", "https://",
            "http://", "pip install", "curl ", "wget ", "/home/", "/mnt/",
            "/Users/", "/storage/", "/sdcard", "/root/pb3"):
    if bad in src:
        fails.append("format_probe.py chua %r (cam)" % bad)

# 3. Bang ENGINE_SUPPORT trung thuc: import that va kiem tra cau truc.
try:
    from sdk import format_probe as fp
    for eng in ("unity", "godot", "unreal", "cocos", "defold",
                "gamemaker", "unknown"):
        spec = fp.ENGINE_SUPPORT.get(eng)
        if spec is None:
            fails.append("ENGINE_SUPPORT thieu engine %s" % eng)
            continue
        if not spec.get("unsupported"):
            fails.append("ENGINE_SUPPORT[%s] khong co unsupported (gia vo ho tro)" % eng)
        if not spec.get("next_steps"):
            fails.append("ENGINE_SUPPORT[%s] khong co next_steps" % eng)
        if not spec.get("note"):
            fails.append("ENGINE_SUPPORT[%s] khong co note bang chung" % eng)
    if fp.ENGINE_SUPPORT["unknown"]["supported"]:
        fails.append("ENGINE_SUPPORT[unknown] co supported (gia vo ho tro)")
    if fp.PROBE_VERSION != 1 or fp.PROBE_FORMAT != "pb3-format-probe":
        fails.append("PROBE_VERSION/PROBE_FORMAT sai")
except Exception as e:  # noqa: BLE001
    fails.append("khong import duoc sdk.format_probe: %s" % e)

# 4. Manifest.
files = json.loads((SITE / "pb3" / "files.json").read_text())["files"]
if len(files) != 109:
    fails.append("files.json co %d file, ky vong 109" % len(files))
if "pb3/sdk/format_probe.py" not in files:
    fails.append("files.json thieu pb3/sdk/format_probe.py")
tests = json.loads((SITE / "pb3" / "tests.json").read_text())["files"]
if len(tests) != 188:
    fails.append("tests.json co %d file, ky vong 188" % len(tests))
if "pb3/tests/test_format_probe.py" not in tests:
    fails.append("tests.json thieu pb3/tests/test_format_probe.py")

# 5. sw.js precache + version.
sw = (SITE / "sw.js").read_text(encoding="utf-8")
if '"pb3/sdk/format_probe.py"' not in sw:
    fails.append("sw.js precache thieu pb3/sdk/format_probe.py")
if 'CACHE_VERSION = "v49-20261118"' not in sw:
    fails.append("sw.js CACHE_VERSION khong phai v49-20261118")

# 6. bridge co op format_probe + khong tra ok:false cho file la (probe
# thanh cong thi ok True, engine unknown — kiem tra chuoi bao dam).
bridge = (SITE / "pb3" / "bridge.py").read_text(encoding="utf-8")
if "def format_probe(" not in bridge:
    fails.append("bridge.py thieu def format_probe(")
if "format_probe.probe_data" not in bridge and "fp.probe_data" not in bridge:
    fails.append("bridge.py format_probe khong goi probe_data")
if '"report": report' not in bridge:
    fails.append("bridge.py format_probe khong tra truong report")

# 7. UI: index.html + app.js co du id/handler probe, tieng Viet co dau.
html = (SITE / "index.html").read_text(encoding="utf-8")
for i in ("btn-probe-run", "btn-probe-download", "probe-file-input",
          "probe-result"):
    if ('id="%s"' % i) not in html:
        fails.append("index.html thieu id %s" % i)
app = (SITE / "app.js").read_text(encoding="utf-8")
for s in ("format_probe", "btn-probe-run", "btn-probe-download",
          "probe-file-input", "probeReportHtml", "Probe định dạng",
          "Hỗ trợ được", "Chưa hỗ trợ", "Bước tiếp theo",
          "__PB3_LAST_PROBE"):
    if s not in app:
        fails.append("app.js thieu %r" % s)
if "Tải báo cáo JSON" not in html:
    fails.append("index.html thieu nut 'Tải báo cáo JSON'")
# Goi y probe khi thao tac cu loi E_UNSUPPORTED_FORMAT (trong runOp).
if "Probe định dạng" not in app or "E_UNSUPPORTED_FORMAT" not in app:
    fails.append("app.js thieu goi y probe khi loi E_UNSUPPORTED_FORMAT")
# Probe phai dung duoc file rieng (khong qua gameExtOk): handler doc tu
# probe-file-input thay vi chi inputPath()/state.file.
if 'probeFile.arrayBuffer()' not in app:
    fails.append("app.js handler probe khong doc file rieng tu probe-file-input")
for bad in ("Probe dinh dang", "Ho tro duoc", "Chua ho tro",
            "Buoc tiep theo", "Tai bao cao JSON", "Chua chon file",
            "Khong co thao tac nao duoc ho tro"):
    if bad in app or bad in html:
        fails.append("co chu tieng Viet khong dau: %r" % bad)

print(json.dumps({"fails": fails}, ensure_ascii=False))
sys.exit(1 if fails else 0)
