"""Phân tầng test: MỘT nguồn sự thật cho conftest.py (pytest) và run_tests.py (unittest).

Năm tầng (khai báo trong TIERS bên dưới; THÊM TẦNG MỚI = thêm 1 dòng vào TIERS, và 1 dòng vào RULES nếu muốn tự nhận diện):
  smoke  ~60 test then chốt, cờ chọn ra từ tầng fast (không phân hoạch). Chạy sau mỗi lần sửa, vài giây.
  fast   test thuần bộ nhớ/file nhỏ. Chạy hằng ngày.
  ish    test chỉ có nghĩa trên máy thật (symlink, /proc, /dev/location, os.link). Chạy riêng để lấy bằng chứng iSH.
  slow   mở tiến trình con, sao chép cả dự án, sleep/timeout thật. Chạy khi bàn giao.
  meta   quét mã nguồn toàn dự án (AST/regex trên mọi file). Chạy khi bàn giao.
Phân hoạch (mỗi test đúng MỘT tầng): fast/ish/slow/meta, ưu tiên ish > slow > meta > fast. smoke chỉ là cờ.

Test MỚI tự được xếp tầng (không cần người viết nhớ đánh dấu), theo thứ tự ưu tiên:
  1. tests/tiers.json (ghi đè tay: "fast" cứu nhầm, "slow"/"meta"/"ish" ép tay; "smoke" chọn test then chốt)
  2. decorator @slow / @meta / @ish / @smoke / @slow_subprocess (trên test hoặc lớp)
  3. dấu hiệu trong mã: gọi subprocess, copy_project, time.sleep, symlink, /proc ... (kể cả qua hàm phụ)
  4. số đo thật trên máy này (reports/test_times.json): test > ngân sách giây => slow

Chỉ dùng cú pháp Python 3.8 (iSH)."""
import hashlib
import json
import os
import platform
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
FAST, SLOW, META, ISH, SMOKE = "fast", "slow", "meta", "ish", "smoke"

# (tên, loại, chạy trong mặc định hằng ngày, mô tả). loại: "partition" = mỗi test đúng một tầng; "flag" = cờ chọn thêm.
TIERS = [
    (SMOKE, "flag", False, "test then chốt, vài giây"),
    (FAST, "partition", True, "thuần bộ nhớ/file nhỏ"),
    (ISH, "partition", False, "chỉ có nghĩa trên máy thật"),
    (SLOW, "partition", False, "tiến trình con / sao chép dự án / sleep thật"),
    (META, "partition", False, "quét mã nguồn toàn dự án"),
]
TIER_NAMES = [t[0] for t in TIERS]
PARTITIONS = [t[0] for t in TIERS if t[1] == "partition"]

OVERRIDES_FILE = TESTS / "tiers.json"
PROFILE_FILE = ROOT / "reports" / "test_times.json"
BASELINE_FILE = ROOT / "reports" / "test_baseline.json"

SLOW_RE = re.compile(
    r"subprocess\.(?:run|Popen|check_output|check_call|call)\(|\bPopen\(|\bcopy_project\(|\bcopytree\("
    r"|time\.sleep\(|os\.system\(|^\s*@slow\b|^\s*@slow_subprocess\b", re.M)
META_RE = re.compile(
    r"\bast_nodes\(|\bimports_of\(|\bread_src\(|\bast\.parse\(|\bast\.walk\(|\bALL_PY\b|\bACTIVE\b"
    r"|^\s*@meta\b", re.M)
ISH_RE = re.compile(
    r"\bcan_symlink\(|os\.symlink\(|\.symlink_to\(|os\.link\(|/proc/|/dev/location|^\s*@ish\b", re.M)
SMOKE_RE = re.compile(r"^\s*@smoke\b", re.M)
RULES = [(ISH, ISH_RE), (SLOW, SLOW_RE), (META, META_RE)]      # theo thứ tự ưu tiên
_SETUPS = ("setUp", "setUpClass", "setUpModule")


def slow(obj):
    """Đánh dấu tay: test/lớp chậm."""
    return obj


