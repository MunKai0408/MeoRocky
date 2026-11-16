"""Test khoa cho ke hoach font fallback TMP (muc C6, sdk/tmp_font.py).

Chi dung item gia lap (dict giong ket qua scan_source) — khong can file
game that, khong can mang. Kiem tra: schema, hop nhat chu thieu, ten
asset fallback, tham so atlas goc duoc giu (PointSize/Padding/atlas),
atlas fallback kep trong [256, 1024], asset du thi vao already_full,
asset khong doc duoc bang glyph thi vao unplanned (khong bia danh sach
ky tu), fallback chi cuu chu Viet (ascii_note khi thieu ca chu Latin),
material_unchanged=True. Kiem chung tren fixture that (tmp_all.ipa) nam
o exp/check_c6_fallback_plan.py + harness trinh duyet.
"""
from __future__ import annotations

import unittest

from sdk import tmp_font as tf


def _item(name="SourceCodePro SDF", status="parsed", face="Source Code Pro",
          pt=36.0, pad=6.0, aw=1024, ah=1024, miss_letters=(), miss_marks=(),
          ascii_missing=(), declared=325, count=325, material="SourceCodePro Material"):
    return {
        "status": status, "name": name, "source": "r.assets", "path_id": 11,
        "font_info": {
            "name": face, "point_size": pt, "scale": 1.0, "line_height": 48,
            "padding": pad, "atlas_width": aw, "atlas_height": ah,
            "atlas_basis": "faceinfo_layout", "declared_char_count": declared,
        },
        "glyph_table": {"count": count},
        "vietnamese": {
            "covered": 134 - len(miss_letters), "required": 134,
            "missing_codepoints": list(miss_letters),
            "marks": {"missing_codepoints": list(miss_marks)},
            "ascii_missing": ["U+%04X" % c for c in ascii_missing],
        },
        "refs": {"material": ([{"path_id": 30, "name": material}]
                               if material else []),
                 "texture": [], "font": []},
    }


class FallbackPlanTest(unittest.TestCase):
    def test_schema_and_missing_union(self):
        a = _item(miss_marks=[0x300, 0x301])
        b = _item(name="CJK SDF", face="CJK", pt=90.0, pad=5.0, aw=4096,
                  ah=4096, miss_letters=list(tf.vietnamese_letters_134()),
                  miss_marks=list(tf.combining_marks_8()), declared=3095,
                  count=3095, material="CJK Material")
        plan = tf.build_fallback_plan([a, b])
        self.assertEqual(plan["schema"], "tmp_fallback_plan/1")
        self.assertEqual(plan["missing_total"], 142)
        self.assertEqual(plan["fallbacks_needed"], 2)
        self.assertEqual(len(plan["chars_text"]), 142)
        self.assertEqual(plan["editor_script"],
                         "unity_editor/TMP_VietnameseFallback.cs")
        fb_a = plan["fallbacks"][0]
        self.assertEqual(fb_a["missing_total"], 2)
        self.assertEqual(fb_a["fallback_name"],
                         "Source Code Pro Vietnamese Fallback")
        self.assertEqual(fb_a["base_point_size"], 36)
        self.assertEqual(fb_a["base_padding"], 6.0)
        self.assertEqual((fb_a["base_atlas_width"],
                          fb_a["base_atlas_height"]), (1024, 1024))
        self.assertEqual((fb_a["fallback_atlas_width"],
                          fb_a["fallback_atlas_height"]), (1024, 1024))
        self.assertTrue(fb_a["material_unchanged"])
        self.assertEqual([r["name"] for r in fb_a["material_refs"]],
                         ["SourceCodePro Material"])
        self.assertIsNone(fb_a["ascii_note"])
        self.assertEqual(fb_a["base_render_mode"], None)
        fb_b = plan["fallbacks"][1]
        # Atlas goc 4096 -> fallback kep ve 1024 (chi chua <=142 ky tu).
        self.assertEqual((fb_b["fallback_atlas_width"],
                          fb_b["fallback_atlas_height"]), (1024, 1024))
        self.assertEqual(fb_b["base_point_size"], 90)
        self.assertEqual(len(fb_b["editor_steps"]), 5)

    def test_atlas_size_clamp_and_default(self):
        self.assertEqual(tf._fallback_atlas_size(512, 256), (512, 256))
        self.assertEqual(tf._fallback_atlas_size(4096, 4096), (1024, 1024))
        self.assertEqual(tf._fallback_atlas_size(128, 128), (256, 256))
        self.assertEqual(tf._fallback_atlas_size(None, None), (512, 512))

    def test_already_full_needs_no_fallback(self):
        plan = tf.build_fallback_plan([_item()])
        self.assertEqual(plan["fallbacks_needed"], 0)
        self.assertEqual(plan["already_full_count"], 1)
        self.assertEqual(plan["missing_total"], 0)
        self.assertEqual(plan["chars_text"], "")

    def test_unreadable_asset_is_unplanned_not_invented(self):
        bad = _item(name="NoGlyphs SDF", status="glyph_table_not_found")
        bad["vietnamese"] = None
        bad["reason"] = "Không thấy bảng glyph 36 byte/phần tử hợp lệ."
        plan = tf.build_fallback_plan([bad])
        self.assertEqual(plan["fallbacks_needed"], 0)
        self.assertEqual(plan["unplanned_count"], 1)
        self.assertIn("Không thấy bảng glyph",
                      plan["unplanned"][0]["reason"])
        self.assertNotIn("missing_codepoints",
                         plan["unplanned"][0])

    def test_ascii_missing_is_flagged_not_rescued(self):
        it = _item(miss_letters=[0x1EF9], ascii_missing=[65, 66])
        plan = tf.build_fallback_plan([it])
        fb = plan["fallbacks"][0]
        self.assertEqual(fb["ascii_missing"], ["U+0041", "U+0042"])
        self.assertIsNotNone(fb["ascii_note"])
        self.assertIn("KHÔNG cứu được", fb["ascii_note"])

    def test_glyph_plan_reused_not_recomputed(self):
        gp = tf.build_glyph_plan([_item(miss_marks=[0x300])])
        plan = tf.build_fallback_plan([_item(miss_marks=[0x300])], gp)
        self.assertEqual(plan["missing_total"], 1)
        self.assertEqual(plan["chars_text"], gp["chars_text"])


if __name__ == "__main__":
    unittest.main()
