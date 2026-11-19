#!/usr/bin/env python3
"""Kiem tra tinh SDF: tai tao atlas SDF TextMeshPro (sdk/sdf.py).

- sdk/sdf.py: chi stdlib + Python 3.9 (khong match/X|Y/eval/exec);
  co parse_font, glyph_segments, render_sdf_cell, pack_cells,
  generate_tmp_atlas, encode_glyph_table_old/new, rebuild_tmp_object,
  measure_atlas_error; SDF_CENTER=126.5, SPREAD_EXTRA=1 (spread =
  padding+1, do tu atlas that).
- sdk/tmp_sdf.py: chi stdlib; co regen_tmp_asset, find_source_font,
  find_atlas_texture, parse_texture2d (2 bo cuc Unity 2022/6000),
  extract_font; fail-closed khi thieu font/atlas/khong xep vua.
- sdk/tmp_font.py: co find_face_info_new (FaceInfo moi: 2 chuoi +
  pointSize INT), STRIDE_NEW=52, read_glyph_records, is_unity_entry.
- sdk/gate.py: co check_tmp_sdf + CHECK_TMP_SDF (khong nam trong
  CHECK_IDS/run_gate — check rieng cho luong tmp_sdf_regen).
- Manifest: files.json 109 (co pb3/sdk/sdf.py, pb3/sdk/tmp_sdf.py),
  tests.json 192 (co pb3/tests/test_sdf_regen.py).
- sw.js precache co 2 file SDK moi + CACHE_VERSION v55-20261118.
- bridge.py: co op tmp_sdf_regen (dry-run, tra dest .raw + MAE).
- UI: index.html co btn-font-tmp-sdf + inp-tmp-sdf-name +
  inp-tmp-sdf-extra; app.js co handler tmp_sdf_regen hien MAE + ĐẠT.
- Khong con claim "SDF khong tai tao duoc" trong font.py/bridge.py/
  app.js/index.html (da thay bang tinh nang thu nghiem tmp_sdf_regen).
In JSON {"fails": [...]}.
"""
import ast
import json
import sys
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent  # P0-1: goc repo (Q2)
fails = []

STDLIB = {"__future__", "copy", "hashlib", "json", "math", "os", "re",
          "struct", "typing", "zipfile"}


def _check_stdlib(path, label, allowed_top):
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] not in allowed_top:
                    fails.append("%s import khong phai stdlib: %s"
                                 % (label, a.name))
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root not in allowed_top and root not in ("core", "sdk"):
                fails.append("%s import-from la: %s" % (label, node.module))
        elif isinstance(node, ast.Match):
            fails.append("%s dung match (Python 3.10+)" % label)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in ("eval", "exec"):
            fails.append("%s dung %s" % (label, node.func.id))
    # X|Y chi cam trong ANNOTATION (hop kieu 3.10+); "|" trong bieu thuc
    # so hoc (v<<8|byte) hay hop tap (set|set) la hop le o Python 3.9.
    for node in ast.walk(tree):
        anns = []
        if isinstance(node, ast.AnnAssign):
            anns.append(node.annotation)
        elif isinstance(node, ast.arg):
            if node.annotation is not None:
                anns.append(node.annotation)
        elif isinstance(node, ast.FunctionDef):
            if node.returns is not None:
                anns.append(node.returns)
        for ann in anns:
            for sub in ast.walk(ann):
                if isinstance(sub, ast.BinOp) \
                        and isinstance(sub.op, ast.BitOr):
                    fails.append("%s dung X|Y trong annotation "
                                 "(Python 3.10+)" % label)
                    break
    try:
        compile(src, path.name, "exec", flags=0, dont_inherit=True)
    except SyntaxError as e:
        fails.append("%s loi cu phap: %s" % (label, e))
    return src


# 1. sdk/sdf.py.
sdf = _check_stdlib(SITE / "pb3" / "sdk" / "sdf.py", "sdf.py", STDLIB)
for s in ("def parse_font(", "def glyph_segments(",
          "def render_sdf_cell(", "def pack_cells(",
          "def generate_tmp_atlas(", "def encode_glyph_table_old(",
          "def encode_glyph_table_new(", "def rebuild_tmp_object(",
          "def measure_atlas_error(", "def is_sdf_atlas(",
          "SDF_CENTER = 126.5",
          "SPREAD_EXTRA = 1", "SDF_MIN_MID_FRACTION = 0.15"):
    if s not in sdf:
        fails.append("sdf.py thieu %r" % s)
for bad in ("urllib", "requests", "socket", "http.client"):
    if bad in sdf:
        fails.append("sdf.py chua %r (khong duoc goi mang)" % bad)
