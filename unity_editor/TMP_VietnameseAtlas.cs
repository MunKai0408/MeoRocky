// TMP_VietnameseAtlas.cs — Mục 7 BearTea: thêm glyph tiếng Việt vào atlas
// TextMeshPro (SDF) trong Unity Editor.
//
// ⚠️ CHẠY TRONG UNITY EDITOR — KHÔNG chạy trên web, không chạy trên iSH.
// Cách dùng: chép file này vào Assets/Editor/ trong project Unity của game,
// mở lại Editor, chọn TMP_FontAsset cần sửa trong cửa sổ Project, rồi dùng
// menu BearTea → TMP (xem hướng dẫn từng bước ở cuối file).
//
// Mức rẻ nhất (mặc định): GIỮ NGUYÊN material (shader + mọi thuộc tính SDF
// như Face Dilate, Outline...), chỉ thêm glyph vào atlas texture hiện có
// (TryAddCharacters). Chỉ khi atlas cũ ĐẦY mới tạo atlas mới — vẫn giữ
// nguyên material, chỉ đổi material.mainTexture sang atlas mới.
//
// Khác với mục C7 (làm sau): C7 tạo TMP_FontAsset MỚI chỉ chứa dấu tiếng
// Việt làm fallback, KHÔNG sửa atlas gốc. Mục 7 sửa chính atlas gốc.
//
// Chưa biên dịch kiểm chứng: không có Unity ở đây, API dùng là API công
// khai của TextMeshPro 3.x/4.x (Unity 6 kèm TMP 4.x). Thử trong Editor
// trước khi dùng cho game thật; nếu bản TMP của project khác tên hàm thì
// Editor báo lỗi rõ ở đúng bước đó (mỗi bước đều có try/catch + log).
// Ghi công: logic đọc tham số + bộ 134 chữ Việt từ BearTea (sdk/tmp_font.py)
// và IPA Localization Studio (js/modules/tmp_font.js, MIT, tác giả Joseph).
//
// Cần: package TextMeshPro (Unity 6: có sẵn trong Package Manager).
#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using System.IO;
using TMPro;
using UnityEditor;
using UnityEngine;

public static class TMP_VietnameseAtlas
{
    // 134 chữ cái tiếng Việt — KHỚP ĐÚNG bộ 134 của BearTea (sdk/font.py)
    // và của IPA Localization Studio. Sinh từ Python, đừng sửa tay.
    private static readonly uint[] VietnameseLetters = new uint[]
    {
        0x00C0, 0x00C1, 0x00C2, 0x00C3, 0x00C8, 0x00C9, 0x00CA, 0x00CC, 0x00CD, 0x00D2, 0x00D3, 0x00D4,
        0x00D5, 0x00D9, 0x00DA, 0x00DD, 0x00E0, 0x00E1, 0x00E2, 0x00E3, 0x00E8, 0x00E9, 0x00EA, 0x00EC,
        0x00ED, 0x00F2, 0x00F3, 0x00F4, 0x00F5, 0x00F9, 0x00FA, 0x00FD, 0x0102, 0x0103, 0x0110, 0x0111,
        0x0128, 0x0129, 0x0168, 0x0169, 0x01A0, 0x01A1, 0x01AF, 0x01B0, 0x1EA0, 0x1EA1, 0x1EA2, 0x1EA3,
        0x1EA4, 0x1EA5, 0x1EA6, 0x1EA7, 0x1EA8, 0x1EA9, 0x1EAA, 0x1EAB, 0x1EAC, 0x1EAD, 0x1EAE, 0x1EAF,
        0x1EB0, 0x1EB1, 0x1EB2, 0x1EB3, 0x1EB4, 0x1EB5, 0x1EB6, 0x1EB7, 0x1EB8, 0x1EB9, 0x1EBA, 0x1EBB,
        0x1EBC, 0x1EBD, 0x1EBE, 0x1EBF, 0x1EC0, 0x1EC1, 0x1EC2, 0x1EC3, 0x1EC4, 0x1EC5, 0x1EC6, 0x1EC7,
        0x1EC8, 0x1EC9, 0x1ECA, 0x1ECB, 0x1ECC, 0x1ECD, 0x1ECE, 0x1ECF, 0x1ED0, 0x1ED1, 0x1ED2, 0x1ED3,
        0x1ED4, 0x1ED5, 0x1ED6, 0x1ED7, 0x1ED8, 0x1ED9, 0x1EDA, 0x1EDB, 0x1EDC, 0x1EDD, 0x1EDE, 0x1EDF,
        0x1EE0, 0x1EE1, 0x1EE2, 0x1EE3, 0x1EE4, 0x1EE5, 0x1EE6, 0x1EE7, 0x1EE8, 0x1EE9, 0x1EEA, 0x1EEB,
        0x1EEC, 0x1EED, 0x1EEE, 0x1EEF, 0x1EF0, 0x1EF1, 0x1EF2, 0x1EF3, 0x1EF4, 0x1EF5, 0x1EF6, 0x1EF7,
        0x1EF8, 0x1EF9,
    };

