"""Mã lỗi chuẩn PB3 (đặc tả Phụ lục D) + ánh xạ mã thoát CLI (12.2).

Mỗi lỗi có: message_vi, detail, plugin_id, where, hint (gợi ý bước tiếp theo).
Người dùng không bao giờ chỉ thấy traceback; traceback đi vào logs/.

Mã MỞ RỘNG (không có trong Phụ lục D, do Claude 01 thêm, xem
handoff/decisions.md): E_UNSUPPORTED_ON_ISH (đặc tả mục 17 nhắc tên),
E_CONFIG_INVALID, E_TOO_LARGE, E_IO, E_CORRUPT, E_NOT_IMPLEMENTED,
E_NETWORK (dịch tự động), E_TIMEOUT (batch, Claude 09).
"""
from __future__ import annotations

import errno
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

# Mã thoát CLI (đặc tả 12.2)
EXIT_OK = 0
EXIT_USER = 1          # lỗi người dùng
EXIT_UNSUPPORTED = 2   # lỗi định dạng không hỗ trợ
EXIT_SAFETY = 3        # lỗi an toàn / từ chối ghi
EXIT_INTERNAL = 4      # lỗi nội bộ


@dataclass(frozen=True)
class ErrorSpec:
    code: str
    message_vi: str
    hint_vi: str
    exit_code: int
    extension: bool = False


def _s(code, msg, hint, exit_code, ext=False):
    return code, ErrorSpec(code, msg, hint, exit_code, ext)


