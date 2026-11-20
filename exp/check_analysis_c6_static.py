#!/usr/bin/env python3
"""Kiem tra tinh cho muc C6 (dung tu exp/verify_analysis_c6.mjs).

- sdk/tmp_font.py: co build_fallback_plan + FALLBACK_SCHEMA; khong them
  import ngoai stdlib/sdk; khong dung msdf-atlas-gen.
- site/unity_editor/TMP_VietnameseFallback.cs: mang codepoint khop dung
  bo 134 + 8 cua Python; co dang ky fallback TMP_Settings
  (fallbackFontAssets) + bang fallback rieng (fallbackFontAssetTable);
  tao TMP_FontAsset moi (CreateFontAsset + TryAddCharacters); KHONG gan
  lai atlasTexture/glyphTable/material cua asset goc; co ghi "chay trong
  Unity Editor" + "chua bien dich"; dau ngoac can bang.
- site/pb3/bridge.py: co op tmp_font_fallback + cache _LAST_TMP_SCAN.
- site/index.html + site/app.js: nut btn-font-fallback + handler goi
  tmp_font_fallback, hien link tai script C#.
- Manifest: tests.json co pb3/tests/test_tmp_fallback.py (files.json
  khong doi o thoi C6 vi khong them module Python moi).
In JSON ra stdout, exit 0 neu dat het.
"""
import ast
import json
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
SITE = ROOT  # P0-1: code o goc repo, khong co thu muc site/ (giong Q2)
PB3 = os.path.join(SITE, "pb3")
FAILS = []


def chk(cond, msg):
    if not cond:
        FAILS.append(msg)


# ------------------------------------------------------------------ Python
TMP_PY = os.path.join(PB3, "sdk", "tmp_font.py")
src = open(TMP_PY, encoding="utf-8").read()
tree = ast.parse(src)
imports = set()
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        imports.update(a.name.split(".")[0] for a in node.names)
    elif isinstance(node, ast.ImportFrom):
        if node.module:
            imports.add(node.module.split(".")[0])
stdlib = set(sys.stdlib_module_names)
third = {m for m in imports if m not in stdlib and m not in ("core", "sdk")}
chk(not third, "tmp_font.py import ngoai stdlib/sdk: %s" % sorted(third))
chk("def build_fallback_plan" in src, "tmp_font.py thieu build_fallback_plan")
chk('FALLBACK_SCHEMA = "tmp_fallback_plan/1"' in src,
    "tmp_font.py thieu FALLBACK_SCHEMA tmp_fallback_plan/1")
# "msdf-atlas-gen" chi duoc nhac trong ghi chu (string/docstring), khong
# duoc dung trong code: bo string literal + comment roi moi kiem.
_code = re.sub(r'"(?:\\.|[^"\\])*"', '""', src)
_code = re.sub(r"#[^\n]*", "", _code)
chk("msdf-atlas-gen" not in _code, "tmp_font.py dung msdf-atlas-gen")

# ------------------------------------------------------------------ C#
CS = os.path.join(SITE, "unity_editor", "TMP_VietnameseFallback.cs")
cs = open(CS, encoding="utf-8").read()
sys.path.insert(0, PB3)
from sdk import tmp_font as tf  # noqa: E402

want_letters = set(tf.vietnamese_letters_134())
want_marks = set(tf.combining_marks_8())


def _array(name):
    m = re.search(re.escape(name) + r"\s*=\s*new uint\[\]\s*\{(.*?)\};",
                  cs, re.S)
    if not m:
        return None
    return {int(x, 16) for x in re.findall(r"0x([0-9A-Fa-f]+)", m.group(1))}


got_letters = _array("VietnameseLetters")
got_marks = _array("VietnameseMarks")
chk(got_letters is not None, "C#: khong tim thay mang VietnameseLetters")
chk(got_marks is not None, "C#: khong tim thay mang VietnameseMarks")
if got_letters is not None:
    chk(got_letters == want_letters,
        "C#: VietnameseLetters lech Python: thua %s, thieu %s"
        % (sorted(got_letters - want_letters)[:5],
           sorted(want_letters - got_letters)[:5]))
    chk(len(got_letters) == 134, "C#: VietnameseLetters dai %d != 134"
        % len(got_letters))