def meta(obj):
    """Đánh dấu tay: test/lớp quét mã nguồn."""
    return obj


def ish(obj):
    """Đánh dấu tay: test chỉ có nghĩa trên máy thật."""
    return obj


def smoke(obj):
    """Đánh dấu tay: test then chốt (phải thuộc tầng fast)."""
    return obj


def budget_s():
    """Ngân sách giây cho một test ở tầng fast: mặc định 1.0s, nhân theo
    hệ số máy (timeout_scale: x5 trên iSH). Trước đây ngân sách cố định
    1.0s nên trên iSH nhiều test khỏe vẫn bị đẩy sang tầng slow (bằng chứng
    01:52: 41 test vượt 1.0s, chậm nhất 16.7s) — chậm do môi trường (CPU
    chậm, spawn subprocess), không phải do code; nhân hệ số thì phân tầng
    đúng trên từng máy. PB3_TEST_BUDGET_S vẫn ghi đè được (giá trị gốc,
    chưa nhân)."""
    try:
        base = float(os.environ.get("PB3_TEST_BUDGET_S", "1.0"))
    except ValueError:
        base = 1.0
    return base * timeout_scale()


def timeout_scale():
    """Hệ số nhân timeout: PB3_TEST_TIMEOUT_SCALE, nếu không thì x5 trên iSH
    (/proc/ish) — cùng quy tắc với tests/helpers.subprocess_timeout."""
    raw = os.environ.get("PB3_TEST_TIMEOUT_SCALE")
    if raw:
        try:
            v = float(raw)
            if v > 0:
                return v
        except ValueError:
            pass
    return 5.0 if os.path.exists("/proc/ish") else 1.0


def runner_timeout_s():
    """Timeout một test trong run_tests.py (D-POLISH-2): mặc định 180s."""
    return 180.0 * timeout_scale()


# ---------------------------------------------------------------- đọc cấu trúc file test (không dùng ast/inspect: chậm trên iSH)
_DEF_RE = re.compile(r"^(\s*)(?:async\s+)?def\s+(\w+)")
_CLS_RE = re.compile(r"^class\s+(\w+)")
_FACTS = {}


def _split_blocks(text):
    """{(lớp, tên hàm): văn bản}. Dòng nằm trong chuỗi ba-nháy bị bỏ (mã plugin mẫu trong test không tính)."""
    body, cur, cls, pending, delim = {}, ("", "<mod>"), "", [], None
    for ln in text.splitlines():
        if delim:
            if ln.count(delim) % 2 == 1:
                delim = None
            continue
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        d3 = '"""' if ln.count('"""') % 2 == 1 else ("'''" if ln.count("'''") % 2 == 1 else None)
        ind = len(ln) - len(ln.lstrip())
        handled = False
        if ind == 0:
            m = _CLS_RE.match(ln)
            if m:
                cls = m.group(1)
                cur = (cls, "")
                body[cur] = list(pending) + [ln]
                pending, handled = [], True
            elif s.startswith("@"):
                pending.append(ln)
                handled = True
            else:
                m = _DEF_RE.match(ln)
                if m:
                    cls, cur = "", ("", m.group(2))
                    body[cur] = list(pending) + [ln]
                    pending, handled = [], True
                else:
                    cls, cur = "", ("", "<mod>")
        elif ind == 4 and cls:
            if s.startswith("@"):
                pending.append(ln)
                handled = True
            else:
                m = _DEF_RE.match(ln)
                if m:
                    cur = (cls, m.group(2))
                    body[cur] = list(pending) + [ln]
                    pending, handled = [], True
        if not handled:
            body.setdefault(cur, []).append(ln)
        if d3:
            delim = d3
    return {k: "\n".join(v) for k, v in body.items()}


