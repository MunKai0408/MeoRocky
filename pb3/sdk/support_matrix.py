"""Ma trận hỗ trợ sinh tự động (Claude 09; đặc tả §10.2–10.3, decisions #51, #113).

Sinh từ: hàng registry.rows() do tầng services truyền vào (dùng maturity
HIỆU LỰC, không dùng mức khai — sdk KHÔNG được import registry, decisions
#129, test kiến trúc test_core_does_not_import_registry),
tests/corpus/<id>.json (kết quả PASS read/write/verify + ngày test = mtime
của file), sdk.containers.support_matrix(), sdk.unity.support_matrix() và
số game đã test từ KB (kb.plugin_stats() nếu KB tồn tại, không có KB thì 0).

KHÔNG tuyên bố M4 cho bất cứ thứ gì: M4 cần bằng chứng game thật + byte-diff
sạch, hiện chưa có. Danh sách "đã xác minh trên game thật" CHỈ có Unity
inspect trên IPA thật 9 Kings v1.0.5 (nguồn: handoff/account_05.md §5);
patch Unity mới test fixture nên vẫn ở danh sách fixture.

Chỉ dùng thư viện chuẩn, Python 3.8+. Không gọi mạng.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core import safe_io
from sdk import containers as _containers
from sdk import unity as _unity

WARNING = "Mức hỗ trợ chỉ đúng với những gì đã kiểm thử"

# Bằng chứng game thật duy nhất (handoff/account_05.md §5).
REAL_GAME = [
    {"muc": "Unity inspect (đọc + bản đồ dữ liệu) trên IPA thật 9 Kings v1.0.5",
     "chi_tiet": "Unity inspect đã chạy trên IPA game thật; hỗ trợ UnityFS v8 "
                 "bổ sung ở Claude 05b. Patch Unity chỉ mới test fixture "
                 "(xem danh sách fixture).",
     "nguon": "handoff/account_05.md §5"},
]

# Chưa hỗ trợ, kèm lý do (§10.3).
UNSUPPORTED = [
    {"muc": "7z", "ly_do": "chưa có backend (không thêm phụ thuộc chưa kiểm chứng)"},
    {"muc": ".locmeta nhị phân (Unreal)",
     "ly_do": "chưa có parser; chỉ .locres được đọc"},
    {"muc": "Godot PCK v0/v3", "ly_do": "chỉ v1/v2 có fixture"},
    {"muc": "Unreal PAK entry nén/mã hóa/Oodle",
     "ly_do": "chỉ đọc entry không nén, không mã hóa"},
    {"muc": "SerializedFile v<15 / v>26", "ly_do": "chỉ v15–26 có fixture"},
    {"muc": "Bundle Unity mã hóa",
     "ly_do": "báo E_ENCRYPTED, không giải mã"},
    {"muc": "Cocos/Defold/GameMaker",
     "ly_do": "chỉ nhận diện, chưa trích xuất"},
]


def _corpus_date(corpus_dir: Path, plugin_id: str) -> str:
    """Ngày test = mtime (UTC, YYYY-MM-DD) của tests/corpus/<id>.json; không có thì "chưa có"."""
    path = corpus_dir / (plugin_id + ".json")
    try:
        mtime = os.path.getmtime(str(path))
    except OSError:
        return "chưa có"
    return datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%d")


def _corpus_results(corpus_dir: Path, plugin_id: str) -> Dict[str, str]:
    """Kết quả PASS read/write/verify trong tests/corpus/<id>.json ({} nếu không có/hỏng)."""
    path = corpus_dir / (plugin_id + ".json")
    try:
        with open(str(path), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, dict):
        return {}
    return {k: v for k, v in results.items() if v == "PASS"}


def _kb_plugin_stats(root: Path) -> Dict[str, int]:
    """Số game đã test theo plugin, từ KB (kb.plugin_stats()) nếu KB tồn tại; không có KB thì {}.

    Mọi lỗi (chưa có sdk/kb.py, chưa có file KB, KB không có plugin_stats)
    đều trả {} — ma trận vẫn sinh được, cột số game hiện 0."""
    kb_file = root / "kb" / "pb3_kb.sqlite"
    if not kb_file.is_file():
        return {}
    try:
        from sdk.kb import KB
        kb = KB(str(kb_file))
    except Exception:  # noqa: BLE001 - KB chưa có/lệch giao diện: coi như không có KB
        return {}
    try:
        stats = getattr(kb, "plugin_stats", None)
        if not callable(stats):
            return {}
        out = stats()
        if not isinstance(out, dict):
            return {}
        return {str(k): int(v) for k, v in out.items()}
    except Exception:  # noqa: BLE001 - KB lỗi: không làm hỏng ma trận
        return {}
    finally:
        try:
            kb.close()
        except Exception:  # noqa: BLE001
            pass


def _limit_note(row: Dict[str, object], manifest) -> str:
    """Giới hạn đã biết của một plugin, ngắn gọn cho bảng."""
    reason = str(row.get("reason") or "")
    state = str(row.get("state") or "")
    if state == "disabled" and reason == "người dùng đã tắt":
        return "người dùng đã tắt"
    if reason and state != "ok":
        return reason
    if manifest is not None:
        caps = list(getattr(manifest, "capabilities", []) or [])
        kind = str(getattr(manifest, "kind", "") or row.get("kind") or "")
        if kind == "engine" or caps == ["hint"]:
            return "chỉ nhận diện (chưa trích xuất)"
        if "write" not in caps:
            return "chỉ đọc"
        return "ghi ngược (mới test fixture)"
    kind = str(row.get("kind") or "")
    if kind == "engine":
        return "chỉ nhận diện (chưa trích xuất)"
    return "mới test fixture"


def _esc(value: object) -> str:
    """Một ô bảng Markdown: bỏ xuống dòng và '|' để không vỡ bảng."""
    return str(value).replace("|", "/").replace("\n", " ").replace("\r", " ").strip() or "—"


def generate(root, rows=None, manifests=None, kb_stats=None) -> Tuple[str, dict]:
    """Sinh ma trận hỗ trợ. Trả (markdown, data).

    `root`: thư mục gốc dự án. `rows`: hàng registry.rows() (dict id/kind/
    engines/maturity/maturity_effective/version/scope/state/reason) do tầng
    services truyền vào — sdk KHÔNG được import registry (decisions #129).
    `rows=None` thì bảng plugin trống kèm ghi chú (ma trận vẫn sinh được).
    `manifests`: dict plugin_id -> manifest (để ghi giới hạn đã biết);
    None thì {}. `kb_stats`: dict plugin_id -> số game đã test; None thì
    đọc từ KB. Xác định: hàng sắp theo id, ngày test theo mtime UTC của
    file corpus.
    """
    root = Path(root)
    if rows is None:
        rows = []
        no_registry_note = True
    else:
        no_registry_note = False
    rows = sorted(rows, key=lambda r: str(r.get("id") or ""))
    manifests = dict(manifests or {})
    corpus_dir = root / "tests" / "corpus"
    if kb_stats is None:
        kb_stats = _kb_plugin_stats(root)

    plugins = []
    for r in rows:
        pid = str(r.get("id") or "?")
        eff = str(r.get("maturity_effective") or r.get("maturity") or "?")
        plugins.append({
            "id": pid,
            "kind": str(r.get("kind") or "?"),
            "engine": ",".join(r.get("engines") or []) or "—",
            "maturity": str(r.get("maturity") or "?"),
            "maturity_effective": eff,
            "games_tested": int(kb_stats.get(pid, 0)),
            "test_date": _corpus_date(corpus_dir, pid),
            "corpus": _corpus_results(corpus_dir, pid),
            "state": str(r.get("state") or "?"),
            "limit": _limit_note(r, manifests.get(pid)),
        })

    container_rows = _containers.support_matrix()
    unity_rows = _unity.support_matrix()
    fixture_only = [p["id"] for p in plugins]
    # Unity/Godot/Unreal đọc-ghi cũng mới test fixture (patch Unity chưa có game thật).
    fixture_only += ["UnityFS v6/v7/v8/v9 + SerializedFile v15–26 (Zstd chỉ đọc trên web, fixture)",
                     "Godot PCK v1/v2 (đọc, fixture)",
                     "Unreal PAK UE4/UE5 (đọc, fixture)"]

    data = {
        "plugins": plugins,
        "containers": container_rows,
        "unity": unity_rows,
        "real_game": [dict(x) for x in REAL_GAME],
        "fixture_only": fixture_only,
        "unsupported": [dict(x) for x in UNSUPPORTED],
    }

    lines = []
    lines.append("# MA TRẬN HỖ TRỢ — PB3 v3.1")
    lines.append("")
    lines.append("> **Cảnh báo: %s.**" % WARNING)
    lines.append("> Không có mục nào đạt M4 (M4 cần bằng chứng game thật + "
                 "byte-diff sạch — chưa có).")
    lines.append("> Mức hiệu lực (maturity hiệu lực) tính theo bằng chứng "
                 "tests/corpus/<id>.json (decisions #51): plugin khai M3/M4 "
                 "mà thiếu bằng chứng thì bị hạ, sàn M2.")
    if no_registry_note:
        lines.append("> Chưa nạp registry — bảng plugin trống (gọi generate "
                     "với rows từ Registry đã dựng ở tầng services).")
    lines.append("")
    lines.append("## Bảng plugin")
    lines.append("")
    lines.append("| Plugin | Loại | Engine | Maturity hiệu lực | Số game đã test | Ngày test | Giới hạn đã biết |")
    lines.append("|---|---|---|---|---|---|---|")
    for p in plugins:
        lines.append("| %s | %s | %s | %s | %d | %s | %s |" % (
            _esc(p["id"]), _esc(p["kind"]), _esc(p["engine"]),
            _esc(p["maturity_effective"]), p["games_tested"],
            _esc(p["test_date"]), _esc(p["limit"])))
    lines.append("")
    lines.append("## Bảng engine / định dạng")
    lines.append("")
    lines.append("### Container / archive")
    lines.append("")
    lines.append("| Định dạng | Đọc | Ghi | Backend |")
    lines.append("|---|---|---|---|")
    for c in container_rows:
        lines.append("| %s | %s | %s | %s |" % (
            _esc(c.get("format")), _esc(c.get("read")), _esc(c.get("write")),
            _esc(c.get("backend"))))
    lines.append("")
    lines.append("### Unity")
    lines.append("")
    lines.append("| Định dạng | Đọc | Ghi | Backend | Maturity |")
    lines.append("|---|---|---|---|---|")
    for u in unity_rows:
        lines.append("| %s | %s | %s | %s | %s |" % (
            _esc(u.get("format")), _esc(u.get("read")), _esc(u.get("write")),
            _esc(u.get("backend")), _esc(u.get("maturity", "—"))))
    lines.append("")
    lines.append("## Đã xác minh trên game thật")
    lines.append("")
    for item in REAL_GAME:
        lines.append("- **%s** — %s (nguồn: %s)." % (
            item["muc"], item["chi_tiet"], item["nguon"]))
    lines.append("")
    lines.append("## Mới test fixture")
    lines.append("")
    lines.append("Mọi plugin/engine còn lại có fixture (chưa test game thật):")
    lines.append("")
    for name in fixture_only:
        lines.append("- %s" % name)
    lines.append("")
    lines.append("## Chưa hỗ trợ")
    lines.append("")
    for item in UNSUPPORTED:
        lines.append("- **%s** — %s." % (item["muc"], item["ly_do"]))
    lines.append("")
    lines.append("Khi gặp định dạng chưa hỗ trợ: cần plugin mới cho định dạng đó; "
                 "có thể dùng extractor `generic_strings` để dò chuỗi thô "
                 "(chỉ đọc chuỗi, không phải bảng localization đã xác minh).")
    lines.append("")
    return "\n".join(lines), data


def write_matrix(root, rows=None, manifests=None, kb_stats=None) -> str:
    """Sinh ma trận rồi ghi nguyên tử vào docs/SUPPORT_MATRIX.md. Trả đường dẫn (str).

    `rows`/`manifests`/`kb_stats` như generate(); None thì như generate()
    (bảng plugin trống khi không có rows)."""
    root = Path(root)
    markdown, _data = generate(root, rows=rows, manifests=manifests, kb_stats=kb_stats)
    dest = root / "docs" / "SUPPORT_MATRIX.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    safe_io.atomic_write_bytes(dest, markdown.encode("utf-8"))
    return str(dest)