if got_marks is not None:
    chk(got_marks == want_marks,
        "C#: VietnameseMarks lech Python: %r" % (got_marks ^ want_marks))
    chk(len(got_marks) == 8, "C#: VietnameseMarks dai %d != 8"
        % len(got_marks))
for need in ("UNITY_EDITOR", "TMP_Settings", "fallbackFontAssets",
             "fallbackFontAssetTable", "CreateFontAsset",
             "TryAddCharacters", "TMP_FontAsset", "không chạy trên web",
             "Assets/Editor", "Chưa biên dịch",
             "creationSettings.sourceFontFile",
             "4. Tạo font fallback tiếng Việt (từ file tmp_fallback_plan.json)"):
    chk(need in cs, "C#: thieu %r" % need)
chk("chưa biên dịch" in cs.lower(), "C#: thieu ghi chu 'chua bien dich'")
# KHONG sua asset goc: khong gan lai atlasTexture/glyphTable/characterTable/
# faceInfo/material cua baseAsset. Chi duoc DOC (baseAsset.atlasWidth...).
for bad in ("baseAsset.atlasTexture =", "baseAsset.glyphTable =",
            "baseAsset.characterTable =", "baseAsset.faceInfo =",
            "baseAsset.material =", "baseAsset.material.mainTexture =",
            "fontAsset.atlasTexture =", "fontAsset.glyphTable ="):
    chk(bad not in cs, "C#: SUA asset goc: chua %r" % bad)
for banned in ("msdf-atlas-gen", "đã biên dịch kiểm chứng"):
    chk(banned not in cs, "C#: chua %r" % banned)
stripped = re.sub(r"//[^\n]*", "", cs)
stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.S)
stripped = re.sub(r'"(?:\\.|[^"\\])*"', '""', stripped)
chk(stripped.count("{") == stripped.count("}"),
    "C#: dau ngoac lech { %d } %d"
    % (stripped.count("{"), stripped.count("}")))

# ------------------------------------------------------------------ bridge + UI
BR = open(os.path.join(PB3, "bridge.py"), encoding="utf-8").read()
chk("def tmp_font_fallback" in BR, "bridge.py thieu op tmp_font_fallback")
chk("_LAST_TMP_SCAN" in BR, "bridge.py thieu cache _LAST_TMP_SCAN")
chk("tmp_fallback_plan.json" in BR, "bridge.py khong ghi tmp_fallback_plan.json")
HTML = open(os.path.join(SITE, "index.html"), encoding="utf-8").read()
chk('id="btn-font-fallback"' in HTML, "index.html thieu nut btn-font-fallback")
chk("Tạo font fallback" in HTML, "index.html thieu nhan nut fallback")
JS = open(os.path.join(SITE, "app.js"), encoding="utf-8").read()
chk('btn-font-fallback' in JS, "app.js thieu handler btn-font-fallback")
chk('"tmp_font_fallback"' in JS, "app.js khong goi op tmp_font_fallback")
chk("unity_editor/TMP_VietnameseFallback.cs" in JS,
    "app.js khong link script C# fallback")
chk("tmp_fallback_plan.json" in JS, "app.js khong nhac tmp_fallback_plan.json")
for bad in ("new SharedArrayBuffer", "Cross-Origin-Opener-Policy",
            "Cross-Origin-Embedder-Policy"):
    chk(bad not in JS, "app.js chua %r" % bad)

# ------------------------------------------------------------------ manifest
files_json = json.load(open(os.path.join(PB3, "files.json"), encoding="utf-8"))
chk(len(files_json["files"]) == 112,
    "files.json %d file != 112 (P0-2 them sdk/gate.py)"
    % len(files_json["files"]))
tests_json = json.load(open(os.path.join(PB3, "tests.json"), encoding="utf-8"))
chk("pb3/tests/test_tmp_fallback.py" in tests_json["files"],
    "tests.json thieu pb3/tests/test_tmp_fallback.py")
chk(len(tests_json["files"]) == 201,
    "tests.json %d file != 200" % len(tests_json["files"]))

print(json.dumps({"fails": FAILS}, ensure_ascii=False, indent=1))
sys.exit(1 if FAILS else 0)