    // 8 dấu kết hợp tiếng Việt (huyền/sắc/mũ/ngã/breve/móc/dấu sừng/chấm dưới).
    private static readonly uint[] VietnameseMarks = new uint[]
    {
        0x0300, 0x0301, 0x0302, 0x0303, 0x0306, 0x0309, 0x031B, 0x0323,
    };

    private const int MaxAtlasSize = 4096;

    // Kế hoạch glyph do web BearTea xuất (tmp_glyph_plan.json).
    // JsonUtility bỏ qua trường thừa nên file plan đầy đủ vẫn đọc được.
    [Serializable]
    private class GlyphPlan
    {
        public string chars_text;
        public int[] missing_letters_codepoints;
        public int[] missing_marks_codepoints;
    }

    // ------------------------------------------------------------ menu
    [MenuItem("BearTea/TMP/1. Đọc tham số Font Asset đang chọn")]
    public static void DiagnoseSelected()
    {
        TMP_FontAsset fontAsset = GetSelectedFontAsset();
        if (fontAsset == null) return;
        LogFontAsset(fontAsset, "THAM SỐ HIỆN TẠI");
        LogCoverage(fontAsset, "ĐỘ PHỦ HIỆN TẠI");
    }

    [MenuItem("BearTea/TMP/2. Thêm glyph tiếng Việt (giữ nguyên material)")]
    public static void AddVietnameseKeepMaterial()
    {
        TMP_FontAsset fontAsset = GetSelectedFontAsset();
        if (fontAsset == null) return;
        AddVietnamese(fontAsset, AllVietnameseCodepoints(), "134 chữ + 8 dấu");
    }

    [MenuItem("BearTea/TMP/3. Thêm glyph từ file tmp_glyph_plan.json (web BearTea)")]
    public static void AddVietnameseFromPlan()
    {
        TMP_FontAsset fontAsset = GetSelectedFontAsset();
        if (fontAsset == null) return;
        string path = EditorUtility.OpenFilePanel(
            "Chọn file tmp_glyph_plan.json do web BearTea xuất",
            "", "json");
        if (string.IsNullOrEmpty(path)) { Debug.Log("[BearTea] Đã hủy."); return; }
        try
        {
            GlyphPlan plan = JsonUtility.FromJson<GlyphPlan>(File.ReadAllText(path));
            List<uint> cps = new List<uint>();
            if (plan.missing_letters_codepoints != null)
                foreach (int cp in plan.missing_letters_codepoints) cps.Add((uint)cp);
            if (plan.missing_marks_codepoints != null)
                foreach (int cp in plan.missing_marks_codepoints) cps.Add((uint)cp);
            if (cps.Count == 0 && !string.IsNullOrEmpty(plan.chars_text))
                foreach (char ch in plan.chars_text) cps.Add(ch);
            if (cps.Count == 0)
            {
                Debug.LogWarning("[BearTea] File plan không có ký tự thiếu nào — atlas đã đủ, không cần làm gì.");
                return;
            }
            AddVietnamese(fontAsset, cps.ToArray(),
                cps.Count + " ký tự thiếu theo file plan");
        }
        catch (Exception e)
        {
            Debug.LogError("[BearTea] Không đọc được file plan: " + e.Message);
            EditorUtility.DisplayDialog("BearTea — TMP",
                "Không đọc được file tmp_glyph_plan.json:\n" + e.Message, "OK");
        }
    }

    // ------------------------------------------------------------ lõi
    private static uint[] AllVietnameseCodepoints()
    {
        List<uint> all = new List<uint>(VietnameseLetters);
        all.AddRange(VietnameseMarks);
        return all.ToArray();
    }