class _Facts:
    """Dấu hiệu từng tầng của một file test, tính một lần. Hàm phụ có phạm vi theo lớp (module-level: cả file)."""

    def __init__(self, text):
        self.blocks = _split_blocks(text)
        self.marks = dict((t, set()) for t, _ in RULES)          # {tầng: {(lớp, tên hàm phụ)}}
        helpers = [k for k in self.blocks if k[1] and not k[1].startswith("test") and k[1] not in _SETUPS
                   and k[1] != "<mod>"]
        for tier, rx in RULES:
            names = self.marks[tier]
            changed = True
            while changed:          # hàm phụ gọi hàm phụ thuộc tầng đó cũng thuộc tầng đó
                changed = False
                for k in helpers:
                    if k not in names and (rx.search(self.blocks[k]) or self._calls(k[0], self.blocks[k], tier)):
                        names.add(k)
                        changed = True
        self.cls_tier = {}
        for (c, n), t in self.blocks.items():
            if not c:
                continue
            head = self.blocks.get((c, ""), "")
            for tier, rx in RULES:
                if rx.search(head) or (n in _SETUPS and (rx.search(t) or self._calls(c, t, tier))):
                    self.cls_tier.setdefault(c, set()).add(tier)

    def _calls(self, cls, text, tier):
        """text (thuộc lớp cls) có gọi hàm phụ thuộc tầng `tier` của chính lớp đó hoặc của module không."""
        for (c, n) in self.marks[tier]:
            if c in ("", cls) and re.search(r"\b%s\(" % re.escape(n), text):
                return True
        return False

    def tier(self, cls, name):
        t = self.blocks.get((cls, name), "")
        have = set(self.cls_tier.get(cls, ()))
        for tier, rx in RULES:
            if rx.search(t) or self._calls(cls, t, tier):
                have.add(tier)
        for tier, _ in RULES:
            if tier in have:
                return tier
        return FAST

    def smoke_marked(self, cls, name):
        return bool(SMOKE_RE.search(self.blocks.get((cls, name), "")) or SMOKE_RE.search(self.blocks.get((cls, ""), "")))


def _facts(rel):
    f = _FACTS.get(rel)
    if f is None:
        p = TESTS / rel
        try:
            raw = p.read_bytes()
        except OSError:
            raw = None
        if raw is None:
            f = _Facts("")
        else:
            digest = hashlib.sha1(raw).hexdigest()
            cached = _facts_cache_lookup(rel, digest)
            if cached is not None:
                f = _FactsView(cached)
            else:
                f = _Facts(raw.decode("utf-8"))
                _facts_cache_note(rel, digest, f)
        _FACTS[rel] = f
    return f


# ---------------------------------------------------------------- bộ nhớ đệm _Facts trên đĩa
# Đọc/phân tích 58 file test bằng regex tốn ~0,5s mỗi lần chạy trên sandbox
# (trên iSH CPU giả lập còn đắt hơn nhiều) mà nội dung file hiếm khi đổi.
# Cache `reports/test_facts_cache.json` lưu (tầng, cờ smoke) từng test, khóa
# bằng sha1 nội dung file + hash luật xếp tầng: file đổi hay luật đổi thì
# cache tự hết hiệu lực, không bao giờ cho kết quả cũ. Ghi theo kiểu
# "bẩn rồi xả một lần" (flush_facts_cache) để lần chạy lạnh chỉ ghi 1 file.
# Mọi lỗi đọc/ghi cache đều nuốt: hỏng cache thì phân tích lại, không vỡ chạy.
FACTS_CACHE_FILE = ROOT / "reports" / "test_facts_cache.json"
FACTS_CACHE_VERSION = 1   # TĂNG khi đổi _split_blocks/_Facts (logic phân tích)
_FACTS_CACHE = None       # nội dung file cache đã nạp (None = chưa nạp)
_FACTS_DIRTY = {}         # {rel: {"sha1":..., "tests": {cls::name: [tier, smoke]}}}


def _facts_rules_hash():
    h = hashlib.sha1()
    for rx in (SLOW_RE, META_RE, ISH_RE, SMOKE_RE):
        h.update(rx.pattern.encode("utf-8"))
        h.update(b"\0")
    h.update("|".join(t[0] for t in TIERS).encode("utf-8"))
    h.update(("v%d" % FACTS_CACHE_VERSION).encode("ascii"))
    return h.hexdigest()


