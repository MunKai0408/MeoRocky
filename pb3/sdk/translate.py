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

# Placeholder giữ nguyên khi dịch (cùng bộ với sdk.exporters).
_PLACEHOLDER_RE = re.compile(
    r"\{\d+\}|\{[A-Za-z_][A-Za-z0-9_]*\}|%(\d+\$)?[sdif]|</?[A-Za-z][^>]*>|\\[nrt\\'\"]")
# Thẻ đóng (</b>, </size>) cũng phải che: trước đây chỉ che thẻ mở nên
# dòng chỉ còn thẻ đóng vẫn có chữ Latin ('size', 'b') và bị coi là
# tiếng Anh yếu rồi gửi đi dịch (fixture Passpartout: dòng rich-text
# CJK/Korean bị đổi). Che cả hai thì dòng chỉ còn thẻ -> không còn chữ
# thật -> bỏ qua.
_TOKEN_FMT = "__PB3PH%d__"
# Google Translate đôi khi chèn khoảng trắng/chữ thường vào token:
# khớp khoan dung (kể cả "__ PB3PH0 __" hay "__pb3ph0__").
_TOKEN_RE = re.compile(r"__\s*PB3PH\s*(\d+)\s*__", re.IGNORECASE)


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
    """Khôi phục placeholder từ token (khoan dung khoảng trắng/chữ hoa)."""

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


class OfflineGlossaryProvider(TranslationProvider):
    """Từ điển offline: chỉ khớp cụm nguyên văn (không cần mạng).

    Không dịch được -> None (giữ nguyên câu gốc, đánh dấu cần dịch tay).
    Chỉ có cặp en<->vi; cặp khác luôn trả None."""

    name = "offline"

    def translate_batch(self, texts: List[str], src: str, dst: str,
                        on_item: Optional[Callable[[int], None]] = None
                        ) -> List[Optional[str]]:
        table = None
        if src == "en" and dst == "vi":
            table = GLOSSARY_EN_VI
        elif src == "vi" and dst == "en":
            table = GLOSSARY_VI_EN
        elif src == "auto":
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


def translate_table(kv: Dict[str, str], *, source_lang: str = "auto",
                    target_lang: str = "vi", provider: TranslationProvider,
                    fallback: Optional[TranslationProvider] = None,
                    cache: Optional[TranslationCache] = None,
                    progress_fn: Optional[Callable[[int, int], None]] = None,
                    chunk: int = DEFAULT_CHUNK,
                    credit: bool = True) -> TranslationResult:
    """Dịch bảng key-value: chỉ dịch VALUE, giữ nguyên KEY.

    - Lỗi mạng ở một lô: ghi vào network_error, dùng fallback (từ điển
      offline) cho lô đó rồi dịch tiếp, không crash.
    - Câu dịch xong mà placeholder/xuống dòng lệch thì giữ bản dịch kèm
      cảnh báo, không đưa vào cache.
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
    for key, value in kv.items():
        if value == "":
            result.translated[key] = ""
            result.empty += 1
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
        segments.extend(segs)

    # Dịch theo lô; mỗi lô lỗi mạng thì fallback rồi dịch tiếp.
    # progress_fn(done, total) gọi sau MỖI chuỗi đã dịch xong (on_item),
    # không đợi hết lô — % tính theo số chuỗi thật (luật #152). Lô nào
    # provider không báo từng chuỗi (hoặc fallback) thì báo một lần khi
    # xong lô; dãy done không bao giờ lùi.
    seg_out: Dict[Tuple[str, int], Tuple[Optional[str], str]] = {}
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
                        credit: bool = True) -> TranslationResult:
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
                             progress_fn=progress_fn, chunk=chunk, credit=credit)
    data = exp.export_key_value_json(result.translated)
    check = exp.verify_export_bytes(data, result.translated)
    if not check["ok"]:
        raise PB3Error("E_VERIFY_FAILED",
                       "verify file dịch lỗi: " + "; ".join(check["problems"][:3]))
    safe_io.atomic_write_bytes(output_path, data)
    return result