    private static void AddVietnamese(TMP_FontAsset fontAsset, uint[] codepoints, string label)
    {
        LogFontAsset(fontAsset, "TRƯỚC KHI THÊM");
        LogCoverage(fontAsset, "ĐỘ PHỦ TRƯỚC KHI THÊM");
        try
        {
            uint[] missing;
            bool ok = fontAsset.TryAddCharacters(codepoints, out missing);
            if (missing == null) missing = new uint[0];
            if (missing.Length == 0)
            {
                Save(fontAsset);
                Debug.Log("[BearTea] ĐÃ THÊM " + label + " vào atlas hiện có — material giữ nguyên, không đổi gì thêm.");
                LogCoverage(fontAsset, "ĐỘ PHỦ SAU KHI THÊM");
                EditorUtility.DisplayDialog("BearTea — TMP",
                    "Đã thêm " + label + " vào atlas \"" + fontAsset.name + "\".\nMaterial giữ nguyên.", "OK");
                return;
            }
            Debug.LogWarning("[BearTea] Atlas hiện có ĐẦY: còn thiếu " + missing.Length
                + " ký tự (" + JoinHex(missing, 12) + ") — chuyển sang tạo atlas mới CÙNG tham số gốc.");
            RebuildAtlasKeepMaterial(fontAsset, codepoints);
        }
        catch (Exception e)
        {
            Debug.LogError("[BearTea] TryAddCharacters lỗi (" + e.GetType().Name + "): " + e.Message
                + "\n— Có thể bản TextMeshPro của project không có hàm này. "
                + "Cách làm tay: mở TMP Font Asset Creator, chọn font nguồn, "
                + "Character Set = Custom Characters, dán chuỗi ký tự thiếu, "
                + "giữ nguyên Point Size / Padding / Atlas Width x Height / Render Mode rồi Generate.");
            EditorUtility.DisplayDialog("BearTea — TMP",
                "Không thêm được glyph bằng TryAddCharacters:\n" + e.Message
                + "\n\nXem Console để biết cách làm tay bằng Font Asset Creator.", "OK");
        }
    }