ERROR_SPECS: Dict[str, ErrorSpec] = dict([
    # --- Phụ lục D ---
    _s("E_OK", "Thành công.", "", EXIT_OK),
    _s("E_INPUT_NOT_FOUND", "Đầu vào không tồn tại hoặc không đọc được.",
       "Kiểm tra đường dẫn và quyền đọc; file phải nằm trong vùng iSH.", EXIT_USER),
    _s("E_BAD_ARCHIVE", "Archive hỏng (CRC sai hoặc cấu trúc lỗi).",
       "Tải/sao chép lại file gốc rồi thử lại.", EXIT_UNSUPPORTED),
    _s("E_ARCHIVE_BOMB", "Vượt ngân sách giải nén/số entry/tỉ lệ nén.",
       "Dừng an toàn. Chỉ nâng giới hạn trong config/local.toml nếu tin cậy file.", EXIT_SAFETY),
    _s("E_UNSAFE_PATH", "Đường dẫn không an toàn (traversal, symlink, tên lạ).",
       "Dùng tên tương đối ngắn, không chứa '..' hoặc ký tự điều khiển.", EXIT_SAFETY),
    _s("E_NO_CANDIDATE", "Không tìm được file/định dạng ứng viên.",
       "Kiểm tra lại đường dẫn; dùng 'container list <file>' để xem file có phải gói hỗ trợ không.",
       EXIT_UNSUPPORTED),
    _s("E_AMBIGUOUS", "Nhiều ứng viên ngang điểm, cần người dùng chọn.",
       "Chọn một ứng viên trong danh sách.", EXIT_USER),
    _s("E_UNSUPPORTED_FORMAT", "Đã nhận diện nhưng chưa có plugin parse được.",
       "Dùng 'container list <file>' để xem nội dung gói; nếu là bundle Unity v9+ hoặc đã mã hóa thì PB3 chưa hỗ trợ — giữ file gốc.",
       EXIT_UNSUPPORTED),
    _s("E_ENCRYPTED", "Dữ liệu hoặc binary được mã hóa/bảo vệ.",
       "PB3 không giải mã; dừng an toàn.", EXIT_UNSUPPORTED),
    _s("E_MISSING_DATA", "Dữ liệu nằm ngoài gói (ODR/remote/Addressables).",
       "Dữ liệu không có trong gói; PB3 không truy cập server.", EXIT_UNSUPPORTED),
    _s("E_PARSE_PARTIAL", "Chỉ parse được một phần; có entry lỗi/bỏ qua.",
       "Xem báo cáo để biết entry bị bỏ qua.", EXIT_UNSUPPORTED),
    _s("E_LOCALE_NOT_FOUND", "Game không có locale được chọn.",
       "Chọn một locale có trong danh sách.", EXIT_USER),
    _s("E_WRITE_DISABLED", "Ghi bị tắt (maturity thấp hoặc chưa cho phép ghi).",
       "Ghi chỉ mở khi dùng 'unity patch ... --allow-write' (hoặc 'mod' có xác nhận); plugin phải đạt maturity yêu cầu.",
       EXIT_SAFETY),
    _s("E_VERIFY_FAILED", "Xác minh sau khi ghi không đạt; từ chối xuất file.",
       "Bản tạm được giữ lại để điều tra; không dùng để thay game.", EXIT_SAFETY),
    _s("E_OUTPUT_EXISTS", "Output đã tồn tại và chưa bật versioning.",
       "Bật versioning hoặc chọn tên khác; PB3 không ghi đè.", EXIT_USER),
    _s("E_NO_SPACE", "Không đủ dung lượng trống.",
       "Giải phóng bộ nhớ trong iSH hoặc giảm phạm vi thao tác.", EXIT_USER),
    _s("E_PLUGIN_INVALID", "Manifest sai, api_version lệch hoặc không import được.",
       "Chạy 'plugin test <id>' để xem lý do.", EXIT_INTERNAL),
    _s("E_PLUGIN_TIMEOUT", "Hook vượt thời gian cho phép.",
       "Tăng hook_timeout_s nếu cần hoặc dùng file nhỏ hơn.", EXIT_INTERNAL),
    _s("E_PLUGIN_FORBIDDEN", "Plugin cố dùng socket/subprocess/đọc ngoài ctx.",
       "Plugin bị chặn. Kiểm tra mã plugin.", EXIT_SAFETY),
    _s("E_CANCELLED", "Người dùng đã hủy.",
       "Thao tác dừng an toàn; file tạm đã được dọn.", EXIT_USER),
    _s("E_INTERNAL", "Lỗi nội bộ.",
       "Xem traceback trong logs/ và báo lỗi kèm mã này.", EXIT_INTERNAL),
    # --- Mở rộng (Claude 01) ---
    _s("E_UNSUPPORTED_ON_ISH", "Tính năng không chạy được trực tiếp trên iSH.",
       "Dùng phương án local khác; PB3 không chuyển xử lý sang máy chủ ngoài.",
       EXIT_UNSUPPORTED, True),
    _s("E_CONFIG_INVALID", "Cấu hình không hợp lệ.",
       "Sửa config/local.toml hoặc xóa file này để dùng mặc định.", EXIT_USER, True),
    _s("E_TOO_LARGE", "File/entry vượt giới hạn kích thước.",
       "Chỉ nâng giới hạn trong config/local.toml khi iSH đủ RAM/dung lượng.",
       EXIT_SAFETY, True),
    _s("E_IO", "Lỗi đọc/ghi tệp.",
       "Kiểm tra quyền ghi và dung lượng của thư mục làm việc.", EXIT_INTERNAL, True),
    _s("E_CORRUPT", "Tệp hỏng hoặc bị cắt cụt.",
       "Dùng bản gốc nguyên vẹn; PB3 không đoán dữ liệu hỏng.", EXIT_UNSUPPORTED, True),
    _s("E_NOT_IMPLEMENTED", "Chức năng chưa được triển khai.",
       "Chức năng này chưa có trong phiên bản hiện tại; xem PROGRESS.md để biết lộ trình.",
       EXIT_UNSUPPORTED, True),
    _s("E_NETWORK", "Lỗi mạng khi gọi dịch vụ dịch.",
       "Kiểm tra mạng trên iSH rồi thử lại; không có mạng thì dùng --provider offline.",
       EXIT_USER, True),
    _s("E_TIMEOUT", "Hết thời gian cho phép.",
       "Tăng --timeout-game hoặc dùng file nhỏ hơn / jobs=1; "
       "timeout là best-effort (thread nền không dừng được ngay).",
       EXIT_INTERNAL, True),
])

_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
MAX_DETAIL_CHARS = 300


def sanitize_detail(text: Any, limit: int = MAX_DETAIL_CHARS) -> str:
    """Rút gọn + bỏ ký tự điều khiển để detail không làm lộ/đổ nội dung file."""
    s = _CTRL.sub("?", str(text))
    if len(s) > limit:
        s = s[:limit] + "…[+%d ký tự]" % (len(s) - limit)
    return s


