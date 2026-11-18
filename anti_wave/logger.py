"""Log an toàn cho anti_wave (Anti-Wave, Phase 2).

Mọi dòng log qua logger này đều đi qua RedactingFilter: token/secret/key
bị thay bằng [REDACTED] trước khi ra stderr. Quy tắc:
  - KHÔNG BAO GIỜ log giá trị key/token/secret — chỉ log TÊN khi cần
    (vd tên key "cobonla" thì được, giá trị thì không).
  - Logger chỉ dùng StreamHandler (stderr) — không ghi file log (ghi file
    có thể chết trong sóng EPERM; file log nếu cần do Phase 5 lo).
  - get_logger() gọi nhiều lần không nhân đôi handler.

Chi stdlib, Python 3.9 tương thích.
"""
from __future__ import annotations

import logging
import re
from typing import Any

REDACTED = "[REDACTED]"

# Token dạng GitHub PAT / OAuth / classic: github_pat_..., ghp_..., gho_...,
# ghu_..., ghs_..., ghr_..., github_token_...
_TOKEN_PAT = re.compile(r"(?:github_pat|gh[pousr]|github_token)_[A-Za-z0-9_]+")
# Token dạng sk-... (OpenAI/Cloudflare-style).
_SK_PAT = re.compile(r"sk-[A-Za-z0-9_\-]{8,}")
# "Bearer <token>" — giữ chữ Bearer, che phần token.
_BEARER_PAT = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-~+/=]+")
# key=value: api_key / token / secret / password / authorization — giữ tên
# key, che giá trị (giá trị dừng ở khoảng trắng, quote, phẩy, ; hoặc }).
_KV_PAT = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|passwd|pwd|authorization)"
    r"(\s*[:=]\s*)(['\"]?)([^\s'\",;}]+)"
)


def _kv_repl(m: "re.Match[str]") -> str:
    return "%s%s%s%s" % (m.group(1), m.group(2), m.group(3), REDACTED)


def redact(text: Any) -> str:
    """Che mọi token/secret trong text; text không phải str thì str() trước.

    Idempotent: chạy lại trên text đã redact không đổi gì thêm
    ("[REDACTED]" không khớp pattern nào).
    """
    s = str(text)
    s = _TOKEN_PAT.sub(REDACTED, s)
    s = _SK_PAT.sub(REDACTED, s)
    s = _BEARER_PAT.sub(lambda m: m.group(1) + REDACTED, s)
    s = _KV_PAT.sub(_kv_repl, s)
    return s


class RedactingFilter(logging.Filter):
    """Filter logging: redact msg + args TRƯỚC KHI record được emit.

    Cách làm: format record trước (record.getMessage() gộp msg + args),
    redact chuỗi đã format, rồi thay msg bằng chuỗi đó và xóa args.
    LÀM NHƯ VẬY (không redact msg thô): redact msg thô có thể phá placeholder
    "%s" trong msg, khiến logging format lỗi (TypeError) và mất dòng log.
    Không bao giờ raise (lỗi redact -> để nguyên record, vẫn trả True).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact(record.getMessage())
            record.args = ()
        except Exception:
            pass
        return True


def get_logger(name: str, *, level: int = logging.INFO) -> logging.Logger:
    """Logger anti_wave: 1 StreamHandler (stderr) + RedactingFilter.

    Gọi nhiều lần với cùng tên không nhân đôi handler. Không propagate
    lên root (tránh log trùng qua handler của app khác).
    """
    lg = logging.getLogger(name)
    lg.setLevel(level)
    lg.propagate = False
    if not any(isinstance(h, logging.StreamHandler) for h in lg.handlers):
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s"))
        h.addFilter(RedactingFilter())
        lg.addHandler(h)
    return lg


def log_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    """Log 1 sự kiện dạng `event k=v k=v` — mọi giá trị đều redact trước.

    Vd: log_event(lg, "wave_on", branch="feat/x", probe="EPERM")
    -> "wave_on branch=feat/x probe=EPERM".
    """
    parts = ["%s=%s" % (k, redact(v)) for k, v in fields.items()]
    logger.info("%s %s", event, " ".join(parts))
