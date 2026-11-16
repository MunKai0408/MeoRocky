// TMP_VietnameseFallback.cs — Mục C6 BearTea: tạo TMP_FontAsset MỚI chỉ
// chứa chữ tiếng Việt còn thiếu rồi đăng ký làm fallback cho TMP_Settings.
//
// ⚠️ CHẠY TRONG UNITY EDITOR — KHÔNG chạy trên web, không chạy trên iSH.
// Cách dùng: chép file này vào Assets/Editor/ trong project Unity của game,
// mở lại Editor, chọn TMP_FontAsset gốc cần cứu trong cửa sổ Project, rồi
// dùng menu BearTea → TMP → mục 4/5/6 (xem hướng dẫn từng bước ở cuối file).
//
// KHÁC MỤC 7 (TMP_VietnameseAtlas.cs): mục 7 THÊM glyph vào atlas GỐC
// (giữ nguyên material, chỉ đổi texture atlas khi atlas cũ đầy). Mục C6
// KHÔNG ĐỘNG TỚI asset gốc: tạo asset MỚI chỉ chứa chữ Việt thiếu, rồi
// đăng ký nó vào danh sách fallback của TMP_Settings (+ bảng fallback của
// asset gốc). Dùng khi atlas gốc không sửa được (mất font nguồn, không
// tạo atlas mới được) hoặc muốn giữ nguyên 100% asset gốc.
//
// RANH GIỚI — ĐỌC KỸ:
// - Atlas SDF của asset fallback CHỈ sinh được trong Editor (FontEngine).
//   Web BearTea chỉ lập KẾ HOẠCH (file tmp_fallback_plan.json: chữ nào
//   thiếu + tham số atlas gốc cần giữ) — kế hoạch đó không phải asset
//   dùng được ngay.
// - Script này KHÔNG sửa asset gốc: không gán lại atlasTexture,
//   glyphTable, characterTable, faceInfo hay material của nó — chỉ ĐỌC
//   tham số (PointSize, Padding, render mode, kích thước atlas) để tạo
//   asset mới CÙNG tham số SDF. Material gốc giữ nguyên.
// - Fallback CHỈ cứu chữ tiếng Việt thiếu. Nếu asset gốc thiếu cả chữ
//   Latin, fallback không cứu được phần đó (kế hoạch ghi rõ ascii_note
//   từng target) — sửa atlas gốc (mục 7) hoặc thêm chữ Latin vào
//   fallback trong Editor.
// - Font nguồn: script dùng creationSettings.sourceFontFile của asset gốc
//   nếu còn; nếu mất, script hỏi người dùng chọn file .ttf/.otf (phải
//   chép font vào thư mục Assets của project trước rồi mới chọn được).
//
// Chưa biên dịch kiểm chứng: không có Unity ở đây, API dùng là API công
// khai của TextMeshPro 3.x/4.x (Unity 6 kèm TMP 4.x):
// TMP_FontAsset.CreateFontAsset, TryAddCharacters, atlasPadding,
// atlasRenderMode, TMP_Settings.instance.fallbackFontAssets,
// TMP_FontAsset.fallbackFontAssetTable. Thử trong Editor trước khi dùng
// cho game thật; nếu bản TMP của project khác tên hàm thì Editor báo lỗi
// rõ ở đúng bước đó (mỗi bước đều có try/catch + hướng dẫn làm tay).
// Ghi công: bộ 134 chữ Việt + logic đọc tham số từ BearTea
// (sdk/tmp_font.py) và IPA Localization Studio (js/modules/tmp_font.js,
// MIT, tác giả Joseph).
//
// Cần: package TextMeshPro (Unity 6: có sẵn trong Package Manager).
#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using System.IO;
using TMPro;
using UnityEditor;
using UnityEngine;

