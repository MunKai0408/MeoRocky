"""Discovery cho pipeline trích xuất ngôn ngữ (Claude 07, đặc tả mục 7.2 bước 1-3).

- Inventory: liệt kê entry qua VFS (kể cả container lồng, tối đa
  limits.max_depth), lọc thô theo kích thước/đuôi file, chấm "khả năng
  chứa text" bằng magic + mẫu byte, loại trừ font/atlas/log/cache/crash/
  test/debug/subtitle (ghi rõ lý do, không parse im lặng).
- Engine detector: nhiều dấu hiệu (magic UnityFS/GDPC/LOCRES/FORM+GEN8, tên
  entry) cho 6 engine (unity, godot, unreal, cocos, defold, gamemaker) ->
  Evidence + confidence; KHÔNG kết luận CHẮC CHẮN chỉ từ tên file; Cocos/
  Defold không có magic đầu file đã xác minh nên tối đa CÓ KHẢ NĂNG.
- Locale detection: mã locale + tên hiển thị (en/zh/Hans/Hant/...), theo
  bảng locale (mặc định có sẵn, thêm mới bằng config/locales.toml mà
  không sửa code — đặc tả 3.4).
- Manifest/index: theo dõi tham chiếu từ file index tới bảng ngôn ngữ khác.

Chỉ thư viện chuẩn, Python >= 3.8. Không import UnityPy/lz4.
"""
from __future__ import annotations

import io
import json
import os
import re
import struct
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.errors import PB3Error
from core.model import Evidence, confidence as _confidence
from sdk.containers import (GODOT_PCK_MAGIC, UNREAL_PAK_MAGIC,
                            detect_container_kind, open_vfs)
from sdk.types import Evidence as _Ev  # noqa: F401  (xuất lại cho pipeline)
from sdk.vfs import VFS, VStat, check_vpath, read_policy

# ------------------------------------------------------------------ locale
# Bảng locale mặc định (preset bắt buộc en/zh theo đặc tả 2 + 7.4; các
# locale khác mở rộng được). Thêm locale mới = thêm mục vào
# config/locales.toml, không sửa code (xem load_locale_table).
LOCALE_BUILTIN: List[Dict[str, Any]] = [
    {"code": "en", "aliases": ["en-us", "en-gb", "en_us", "english", "eng"],
     "display": "English", "script": "Latn"},
    # BUG#2 (2026-11-17): zh-CN / zh-TW là mã riêng, KHÔNG gộp về "zh" nữa —
    # xin zh thì ra cả 2 file zh-CN.json + zh-TW.json. Đặt trước mục "zh"
    # (detect_locale ưu tiên mã dài hơn khi cùng độ tin cậy, xem detect_locale).
    {"code": "zh-CN", "aliases": ["zh-cn", "zh_cn", "简体中文", "simplified chinese"],
     "display": "Chinese (Simplified)", "script": "Hans"},
    {"code": "zh-TW", "aliases": ["zh-tw", "zh_hk", "繁體中文", "traditional chinese"],
     "display": "Chinese (Traditional)", "script": "Hant"},
    {"code": "zh", "aliases": ["chinese", "中文", "mandarin"],
     "display": "Chinese", "script": "Hans"},
    {"code": "zh-Hans", "aliases": ["zh-hans", "简体中文"],
     "display": "Chinese (Simplified)", "script": "Hans"},
    {"code": "zh-Hant", "aliases": ["zh-hant", "繁體中文"],
     "display": "Chinese (Traditional)", "script": "Hant"},
    {"code": "vi", "aliases": ["vi-vn", "vietnamese", "tiếng việt", "tieng viet"],
     "display": "Vietnamese", "script": "Latn"},
    {"code": "ja", "aliases": ["ja-jp", "japanese", "日本語"],
     "display": "Japanese", "script": "Jpan"},
    {"code": "ko", "aliases": ["ko-kr", "korean", "한국어"],
     "display": "Korean", "script": "Kore"},
    {"code": "fr", "aliases": ["fr-fr", "french", "français"],
     "display": "French", "script": "Latn"},
    {"code": "de", "aliases": ["de-de", "german", "deutsch"],
     "display": "German", "script": "Latn"},
    {"code": "es", "aliases": ["es-es", "spanish", "español"],
     "display": "Spanish", "script": "Latn"},
    {"code": "pt", "aliases": ["pt-br", "pt-pt", "portuguese", "português"],
     "display": "Portuguese", "script": "Latn"},
    {"code": "ru", "aliases": ["ru-ru", "russian", "русский"],
     "display": "Russian", "script": "Cyrl"},
    {"code": "th", "aliases": ["th-th", "thai", "ภาษาไทย"],
     "display": "Thai", "script": "Thai"},
    {"code": "id", "aliases": ["id-id", "indonesian", "bahasa indonesia"],
     "display": "Indonesian", "script": "Latn"},
    {"code": "ms", "aliases": ["ms-my", "malay", "bahasa melayu"],
     "display": "Malay", "script": "Latn"},
    {"code": "tl", "aliases": ["tl-ph", "tagalog", "filipino", "wikang tagalog"],
     "display": "Tagalog", "script": "Latn"},
    {"code": "hu", "aliases": ["hu-hu", "hungarian", "magyar"],
     "display": "Hungarian", "script": "Latn"},
    {"code": "it", "aliases": ["it-it", "italian", "italiano"],
     "display": "Italian", "script": "Latn"},
    {"code": "nl", "aliases": ["nl-nl", "dutch", "nederlands"],
     "display": "Dutch", "script": "Latn"},
    {"code": "pl", "aliases": ["pl-pl", "polish", "polski"],
     "display": "Polish", "script": "Latn"},
    {"code": "tr", "aliases": ["tr-tr", "turkish", "türkçe"],
     "display": "Turkish", "script": "Latn"},
    {"code": "ar", "aliases": ["ar-sa", "arabic", "العربية"],
     "display": "Arabic", "script": "Arab"},
    {"code": "hi", "aliases": ["hi-in", "hindi", "हिन्दी"],
     "display": "Hindi", "script": "Deva"},
    {"code": "uk", "aliases": ["uk-ua", "ukrainian", "українська"],
     "display": "Ukrainian", "script": "Cyrl"},
    {"code": "sv", "aliases": ["sv-se", "swedish", "svenska"],
     "display": "Swedish", "script": "Latn"},
    # Mục A (2026-11-16): các mã langdetect có thể tách ra từ MonoBehaviour
    # (da/fi/ro/cs/sk/no/el/he) — trước đây thiếu nên file tách ngôn ngữ
    # mang mã này không nhận diện được locale: vừa bị lọc mềm giữ lại nhầm
    # (lọt vào đợt en/zh), vừa mất locale ở kết quả (locale None).
    {"code": "da", "aliases": ["da-dk", "danish", "dansk"],
     "display": "Danish", "script": "Latn"},
    {"code": "fi", "aliases": ["fi-fi", "finnish", "suomi"],
     "display": "Finnish", "script": "Latn"},
    {"code": "ro", "aliases": ["ro-ro", "romanian", "română"],
     "display": "Romanian", "script": "Latn"},
    {"code": "cs", "aliases": ["cs-cz", "czech", "čeština"],
     "display": "Czech", "script": "Latn"},
    {"code": "sk", "aliases": ["sk-sk", "slovak", "slovenčina"],
     "display": "Slovak", "script": "Latn"},
    # BUG#2 (2026-11-17): "nb" (Bokmål) là mã riêng — trước đây chỉ nằm
    # trong alias "nb-no" của "no" nên file nb bị nhận locale None rồi lọt
    # bộ lọc. Đặt TRƯỚC "no" để "nb-no" khớp "nb" (cùng độ tin cậy, "nb"
    # đứng trước thắng khi hòa).
    {"code": "nb", "aliases": ["nb-no", "norwegian bokmål", "bokmål"],
     "display": "Norwegian Bokmål", "script": "Latn"},
    {"code": "no", "aliases": ["nn-no", "norwegian", "norsk"],
     "display": "Norwegian", "script": "Latn"},
    # BUG#2 (2026-11-17): "bg" và "fil" trước đây không có trong bảng nên
    # file bg/fil bị nhận locale None rồi lọt bộ lọc en/zh.
    {"code": "bg", "aliases": ["bg-bg", "bulgarian", "български"],
     "display": "Bulgarian", "script": "Cyrl"},
    {"code": "fil", "aliases": ["fil-ph", "filipino"],
     "display": "Filipino", "script": "Latn"},
    {"code": "el", "aliases": ["el-gr", "greek", "ελληνικά"],
     "display": "Greek", "script": "Grek"},
    {"code": "he", "aliases": ["he-il", "hebrew", "עברית", "iw"],
     "display": "Hebrew", "script": "Hebr"},
    # (fix strings-arbitration 2026-11-17): các mã BCP 47 hay gặp trong
    # thư mục .lproj của IPA iOS mà bảng còn thiếu — trước đây detect_locale
    # trả None nên file bị loại khỏi merge (locale None) và xuất tên rác
    # đánh số ở chế độ tách (BGNativeAd.BGNativeAd.2.json...). Đo thật trên
    # Tom (bn-BD/bn-IN/gu/kn/mr/ne/pa/si/ta/te/ur/uz/az/hy/kk/ky/lv/sr) và
    # EK2 (ca/hr). "iw" là mã cũ của Hebrew trên iOS (nay "he").
    {"code": "bn", "aliases": ["bn-bd", "bn-in", "bengali", "বাংলা"],
     "display": "Bengali", "script": "Beng"},
    {"code": "gu", "aliases": ["gu-in", "gujarati", "ગુજરાતી"],
     "display": "Gujarati", "script": "Gujr"},
    {"code": "kn", "aliases": ["kn-in", "kannada", "ಕನ್ನಡ"],
     "display": "Kannada", "script": "Knda"},
    {"code": "mr", "aliases": ["mr-in", "marathi", "मराठी"],
     "display": "Marathi", "script": "Deva"},
    {"code": "ne", "aliases": ["ne-np", "nepali", "नेपाली"],
     "display": "Nepali", "script": "Deva"},
    {"code": "pa", "aliases": ["pa-in", "punjabi", "ਪੰਜਾਬੀ"],
     "display": "Punjabi", "script": "Guru"},
    {"code": "si", "aliases": ["si-lk", "sinhala", "සිංහල"],
     "display": "Sinhala", "script": "Sinh"},
    {"code": "ta", "aliases": ["ta-in", "tamil", "தமிழ்"],
     "display": "Tamil", "script": "Taml"},
    {"code": "te", "aliases": ["te-in", "telugu", "తెలుగు"],
     "display": "Telugu", "script": "Telu"},
    {"code": "ur", "aliases": ["ur-pk", "urdu", "اردو"],
     "display": "Urdu", "script": "Arab"},
    {"code": "uz", "aliases": ["uz-uz", "uzbek", "o'zbek"],
     "display": "Uzbek", "script": "Latn"},
    {"code": "az", "aliases": ["az-az", "azerbaijani", "azərbaycan"],
     "display": "Azerbaijani", "script": "Latn"},
    {"code": "hy", "aliases": ["hy-am", "armenian", "հայերեն"],
     "display": "Armenian", "script": "Armn"},
    {"code": "kk", "aliases": ["kk-kz", "kazakh", "қазақша"],
     "display": "Kazakh", "script": "Cyrl"},
    {"code": "ky", "aliases": ["ky-kg", "kyrgyz", "кыргызча"],
     "display": "Kyrgyz", "script": "Cyrl"},
    {"code": "lv", "aliases": ["lv-lv", "latvian", "latviešu"],
     "display": "Latvian", "script": "Latn"},
    {"code": "sr", "aliases": ["sr-rs", "serbian", "српски"],
     "display": "Serbian", "script": "Cyrl"},
    {"code": "ca", "aliases": ["ca-es", "catalan", "català"],
     "display": "Catalan", "script": "Latn"},
    {"code": "hr", "aliases": ["hr-hr", "croatian", "hrvatski"],
     "display": "Croatian", "script": "Latn"},
]

