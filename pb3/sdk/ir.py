"""IR: phong bì có version quanh các LocTable (đặc tả mục 3). Trung lập định dạng; Claude 07 chuẩn hóa vào đây.

Tương thích: cùng 'major' và minor tài liệu <= minor của người đọc thì đọc được; major khác hoặc minor mới
hơn -> E_CORRUPT (không đoán, không bỏ qua âm thầm). Bảo toàn từng LocEntry (key, value chính xác từng ký tự,
locale, key_origin, status, source, notes), thứ tự nguồn, conflicts/warnings; key trùng KHÔNG tự gộp.
IR 2.0 thay IR 1.0 (TextEntry/ExtractionResult) — xem handoff/decisions.md (02b).
"""
from __future__ import annotations

import codecs
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from core.errors import PB3Error
from core.model import Provenance
from core.model import _Base as ModelBase
from sdk.types import LocEntry, LocTable

IR_SCHEMA = "pb3.ir"
IR_VERSION: Tuple[int, int] = (2, 0)


def version_str(v: Tuple[int, int]) -> str:
    return "%d.%d" % v


def parse_version(s: Any) -> Tuple[int, int]:
    try:
        a, b = str(s).split(".")
        return int(a), int(b)
    except (ValueError, TypeError):
        raise PB3Error("E_CORRUPT", "IR.version không hợp lệ: %r" % (s,))


def check_compat(doc_version: Any, reader: Tuple[int, int] = IR_VERSION) -> Tuple[int, int]:
    dv = parse_version(doc_version)
    if dv[0] != reader[0] or dv[1] > reader[1]:
        raise PB3Error("E_CORRUPT", "IR %s không tương thích với người đọc %s"
                       % (version_str(dv), version_str(reader)))
    return dv


@dataclass
class IRDocument(ModelBase):
    _NESTED = {"producer": (Provenance, "one"), "tables": (LocTable, "list")}
    schema: str = IR_SCHEMA
    version: str = version_str(IR_VERSION)
    producer: Optional[Provenance] = None
    tables: List[LocTable] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d):
        if isinstance(d, dict):
            if d.get("schema") != IR_SCHEMA:
                raise PB3Error("E_CORRUPT", "IR.schema sai: %r" % (d.get("schema"),))
            check_compat(d.get("version"))
        return super(IRDocument, cls).from_dict(d)

    def entries(self) -> List[LocEntry]:
        return [e for t in self.tables for e in t.entries]

    def duplicates(self) -> List[Tuple[str, str, str]]:
        """(adapter_id, locale, key) xuất hiện > 1 lần trong cùng bảng. Chỉ báo cáo, không gộp."""
        dup: List[Tuple[str, str, str]] = []
        for t in self.tables:
            seen = set()
            for e in t.entries:
                k = (e.locale, e.key)
                if k in seen:
                    item = (t.adapter_id, e.locale or "", e.key)
                    if item not in dup:
                        dup.append(item)
                seen.add(k)
        return dup

    def for_locale(self, code: str) -> List[LocEntry]:
        return [e for t in self.tables for e in t.entries if (e.locale or t.locale) == code]

    def unencodable(self) -> List[str]:
        """Key có value không mã hóa được theo source.encoding (ghi ngược cần cảnh báo trước)."""
        bad = []
        for e in self.entries():
            try:
                codecs.lookup(e.source.encoding)               # type: ignore[union-attr]
                e.value.encode(e.source.encoding)              # type: ignore[union-attr]
            except (LookupError, UnicodeError):
                bad.append(e.key)
        return bad


def from_tables(*tables: LocTable, producer: Optional[Provenance] = None) -> IRDocument:
    return IRDocument(producer=producer, tables=list(tables))


def dumps(doc: IRDocument) -> str:
    return json.dumps(doc.to_dict(), ensure_ascii=False, sort_keys=True, indent=1)


def loads(text: str) -> IRDocument:
    try:
        data = json.loads(text)
    except ValueError as e:
        raise PB3Error("E_CORRUPT", "IR JSON hỏng: " + str(e)[:80])
    return IRDocument.from_dict(data)