public static class TMP_VietnameseFallback
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
    private const int FallbackAtlasMin = 256;
    private const int FallbackAtlasMax = 1024;
    private const string FallbackFolder = "Assets/BearTea";

    // Kế hoạch fallback do web BearTea xuất (tmp_fallback_plan.json).
    // JsonUtility bỏ qua trường thừa nên file plan đầy đủ vẫn đọc được.
    [Serializable]
    private class FallbackTarget
    {
        public string name;
        public string fallback_name;
        public string source;
        public long path_id;
        public int[] missing_letters_codepoints;
        public int[] missing_marks_codepoints;
        public string chars_text;
        public int fallback_atlas_width;
        public int fallback_atlas_height;
        public float base_point_size;
        public float base_padding;
    }

    [Serializable]
    private class FallbackPlan
    {
        public string chars_text;
        public int[] missing_letters_codepoints;
        public int[] missing_marks_codepoints;
        public FallbackTarget[] fallbacks;
    }

    // ------------------------------------------------------------ menu
    [MenuItem("BearTea/TMP/4. Tạo font fallback tiếng Việt (từ file tmp_fallback_plan.json)")]
    public static void CreateFallbackFromPlan()
    {
        TMP_FontAsset baseAsset = GetSelectedFontAsset();
        if (baseAsset == null) return;
        string path = EditorUtility.OpenFilePanel(
            "Chọn file tmp_fallback_plan.json do web BearTea xuất",
            "", "json");
        if (string.IsNullOrEmpty(path)) { Debug.Log("[BearTea] Đã hủy."); return; }
        try
        {
            FallbackPlan plan = JsonUtility.FromJson<FallbackPlan>(File.ReadAllText(path));
            if (plan.fallbacks == null || plan.fallbacks.Length == 0)
            {
                Debug.Log("[BearTea] File plan không có fallback nào cần tạo — "
                    + "mọi asset đã đủ chữ Việt, hoặc không đọc được bảng glyph (xem unplanned trong file plan).");
                EditorUtility.DisplayDialog("BearTea — TMP",
                    "File plan không có fallback nào cần tạo.\nMọi asset đã đủ chữ Việt, hoặc không đọc được bảng glyph.", "OK");
                return;
            }
            FallbackTarget target = null;
            foreach (FallbackTarget t in plan.fallbacks)
                if (t.name == baseAsset.name) { target = t; break; }
            if (target == null && plan.fallbacks.Length == 1) target = plan.fallbacks[0];
            if (target == null)
            {
                Debug.LogWarning("[BearTea] File plan có " + plan.fallbacks.Length
                    + " fallback nhưng không có target nào khớp asset đang chọn \"" + baseAsset.name + "\". "
                    + "Hãy chọn đúng TMP_FontAsset gốc trong cửa sổ Project rồi chạy lại.");
                EditorUtility.DisplayDialog("BearTea — TMP",
                    "File plan có " + plan.fallbacks.Length + " fallback nhưng không khớp asset đang chọn \""
                    + baseAsset.name + "\".\nHãy chọn đúng TMP_FontAsset gốc rồi chạy lại.", "OK");
                return;
            }
            List<uint> cps = new List<uint>();
            if (target.missing_letters_codepoints != null)
                foreach (int cp in target.missing_letters_codepoints) cps.Add((uint)cp);
            if (target.missing_marks_codepoints != null)
                foreach (int cp in target.missing_marks_codepoints) cps.Add((uint)cp);
            if (cps.Count == 0 && !string.IsNullOrEmpty(target.chars_text))
                foreach (char ch in target.chars_text) cps.Add(ch);
            if (cps.Count == 0)
            {
                Debug.LogWarning("[BearTea] Target \"" + target.name + "\" trong file plan không có ký tự thiếu nào.");
                return;
            }
            string fallbackName = string.IsNullOrEmpty(target.fallback_name)
                ? baseAsset.name + " Vietnamese Fallback" : target.fallback_name;
            CreateFallbackAsset(baseAsset, cps.ToArray(), fallbackName,
                target.fallback_atlas_width, target.fallback_atlas_height);
        }
        catch (Exception e)
        {
            Debug.LogError("[BearTea] Không đọc được file plan: " + e.Message);
            EditorUtility.DisplayDialog("BearTea — TMP",
                "Không đọc được file tmp_fallback_plan.json:\n" + e.Message, "OK");
        }
    }

    [MenuItem("BearTea/TMP/5. Tạo font fallback tiếng Việt (đủ 134+8) cho asset đang chọn")]
    public static void CreateFallbackFull()
    {
        TMP_FontAsset baseAsset = GetSelectedFontAsset();
        if (baseAsset == null) return;
        CreateFallbackAsset(baseAsset, AllVietnameseCodepoints(),
            baseAsset.name + " Vietnamese Fallback", 0, 0);
    }

    [MenuItem("BearTea/TMP/6. Kiểm tra fallback đã đăng ký (TMP_Settings)")]
    public static void CheckRegisteredFallbacks()
    {
        try
        {
            TMP_Settings settings = TMP_Settings.instance;
            if (settings == null) { Debug.LogWarning("[BearTea] Không đọc được TMP_Settings.instance."); return; }
            if (settings.fallbackFontAssets == null || settings.fallbackFontAssets.Count == 0)
            {
                Debug.Log("[BearTea] TMP_Settings chưa có fallback font nào đăng ký.");
            }
            else
            {
                List<string> names = new List<string>();
                foreach (TMP_FontAsset fb in settings.fallbackFontAssets)
                    names.Add(fb != null ? fb.name : "(null)");
                Debug.Log("[BearTea] TMP_Settings fallback (" + names.Count + "): " + string.Join(", ", names.ToArray()));
            }
            if (settings.defaultFontAsset != null)
                Debug.Log("[BearTea] TMP_Settings defaultFontAsset: " + settings.defaultFontAsset.name);
        }
        catch (Exception e)
        {
            Debug.LogError("[BearTea] Không đọc được TMP_Settings (" + e.GetType().Name + "): " + e.Message
                + "\n— Cách kiểm tra tay: Edit → Project Settings → TextMesh Pro → Fallback Font Assets.");
        }
        TMP_FontAsset baseAsset = Selection.activeObject as TMP_FontAsset;
        if (baseAsset != null)
        {
            LogCoverage(baseAsset, "ĐỘ PHỦ ASSET ĐANG CHỌN");
            try
            {
                if (baseAsset.fallbackFontAssetTable == null || baseAsset.fallbackFontAssetTable.Count == 0)
                    Debug.Log("[BearTea] Asset \"" + baseAsset.name + "\" chưa có fallback riêng trong fallbackFontAssetTable.");
                else
                {
                    List<string> names = new List<string>();
                    foreach (TMP_FontAsset fb in baseAsset.fallbackFontAssetTable)
                        names.Add(fb != null ? fb.name : "(null)");
                    Debug.Log("[BearTea] Asset \"" + baseAsset.name + "\" fallback riêng ("
                        + names.Count + "): " + string.Join(", ", names.ToArray()));
                }
            }
            catch (Exception e)
            {
                Debug.LogWarning("[BearTea] Không đọc được fallbackFontAssetTable (" + e.GetType().Name + "): " + e.Message);
            }
        }
    }

    // ------------------------------------------------------------ lõi
    private static uint[] AllVietnameseCodepoints()
    {
        List<uint> all = new List<uint>(VietnameseLetters);
        all.AddRange(VietnameseMarks);
        return all.ToArray();
    }

    // Tạo asset fallback MỚI từ asset gốc. KHÔNG sửa asset gốc: chỉ đọc
    // tham số (PointSize, Padding, render mode, kích thước atlas) rồi tạo
    // asset mới CÙNG tham số SDF đó. Atlas fallback nhỏ hơn atlas gốc
    // cũng được (chỉ chứa chữ thiếu, <= 142 ký tự) nhưng PointSize /
    // Padding / render mode phải GIỮ NGUYÊN để nét chữ khớp font gốc.
    private static void CreateFallbackAsset(TMP_FontAsset baseAsset, uint[] codepoints,
        string fallbackName, int planAtlasWidth, int planAtlasHeight)
    {
        LogFontAsset(baseAsset, "ASSET GỐC (CHỈ ĐỌC — KHÔNG SỬA)");
        LogCoverage(baseAsset, "ĐỘ PHỦ ASSET GỐC");
        Font sourceFont = GetSourceFont(baseAsset);
        if (sourceFont == null) return;

        int pointSize = baseAsset.faceInfo.pointSize > 0
            ? Mathf.RoundToInt(baseAsset.faceInfo.pointSize) : 90;
        float padding = baseAsset.atlasPadding;
        int atlasWidth = planAtlasWidth > 0 ? planAtlasWidth
            : Mathf.Clamp(Mathf.Max(baseAsset.atlasWidth, FallbackAtlasMin),
                FallbackAtlasMin, FallbackAtlasMax);
        int atlasHeight = planAtlasHeight > 0 ? planAtlasHeight
            : Mathf.Clamp(Mathf.Max(baseAsset.atlasHeight, FallbackAtlasMin),
                FallbackAtlasMin, FallbackAtlasMax);
        EnsureFolder(FallbackFolder);
        string assetPath = FallbackFolder + "/" + fallbackName + ".asset";

        TMP_FontAsset fallback = AssetDatabase.LoadAssetAtPath<TMP_FontAsset>(assetPath);
        if (fallback != null)
        {
            Debug.Log("[BearTea] Đã có fallback \"" + fallbackName + "\" tại "
                + assetPath + " — thêm ký tự còn thiếu vào asset đó (không tạo mới).");
            try
            {
                uint[] missing;
                fallback.TryAddCharacters(codepoints, out missing);
                if (missing != null && missing.Length > 0)
                    Debug.LogWarning("[BearTea] Atlas fallback hiện có ĐẦY: còn thiếu "
                        + missing.Length + " ký tự — xóa asset fallback cũ rồi chạy lại để tạo atlas lớn hơn.");
                EditorUtility.SetDirty(fallback);
                AssetDatabase.SaveAssets();
            }
            catch (Exception e)
            {
                Debug.LogError("[BearTea] Không thêm được ký tự vào fallback có sẵn: " + e.Message);
                return;
            }
            RegisterFallback(baseAsset, fallback);
            LogCoverage(fallback, "ĐỘ PHỦ FALLBACK SAU KHI THÊM");
            return;
        }

        try
        {
            fallback = TMP_FontAsset.CreateFontAsset(
                sourceFont,
                pointSize,
                padding,
                baseAsset.atlasRenderMode,
                atlasWidth, atlasHeight,
                AtlasPopulationMode.Static,
                false);
            if (fallback == null) throw new Exception("CreateFontAsset trả về null.");
            fallback.name = fallbackName;
            uint[] missing;
            fallback.TryAddCharacters(codepoints, out missing);
            if (missing != null && missing.Length > 0)
            {
                Debug.LogWarning("[BearTea] Atlas fallback " + atlasWidth + "x" + atlasHeight
                    + " ĐẦY: còn thiếu " + missing.Length + " ký tự — tạo lại atlas lớn hơn "
                    + "(tối đa " + MaxAtlasSize + "x" + MaxAtlasSize + ").");
                UnityEngine.Object.DestroyImmediate(fallback, true);
                atlasWidth = Mathf.Min(atlasWidth * 2, MaxAtlasSize);
                atlasHeight = Mathf.Min(atlasHeight * 2, MaxAtlasSize);
                fallback = TMP_FontAsset.CreateFontAsset(
                    sourceFont, pointSize, padding, baseAsset.atlasRenderMode,
                    atlasWidth, atlasHeight, AtlasPopulationMode.Static, false);
                if (fallback == null) throw new Exception("CreateFontAsset (lần 2) trả về null.");
                fallback.name = fallbackName;
                fallback.TryAddCharacters(codepoints, out missing);
                if (missing != null && missing.Length > 0)
                    Debug.LogWarning("[BearTea] Atlas fallback lớn nhất vẫn thiếu "
                        + missing.Length + " ký tự — kiểm tra lại font nguồn có đủ chữ Việt không.");
            }
            AssetDatabase.CreateAsset(fallback, assetPath);
            AssetDatabase.SaveAssets();
            Debug.Log("[BearTea] ĐÃ TẠO fallback \"" + fallbackName + "\" tại " + assetPath
                + " (PointSize " + pointSize + ", Padding " + padding
                + ", renderMode " + baseAsset.atlasRenderMode
                + ", atlas " + atlasWidth + "x" + atlasHeight
                + ", font nguồn \"" + sourceFont.name + "\") — asset gốc KHÔNG đổi gì.");
            RegisterFallback(baseAsset, fallback);
            LogCoverage(fallback, "ĐỘ PHỦ FALLBACK MỚI");
            EditorUtility.DisplayDialog("BearTea — TMP",
                "Đã tạo font fallback \"" + fallbackName + "\".\n"
                + "Đã đăng ký vào TMP_Settings + bảng fallback của asset gốc.\n"
                + "Asset gốc \"" + baseAsset.name + "\" không đổi gì.\n\n"
                + "Hãy bấm Play để kiểm tra chữ Việt trong game.", "OK");
        }
        catch (Exception e)
        {
            Debug.LogError("[BearTea] Tạo fallback lỗi (" + e.GetType().Name + "): " + e.Message
                + "\n— Cách làm tay: Window → TextMeshPro → Font Asset Creator → "
                + "Source Font File: chọn font nguồn → Character Set: Custom Characters → "
                + "dán chuỗi ký tự thiếu (trong tmp_fallback_plan.json, trường chars_text) → "
                + "giữ nguyên Point Size / Padding / Atlas Width x Height / Render Mode như asset gốc "
                + "→ Generate Font Atlas → Save. Sau đó vào Edit → Project Settings → "
                + "TextMesh Pro → Fallback Font Assets → thêm asset mới vào danh sách.");
            EditorUtility.DisplayDialog("BearTea — TMP",
                "Không tạo được font fallback:\n" + e.Message
                + "\n\nXem Console để biết cách làm tay bằng Font Asset Creator.", "OK");
        }
    }

    // Đăng ký fallback vào 2 nơi: danh sách fallback CHUNG của TMP_Settings
    // (mọi text dùng font mặc định đều được cứu) + bảng fallback RIÊNG của
    // asset gốc (text đang dùng đúng asset gốc đó được cứu). Cả hai đều chỉ
    // THÊM tham chiếu — không sửa atlas/material của asset gốc.
    private static void RegisterFallback(TMP_FontAsset baseAsset, TMP_FontAsset fallback)
    {
        bool settingsOk = false, tableOk = false;
        try
        {
            TMP_Settings settings = TMP_Settings.instance;
            if (settings == null) throw new Exception("TMP_Settings.instance là null.");
            if (settings.fallbackFontAssets == null)
                settings.fallbackFontAssets = new List<TMP_FontAsset>();
            if (!settings.fallbackFontAssets.Contains(fallback))
            {
                settings.fallbackFontAssets.Add(fallback);
                EditorUtility.SetDirty(settings);
                Debug.Log("[BearTea] Đã đăng ký fallback vào TMP_Settings.fallbackFontAssets.");
            }
            else Debug.Log("[BearTea] Fallback đã có trong TMP_Settings từ trước.");
            settingsOk = true;
        }
        catch (Exception e)
        {
            Debug.LogError("[BearTea] Không đăng ký được vào TMP_Settings (" + e.GetType().Name + "): " + e.Message
                + "\n— Đăng ký tay: Edit → Project Settings → TextMesh Pro → "
                + "Fallback Font Assets → bấm + rồi chọn \"" + fallback.name + "\".");
        }
        try
        {
            if (baseAsset.fallbackFontAssetTable == null)
                baseAsset.fallbackFontAssetTable = new List<TMP_FontAsset>();
            if (!baseAsset.fallbackFontAssetTable.Contains(fallback))
            {
                baseAsset.fallbackFontAssetTable.Add(fallback);
                EditorUtility.SetDirty(baseAsset);
                Debug.Log("[BearTea] Đã đăng ký fallback vào bảng fallback riêng của asset gốc.");
            }
            else Debug.Log("[BearTea] Fallback đã có trong bảng fallback riêng của asset gốc từ trước.");
            tableOk = true;
        }
        catch (Exception e)
        {
            Debug.LogWarning("[BearTea] Không đăng ký được vào bảng fallback riêng của asset gốc ("
                + e.GetType().Name + "): " + e.Message
                + " — chỉ cần TMP_Settings là đủ cho hầu hết trường hợp.");
        }
        AssetDatabase.SaveAssets();
        if (settingsOk || tableOk)
            Debug.Log("[BearTea] Đăng ký fallback xong (TMP_Settings: " + (settingsOk ? "được" : "LỖI")
                + ", bảng riêng asset gốc: " + (tableOk ? "được" : "LỖI") + ").");
    }

    // Font nguồn: ưu tiên font gốc của asset (creationSettings), nếu mất thì
    // hỏi người dùng chọn file .ttf/.otf — file đó PHẢI nằm trong thư mục
    // Assets của project thì Unity mới load được (LoadAssetAtPath chỉ đọc
    // đường dẫn Assets/...). Không tự đoán font khác thay thế.
    private static Font GetSourceFont(TMP_FontAsset baseAsset)
    {
        try
        {
            if (baseAsset.creationSettings != null
                && baseAsset.creationSettings.sourceFontFile != null)
            {
                Debug.Log("[BearTea] Font nguồn lấy từ asset gốc: \""
                    + baseAsset.creationSettings.sourceFontFile.name + "\".");
                return baseAsset.creationSettings.sourceFontFile;
            }
        }
        catch (Exception e)
        {
            Debug.LogWarning("[BearTea] Không đọc được creationSettings.sourceFontFile: " + e.Message);
        }
        Debug.LogWarning("[BearTea] Asset gốc không còn font nguồn — cần người dùng chọn font nguồn (.ttf/.otf trong thư mục Assets).");
        if (!EditorUtility.DisplayDialog("BearTea — TMP",
            "Asset gốc \"" + baseAsset.name + "\" không còn font nguồn.\n\n"
            + "Hãy chọn file font nguồn (.ttf/.otf) để tạo atlas SDF cho fallback.\n"
            + "Font PHẢI nằm trong thư mục Assets của project (chép vào trước nếu chưa có).\n"
            + "Gợi ý: font gốc của game, hoặc Be Vietnam Pro (đủ tiếng Việt).",
            "Chọn font nguồn", "Hủy")) return null;
        string path = EditorUtility.OpenFilePanel("Chọn font nguồn (.ttf/.otf)", "Assets", "ttf,otf");
        if (string.IsNullOrEmpty(path)) { Debug.Log("[BearTea] Đã hủy chọn font nguồn."); return null; }
        string dataPath = Application.dataPath;
        if (!path.StartsWith(dataPath))
        {
            Debug.LogError("[BearTea] File font đã chọn nằm NGOÀI thư mục Assets của project — "
                + "Unity không load được. Hãy chép file vào thư mục Assets rồi chọn lại.");
            EditorUtility.DisplayDialog("BearTea — TMP",
                "File font nằm ngoài thư mục Assets — Unity không load được.\nHãy chép file vào thư mục Assets rồi chọn lại.", "OK");
            return null;
        }
        string assetPath = "Assets" + path.Substring(dataPath.Length);
        Font font = AssetDatabase.LoadAssetAtPath<Font>(assetPath);
        if (font == null)
        {
            Debug.LogError("[BearTea] Không load được Font tại " + assetPath
                + " — kiểm tra file có phải .ttf/.otf hợp lệ và đã import xong chưa.");
            EditorUtility.DisplayDialog("BearTea — TMP",
                "Không load được Font tại:\n" + assetPath, "OK");
            return null;
        }
        Debug.Log("[BearTea] Font nguồn do người dùng chọn: \"" + font.name + "\" (" + assetPath + ").");
        return font;
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

    private static void EnsureFolder(string folder)
    {
        if (AssetDatabase.IsValidFolder(folder)) return;
        string parent = "Assets";
        foreach (string part in folder.Substring("Assets/".Length).Split('/'))
        {
            string next = parent + "/" + part;
            if (!AssetDatabase.IsValidFolder(next)) AssetDatabase.CreateFolder(parent, part);
            parent = next;
        }
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
// 1. Trên web BearTea: chọn file .assets/.unity3d hoặc IPA của game → tab Font
//    → "Đọc TMP_FontAsset (atlas SDF)" → "Tạo font fallback" → tải 2 file về:
//    tmp_fallback_plan.json (kế hoạch: chữ nào thiếu + tham số atlas gốc)
//    và TMP_VietnameseFallback.cs (script này).
// 2. Chép file script này vào Assets/Editor/ trong project Unity của game
//    (cần package TextMeshPro; Unity 6: có sẵn). Mở lại Editor.
// 3. Trong Unity Editor: mở cửa sổ Project, chọn TMP_FontAsset gốc của game
//    (thường tên có "SDF", vd "SourceCodePro SDF").
// 4. Menu BearTea → TMP → "4. Tạo font fallback tiếng Việt (từ file
//    tmp_fallback_plan.json)" rồi chọn file plan đã tải ở bước 1.
//    - Font nguồn còn trong asset gốc: script dùng luôn, không hỏi.
//    - Font nguồn đã mất: script hỏi chọn file .ttf/.otf (phải chép font
//      vào thư mục Assets trước; gợi ý: font gốc của game, hoặc
//      Be Vietnam Pro đi kèm BearTea — đủ tiếng Việt).
//    - Script tạo asset mới "Assets/BearTea/<Tên font> Vietnamese
//      Fallback.asset" CHỈ chứa chữ Việt thiếu, CÙNG PointSize / Padding /
//      render mode với asset gốc; rồi đăng ký nó vào TMP_Settings +
//      bảng fallback riêng của asset gốc. Asset gốc KHÔNG đổi gì.
// 5. Kiểm tra: menu BearTea → TMP → "6. Kiểm tra fallback đã đăng ký
//    (TMP_Settings)" — Console phải liệt kê đủ fallback mới.
// 6. Thử trong game thật: bấm Play trong Editor, mở màn hình có chữ Việt
//    (vd menu, hội thoại) → chữ thiếu trước đây phải hiện đủ dấu, font gốc
//    không đổi nét/viền. Rồi build ra iPhone thử lại (bước cuối này chỉ
//    Joseph làm được).
// 7. Nếu chữ vẫn thiếu: đừng sửa tay lung tung — ghi lại tên font asset +
//    dòng log Console gửi cho Joseph (xem mục "chờ kiểm chứng" trong
//    exp/analysis_c6_results.md).
//
// Cách làm tay (khi script không chạy được vì bản TMP khác):
// - Window → TextMeshPro → Font Asset Creator → Source Font File: chọn font
//   nguồn → Character Set: Custom Characters → dán chuỗi ký tự thiếu
//   (trong tmp_fallback_plan.json, trường chars_text) → giữ nguyên Point
//   Size / Padding / Atlas Width x Height / Render Mode như asset gốc
//   → Generate Font Atlas → Save.
// - Sau đó đăng ký tay: Edit → Project Settings → TextMesh Pro →
//   Fallback Font Assets → bấm + rồi chọn asset fallback mới tạo.
// ============================================================================
