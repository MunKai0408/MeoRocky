"""Chạy test có tiến độ sống: [n/tổng] %, thời gian, và TEST ĐANG CHẠY. Chia tầng nhanh/chậm.

  python3 run_tests.py --tier smoke             vài giây: ~60 test then chốt (chạy sau mỗi lần sửa)
  python3 run_tests.py                          HẰNG NGÀY: chỉ bộ NHANH (bỏ ish + slow + meta)
  python3 run_tests.py --tier ish               test chỉ có nghĩa trên máy thật (symlink, /proc, ...): bằng chứng iSH
  python3 run_tests.py --tier fast,ish          ghép nhiều tầng bằng dấu phẩy
  python3 run_tests.py --changed                chỉ test bị ảnh hưởng bởi file đã đổi từ lần FULL xanh gần nhất
  python3 run_tests.py tests/test_plugin_toggle.py   MỘT GÓI: chạy ĐỦ file test của gói (cả test chậm)
  python3 run_tests.py --tier full              BÀN GIAO: chạy TẤT CẢ (xanh thì lập mốc cho --changed)
  python3 run_tests.py -k toggle                lọc theo chuỗi trong tên test
  python3 run_tests.py --plan                   chỉ in kế hoạch (bao nhiêu test mỗi tầng), không chạy
  tùy chọn: -l /root/t.log  ghi dòng tổng kết vào file | --budget 1.0  ngưỡng giây của test nhanh

Mỗi lần chạy ghi thời gian từng test ĐÃ ĐẠT vào reports/test_times.json (số đo THẬT trên máy này).
Test nhanh nào vượt ngân sách 2 lần liên tiếp mới chuyển sang tầng chậm (hysteresis, D-POLISH-3);
test chậm chạy dưới ngân sách 2 lần FULL liên tiếp thì hồi phục về tầng nhanh.
Mỗi test chạy trong luồng riêng, quá 180s (x5 trên iSH) thì tính ERROR và bỏ lại
(D-POLISH-2: Python 3.9 không kill được thread không hợp tác trong tiến trình).
Khi có timeout thì không lập mốc. PB3_FAST=1 cũng không lập mốc.
Nếu một test treo > 15s (x hệ số máy trên iSH), in cảnh báo kèm tên test đó;
treo ở setUpClass/setUpModule thì nêu rõ pha đang treo.
Cuối suite in thêm bảng tóm tắt (sau dòng Ran, không thay thế dòng đó)."""
import os, shutil, signal, sys, tempfile, time, threading, unittest

if __name__ == "__main__":
    # Ctrl+C trong lúc còn nạp/import (trên iSH import typing rất chậm):
    # KeyboardInterrupt không bắt ở giai đoạn này làm tiến trình chết bởi tín
    # hiệu (-2) thay vì thoát 130 — đã kiểm chứng bằng thực nghiệm (SIGINT
    # giữa import -> traceback KeyboardInterrupt + returncode -2; handler ép
    # SystemExit(130) -> 130). main() trả lại handler mặc định khi vào vùng
    # chạy test để except KeyboardInterrupt còn lưu được mốc một phần.
    def _early_sigint(signum, frame):
        raise SystemExit(130)
    signal.signal(signal.SIGINT, _early_sigint)

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

from core.color import is_tty, kv_table, paint, status, title, truncate_text  # noqa: E402
from core.progress import fmt_duration  # noqa: E402
from tests import tiering as T  # noqa: E402


def _emit(stream, text):
    """Ghi ra stream, nuốt lỗi ghi (đĩa đầy/pipe vỡ): bộ chạy không được
    chết giữa chừng chỉ vì không in được tiến độ — kết quả vẫn tính đúng,
    mã thoát vẫn đúng, failures.txt (ghi sau, ở đĩa khác) vẫn cứu được
    danh sách lỗi. Bài học 2026-11-16: hai lần full chạy song song làm đầy
    /tmp 512MB, lần chạy viết log ra /tmp chết giữa chừng vì write() lỗi
    ENOSPC không được bắt."""
    try:
        stream.write(text)
        stream.flush()
    except (OSError, ValueError):
        pass