# Preset ngôn ngữ bắt buộc (đặc tả 2, 7.4): English + Chinese.
PRESET_LOCALES = ("en", "zh")


def locale_display_name(code: str, table: Optional[List[Dict[str, Any]]] = None) -> str:
    """Tên hiển thị của locale (dùng đặt tên file xuất: English.json...)."""
    for loc in (table if table is not None else LOCALE_BUILTIN):
        if loc["code"].lower() == code.lower():
            return loc["display"]
    return code


def export_filename(code: str, table: Optional[List[Dict[str, Any]]] = None) -> str:
    """Tên file JSON xuất cho một locale (đặc tả 7.6: English.json...)."""
    return locale_display_name(code, table) + ".json"


def locale_base(code: str) -> str:
    """Mã gốc của một mã locale (BUG#2): "zh-CN" -> "zh", "en" -> "en"."""
    return (code or "").replace("_", "-").split("-")[0].lower()


def canonical_locale_code(code: str, table: Optional[List[Dict[str, Any]]] = None) -> str:
    """Mã locale với đúng dạng chữ hoa/thường trong bảng (BUG#2):
    "zh-cn" -> "zh-CN", "en" -> "en". Không có trong bảng thì giữ nguyên."""
    table = table if table is not None else LOCALE_BUILTIN
    low = (code or "").lower()
    for loc in table:
        if loc["code"].lower() == low:
            return loc["code"]
    return code


def locale_in_wanted(code: Optional[str], wanted) -> bool:
    """Mã locale có thuộc tập lọc không (BUG#2, khớp theo họ locale).

    wanted là tập mã chữ thường người dùng xin (vd {"en", "zh"}).
    Khớp khi: đúng mã ("zh-CN" xin "zh-cn"), mã gốc được xin
    ("zh-CN" xin "zh"), hoặc mã là mã gốc của một biến thể được xin
    ("zh" xin "zh-cn" — bảng zh gốc là fallback cho biến thể).
    Biến thể anh em KHÔNG khớp nhau ("zh-TW" xin "zh-cn" là sai)."""
    if code is None or wanted is None:
        return False
    c = str(code).lower()
    want = {str(w).lower() for w in wanted}
    if c in want:
        return True
    base = locale_base(c)
    if base in want:
        return True
    if c == base and any(base == locale_base(str(w)) for w in want):
        return True
    return False


def load_locale_table(root: Optional[str] = None) -> List[Dict[str, Any]]:
    """Bảng locale = mặc định + config/locales.toml (nếu có) ghi đè/bổ sung.

    Định dạng file (TOML): [locale.en] aliases=[...] display="..." script="...".
    File hỏng -> bỏ qua, dùng bảng mặc định (không làm hỏng pipeline)."""
    table = [dict(loc, aliases=list(loc["aliases"])) for loc in LOCALE_BUILTIN]
    if not root:
        return table
    path = os.path.join(str(root), "config", "locales.toml")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return table
    try:
        from core.config import parse_toml
        data = parse_toml(text)
    except Exception:
        return table
    if not isinstance(data, dict):
        return table
    by_code = {loc["code"].lower(): loc for loc in table}
    locales = data.get("locale")
    if not isinstance(locales, dict):
        return table
    for code, spec in locales.items():
        if not isinstance(spec, dict):
            continue
        aliases = spec.get("aliases")
        if not isinstance(aliases, list):
            aliases = []
        entry = {"code": str(code),
                 "aliases": [str(a).strip().lower() for a in aliases if str(a).strip()],
                 "display": str(spec.get("display") or code),
                 "script": str(spec.get("script") or "")}
        old = by_code.get(str(code).lower())
        if old is not None:
            old.update(entry)
        else:
            table.append(entry)
            by_code[str(code).lower()] = entry
    return table


# Tên cột header CSV -> mã chuẩn, GHI ĐÈ trước khi khớp bảng (đợt sửa
# 2026-11-16): "Chinese (Simplified)" -> "zh" (lọt lọc en/zh của UI —
# giản thể là tiếng Trung mặc định của preset), "Chinese (Traditional)" ->
# "zh-Hant" (KHÔNG lọt lọc en/zh: lọc chỉ nhận {'en','zh'} nên cột phồn
# thể bị loại khi lọc en/zh — đúng ngữ nghĩa của bộ lọc, không phải lỗi).
# "zh-tw"/"zh-hk" -> "zh-Hant" (Đài Loan/Hồng Kông dùng phồn thể) dù bảng
# LOCALE_BUILTIN liệt kê chúng cả dưới "zh" (khớp theo thứ tự bảng sẽ sai).
_LOCALE_NAME_OVERRIDES = {
    "chinese (simplified)": "zh",
    "simplified chinese": "zh",
    "chinese (traditional)": "zh-Hant",
    "traditional chinese": "zh-Hant",
    "zh-tw": "zh-Hant",
    "zh_tw": "zh-Hant",
    "zh-hk": "zh-Hant",
    "zh_hk": "zh-Hant",
    "zh-mo": "zh-Hant",
    "zh_mo": "zh-Hant",
}


