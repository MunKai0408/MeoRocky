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
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.errors import PB3Error
from core.model import Evidence, confidence as _confidence
from sdk.containers import GODOT_PCK_MAGIC, detect_container_kind, open_vfs
from sdk.types import Evidence as _Ev  # noqa: F401  (xuất lại cho pipeline)
from sdk.vfs import VFS, VStat, check_vpath, read_policy

# ------------------------------------------------------------------ locale
# Bảng locale mặc định (preset bắt buộc en/zh theo đặc tả 2 + 7.4; các
# locale khác mở rộng được). Thêm locale mới = thêm mục vào
# config/locales.toml, không sửa code (xem load_locale_table).
LOCALE_BUILTIN: List[Dict[str, Any]] = [
    {"code": "en", "aliases": ["en-us", "en-gb", "en_us", "english", "eng"],
     "display": "English", "script": "Latn"},
    {"code": "zh", "aliases": ["zh-cn", "zh-tw", "zh_cn", "chinese", "中文", "mandarin"],
     "display": "Chinese", "script": "Hans"},
    {"code": "zh-Hans", "aliases": ["zh-hans", "zh_cn", "简体中文", "simplified chinese"],
     "display": "Chinese (Simplified)", "script": "Hans"},
    {"code": "zh-Hant", "aliases": ["zh-hant", "zh-tw", "zh_hk", "繁體中文", "traditional chinese"],
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
    with open(path, "rb") as fh:
        data = fh.read()
    return MemoryVFS({os.path.basename(path): data}, limits=limits)