def _print(*args, **kwargs):
    """print() không chết vì lỗi ghi (như _emit)."""
    try:
        print(*args, **kwargs)
    except (OSError, ValueError):
        pass


def _flatten(s):
    for x in s:
        if isinstance(x, unittest.TestSuite):
            for y in _flatten(x):
                yield y
        else:
            yield x


class R(unittest.TextTestResult):
    """Kết quả chạy test: thanh tiến độ + trạng thái từng test (TTY mới có màu
    và ký tự ✓/✗/↷; pipe/log thì thuần, dòng ngắn, không mã escape)."""
    def __init__(self, stream, descriptions, verbosity):
        super().__init__(stream, descriptions, verbosity)
        self.n = 0
        self.total = 0
        self.cur = ""
        self.t0 = time.time()
        self.timeouts = 0
        self.last_kind = None   # pass | fail | skip
        self.last_name = ""
        self.eta_sums = []      # suffix sum thời gian profile còn lại (giây)
        self._tty = is_tty()
        self._last_pct = -1     # % đã in gần nhất (pipe: chỉ in khi % đổi — bước từng 1%, luật #152)
        self._phase = ""        # pha đang chạy: "" = thân test, "setUpClass"/"setUpModule" = fixture lớp/module
        self._phase_test = ""
        self._phase_t0 = 0.0

    def _short_name(self, test):
        """Tên test gọn ~40 ký tự: giữ tên test, cắt tên class (R5)."""
        tid = test.id()
        parts = tid.split(".")
        if len(parts) >= 2:
            name = "%s (%s)" % (parts[-1], parts[-2])
        else:
            name = tid
        return truncate_text(name, 40)

    def _progress_line(self, symbol=None):
        pct = self.n * 100 // self.total if self.total else 0
        line = "[%d/%d] %s" % (self.n, self.total, paint("%3d%%" % pct, "pass"))
        if self.n and self.n <= len(self.eta_sums):
            eta = self.eta_sums[self.n - 1]
            if eta > 0:
                line += " ETA ~%s" % fmt_duration(eta)
        name = self._short_name_text()
        if symbol:
            line += " " + symbol + " " + name
        else:
            line += " " + name
        prof = getattr(self, "_cur_profile", 0.0)
        if prof > T.budget_s():
            line += " " + paint("SLOW", "warn")
        return line

    def _short_name_text(self):
        return getattr(self, "_cur_short", "")

    def startTest(self, test):
        self.cur, self.t0 = str(test), time.time()
        self.n += 1
        self._phase = ""   # từ đây là thân test (fixture lớp/module đã xong)
        self._cur_short = self._short_name(test)
        p = T.split_id(test.id())
        self._cur_profile = T.profile_seconds(T.make_id(*p)) if p else 0.0
        if self._tty:
            line = self._progress_line()
            _emit(self.stream, "\r\033[K" + line)
        elif self.total > 0:
            # Pipe/log: in một dòng mỗi khi % nguyên đổi (tối đa ~100 dòng),
            # không nhảy cục (trước đây in mỗi 50 test, % nhảy ~4% giữa 2 dòng).
            pct = self.n * 100 // self.total
            if pct != self._last_pct:
                self._last_pct = pct
                _emit(self.stream, self._progress_line() + "\n")
        unittest.TestResult.startTest(self, test)

    def _finish_line(self, kind):
        self.last_kind = kind
        if not self._tty:
            return
        sym = {"pass": status("pass", "✓"), "fail": status("fail", "✗"),
               "skip": status("skip", "↷")}[kind]
        _emit(self.stream, "\r\033[K" + self._progress_line(sym))

    def addSuccess(self, t):
        unittest.TestResult.addSuccess(self, t)
        self._finish_line("pass")

    def addSkip(self, t, r):
        unittest.TestResult.addSkip(self, t, r)
        self.skipN = getattr(self, "skipN", 0) + 1
        self._finish_line("skip")

    def addError(self, t, e):
        unittest.TestResult.addError(self, t, e)
        if e and e[0] is TimeoutError:
            self.timeouts += 1
        _emit(self.stream, "\n  LỖI: %s\n" % t)
        self._finish_line("fail")   # R10: vẽ lại dòng tiến độ sau dòng lỗi

    def addFailure(self, t, e):
        unittest.TestResult.addFailure(self, t, e)
        _emit(self.stream, "\n  FAIL: %s\n" % t)
        self._finish_line("fail")   # R10: vẽ lại dòng tiến độ sau dòng fail

    def stopTest(self, t):
        self.times = getattr(self, "times", {})
        self.times[t.id()] = time.time() - self.t0
        unittest.TestResult.stopTest(self, t)