def normalize_locale_name(name, table=None):
    """Chuẩn hoá tên locale (tên cột header CSV, tên ngôn ngữ) về mã chuẩn:
    'English'->'en', 'French'->'fr', 'Chinese'->'zh', 'en'/'zh' giữ nguyên.
    Không phân biệt hoa/thường, '_' ~ '-', bỏ khoảng trắng thừa và phần
    trong ngoặc khi khớp display ("Chinese (Simplified)" khớp cả display
    lẫn alias). Trả None khi tên KHÔNG phải ngôn ngữ ('Notes', 'Key',
    'col13', ''...) — cột đó bị bỏ qua, không thành locale. Dùng bảng
    LOCALE_BUILTIN (cộng config/locales.toml nếu truyền table đã load)."""
    if not isinstance(name, str):
        return None
    raw = name.strip()
    if not raw or re.match(r"^col\d+$", raw, re.IGNORECASE):
        return None
    norm = re.sub(r"\s+", " ", raw.lower().replace("_", "-")).strip()
    norm = re.sub(r"\s*\(\s*", " (", norm)
    norm = re.sub(r"\s*\)\s*", ")", norm).strip()
    if norm in _LOCALE_NAME_OVERRIDES:
        return _LOCALE_NAME_OVERRIDES[norm]
    table = table if table is not None else LOCALE_BUILTIN
    # Khớp mã/alias/display trực tiếp, rồi khớp tên gốc bỏ phần ngoặc
    # ("Chinese (Simplified)" -> "chinese" khớp display "Chinese" của zh).
    base = re.sub(r"\s*\([^)]*\)\s*", "", norm).strip()
    for cand in {norm, base}:
        if not cand:
            continue
        for loc in table:
            if cand == loc["code"].lower() or cand == loc["display"].lower():
                return loc["code"]
            for a in loc.get("aliases", []):
                if cand == a.lower():
                    return loc["code"]
    # Khớp alias dạng "simplified chinese" từ phần trong ngoặc.
    m = re.search(r"\(([^)]+)\)", norm)
    if m and base:
        qual = (base + " " + m.group(1)).strip()
        for loc in table:
            for a in loc.get("aliases", []):
                if qual == a.lower():
                    return loc["code"]
    return None


def _alias_hit(text_low: str, alias: str) -> bool:
    """Alias xuất hiện trong text (khớp ranh giới từ/ký tự phân tách)."""
    if not alias:
        return False
    idx = text_low.find(alias)
    while idx != -1:
        before = text_low[idx - 1] if idx > 0 else ""
        after = text_low[idx + len(alias)] if idx + len(alias) < len(text_low) else ""
        ok_before = (not before) or (not before.isalnum())
        ok_after = (not after) or (not after.isalnum())
        if ok_before and ok_after:
            return True
        idx = text_low.find(alias, idx + 1)
    return False


# Regex mã locale biên dịch sẵn (detect_locale gọi cho từng file ứng viên:
# biên dịch lại mỗi lần qua re.search + re.escape rất phí; cache theo mã).
_LOCALE_CODE_RES: Dict[str, Any] = {}


def _locale_code_re(code_low: str):
    rx = _LOCALE_CODE_RES.get(code_low)
    if rx is None:
        rx = re.compile(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(code_low))
        if len(_LOCALE_CODE_RES) < 512:
            _LOCALE_CODE_RES[code_low] = rx
    return rx


def detect_locale(text: str, table: Optional[List[Dict[str, Any]]] = None
                  ) -> Tuple[Optional[str], float, List[Evidence]]:
    """Nhận diện locale từ tên file/đường dẫn/nội dung.

    Trả (code, confidence, evidence). Mã locale khớp theo dạng `_en.`/`/en/`/
    `-en-` được tin hơn tên hiển thị ("English"); biến thể Hans/Hant chỉ
    kết luận khi có alias riêng của nó. Không khớp -> (None, 0.0, [])."""
    table = table if table is not None else LOCALE_BUILTIN
    low = (text or "").lower()
    best: Tuple[Optional[str], float, List[Evidence]] = (None, 0.0, [])
    for loc in table:
        code = loc["code"]
        conf = 0.0
        ev: List[Evidence] = []
        # Mã locale đúng dạng tách biệt (vd "en.json", "/zh-Hans/"): tin nhất.
        # Kiểm tra chuỗi con trước (rẻ): mã không xuất hiện thì regex chắc
        # chắn không khớp; chỉ chạy regex (đã biên dịch sẵn) khi cần.
        code_low = code.lower()
        if code_low in low and _locale_code_re(code_low).search(low):
            conf = 0.9
            ev.append(Evidence("locale_code", code, 0.9,
                               "mã locale %r trong tên/đường dẫn" % code))
        else:
            for alias in loc["aliases"]:
                if _alias_hit(low, alias):
                    conf = max(conf, 0.7)
                    ev.append(Evidence("locale_name", alias, 0.7,
                                       "tên/alias %r gợi ý locale %s" % (alias, code)))
                    break
        if conf > best[1] or (conf == best[1] and best[0] is not None
                              and len(code) > len(best[0])):
            best = (code, conf, ev)
    return best


# ------------------------------------------------------------------ engine
# Magic đầu file đã xác minh (Claude 08; xem bảng bằng chứng trong
# handoff/account_08.md):
# - UnityFS: "UnityFS" ở đầu file (sdk.unity).
# - Godot PCK: "GDPC" ở đầu file, Godot 3/4 (sdk.containers.GODOT_PCK_MAGIC).
# - Unreal LOCRES (Compact trở lên): 16 byte
#   0E 14 74 75 67 4A 03 FC 4A 15 90 9D C3 37 7F 1B ở đầu file; bản Legacy
#   không có magic -> chỉ nhận bằng tên. PAK không có magic ở đầu file
#   (magic nằm ở footer cuối file) nên chỉ nhận bằng tên.
# - GameMaker data.win: IFF "FORM" ở đầu file + chunk đầu tiên "GEN8" ở
#   offset 8. "FORM" một mình quá chung (AIFF/IFF) nên không đủ.
# - Cocos, Defold: KHÔNG có magic đầu file đã xác minh -> chỉ nhận bằng tên,
#   tối đa CÓ KHẢ NĂNG, không bao giờ CHẮC CHẮN.
LOCRES_MAGIC = bytes.fromhex("0E147475674A03FC4A15909DC3377F1B")
GAMEMAKER_FORM_MAGIC = b"FORM"
GAMEMAKER_GEN8_CHUNK = b"GEN8"


def _head_magic(engine: str, head: bytes) -> Optional[str]:
    """Tên magic khớp ở đầu file cho engine (None = không khớp/không có magic)."""
    if engine == "godot" and head[:4] == GODOT_PCK_MAGIC:
        return "GDPC"
    if engine == "unreal" and head[:16] == LOCRES_MAGIC:
        return "LOCRES"
    if (engine == "gamemaker" and head[:4] == GAMEMAKER_FORM_MAGIC
            and head[8:12] == GAMEMAKER_GEN8_CHUNK):
        return "FORM/GEN8"
    return None


# Dấu hiệu tên cho từng engine. Mạnh 0.3 / yếu 0.15 (như Unity):
# - ".ext": khớp đuôi file (vd ".pck", ".yyp").
# - "tên.đuôi": khớp đúng tên file (vd "project.godot", "game.project",
#   "project.json", "main.js").
# - "thư_mục/": khớp đầu đường dẫn hoặc sau dấu "/" (vd "res/", "datafiles/").
# - chuỗi khác: xuất hiện trong đường dẫn (vd "cocos2d").
_ENGINE_NAME_SIGS = {
    "godot": {
        "strong": (".pck", "project.godot", ".translation"),
        "weak": (".gd", ".tscn", ".tres", ".res", ".gdc", ".scn"),
    },
    "unreal": {
        "strong": (".pak", ".locres", ".locmeta", ".uproject"),
        "weak": (".uasset", ".umap"),
    },
    "cocos": {
        "strong": ("project.json", "cocos2d", ".ccb", ".fire"),
        "weak": ("main.js", "res/", "settings/"),
    },
    "defold": {
        "strong": ("game.project", ".archive", ".arci", ".arcd", ".dmanifest",
                   ".collection", ".atlas"),
        "weak": (".script", ".gui"),
    },
    "gamemaker": {
        "strong": (".yyp", ".yy", ".gml", ".yyz"),
        "weak": (".win", "datafiles/", "options/"),
    },
}
# Thứ tự ưu tiên khi hai engine hòa điểm (Unity trước để không làm giảm độ
# chính xác nhận diện Unity đã có).
_ENGINE_PRIORITY = ("unity", "godot", "unreal", "cocos", "defold", "gamemaker")

_UNITY_STRONG_REASONS = ("globalgamemanagers", "resources.assets",
                         ".unity3d", "Data/Managed", "UnityFramework")