def _load_facts_cache_file():
    try:
        d = json.loads(FACTS_CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or d.get("v") != FACTS_CACHE_VERSION:
        return None
    if d.get("rules") != _facts_rules_hash():
        return None
    files = d.get("files")
    if not isinstance(files, dict):
        return None
    return d


def _facts_cache_lookup(rel, digest):
    """{cls::name: (tier, smoke)} từ cache đĩa, hoặc None (hết hiệu lực/lỗi)."""
    global _FACTS_CACHE
    if _FACTS_CACHE is None:
        _FACTS_CACHE = _load_facts_cache_file() or {}
    entry = (_FACTS_CACHE.get("files") or {}).get(rel)
    if not isinstance(entry, dict) or entry.get("sha1") != digest:
        return None
    tests = entry.get("tests")
    if not isinstance(tests, dict):
        return None
    out = {}
    for key, val in tests.items():
        if not isinstance(key, str) or "::" not in key:
            return None
        if not isinstance(val, list) or len(val) != 2 or val[0] not in PARTITIONS or not isinstance(val[1], bool):
            return None
        out[key] = (val[0], val[1])
    return out


def _facts_cache_note(rel, digest, facts):
    """Ghi nhận kết quả phân tích tươi vào bộ nhớ đệm bẩn (chờ flush)."""
    entry = {}
    for (cls, name) in facts.blocks:
        if cls and name.startswith("test"):
            entry["%s::%s" % (cls, name)] = [facts.tier(cls, name), facts.smoke_marked(cls, name)]
    _FACTS_DIRTY[rel] = {"sha1": digest, "tests": entry}


def flush_facts_cache():
    """Xả các mục bẩn vào file cache (ghi nguyên tử). Không bao giờ raise."""
    if not _FACTS_DIRTY:
        return
    try:
        cur = _load_facts_cache_file()
        if cur is None:
            cur = {"v": FACTS_CACHE_VERSION, "rules": _facts_rules_hash(), "files": {}}
        cur["files"].update(_FACTS_DIRTY)
        _FACTS_DIRTY.clear()
        FACTS_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = str(FACTS_CACHE_FILE) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cur, f, indent=0, sort_keys=True)
        os.replace(tmp, str(FACTS_CACHE_FILE))
    except Exception:
        pass


class _FactsView:
    """Bản đọc nhẹ của _Facts, dựng từ cache đĩa: chỉ cần .blocks (khóa),
    .tier() và .smoke_marked() — các nơi dùng _Facts bên ngoài tiering.py
    (plan_counts, test_tiering) không chạm gì khác."""

    def __init__(self, tests):
        self._tests = tests
        self.blocks = dict(((k.split("::", 1)[0], k.split("::", 1)[1]), "") for k in tests)

    def tier(self, cls, name):
        hit = self._tests.get("%s::%s" % (cls, name))
        return hit[0] if hit else FAST

    def smoke_marked(self, cls, name):
        hit = self._tests.get("%s::%s" % (cls, name))
        return bool(hit and hit[1])


