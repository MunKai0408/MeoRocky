#!/usr/bin/env python3
"""Sinh lại fixture cho harness C5 (exp/verify_analysis_c5.mjs).

- Copy 3 fixture bundle đã commit ở pb3/tests/fixtures/unity/
  (sample_zstd.unity3d, sample_v9.unity3d, sample_v9_zstd.unity3d —
  sinh bằng tests/fixtures/unity/build_fixtures.py, cần gói
  `zstandard` lúc sinh) + 1 bundle LZ4 đối chứng.
- Dựng bundle mã hoá c5_bundle_encrypted.unity3d bằng sdk/unity.py
  (cờ mới 0x1400, revision Unity 6) — parse phải từ chối E_ENCRYPTED.
- Sinh frame Zstd thô c5_zstd_frame.bin bằng gói `zstandard`
  (thiếu gói thì giữ file đã commit và in rõ lý do).

Chạy:  python3 exp/build_c5_fixtures.py
"""
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(os.path.dirname(HERE), "site")
PB3 = os.path.join(SITE, "pb3")
FX_SRC = os.path.join(PB3, "tests", "fixtures", "unity")
FX_DST = os.path.join(HERE, "fixtures")

COPIES = {
    "sample_zstd.unity3d": "c5_bundle_zstd.unity3d",
    "sample_v9.unity3d": "c5_bundle_v9.unity3d",
    "sample_v9_zstd.unity3d": "c5_bundle_v9_zstd.unity3d",
    "sample_lz4.unity3d": "c5_bundle_lz4_control.unity3d",
}

PLAIN = b"Xin chao BearTea - cau noi Zstd thu nghiem 12345"


def main():
    os.makedirs(FX_DST, exist_ok=True)
    for src, dst in COPIES.items():
        p = os.path.join(FX_SRC, src)
        if not os.path.exists(p):
            print("THIEU fixture goc (chay build_fixtures.py truoc):", src)
            continue
        shutil.copyfile(p, os.path.join(FX_DST, dst))
        print("copy %s -> %s (%d byte)"
              % (src, dst, os.path.getsize(os.path.join(FX_DST, dst))))
    sys.path.insert(0, PB3)
    from sdk import unity as u
    assets = open(os.path.join(FX_SRC, "resources.assets"), "rb").read()
    enc = u.build_bundle(
        [("CAB-0123456789abcdef0123456789abcdef", 4, assets)],
        version=8, unity_version="5.x.x", unity_revision="6000.0.23f1",
        compression="none", extra_data_flags=0x1400,
        backend=u.UnityBackend("NATIVE"))
    with open(os.path.join(FX_DST, "c5_bundle_encrypted.unity3d"), "wb") as f:
        f.write(enc)
    print("build c5_bundle_encrypted.unity3d (%d byte)" % len(enc))
    try:
        import zstandard
    except ImportError:
        print("BO QUA c5_zstd_frame.bin: chua cai goi `zstandard` "
              "(pip install zstandard) — giu file da commit.")
        return
    frame = zstandard.ZstdCompressor(level=3).compress(PLAIN)
    with open(os.path.join(FX_DST, "c5_zstd_frame.bin"), "wb") as f:
        f.write(frame)
    print("build c5_zstd_frame.bin (%d byte, plain %d byte)"
          % (len(frame), len(PLAIN)))


if __name__ == "__main__":
    main()