def _name_hit(low: str, sig: str) -> bool:
    """Dấu hiệu tên `sig` có khớp đường dẫn `low` (đã viết thường) không."""
    if sig.startswith("."):
        return os.path.splitext(low.rsplit("/", 1)[-1])[1] == sig
    if sig.endswith("/"):
        return low.startswith(sig) or ("/" + sig) in low
    if "." in sig:
        return low.rsplit("/", 1)[-1] == sig
    return sig in low


def engine_level(confidence: float, has_magic: bool) -> str:
    """Mức kết luận cho một engine: CHẮC CHẮN chỉ khi có magic; chỉ tên
    file thì tối đa CÓ KHẢ NĂNG (đặc tả 7.2 bước 2)."""
    if has_magic and confidence >= 0.8:
        return "CHẮC CHẮN"
    if confidence >= 0.5:
        return "CÓ KHẢ NĂNG"
    return "CHƯA ĐỦ BẰNG CHỨNG"


def engine_signals(engine: str, names: List[str],
                   heads: Optional[Dict[str, bytes]] = None
                   ) -> Tuple[List[Evidence], float, bool]:
    """Bằng chứng + confidence + có-magic cho MỘT engine cụ thể.

    Nguồn sự thật duy nhất cho bảng dấu hiệu: detect_engine() và các plugin
    nhận diện engine (kind="engine") đều dùng hàm này. Magic mạnh 0.9; tên
    mạnh 0.3 / yếu 0.15; mỗi dấu hiệu chỉ tính một lần. Engine lạ -> PB3Error
    E_PLUGIN_INVALID."""
    from sdk import unity as unity_sdk
    if engine not in _ENGINE_PRIORITY:
        raise PB3Error("E_PLUGIN_INVALID", "engine lạ: %r" % (engine,))
    evidence: List[Evidence] = []
    heads = heads or {}
    has_magic = False
    if engine == "unity":
        for path, head in heads.items():
            if head[:7] == b"UnityFS":
                has_magic = True
                evidence.append(Evidence("magic", "UnityFS", 0.9,
                                         "byte đầu của %s = UnityFS" % path))
                break
        verdict = unity_sdk.detect_engine(names)
        for reason in verdict.reasons:
            if reason.startswith("không thấy"):
                continue
            strong = any(s in reason for s in _UNITY_STRONG_REASONS)
            evidence.append(Evidence("name", reason, 0.3 if strong else 0.15,
                                     "tên entry: " + reason))
        return evidence, _confidence(evidence), has_magic
    for path, head in heads.items():
        magic = _head_magic(engine, head)
        if magic is not None:
            has_magic = True
            evidence.append(Evidence("magic", magic, 0.9,
                                     "byte đầu của %s = %s" % (path, magic)))
            break
    sigs = _ENGINE_NAME_SIGS[engine]
    # Tính trước mỗi tên một lần (chữ thường + tên file + đuôi): bản cũ gọi
    # str(name).lower() + rsplit/splitext trong _name_hit cho MỖI dấu hiệu
    # (6 engine x ~13 dấu hiệu x N tên) — cùng một phép tính lặp hàng chục lần.
    # Logic khớp y hệt _name_hit, chỉ hoán đổi thứ tự vòng lặp (dấu hiệu
    # ngoài, tên trong -> tên ngoài, dấu hiệu trong vẫn cho cùng kết quả
    # vì mỗi dấu hiệu chỉ tính một lần qua `break`).
    prepared = []
    for name in names:
        low = str(name).lower()
        base = low.rsplit("/", 1)[-1]
        prepared.append((name, low, base, os.path.splitext(base)[1]))
    for weight, group in ((0.3, sigs["strong"]), (0.15, sigs["weak"])):
        for sig in group:
            for name, low, base, ext in prepared:
                if sig.startswith("."):
                    hit = (ext == sig)
                elif sig.endswith("/"):
                    hit = low.startswith(sig) or ("/" + sig) in low
                elif "." in sig:
                    hit = (base == sig)
                else:
                    hit = (sig in low)
                if hit:
                    evidence.append(Evidence(
                        "name", sig, weight,
                        "tên entry %s khớp dấu hiệu %s của %s" % (name, sig, engine)))
                    break
    return evidence, _confidence(evidence), has_magic


@dataclass
class EngineGuess:
    """Kết quả nhận diện engine: engine + mức + confidence + evidence."""
    engine: str = "unknown"
    level: str = "CHƯA ĐỦ BẰNG CHỨNG"
    confidence: float = 0.0
    evidence: List[Evidence] = field(default_factory=list)


def detect_engine(names: List[str], heads: Optional[Dict[str, bytes]] = None) -> EngineGuess:
    """Nhận diện engine từ tên entry + magic byte đầu file.

    Chấm riêng từng engine (unity, godot, unreal, cocos, defold, gamemaker)
    bằng engine_signals() rồi chọn engine có mức cao nhất (hòa điểm -> ưu
    tiên Unity, rồi theo _ENGINE_PRIORITY).
    - Magic ở đầu file -> bằng chứng mạnh (0.9).
    - Tên entry -> bằng chứng yếu (0.3 mạnh / 0.15 yếu).
    - CHẮC CHẮN chỉ khi có magic; chỉ tên file thì tối đa CÓ KHẢ NĂNG.
    - Cocos/Defold không có magic đầu file đã xác minh -> chỉ nhận bằng tên.
    - Unreal PAK chỉ nhận bằng tên (magic ở footer cuối file, không có trong
      mẫu đầu); .locres (Compact+) có magic 16 byte."""
    best = None
    for engine in _ENGINE_PRIORITY:
        evidence, conf, has_magic = engine_signals(engine, names, heads)
        level = engine_level(conf, has_magic)
        rank = {"CHẮC CHẮN": 2, "CÓ KHẢ NĂNG": 1}.get(level, 0)
        key = (rank, conf, 1 if has_magic else 0, -_ENGINE_PRIORITY.index(engine))
        if best is None or key > best[0]:
            best = (key, engine, level, conf, evidence)
    _key, engine, level, conf, evidence = best
    if level == "CHƯA ĐỦ BẰNG CHỨNG":
        return EngineGuess(engine="unknown", level=level, confidence=conf, evidence=evidence)
    return EngineGuess(engine=engine, level=level, confidence=conf, evidence=evidence)


# ------------------------------------------------------------------ inventory
# Đuôi file localization đã biết (đặc tả 7.3 + plugin Claude 06 + Claude 08).
LOC_EXTENSIONS = frozenset([
    ".json", ".csv", ".tsv", ".xml", ".yaml", ".yml", ".ini", ".cfg", ".conf",
    ".properties", ".plist", ".stringsdict", ".xcstrings", ".strings",
    ".sqlite", ".db", ".sqlite3", ".db3", ".po", ".mo", ".xlf", ".xliff", ".resx",
    # Đuôi localization của engine khác (Claude 08): .translation (Godot,
    # nhị phân), .locres/.locmeta (Unreal), .yy/.yyp/.gml (GameMaker: JSON/
    # script chứa chuỗi). Chỉ liệt kê để inventory chấm text-score; parse
    # vẫn do plugin định dạng (chưa có plugin nào parse các đuôi này).
    ".translation", ".locres", ".locmeta", ".yy", ".yyp", ".gml",
])
CONTAINER_EXTENSIONS = frozenset([
    ".zip", ".ipa", ".tar", ".gz", ".tgz", ".xz", ".bz2",
    # Container engine khác (Claude 08): .pck (Godot, magic GDPC), .pak
    # (Unreal, magic ở footer cuối file). Chưa có backend mở (sdk.godot /
    # sdk.unreal chưa tồn tại) nên inventory liệt kê dạng container nhưng
    # không mở được con (open_vfs trả E_UNSUPPORTED_FORMAT, bắt gọn trong
    # inventory()).
    ".pck", ".pak",
])
UNITY_EXTENSIONS = frozenset([".assets", ".unity3d", ".bundle", ".resS"])
# Bộ tuple dùng cho str.endswith (đừng dựng lại mỗi entry trong inventory).
_UNITY_EXTS_TUPLE = tuple(UNITY_EXTENSIONS)

# Mọi đuôi đặc thù engine (nhận diện + gợi ý plugin): hợp của các bảng dấu
# hiệu tên trong _ENGINE_NAME_SIGS.
ENGINE_EXTENSIONS = frozenset([
    ".pck", ".translation", ".gd", ".tscn", ".tres", ".res", ".gdc", ".scn",
    ".pak", ".locres", ".locmeta", ".uproject", ".uasset", ".umap",
    ".ccb", ".fire",
    ".archive", ".arci", ".arcd", ".dmanifest", ".collection", ".atlas",
    ".script", ".gui",
    ".yyp", ".yy", ".gml", ".yyz", ".win",
])