class _Collector(unittest.TestResult):
    """Collector riêng cho mỗi test: giữ cả exc_info THÔ (Python 3.12 lưu trong
    .errors/.failures chỉ còn chuỗi đã định dạng — không gộp lại được qua
    addError, nên giữ bản thô ở đây để gộp vào kết quả chính)."""
    def __init__(self):
        super().__init__()
        self.raw_errors = []
        self.raw_failures = []

    def addError(self, test, err):
        self.raw_errors.append((test, err))
        super().addError(test, err)

    def addFailure(self, test, err):
        self.raw_failures.append((test, err))
        super().addFailure(test, err)


class ThreadedSuite(unittest.TestSuite):
    """Mỗi test lá chạy trong daemon thread riêng, join(timeout); quá hạn ->
    ERROR trong kết quả chính rồi chạy tiếp (D-POLISH-2). Luồng bị bỏ lại chỉ
    ghi vào collector riêng (bị hủy), không chạm vào kết quả chính — nên số
    liệu không bao giờ bị ghi đè. Python 3.9 không kill được thread không
    hợp tác trong tiến trình: không giả vờ kill, chỉ bỏ lại."""
    timeout = 180.0

    def _timeout_for(self, test):
        """Timeout cho 1 test (giây, đã nhân hệ số máy): `timeout_s`
        trên phương thức test, rồi trên lớp test, mặc định là timeout
        chung của suite. Test dài (vd round-trip PB3 ~16 phút) đặt
        riêng thay vì nâng trần chung — trần chung vẫn bắt test treo."""
        method = getattr(test, getattr(test, "_testMethodName", ""), None)
        base = getattr(method, "timeout_s", None)
        if base is None:
            base = getattr(test.__class__, "timeout_s", None)
        if base is None:
            return self.timeout
        try:
            base = float(base)
        except (TypeError, ValueError):
            return self.timeout
        if base <= 0:
            return self.timeout
        return base * T.timeout_scale()

    def run(self, result):
        for test in self:
            if result.shouldStop:
                break
            if isinstance(test, unittest.TestSuite):
                test.run(result)
                continue
            # Giống unittest.TestSuite.run: dọn class trước, fixture module,
            # rồi setUpClass (chạy trên main thread, như chuẩn) trước khi test
            # chạy trong luồng riêng. Thiếu bước này -> _shared_proj=None...
            # Ghi pha đang chạy để watchdog (luồng watch trong main) nêu rõ
            # tên khi fixture treo — fixture chạy trên main thread nên timeout
            # của test không áp dụng được; treo ở đây chỉ báo rõ + Ctrl+C
            # vẫn thoát 130 (không giả vờ kill được, như D-POLISH-2).
            _prev = getattr(result, "_previousTestClass", None)
            if _prev is not None and _prev is not test.__class__:
                result._phase = "tearDownClass"
                result._phase_test = getattr(_prev, "__name__", str(_prev))
                result._phase_t0 = time.time()
            self._tearDownPreviousClass(test, result)
            result._phase = "setUpModule"
            result._phase_test = str(test)
            result._phase_t0 = time.time()
            self._handleModuleFixture(test, result)
            result._phase = "setUpClass"
            result._phase_test = str(test)
            result._phase_t0 = time.time()
            self._handleClassSetUp(test, result)
            result.startTest(test)
            collector = _Collector()
            th = threading.Thread(target=test.run, args=(collector,), daemon=True)
            th.start()
            timeout = self._timeout_for(test)
            th.join(timeout)
            if th.is_alive():
                result.addError(test, (TimeoutError, TimeoutError(
                    "test vượt %.0fs, bỏ qua (thread bị bỏ lại, không kill được)" % timeout), None))
                result.timeouts = getattr(result, "timeouts", 0) + 1
            else:
                for t2, err in collector.raw_errors:
                    result.addError(t2, err)
                for t2, err in collector.raw_failures:
                    result.addFailure(t2, err)
                for t2, reason in collector.skipped:
                    result.addSkip(t2, reason)
                if not collector.errors and not collector.failures and not collector.skipped:
                    result.addSuccess(test)
            result.stopTest(test)
        self._tearDownPreviousClass(None, result)
        return result


