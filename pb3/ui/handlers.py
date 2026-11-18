"""Bảng chức năng: nối mục menu/lệnh CLI tới module trong services/.

HỢP ĐỒNG DỊCH VỤ (cho Claude 02..10; KHÔNG cần sửa menu.py/ui/ để gắn chức năng):
  Tạo services/<tên>.py (và services/__init__.py nếu chưa có) với:
      def run_interactive(app) -> None      # màn hình tương tác (tuỳ chọn)
      def run_cli(app, ns) -> int           # ns = argparse.Namespace; trả mã thoát 0..4
      def add_arguments(subparser) -> None  # (tuỳ chọn) thêm cờ CLI riêng cho lệnh
  `app` là core.app.App. Khi module tồn tại, menu/CLI tự gọi nó; khi chưa có, menu
  hiển thị trạng thái 'chưa triển khai' (KHÔNG giả lập). Lỗi bên trong dịch vụ bị
  cô lập: menu vẫn chạy, người dùng thấy mã lỗi + log, không thấy traceback.
THANH TIẾN ĐỘ: menu/CLI đã tự bọc mọi dịch vụ bằng app.job(); dịch vụ chỉ cần gọi
`app.report_progress(da_xong/tong, note=...)` (hoặc app.overall.begin("Tên bước")) để thanh chính xác hơn.
Sau khi một chức năng thật sự chạy, hãy cập nhật maturity trong bảng dưới đây
CHỈ KHI có bằng chứng test (đặc tả 10.1).
"""
from __future__ import annotations

import importlib
import importlib.util
from dataclasses import dataclass
from typing import Dict, Optional, Tuple


@dataclass(frozen=True)
class Feature:
    key: str
    label_vi: str
    module: str
    owner: str
    maturity: str = "M0 PLANNED"
    note_vi: str = ""
    builtin: str = ""      # phần ĐÃ có sẵn trong lõi (nếu có), mô tả ngắn
    partial_vi: str = ""   # phần của mục này CHƯA có (hiện inline sau trạng thái, vd ma trận hỗ trợ)


# Nhãn khớp đặc tả 12.1 từng chữ.
MENU: Tuple[Tuple[str, Feature], ...] = (
    ("1", Feature("mod", "Mod và ghi ngược (đa engine, Unity hạng nhất)", "services.unity_tool",
                  "Claude 05 (Unity, ghi ngược)", maturity="M2",
                  note_vi="UnityFS v6/v7/v8 + SerializedFile v15-26, patch TextAsset (M2).")),
    ("2", Feature("extract", "Trích xuất ngôn ngữ (đa engine)", "services.language_tool",
                  "Claude 07 (pipeline trích xuất)", maturity="M2",
                  note_vi="Discovery + engine detect + arbitration + export JSON + ghi ngược (M2).")),
    ("3", Feature("plugins", "Quản lý plugin và ma trận hỗ trợ", "services.plugin_manager",
                  "Claude 03 (plugin) và Claude 09 (support, KB)")),
    ("4", Feature("batch", "Xử lý hàng loạt", "services.batch_tool", "Claude 09 (batch)",
                  maturity="M2",
                  note_vi="manifest CSV/JSON, chạy tuần tự/song song, checkpoint/resume, dry-run (M2).")),
    ("5", Feature("diagnose", "Chẩn đoán và điều tra dữ liệu", "services.diagnostics_tool",
                  "Claude 04+ (inventory, hex/strings) / Claude 07 (verbose)",
                  maturity="M2",
                  note_vi="inventory/hex/strings/encoding, chỉ đọc (đợt tổng cuối).",
                  builtin="môi trường iSH, cách ly tệp tạm")),
    ("6", Feature("compress", "Nén IPA kiểu WinRAR (solid LZMA)", "services.compress_tool",
                  "Đợt tổng cuối (nhóm nén IPA)", maturity="M2",
                  note_vi="đầu vào ≤2GB, đích ≤400MB, không quá 500MB; archive lưu trữ, giải nén lại được IPA gốc.")),
    ("7", Feature("font", "Công cụ font (4 chế độ: quét/gộp/thay font)", "services.font_tool",
                  "Đợt tổng cuối (nhóm font)", maturity="M2",
                  note_vi="quét/gộp/thay font để game hiển thị đúng tiếng Việt; atlas SDF tái tạo được (thử nghiệm, nút TMP).")),
)

# Lệnh CLI (đặc tả 12.2)
COMMANDS: Dict[str, Feature] = {
    "mod": MENU[0][1],
    "extract": MENU[1][1],
    "translate": Feature("translate", "dịch tự động bảng JSON (Google Translate)",
                         "services.translate_tool", "Dịch tự động (sau Claude 07)"),
    "batch": MENU[3][1],
    "diagnose": MENU[4][1],
    "plugin": Feature("plugin", "plugin list|test|new|enable|disable", "services.plugin_manager", "Claude 03"),
    "container": Feature("container", "container list|read <file>", "services.container_tool", "Claude 04"),
    "unity": Feature("unity", "unity inspect|read|patch <file>", "services.unity_tool", "Claude 05"),
    "support": Feature("support", "support [--write]", "services.plugin_manager", "Claude 09"),
    "font": Feature("font", "font scan|rebuild|replace|bundle <path>", "services.font_tool",
                    "Đợt tổng cuối (nhóm font)", maturity="M2",
                    note_vi="Quét/gộp/thay font tiếng Việt 4 chế độ (M2)."),
    "kb": Feature("kb", "kb export|import|forget", "services.plugin_manager", "Claude 09"),
    "compress": Feature("compress", "nén IPA kiểu WinRAR (solid LZMA)", "services.compress_tool", "Final (nhóm nén IPA)"),
    "decompress": Feature("decompress", "khôi phục IPA từ archive nén PB3", "services.compress_tool", "Final (nhóm nén IPA)"),
}


def module_present(feature: Feature) -> bool:
    """True nếu services/<module> tồn tại (find_spec, KHÔNG import -> khởi động nhanh)."""
    try:
        return importlib.util.find_spec(feature.module) is not None
    except (ImportError, ValueError, AttributeError):
        return False


def load_module(feature: Feature):
    """Import module dịch vụ. Trả None nếu module KHÔNG tồn tại; lỗi bên trong module
    được ném lại (để lớp gọi cô lập thành E_INTERNAL, không nuốt lỗi thật)."""
    if not module_present(feature):
        return None
    return importlib.import_module(feature.module)


def status_vi(feature: Feature) -> str:
    if module_present(feature):
        base = "đã nối dịch vụ (%s)" % feature.module
    elif feature.builtin:
        base = "MỘT PHẦN: có %s; phần còn lại chưa triển khai" % feature.builtin
    else:
        base = "chưa triển khai (%s)" % feature.owner.split(" (")[0]
    if feature.partial_vi:
        base += "; " + feature.partial_vi
    return base