# Loại trừ theo đặc tả 7.3: subtitle (trừ khi người dùng chọn), font, atlas
# hình, log, cache, crash report, dữ liệu test, chuỗi debug.
_EXCLUDE_RES = [
    (re.compile(r"\.(ttf|otf|woff2?|fnt|atlas)$", re.I), "font/atlas hình"),
    (re.compile(r"\.(log|cache|crash|dmp)$", re.I), "log/cache/crash report"),
    (re.compile(r"(^|/)(test|tests|__tests__|debug|dbg)(/|$)", re.I), "dữ liệu test/debug"),
    (re.compile(r"\.(srt|vtt|ass|ssa)$", re.I), "phụ đề (subtitle)"),
]


def exclusion_reason(path: str) -> Optional[str]:
    """Lý do loại trừ entry khỏi pipeline (None = không loại trừ)."""
    for rx, reason in _EXCLUDE_RES:
        if rx.search(path):
            return reason
    return None


def head_text_ratio(head: bytes) -> float:
    """Tỉ lệ byte in được (ASCII + UTF-8 hợp lệ) trong mẫu đầu file."""
    if not head:
        return 0.0
    printable = sum(1 for b in head if 0x20 <= b <= 0x7E or b in (0x09, 0x0A, 0x0D))
    return printable / float(len(head))


def score_text_likelihood(path: str, head: bytes, size: int) -> float:
    """Điểm 0..1: khả năng entry chứa text localization (magic + đuôi + mẫu byte)."""
    low = path.lower()
    ext = os.path.splitext(low)[1]
    if ext in LOC_EXTENSIONS:
        score = 0.85
    elif ext in UNITY_EXTENSIONS:
        score = 0.5
    elif ext in CONTAINER_EXTENSIONS:
        score = 0.0
    else:
        score = 0.3
    if head[:7] == b"UnityFS":
        return 0.0
    if head.startswith(b"SQLite format 3"):
        score = max(score, 0.9)
    elif head.lstrip(b"\xef\xbb\xbf \t\r\n")[:1] in (b"{", b"["):
        score = max(score, 0.9)
    elif head.startswith((b"bplist00", b"<?xml")):
        score = max(score, 0.9)
    elif head.startswith(b"msgid") or head.startswith(b"\xde\x12\x04\x95") or head.startswith(b"\x95\x04\x12\xde"):
        score = max(score, 0.9)
    elif head_text_ratio(head) >= 0.9 and size > 0:
        score = max(score, 0.7)
    return min(1.0, score)


@dataclass
class InventoryEntry:
    """Một entry trong inventory (đặc tả 7.2 bước 1)."""
    path: str                       # vpath đầy đủ ('a.zip::b/c.json')
    container_stack: List[str] = field(default_factory=list)
    rel_path: str = ""
    size: int = 0
    kind: str = "file"              # file | container | unity | excluded
    text_score: float = 0.0
    excluded_reason: Optional[str] = None


def _read_head(vfs: VFS, path: str) -> bytes:
    """Đọc tối đa 64 byte đầu (dùng slice để khỏi giải nén cả entry)."""
    try:
        return bytes(vfs.slice(path, 0, 64))
    except Exception:
        try:
            return bytes(vfs.read(path, max_bytes=64))
        except Exception:
            return b""


def _is_container(head: bytes, path: str) -> bool:
    if os.path.splitext(path.lower())[1] in CONTAINER_EXTENSIONS:
        return True
    kind = detect_container_kind(head)
    return kind in ("zip", "tar", "gzip", "xz", "bzip2")


def inventory(vfs: VFS, limits, *, prefix: str = "", depth: int = 0,
              _seen: Optional[set] = None,
              on_entry: Optional[Callable[[int, str], None]] = None) -> List[InventoryEntry]:
    """Liệt kê entry đệ quy qua VFS (container lồng dùng '::').

    - Entry container -> mở bằng open_vfs, liệt kê tiếp (tối đa
      limits.max_depth; vượt -> E_ARCHIVE_BOMB).
    - Entry Unity (.assets/.unity3d/.bundle/UnityFS) -> kind 'unity' (pipeline
      mở rộng thành TextAsset ảo ở bước sau, không liệt kê như container).
    - 7z -> kind 'excluded', lý do rõ ràng (chưa có backend).
    - Tổng số entry vượt limits.max_entries -> E_ARCHIVE_BOMB.
    - on_entry(đã_liệt_kê, tên_entry): callback sau mỗi entry (kể cả entry
      trong container lồng — tổng số cuối cùng chưa biết trước nên chỉ báo
      SỐ LƯỢNG thật, không báo %; tầng trên hiện "đã liệt kê N entry")."""
    from core.limits import Limits as _Limits
    lim = limits or _Limits()
    seen = _seen if _seen is not None else set()
    out: List[InventoryEntry] = []
    _done = [0]

    def _tick(name: str) -> None:
        if on_entry is not None:
            _done[0] += 1
            on_entry(_done[0], name)

    try:
        names = list(vfs.iter_entries())
    except PB3Error:
        raise
    except Exception as exc:
        raise PB3Error("E_BAD_ARCHIVE", "không liệt kê được container: %s" % type(exc).__name__)
    for name in names:
        full = prefix + name
        if full in seen:
            continue
        seen.add(full)
        lim.check_entries(len(seen))
        reason = exclusion_reason(name)
        if reason is not None:
            out.append(InventoryEntry(full, [], name, 0, "excluded", 0.0, reason))
            _tick(full)
            continue
        try:
            size = vfs.stat(name).size
        except Exception:
            size = 0
        head = _read_head(vfs, name)
        low = name.lower()
        if head[:6] == b"7z\xbc\xaf\x27\x1c":
            out.append(InventoryEntry(full, [], name, size, "excluded", 0.0,
                                      "7z chưa có backend (E_UNSUPPORTED_FORMAT)"))
            _tick(full)
            continue
        if low.endswith(_UNITY_EXTS_TUPLE) or head[:7] == b"UnityFS":
            out.append(InventoryEntry(full, [], name, size, "unity",
                                      score_text_likelihood(name, head, size)))
            _tick(full)
            continue
        if _is_container(head, name):
            out.append(InventoryEntry(full, [], name, size, "container", 0.0))
            _tick(full)
            if depth >= lim.max_depth:
                raise PB3Error("E_ARCHIVE_BOMB",
                               "container lồng %d tầng > giới hạn %d" % (depth + 1, lim.max_depth),
                               where=full)
            try:
                data = bytes(vfs.read(name, None))
            except PB3Error:
                continue
            try:
                child = open_vfs(data, limits=lim, source_name=name)
            except PB3Error:
                continue
            # Đệ quy dùng chung bộ đếm của tầng cha (qua _tick): số lượng báo
            # lên là tổng lũy kế mọi tầng, đơn điệu tăng, không đếm lại từ 1.
            _child_tick = (lambda _d, _n: _tick(_n)) if on_entry is not None else None
            out.extend(inventory(child, lim, prefix=full + "::", depth=depth + 1, _seen=seen,
                                 on_entry=_child_tick))
            continue
        out.append(InventoryEntry(full, [], name, size, "file",
                                  score_text_likelihood(name, head, size)))
        _tick(full)
    return out


# ------------------------------------------------------------------ manifest/index
_REF_EXTS = frozenset([
    ".json", ".csv", ".tsv", ".xml", ".strings", ".plist", ".po",
    ".xlf", ".xliff", ".resx", ".properties", ".yaml", ".yml", ".ini",
])