def _arg(argv, name, default=None):
    return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else default


def _say(msg):
    """Tiến độ nạp/xếp tầng: chỉ hiện khi TTY; khi pipe thì im lặng
    (tránh spam hàng chục dòng \r\033[K vào log)."""
    if is_tty():
        _emit(sys.stdout, "\r\033[K" + msg)


def _mod_name(f):
    """Đường dẫn file test (tương đối hoặc TUYỆT ĐỐI) -> tên module.
    Đường tuyệt đối được resolve() rồi relative_to(ROOT) (F5)."""
    if not f.endswith(".py"):
        return f
    try:
        rel = os.path.relpath(os.path.abspath(f), ROOT)
    except ValueError:
        rel = f
    return rel.replace("\\", "/").strip("/").replace("/", ".")[:-3]


def _count_loaded(tests):
    """({tầng: số}, smoke) của các test đã nạp — dùng để đối chiếu với kế
    hoạch nhẹ (chốt chặn nạp chọn lọc)."""
    cnt = dict((n, 0) for n in T.PARTITIONS)
    smoke_n = 0
    for t in tests:
        cnt[T.classify_id(t.id())] += 1
        if T.is_smoke_id(t.id()):
            smoke_n += 1
    return cnt, smoke_n


def _build(argv):
    """-> (danh sách test đã chọn, mô tả kế hoạch, có lọc không). In tiến độ nạp/xếp tầng (trên iSH mất vài chục giây).
    Nạp chọn lọc: khi không chỉ định file và không dùng --changed, chỉ nạp
    module chứa test thuộc tầng đang chạy (tính bằng _Facts, không import —
    smoke chỉ nạp 9/59 module). Đếm lại sau khi nạp: lệch khỏi kế hoạch nhẹ
    (test động, lỗi import...) thì nạp đủ rồi mới chọn — không mất test âm thầm."""
    files = [a for a in argv if a.endswith(".py") or a.startswith("tests.")]
    loader = unittest.defaultTestLoader
    tier = _arg(argv, "--tier", "full" if files else "fast")
    want = T.parse_tiers(tier)
    selective = not files and "--changed" not in argv
    plan_total = plan_cnt = plan_smoke = None
    if files:
        mods = [_mod_name(f) for f in files]
    elif selective:
        plan_total, plan_cnt, plan_smoke, cand = T.plan_modules(want)
        mods = sorted(cand)
    else:
        mods = ["tests." + f[:-3] for f in sorted(os.listdir("tests")) if f.startswith("test") and f.endswith(".py")]
    suite = unittest.TestSuite()
    for i, mod in enumerate(mods, 1):
        _say("Đang nạp test [%d/%d] %s" % (i, len(mods), mod.split(".")[-1]))
        suite.addTests(loader.loadTestsFromName(mod))
    tests = list(_flatten(suite))
    if selective:
        _cnt, _smoke = _count_loaded(tests)
        if len(tests) != plan_total or _cnt != plan_cnt or _smoke != plan_smoke:
            # Chốt chặn nạp chọn lọc: lệch -> nạp đủ, đếm lại bằng số thật.
            suite = unittest.TestSuite()
            all_mods = ["tests." + f[:-3] for f in sorted(os.listdir("tests"))
                        if f.startswith("test") and f.endswith(".py")]
            for i, mod in enumerate(all_mods, 1):
                _say("Đang nạp đủ test [%d/%d] %s" % (i, len(all_mods), mod.split(".")[-1]))
                suite.addTests(loader.loadTestsFromName(mod))
            tests = list(_flatten(suite))
    _say("Đang xếp tầng %d test..." % len(tests))
    filtered = bool(files) or not T.is_full(want)
    cnt = dict((n, 0) for n in T.PARTITIONS)
    tiers, smoke = {}, {}
    for t in tests:
        k = T.classify_id(t.id())
        tiers[id(t)] = k
        smoke[id(t)] = T.is_smoke_id(t.id())
        cnt[k] += 1
    sel = [t for t in tests if T.is_selected(want, tiers[id(t)], smoke[id(t)])]
    note = []
    if "--changed" in argv:
        changed, has_base = T.changed_since_baseline()
        filtered = True
        if not has_base:
            note.append("CHƯA có mốc (chưa có lần FULL xanh nào) -> không lọc theo thay đổi; chạy --tier full một lần để lập mốc")
        else:
            deleted = [c for c in changed if not os.path.exists(os.path.join(ROOT, c))]
            if deleted:
                # File đã xóa: không biết test nào import nó -> không lọc, chạy đủ tầng đang chọn (R7).
                note.append("có %d file đã xóa (%s) -> không lọc theo thay đổi, chạy đủ %d test tầng đang chọn"
                            % (len(deleted), ", ".join(deleted[:3]), len(sel)))
            else:
                affected = [t for t in tests if _affected(t, tiers, changed)]
                keep = [t for t in sel if _affected(t, tiers, changed)]
                excluded = len(affected) - len(keep)
                if excluded:
                    note.append("còn %d test bị ảnh hưởng nằm ngoài tầng đang chạy (slow/meta) — chạy --tier full để kiểm đủ" % excluded)
                note.append("%d file đã đổi từ mốc FULL xanh -> chọn %d/%d test bị ảnh hưởng"
                            % (len(changed), len(keep), len(sel)))
                sel = keep
    k = _arg(argv, "-k")
    if k:
        sel = [t for t in sel if k.lower() in t.id().lower()]
        filtered = True
        note.append("lọc -k %r -> %d test" % (k, len(sel)))
    _say("")
    plan = "TẦNG %s | tổng %d test: %s, smoke %d | chạy %d" % (
        tier.upper(), len(tests), ", ".join("%s %d" % (n, cnt[n]) for n in T.PARTITIONS),
        sum(smoke.values()), len(sel))
    all_idents = set()
    for t in tests:
        p = T.split_id(t.id())
        if p:
            all_idents.add(T.make_id(*p))
    T.flush_facts_cache()
    return sel, plan, note, filtered, tiers, want, all_idents


