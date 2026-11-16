"""Guard dấu tiếng Việt cho thông báo hướng tới người dùng (đợt polish).

Quét CHỈ các chuỗi message đã định danh, không quét toàn bộ source:
  1. bảng ERROR_SPECS trong core/errors.py (cả message_vi và hint_vi);
  2. các chuỗi literal trong các module CLI/menu chính: menu.py, core/errors.py,
     services/unity_tool.py, services/container_tool.py, services/plugin_manager.py,
     ui/cli.py, ui/screens.py, ui/handlers.py.

Mỗi chuỗi được coi là "thông báo tiếng Việt" khi thỏa MỘT trong ba điều:
  (a) chứa ký tự có dấu tiếng Việt, hoặc
  (b) chứa >= 2 âm tiết tiếng Việt (sau khi bỏ dấu), hoặc
  (c) chứa một từ trong danh sách đen BAD_WORDS.
Chuỗi đó FAIL khi:
  (1) không chứa ký tự có dấu nào, hoặc
  (2) chứa từ trong BAD_WORDS.

Tránh false positive bằng cách:
  - BAD_WORDS chỉ gồm những từ KHÔNG BAO GIỜ đúng dạng không dấu. Nhiều từ
    tiếng Việt đúng dạng không dấu nên KHÔNG có trong danh sách, ví dụ:
    trong, sau, dang (dở dang), co, la, va, cho, ra, ve, qua, xem, ghi,
    thi, ma, nhung, nay, kia, tim, dung, bao, cao, nhanh, nho, cu, con,
    lai, chi, ca, nhau, minh, ban, anh, em, ong, ba, chu, di, tu, canh,
    lan, phai, lam, dinh, tin, thu, xoa, tao, sao, tra, xay, hoan, thanh,
    cong, xin, chao, cam, vui, long, hay, hai, bon, nam, kho, khan, de,
    khoe, manh, ngu, ngon, thang, ngay, sang, khuya, tre, sai, can.
    (Ví dụ: "Không có tệp tạm dở dang." và "Trong iSH: apk add python3"
    đều đúng chính tả dù không có dấu ở từ dang/trong.)
  - Chuỗi chỉ có 1 âm tiết tiếng Việt không dấu và không thuộc BAD_WORDS
    (vd "Trong iSH: apk add python3") được bỏ qua, vì 1 từ đơn lẻ không
    đủ bằng chứng đó là câu tiếng Việt.
  - Không quét tests/, fixtures/, docs/, handoff/: ở đó có dữ liệu giả lập
    nội dung game ("Xin chao 9 Kings!" là text của TextAsset trong game,
    không phải thông báo của PB3), identifier (khong_co) và trích dẫn
    nguyên văn bằng chứng iSH.

Tầng meta (quét mã nguồn): đánh dấu @meta + dùng ast.parse/read_src.
Chỉ dùng cú pháp Python 3.8 (iSH).
"""
import ast
import re
import unicodedata
import unittest

from core.errors import ERROR_SPECS
from tests.helpers import ROOT, read_src
from tests.tiering import meta

# Ký tự có dấu tiếng Việt (kể cả ý/Ý: thiếu ý là lỗi hay gặp khi liệt kê).
ACCENTED = set(
    "àáâãèéêìíòóôõùúýăđĩũơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ"
    "ÀÁÂÃÈÉÊÌÍÒÓÔÕÙÚÝĂĐĨŨƠƯẠẢẤẦẨẪẬẮẰẲẴẶẸẺẼẾỀỂỄỆỈỊỌỎỐỒỔỖỘỚỜỞỠỢỤỦỨỪỬỮỰỲỴỶỸ")

_WORD_RE = re.compile(r"[A-Za-zÀ-ỹ]+")

# Âm tiết tiếng Việt dạng bỏ dấu (chỉ âm >= 2 ký tự: tránh "a", "e", "y"
# của mã/tiếng Anh bị nhận nhầm). Dùng để nhận diện câu tiếng Việt.
VI_SYL = set(
    "ai am an anh ao ap at au ay ba bac bai ban bao bat bay be ben bi bien biet "
    "bo bon bu bua buc bui bun bung buoc buoi ca cac cai cam can cao cap cat cau "
    "cay cha chai cham chan chao chap chat chau chay che chem chen chet chi chia "
    "chien chieu chim chin chiu cho chom chon chong chot chu chua chuc chui chung "
    "chup chuyen co coc coi com con cong cop cot cu cua cuc cui cung cuoc cuoi "
    "da dai dam dan dang dao dat dau day de dem den deo dep di dich dieu dinh "
    "do doc doi dom don dong dot du dua duc dui dung duong duoc duy em ga gai "
    "gam gan gap gat gay ghi gia giai giam gian giao giap giat giau giay giu "
    "giua giup go goc goi gom gon got gu ha hai ham han hang hat hay hen heo "
    "het hi hieu hien hoa hoac hoai hoan hoc hoi hom hop hu hue hung huong kha "
    "khac khai kham khan khang khao khat khau kheo khep khi khien khoa khoe khoi "
    "khong khu khuya kiem kieu kin kinh la lai lam lan lang lap lat lay le len "
    "leo li lich lieu lo loan loi lon lua luc lui lung luong luu ly ma mai mang "
    "manh mau may me men mi mo moi mon mong mot mu mua muc mui nao nam nang nep "
    "nga ngai ngan ngay ngap ngat nghe nghiem nghiep ngo ngoai ngon ngu ngua "
    "nguyen nha nhan nhat nhe nhi nhieu nhin nho nhu nhung no noi non nu nua "
    "oi om on ong qua quai quan quat quay quen ra rang rau re ri ro roi rom "
    "rong rua rung sa sai sam san sang sao sat sau se si sinh so soc soi son "
    "sua sung ta tai tam tan tao tap tat tau te ten tep thai tham than thang "
    "thanh thao that the them theo thi thich thieu thoi thong thu thuan thuc "
    "thung ti tien tiep tim tin tinh to toa toan toc toi tom tong tra trai "
    "tram tran tri tro trong tru truoc truong tu tuan tue tung tuoi tuyet ua "
    "uc ung uoc va vai van vang vat ve vi viec vo voi vu vung xem xe xep xin "
    "xu xua xuan xuat yeu hong xong can".split())