# ---------------------------------------------------------------- ghi đè tay + số đo thật
def load_overrides():
    try:
        d = json.loads(OVERRIDES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for tier in PARTITIONS:
        for prefix in d.get(tier, []):
            out[prefix] = tier
    return out


def load_smoke_prefixes():
    try:
        d = json.loads(OVERRIDES_FILE.read_text(encoding="utf-8"))
        return [x for x in d.get(SMOKE, []) if isinstance(x, str)]
    except (OSError, ValueError):
        return []


def load_profile():
    try:
        d = json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def profile_entry(raw):
    """Chuẩn hóa một giá trị profile -> (giây, số lần vượt liên tiếp, số lần
    full dưới ngân sách liên tiếp). Định dạng cũ (một số) vẫn đọc được
    (D-POLISH-3, tương thích ngược)."""
    if isinstance(raw, dict):
        try:
            t = float(raw.get("t", 0.0))
        except (TypeError, ValueError):
            t = 0.0
        try:
            over = int(raw.get("over", 0))
        except (TypeError, ValueError):
            over = 0
        try:
            under = int(raw.get("under", 0))
        except (TypeError, ValueError):
            under = 0
        return t, max(0, over), max(0, under)
    try:
        return float(raw), 0, 0
    except (TypeError, ValueError):
        return 0.0, 0, 0


def profile_seconds(ident, prof=None):
    d = prof if prof is not None else _ctx()["prof"]
    t, _, _ = profile_entry(d.get(ident))
    return t


def record_times(times, tiers, is_full, known_idents=None):
    """Ghi thời gian các test ĐÃ ĐẠT (R3: chỉ test pass mới được ghi) + áp
    hysteresis (R2/D-POLISH-3): vượt ngân sách 2 lần liên tiếp mới promote
    sang slow; slow chạy dưới ngân sách 2 lần FULL liên tiếp mới demote.
    Trả (mới_chậm, đã_hồi_phục) — danh sách ident. Khi is_full thì tỉa khóa
    profile của test không còn tồn tại + ghi _meta (máy, thời điểm)."""
    prof = load_profile()
    budget = budget_s()
    promoted, recovered = [], []
    for ident, sec in times.items():
        tier = tiers.get(ident, FAST)
        t_old, over, under = profile_entry(prof.get(ident))
        was_slow = over >= 2 or (not isinstance(prof.get(ident), dict) and t_old > budget)
        if sec > budget:
            over += 1
            under = 0
            if over >= 2 and tier == FAST and ident not in promoted:
                promoted.append(ident)
        else:
            if was_slow and is_full:
                under += 1
                if under >= 2:
                    over, under = 0, 0
                    recovered.append(ident)
            else:
                over, under = 0, 0
        if over or under:
            prof[ident] = {"t": round(sec, 3), "over": over, "under": under}
        else:
            prof[ident] = round(sec, 3)
    if is_full and known_idents is not None:
        stale = [k for k in prof if k != "_meta" and k not in known_idents]
        for k in stale:
            del prof[k]
        prof["_meta"] = {"machine": platform.node() or "?",
                         "updated": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())}
    PROFILE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(PROFILE_FILE) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(prof, f, indent=0, sort_keys=True)
    os.replace(tmp, str(PROFILE_FILE))
    return promoted, recovered


def save_profile(updates):
    d = load_profile()
    d.update(updates)
    PROFILE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(PROFILE_FILE) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=0, sort_keys=True)
    os.replace(tmp, str(PROFILE_FILE))


_CTX = {}


def _ctx():
    if not _CTX:
        _CTX["ov"], _CTX["prof"], _CTX["budget"] = load_overrides(), load_profile(), budget_s()
        _CTX["smoke"] = load_smoke_prefixes()
    return _CTX


def reset_cache():
    global _FACTS_CACHE
    _CTX.clear()
    _FACTS.clear()
    _GRAPH.clear()
    _FACTS_CACHE = None


def make_id(rel, cls, name):
    return "%s::%s::%s" % (rel, cls, name)


def split_id(some_id):
    """'tests/test_x.py::C::t' (pytest) hoặc 'tests.test_x.C.t' (unittest) -> (rel, cls, name) hoặc None."""
    if "::" in some_id:
        parts = some_id.split("::")
        if len(parts) == 3:
            return parts[0].replace("tests/", "", 1).replace("\\", "/"), parts[1], parts[2].split("[")[0]
        return None
    parts = some_id.split(".")
    if len(parts) >= 4 and parts[0] == "tests":
        return parts[1] + ".py", parts[2], parts[3]
    return None


def classify(rel, cls, name):
    ident = make_id(rel, cls, name)
    c = _ctx()
    memo = c.setdefault("memo", {})
    if ident not in memo:
        memo[ident] = _classify(ident, rel, cls, name, c)
    return memo[ident]


def _classify(ident, rel, cls, name, c):
    best = None
    for prefix, tier in c["ov"].items():
        if ident.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, tier)
    if best:
        return best[1]
    tier = _facts(rel).tier(cls, name)
    if tier == FAST:
        t, over, _ = profile_entry(c["prof"].get(ident))
        if over >= 2:
            return SLOW
        if not isinstance(c["prof"].get(ident), dict) and t > c["budget"]:
            return SLOW   # định dạng cũ: một số vượt ngân sách -> slow như trước
    return tier