def _affected(t, tiers, changed):
    p = T.split_id(t.id())
    return p is None or T.is_affected(p[0], tiers[id(t)], changed)


def _plan_light(argv):
    """--plan nhẹ (R8): đếm bằng _Facts, không nạp test. Trả (plan, notes)."""
    tier = _arg(argv, "--tier", "fast")
    want = T.parse_tiers(tier)
    total, cnt, smoke_n = T.plan_counts()
    run_n = sum(cnt[n] for n in want if n in T.PARTITIONS)
    if "smoke" in want and "fast" not in want:
        run_n += smoke_n
    plan = "TẦNG %s | tổng %d test: %s, smoke %d | chạy %d" % (
        tier.upper(), total, ", ".join("%s %d" % (n, cnt[n]) for n in T.PARTITIONS),
        smoke_n, run_n)
    return plan, ["kế hoạch nhẹ (không nạp test)"]


_STALE_TMP_PREFIXES = ("pb3test-", "pb3-runner-", "pb3-realcli-")
_STALE_TMP_MAX_AGE_S = 3600.0   # thư mục tạm của lần chạy trước còn sót sau 1 giờ coi như bỏ


def _sweep_stale_tmp_dirs(root=None, max_age_s=_STALE_TMP_MAX_AGE_S):
    """Dọn thư mục tạm pb3test-* cũ còn sót trong thư mục tạm hệ thống.
    Lần chạy trước bị giết thì atexit không kịp dọn; 274 thư mục từng làm đầy
    /tmp 512MB trên iSH và gây lỗi giải nén "disk full?". Chỉ xóa thư mục quá
    `max_age_s` giây để không đụng lần chạy đang chạy song song. Trả số thư
    mục đã xóa; không bao giờ làm hỏng lần chạy (mọi lỗi đều nuốt)."""
    try:
        base = root or tempfile.gettempdir()
        names = os.listdir(base)
    except Exception:
        return 0
    now = time.time()
    gone = 0
    for name in names:
        if not name.startswith(_STALE_TMP_PREFIXES):
            continue
        p = os.path.join(base, name)
        try:
            if not os.path.isdir(p):
                continue
            if now - os.path.getmtime(p) < max_age_s:
                continue
            shutil.rmtree(p, ignore_errors=True)
            gone += 1
        except OSError:
            continue
    return gone