def find_referenced_paths(vfs: VFS, data: bytes, path: str) -> List[str]:
    """Đường dẫn localization mà file JSON index/catalog tham chiếu tới.

    Chỉ đọc file JSON nhỏ (<= 1MB); giá trị chuỗi là đường dẫn tương đối
    có đuôi localization và tồn tại trong VFS thì được theo dõi (đặc tả 7.3
    quan hệ index <-> data). File không phải JSON / parse lỗi -> []."""
    if len(data) > 1024 * 1024:
        return []
    try:
        doc = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return []
    refs: List[str] = []
    # Thư mục gốc của file đang xét (không đổi trong cả lần duyệt): tính một
    # lần thay vì os.path.dirname() cho mỗi chuỗi con tìm được.
    base = os.path.dirname(path).replace("\\", "/")
    # Độ dài đuôi localization đã biết (".po"=3 .. ".properties"=11): chuỗi
    # không có dấu chấm ở thành phần cuối, hoặc đuôi dài ngoài khoảng này,
    # thì chắc chắn không phải đường dẫn localization — khỏi splitext/lower.
    _MIN_EXT = 3
    _MAX_EXT = 11

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            for v in node.values():
                _walk(v)
        elif isinstance(node, list):
            for v in node:
                _walk(v)
        elif isinstance(node, str):
            cand = node.strip()
            if "\\" in cand:
                cand = cand.replace("\\", "/")
            dot = cand.rfind(".")
            slash = cand.rfind("/")
            # Dấu chấm phải nằm TRONG tên file (sau dấu "/" cuối và không ở
            # đầu tên: ".json" theo splitext có đuôi rỗng, không phải ".json").
            if dot <= slash + 1:
                return
            ext_len = len(cand) - dot
            if ext_len < _MIN_EXT or ext_len > _MAX_EXT:
                return
            if cand[dot:].lower() not in _REF_EXTS:
                return
            if ".." in cand.split("/"):
                return
            joined = (base + "/" + cand).lstrip("/") if base else cand
            for trial in (joined, cand):
                try:
                    if vfs.exists(trial):
                        refs.append(trial)
                        break
                except Exception:
                    pass

    _walk(doc)
    return sorted(set(refs))


# ------------------------------------------------------------------ VFS thư mục
class DirVFS(VFS):
    """VFS chỉ đọc trên một thư mục đã giải nén trên đĩa (đặc tả 7.1).

    Entry = đường dẫn tương đối kiểu POSIX, sắp xếp ổn định. Không bao giờ
    ghi; đường dẫn ra ngoài thư mục gốc -> E_UNSAFE_PATH."""

    def __init__(self, root: str, limits=None):
        from core.limits import Limits as _Limits
        self._root = os.path.abspath(str(root))
        self.limits = limits or _Limits()
        if not os.path.isdir(self._root):
            raise PB3Error("E_INPUT_NOT_FOUND", "không phải thư mục: %s" % self._root,
                           where=self._root)

    def _resolve(self, path: str) -> str:
        rel = check_vpath(path)
        if rel in ("", "."):
            raise PB3Error("E_UNSAFE_PATH", "đường dẫn rỗng", where=path)
        target = os.path.abspath(os.path.join(self._root, *rel.split("/")))
        if target != self._root and not target.startswith(self._root + os.sep):
            raise PB3Error("E_UNSAFE_PATH", "đường dẫn ra ngoài thư mục gốc", where=path)
        return target

    def iter_entries(self, prefix: Optional[str] = None):
        found = []
        for dirpath, _dirnames, filenames in os.walk(self._root):
            for fn in filenames:
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, self._root).replace(os.sep, "/")
                found.append(rel)
        found.sort()
        for rel in found:
            if prefix is None or rel.startswith(prefix):
                yield rel

    def stat(self, path: str) -> VStat:
        target = self._resolve(path)
        try:
            st = os.stat(target)
        except OSError as exc:
            raise PB3Error("E_INPUT_NOT_FOUND", "không có entry: %s" % path,
                           where=path) from exc
        return VStat(path=path, size=st.st_size)

    def read(self, path: str, max_bytes: Optional[int] = None) -> bytes:
        target = self._resolve(path)
        size = os.path.getsize(target)
        n = read_policy(size, max_bytes, self.limits, path)
        with open(target, "rb") as fh:
            return fh.read(n)

    def slice(self, path: str, offset: int, size: int) -> bytes:
        target = self._resolve(path)
        with open(target, "rb") as fh:
            fh.seek(offset)
            return fh.read(size)

    def open_stream(self, path: str):
        target = self._resolve(path)
        return open(target, "rb")

    def exists(self, path: str) -> bool:
        try:
            return os.path.isfile(self._resolve(path))
        except PB3Error:
            return False


def open_source_vfs(source, limits):
    """Mở đầu vào trích xuất thành VFS chỉ đọc.

    - Thư mục -> DirVFS; file container (ZIP/IPA/TAR/GZ/XZ) -> open_vfs;
      file đơn lẻ không phải container -> MemoryVFS một entry (tên file);
      bytes -> open_vfs (container) hoặc MemoryVFS một entry."""
    from sdk.vfs import MemoryVFS
    if isinstance(source, (bytes, bytearray)):
        data = bytes(source)
        try:
            return open_vfs(data, limits=limits, source_name="<bytes>")
        except PB3Error as exc:
            if exc.code != "E_UNSUPPORTED_FORMAT":
                raise
            return MemoryVFS({"<bytes>": data}, limits=limits)
    path = str(source)
    if os.path.isdir(path):
        return DirVFS(path, limits=limits)
    if not os.path.isfile(path):
        raise PB3Error("E_INPUT_NOT_FOUND", "không có file/thư mục: %s" % path, where=path)
    with open(path, "rb") as fh:
        head = fh.read(2048)
    kind = detect_container_kind(head)
    if kind in ("zip", "tar", "gzip", "xz", "bzip2"):
        return open_vfs(path, limits=limits, source_name=os.path.basename(path))
    if kind == "7z":
        raise PB3Error("E_UNSUPPORTED_FORMAT", "7z chưa có backend", where=path)
    # File UnityFS độc lập vượt giới hạn entry (vd data.unity3d 590MB): VFS
    # lười theo đường dẫn, KHÔNG nạp cả file vào RAM (trước đây đọc trọn
    # file vào MemoryVFS rồi _build_layers mới skip vì >64MB — 590MB đó
    # cộng với bản copy trong FS trình duyệt đủ làm chết tab). Tầng trích
    # xuất đọc từng entry qua stream_bundle_entries.
    if head[:7] == b"UnityFS" and os.path.getsize(path) > limits.max_entry_bytes:
        from sdk.vfs import SingleFileVFS
        return SingleFileVFS(path, limits=limits)
    with open(path, "rb") as fh:
        data = fh.read()
    return MemoryVFS({os.path.basename(path): data}, limits=limits)


# ------------------------------------------------- nhận diện engine game (v64)
# Tab Tổng quan: khi mở 1 file IPA phải hiện game thuộc engine nào, chuẩn
# xác nhất. Khác với detect_engine() ở trên (gợi ý 6 engine cho pipeline
# trích xuất, evidence kiểu Evidence): hàm này phân biệt rõ 4 loại Duy
# cần — Unity IL2CPP, Unity Mono (Unity 3D), Unreal Engine, Native iOS —
# bằng ĐA TÍN HIỆU có liệt kê bằng chứng từng tín hiệu (file/marker nào),
# và trả Unknown kèm lý do khi không đủ tín hiệu (fail-closed, KHÔNG đoán).
#
# Nguyên tắc chấm (đo trên 7 game Unity + DST/Dead Cells/Carrion/Fortune
# Mill/Balatro local, xem pb3/tests/test_engine_detect_overview.py):
# - Mỗi tín hiệu chỉ tính 1 lần dù lặp lại nhiều file (vd 5 file .pak của
#   Dead Cells chỉ cho 0.30 một lần, không cộng dồn).
# - Magic là bằng chứng mạnh nhất (1.00): IL2CPP = magic 0xFAB11BAF đầu
#   global-metadata.dat; Unreal = magic .locres 16 byte hoặc magic footer
#   .pak 0x5A6F12E1; Mach-O = 4 byte đầu file thực thi .app.
# - Unity IL2CPP BẮT BUỘC có dấu IL2CPP (magic hoặc tên global-metadata.dat);
#   Unity Mono BẮT BUỘC có dấu Mono (DLL trong Data/Managed / libmono /
#   Assembly-CSharp.dll) và KHÔNG có dấu IL2CPP.
# - .pak riêng lẻ KHÔNG chứng minh Unreal: Dead Cells (Heaps) cũng dùng
#   .pak — đầu file Heaps có magic "PAK" (+ version ≤ 16) ở offset 0 hoặc
#   64 (sau 64 ký tự hex), xem sdk/heaps.py _header_at; khi phát hiện
#   Heaps thì bằng chứng tên .pak bị loại và Unreal không được chấm.
# - Native iOS KHÔNG dùng Assets.car/.lproj/.strings/.storyboardc
#   LaunchScreen/.nib làm bằng chứng: đo thật thấy CẢ 7 game Unity đều
#   có đủ các thứ đó (chỉ là mẫu Xcode). Native CHẮC CHẮN cần 1 storyboard
#   KHÔNG phải LaunchScreen (vd Main.storyboardc) + cấu trúc .app +
#   Mach-O + không dấu Unity/Unreal; mức CÓ KHẢ NĂNG chỉ khi có cấu trúc
#   .app + Mach-O + không dấu Unity/Unreal + không có cây data/ game
#   lớn (DST có data/ 8.449 entry + 1.352 file .tex → chặn mức này).
# - Thắng khi điểm ≥ 0.50 và hơn engine thứ 2 ít nhất 0.20 (hoặc engine
#   thứ 2 < 0.50); 2 engine cùng ≥ 0.50 và chênh < 0.20 → Unknown (mâu
#   thuẫn). Mức: ≥ 0.80 CHẮC CHẮN, ≥ 0.50 CÓ KHẢ NĂNG.