# Từ KHÔNG BAO GIỜ đúng dạng không dấu trong thông báo PB3. Cố ý KHÔNG gồm
# các từ đúng dạng không dấu (xem docstring module): gồm chúng sẽ báo sai
# cả câu đúng, vd "trong" trong "Dữ liệu không có trong gói".
BAD_WORDS = set(
    "khong duoc nguoi hien tren truoc duong loi chon kiem quyen viet biet "
    "sua doi ten chuyen that bai chay neu cac day len xuong them bot tap "
    "tep doc mo luu chep phat thap cham lon khac nhat het nua toi bac den "
    "duoi giua ben ngoai gio phut giay dau tien viec giu nguyen goc luc "
    "hanh luon mot tram nghin trieu dong benh tat om met giac dep xau "
    "tot hon tuan trua chieu dem som muon thieu".split())

# Module chứa thông báo CLI/menu hướng tới người dùng (đã định danh).
MESSAGE_MODULES = [
    "menu.py",
    "core/errors.py",
    "services/unity_tool.py",
    "services/container_tool.py",
    "services/plugin_manager.py",
    "ui/cli.py",
    "ui/screens.py",
    "ui/handlers.py",
]


def _strip_marks(word):
    word = word.replace("đ", "d").replace("Đ", "D")
    return "".join(c for c in unicodedata.normalize("NFD", word)
                   if unicodedata.category(c) != "Mn")


def _is_vietnamese(text):
    """True nếu text có vẻ là câu tiếng Việt (xem docstring module)."""
    words = [w.lower() for w in _WORD_RE.findall(text)]
    if not words:
        return False
    if any(ch in ACCENTED for ch in text):
        return True
    if any(w in BAD_WORDS for w in words):
        return True
    vi_count = sum(1 for w in words if _strip_marks(w) in VI_SYL)
    return vi_count >= 2


def _problems(text):
    """Danh sách lỗi dấu của một chuỗi tiếng Việt; [] = đạt hoặc không phải
    tiếng Việt (bỏ qua: identifier, tiếng Anh, đường dẫn)."""
    if not _is_vietnamese(text):
        return []
    out = []
    if not any(ch in ACCENTED for ch in text):
        out.append("không có ký tự có dấu")
    bad = sorted({w for w in _WORD_RE.findall(text.lower()) if w in BAD_WORDS})
    if bad:
        out.append("chứa từ không dấu: " + ", ".join(bad))
    return out


@meta
class TestVietnameseDiacritics(unittest.TestCase):
    def test_error_specs_vi_messages(self):
        fails = []
        for code in sorted(ERROR_SPECS):
            spec = ERROR_SPECS[code]
            self.assertTrue(spec.message_vi.strip(), code)
            for field in ("message_vi", "hint_vi"):
                text = getattr(spec, field)
                if not text.strip():
                    continue
                for p in _problems(text):
                    fails.append("%s.%s: %s (%r)" % (code, field, p, text))
        self.assertEqual(fails, [])

    def test_cli_messages_in_services_and_ui(self):
        fails = []
        for rel in MESSAGE_MODULES:
            path = ROOT / rel
            tree = ast.parse(read_src(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    for p in _problems(node.value):
                        one_line = " / ".join(node.value.splitlines())[:120]
                        fails.append("%s:%d: %s (%r)"
                                     % (rel, node.lineno, p, one_line))
        self.assertEqual(fails, [])

    def test_guard_self_check(self):
        # Guard phải bắt được câu sai dấu, nếu không nó vô dụng.
        self.assertTrue(_problems("khong co plugin"))
        self.assertTrue(_problems("Xin chao 9 Kings!"))
        # ... và phải tha câu đúng dấu.
        self.assertEqual(_problems("Không có plugin này."), [])
        self.assertEqual(_problems("Không có tệp tạm dở dang."), [])
        # ... và tha từ đơn lẻ đúng dạng không dấu ("Trong" ở đây đúng).
        self.assertFalse(_is_vietnamese("Trong iSH: apk add python3"))
        self.assertTrue(_is_vietnamese("thieu --out"))


if __name__ == "__main__":
    unittest.main()