    // Atlas cũ đầy: tạo asset mới CÙNG tham số gốc (padding, render mode,
    // point size) nhưng atlas lớn hơn, rồi copy atlas + bảng glyph về asset
    // GỐC (mọi tham chiếu trong game giữ nguyên), chỉ đổi material.mainTexture.
    private static void RebuildAtlasKeepMaterial(TMP_FontAsset fontAsset, uint[] codepoints)
    {
        Font sourceFont = fontAsset.creationSettings.sourceFontFile;
        if (sourceFont == null)
        {
            Debug.LogError("[BearTea] Không tạo atlas mới được: font asset gốc không còn "
                + "creationSettings.sourceFontFile (font nguồn đã mất). "
                + "Cách làm tay: mở TMP Font Asset Creator, chọn lại font nguồn, "
                + "dán chuỗi ký tự thiếu, Generate atlas mới rồi thay material.mainTexture.");
            EditorUtility.DisplayDialog("BearTea — TMP",
                "Font asset gốc không còn font nguồn (creationSettings.sourceFontFile trống).\n"
                + "Hãy tạo atlas mới bằng TMP Font Asset Creator rồi thay material.mainTexture tay.", "OK");
            return;
        }
        int width = Mathf.Min(Mathf.Max(fontAsset.atlasWidth, 512) * 2, MaxAtlasSize);
        int height = Mathf.Min(Mathf.Max(fontAsset.atlasHeight, 512) * 2, MaxAtlasSize);
        TMP_FontAsset rebuilt = null;
        try
        {
            rebuilt = TMP_FontAsset.CreateFontAsset(
                sourceFont,
                fontAsset.faceInfo.pointSize > 0 ? Mathf.RoundToInt(fontAsset.faceInfo.pointSize) : 90,
                fontAsset.atlasPadding,
                fontAsset.atlasRenderMode,
                width, height,
                AtlasPopulationMode.Static,
                false);
            if (rebuilt == null) throw new Exception("CreateFontAsset trả về null.");
            uint[] missing;
            rebuilt.TryAddCharacters(codepoints, out missing);
            if (missing != null && missing.Length > 0)
            {
                Debug.LogWarning("[BearTea] Atlas mới " + width + "x" + height
                    + " vẫn thiếu " + missing.Length + " ký tự — thử tăng atlas lên tối đa "
                    + MaxAtlasSize + "x" + MaxAtlasSize + " hoặc tách font fallback (mục C7, làm sau).");
            }
            // Copy về asset gốc: texture + bảng glyph + face info.
            fontAsset.atlasTexture = rebuilt.atlasTexture;
            fontAsset.glyphTable = rebuilt.glyphTable;
            fontAsset.characterTable = rebuilt.characterTable;
            fontAsset.faceInfo = rebuilt.faceInfo;
            fontAsset.atlasWidth = rebuilt.atlasWidth;
            fontAsset.atlasHeight = rebuilt.atlasHeight;
            // Mức rẻ nhất: material CŨ giữ nguyên, chỉ đổi texture atlas nó trỏ tới.
            if (fontAsset.material != null)
                fontAsset.material.mainTexture = fontAsset.atlasTexture;
            Save(fontAsset);
            Debug.Log("[BearTea] ĐÃ TẠO ATLAS MỚI " + width + "x" + height
                + " — asset gốc giữ nguyên tên/tham chiếu; material cũ giữ nguyên, "
                + "chỉ đổi mainTexture sang atlas mới.");
            LogCoverage(fontAsset, "ĐỘ PHỦ SAU KHI TẠO ATLAS MỚI");
            EditorUtility.DisplayDialog("BearTea — TMP",
                "Đã tạo atlas mới " + width + "x" + height + " cho \"" + fontAsset.name + "\".\n"
                + "Material cũ giữ nguyên, chỉ đổi texture atlas.\n"
                + "Hãy thử trong game thật (Play) để kiểm tra chữ Việt.", "OK");
        }
        catch (Exception e)
        {
            Debug.LogError("[BearTea] Tạo atlas mới lỗi (" + e.GetType().Name + "): " + e.Message);
            EditorUtility.DisplayDialog("BearTea — TMP",
                "Không tạo được atlas mới:\n" + e.Message
                + "\n\nCách làm tay: dùng TMP Font Asset Creator với cùng tham số gốc.", "OK");
        }
        finally
        {
            if (rebuilt != null)
            {
                // Asset tạm chỉ để lấy atlas + bảng glyph; asset gốc đã copy xong.
                string path = AssetDatabase.GetAssetPath(rebuilt);
                if (!string.IsNullOrEmpty(path)) AssetDatabase.DeleteAsset(path);
                else UnityEngine.Object.DestroyImmediate(rebuilt, true);
            }
        }
    }

    // ------------------------------------------------------------ phụ trợ
    private static TMP_FontAsset GetSelectedFontAsset()
    {
        TMP_FontAsset fontAsset = Selection.activeObject as TMP_FontAsset;
        if (fontAsset == null)
        {
            EditorUtility.DisplayDialog("BearTea — TMP",
                "Hãy chọn 1 TMP_FontAsset trong cửa sổ Project trước (vd font SDF của game).", "OK");
            Debug.LogWarning("[BearTea] Chưa chọn TMP_FontAsset nào.");
        }
        return fontAsset;
    }

    private static void Save(TMP_FontAsset fontAsset)
    {
        EditorUtility.SetDirty(fontAsset);
        if (fontAsset.material != null) EditorUtility.SetDirty(fontAsset.material);
        AssetDatabase.SaveAssets();
    }

    private static void LogFontAsset(TMP_FontAsset fontAsset, string label)
    {
        FaceInfo fi = fontAsset.faceInfo;
        Debug.Log("[BearTea] " + label + " — \"" + fontAsset.name + "\": "
            + "font nguồn=" + fi.familyName
            + ", PointSize=" + fi.pointSize
            + ", Scale=" + fi.scale
            + ", LineHeight=" + fi.lineHeight
            + ", Padding=" + fi.padding
            + ", atlas=" + fi.atlasWidth + "x" + fi.atlasHeight
            + ", atlasPadding=" + fontAsset.atlasPadding
            + ", renderMode=" + fontAsset.atlasRenderMode
            + ", material=" + (fontAsset.material != null ? fontAsset.material.name : "(không có)")
            + ", shader=" + (fontAsset.material != null && fontAsset.material.shader != null ? fontAsset.material.shader.name : "(không có)")
            + ", glyphs=" + (fontAsset.glyphTable != null ? fontAsset.glyphTable.Count : 0)
            + ", ký tự=" + (fontAsset.characterTable != null ? fontAsset.characterTable.Count : 0));
    }