def classify_id(some_id):
    p = split_id(some_id)
    return classify(*p) if p else FAST


def is_smoke(rel, cls, name):
    """Cờ smoke: do tiers.json hoặc @smoke chọn, và CHỈ khi test thuộc tầng fast (smoke phải rẻ)."""
    if classify(rel, cls, name) != FAST:
        return False
    ident = make_id(rel, cls, name)
    return any(ident.startswith(x) for x in _ctx()["smoke"]) or _facts(rel).smoke_marked(cls, name)


def is_smoke_id(some_id):
    p = split_id(some_id)
    return bool(p) and is_smoke(*p)


def parse_tiers(spec):
    """'fast' | 'fast,ish' | 'full' -> tập tên tầng. 'full' = mọi tầng phân hoạch. Tên lạ -> ValueError."""
    out = set()
    for x in (spec or "").replace(" ", "").split(","):
        if not x:
            continue
        if x == "full":
            out.update(PARTITIONS)
        elif x in TIER_NAMES:
            out.add(x)
        else:
            raise ValueError("tầng lạ %r; hợp lệ: %s, full" % (x, ", ".join(TIER_NAMES)))
    if not out:
        raise ValueError("chưa chỉ định tầng")
    return out


def is_selected(tiers, primary, smoke_flag):
    """Test (tầng chính `primary`, cờ smoke) có nằm trong tập tầng `tiers` không."""
    return primary in tiers or (SMOKE in tiers and smoke_flag)


def is_full(tiers):
    return set(PARTITIONS) <= set(tiers)


def plan_modules(want):
    """Đếm test mỗi tầng bằng _Facts (không nạp/import test) + module nào
    chứa test được chọn. Trả (tổng, {tầng: số}, smoke, {module: số test
    được chọn}). Số đếm phải khớp số đếm khi nạp thật (test test_runner
    kiểm tra); bộ chạy dùng {module} để chỉ nạp module cần thiết, và đếm
    lại sau khi nạp — lệch thì nạp đủ, không bao giờ mất test âm thầm."""
    reset_cache()
    total = 0
    cnt = dict((n, 0) for n in PARTITIONS)
    smoke_n = 0
    mods = {}
    for rel in sorted(TESTS.glob("test_*.py")):
        r = rel.name
        facts = _facts(r)
        n_sel = 0
        for (cls, name) in sorted(facts.blocks):
            if not name.startswith("test"):
                continue
            total += 1
            tier = classify(r, cls, name)
            cnt[tier] += 1
            sm = is_smoke(r, cls, name)
            if sm:
                smoke_n += 1
            if is_selected(want, tier, sm):
                n_sel += 1
        if n_sel:
            mods["tests." + r[:-3]] = n_sel
    flush_facts_cache()
    return total, cnt, smoke_n, mods


def plan_counts():
    """Đếm test mỗi tầng bằng _Facts (không nạp/import test): dùng cho
    --plan nhẹ (R8). Trả (tổng, {tầng: số}, smoke). Số đếm phải khớp số đếm
    khi nạp thật (test test_runner kiểm tra)."""
    total, cnt, smoke_n, _mods = plan_modules(parse_tiers("full"))
    return total, cnt, smoke_n


# ---------------------------------------------------------------- chọn theo thay đổi (impact)
_GRAPH = {}
_SKIP_DIRS = {"reports", "logs", "workspace", "kb", "legacy", ".git", "__pycache__", ".pytest_cache"}
_FROM_RE = re.compile(r"^[ \t]*from[ \t]+(\.*)([\w.]*)[ \t]+import[ \t]+(\([^)]*\)|[^\n]+)", re.M)
_IMP_RE = re.compile(r"^[ \t]*import[ \t]+([^\n]+)", re.M)
_IMPORT_MODULE_RE = re.compile(r"""importlib\.import_module\(\s*["']([\w.]+)["']\s*\)""")