# Thu vien ngoai chi cam o IMPORT (da kiem o _check_stdlib); nhac ten
# trong docstring de noi "khong dung" la duoc (vd "Khong import ...").
for bad in ("numpy", "fontTools", "PIL"):
    tree_sdf = ast.parse(sdf)
    for node in ast.walk(tree_sdf):
        if isinstance(node, ast.Import):
            for a in node.names:
                if bad in a.name:
                    fails.append("sdf.py import %r (chi stdlib)" % bad)
        elif isinstance(node, ast.ImportFrom):
            if bad in (node.module or ""):
                fails.append("sdf.py import-from %r (chi stdlib)" % bad)

# 2. sdk/tmp_sdf.py.
tmpsdf = _check_stdlib(SITE / "pb3" / "sdk" / "tmp_sdf.py", "tmp_sdf.py",
                       STDLIB)
for s in ("def regen_tmp_asset(", "def find_source_font(",
          "def find_atlas_texture(", "def parse_texture2d(",
          "def extract_font(", "ress_slice", "is_sdf_atlas(",
          "atlas la bitmap"):
    if s not in tmpsdf:
        fails.append("tmp_sdf.py thieu %r" % s)

# 3. sdk/tmp_font.py: FaceInfo moi + bang glyph 52B.
tf = (SITE / "pb3" / "sdk" / "tmp_font.py").read_text(encoding="utf-8")
for s in ("def find_face_info_new(", "STRIDE_NEW = 52",
          "def read_glyph_records(", "def is_unity_entry(",
          "def find_glyph_table("):
    if s not in tf:
        fails.append("tmp_font.py thieu %r" % s)

# 4. sdk/gate.py: check_tmp_sdf (rieng, khong trong CHECK_IDS).
gate = (SITE / "pb3" / "sdk" / "gate.py").read_text(encoding="utf-8")
for s in ("def check_tmp_sdf(", 'CHECK_TMP_SDF = "tmp_sdf"'):
    if s not in gate:
        fails.append("gate.py thieu %r" % s)

# 5. Manifest.
files = json.loads((SITE / "pb3" / "files.json").read_text())["files"]
if len(files) != 109:
    fails.append("files.json co %d file, ky vong 109" % len(files))
for f in ("pb3/sdk/sdf.py", "pb3/sdk/tmp_sdf.py"):
    if f not in files:
        fails.append("files.json thieu %s" % f)
tests = json.loads((SITE / "pb3" / "tests.json").read_text())["files"]
if len(tests) != 192:
    fails.append("tests.json co %d file, ky vong 192" % len(tests))
if "pb3/tests/test_sdf_regen.py" not in tests:
    fails.append("tests.json thieu pb3/tests/test_sdf_regen.py")

# 6. sw.js precache + version.
sw = (SITE / "sw.js").read_text(encoding="utf-8")
for f in ('"pb3/sdk/sdf.py"', '"pb3/sdk/tmp_sdf.py"'):
    if f not in sw:
        fails.append("sw.js precache thieu %s" % f)
if 'CACHE_VERSION = "v55-20261118"' not in sw:
    fails.append("sw.js CACHE_VERSION khong phai v55-20261118")

# 7. bridge co op tmp_sdf_regen (dry-run).
bridge = (SITE / "pb3" / "bridge.py").read_text(encoding="utf-8")
for s in ("def tmp_sdf_regen(", "def _iter_tmp_sf(",
          "def _parse_extra_codepoints(", "tmp_sdf_"):
    if s not in bridge:
        fails.append("bridge.py thieu %r" % s)

# 8. UI: nut + 2 o nhap + handler hien MAE.
html = (SITE / "index.html").read_text(encoding="utf-8")
for i in ("btn-font-tmp-sdf", "inp-tmp-sdf-name", "inp-tmp-sdf-extra"):
    if ('id="%s"' % i) not in html:
        fails.append("index.html thieu id %s" % i)
app = (SITE / "app.js").read_text(encoding="utf-8")
for s in ('"tmp_sdf_regen"', "btn-font-tmp-sdf", "inp-tmp-sdf-name",
          "inp-tmp-sdf-extra", "median_mae", "Tái tạo atlas SDF"):
    if s not in app:
        fails.append("app.js thieu %r" % s)

# 9. Khong con claim cu "SDF khong tai tao duoc".
for label, text in (("font.py", (SITE / "pb3" / "sdk" / "font.py").read_text(encoding="utf-8")),
                    ("bridge.py", bridge), ("app.js", app), ("index.html", html)):
    for bad in ("KHÔNG tái tạo được", "không tái tạo được",
                "chỉ tái tạo được trong Unity Editor",
                "atlas SDF chỉ tái tạo"):
        if bad in text:
            fails.append("%s con claim cu %r" % (label, bad))

print(json.dumps({"fails": fails}, ensure_ascii=False))
sys.exit(1 if fails else 0)