ENGINE_LABELS = {
    "unity-il2cpp": "Unity IL2CPP",
    "unity-mono": "Unity Mono (Unity 3D)",
    "unreal": "Unreal Engine",
    "native-ios": "Native iOS",
    "unknown": "Chưa xác định",
}

_IL2CPP_MAGIC = bytes.fromhex("af1bb1fa")  # 0xFAB11BAF little-endian
_LOCRES_MAGIC = LOCRES_MAGIC  # 16 byte đầu file .locres (Compact+)
_MACHO_MAGICS = (
    bytes.fromhex("cffaedfe"),  # MH_MAGIC_64 LE
    bytes.fromhex("feedface"),  # MH_MAGIC LE
    bytes.fromhex("feedfacf"),  # MH_MAGIC_64 BE
    bytes.fromhex("cafebabe"),  # FAT_MAGIC BE
    bytes.fromhex("cafebabf"),  # FAT_MAGIC LE
)
_HEX_BYTES = frozenset(b"0123456789abcdefABCDEF")
_DATA_TREE_MIN = 100  # số entry tối thiểu dưới <app>/data/ để coi là cây game


def _game_level(score: float) -> str:
    if score >= 0.8:
        return "CHẮC CHẮN"
    if score >= 0.5:
        return "CÓ KHẢ NĂNG"
    return "CHƯA ĐỦ BẰNG CHỨNG"


def _is_heaps_pak_head(head: bytes) -> bool:
    """Đầu file có phải Heaps PAK không (bản rút gọn của sdk.heaps:
    magic "PAK" + version ≤ 16 + header_size/data_size hợp lệ, ở offset
    0 hoặc offset 64 sau 64 ký tự hex ASCII — layout Dead Cells)."""
    if not head:
        return False
    for start in (0, 64):
        if len(head) < start + 12 or head[start:start + 3] != b"PAK":
            continue
        if start == 64 and not all(b in _HEX_BYTES for b in head[:64]):
            continue
        if head[start + 3] > 16:
            continue
        header_size = struct.unpack_from("<I", head, start + 4)[0]
        data_size = struct.unpack_from("<I", head, start + 8)[0]
        if header_size < 12 or header_size > len(head):
            continue
        if start + header_size + data_size > len(head):
            continue
        return True
    return False


