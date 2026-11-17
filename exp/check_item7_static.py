#!/usr/bin/env python3
"""Kiem tra tinh cho muc 7 (dung tu exp/verify_item7.mjs).

- sdk/tmp_font.py: chi stdlib, tuong thich Python 3.9 (khong `match`,
  khong `X | Y` trong annotation), khong COI/SAB.
- site/unity_editor/TMP_VietnameseAtlas.cs: mang codepoint khop dung bo
  134 + 8 cua Python; co du API TMP can thiet; KHONG them fallback
  (C7, lam sau); KHONG dung msdf-atlas-gen; dau ngoac can bang; co ghi
  "chay trong Unity Editor".
- Manifest: files.json / tests.json / sw.js PRECACHE_URLS co file moi;
  CACHE_VERSION da tang len v10 (muc C4 them opfs.js vao precache,
  2026-11-16; theo dung luat trong sw.js: doi precache thi phai doi
  version).
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
chk(not any(isinstance(n, ast.Match) for n in ast.walk(tree)),
    "tmp_font.py dung `match` (Python 3.10+, khong tuong thich 3.9)")
for node in ast.walk(tree):
    if isinstance(node, (ast.AnnAssign, ast.arg)):
        ann = getattr(node, "annotation", None)
        if ann is not None and any(
                isinstance(n, ast.BinOp) and isinstance(n.op, ast.BitOr)
                for n in ast.walk(ann)):
            chk(False, "tmp_font.py dung `X | Y` trong annotation "
                       "(khong tuong thich 3.9)")
            break
for bad in ("new SharedArrayBuffer", "SharedArrayBuffer(",
            "Cross-Origin-Opener-Policy", "Cross-Origin-Embedder-Policy"):
    chk(bad not in src, "tmp_font.py chua %r" % bad)
# Muc 7 khong import msdf-atlas-gen (chi duoc nhac trong ghi chu "khong dung").
chk("msdf-atlas-gen" not in src.replace("không dùng msdf-atlas-gen", "")
    .replace("khong dung msdf-atlas-gen", ""),
    "tmp_font.py dung msdf-atlas-gen")

# ------------------------------------------------------------------ C#
CS = os.path.join(SITE, "unity_editor", "TMP_VietnameseAtlas.cs")
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
for need in ("UNITY_EDITOR", "TryAddCharacters", "CreateFontAsset",
             "material.mainTexture", "TMP_FontAsset", "FaceInfo",
             "không chạy trên web", "Assets/Editor", "giữ nguyên material"):
    chk(need in cs, "C#: thieu %r" % need)
for banned in ("fallbackFontAssetTable.Add", "msdf-atlas-gen"):
    chk(banned not in cs, "C#: chua %r (thuoc muc C7 hoac bi cam)" % banned)
stripped = re.sub(r"//[^\n]*", "", cs)
stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.S)
stripped = re.sub(r'"(?:\\.|[^"\\])*"', '""', stripped)
chk(stripped.count("{") == stripped.count("}"),
    "C#: dau ngoac lech { %d } %d"
    % (stripped.count("{"), stripped.count("}")))

# ------------------------------------------------------------------ manifest
files_json = json.load(open(os.path.join(PB3, "files.json"), encoding="utf-8"))
chk("pb3/sdk/tmp_font.py" in files_json["files"],
    "files.json thieu pb3/sdk/tmp_font.py")
tests_json = json.load(open(os.path.join(PB3, "tests.json"), encoding="utf-8"))
chk("pb3/tests/test_tmp_font.py" in tests_json["files"],
    "tests.json thieu pb3/tests/test_tmp_font.py")
sw = open(os.path.join(SITE, "sw.js"), encoding="utf-8").read()
m = re.search(r'const CACHE_VERSION = "([^"]+)"', sw)
chk(m is not None and m.group(1) == "v32-20261117",
    "sw.js CACHE_VERSION %r != v32-20261117" % (m.group(1) if m else None))
listed = re.findall(r'"([^"]+)"', re.search(
    r"const PRECACHE_URLS = \[(.*?)\];", sw, re.S).group(1))
chk("pb3/sdk/tmp_font.py" in listed, "sw.js precache thieu pb3/sdk/tmp_font.py")
chk(set(listed) == set(files_json["files"]) | {
    "./", "index.html", "app.js", "worker.js", "worker-pool.js", "stream.js",
    "opfs.js",
    "kb-store.js", "gt.js", "manifest.webmanifest", "diagnostics.html",
    "icons/icon-192.png", "icons/icon-512.png", "icons/apple-touch-icon.png",
    "fonts/BeVietnamPro-Regular.ttf.b64",
    "fixtures/selftest.zip",
    "fzstd/index.mjs",
    "pb3/files.json",
} | {"pyodide/" + f for f in os.listdir(os.path.join(SITE, "pyodide"))},
    "sw.js precache lech files.json/dia")
for f in ("app.js", "sw.js"):
    s2 = open(os.path.join(SITE, f), encoding="utf-8").read()
    for bad in ("new SharedArrayBuffer", "Cross-Origin-Opener-Policy",
                "Cross-Origin-Embedder-Policy"):
        chk(bad not in s2, "%s chua %r" % (f, bad))

print(json.dumps({"fails": FAILS}, ensure_ascii=False, indent=1))
sys.exit(1 if FAILS else 0)