    private static void LogCoverage(TMP_FontAsset fontAsset, string label)
    {
        HashSet<uint> have = new HashSet<uint>();
        if (fontAsset.characterTable != null)
            foreach (TMP_Character ch in fontAsset.characterTable) have.Add(ch.unicode);
        int letters = 0, marks = 0;
        List<uint> missLetters = new List<uint>(), missMarks = new List<uint>();
        foreach (uint cp in VietnameseLetters)
            if (have.Contains(cp)) letters++; else missLetters.Add(cp);
        foreach (uint cp in VietnameseMarks)
            if (have.Contains(cp)) marks++; else missMarks.Add(cp);
        Debug.Log("[BearTea] " + label + " — \"" + fontAsset.name + "\": "
            + "chữ cái " + letters + "/" + VietnameseLetters.Length
            + ", dấu kết hợp " + marks + "/" + VietnameseMarks.Length
            + (missLetters.Count > 0 ? ", thiếu: " + JoinHex(missLetters, 20) : " — ĐỦ CHỮ VIỆT"));
    }

    private static string JoinHex(ICollection<uint> cps, int max)
    {
        List<string> parts = new List<string>();
        int n = 0;
        foreach (uint cp in cps)
        {
            if (n >= max) { parts.Add("..."); break; }
            parts.Add("U+" + cp.ToString("X4"));
            n++;
        }
        return string.Join(" ", parts);
    }
}
#endif

// ============================================================================
// HƯỚNG DẪN TỪNG BƯỚC (chạy trong Unity Editor, không chạy trên web/iSH)
// ============================================================================
// 1. Chép file này vào Assets/Editor/ trong project Unity của game
//    (cần package TextMeshPro; Unity 6: có sẵn).
// 2. Trên web BearTea: chọn file .assets/.unity3d hoặc IPA của game → tab Font
//    → "Đọc TMP_FontAsset (atlas SDF)" → tải file tmp_glyph_plan.json về
//    (trong đó có chuỗi ký tự thiếu + tham số atlas từng asset).
// 3. Trong Unity Editor: mở cửa sổ Project, chọn TMP_FontAsset của game
//    (thường tên có "SDF", vd "SourceCodePro SDF").
// 4. Menu BearTea → TMP → "1. Đọc tham số Font Asset đang chọn": xem Console
//    — đối chiếu PointSize/Padding/atlas/render mode với số web đã đọc.
//    Nếu số không khớp, dừng lại và báo cho Joseph (bố cục TMP bản này lạ).
// 5. Menu BearTea → TMP → "2. Thêm glyph tiếng Việt (giữ nguyên material)"
//    (hoặc "3. Thêm glyph từ file tmp_glyph_plan.json" nếu chỉ muốn thêm
//    đúng chữ thiếu theo file plan).
//    - Atlas còn chỗ: xong ngay, material giữ nguyên. Lưu scene/project.
//    - Atlas đầy: script tự tạo atlas mới CÙNG tham số gốc (chỉ lớn hơn),
//      copy về asset gốc, chỉ đổi material.mainTexture. Lưu lại.
// 6. Thử trong game thật: bấm Play trong Editor, mở màn hình có chữ Việt
//    (vd menu, hội thoại) → chữ phải hiện đủ dấu, không ô vuông, không mất
//    nét/viền của font gốc. Rồi build ra iPhone thử lại (bước cuối này chỉ
//    Joseph làm được).
// 7. Nếu chữ vẫn thiếu hoặc lệch nét: đừng sửa tay lung tung — ghi lại tên
//    font asset + dòng log Console gửi cho Joseph (xem mục "chờ kiểm chứng"
//    trong exp/item7_results.md).
//
// Cách làm tay (khi script không chạy được vì bản TMP khác):
// - Window → TextMeshPro → Font Asset Creator → Source Font File: chọn font
//   nguồn (vd Be Vietnam Pro) → Character Set: Custom Characters → dán chuỗi
//   ký tự thiếu (trong tmp_glyph_plan.json, trường chars_text) → giữ nguyên
//   Point Size / Padding / Atlas Width x Height / Render Mode như asset gốc
//   → Generate Font Atlas → Save. Sau đó gán material.mainTexture của font
//   asset gốc sang atlas mới (đây chính là "mức rẻ nhất: sửa material").
// ============================================================================
