"""Từ điển riêng theo từng game (T2).

Người dùng tự thêm/sửa các cặp (cụm nguồn -> bản dịch) cho từng game;
từ điển được áp dụng TRƯỚC dịch máy (cả 2 đường translate_table và
translate_json_google) và có độ ưu tiên cao nhất: entry trong từ điển
không bao giờ bị dịch máy ghi đè, cũng không bị bước sửa placeholder
dịch lại (xem translate.py::_repair_placeholder_lines).

Định dạng JSON xuất/nhập (đơn giản, đọc được bằng tay):
    {"version": 1, "game": "9 Kings", "src": "en", "dst": "vi",
     "entries": {"play": "Chơi", "Save {0} gold": "Lưu {0} vàng"}}

Khớp chính xác cả cụm/dòng (chuẩn hoá strip + casefold), không dịch
từng từ. Entry có placeholder thì khớp theo dòng gốc chưa che (vd
"Save {0} gold") và bản dịch giữ nguyên placeholder đó.

Chỉ thư viện chuẩn, Python >= 3.8.
"""
from __future__ import annotations

import json
from typing import Dict, Optional

from core.errors import PB3Error

GLOSSARY_VERSION = 1
MAX_ENTRIES = 5000


def normalize_key(text: str) -> str:
    """Chuẩn hoá khóa tra cứu: bỏ trắng đầu/cuối + casefold."""
    return (text or "").strip().casefold()


class GameGlossary:
    """Từ điển theo game cho 1 cặp ngôn ngữ (src -> dst)."""

    def __init__(self, entries: Optional[Dict[str, str]] = None, *,
                 game: str = "", src: str = "en", dst: str = "vi"):
        self.game = game or ""
        self.src = src or "en"
        self.dst = dst or "vi"
        self._entries: Dict[str, str] = {}
        for key, value in (entries or {}).items():
            self.set(key, value)

    # ------------------------------------------------------------ CRUD
    def set(self, key: str, value: str) -> None:
        """Thêm/ghi đè 1 entry. Khóa/giá trị rỗng -> lỗi."""
        if not isinstance(key, str) or not key.strip():
            raise PB3Error("E_INPUT_NOT_FOUND",
                           "từ điển game: khóa trống (cần cụm nguồn).")
        if not isinstance(value, str) or not value.strip():
            raise PB3Error("E_INPUT_NOT_FOUND",
                           "từ điển game: bản dịch trống cho khóa %r." % key)
        norm = normalize_key(key)
        if norm not in self._entries and len(self._entries) >= MAX_ENTRIES:
            raise PB3Error("E_CONFIG_INVALID",
                           "từ điển game đã đủ %d entry (tối đa)." % MAX_ENTRIES)
        self._entries[norm] = value.strip()

    def remove(self, key: str) -> bool:
        """Xoá 1 entry theo khóa (đã chuẩn hoá). Trả True nếu đã có."""
        return self._entries.pop(normalize_key(key), None) is not None

    def lookup(self, text: str) -> Optional[str]:
        """Tra cứu chính xác cả cụm/dòng. Trả bản dịch hoặc None."""
        if not isinstance(text, str):
            return None
        return self._entries.get(normalize_key(text))

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and normalize_key(key) in self._entries

    # ------------------------------------------------------------ JSON
    def to_dict(self) -> Dict[str, object]:
        return {"version": GLOSSARY_VERSION, "game": self.game,
                "src": self.src, "dst": self.dst,
                "entries": dict(self._entries)}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, text: str) -> "GameGlossary":
        """Đọc từ điển từ chuỗi JSON (định dạng xuất/nhập).

        Sai định dạng -> PB3Error E_CORRUPT (không đoán mò, không lặng
        lẽ bỏ entry hỏng: entry hỏng nào cũng báo rõ rồi từ chối cả file).
        """
        try:
            data = json.loads(text)
        except (ValueError, UnicodeDecodeError) as exc:
            raise PB3Error("E_CORRUPT",
                           "file từ điển game không phải JSON hợp lệ: %s"
                           % type(exc).__name__)
        if not isinstance(data, dict):
            raise PB3Error("E_CORRUPT",
                           "file từ điển game phải là object JSON.")
        version = data.get("version", GLOSSARY_VERSION)
        if version != GLOSSARY_VERSION:
            raise PB3Error("E_CORRUPT",
                           "file từ điển game phiên bản %r (cần %d)."
                           % (version, GLOSSARY_VERSION))
        entries = data.get("entries")
        if not isinstance(entries, dict):
            raise PB3Error("E_CORRUPT",
                           "file từ điển game thiếu trường 'entries' (object).")
        game = data.get("game") or ""
        src = data.get("src") or "en"
        dst = data.get("dst") or "vi"
        if not isinstance(game, str) or not isinstance(src, str) \
                or not isinstance(dst, str):
            raise PB3Error("E_CORRUPT",
                           "file từ điển game: game/src/dst phải là chuỗi.")
        glossary = cls(game=game, src=src, dst=dst)
        for key, value in entries.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise PB3Error("E_CORRUPT",
                               "file từ điển game: entry %r phải là chuỗi -> chuỗi."
                               % (key,))
            glossary.set(key, value)
        return glossary

    @classmethod
    def from_entries(cls, entries: Dict[str, str], *, game: str = "",
                     src: str = "en", dst: str = "vi") -> "GameGlossary":
        """Dựng nhanh từ dict {cụm nguồn: bản dịch} (dùng trong test/bridge)."""
        return cls(entries, game=game, src=src, dst=dst)
