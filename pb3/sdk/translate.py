"""Dịch tự động text đã trích xuất (Google Translate + từ điển offline).

Chỉ dịch VALUE, không dịch KEY (bảng JSON key-value từ `extract`).
Placeholder ({0}, {name}, %s, %1$s), thẻ định dạng (<b>, <color>...) và ký
tự xuống dòng được che (mask) trước khi gửi, khôi phục sau khi dịch; dịch
xong kiểm lại placeholder/xuống dòng, lệch thì cảnh báo + không cache.

Credit 'Việt Hoá Bởi Bear' (bổ sung sau Claude 08): sau khi dịch xong,
gắn vào 1 entry tiêu đề (ưu tiên) hoặc version/copyright trong bảng đã
dịch — không thêm key mới (apply_edits bỏ qua key lạ), không đi vào cache;
tắt bằng credit=False / cờ --no-credit; không có chỗ phù hợp thì báo rõ
(credit_key=None), không lặng lẽ bỏ qua.

Provider Google Translate: endpoint miễn phí translate.googleapis.com
(client=gtx, KHÔNG key, cần mạng). Thực nghiệm 2026-11-16: endpoint chỉ
dịch đúng 1 tham số q mỗi request (q thứ hai bị bỏ), và giới hạn tốc độ
rất gắt (2 request liên tiếp đã 429) — nên mỗi câu là 1 request, cách nhau
`gap_s`, retry 429/5xx với backoff. Chỉ thư viện chuẩn (urllib).

Ngoại lệ kiến trúc (decision #151): đây là nơi DUY NHẤT trong dự án được
gọi mạng; test_rules_ish_only cho phép riêng file này import urllib và
dùng đúng host translate.googleapis.com. Mọi file khác vẫn cấm mạng.

Từ điển offline dự phòng: cụm từ game thông dụng, khớp chính xác cả cụm
(không dịch từng từ); câu không có trong từ điển thì giữ nguyên + đánh dấu
cần dịch tay. Cache: câu đã dịch rồi không dịch lại (khóa sha256 text +
cặp ngôn ngữ), lưu workspace/cache/translation_cache.json; lỗi đọc/ghi
cache không làm hỏng dịch.

Chỉ thư viện chuẩn, Python >= 3.8. Test KHÔNG gọi mạng thật: truyền
`urlopen` giả vào GoogleTranslateProvider hoặc dùng FakeProvider.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from core import safe_io
from core.errors import PB3Error
from sdk import exporters as exp

# Chỉ dùng cho chú thích kiểu (tránh import vòng): GameGlossary ở
# sdk/glossary.py, không import gì từ translate.
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from sdk.glossary import GameGlossary  # noqa: F401

GT_ENDPOINT = "https://translate.googleapis.com/translate_a/single"
GT_USER_AGENT = "Mozilla/5.0"
CREDIT_TEXT = "Việt Hoá Bởi Bear"
CREDIT_SEPARATOR = " — "
# Key ứng viên gắn credit, theo thứ tự ưu tiên: tiêu đề trước, rồi
# version/copyright. Chỉ khớp theo tên key (không đoán theo giá trị);
# key chứa "title"/"copyright"/"version"/"credits" cũng tính.
_TITLE_KEYS = {"title", "game_title", "app_name", "game_name", "main_title",
               "title_text", "app_title", "title_screen"}
_VERSION_KEYS = {"version", "copyright", "credits", "about", "copyright_text",
                 "version_text", "credit", "credits_text"}
CACHE_VERSION = 1
CACHE_FILENAME = "translation_cache.json"
DEFAULT_TIMEOUT_S = 20.0
DEFAULT_MAX_RETRIES = 4
DEFAULT_GAP_S = 1.0
DEFAULT_BACKOFF_S = (1.0, 2.0, 4.0, 8.0)
DEFAULT_CHUNK = 10

_LANG_RE = re.compile(r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$")

# Placeholder giữ nguyên khi dịch. GIỮ ĐỒNG BỘ với cổng kiểm tra
# sdk/gate.py::_PLACEHOLDER_RE (chuẩn quyết định có cho ghi ngược) và
# sdk/exporters.py::_PLACEHOLDER_RES: cổng đếm cả $VAR và {nội dung bất
# kỳ} ({foo-bar}, {a.b}, {}) — trước đây mask không che hai dạng này nên
# máy dịch nhìn thấy chúng như chữ thường và làm mất (Build 68: {VAR1},
# {KINGCOLOR}). Thẻ đóng (</b>) vẫn che thêm (siêu tập của cổng, vô hại).
_PLACEHOLDER_RE = re.compile(
    r"\{[^}]*\}|%(\d+\$)?[sdif]|\$[A-Za-z_][A-Za-z0-9_]*|</?[A-Za-z][^>]*>|\\[nrt\\'\"]")
# Thẻ đóng (</b>, </size>) cũng phải che: trước đây chỉ che thẻ mở nên
# dòng chỉ còn thẻ đóng vẫn có chữ Latin ('size', 'b') và bị coi là
# tiếng Anh yếu rồi gửi đi dịch (fixture Passpartout: dòng rich-text
# CJK/Korean bị đổi). Che cả hai thì dòng chỉ còn thẻ -> không còn chữ
# thật -> bỏ qua.
_TOKEN_FMT = "__PB3PH%d__"
# Máy dịch đôi khi chèn khoảng trắng/chữ thường vào token, hoặc LỘT
# dấu gạch dưới ("PB3PH0", "__PB3PH0", "PB3PH1__"): khớp khoan dung cả
# ba dạng — mỗi bên 0–2 gạch dưới. Khoảng trắng chỉ được nuốt KHI CÓ
# gạch dưới kề bên ("__ PB3PH0 __"), không nuốt khoảng trắng quanh
# token trần (không thì "Unlock PB3PH0 and" mất luôn dấu cách).
_TOKEN_RE = re.compile(r"(?:_{1,2}\s*)?PB3PH\s*(\d+)(?:\s*_{1,2})?",
                       re.IGNORECASE)


def check_lang(code: str, *, allow_auto: bool = False) -> None:
    """Mã ngôn ngữ hợp lệ (ISO như vi, en, zh-CN) hoặc "auto" cho nguồn."""
    if allow_auto and code == "auto":
        return
    if not isinstance(code, str) or not _LANG_RE.match(code):
        raise PB3Error("E_INPUT_NOT_FOUND",
                       "mã ngôn ngữ không hợp lệ: %r (cần dạng ISO như vi, en, zh-CN)"
                       % (code,))


# ------------------------------------------------------------------ mask
def mask_line(line: str) -> Tuple[str, List[str]]:
    """Che placeholder/thẻ trong một dòng thành token __PB3PH<n>__.

    Trả (dòng đã che, danh sách placeholder gốc theo thứ tự)."""
    tokens: List[str] = []

    def _rep(m: "re.Match[str]") -> str:
        tokens.append(m.group(0))
        return _TOKEN_FMT % (len(tokens) - 1)

    return _PLACEHOLDER_RE.sub(_rep, line), tokens


def unmask_line(masked: str, tokens: List[str]) -> str:
    """Khôi phục placeholder từ token (khoan dung khoảng trắng/chữ hoa/gạch dưới bị lột)."""

    def _rep(m: "re.Match[str]") -> str:
        try:
            idx = int(m.group(1))
        except ValueError:
            return m.group(0)
        return tokens[idx] if 0 <= idx < len(tokens) else m.group(0)

    return _TOKEN_RE.sub(_rep, masked)


def _line_has_text(masked: str) -> bool:
    """Dòng còn chữ cần dịch không (bỏ token rồi xem còn ký tự chữ/số)."""
    bare = _TOKEN_RE.sub("", masked)
    return any(ch.isalnum() for ch in bare)


def placeholders_ok(source: str, translated: str) -> bool:
    """Placeholder + số ký tự xuống dòng giữ nguyên sau khi dịch.

    So trực tiếp qua exp.placeholders (cùng phép so như
    exp.check_placeholders nhưng khỏi dựng dict bọc mỗi key — hàm này
    chạy cho từng key đã dịch xong)."""
    if source.count("\n") != translated.count("\n"):
        return False
    return exp.placeholders(source) == exp.placeholders(translated)


def placeholders_in_order(text: str) -> List[str]:
    """Placeholder theo thứ tự xuất hiện (không gộp trùng) — cùng quy tắc
    cổng kiểm tra sdk/gate.py dùng để quyết định có cho ghi ngược không
    (thứ tự + số lần), khác exp.placeholders vốn gộp trùng theo nhóm."""
    return [m.group(0) for m in _PLACEHOLDER_RE.finditer(text or "")]


def placeholders_match_strict(source: str, translated: str) -> bool:
    """Placeholder khớp y như cổng (b): cùng thứ tự + số lần, và cùng số
    lần xuống dòng. Dùng để quyết định một dòng đã dịch có cần dịch lại
    hay không — placeholders_ok chỉ so tập hợp nên không đủ chặt.
    (Mask còn che cả thẻ đóng nên kiểm tra này chặt hơn cổng một chút ở
    thẻ đóng; an toàn vì thẻ đóng đã được che thành token.)"""
    if (source or "").count("\n") != (translated or "").count("\n"):
        return False
    return placeholders_in_order(source) == placeholders_in_order(translated)


# ------------------------------------------------------------------ credit
def find_credit_entry(kv: Dict[str, str]) -> Optional[str]:
    """Chọn 1 entry để gắn credit 'Việt Hoá Bởi Bear'.

    Ưu tiên chuỗi màn hình tiêu đề (key tên game/tiêu đề), rồi chuỗi
    version/copyright; duyệt theo thứ tự key của bảng. Giá trị phải có chữ
    thật (không rỗng, không chỉ placeholder/ký tự đặc biệt). Trả key hoặc
    None khi không có chỗ phù hợp."""

    def _is_title(key: str) -> bool:
        k = key.strip().casefold()
        return k in _TITLE_KEYS or "title" in k

    def _is_version(key: str) -> bool:
        k = key.strip().casefold()
        return k in _VERSION_KEYS or any(
            s in k for s in ("copyright", "version", "credits"))

    for pred in (_is_title, _is_version):
        for key, value in kv.items():
            if not isinstance(value, str) or not value.strip():
                continue
            # Bỏ placeholder/thẻ rồi mới xem còn chữ thật không ("{0}" không tính).
            masked, _tokens = mask_line(value)
            if not _line_has_text(masked):
                continue
            if pred(key):
                return key
    return None


def apply_credit(translated: Dict[str, str]) -> Optional[str]:
    """Gắn credit vào 1 entry đã chọn trong bảng đã dịch (sửa tại chỗ).

    Không thêm key mới: `extract --apply` bỏ qua key lạ nên entry riêng
    không vào được game — chỉ có gắn vào entry sẵn có mới hiện lên màn
    hình game. Lũy đẳng: đã có credit thì không gắn lại. Trả key đã gắn
    hoặc None khi không có chỗ phù hợp."""
    key = find_credit_entry(translated)
    if key is None:
        return None
    value = translated[key]
    if CREDIT_TEXT in value:
        return key
    translated[key] = value + CREDIT_SEPARATOR + CREDIT_TEXT
    return key


@dataclass
class _Segment:
    key: str
    line_no: int
    masked: str
    tokens: List[str]


# ------------------------------------------------------------------ provider
class TranslationProvider:
    """Giao diện provider dịch: dịch một lô dòng đã che, trả cùng độ dài.

    Phần tử None = không dịch được dòng đó (chỉ từ điển offline mới trả
    None; provider mạng lỗi thì raise PB3Error E_NETWORK).
    `on_item(i)` (nếu có) được gọi sau khi dịch xong texts[i] — để tầng trên
    báo tiến độ theo từng chuỗi thật (luật #152), không đợi hết cả lô."""

    name = "?"

    def translate_batch(self, texts: List[str], src: str, dst: str,
                        on_item: Optional[Callable[[int], None]] = None
                        ) -> List[Optional[str]]:
        raise NotImplementedError


def _parse_gt_response(data: object) -> Optional[str]:
    """Đọc bản dịch từ JSON của endpoint gtx (một q -> đúng một khối).

    Trả text đã dịch, hoặc None nếu không đọc được."""
    if not isinstance(data, list) or not data:
        return None
    parts: List[str] = []
    for block in data:
        if not isinstance(block, list) or not block:
            break
        first = block[0]
        if not isinstance(first, list) or not first or not isinstance(first[0], str):
            break
        parts.append("".join(str(s[0]) for s in block
                             if isinstance(s, list) and s and isinstance(s[0], str)))
    if len(parts) != 1:
        return None
    return parts[0]


class GoogleTranslateProvider(TranslationProvider):
    """Google Translate miễn phí (không key, cần mạng).

    Mỗi câu là 1 request GET (endpoint chỉ dịch 1 q/request); các request
    cách nhau `gap_s`, lỗi 429/5xx/lỗi mạng thì retry với backoff.
    `urlopen` và `sleep` truyền vào được để test không chạm mạng thật."""

    name = "google"

    def __init__(self, *, timeout: float = DEFAULT_TIMEOUT_S,
                 max_retries: int = DEFAULT_MAX_RETRIES,
                 gap_s: float = DEFAULT_GAP_S,
                 backoff: Tuple[float, ...] = DEFAULT_BACKOFF_S,
                 sleep: Callable[[float], None] = time.sleep,
                 urlopen: Optional[Callable[..., object]] = None):
        self.timeout = timeout
        self.max_retries = max_retries
        self.gap_s = gap_s
        self.backoff = backoff
        self._sleep = sleep
        self._urlopen = urlopen or urllib.request.urlopen

    def translate_batch(self, texts: List[str], src: str, dst: str,
                        on_item: Optional[Callable[[int], None]] = None
                        ) -> List[Optional[str]]:
        out: List[Optional[str]] = []
        for i, text in enumerate(texts):
            if i:
                self._sleep(self.gap_s)
            out.append(self._translate_one(text, src, dst))
            if on_item is not None:
                on_item(i)
        return out

    def _translate_one(self, text: str, src: str, dst: str) -> str:
        params = {"client": "gtx", "sl": src, "tl": dst, "dt": "t", "q": text}
        url = GT_ENDPOINT + "?" + urllib.parse.urlencode(params)
        for attempt in range(self.max_retries + 1):
            if attempt:
                self._sleep(self.backoff[min(attempt - 1, len(self.backoff) - 1)])
            try:
                req = urllib.request.Request(url, headers={"User-Agent": GT_USER_AGENT})
                with self._urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read()
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                    continue
                raise PB3Error("E_NETWORK",
                               "Google Translate từ chối yêu cầu (HTTP %d)" % exc.code,
                               hint="Đợi một lát rồi thử lại; nếu vẫn lỗi, dùng --provider offline.")
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt < self.max_retries:
                    continue
                raise PB3Error("E_NETWORK",
                               "không gọi được Google Translate: %s" % type(exc).__name__,
                               hint="Kiểm tra mạng trên iSH rồi thử lại; không có mạng thì dùng --provider offline.")
            try:
                data = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                raise PB3Error("E_NETWORK",
                               "Google Translate trả về dữ liệu không đọc được",
                               hint="Thử lại sau; nếu vẫn lỗi, dùng --provider offline.")
            got = _parse_gt_response(data)
            if got is None:
                raise PB3Error("E_NETWORK",
                               "Google Translate trả về thiếu bản dịch",
                               hint="Thử lại sau; nếu vẫn lỗi, dùng --provider offline.")
            return got
        raise PB3Error("E_NETWORK",
                       "Google Translate giới hạn tốc độ, thử lại sau",
                       hint="Đợi một lát rồi thử lại; nếu vẫn lỗi, dùng --provider offline.")


# Từ điển cụm game thông dụng (khóa viết thường, khớp chính xác cả cụm).
GLOSSARY_EN_VI: Dict[str, str] = {
    "play": "Chơi", "settings": "Cài đặt", "start": "Bắt đầu",
    "continue": "Tiếp tục", "quit": "Thoát", "exit": "Thoát",
    "level": "Màn", "score": "Điểm", "loading": "Đang tải",
    "loading...": "Đang tải...", "tap to start": "Chạm để bắt đầu",
    "tap to continue": "Chạm để tiếp tục", "press start": "Nhấn bắt đầu",
    "game over": "Kết thúc", "victory": "Chiến thắng", "defeat": "Thất bại",
    "pause": "Tạm dừng", "resume": "Tiếp tục", "restart": "Chơi lại",
    "new game": "Game mới", "save": "Lưu", "load": "Tải",
    "options": "Tùy chọn", "sound": "Âm thanh", "music": "Nhạc",
    "language": "Ngôn ngữ", "back": "Quay lại", "cancel": "Hủy",
    "confirm": "Xác nhận", "ok": "OK", "yes": "Có", "no": "Không",
    "next": "Tiếp", "previous": "Trước", "buy": "Mua", "free": "Miễn phí",
    "coins": "Xu", "gems": "Đá quý", "health": "Máu", "mana": "Năng lượng",
    "attack": "Tấn công", "defense": "Phòng thủ", "shop": "Cửa hàng",
    "inventory": "Túi đồ", "quests": "Nhiệm vụ", "achievements": "Thành tựu",
    "leaderboard": "Bảng xếp hạng", "multiplayer": "Chơi mạng",
    "single player": "Chơi đơn", "tutorial": "Hướng dẫn",
    "help": "Trợ giúp", "about": "Giới thiệu", "credits": "Danh đề",
    "version": "Phiên bản", "connecting...": "Đang kết nối...",
    "error": "Lỗi", "success": "Thành công", "failed": "Thất bại",
    "retry": "Thử lại", "close": "Đóng", "open": "Mở", "delete": "Xóa",
    "edit": "Sửa", "create": "Tạo", "join": "Tham gia", "leave": "Rời",
    "invite": "Mời", "friends": "Bạn bè", "profile": "Hồ sơ",
    "logout": "Đăng xuất", "login": "Đăng nhập", "register": "Đăng ký",
    "password": "Mật khẩu", "username": "Tên người dùng", "email": "Email",
    "search": "Tìm kiếm", "filter": "Lọc", "sort": "Sắp xếp",
    "all": "Tất cả", "none": "Không có", "locked": "Đã khóa",
    "unlocked": "Đã mở", "completed": "Đã xong", "in progress": "Đang làm",
    "available": "Có sẵn", "sold out": "Đã bán hết",
    "coming soon": "Sắp ra mắt", "update": "Cập nhật",
    "download": "Tải xuống", "install": "Cài đặt",
    "uninstall": "Gỡ cài đặt", "rate us": "Đánh giá", "share": "Chia sẻ",
    "more": "Thêm", "less": "Ít hơn", "welcome": "Chào mừng",
    "good luck": "Chúc may mắn", "well done": "Làm tốt lắm",
    "level up": "Lên cấp", "new record": "Kỷ lục mới",
    "high score": "Điểm cao", "time up": "Hết giờ",
    "you win": "Bạn thắng", "you lose": "Bạn thua", "draw": "Hòa",
}
# Chiều ngược lấy nghĩa đầu tiên khi trùng (vd "Tiếp tục" <- continue);
# giá trị viết hoa chữ đầu cho đúng chính tả (OK giữ nguyên).
GLOSSARY_VI_EN: Dict[str, str] = {}
for _en, _vi in GLOSSARY_EN_VI.items():
    GLOSSARY_VI_EN.setdefault(_vi.casefold(), _en.capitalize())
GLOSSARY_VI_EN["ok"] = "OK"

# Từ điển khởi đầu EN -> ngôn ngữ đích mới (T2, 44 cụm UI thông dụng
# mỗi ngôn ngữ — cùng tập khóa với GLOSSARY_EN_VI để dễ đối chiếu).
# Chỉ thêm đích nào font Be Vietnam Pro phủ đủ bảng chữ cái có dấu
# (đã đo bằng sdk/font.py coverage() trên font đi kèm — xem NOTES T2):
# de/fr/es/pt/it/nl/id/ms/tr/pl/cs/sk/hu/sv/da/no/fi. ro/ru/uk/ja/ko/
# th/zh KHÔNG có trong danh sách này vì font thiếu chữ của chúng
# (ro thiếu ȘșȚț; Cyrillic/CJK/Thai thiếu toàn bộ) — thêm sau khi có
# font phủ được, không thêm cho có rồi để cổng font bắt lỗi.
GLOSSARY_EN_DE: Dict[str, str] = {
    "play": "Spielen", "settings": "Einstellungen", "start": "Start",
    "continue": "Weiter", "quit": "Beenden", "exit": "Verlassen",
    "level": "Level", "score": "Punkte", "loading": "Wird geladen",
    "loading...": "Wird geladen...", "tap to start": "Tippen zum Starten",
    "game over": "Spiel vorbei", "victory": "Sieg", "defeat": "Niederlage",
    "pause": "Pause", "resume": "Fortsetzen", "restart": "Neustart",
    "new game": "Neues Spiel", "save": "Speichern", "load": "Laden",
    "options": "Optionen", "sound": "Ton", "music": "Musik",
    "language": "Sprache", "back": "Zurück", "cancel": "Abbrechen",
    "confirm": "Bestätigen", "ok": "OK", "yes": "Ja", "no": "Nein",
    "next": "Weiter", "buy": "Kaufen", "free": "Kostenlos",
    "shop": "Shop", "help": "Hilfe", "about": "Über", "error": "Fehler",
    "retry": "Erneut versuchen", "close": "Schließen", "delete": "Löschen",
    "welcome": "Willkommen", "level up": "Level aufgestiegen",
    "you win": "Du gewinnst", "you lose": "Du verlierst",
}
GLOSSARY_EN_FR: Dict[str, str] = {
    "play": "Jouer", "settings": "Paramètres", "start": "Démarrer",
    "continue": "Continuer", "quit": "Quitter", "exit": "Sortie",
    "level": "Niveau", "score": "Score", "loading": "Chargement",
    "loading...": "Chargement...", "tap to start": "Touchez pour commencer",
    "game over": "Partie terminée", "victory": "Victoire", "defeat": "Défaite",
    "pause": "Pause", "resume": "Reprendre", "restart": "Recommencer",
    "new game": "Nouvelle partie", "save": "Sauvegarder", "load": "Charger",
    "options": "Options", "sound": "Son", "music": "Musique",
    "language": "Langue", "back": "Retour", "cancel": "Annuler",
    "confirm": "Confirmer", "ok": "OK", "yes": "Oui", "no": "Non",
    "next": "Suivant", "buy": "Acheter", "free": "Gratuit",
    "shop": "Boutique", "help": "Aide", "about": "À propos", "error": "Erreur",
    "retry": "Réessayer", "close": "Fermer", "delete": "Supprimer",
    "welcome": "Bienvenue", "level up": "Niveau supérieur",
    "you win": "Vous gagnez", "you lose": "Vous perdez",
}
GLOSSARY_EN_ES: Dict[str, str] = {
    "play": "Jugar", "settings": "Ajustes", "start": "Iniciar",
    "continue": "Continuar", "quit": "Salir", "exit": "Salir",
    "level": "Nivel", "score": "Puntuación", "loading": "Cargando",
    "loading...": "Cargando...", "tap to start": "Toca para empezar",
    "game over": "Fin del juego", "victory": "Victoria", "defeat": "Derrota",
    "pause": "Pausa", "resume": "Reanudar", "restart": "Reiniciar",
    "new game": "Nueva partida", "save": "Guardar", "load": "Cargar",
    "options": "Opciones", "sound": "Sonido", "music": "Música",
    "language": "Idioma", "back": "Atrás", "cancel": "Cancelar",
    "confirm": "Confirmar", "ok": "OK", "yes": "Sí", "no": "No",
    "next": "Siguiente", "buy": "Comprar", "free": "Gratis",
    "shop": "Tienda", "help": "Ayuda", "about": "Acerca de", "error": "Error",
    "retry": "Reintentar", "close": "Cerrar", "delete": "Eliminar",
    "welcome": "Bienvenido", "level up": "Subida de nivel",
    "you win": "Ganas", "you lose": "Pierdes",
}
GLOSSARY_EN_PT: Dict[str, str] = {
    "play": "Jogar", "settings": "Configurações", "start": "Iniciar",
    "continue": "Continuar", "quit": "Sair", "exit": "Sair",
    "level": "Nível", "score": "Pontuação", "loading": "Carregando",
    "loading...": "Carregando...", "tap to start": "Toque para começar",
    "game over": "Fim de jogo", "victory": "Vitória", "defeat": "Derrota",
    "pause": "Pausa", "resume": "Continuar", "restart": "Reiniciar",
    "new game": "Novo jogo", "save": "Salvar", "load": "Carregar",
    "options": "Opções", "sound": "Som", "music": "Música",
    "language": "Idioma", "back": "Voltar", "cancel": "Cancelar",
    "confirm": "Confirmar", "ok": "OK", "yes": "Sim", "no": "Não",
    "next": "Próximo", "buy": "Comprar", "free": "Grátis",
    "shop": "Loja", "help": "Ajuda", "about": "Sobre", "error": "Erro",
    "retry": "Tentar novamente", "close": "Fechar", "delete": "Excluir",
    "welcome": "Bem-vindo", "level up": "Subiu de nível",
    "you win": "Você ganhou", "you lose": "Você perdeu",
}
GLOSSARY_EN_IT: Dict[str, str] = {
    "play": "Gioca", "settings": "Impostazioni", "start": "Inizia",
    "continue": "Continua", "quit": "Esci", "exit": "Esci",
    "level": "Livello", "score": "Punteggio", "loading": "Caricamento",
    "loading...": "Caricamento...", "tap to start": "Tocca per iniziare",
    "game over": "Fine del gioco", "victory": "Vittoria", "defeat": "Sconfitta",
    "pause": "Pausa", "resume": "Riprendi", "restart": "Ricomincia",
    "new game": "Nuova partita", "save": "Salva", "load": "Carica",
    "options": "Opzioni", "sound": "Suono", "music": "Musica",
    "language": "Lingua", "back": "Indietro", "cancel": "Annulla",
    "confirm": "Conferma", "ok": "OK", "yes": "Sì", "no": "No",
    "next": "Avanti", "buy": "Compra", "free": "Gratis",
    "shop": "Negozio", "help": "Aiuto", "about": "Informazioni", "error": "Errore",
    "retry": "Riprova", "close": "Chiudi", "delete": "Elimina",
    "welcome": "Benvenuto", "level up": "Livello superiore",
    "you win": "Hai vinto", "you lose": "Hai perso",
}
GLOSSARY_EN_NL: Dict[str, str] = {
    "play": "Spelen", "settings": "Instellingen", "start": "Start",
    "continue": "Doorgaan", "quit": "Stoppen", "exit": "Afsluiten",
    "level": "Level", "score": "Score", "loading": "Laden",
    "loading...": "Laden...", "tap to start": "Tik om te starten",
    "game over": "Game over", "victory": "Overwinning", "defeat": "Nederlaag",
    "pause": "Pauze", "resume": "Hervatten", "restart": "Opnieuw starten",
    "new game": "Nieuw spel", "save": "Opslaan", "load": "Laden",
    "options": "Opties", "sound": "Geluid", "music": "Muziek",
    "language": "Taal", "back": "Terug", "cancel": "Annuleren",
    "confirm": "Bevestigen", "ok": "OK", "yes": "Ja", "no": "Nee",
    "next": "Volgende", "buy": "Kopen", "free": "Gratis",
    "shop": "Winkel", "help": "Help", "about": "Over", "error": "Fout",
    "retry": "Opnieuw proberen", "close": "Sluiten", "delete": "Verwijderen",
    "welcome": "Welkom", "level up": "Level omhoog",
    "you win": "Je wint", "you lose": "Je verliest",
}
GLOSSARY_EN_ID: Dict[str, str] = {
    "play": "Main", "settings": "Pengaturan", "start": "Mulai",
    "continue": "Lanjutkan", "quit": "Keluar", "exit": "Keluar",
    "level": "Level", "score": "Skor", "loading": "Memuat",
    "loading...": "Memuat...", "tap to start": "Ketuk untuk memulai",
    "game over": "Permainan berakhir", "victory": "Kemenangan", "defeat": "Kekalahan",
    "pause": "Jeda", "resume": "Lanjutkan", "restart": "Mulai ulang",
    "new game": "Permainan baru", "save": "Simpan", "load": "Muat",
    "options": "Opsi", "sound": "Suara", "music": "Musik",
    "language": "Bahasa", "back": "Kembali", "cancel": "Batal",
    "confirm": "Konfirmasi", "ok": "OK", "yes": "Ya", "no": "Tidak",
    "next": "Berikutnya", "buy": "Beli", "free": "Gratis",
    "shop": "Toko", "help": "Bantuan", "about": "Tentang", "error": "Kesalahan",
    "retry": "Coba lagi", "close": "Tutup", "delete": "Hapus",
    "welcome": "Selamat datang", "level up": "Naik level",
    "you win": "Kamu menang", "you lose": "Kamu kalah",
}
GLOSSARY_EN_MS: Dict[str, str] = {
    "play": "Main", "settings": "Tetapan", "start": "Mula",
    "continue": "Teruskan", "quit": "Keluar", "exit": "Keluar",
    "level": "Tahap", "score": "Skor", "loading": "Memuatkan",
    "loading...": "Memuatkan...", "tap to start": "Ketik untuk mula",
    "game over": "Tamat permainan", "victory": "Kemenangan", "defeat": "Kekalahan",
    "pause": "Jeda", "resume": "Sambung", "restart": "Mula semula",
    "new game": "Permainan baharu", "save": "Simpan", "load": "Muat",
    "options": "Pilihan", "sound": "Bunyi", "music": "Muzik",
    "language": "Bahasa", "back": "Kembali", "cancel": "Batal",
    "confirm": "Sahkan", "ok": "OK", "yes": "Ya", "no": "Tidak",
    "next": "Seterusnya", "buy": "Beli", "free": "Percuma",
    "shop": "Kedai", "help": "Bantuan", "about": "Tentang", "error": "Ralat",
    "retry": "Cuba lagi", "close": "Tutup", "delete": "Padam",
    "welcome": "Selamat datang", "level up": "Naik tahap",
    "you win": "Anda menang", "you lose": "Anda kalah",
}
GLOSSARY_EN_TR: Dict[str, str] = {
    "play": "Oyna", "settings": "Ayarlar", "start": "Başla",
    "continue": "Devam et", "quit": "Çık", "exit": "Çıkış",
    "level": "Seviye", "score": "Skor", "loading": "Yükleniyor",
    "loading...": "Yükleniyor...", "tap to start": "Başlamak için dokun",
    "game over": "Oyun bitti", "victory": "Zafer", "defeat": "Yenilgi",
    "pause": "Duraklat", "resume": "Devam et", "restart": "Yeniden başlat",
    "new game": "Yeni oyun", "save": "Kaydet", "load": "Yükle",
    "options": "Seçenekler", "sound": "Ses", "music": "Müzik",
    "language": "Dil", "back": "Geri", "cancel": "İptal",
    "confirm": "Onayla", "ok": "Tamam", "yes": "Evet", "no": "Hayır",
    "next": "İleri", "buy": "Satın al", "free": "Ücretsiz",
    "shop": "Mağaza", "help": "Yardım", "about": "Hakkında", "error": "Hata",
    "retry": "Tekrar dene", "close": "Kapat", "delete": "Sil",
    "welcome": "Hoş geldin", "level up": "Seviye atladın",
    "you win": "Kazandın", "you lose": "Kaybettin",
}
GLOSSARY_EN_PL: Dict[str, str] = {
    "play": "Graj", "settings": "Ustawienia", "start": "Start",
    "continue": "Kontynuuj", "quit": "Wyjdź", "exit": "Wyjście",
    "level": "Poziom", "score": "Wynik", "loading": "Ładowanie",
    "loading...": "Ładowanie...", "tap to start": "Dotknij, aby zacząć",
    "game over": "Koniec gry", "victory": "Zwycięstwo", "defeat": "Porażka",
    "pause": "Pauza", "resume": "Wznów", "restart": "Restart",
    "new game": "Nowa gra", "save": "Zapisz", "load": "Wczytaj",
    "options": "Opcje", "sound": "Dźwięk", "music": "Muzyka",
    "language": "Język", "back": "Wstecz", "cancel": "Anuluj",
    "confirm": "Potwierdź", "ok": "OK", "yes": "Tak", "no": "Nie",
    "next": "Dalej", "buy": "Kup", "free": "Za darmo",
    "shop": "Sklep", "help": "Pomoc", "about": "O grze", "error": "Błąd",
    "retry": "Spróbuj ponownie", "close": "Zamknij", "delete": "Usuń",
    "welcome": "Witamy", "level up": "Nowy poziom",
    "you win": "Wygrałeś", "you lose": "Przegrałeś",
}
GLOSSARY_EN_CS: Dict[str, str] = {
    "play": "Hrát", "settings": "Nastavení", "start": "Start",
    "continue": "Pokračovat", "quit": "Ukončit", "exit": "Ukončit",
    "level": "Úroveň", "score": "Skóre", "loading": "Načítání",
    "loading...": "Načítání...", "tap to start": "Klepnutím spustíte hru",
    "game over": "Konec hry", "victory": "Vítězství", "defeat": "Porážka",
    "pause": "Pauza", "resume": "Pokračovat", "restart": "Restartovat",
    "new game": "Nová hra", "save": "Uložit", "load": "Načíst",
    "options": "Možnosti", "sound": "Zvuk", "music": "Hudba",
    "language": "Jazyk", "back": "Zpět", "cancel": "Zrušit",
    "confirm": "Potvrdit", "ok": "OK", "yes": "Ano", "no": "Ne",
    "next": "Další", "buy": "Koupit", "free": "Zdarma",
    "shop": "Obchod", "help": "Nápověda", "about": "O hře", "error": "Chyba",
    "retry": "Zkusit znovu", "close": "Zavřít", "delete": "Smazat",
    "welcome": "Vítejte", "level up": "Nová úroveň",
    "you win": "Vyhrál jsi", "you lose": "Prohrál jsi",
}
GLOSSARY_EN_SK: Dict[str, str] = {
    "play": "Hrať", "settings": "Nastavenia", "start": "Štart",
    "continue": "Pokračovať", "quit": "Ukončiť", "exit": "Ukončiť",
    "level": "Úroveň", "score": "Skóre", "loading": "Načítavanie",
    "loading...": "Načítavanie...", "tap to start": "Ťuknutím spustíte hru",
    "game over": "Koniec hry", "victory": "Víťazstvo", "defeat": "Porážka",
    "pause": "Pauza", "resume": "Pokračovať", "restart": "Reštartovať",
    "new game": "Nová hra", "save": "Uložiť", "load": "Načítať",
    "options": "Možnosti", "sound": "Zvuk", "music": "Hudba",
    "language": "Jazyk", "back": "Späť", "cancel": "Zrušiť",
    "confirm": "Potvrdiť", "ok": "OK", "yes": "Áno", "no": "Nie",
    "next": "Ďalej", "buy": "Kúpiť", "free": "Zadarmo",
    "shop": "Obchod", "help": "Pomoc", "about": "O hre", "error": "Chyba",
    "retry": "Skúsiť znova", "close": "Zavrieť", "delete": "Vymazať",
    "welcome": "Vitajte", "level up": "Nová úroveň",
    "you win": "Vyhral si", "you lose": "Prehral si",
}
GLOSSARY_EN_HU: Dict[str, str] = {
    "play": "Játék", "settings": "Beállítások", "start": "Indítás",
    "continue": "Folytatás", "quit": "Kilépés", "exit": "Kilépés",
    "level": "Szint", "score": "Pontszám", "loading": "Betöltés",
    "loading...": "Betöltés...", "tap to start": "Érintsd meg az indításhoz",
    "game over": "Játék vége", "victory": "Győzelem", "defeat": "Vereség",
    "pause": "Szünet", "resume": "Folytatás", "restart": "Újraindítás",
    "new game": "Új játék", "save": "Mentés", "load": "Betöltés",
    "options": "Beállítások", "sound": "Hang", "music": "Zene",
    "language": "Nyelv", "back": "Vissza", "cancel": "Mégse",
    "confirm": "Megerősítés", "ok": "OK", "yes": "Igen", "no": "Nem",
    "next": "Tovább", "buy": "Vásárlás", "free": "Ingyenes",
    "shop": "Bolt", "help": "Súgó", "about": "Névjegy", "error": "Hiba",
    "retry": "Újrapróbálkozás", "close": "Bezárás", "delete": "Törlés",
    "welcome": "Üdvözöljük", "level up": "Szintlépés",
    "you win": "Nyertél", "you lose": "Vesztettél",
}
GLOSSARY_EN_SV: Dict[str, str] = {
    "play": "Spela", "settings": "Inställningar", "start": "Start",
    "continue": "Fortsätt", "quit": "Avsluta", "exit": "Avsluta",
    "level": "Nivå", "score": "Poäng", "loading": "Laddar",
    "loading...": "Laddar...", "tap to start": "Tryck för att starta",
    "game over": "Spelet är slut", "victory": "Seger", "defeat": "Förlust",
    "pause": "Paus", "resume": "Fortsätt", "restart": "Starta om",
    "new game": "Nytt spel", "save": "Spara", "load": "Ladda",
    "options": "Alternativ", "sound": "Ljud", "music": "Musik",
    "language": "Språk", "back": "Tillbaka", "cancel": "Avbryt",
    "confirm": "Bekräfta", "ok": "OK", "yes": "Ja", "no": "Nej",
    "next": "Nästa", "buy": "Köp", "free": "Gratis",
    "shop": "Butik", "help": "Hjälp", "about": "Om", "error": "Fel",
    "retry": "Försök igen", "close": "Stäng", "delete": "Ta bort",
    "welcome": "Välkommen", "level up": "Ny nivå",
    "you win": "Du vinner", "you lose": "Du förlorar",
}
GLOSSARY_EN_DA: Dict[str, str] = {
    "play": "Spil", "settings": "Indstillinger", "start": "Start",
    "continue": "Fortsæt", "quit": "Afslut", "exit": "Afslut",
    "level": "Niveau", "score": "Point", "loading": "Indlæser",
    "loading...": "Indlæser...", "tap to start": "Tryk for at starte",
    "game over": "Spillet er slut", "victory": "Sejr", "defeat": "Nederlag",
    "pause": "Pause", "resume": "Fortsæt", "restart": "Genstart",
    "new game": "Nyt spil", "save": "Gem", "load": "Indlæs",
    "options": "Indstillinger", "sound": "Lyd", "music": "Musik",
    "language": "Sprog", "back": "Tilbage", "cancel": "Annuller",
    "confirm": "Bekræft", "ok": "OK", "yes": "Ja", "no": "Nej",
    "next": "Næste", "buy": "Køb", "free": "Gratis",
    "shop": "Butik", "help": "Hjælp", "about": "Om", "error": "Fejl",
    "retry": "Prøv igen", "close": "Luk", "delete": "Slet",
    "welcome": "Velkommen", "level up": "Nyt niveau",
    "you win": "Du vinder", "you lose": "Du taber",
}
GLOSSARY_EN_NO: Dict[str, str] = {
    "play": "Spill", "settings": "Innstillinger", "start": "Start",
    "continue": "Fortsett", "quit": "Avslutt", "exit": "Avslutt",
    "level": "Nivå", "score": "Poeng", "loading": "Laster",
    "loading...": "Laster...", "tap to start": "Trykk for å starte",
    "game over": "Spillet er over", "victory": "Seier", "defeat": "Nederlag",
    "pause": "Pause", "resume": "Fortsett", "restart": "Start på nytt",
    "new game": "Nytt spill", "save": "Lagre", "load": "Last inn",
    "options": "Alternativer", "sound": "Lyd", "music": "Musikk",
    "language": "Språk", "back": "Tilbake", "cancel": "Avbryt",
    "confirm": "Bekreft", "ok": "OK", "yes": "Ja", "no": "Nei",
    "next": "Neste", "buy": "Kjøp", "free": "Gratis",
    "shop": "Butikk", "help": "Hjelp", "about": "Om", "error": "Feil",
    "retry": "Prøv igjen", "close": "Lukk", "delete": "Slett",
    "welcome": "Velkommen", "level up": "Nytt nivå",
    "you win": "Du vinner", "you lose": "Du taper",
}
GLOSSARY_EN_FI: Dict[str, str] = {
    "play": "Pelaa", "settings": "Asetukset", "start": "Aloita",
    "continue": "Jatka", "quit": "Lopeta", "exit": "Poistu",
    "level": "Taso", "score": "Pisteet", "loading": "Ladataan",
    "loading...": "Ladataan...", "tap to start": "Napauta aloittaaksesi",
    "game over": "Peli ohi", "victory": "Voitto", "defeat": "Tappio",
    "pause": "Tauko", "resume": "Jatka", "restart": "Aloita alusta",
    "new game": "Uusi peli", "save": "Tallenna", "load": "Lataa",
    "options": "Asetukset", "sound": "Ääni", "music": "Musiikki",
    "language": "Kieli", "back": "Takaisin", "cancel": "Peruuta",
    "confirm": "Vahvista", "ok": "OK", "yes": "Kyllä", "no": "Ei",
    "next": "Seuraava", "buy": "Osta", "free": "Ilmainen",
    "shop": "Kauppa", "help": "Ohje", "about": "Tietoja", "error": "Virhe",
    "retry": "Yritä uudelleen", "close": "Sulje", "delete": "Poista",
    "welcome": "Tervetuloa", "level up": "Uusi taso",
    "you win": "Voitit", "you lose": "Hävisit",
}

# Chiều ngược XX -> EN suy ra như VI_EN (nghĩa đầu tiên khi trùng,
# viết hoa chữ đầu; "OK" giữ nguyên).
def _reverse_glossary(table: Dict[str, str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for en, xx in table.items():
        out.setdefault(xx.casefold(), en.capitalize())
    out["ok"] = "OK"
    return out


# Đăng ký cặp ngôn ngữ từ điển offline: (nguồn, đích) -> bảng tra.
# Cặp không có trong này -> OfflineGlossaryProvider trả None (rơi về
# Google như trước). GLOSSARY_EN_VI giữ nguyên 105 cụm.
GLOSSARIES: Dict[Tuple[str, str], Dict[str, str]] = {
    ("en", "vi"): GLOSSARY_EN_VI,
    ("vi", "en"): GLOSSARY_VI_EN,
}
for _code, _table in (("de", GLOSSARY_EN_DE), ("fr", GLOSSARY_EN_FR),
                      ("es", GLOSSARY_EN_ES), ("pt", GLOSSARY_EN_PT),
                      ("it", GLOSSARY_EN_IT), ("nl", GLOSSARY_EN_NL),
                      ("id", GLOSSARY_EN_ID), ("ms", GLOSSARY_EN_MS),
                      ("tr", GLOSSARY_EN_TR), ("pl", GLOSSARY_EN_PL),
                      ("cs", GLOSSARY_EN_CS), ("sk", GLOSSARY_EN_SK),
                      ("hu", GLOSSARY_EN_HU), ("sv", GLOSSARY_EN_SV),
                      ("da", GLOSSARY_EN_DA), ("no", GLOSSARY_EN_NO),
                      ("fi", GLOSSARY_EN_FI)):
    GLOSSARIES[("en", _code)] = _table
    GLOSSARIES[(_code, "en")] = _reverse_glossary(_table)


class OfflineGlossaryProvider(TranslationProvider):
    """Từ điển offline: chỉ khớp cụm nguyên văn (không cần mạng).

    Không dịch được -> None (giữ nguyên câu gốc, đánh dấu cần dịch tay).
    Tra theo đăng ký GLOSSARIES (en<->vi + en<->17 đích mới); cặp khác
    luôn trả None. src == "auto" thì thử EN_VI trước rồi VI_EN (như cũ).
    """

    name = "offline"

    def translate_batch(self, texts: List[str], src: str, dst: str,
                        on_item: Optional[Callable[[int], None]] = None
                        ) -> List[Optional[str]]:
        table = GLOSSARIES.get((src, dst))
        if table is None and src == "auto":
            table = GLOSSARY_EN_VI
        if table is None:
            return [None] * len(texts)
        out: List[Optional[str]] = []
        for i, text in enumerate(texts):
            key = text.strip().casefold()
            got = table.get(key)
            if got is None and src == "auto":
                got = GLOSSARY_VI_EN.get(key)
            out.append(got)
            if on_item is not None:
                on_item(i)
        return out


class FakeProvider(TranslationProvider):
    """Provider giả, CHỈ dùng cho test (không gọi mạng).

    Trả "[<dst>] " + text; đếm số lần gọi để test cache."""

    name = "fake"

    def __init__(self) -> None:
        self.calls = 0
        self.batches: List[List[str]] = []

    def translate_batch(self, texts: List[str], src: str, dst: str,
                        on_item: Optional[Callable[[int], None]] = None
                        ) -> List[Optional[str]]:
        self.calls += 1
        self.batches.append(list(texts))
        out = ["[%s] %s" % (dst, t) for t in texts]
        if on_item is not None:
            for i in range(len(texts)):
                on_item(i)
        return out


# ------------------------------------------------------------------ cache
class TranslationCache:
    """Cache câu đã dịch (khóa sha256 text + cặp ngôn ngữ).

    Lỗi đọc/ghi file cache không làm hỏng dịch (bỏ qua im lặng). Chỉ ghi
    đĩa khi gọi save() (cuối đợt dịch), không ghi sau mỗi câu."""

    def __init__(self, path: Optional[str] = None, max_entries: int = 2000):
        self.path = str(path) if path else None
        self.max_entries = max_entries
        self._entries: Dict[str, str] = {}
        self._load()

    @staticmethod
    def key(source: str, src: str, dst: str) -> str:
        blob = ("%s\0%s\0%s" % (src, dst, source)).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()

    def get(self, source: str, src: str, dst: str) -> Optional[str]:
        return self._entries.get(self.key(source, src, dst))

    def put(self, source: str, src: str, dst: str, translated: str) -> None:
        key = self.key(source, src, dst)
        self._entries[key] = translated
        while len(self._entries) > self.max_entries:
            self._entries.pop(next(iter(self._entries)))

    def save(self) -> None:
        if not self.path:
            return
        try:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            # JSON gọn (không indent): file cache là dữ liệu nội bộ, ghi/đọc
            # nhanh hơn và nhỏ hơn hẳn khi đủ 2000 câu; khuôn dạng đọc
            # (json.load) không đổi nên cache cũ vẫn đọc được.
            blob = json.dumps({"version": CACHE_VERSION, "entries": self._entries},
                              ensure_ascii=False).encode("utf-8")
            safe_io.atomic_write_bytes(self.path, blob)
        except Exception:
            pass

    def _load(self) -> None:
        if not self.path:
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError, UnicodeDecodeError):
            return
        if not isinstance(data, dict) or data.get("version") != CACHE_VERSION:
            return
        entries = data.get("entries")
        if isinstance(entries, dict):
            self._entries = {str(k): str(v) for k, v in entries.items()
                             if isinstance(v, str)}


# ------------------------------------------------------------------ dịch bảng
@dataclass
class TranslationResult:
    translated: Dict[str, str]
    total: int = 0
    done: int = 0            # dịch xong bằng provider (không tính cache/từ điển)
    cached: int = 0
    glossary: int = 0
    partial: int = 0
    untranslated: int = 0
    empty: int = 0
    unchanged: int = 0
    network_error: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    untranslated_keys: List[str] = field(default_factory=list)
    credit_key: Optional[str] = None   # key đã gắn credit (None = không đặt được)
    credit_requested: bool = False     # người dùng có bật credit không


def _repair_placeholder_lines(pending, seg_out, provider, fallback,
                              source_lang, target_lang, chunk):
    """Dịch lại 1 lần các dòng đã dịch nhưng lệch placeholder.

    Chỉ chạm các dòng đã có bản dịch (val is not None) và lệch theo
    placeholders_match_strict (thứ tự + số lần, như cổng kiểm tra).
    Gom theo masked text để khỏi dịch trùng; chấp nhận bản dịch lại chỉ
    khi strict pass sau khi bỏ che. Lỗi ở bước này không crash: giữ
    nguyên bản cũ, cảnh báo sẽ phát ra ở bước ráp như trước.
    """
    by_masked = {}
    for key, (value, lines, segs) in pending.items():
        for seg in segs:
            val, origin = seg_out.get((key, seg.line_no), (None, ""))
            if val is None:
                continue
            # Đoạn từ từ điển game (người dùng chọn tay): KHÔNG dịch lại
            # bằng máy — ghi đè entry của user là sai, dù placeholder
            # có lệch (cảnh báo vẫn phát ra ở bước ráp như trước).
            if origin == "glossary":
                continue
            if placeholders_match_strict(lines[seg.line_no],
                                         unmask_line(val, seg.tokens)):
                continue
            by_masked.setdefault(seg.masked, []).append((key, seg))
    if not by_masked:
        return
    masked_list = list(by_masked)
    got = []
    try:
        for start in range(0, len(masked_list), chunk):
            part = masked_list[start:start + chunk]
            try:
                batch = provider.translate_batch(part, source_lang,
                                                 target_lang)
            except PB3Error as exc:
                if exc.code != "E_NETWORK":
                    raise
                batch = [None] * len(part)
                if fallback is not None:
                    try:
                        fb = fallback.translate_batch(part, source_lang,
                                                      target_lang)
                    except Exception:
                        fb = [None] * len(part)
                    batch = [b if a is None else a
                             for a, b in zip(batch, fb)]
            got.extend(batch)
    except Exception:
        return
    for masked, val in zip(masked_list, got):
        if val is None:
            continue
        for key, seg in by_masked[masked]:
            if placeholders_match_strict(
                    pending[key][1][seg.line_no],
                    unmask_line(val, seg.tokens)):
                seg_out[(key, seg.line_no)] = (val, "provider")


def translate_table(kv: Dict[str, str], *, source_lang: str = "auto",
                    target_lang: str = "vi", provider: TranslationProvider,
                    fallback: Optional[TranslationProvider] = None,
                    cache: Optional[TranslationCache] = None,
                    progress_fn: Optional[Callable[[int, int], None]] = None,
                    chunk: int = DEFAULT_CHUNK,
                    credit: bool = True,
                    game_glossary: Optional["GameGlossary"] = None) -> TranslationResult:
    """Dịch bảng key-value: chỉ dịch VALUE, giữ nguyên KEY.

    - game_glossary (sdk.glossary.GameGlossary): từ điển riêng theo game,
      áp dụng TRƯỚC cache và TRƯỚC dịch máy, ở mức từng dòng: dòng nào
      khớp chính xác trong từ điển thì dùng luôn, không gửi provider,
      không đưa vào cache, không bị bước sửa placeholder dịch lại.
    - Lỗi mạng ở một lô: ghi vào network_error, dùng fallback (từ điển
      offline) cho lô đó rồi dịch tiếp, không crash.
    - Câu dịch xong mà placeholder/xuống dòng lệch thì dịch lại 1 lần;
      vẫn lệch thì giữ bản dịch kèm cảnh báo, không đưa vào cache.
    - progress_fn(done, total) gọi sau mỗi đoạn text đã dịch xong (đơn vị:
      đoạn text cần dịch) — % tính theo số đoạn thật, dãy done không lùi.
    - credit=True (mặc định): sau khi dịch xong, gắn 'Việt Hoá Bởi Bear'
      vào 1 entry tiêu đề/version (xem apply_credit); credit KHÔNG đi vào
      cache (cache chỉ giữ bản dịch thuần) nên chạy lại với credit=False
      vẫn ra bản dịch thuần. credit=False thì không gắn gì.
    """
    check_lang(target_lang, allow_auto=False)
    check_lang(source_lang, allow_auto=True)
    if source_lang != "auto" and source_lang == target_lang:
        raise PB3Error("E_INPUT_NOT_FOUND",
                       "ngôn ngữ nguồn và đích trùng nhau: %s" % target_lang)
    for key, value in kv.items():
        if not isinstance(value, str):
            raise PB3Error("E_CORRUPT", "giá trị key %s không phải chuỗi" % key)

    result = TranslationResult(translated={}, total=len(kv))
    pending: Dict[str, Tuple[str, List[str], List[_Segment]]] = {}
    segments: List[_Segment] = []
    # Đoạn đã có đáp án từ từ điển game (dòng khớp) — không gửi provider.
    seg_out: Dict[Tuple[str, int], Tuple[Optional[str], str]] = {}
    for key, value in kv.items():
        if value == "":
            result.translated[key] = ""
            result.empty += 1
            continue
        # Từ điển game TRƯỚC cache: cả giá trị khớp hết thì dùng luôn
        # (cache cũ của máy dịch không được ghi đè entry của user).
        if game_glossary is not None:
            lines = value.split("\n")
            resolved: List[str] = []
            all_glossary = True
            for line in lines:
                masked, _tokens = mask_line(line)
                if not _line_has_text(masked):
                    resolved.append(line)
                    continue
                hit = game_glossary.lookup(line)
                if hit is None:
                    all_glossary = False
                    break
                resolved.append(hit)
            if all_glossary:
                new_value = "\n".join(resolved)
                result.translated[key] = new_value
                result.glossary += 1
                if not placeholders_ok(value, new_value):
                    result.warnings.append(
                        "key %s: placeholder/thẻ/xuống dòng thay đổi sau khi "
                        "dịch bằng từ điển game (đã giữ bản dịch, kiểm tra "
                        "lại trước khi ghi ngược)" % key)
                continue
        hit = cache.get(value, source_lang, target_lang) if cache is not None else None
        if hit is not None:
            result.translated[key] = hit
            result.cached += 1
            continue
        lines = value.split("\n")
        segs: List[_Segment] = []
        for i, line in enumerate(lines):
            masked, tokens = mask_line(line)
            if not _line_has_text(masked):
                continue
            segs.append(_Segment(key, i, masked, tokens))
        if not segs:
            result.translated[key] = value
            result.unchanged += 1
            continue
        pending[key] = (value, lines, segs)
        # Từ điển game ở mức đoạn: dòng nào khớp thì dùng luôn, dòng còn
        # lại mới gửi provider (giá trị nhiều dòng khớp một phần vẫn được).
        for seg in segs:
            ghit = (game_glossary.lookup(lines[seg.line_no])
                    if game_glossary is not None else None)
            if ghit is not None:
                seg_out[(seg.key, seg.line_no)] = (ghit, "glossary")
            else:
                segments.append(seg)

    # Dịch theo lô; mỗi lô lỗi mạng thì fallback rồi dịch tiếp.
    # progress_fn(done, total) gọi sau MỖI chuỗi đã dịch xong (on_item),
    # không đợi hết lô — % tính theo số chuỗi thật (luật #152). Lô nào
    # provider không báo từng chuỗi (hoặc fallback) thì báo một lần khi
    # xong lô; dãy done không bao giờ lùi.
    # (seg_out đã có sẵn đáp án từ điển game ở trên — không reset ở đây.)
    total = len(segments)
    done = 0
    for start in range(0, total, chunk):
        part = segments[start:start + chunk]
        texts = [s.masked for s in part]
        done_before = done
        reported = [0]

        def _on_item(i: int, _done_before: int = done_before,
                     _reported: List[int] = reported) -> None:
            _reported[0] = i + 1
            if progress_fn is not None:
                progress_fn(_done_before + i + 1, total)

        try:
            got = provider.translate_batch(texts, source_lang, target_lang,
                                           on_item=_on_item)
            origins = ["provider"] * len(part)
            net_err = None
        except PB3Error as exc:
            if exc.code != "E_NETWORK":
                raise
            net_err = exc
            got = [None] * len(part)
            origins = [None] * len(part)
            if result.network_error is None:
                result.network_error = exc.detail or exc.message_vi
        if net_err is not None and fallback is not None:
            fb = fallback.translate_batch(texts, source_lang, target_lang)
            for i, val in enumerate(fb):
                if val is not None and got[i] is None:
                    got[i] = val
                    origins[i] = "glossary"
        for seg, val, origin in zip(part, got, origins):
            seg_out[(seg.key, seg.line_no)] = (val, origin or "")
        done += len(part)
        if reported[0] < len(part) and progress_fn is not None:
            progress_fn(done, total)

    # Sửa placeholder (lâu dài): dòng nào sau khi bỏ che vẫn lệch
    # placeholder (thứ tự/số lần/xuống dòng, như cổng kiểm tra) thì dịch
    # lại 1 lần — dịch lại thường giữ token tốt hơn batch gộp. Vẫn lệch
    # thì giữ kết quả cũ + cảnh báo như trước (không giấu lỗi).
    _repair_placeholder_lines(pending, seg_out, provider, fallback,
                              source_lang, target_lang, chunk)

    for key, (value, lines, segs) in pending.items():
        out_lines = list(lines)
        n_provider = n_glossary = n_missing = 0
        for seg in segs:
            val, origin = seg_out.get((key, seg.line_no), (None, ""))
            if val is None:
                n_missing += 1
                continue
            out_lines[seg.line_no] = unmask_line(val, seg.tokens)
            if origin == "glossary":
                n_glossary += 1
            else:
                n_provider += 1
        new_value = "\n".join(out_lines)
        if n_missing == len(segs):
            result.translated[key] = value
            result.untranslated += 1
            result.untranslated_keys.append(key)
        elif n_missing:
            result.translated[key] = new_value
            result.partial += 1
            result.untranslated_keys.append(key)
            result.warnings.append("key %s: %d/%d dòng chưa dịch (giữ nguyên)"
                                   % (key, n_missing, len(segs)))
        elif n_glossary == len(segs):
            result.translated[key] = new_value
            result.glossary += 1
        else:
            result.translated[key] = new_value
            result.done += 1
        if result.translated[key] != value and n_missing == 0:
            if not placeholders_ok(value, result.translated[key]):
                result.warnings.append(
                    "key %s: placeholder/thẻ/xuống dòng thay đổi sau khi dịch "
                    "(đã giữ bản dịch, kiểm tra lại trước khi ghi ngược)" % key)
            elif cache is not None and n_glossary == 0:
                cache.put(value, source_lang, target_lang, result.translated[key])
    # Giữ đúng thứ tự key của nguồn (như export JSON của extract).
    result.translated = {key: result.translated[key] for key in kv}
    if cache is not None:
        cache.save()
    # Credit gắn SAU khi dịch + cache: cache chỉ giữ bản dịch thuần.
    result.credit_requested = credit
    if credit:
        result.credit_key = apply_credit(result.translated)
    return result


def translate_json_file(input_path: str, output_path: str, *,
                        source_lang: str = "auto", target_lang: str = "vi",
                        provider: TranslationProvider,
                        fallback: Optional[TranslationProvider] = None,
                        cache: Optional[TranslationCache] = None,
                        progress_fn: Optional[Callable[[int, int], None]] = None,
                        chunk: int = DEFAULT_CHUNK,
                        credit: bool = True,
                        game_glossary: Optional["GameGlossary"] = None) -> TranslationResult:
    """Đọc bảng JSON key-value, dịch, ghi file đã dịch (ghi nguyên tử).

    File đầu vào phải là object {key: value chuỗi}; giá trị khác chuỗi ->
    E_CORRUPT. File xuất được verify lại trước khi ghi. credit=True (mặc
    định): gắn 'Việt Hoá Bởi Bear' vào 1 entry tiêu đề/version trong file
    xuất (xem apply_credit); credit=False thì file xuất là bản dịch thuần.
    """
    try:
        with open(input_path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise PB3Error("E_CORRUPT", "file JSON đầu vào hỏng: %s" % type(exc).__name__,
                       where=input_path)
    if not isinstance(doc, dict):
        raise PB3Error("E_CORRUPT", "file JSON phải là object key-value", where=input_path)
    for key, value in doc.items():
        if not isinstance(value, str):
            raise PB3Error("E_CORRUPT", "giá trị key %s không phải chuỗi" % key,
                           where=input_path)
    result = translate_table(doc, source_lang=source_lang, target_lang=target_lang,
                             provider=provider, fallback=fallback, cache=cache,
                             progress_fn=progress_fn, chunk=chunk, credit=credit,
                             game_glossary=game_glossary)
    data = exp.export_key_value_json(result.translated)
    check = exp.verify_export_bytes(data, result.translated)
    if not check["ok"]:
        raise PB3Error("E_VERIFY_FAILED",
                       "verify file dịch lỗi: " + "; ".join(check["problems"][:3]))
    safe_io.atomic_write_bytes(output_path, data)
    return result