class PB3Error(Exception):
    """Lỗi PB3 có mã chuẩn. KHÔNG đặt nội dung file người dùng vào detail."""

    def __init__(self, code: str, detail: str = "", *, plugin_id: Optional[str] = None,
                 where: Optional[str] = None, hint: Optional[str] = None,
                 message_vi: Optional[str] = None):
        spec = ERROR_SPECS.get(code)
        if spec is None:  # mã lạ -> không im lặng, quy về E_INTERNAL
            detail = "mã lỗi không hợp lệ %r; %s" % (code, detail)
            code, spec = "E_INTERNAL", ERROR_SPECS["E_INTERNAL"]
        self.code = code
        self.spec = spec
        self.message_vi = message_vi or spec.message_vi
        self.detail = sanitize_detail(detail) if detail else ""
        self.plugin_id = plugin_id
        self.where = sanitize_detail(where, 200) if where else None
        self.hint = hint if hint is not None else spec.hint_vi
        super().__init__("%s: %s" % (code, self.message_vi))

    @property
    def exit_code(self) -> int:
        return self.spec.exit_code

    def to_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "message_vi": self.message_vi, "detail": self.detail,
                "plugin_id": self.plugin_id, "where": self.where, "hint": self.hint,
                "exit_code": self.exit_code}


def error_from_exception(exc: BaseException, *, where: Optional[str] = None,
                         op: str = "read") -> PB3Error:
    """Chuẩn hóa ngoại lệ hệ thống thành PB3Error (không để lộ traceback)."""
    import zipfile
    if isinstance(exc, PB3Error):
        return exc
    if isinstance(exc, KeyboardInterrupt):
        return PB3Error("E_CANCELLED", where=where)
    if isinstance(exc, MemoryError):
        return PB3Error("E_TOO_LARGE", "hết bộ nhớ khi xử lý", where=where)
    if isinstance(exc, zipfile.BadZipFile):
        return PB3Error("E_BAD_ARCHIVE", "cấu trúc zip lỗi", where=where)
    if isinstance(exc, UnicodeDecodeError):
        return PB3Error("E_CORRUPT", "giải mã %s thất bại ở byte %d" % (exc.encoding, exc.start),
                        where=where, hint="Có thể file dùng encoding khác hoặc bị hỏng.")
    if isinstance(exc, OSError):
        no = getattr(exc, "errno", None)
        if no == errno.ENOSPC or no == getattr(errno, "EDQUOT", -1):
            return PB3Error("E_NO_SPACE", where=where)
        if no == errno.ENAMETOOLONG:
            return PB3Error("E_UNSAFE_PATH", "tên/đường dẫn quá dài", where=where)
        if op == "read" and isinstance(exc, (FileNotFoundError, PermissionError,
                                             NotADirectoryError, IsADirectoryError)):
            return PB3Error("E_INPUT_NOT_FOUND", type(exc).__name__, where=where)
        if op == "write" and isinstance(exc, PermissionError):
            # Ghi bị từ chối quyền: KHÔNG nói 'đầu vào không tồn tại' (như nhánh read).
            return PB3Error("E_IO", "không có quyền ghi: %s" % (exc.filename or where or ""),
                            where=where, hint="Kiểm tra quyền thư mục đích hoặc chọn nơi khác.")
        if op == "write" and isinstance(exc, IsADirectoryError):
            return PB3Error("E_IO", "đường đích là thư mục, không phải file",
                            where=where, hint="Chọn tên file đích, không phải thư mục.")
        return PB3Error("E_IO", "%s (errno=%s)" % (type(exc).__name__, no), where=where)
    return PB3Error("E_INTERNAL", type(exc).__name__, where=where)


def format_error(err: PB3Error, *, verbose: bool = False, log_path=None) -> str:
    """Thông báo tiếng Việt cho người dùng (không traceback).
    log_path: đường dẫn ĐẦY ĐỦ tới file log phiên (nếu có) -> thêm dòng
    'Nhật ký: ...' để người dùng tìm được chi tiết kỹ thuật (X2)."""
    lines = ["[%s] %s" % (err.code, err.message_vi)]
    if err.detail:
        lines.append("Chi tiết: " + err.detail)
    if err.where:
        lines.append("Tại: " + err.where)
    if err.plugin_id:
        lines.append("Plugin: " + err.plugin_id)
    if err.hint:
        lines.append("Gợi ý: " + err.hint)
    if log_path:
        lines.append("Nhật ký: " + str(log_path))
    if verbose:
        lines.append("Mã thoát: %d" % err.exit_code)
    return "\n".join(lines)
