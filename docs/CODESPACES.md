# Chạy MeoRocky trên GitHub Codespaces (từ iPhone)

## Codespaces là gì

Máy Linux trên cloud của GitHub, có VS Code + terminal chạy ngay trong trình
duyệt. Dùng để chạy test Python khi không có máy tính. Miễn phí
120 core-hours/tháng + 15 GB lưu trữ — hết quota thì máy dừng, không phát
sinh phí nếu chưa đặt spending limit.

## Mở Codespaces (từ Safari trên iPhone)

1. Mở github.com/MunKai0408/MeoRocky
2. Chạm nút xanh Code → chạm tab Codespaces → Create codespace on main
3. Đợi build xong (~1–2 phút lần đầu) — VS Code mở ngay trong trình duyệt,
   Python 3.12 + pytest + lz4 đã cài sẵn theo `.devcontainer/devcontainer.json`
4. Mở terminal: chạm menu ≡ → Terminal → New Terminal

## Câu lệnh test (dán vào terminal)

Khôi phục font (chạy 1 lần sau khi mở codespace mới — giải mã file `.b64`
có sẵn trong repo):

```bash
python3 - <<'EOF'
import base64, pathlib
raw = base64.b64decode(pathlib.Path("fonts/BeVietnamPro-Regular.ttf.b64").read_text().replace("\n", "").replace(" ", ""))
pathlib.Path("pb3/fonts/BeVietnamPro-Regular.ttf").write_bytes(raw)
print("font bytes:", len(raw))
EOF
```

Smoke (nhanh, ~60 test then chốt, vài giây):

```bash
cd pb3 && python3 run_tests.py --tier smoke
```

Unit (1436 test, không cần fixture nhị phân):

```bash
cd pb3 && python3 -m pytest tests -q -rs -m "not needs_fixture"
```

Full suite (toàn bộ, cần fixture — tải 1 lần, `gh` đã đăng nhập sẵn trong
Codespaces):

```bash
gh release download fixtures-v1 --pattern pb3-fixtures-v1.zip --dir /tmp
python3 -c "import zipfile; zipfile.ZipFile('/tmp/pb3-fixtures-v1.zip').extractall('pb3')"
cd pb3 && python3 -m pytest tests -q -rs
```

Chạy 1 file test:

```bash
cd pb3 && python3 -m pytest tests/test_errors.py -q
```

Harness BUG#2 (từ gốc repo; thiếu file game thì bỏ qua, không fail):

```bash
python3 exp/verify_bug2_merge.py
```

Pyodide smoke (kiểm chứng runtime trình duyệt, cần Node 20 — đã có sẵn):

```bash
node .github/scripts/pyodide_smoke.mjs
```

## Tiết kiệm quota

- Codespace tự dừng sau 30 phút không dùng. Khi xong việc, xóa codespace
  (github.com → Code → Codespaces → … → Delete codespace) để khỏi tốn giờ free.
- Mỗi lần mở codespace mới là máy mới: phải chạy lại bước khôi phục font +
  tải fixture.
