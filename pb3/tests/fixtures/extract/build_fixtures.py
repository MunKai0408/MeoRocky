#!/usr/bin/env python3
"""Dựng fixture trích xuất (Claude 07) — chạy lại cho cùng kết quả.

- game1.zip: nhiều định dạng + nhiều locale + key trùng + file index +
  container lồng + file loại trừ + file chuỗi thô.
- ambig.zip: hai file en.json ngang điểm (cho test hỏi người dùng).
"""
import io
import json
import zipfile

FIXED_DATE = (2026, 1, 1, 0, 0, 0)


def _zip_bytes(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in sorted(entries.items()):
            info = zipfile.ZipInfo(name, FIXED_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)
    return buf.getvalue()


def build_game1():
    en = {"hello": "Hello", "bye": "Goodbye", "settings": "Settings",
          "new_game": "New Game", "welcome": "Welcome, {name}!",
          "cafe": "café"}
    zh = {"hello": "你好", "bye": "再见", "settings": "设置",
          "new_game": "新游戏", "welcome": "欢迎，{name}！", "cafe": "咖啡馆"}
    csv = "key,en,zh\nplay,Play,开始\nquit,Quit,退出\n"
    strings = '"ok" = "OK";\n"cancel" = "Cancel";\n'
    props = "title=My Game\nwelcome=Welcome %s!\ndup=first\ndup=second\n"
    index = {"game": "Test Game",
             "localization": {"en": "en.json", "zh": "zh-Hans.json"}}
    inner = _zip_bytes({"en.json": json.dumps(
        {"inner_key": "Inner value", "hello": "Hello from inner"},
        ensure_ascii=False).encode("utf-8")})
    raw = b"\x00\x01\x02" + b"SecretString123\x00" + b"AnotherHiddenText\x00"
    return _zip_bytes({
        "localization/en.json": json.dumps(en, ensure_ascii=False, indent=2).encode("utf-8"),
        "localization/zh-Hans.json": json.dumps(zh, ensure_ascii=False, indent=2).encode("utf-8"),
        "localization/strings.csv": csv.encode("utf-8"),
        "localization/ui.strings": b"\xff\xfe" + strings.encode("utf-16-le"),
        "localization/app.properties": props.encode("utf-8"),
        "localization/index.json": json.dumps(index, ensure_ascii=False).encode("utf-8"),
        "data/font.ttf": b"\x00\x01\x00\x00" + b"\x00" * 100,
        "logs/debug.log": b"debug log\n",
        "inner.zip": inner,
        "raw.bin": raw,
    })


def build_ambig():
    a = {"hello": "Hello A", "bye": "Goodbye A"}
    b = {"hello": "Hello B", "bye": "Goodbye B"}
    return _zip_bytes({
        "a/en.json": json.dumps(a, ensure_ascii=False).encode("utf-8"),
        "b/en.json": json.dumps(b, ensure_ascii=False).encode("utf-8"),
    })


if __name__ == "__main__":
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "game1.zip"), "wb") as fh:
        fh.write(build_game1())
    with open(os.path.join(here, "ambig.zip"), "wb") as fh:
        fh.write(build_ambig())
    print("đã dựng game1.zip + ambig.zip")