def detect_game_engine(names, heads=None, footers=None):
    """Nhận diện engine game từ tên entry + byte đầu/cuối một số entry.

    names: danh sách tên entry (vd namelist của IPA). heads: {tên entry:
    byte đầu} — dùng để kiểm magic (global-metadata.dat 4 byte,
    .locres 16 byte, file thực thi 4 byte, .pak ≥ 76 byte để thấy Heaps
    ở offset 64, .pck 4 byte). footers: {tên entry .pak: byte cuối}
    — dùng để tìm magic footer Unreal 0x5A6F12E1. Thiếu heads/footers
    thì chỉ chấm bằng tên (yếu hơn, fail-closed).

    Trả dict JSON-được: {engine, label, level, confidence, evidence
    [{kind, value, weight, reason}], candidates [{engine, label, level,
    confidence, evidence [reason]}], reason, other_hints [str],
    total_entries}. evidence chỉ tính mỗi tín hiệu 1 lần.
    """
    names = [str(n) for n in (names or [])]
    lower = [n.lower() for n in names]
    heads = heads or {}
    footers = footers or {}
    heads_low = {str(k).lower(): v for k, v in heads.items()}
    footers_low = {str(k).lower(): v for k, v in footers.items()}

    def _find(pred):
        for i, n in enumerate(lower):
            if pred(n):
                return names[i]
        return None

    def _has(pred):
        return _find(pred) is not None

    def _count(pred):
        return sum(1 for n in lower if pred(n))

    def _head_of(pred):
        for k, v in heads_low.items():
            if pred(k):
                return v
        return None

    # --- bằng chứng (khử trùng theo (kind, value)) ---
    seen = set()
    all_ev = []  # mọi bằng chứng tìm thấy, theo thứ tự phát hiện

    def _ev(kind, value, weight, reason):
        key = (kind, value)
        if key in seen:
            return None
        seen.add(key)
        ev = {"kind": kind, "value": value, "weight": float(weight),
              "reason": reason}
        all_ev.append(ev)
        return ev

    # --- dấu hiệu engine khác (chỉ để ghi chú / chặn, không phải ứng viên) ---
    other_hints = []
    pck_head = _head_of(lambda k: k.endswith(".pck"))
    if pck_head is not None and pck_head[:4] == GODOT_PCK_MAGIC:
        other_hints.append("Godot (.pck có magic GDPC)")
    elif _has(lambda n: n.endswith(".pck")):
        other_hints.append("có file .pck (nghi Godot, chưa đọc được magic)")
    pak_heads = {k: v for k, v in heads_low.items() if k.endswith(".pak")}
    heaps_pak = None
    for k, v in pak_heads.items():
        if _is_heaps_pak_head(v):
            heaps_pak = k
            break
    if heaps_pak is not None:
        other_hints.append("Heaps (file .pak có magic PAK ở đầu, không "
                           "phải Unreal)")
    if _has(lambda n: "monogame.framework.dll" in n):
        other_hints.append("MonoGame (MonoGame.Framework.dll)")
    if _has(lambda n: n.endswith("game.love")):
        other_hints.append("LÖVE (game.love)")

    # cây data/ lớn trong .app (DST: 8.449 entry) — dấu hiệu game dùng
    # engine riêng, chặn mức Native yếu. Đếm PHÂN BIỆT hoa/thường:
    # thư mục Data/ (hoa D) là của Unity, không tính; chỉ data/ (thường)
    # kiểu Klei mới tính.
    app_prefix = None
    app_prefix_orig = None
    for n in names:
        m = re.match(r"^(payload/[^/]+\.app/|[^/]+\.app/)", n, re.I)
        if m:
            app_prefix = m.group(1).lower()
            app_prefix_orig = m.group(1)
            break
    data_tree = 0
    if app_prefix_orig:
        data_tree = sum(1 for n in names
                        if n.startswith(app_prefix_orig + "data/"))
    if data_tree >= _DATA_TREE_MIN:
        other_hints.append("engine khác (thư mục data/ có %d entry)" % data_tree)

    # --- Unity ---
    unity_ev = []

    def _u(kind, value, weight, reason):
        ev = _ev(kind, value, weight, reason)
        if ev is not None:
            unity_ev.append(ev)
        return ev

    gmd_path = _find(lambda n: n.endswith("global-metadata.dat"))
    gmd_magic = False
    gmd_ev = None
    if gmd_path is not None:
        head = heads_low.get(gmd_path.lower())
        if head is not None and head[:4] == _IL2CPP_MAGIC:
            gmd_magic = True
            gmd_ev = _ev("magic", "il2cpp-metadata", 1.0,
                         "global-metadata.dat có magic IL2CPP 0xFAB11BAF "
                         "(%s)" % gmd_path)
        else:
            gmd_ev = _ev("marker", "global-metadata.dat", 0.5,
                         "có file global-metadata.dat (metadata IL2CPP) "
                         "(%s)" % gmd_path)
    if _has(lambda n: "unityframework" in n):
        _u("marker", "UnityFramework", 0.4,
           "có UnityFramework (%s)"
           % _find(lambda n: "unityframework" in n))
    if _has(lambda n: "globalgamemanagers" in n):
        _u("marker", "globalgamemanagers", 0.3,
           "có file globalgamemanagers")
    if _has(lambda n: n.endswith("resources.assets")):
        _u("marker", "resources.assets", 0.3, "có file resources.assets")
    if _has(lambda n: ".unity3d" in n):
        _u("marker", ".unity3d", 0.2, "có bundle .unity3d")
    if _has(lambda n: n.endswith(".assets")):
        _u("name", ".assets", 0.15, "có file .assets")
    if _has(lambda n: "sharedassets" in n):
        _u("name", "sharedassets", 0.15, "có file sharedassets")
    if _has(lambda n: "data/managed" in n):
        _u("name", "Data/Managed", 0.2, "có thư mục Data/Managed")
    mono_ev = []
    if _has(lambda n: n.endswith("assembly-csharp.dll")):
        ev = _ev("marker", "Assembly-CSharp.dll", 0.6,
                 "có Assembly-CSharp.dll trong Data/Managed")
        if ev is not None:
            mono_ev.append(ev)
    if _has(lambda n: "data/managed" in n and n.endswith(".dll")):
        ev = _ev("marker", "managed-dll", 0.5,
                 "có file .dll trong Data/Managed (%s)"
                 % _find(lambda n: "data/managed" in n and n.endswith(".dll")))
        if ev is not None:
            mono_ev.append(ev)
    if _has(lambda n: "libmono" in n):
        ev = _ev("marker", "libmono", 0.3,
                 "có libmono (%s)" % _find(lambda n: "libmono" in n))
        if ev is not None:
            mono_ev.append(ev)

    unity_base = sum(e["weight"] for e in unity_ev)
    mono_extra = sum(e["weight"] for e in mono_ev)
    gmd_weight = gmd_ev["weight"] if gmd_ev is not None else 0.0
    # IL2CPP = dấu Unity chung + dấu global-metadata.dat (tính 1 lần);
    # Mono = dấu Unity chung + dấu Mono, chỉ khi KHÔNG có dấu IL2CPP.
    il2cpp_score = (unity_base + gmd_weight) if gmd_ev is not None else 0.0
    mono_score = (unity_base + mono_extra) if (mono_extra > 0
                                               and gmd_ev is None) else 0.0
    il2cpp_ev = ([gmd_ev] if gmd_ev is not None else []) + unity_ev

    # --- Unreal ---
    unreal_ev = []

    def _r(kind, value, weight, reason):
        ev = _ev(kind, value, weight, reason)
        if ev is not None:
            unreal_ev.append(ev)
        return ev

    locres_path = _find(lambda n: n.endswith(".locres"))
    if locres_path is not None:
        head = heads_low.get(locres_path.lower())
        if head is not None and head[:16] == _LOCRES_MAGIC:
            _r("magic", "locres", 1.0,
               "file .locres có magic Unreal 16 byte (%s)" % locres_path)
    pak_footer_path = None
    for k, v in footers_low.items():
        if k.endswith(".pak") and UNREAL_PAK_MAGIC in bytes(v):
            pak_footer_path = k
            break
    if pak_footer_path is not None:
        _r("magic", "pak-footer", 1.0,
           "file .pak có magic footer Unreal 0x5A6F12E1 (%s)"
           % pak_footer_path)
    if _has(lambda n: n.endswith(".uproject")):
        _r("marker", ".uproject", 0.7,
           "có file .uproject (%s)" % _find(lambda n: n.endswith(".uproject")))
    if _has(lambda n: n.endswith(".locmeta")):
        _r("marker", ".locmeta", 0.5,
           "có file .locmeta (%s)" % _find(lambda n: n.endswith(".locmeta")))
    if _has(lambda n: n.endswith(".utoc")):
        _r("marker", ".utoc", 0.6,
           "có file .utoc (IoStore) (%s)" % _find(lambda n: n.endswith(".utoc")))
    if _has(lambda n: n.endswith(".ucas")):
        _r("marker", ".ucas", 0.6,
           "có file .ucas (IoStore) (%s)" % _find(lambda n: n.endswith(".ucas")))
    # Tên .pak chỉ tính khi KHÔNG phải Heaps (Heaps cũng dùng .pak)
    if _has(lambda n: n.endswith(".pak")) and heaps_pak is None:
        _r("name", ".pak", 0.3, "có file .pak")
    unreal_score = sum(e["weight"] for e in unreal_ev)

    # --- Native iOS ---
    native_ev = []
    info_path = None
    if app_prefix:
        info_path = _find(lambda n, p=app_prefix: n == p + "info.plist")
    has_info = info_path is not None
    if has_info:
        ev = _ev("structure", "app-info-plist", 0.3,
                 "có cấu trúc .app (Info.plist ở %s)" % info_path)
        if ev is not None:
            native_ev.append(ev)
    macho_path = None
    for k, v in heads_low.items():
        if v[:4] in _MACHO_MAGICS and app_prefix \
                and k.startswith(app_prefix) \
                and "/" not in k[len(app_prefix):]:
            macho_path = _find(lambda n, kk=k: n == kk)
            break
    if macho_path is not None:
        ev = _ev("magic", "macho-exec", 0.4,
                 "file thực thi .app có magic Mach-O (%s)" % macho_path)
        if ev is not None:
            native_ev.append(ev)
    storyboard = _find(lambda n: ".storyboardc" in n
                       and "launch" not in n.lower())
    if storyboard is not None and app_prefix \
            and storyboard.lower().startswith(app_prefix):
        ev = _ev("marker", "main-storyboardc", 0.5,
                 "có storyboard giao diện không phải LaunchScreen (%s)"
                 % storyboard)
        if ev is not None:
            native_ev.append(ev)
    native_blocked = (data_tree >= _DATA_TREE_MIN) or bool(
        [h for h in other_hints if h.startswith(("Godot", "Heaps",
                                                 "MonoGame", "LÖVE"))])
    native_score = 0.0
    if has_info and not native_blocked \
            and il2cpp_score < 0.5 and mono_score < 0.5 \
            and unreal_score < 0.5:
        native_score = sum(e["weight"] for e in native_ev)

    scores = {
        "unity-il2cpp": (il2cpp_score, il2cpp_ev),
        "unity-mono": (mono_score, unity_ev + mono_ev),
        "unreal": (unreal_score, unreal_ev),
        "native-ios": (native_score, native_ev),
    }
    ranked = sorted(scores.items(), key=lambda kv: -kv[1][0])
    top_eng, (top_score, top_ev) = ranked[0]
    second_score = ranked[1][1][0]

    def _cand(eng):
        sc, evs = scores[eng]
        return {"engine": eng, "label": ENGINE_LABELS[eng],
                "level": _game_level(sc), "confidence": round(min(1.0, sc), 2),
                "evidence": [e["reason"] for e in evs]}

    if top_score >= 0.5 and (second_score < 0.5
                             or top_score - second_score >= 0.2):
        level = _game_level(top_score)
        if top_eng == "unity-il2cpp":
            reason = ("Tìm thấy dấu IL2CPP (%s) + dấu Unity."
                      % ("magic 0xFAB11BAF" if gmd_magic
                         else "global-metadata.dat"))
        elif top_eng == "unity-mono":
            reason = ("Có dấu Unity + file DLL/libmono trong Data/Managed, "
                      "không có global-metadata.dat (IL2CPP).")
        elif top_eng == "unreal":
            reason = ("Tìm thấy dấu Unreal (%s)."
                      % ("magic .locres/footer .pak"
                         if any(e["kind"] == "magic" for e in top_ev)
                         else "tên file .uproject/.locmeta/.utoc/.ucas"))
        else:
            strong = any(e["value"] == "main-storyboardc" for e in top_ev)
            reason = ("Có cấu trúc .app + file thực thi Mach-O, không có "
                      "dấu Unity/Unreal" + (" + storyboard giao diện."
                                            if strong else
                                            " (chưa có bằng chứng Native "
                                            "dương tính mạnh — chỉ ở mức "
                                            "có khả năng)."))
        return {"engine": top_eng, "label": ENGINE_LABELS[top_eng],
                "level": level, "confidence": round(min(1.0, top_score), 2),
                "evidence": top_ev,
                "candidates": [_cand(e) for e, (s, _) in ranked[1:]
                               if s > 0],
                "reason": reason, "other_hints": other_hints,
                "total_entries": len(names)}

    # Unknown: không đủ tín hiệu, hoặc 2 engine mâu thuẫn
    found = "; ".join(e["reason"] for e in all_ev[:6]) or \
        "không tìm thấy dấu hiệu engine nào"
    if top_score >= 0.5:
        reason = ("Mâu thuẫn: vừa có dấu %s (%.2f) vừa có dấu %s (%.2f) "
                  "— không dám kết luận."
                  % (ENGINE_LABELS[top_eng], top_score,
                     ENGINE_LABELS[ranked[1][0]], second_score))
    else:
        reason = ("Không đủ bằng chứng để kết luận engine. Đã tìm thấy: "
                  "%s." % found)
        if other_hints:
            reason += " Ghi chú: %s." % "; ".join(other_hints)
    return {"engine": "unknown", "label": ENGINE_LABELS["unknown"],
            "level": "CHƯA ĐỦ BẰNG CHỨNG", "confidence": 0.0,
            "evidence": all_ev,
            "candidates": [_cand(e) for e, (s, _) in ranked if s > 0],
            "reason": reason, "other_hints": other_hints,
            "total_entries": len(names)}
