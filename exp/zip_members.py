#!/usr/bin/env python3
"""zip_members.py — liệt kê sha256 từng member của 1 file zip (dùng cho
harness verify_ipa_direct.mjs). Chuẩn hoá tên file nguồn trong report
(*.ipa vs *.zip) trước khi hash: report ghi đúng tên file nguồn nên 2
report khác nhau đúng 1 dòng tên file — đó là hành vi đúng, không phải
lệch kết quả. In JSON: [[filename, sha256, size], ...] đã sắp xếp.
Dùng: python3 zip_members.py <file.zip>
"""
import hashlib
import json
import re
import sys
import zipfile

FIXTURE_RE = re.compile(rb"extract_select_fixture\.(ipa|zip)")
NESTED_RE = re.compile(rb"nested\.(ipa|zip)")


def main() -> int:
    rows = []
    with zipfile.ZipFile(sys.argv[1]) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            data = z.read(info.filename)
            data = FIXTURE_RE.sub(b"FIXTURE", data)
            data = NESTED_RE.sub(b"FIXTURE", data)
            rows.append([info.filename, hashlib.sha256(data).hexdigest(), info.file_size])
    print(json.dumps(sorted(rows)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