def _save_failures(res, summary):
    """Lưu danh sách lỗi ra reports/failures.txt mỗi lần chạy (để trên iSH
    chỉ cần cat file là thấy, không mất khi cuộn màn hình). Mỗi lỗi: tên test
    + dòng ngoại lệ cuối (dòng lỗi quan trọng nhất). Khi xanh thì file vẫn
    ghi dòng tổng kết + 'không có test lỗi' để lần sau khỏi nhầm file cũ."""
    lines = [summary]
    for kind, items in (("ERROR", res.errors), ("FAIL", res.failures)):
        for t, tb in items:
            tid = t.id() if hasattr(t, "id") else str(t)
            lines.append("%s: %s" % (kind, tid))
            last = ""
            if isinstance(tb, str):
                for ln in reversed(tb.splitlines()):
                    if ln.strip():
                        last = ln.strip()
                        break
            elif tb is not None:
                last = str(tb).strip().splitlines()[-1].strip() if str(tb).strip() else ""
            if last:
                lines.append("  " + last)
    if not res.errors and not res.failures:
        lines.append("OK — không có test lỗi.")
    try:
        os.makedirs(os.path.join(ROOT, "reports"), exist_ok=True)
        with open(os.path.join(ROOT, "reports", "failures.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        pass


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    _sweep_stale_tmp_dirs()
    log = _arg(argv, "-l")
    if _arg(argv, "--budget"):
        os.environ["PB3_TEST_BUDGET_S"] = _arg(argv, "--budget")
    T.reset_cache()
    if "--plan" in argv and not [a for a in argv if a.endswith(".py") or a.startswith("tests.")]:
        # --plan toàn bộ: đếm nhẹ, không nạp (R8); --plan kèm file thì vẫn nạp để đếm đúng file đó.
        try:
            plan, notes = _plan_light(argv)
        except ValueError as e:
            _print("Lỗi tham số: %s" % e)
            return 2
        _print(plan)
        for n in notes:
            _print("  - " + n)
        return 0
    _say("Khởi động bộ chạy test...")
    try:
        sel, plan, notes, filtered, tiers, want, all_idents = _build(argv)
    except ValueError as e:
        _print("Lỗi tham số: %s" % e)
        return 2
    # Từ đây Ctrl+C đi qua except KeyboardInterrupt bên dưới (lưu mốc một
    # phần + thoát 130): trả lại handler mặc định — handler sớm ở đầu file
    # chỉ để ép thoát 130 trong lúc nạp/import.
    try:
        signal.signal(signal.SIGINT, signal.default_int_handler)
    except ValueError:
        pass   # main() gọi từ luồng khác (test gọi trực tiếp): giữ nguyên
    total = 0
    res = R(sys.stdout, False, 0)
    done = threading.Event()
    try:
        _print(plan, flush=True)   # flush: test Ctrl+C chờ dòng này làm dấu hiệu con đã sẵn sàng
        for n in notes:
            _print("  - " + n)
        if "--plan" in argv:
            return 0
        if not sel:
            _print("Không có test nào cần chạy.")
            return 0
        suite = ThreadedSuite(sel)
        suite.timeout = T.runner_timeout_s()
        total = len(sel)
        res.total = total
        res.cur = ""
        res.t0 = time.time()
        # ETA: suffix sum thời gian profile của các test còn lại (R5).
        prof = T.load_profile()
        secs = []
        for t in sel:
            p = T.split_id(t.id())
            secs.append(T.profile_seconds(T.make_id(*p), prof) if p else 0.0)
        suffix = [0.0] * (total + 1)
        for i in range(total - 1, -1, -1):
            suffix[i] = suffix[i + 1] + secs[i]
        res.eta_sums = suffix[1:]
        start = time.time()

        def watch():
            # Ngưỡng cảnh báo theo hệ số máy (x5 trên iSH): test khỏe trên
            # iSH vẫn chạy > 15s nên ngưỡng cứng 15s sẽ spam cảnh báo oan.
            warn_after = 15.0 * T.timeout_scale()
            while not done.wait(5):
                phase = getattr(res, "_phase", "")
                if phase:
                    run = time.time() - getattr(res, "_phase_t0", res.t0)
                    if run > warn_after:
                        _emit(sys.stdout, "\n  ... %s cho %s đã %ds (đang treo?)\n"
                              % (phase, getattr(res, "_phase_test", ""), run))
                    continue
                run = time.time() - res.t0
                if run > warn_after:
                    _emit(sys.stdout, "\n  ... test này đã chạy %ds (đang treo?): %s\n" % (run, res.cur))
        threading.Thread(target=watch, daemon=True).start()
        suite.run(res)
    except KeyboardInterrupt:
        # R6: lưu profile một phần (chỉ test đã đạt), rồi báo số đã chạy/còn lại.
        done.set()
        _save_partial_profile(res, tiers)
        _save_failures(res, "Đã hủy ở test: %s — đã chạy %d/%d test." % (res.cur, res.testsRun, total))
        _print("\nĐã hủy ở test: %s" % res.cur)
        _print("Đã chạy %d/%d test, còn lại %d test." % (res.testsRun, total, max(0, total - res.testsRun)))
        return 130
    done.set()
    el = time.time() - start
    ok = not res.errors and not res.failures
    line = "Ran %d tests in %.1fs  %s (skipped=%d, lỗi=%d, fail=%d)" % (
        res.testsRun, el, "OK" if ok else "FAILED", len(res.skipped), len(res.errors), len(res.failures))
    if is_tty():
        _print("\r\033[K" + paint("[%d/%d] 100%%" % (total, total), "pass"))
    else:
        _print("[%d/%d] 100%%" % (total, total))
    _print(line)
    for t, tb in res.errors + res.failures:
        _print("-" * 60)
        _print(t)
        _print(tb)
    _save_failures(res, line)

    times = getattr(res, "times", {})
    failed_ids = {t.id() for t, _ in res.errors + res.failures}
    skipped_ids = {t.id() for t, _ in res.skipped}
    upd, over = {}, []
    for tid, sec in times.items():
        p = T.split_id(tid)
        if p and tid not in failed_ids and tid not in skipped_ids:
            upd[T.make_id(*p)] = round(sec, 3)   # R3: chỉ test ĐẠT mới được ghi
    for t in sel:                                   # test nhanh vượt ngân sách
        p = T.split_id(t.id())
        sec = times.get(t.id(), 0.0)
        if p and tiers.get(id(t)) == T.FAST and sec > T.budget_s():
            over.append((sec, T.make_id(*p)))
    promoted, recovered = [], []
    try:
        if upd:
            tiers_by_ident = {}
            for t in sel:
                p = T.split_id(t.id())
                if p:
                    tiers_by_ident[T.make_id(*p)] = tiers[id(t)]
            promoted, recovered = T.record_times(upd, tiers_by_ident, not filtered, all_idents)
    except OSError as e:
        _print("(không ghi được reports/test_times.json: %s)" % e)
    slowest = sorted(((s, i) for i, s in upd.items()), reverse=True)[:5]
    # Bảng tóm tắt cuối suite (BỔ SUNG sau dòng Ran; dòng Ran giữ nguyên y hệt).
    rows = [
        ("Tầng", "full" if T.is_full(want) else ", ".join(sorted(want))),
        ("Tổng", "%d" % res.testsRun),
        ("Đạt", "%d" % (res.testsRun - len(res.skipped) - len(res.errors) - len(res.failures))),
        ("Lỗi", "%d" % len(res.errors)),
        ("Fail", "%d" % len(res.failures)),
        ("Bỏ qua", "%d" % len(res.skipped)),
        ("Thời gian", "%.1fs" % el),
        ("Timeout", "%d" % res.timeouts),
    ]
    if slowest:
        rows.append(("Chậm nhất", "%.1fs %s" % (slowest[0][0], slowest[0][1].split("::", 1)[1])))
    _print(title("Tóm tắt"))
    for ln in kv_table(rows, width=40):
        _print(ln)
    if promoted:
        _print("MỚI CHẬM (%d): %s" % (len(promoted), ", ".join(i.split("::", 1)[1] for i in promoted[:5])))
    if recovered:
        _print("ĐÃ HỒI PHỤC (%d): %s" % (len(recovered), ", ".join(i.split("::", 1)[1] for i in recovered[:5])))
    if over:
        _print("VƯỢT NGÂN SÁCH %.1fs (tự chuyển sang tầng chậm từ lần sau): %d test" % (T.budget_s(), len(over)))
        for s, i in sorted(over, reverse=True)[:10]:
            _print("   %.1fs %s" % (s, i))

    if ok and not filtered and res.timeouts == 0 and os.environ.get("PB3_FAST") != "1":
        try:
            T.save_baseline()
            _print("Đã lập mốc FULL xanh (dùng cho --changed).")
        except OSError as e:
            _print("(không ghi được mốc: %s)" % e)
    elif res.timeouts:
        _print("LƯU Ý: có %d test timeout — không lập mốc." % res.timeouts)
    elif ok and os.environ.get("PB3_FAST") == "1":
        _print("LƯU Ý: PB3_FAST=1 — không lập mốc.")
    elif ok:
        _print("LƯU Ý: đây KHÔNG phải bộ đầy đủ - chưa đủ điều kiện bàn giao. Bàn giao: python3 run_tests.py --tier full")
    if log:
        with open(log, "w") as f:
            f.write(line + "\n")
    return 0 if ok else 1


def _save_partial_profile(res, tiers):
    """Lưu thời gian các test đã ĐẠT trước khi Ctrl+C (R6); không tỉa, không demote."""
    times = getattr(res, "times", {})
    failed_ids = {t.id() for t, _ in res.errors + res.failures}
    skipped_ids = {t.id() for t, _ in res.skipped}
    upd = {}
    for tid, sec in times.items():
        p = T.split_id(tid)
        if p and tid not in failed_ids and tid not in skipped_ids:
            upd[T.make_id(*p)] = round(sec, 3)
    if not upd:
        return
    try:
        T.record_times(upd, {}, False, None)
    except OSError:
        pass


if __name__ == "__main__":
    sys.exit(main())
