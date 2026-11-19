"""Exporter cho pipeline trích xuất ngôn ngữ (Claude 07, đặc tả 7.5-7.7).

- JSON key-value (bắt buộc): chỉ cặp key-value, UTF-8, không metadata/
  language/engine/source/path/status trong file cuối. Key trùng giữ bản
  ghi đầu (đặc tả 7.5); key sinh đã có sẵn từ plugin (row_000001/s_...).
- Report: extraction_report.txt (người đọc) + extraction_report.json
  (máy đọc, mã lỗi chuẩn) — đặc tả 7.6.
- Verify xuất (7.7): json.loads lại được, UTF-8, đếm entry khớp, placeholder
  giữ nguyên, không cắt Unicode.

Chỉ thư viện chuẩn, Python >= 3.8.
"""
from __future__ import annotations

import io
import json
import re
from typing import Any, Dict, List, Optional, Tuple

from core.errors import PB3Error

# Placeholder giữ nguyên khi chuẩn hóa (đặc tả 7.5): {0}, {name}, %s, %d,
# %1$s, <color>, \n, escape sequence, thẻ HTML/rich text.
# GIỮ ĐỒNG BỘ với sdk/gate.py::_PLACEHOLDER_RE và
# sdk/translate.py::_PLACEHOLDER_RE: cổng đếm cả $VAR và {nội dung bất
# kỳ} — placeholders_ok phải thấy chúng thì mới cảnh báo + không cache
# bản dịch làm mất chúng.
_PLACEHOLDER_RES = [
    re.compile(r"\{[^}]*\}"),                  # {0}, {name}, {foo-bar}, {}
    re.compile(r"%(\d+\$)?[sdif]"),            # %s, %d, %1$s
    re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*"),    # $VAR
    re.compile(r"<[A-Za-z][^>]*>"),            # <color>, <b>
    re.compile(r"\\[nrt\\'\"]"),               # \n, \t, \\, \'
]


def placeholders(text: str) -> List[str]:
    """Các placeholder trong một chuỗi, theo thứ tự xuất hiện (không trùng)."""
    found: List[str] = []
    for rx in _PLACEHOLDER_RES:
        for m in rx.finditer(text or ""):
            if m.group(0) not in found:
                found.append(m.group(0))
    return found


def check_placeholders(source: Dict[str, str], exported: Dict[str, str]) -> List[Dict[str, Any]]:
    """So placeholder từng key: nguồn vs file xuất. Trả danh sách lệch
    (rỗng = giữ nguyên hết)."""
    bad: List[Dict[str, Any]] = []
    for key, value in source.items():
        if key not in exported:
            continue
        want = placeholders(value)
        got = placeholders(exported[key])
        if want != got:
            bad.append({"key": key, "source": want, "exported": got})
    return bad


def check_mojibake(values: List[str]) -> List[str]:
    """Heuristic mojibake (đặc tả 7.7): chuỗi chứa 'Ã©'/'Ã¨'... do UTF-8
    bị đọc nhầm thành latin-1. Trả các giá trị nghi ngờ (cắt ở 200 ký tự)."""
    bad = []
    for v in values:
        if "Ã" in v and re.search(r"Ã[©¨ª«­°±]", v):
            bad.append(v[:200])
    return bad


def export_key_value_json(kv: Dict[str, str]) -> bytes:
    """JSON key-value cuối (UTF-8, chỉ key-value, giữ đúng thứ tự nguồn).

    SPEED-EXTRACT (2026-11-18): ghi streaming qua iterencode thay vì
    `json.dumps(...) + "\\n"` rồi `.encode("utf-8")` — bản cũ giữ đồng thời
    1 str + 1 bytes cỡ toàn bộ output trong RAM (đỉnh = ~2x output).
    Byte-identical với dumps (dumps chính là "".join(iterencode)); test
    test_export_key_value_json_streaming_matches_dumps khóa điều này.
    """
    buf = io.BytesIO()
    enc = json.JSONEncoder(ensure_ascii=False, indent=2)
    for chunk in enc.iterencode(kv):
        buf.write(chunk.encode("utf-8"))
    buf.write(b"\n")
    return buf.getvalue()


