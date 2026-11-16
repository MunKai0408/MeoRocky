PB3 TOOLKIT v3.1 (iSH-only)
===========================
Tên hiển thị trong menu: BearTea (tên project, đường dẫn, tên file, tên zip
giữ nguyên PB3; xem handoff/decisions.md #165).
Công cụ mod / trích xuất ngôn ngữ cho game iOS, chạy 100% trong iSH trên iPhone.
Không server, không cloud, không tải mã khi chạy.

TRẠNG THÁI HIỆN TẠI (cập nhật 2026-11-16, Claude 10 sandbox DONE — RELEASE BLOCKED chờ kiểm chứng iSH)
-----------------------------------------------------------
ĐÃ CÓ:   nền móng (core/ ui/), Plugin SDK, loader + registry + isolation +
         arbitration, conformance kit, lệnh `plugin list/new/test/enable/disable`, chẩn đoán iSH,
         thanh tiến độ, giữ iSH chạy nền.
         VFS thật cho ZIP/TAR/GZ/XZ (ZIP ghi được, còn lại chỉ đọc), container lồng
         ('::'), plugin container zip (M2, plugins/zip/), lệnh `container list/read`.
         Unity backend (M2): UnityFS v6/v7/v8 + SerializedFile v15-23 (Unity 6), LZ4/LZMA,
         backend AUTO/LIBRARY/NATIVE, planner, patch TextAsset
         (PATCH_INPLACE/REBUILD_SECTION + diffverify; REBUILD_ALL khóa ở M2),
         lệnh `unity inspect/read/patch` và `mod` (dry-run mặc định).
         Trích xuất ngôn ngữ (M2): pipeline 7 bước qua plugin định dạng C06
         (discovery/engine/locale, arbitration, export JSON key-value + report
         + checkpoint/resume), lệnh `extract` + `--apply`, menu [2]; ghi ngược
         TextAsset Unity patch vào bundle (dry-run mặc định).
         12 plugin định dạng (JSON/CSV/XML/YAML/INI/Properties/plist/.strings/
         SQLite chỉ đọc/PO/MO/XLIFF/RESX/generic_strings) + đa engine
         (Godot PCK/.translation, Unreal PAK/LOCRES, Cocos/Defold/GameMaker
         chỉ nhận diện) + dịch tự động Google Translate (kèm credit
         'Việt Hoá Bởi Bear', menu hiển thị BearTea) + batch, support, kb
         (Claude 09 — xem handoff/account_09.md; số test xem CURRENT_STATE.md).
         7z chưa hỗ trợ. UnityPy 1.25.4 + lz4 đã cài trên iSH (2026-11-15).
         Gói 05 COMPLETE 2026-11-15 (iSH PASS bản 643 test + Unity CLI thật).
         Gói 10 sandbox DONE 2026-11-16 (suite 1395 test OK; nghiệm thu 12/13
         tiêu chí PASS, tiêu chí 1 NOT RUN ON DEVICE — xem handoff/account_10.md).
Chi tiết: CURRENT_STATE.md (nguồn sự thật), PROGRESS.md, CHANGELOG.txt.

1. CÀI ĐẶT TRONG iSH
   apk update
   apk add python3            # cần Python >= 3.8 (iSH thường có 3.9)
   apk add py3-pytest         # tuỳ chọn; không có vẫn chạy test được (xem mục 4)
   Giải nén pb3.zip, đặt thư mục pb3 vào /root/pb3.
   Không cần pip. Đừng "pip install" tạm vì có thể treo/đầy bộ nhớ.

2. KHỞI CHẠY
   cd /root/pb3
   python3 menu.py                  # menu tương tác (gõ số, 0 để thoát)
   python3 menu.py --help           # danh sách lệnh
   python3 menu.py -V               # xem phiên bản

   Tuỳ chọn chung (đặt trước tên lệnh):
     --debug       giữ thư mục tạm, log DEBUG ra stderr
     --keepalive   giữ iSH chạy nền qua /dev/location (cần cấp quyền vị trí cho iSH)

   LỆNH ĐÃ DÙNG ĐƯỢC
     python3 menu.py diagnose --ish           báo cáo môi trường/tương thích iSH
     python3 menu.py diagnose --ish --deep    thử import lz4/UnityPy (có thể >1 phút)
     python3 menu.py plugin list              bảng plugin: id, kind, engine, maturity,
                                              trạng thái, lý do (M4→M2 = bị hạ do thiếu
                                              bằng chứng)
     python3 menu.py plugin new <kind> <id>   sinh skeleton vào plugins_user/<id>/
                                              kind: format | container | engine | extractor
     python3 menu.py plugin test <id>         chạy conformance cho plugin <id>
                                              (kỳ vọng "KẾT QUẢ: PASS (CHƯA KIỂM DỮ LIỆU)" khi fixture còn rỗng)
     python3 menu.py plugin disable <id>      tắt plugin (lưu trong config/local.toml)
     python3 menu.py plugin enable <id>       bật lại plugin
                                              (hoặc menu mục 3: gõ số để bật/tắt, khỏi nhớ id)
     python3 menu.py container list <file>    bảng kê entry của ZIP/IPA/TAR/GZ/XZ
     python3 menu.py container read <file> <entry> --out <dest>
                                              đọc một entry ra file (kể cả đường dẫn
                                              lồng '::', vd level.unity3d::resources.assets;
                                              không ghi đè file đã có)
     python3 menu.py unity inspect <file>     bản đồ container -> entry -> object
                                              (TextAsset, JSON, chuỗi dài được ưu tiên)
     python3 menu.py unity read <file> [entry] --out <dest>
                                              đọc entry (kể cả '::'); --object <id> với
                                              TextAsset thì in script ra stdout
     python3 menu.py unity patch <file> [entry] --object <id> --old <s> --new <s>
                                              patch TextAsset; mặc định chỉ dry-run,
                                              thêm --allow-write để ghi thật (ghi ra
                                              workspace/output, không ghi đè nguồn;
                                              bundle trong IPA chỉ xuất bundle)
     python3 menu.py mod <file> [--file <entry>] --object <id> --old <s> --new <s>
                                              như unity patch (không có --old/--new
                                              thì chỉ in plan); menu mục 1 cũng dùng được

   Thử nhanh quy trình plugin:
     python3 menu.py plugin new format thu_nghiem
     python3 menu.py plugin test thu_nghiem
     # xong thì xóa: plugins_user/thu_nghiem và tests/fixtures/thu_nghiem
     Lưu ý: skeleton PASS với fixture rỗng chỉ chứng minh hợp đồng + selftest,
     không chứng minh parse đúng dữ liệu thật. Muốn kiểm thật, bỏ file mẫu nhỏ vào
     tests/fixtures/<id>/.

   LỆNH ĐÃ DÙNG ĐƯỢC (tiếp)
     python3 menu.py extract <path> [--locale L] [--out DIR] [--translate vi]
                                              trích xuất ngôn ngữ ra JSON key-value + report
                                              (dry-run; --apply + --allow-write mới ghi ngược)
     python3 menu.py translate <file.json> [--to vi] [--provider google|offline|fake]
                                              dịch tự động bảng đã trích (mặc định gắn credit
                                              'Việt Hoá Bởi Bear'; --no-credit để tắt)
     python3 menu.py batch <manifest> [--jobs N] [--resume] [--limit N] [--only PATTERN]
                                              [--timeout-game GIÂY] [--allow-write]
                                              xử lý hàng loạt theo manifest CSV/JSON (dry-run mặc định)
     python3 menu.py support [--write]        in ma trận hỗ trợ (ghi docs/SUPPORT_MATRIX.md)
     python3 menu.py kb export|import|forget|prune [arg]
                                              kho tri thức game (SQLite, xem docs/HUONG_DAN.md)
     python3 menu.py compress <file.ipa> [--out FILE] [--preset 0-9] [--force]
                                              nén IPA kiểu WinRAR (solid LZMA, đích ≤400MB,
                                              không bao giờ xuất quá 500MB)
     python3 menu.py decompress <file.tar.xz> [--out FILE]
                                              khôi phục IPA gốc từ archive nén PB3
     python3 menu.py font scan|rebuild|replace|bundle <path> [--font FILE] [--out DIR]
                                              công cụ font 4 chế độ (quét/gộp/thay font)
     python3 menu.py diagnose inventory|hex|strings <file> [--offset N] [--limit N]
                                              chẩn đoán dữ liệu (chỉ đọc)

   MENU TƯƠNG TÁC: [1] Mod và ghi ngược, [2] Trích xuất ngôn ngữ (+ dịch tự
   động), [3] Quản lý plugin và ma trận hỗ trợ, [4] Xử lý hàng loạt,
   [5] Chẩn đoán ([1] Môi trường iSH, [2] Cách ly tệp tạm,
   [3] Inventory/hex/strings, [4] Chạy nền qua vị trí), [6] Nén IPA kiểu
   WinRAR, [7] Công cụ font (4 chế độ).

   Mã thoát: 0 ok | 1 lỗi người dùng | 2 chưa hỗ trợ/chưa triển khai |
             3 an toàn/từ chối ghi | 4 lỗi nội bộ.
   Ctrl-C lần 1 = hủy an toàn, lần 2 = dừng ngay.
   Nếu iOS đóng iSH giữa chừng, lần chạy sau sẽ cảnh báo và (khi bạn đồng ý)
   cách ly tệp tạm dở dang vào workspace/temp/_stale/. PB3 không tự xóa dữ liệu.

   CHẠY TEST (5 tầng: smoke / fast / ish / slow / meta; chi tiết docs/TEST_STRATEGY.md):
     python3 run_tests.py --tier smoke          vài giây, test then chốt (sau mỗi lần sửa)
     python3 run_tests.py                       hằng ngày: chỉ bộ NHANH
     python3 run_tests.py --tier ish            test chỉ có nghĩa trên máy thật (symlink, /proc)
     python3 run_tests.py --changed             chỉ test bị ảnh hưởng bởi file đã đổi
     python3 run_tests.py tests/test_X.py       một gói: chạy ĐỦ file test của gói
     python3 run_tests.py --tier full           BÀN GIAO: tất cả (xanh thì lập mốc cho --changed)
     python3 run_tests.py --plan                chỉ in kế hoạch, không chạy
   Dùng pytest: python3 -m pytest tests --tier fast (hoặc smoke, ish, fast,ish) | python3 -m pytest tests (đầy đủ).
   PB3_FAST=1 (cũ, chỉ bỏ test tiến trình con) vẫn chạy được nhưng đã được --tier fast thay thế.

   Thanh tiến độ: tự hiện khi chạy trong terminal thật. Ép bật/tắt bằng
   PB3_PROGRESS=1 / PB3_PROGRESS=0, hoặc progress.overall = false trong
   config/local.toml. Chi tiết: PROGRESS.md.

3. CẤU TRÚC THƯ MỤC (/root/pb3)
   menu.py            điểm vào duy nhất (chỉ điều phối)
   run_tests.py       chạy test có tiến độ sống
   core/              hạ tầng: errors, config, logging, safe_io, limits, progress,
                      overall, keepalive, paths, ish_compat, app, ...
                      (không import ngược lên tầng trên)
   ui/                menu tương tác + CLI (không chứa logic parse/patch)
   sdk/               Plugin SDK: api, manifest, types, ir, vfs, containers (VFS thật),
                      testing, example_kv, formats, unity, lz4pure, discovery,
                      extraction, exporters, translate, godot, unreal, batch, kb,
                      support_matrix, arbitration
   registry/          loader, registry, isolation, arbitration, compatibility,
                      conformance, evidence, template, pluginpkg, scan
   services/          plugin_manager (lệnh `plugin ...`, `support`, `kb`),
                      container_tool (lệnh `container ...`), unity_tool
                      (lệnh `unity ...`, `mod`), language_tool (lệnh `extract`),
                      translate_tool (lệnh `translate`), batch_tool (lệnh `batch`)
   plugins/           20 plugin dựng sẵn (zip, 12 định dạng, Godot/Unreal,
                      Cocos/Defold/GameMaker chỉ nhận diện)
   plugins_user/      plugin của bạn (trùng id thì thắng builtin, có cảnh báo)
   config/            default.toml (mặc định, đừng sửa); local.toml (của bạn, tự tạo)
   workspace/         input/ temp/ output/ cache/ (tự tạo khi chạy)
   logs/ reports/ kb/ log (xoay vòng + theo phiên), báo cáo, kho tri thức
   handoff/           bàn giao giữa các tài khoản (decisions.md, account_*.md)
   docs/              INVENTORY.md, SDK_CONTRACT.md (hợp đồng plugin),
                      SUPPORT_MATRIX.md (sinh tự động), HUONG_DAN.md (hướng dẫn
                      sử dụng), TEST_STRATEGY.md (quy hoạch bộ test)
   tests/             test tự động
   tests/corpus/      bằng chứng M3/M4 (R5) — xem tests/corpus/README.txt
   Nghiệm thu: handoff/account_10.md (§4 bảng 13 tiêu chí, §5 checklist iSH).

   Đầu vào: đặt file vào workspace/input/ hoặc nhập đường dẫn local. Đầu ra luôn
   nằm trong workspace/output/ và KHÔNG bao giờ ghi đè: đã có thì tạo
   ten.v1.ext, ten.v2.ext...

4. CHẠY TEST (trong /root/pb3)
   python3 run_tests.py                              # có [n/tổng] %, báo test treo
   python3 run_tests.py -l /root/t.log               # ghi thêm dòng kết quả vào log
   python3 -m unittest discover -s tests -t . -v     # luôn chạy được
   python3 -m pytest tests -v                        # nếu đã cài pytest
   Kỳ vọng bản này: 1395 test (sandbox; iSH có 5 skip: 1 skip chung + 4 skip
   chỉ iSH — 2 thiếu tomllib, 1 máy có /dev/location, 1 UnityPy đã cài).
   iSH 2026-11-15: PASS bản 527 test — 524 passed, 3 skipped, 0 failed, 264,9 s (gói 03 COMPLETE).
   iSH 2026-11-15 19:20: PASS bản 575 test — 572 passed, 3 skipped, 0 failed, 449,0 s (gói 04 COMPLETE);
   container list + plugin test zip PASS 19:21 trên fixture sample.ipa.
   iSH 2026-11-15 20:50: PASS bản 643 test — 639 passed, 4 skipped, 0 failed, 719,4 s (gói 05 COMPLETE);
   Unity CLI thật PASS 20:52/20:56. Gói 10 (1395 test) sandbox DONE, chưa chạy trên iSH —
   nghiệm thu 12/13 tiêu chí PASS, tiêu chí 1 NOT RUN ON DEVICE (xem handoff/account_10.md §5).
   Test chạy ở môi trường nào thì chỉ là bằng chứng cho môi trường đó.

5. CẤU HÌNH
   Ghi đè trong config/local.toml (chỉ cần khóa muốn đổi), ví dụ:
       [limits]
       workers = 1
       max_memory_read_bytes = 67108864
   Khóa/kiểu/miền giá trị hợp lệ: core/config.py (OPTIONS). Giá trị sai hoặc file
   hỏng KHÔNG làm menu hỏng: PB3 cảnh báo rồi dùng mặc định.

6. GIỚI HẠN ĐÃ BIẾT
   - Nếu dòng tiến độ bị gập trên iSH (terminal báo sai số cột): đặt tay [progress] width = 40 trong config/local.toml.
   - Đã có ghi ngược Unity (PATCH_INPLACE/REBUILD_SECTION + diffverify) và đa
     engine (Godot/Unreal đọc có giới hạn, Cocos/Defold/GameMaker chỉ nhận
     diện). 7z chưa hỗ trợ; PCK v0/v3, PAK nén/mã hóa/Oodle, bundle mã hóa
     chưa hỗ trợ; REBUILD_ALL khóa ở M2 (cần M4).
   - Plugin chạy dưới "guard" best effort, KHÔNG phải sandbox. Thêm plugin =
     chạy code trên máy bạn; chỉ dùng plugin bạn tin cậy.
   - Trước khi nạp, plugin trong plugins_user bị quét tĩnh (registry/scan.py): từ chối nếu import
     socket/posix/ctypes/subprocess/importlib..., gọi eval/exec/__import__... Đây chỉ là lint: mã che
     giấu vẫn qua được. Vẫn phải đọc mã plugin lạ trước khi bỏ vào plugins_user/.
   - Plugin khai maturity M3/M4 mà thiếu tests/corpus/<id>.json hợp lệ vẫn nạp
     nhưng bị hạ xuống M2 và có cảnh báo.
   - Bật/tắt plugin chỉ có hiệu lực từ lần dựng danh sách plugin sau (mỗi lệnh CLI
     tự dựng lại; menu đảo ngay). Plugin có manifest hỏng không bật/tắt được.
   - Đã kiểm thử đầy đủ trên iSH thật (2026-11-15): 527 test PASS (524 passed, 3 skipped,
     264,9 s); `plugin new/test/disable/enable thu_nghiem` thật PASS cùng ngày.
   - Đợt polish (2026-11-15): 756 test PASS trong sandbox (755 passed, 1 skipped, 29,8 s);
     iSH NOT RUN — kiểm chứng bằng zip polish (thay zip 05b), xem handoff/account_polish.md.
   - Chữ ký IPA: PB3 sẽ không ký lại (đặc tả 11.1).