def tracked_files():
    """File ảnh hưởng tới kết quả test: .py/.toml/.json (không tính reports/logs/workspace/kb)."""
    out = []
    for dp, dn, fn in os.walk(str(ROOT)):
        dn[:] = [d for d in dn if d not in _SKIP_DIRS]
        for f in fn:
            if f.endswith((".py", ".toml", ".json")) and f != "local.toml":
                out.append(os.path.relpath(os.path.join(dp, f), str(ROOT)).replace("\\", "/"))
    return sorted(out)


def file_hashes():
    out = {}
    for rel in tracked_files():
        try:
            out[rel] = hashlib.sha1((ROOT / rel).read_bytes()).hexdigest()
        except OSError:
            pass
    return out


def load_baseline():
    try:
        d = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def save_baseline():
    BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_FILE.write_text(json.dumps(file_hashes(), indent=0, sort_keys=True), encoding="utf-8")


def changed_since_baseline():
    """(tập file đổi, có baseline không)."""
    base = load_baseline()
    if base is None:
        return set(), False
    now = file_hashes()
    ch = {k for k in set(now) | set(base) if now.get(k) != base.get(k)}
    return ch, True


def _deps(rel, known):
    try:
        text = (ROOT / rel).read_text(encoding="utf-8")
    except OSError:
        return set()
    pkg = rel.split("/")[:-1]
    out = set()

    def add_mod(parts):
        for i in range(1, len(parts) + 1):
            for cand in ("/".join(parts[:i]) + ".py", "/".join(parts[:i]) + "/__init__.py"):
                if cand in known:
                    out.add(cand)

    for m in _FROM_RE.finditer(text):
        dots, mod, names = m.group(1), m.group(2), m.group(3)
        base = pkg[:len(pkg) - (len(dots) - 1)] if dots else []
        parts = base + [x for x in mod.split(".") if x]
        add_mod(parts)
        for n in re.split(r"[,\s()]+", names.split("#")[0]):
            if n and n != "as" and re.match(r"^\w+$", n):
                add_mod(parts + [n])
    for m in _IMP_RE.finditer(text):
        for item in m.group(1).split("#")[0].split(","):
            item = item.strip().split(" as ")[0].strip()
            if item:
                add_mod(item.split("."))
    for m in _IMPORT_MODULE_RE.finditer(text):   # importlib.import_module("x.y") literal (R7)
        add_mod(m.group(1).split("."))
    out.discard(rel)
    return out


def closure(rel):
    """Mọi file .py mà `rel` (có thể) phụ thuộc, tính cả gián tiếp."""
    if "known" not in _GRAPH:
        _GRAPH["known"] = {f for f in tracked_files() if f.endswith(".py")}
        _GRAPH["deps"], _GRAPH["clo"] = {}, {}
    known = _GRAPH["known"]
    if rel in _GRAPH["clo"]:
        return _GRAPH["clo"][rel]
    seen, todo = set(), [rel]
    while todo:
        cur = todo.pop()
        if cur in seen:
            continue
        seen.add(cur)
        if cur not in _GRAPH["deps"]:
            _GRAPH["deps"][cur] = _deps(cur, known)
        todo.extend(_GRAPH["deps"][cur])
    _GRAPH["clo"][rel] = seen
    return seen


def is_affected(rel, tier, changed):
    """File test `rel` (tầng `tier`) có bị ảnh hưởng bởi tập file đổi `changed` không."""
    if not changed:
        return False
    infra = {"tests/conftest.py", "tests/tiering.py", "tests/tiers.json"}
    if changed & infra:
        return True
    if any(not c.endswith(".py") for c in changed):      # cấu hình/dữ liệu: không biết ai đọc -> chọn hết
        return True
    me = "tests/" + rel
    if me in changed:
        return True
    clo = closure(me)
    if changed & clo:
        return True
    if tier in (SLOW, META):                              # test slow/meta chạy cả ứng dụng/quét cả cây
        return any(not c.startswith("tests/") for c in changed)
    return False