def verify_export_bytes(data: bytes, expected: Dict[str, str]) -> Dict[str, Any]:
    """Kiểm lại file JSON đã xuất (đặc tả 7.7): json.loads được, UTF-8,
    số entry khớp, placeholder giữ nguyên, không cắt Unicode.

    Trả {"ok": bool, "problems": [...]}. Lỗi giải mã -> E_CORRUPT.

    SPEED-EXTRACT (2026-11-18): json.loads trực tiếp trên bytes thay vì
    `data.decode("utf-8")` rồi loads(str) — bỏ 1 bản copy str cỡ toàn bộ
    file. Thông điệp lỗi giữ nguyên: UTF-8 hỏng -> UnicodeDecodeError (con
    của ValueError) bắt riêng trước; JSON hỏng -> ValueError.
    """
    try:
        doc = json.loads(data)
    except UnicodeDecodeError as exc:
        raise PB3Error("E_CORRUPT", "file xuất không phải UTF-8: %s" % str(exc)[:60])
    except ValueError as exc:
        raise PB3Error("E_CORRUPT", "file xuất JSON hỏng: %s" % str(exc)[:60])
    problems: List[str] = []
    if not isinstance(doc, dict):
        problems.append("file xuất không phải object key-value")
        return {"ok": False, "problems": problems}
    if len(doc) != len(expected):
        problems.append("số entry khác: nguồn %d, xuất %d" % (len(expected), len(doc)))
    for key, value in expected.items():
        if key not in doc:
            problems.append("thiếu key: %s" % key)
        elif doc[key] != value:
            problems.append("giá trị key %s khác nguồn" % key)
    for item in check_placeholders(expected, doc):
        problems.append("key %s mất placeholder: %s -> %s"
                        % (item["key"], item["source"], item["exported"]))
    for v in check_mojibake(list(doc.values())):
        problems.append("nghi mojibake: %r" % v[:60])
    return {"ok": not problems, "problems": problems}


def report_text(report: Dict[str, Any]) -> str:
    """Báo cáo người đọc (extraction_report.txt)."""
    lines = ["BÁO CÁO TRÍCH XUẤT NGÔN NGỮ (PB3 v3.1)", ""]
    lines.append("nguồn: %s" % report.get("source", "?"))
    lines.append("engine: %s (%s, tin cậy %.2f)"
                 % (report.get("engine", "unknown"), report.get("engine_level", "?"),
                    report.get("engine_confidence", 0.0)))
    for reason in report.get("engine_reasons", []):
        lines.append("  lý do: " + reason)
    lines.append("thời gian: %.1f giây" % report.get("elapsed_s", 0.0))
    lines.append("")
    lines.append("file đã trích xuất (%d):" % len(report.get("files", [])))
    for f in report.get("files", []):
        lines.append("  - %s [%s, locale %s]: %d entry (%d ok, %d rỗng, %d trùng, %d chưa parse)"
                     % (f.get("path", "?"), f.get("plugin", "?"), f.get("locale", "?"),
                        f.get("entries", 0), f.get("ok", 0), f.get("empty", 0),
                        f.get("conflicts", 0), f.get("unparsed", 0)))
        if f.get("exported"):
            # Nguồn gốc đầy đủ: file xuất ← đơn vị Unity/file nguồn trong IPA.
            lines.append("      %s ← %s" % (f["exported"],
                                            f.get("source") or f.get("path", "?")))
        for w in f.get("warnings", []):
            lines.append("      cảnh báo: " + w)
    if report.get("merged_files"):
        lines.append("")
        lines.append("gộp thành 1 file mỗi ngôn ngữ (%d):" % len(report["merged_files"]))
        for m in report["merged_files"]:
            lines.append("  - %s: %d key (gộp từ %d file, %d key trùng giống hệt đã gộp, "
                         "%d key trùng khác giá trị — giữ bản đầu)"
                         % (m.get("file", "?"), m.get("keys", 0),
                            len(m.get("sources", [])), m.get("duplicates_identical", 0),
                            m.get("conflicts_total", 0)))
            for src in m.get("sources", []):
                lines.append("      gộp từ: %s" % src)
            for c in m.get("conflicts", [])[:10]:
                lines.append("      xung đột key %s: giữ %r ← %s; bỏ %r ← %s"
                             % (c.get("key", "?"),
                                (c.get("kept") or {}).get("value", "?"),
                                (c.get("kept") or {}).get("source", "?"),
                                (c.get("dropped") or {}).get("value", "?"),
                                (c.get("dropped") or {}).get("source", "?")))
            if m.get("conflicts_total", 0) > len(m.get("conflicts", [])):
                lines.append("      ... và %d xung đột khác (xem extraction_report.json)"
                             % (m["conflicts_total"] - len(m.get("conflicts", []))))
    if report.get("skipped"):
        lines.append("")
        lines.append("file bỏ qua (%d):" % len(report["skipped"]))
        for s in report["skipped"]:
            lines.append("  - %s: %s" % (s.get("path", "?"), s.get("reason", "?")))
    if report.get("errors"):
        lines.append("")
        lines.append("lỗi (%d):" % len(report["errors"]))
        for e in report["errors"]:
            lines.append("  - %s: [%s] %s" % (e.get("path", "?"), e.get("code", "?"),
                                              e.get("message", "?")))
    if report.get("generated_keys"):
        lines.append("")
        lines.append("key sinh (%d): %s"
                     % (len(report["generated_keys"]),
                        ", ".join(report["generated_keys"][:10])))
        if len(report["generated_keys"]) > 10:
            lines.append("  ... và %d key sinh khác" % (len(report["generated_keys"]) - 10))
    lines.append("")
    lines.append("tổng: %d entry đã xuất, %d file" % (report.get("total_entries", 0),
                                                       len(report.get("files", []))))
    return "\n".join(lines) + "\n"


def report_json(report: Dict[str, Any]) -> bytes:
    """Báo cáo máy đọc (extraction_report.json): cùng nội dung, có mã lỗi."""
    return (json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
