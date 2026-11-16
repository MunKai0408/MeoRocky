"""Dịch vụ dịch tự động (sau Claude 07): lệnh `translate` + dùng chung cho
`extract --translate` và menu [2].

- `translate <json> [--to vi] [--from auto] [--provider google|offline|fake]
  [--out FILE] [--no-cache] [--no-credit]`: dịch bảng JSON key-value đã trích xuất, ghi
  file `<stem>.<to>.json` (mặc định cùng thư mục). Chỉ dịch VALUE, giữ KEY.
  Mặc định gắn credit 'Việt Hoá Bởi Bear' vào 1 entry tiêu đề/version
  (xem sdk.translate.apply_credit); --no-credit tắt.
- Provider google: Google Translate miễn phí, cần mạng (xem sdk.translate).
  Provider offline: từ điển cụm game, không cần mạng. Provider fake: chỉ
  dùng cho test (cần env PB3_TRANSLATE_FAKE=1), không gọi mạng.
- Lệnh này chỉ tạo file JSON đã dịch, không chạm vào game. Ghi ngược vào
  game vẫn qua `extract --apply <json-đã-dịch> [--allow-write]` (dry-run
  mặc định theo luật chung) — credit trong file JSON sẽ vào game cùng lúc
  ghi ngược, và chỉ ghi thật khi --allow-write.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional, Tuple

from core.errors import EXIT_OK, PB3Error
from core.color import paint, status
from core.progress import SteadyProgress, step_pcts
from sdk import translate as tr


class _ProgressAdapter:
    """progress_fn(done, total) -> thanh tiến độ, tăng dần từng 1% (luật #152).

    Có thanh Overall (menu/CLI đều có): bước qua từng mốc % trung gian bằng
    ov.update(frac=mốc, note="dịch i/N") + ov.render() để thanh quét dần,
    không nhảy cục; ghi chú hiện số lượng thật đã xong/tổng. Không có Overall
    thì dùng SteadyProgress riêng (cùng mẫu một dòng format_progress)."""

    def __init__(self, app, label: str = "dịch"):
        self._label = label
        self._ov = getattr(app, "overall", None) if app is not None else None
        self._steady = None
        self._shown_pct = 0

    def __call__(self, done: int, total: int) -> None:
        if total <= 0:
            return
        new_pct = int(min(1.0, max(0.0, done / total)) * 100)
        if self._ov is not None:
            for pct in step_pcts(self._shown_pct, new_pct):
                interp = int(round(pct / 100.0 * total))
                self._ov.update(frac=pct / 100.0,
                                note="%s %d/%d" % (self._label, interp, total))
                self._ov.render()
            self._shown_pct = new_pct
            return
        if self._steady is None:
            self._steady = SteadyProgress(self._label, total)
        self._steady.update(done, note=self._label)


def _cache_path(app) -> Optional[str]:
    paths = getattr(app, "paths", None)
    cache_dir = getattr(paths, "cache", None)
    if cache_dir is None:
        return None
    return str(Path(str(cache_dir)) / tr.CACHE_FILENAME)


def _resolve_provider(name: str):
    """Provider theo tên; fake chỉ dùng cho test (cần env PB3_TRANSLATE_FAKE)."""
    if name == "google":
        return tr.GoogleTranslateProvider()
    if name == "offline":
        return tr.OfflineGlossaryProvider()
    if name == "fake":
        if os.environ.get("PB3_TRANSLATE_FAKE"):
            return tr.FakeProvider()
        raise PB3Error("E_INPUT_NOT_FOUND",
                       "provider 'fake' chỉ dùng cho test (đặt PB3_TRANSLATE_FAKE=1)")
    raise PB3Error("E_INPUT_NOT_FOUND", "provider không có: %s (google|offline|fake)" % name)


def _fallback_for(name: str):
    """Từ điển offline dự phòng khi provider mạng lỗi (chỉ cho google)."""
    return tr.OfflineGlossaryProvider() if name == "google" else None


def _default_out(input_path: str, target_lang: str) -> str:
    stem, _ext = os.path.splitext(input_path)
    return "%s.%s.json" % (stem, target_lang)


def translate_exported_file(kv_path: str, *, to: str, source_lang: str = "auto",
                            provider_name: str = "google", app=None,
                            progress_fn: Optional[Callable[[int, int], None]] = None,
                            credit: bool = True
                            ) -> Tuple[str, tr.TranslationResult]:
    """Dịch một file JSON đã trích xuất; trả (đường dẫn file đã dịch, kết quả).

    Dùng chung cho lệnh `translate`, `extract --translate` và menu [2].
    progress_fn mặc định: adapter tăng từng 1% qua thanh Overall (luật #152).
    credit=True (mặc định): gắn 'Việt Hoá Bởi Bear' vào 1 entry tiêu đề/
    version trong file xuất; credit=False thì file xuất là bản dịch thuần."""
    provider = _resolve_provider(provider_name)
    cache = None
    if app is not None:
        path = _cache_path(app)
        if path is not None:
            cache = tr.TranslationCache(path)
    if progress_fn is None and app is not None:
        progress_fn = _ProgressAdapter(app)
    out_path = _default_out(kv_path, to)
    result = tr.translate_json_file(
        kv_path, out_path, source_lang=source_lang, target_lang=to,
        provider=provider, fallback=_fallback_for(provider_name),
        cache=cache, progress_fn=progress_fn, credit=credit)
    return out_path, result


def print_summary(result: tr.TranslationResult, out_path: str,
                  out: Callable[[str], None] = print) -> None:
    if result.network_error:
        out(paint("  lỗi mạng: %s — đã dùng từ điển offline cho phần còn lại"
                  % result.network_error, "warn"))
    out(status("pass", "đã dịch: %s" % out_path))
    out("  tổng %d entry: %d đã dịch, %d từ cache, %d từ điển, %d dịch một phần, "
        "%d chưa dịch, %d rỗng, %d không đổi"
        % (result.total, result.done, result.cached, result.glossary,
           result.partial, result.untranslated, result.empty, result.unchanged))
    if result.credit_key:
        out("  credit 'Việt Hoá Bởi Bear' đặt ở key: %s" % result.credit_key)
    elif result.credit_requested:
        out(paint("  không đặt được credit 'Việt Hoá Bởi Bear': không có chuỗi "
                  "tiêu đề/version/copyright", "warn"))
    for warn in result.warnings[:5]:
        out(paint("  cảnh báo: " + warn, "warn"))
    if result.untranslated_keys:
        shown = ", ".join(result.untranslated_keys[:5])
        more = len(result.untranslated_keys) - 5
        out(paint("  chưa dịch (%d): %s%s" % (len(result.untranslated_keys), shown,
                                              " ... và %d key khác" % more if more > 0 else ""),
                  "warn"))


def run_cli(app, ns) -> int:
    src_path = str(ns.json)
    target = str(getattr(ns, "to", "vi") or "vi")
    source_lang = str(getattr(ns, "from_lang", "auto") or "auto")
    provider_name = str(getattr(ns, "provider", "google") or "google")
    cache = None
    if not getattr(ns, "no_cache", False):
        path = str(getattr(ns, "cache", None) or _cache_path(app) or "")
        if path:
            cache = tr.TranslationCache(path)
    provider = _resolve_provider(provider_name)

    out_path = str(getattr(ns, "out", None) or _default_out(src_path, target))
    credit = not bool(getattr(ns, "no_credit", False))
    result = tr.translate_json_file(
        src_path, out_path, source_lang=source_lang, target_lang=target,
        provider=provider, fallback=_fallback_for(provider_name),
        cache=cache, progress_fn=_ProgressAdapter(app), credit=credit)
    print_summary(result, out_path)
    return EXIT_OK


def add_arguments(subparser) -> None:
    """Cờ riêng cho `translate` (gọi từ ui/cli.py)."""
    subparser.add_argument("--to", default="vi", metavar="LANG",
                           help="ngôn ngữ đích (mặc định vi)")
    subparser.add_argument("--from", dest="from_lang", default="auto", metavar="LANG",
                           help="ngôn ngữ nguồn (mặc định auto)")
    subparser.add_argument("--out", default=None, metavar="FILE",
                           help="file JSON đã dịch (mặc định <tên>.<đích>.json)")
    subparser.add_argument("--provider", default="google",
                           choices=["google", "offline", "fake"],
                           help="google: Google Translate (cần mạng); offline: từ điển cụm game; "
                                "fake: chỉ dùng cho test")
    subparser.add_argument("--no-cache", action="store_true", default=False,
                           help="không dùng cache câu đã dịch")
    subparser.add_argument("--no-credit", action="store_true", default=False,
                           help="không thêm dòng credit 'Việt Hoá Bởi Bear' "
                                "(mặc định gắn vào 1 entry tiêu đề/version)")
    subparser.add_argument("--cache", default=None, metavar="FILE",
                           help="file cache câu đã dịch (mặc định workspace/cache/translation_cache.json)")
