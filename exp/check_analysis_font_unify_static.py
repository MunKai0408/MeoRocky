#!/usr/bin/env python3
"""Kiem tra tinh phan (D): liet ke font + gom 1 font (sdk/font_list.py).

- sdk/font_list.py: chi stdlib + core/sdk; co scan_source, build_unify_plan,
  merge_standalone_ttfs; SCHEMA_PLAN font_unify_plan/1; DONOR du 142/142;
  LIMITS noi ro atlas SDF chi sinh duoc trong Unity Editor.
- bridge.py: co op font_list + font_unify + cache _LAST_FONT_SCAN.
- index.html + app.js: nut btn-font-list + btn-font-unify, handler goi
  dung op, hien tmp_candidates + font_unified.zip.
- Manifest: files.json 101 (co pb3/sdk/font_list.py), tests.json 175
  (co pb3/tests/test_font_unify.py); sw.js CACHE_VERSION v39-20261117
