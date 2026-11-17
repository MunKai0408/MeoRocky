"""Test tăng tốc dịch (feat/translate-speed) — đo bằng fixture/mock, không
gọi Google thật.

- Split-on-failure: batch lỗi do mất marker thì chia đôi đệ quy thay vì
  rớt cả batch về dịch đơn (đo thật: 1 batch 100 chuỗi rớt marker = 52s
  thay vì 2,3s); 429 thì KHÔNG chia (chia cũng lỗi như cũ).
- Batched repair: bước sửa placeholder (PR #9) dịch lại theo batch <=25
  dòng lệch thay vì từng dòng (đo thật: 28 dòng = ~14s khi dịch đơn).
- Latch tắt batch sau 2 lần lỗi liên tiếp vẫn giữ nguyên.
- Masking placeholder + verify+repair của PR #9 giữ nguyên: cổng kiểm
  tra check_placeholders phải PASS ở mọi test có placeholder.
"""
import asyncio
import json
import re
import sys
import unittest

import bridge
from bridge import _batch_split_retryable


class _BatchErr(Exception):
    """Lỗi batch từ fake js: có kind (như JsException) + cờ splitRetryable
    tùy chọn (như gt.js gắn vào lỗi batch_failed)."""

    def __init__(self, kind, msg, split_retryable=None):
        super().__init__(msg)
        self.kind = kind
        if split_retryable is not None:
            self.splitRetryable = split_retryable


class _FakeJsBase:
    """Nền fake js: dịch đơn giữ nguyên text (thêm tiền tố [vi])."""

    def __init__(self):
        self.batch_calls = 0
        self.single_calls = 0

    async def pb3_google_translate(self, text, src, dst):
        self.single_calls += 1
        return "[vi] " + text

    def pb3_google_cancelled(self):
        return False

    def pb3_fallback_trace(self):
        return "[]"


def _batch_ok(texts):
    return json.dumps({"translations": ["[vi] " + t for t in texts],
                       "mode": "gtx-joined"}, ensure_ascii=False)


class _FakeJsSplitOnce(_FakeJsBase):
    """Batch lỗi marker 1 lần duy nhất (lần gọi đầu), sau đó batch nào
    cũng thành công — mô phỏng Google làm mất marker thoáng qua."""

    def __init__(self):
        super().__init__()
        self._failed_once = False

    async def pb3_google_translate_batch(self, texts, src, dst):
        self.batch_calls += 1
        if not self._failed_once:
            self._failed_once = True
            raise _BatchErr(
                "batch_failed",
                "Google trả về thiếu marker batch — sẽ tự dịch lại từng chuỗi.",
                split_retryable=True)
        return _batch_ok(texts)


class _FakeJsBadString(_FakeJsBase):
    """Mọi batch chứa chuỗi ZZZBAD đều lỗi marker (mất marker do nội dung
    chuỗi đó) — split phải cô lập đúng chuỗi xấu đó xuống tới dịch đơn."""

    async def pb3_google_translate_batch(self, texts, src, dst):
        self.batch_calls += 1
        if any("ZZZBAD" in t for t in texts):
            raise _BatchErr(
                "batch_failed",
                "Google trả về thiếu marker batch — sẽ tự dịch lại từng chuỗi.",
                split_retryable=True)
        return _batch_ok(texts)


class _FakeJs429(_FakeJsBase):
    """Mọi batch đều 429 (không chia được); dịch đơn vẫn qua Google."""

    async def pb3_google_translate_batch(self, texts, src, dst):
        self.batch_calls += 1
        raise _BatchErr(
            "batch_failed",
            "Google lỗi Google Translate giới hạn tốc độ (HTTP 429). — batch lỗi.",
            split_retryable=False)


class _FakeJsMarkerNoFlag(_FakeJsBase):
    """Như SplitOnce nhưng lỗi KHÔNG gắn cờ splitRetryable (mô phỏng
    thuộc tính JS không qua được ranh giới Pyodide) — bridge phải nhận
    diện qua chuỗi 'thiếu marker' trong message."""

    def __init__(self):
        super().__init__()
        self._failed_once = False

    async def pb3_google_translate_batch(self, texts, src, dst):
        self.batch_calls += 1
        if not self._failed_once:
            self._failed_once = True
            raise _BatchErr(
                "batch_failed",
                "Google trả về thiếu marker batch — sẽ tự dịch lại từng chuỗi.")
        return _batch_ok(texts)


class _FakeJsDropFirstBatch(_FakeJsBase):
    """Batch gộp lần đầu làm rơi token placeholder (như gtx thật); các lần
    batch sau giữ token. Dịch đơn luôn giữ token."""

    async def pb3_google_translate_batch(self, texts, src, dst):
        self.batch_calls += 1
        if self.batch_calls == 1:
            items = ["[vi] " + re.sub(r"__PB3PH\d+__", "", t) for t in texts]
        else:
            items = ["[vi] " + t for t in texts]
        return json.dumps({"translations": items, "mode": "gtx-joined"},
                          ensure_ascii=False)


class _FakeJsRepairBatchFails(_FakeJsBase):
    """Batch đầu làm rơi token; batch sửa (lần gọi batch thứ 2) thì lỗi —
    bước sửa phải rớt về dịch đơn như trước."""

    async def pb3_google_translate_batch(self, texts, src, dst):
        self.batch_calls += 1
        if self.batch_calls == 1:
            items = ["[vi] " + re.sub(r"__PB3PH\d+__", "", t) for t in texts]
            return json.dumps({"translations": items, "mode": "gtx-joined"},
                              ensure_ascii=False)
        raise _BatchErr(
            "batch_failed",
            "Google trả về thiếu marker batch — sẽ tự dịch lại từng chuỗi.",
            split_retryable=True)


def _run(kv, fake):
    sys.modules["js"] = fake
    try:
        raw = asyncio.run(bridge.translate_json_google(
            json.dumps(kv, ensure_ascii=False), "vi", False, "en"))
    finally:
        sys.modules.pop("js", None)
    return json.loads(raw)


def _kv_plain(n, prefix="Market stall number"):
    return {"k%03d" % i: "%s %d" % (prefix, i) for i in range(n)}


def _kv_with_placeholders(n):
    return {"p%03d" % i: "Unlock {VAR1} item %d and win {0} points" % i
            for i in range(n)}


class SplitCase(unittest.TestCase):
    def test_split_on_marker_loss_saves_batch(self):
        # 30 chuỗi = 1 batch; batch lỗi marker 1 lần -> chia 15+15, cả 2
        # nửa đều xong qua batch, KHÔNG chuỗi nào phải dịch đơn.
        fake = _FakeJsSplitOnce()
        kv = _kv_plain(30)
        res = _run(kv, fake)
        self.assertEqual(fake.batch_calls, 3)
        self.assertEqual(fake.single_calls, 0)
        self.assertEqual(res["batch_requests"], 2)
        self.assertEqual(res["single_requests"], 0)
        self.assertEqual(res["glossary_count"], 0)
        for key, value in kv.items():
            self.assertEqual(res["translated"][key], "[vi] " + value, key)
        self.assertTrue(res["self_check"]["ok"])

    def test_split_isolates_persistent_bad_string(self):
        # Chỉ chuỗi ZZZBAD làm lỗi marker: split cô lập đúng nửa chứa nó
        # xuống tới <=10 chuỗi rồi dịch đơn 7 chuỗi đó; 23 chuỗi còn lại
        # vẫn qua batch. Batch gọi: 30(lỗi) + 15(lỗi) + 15(ok) + 7(lỗi)
        # + 8(ok) = 5 lần.
        fake = _FakeJsBadString()
        kv = _kv_plain(30)
        kv["k004"] = "Market stall ZZZBAD {0} number 4"
        res = _run(kv, fake)
        self.assertEqual(fake.batch_calls, 5)
        self.assertEqual(fake.single_calls, 7)
        for key, value in kv.items():
            self.assertEqual(res["translated"][key], "[vi] " + value, key)
        self.assertTrue(res["self_check"]["ok"])

    def test_no_split_on_429_falls_back_to_singles(self):
        # Batch 429 (splitRetryable=False) thì KHÔNG chia — rớt về dịch
        # đơn cả batch như trước (1 lần gọi batch duy nhất).
        fake = _FakeJs429()
        kv = _kv_plain(30)
        res = _run(kv, fake)
        self.assertEqual(fake.batch_calls, 1)
        self.assertEqual(fake.single_calls, 30)
        for key, value in kv.items():
            self.assertEqual(res["translated"][key], "[vi] " + value, key)
        self.assertTrue(res["self_check"]["ok"])

    def test_split_uses_message_fallback_without_flag(self):
        # Lỗi không gắn cờ splitRetryable nhưng message có "thiếu marker"
        # thì vẫn chia (phòng thuộc tính JS không qua được Pyodide).
        fake = _FakeJsMarkerNoFlag()
        kv = _kv_plain(30)
        res = _run(kv, fake)
        self.assertEqual(fake.batch_calls, 3)
        self.assertEqual(fake.single_calls, 0)
        for key, value in kv.items():
            self.assertEqual(res["translated"][key], "[vi] " + value, key)

    def test_latch_disables_batch_after_two_failures(self):
        # 250 chuỗi = 3 batch (100+100+50), batch nào cũng 429: sau 2 lần
        # lỗi liên tiếp thì latch tắt batch, batch thứ 3 đi thẳng về đơn
        # (chỉ 2 lần gọi batch).
        fake = _FakeJs429()
        kv = _kv_plain(250)
        res = _run(kv, fake)
        self.assertEqual(fake.batch_calls, 2)
        self.assertEqual(fake.single_calls, 250)
        self.assertTrue(any("tắt batch" in (w or "") for w in res["warnings"]))
        for key, value in kv.items():
            self.assertEqual(res["translated"][key], "[vi] " + value, key)
        self.assertTrue(res["self_check"]["ok"])


class SplitFlagCase(unittest.TestCase):
    def test_split_retryable_flag_detection(self):
        self.assertTrue(_batch_split_retryable(
            _BatchErr("batch_failed", "x", split_retryable=True)))
        self.assertFalse(_batch_split_retryable(
            _BatchErr("batch_failed", "x", split_retryable=False)))
        self.assertTrue(_batch_split_retryable(_BatchErr(
            "batch_failed",
            "Google trả về thiếu marker batch — sẽ tự dịch lại từng chuỗi.")))
        self.assertFalse(_batch_split_retryable(_BatchErr(
            "batch_failed",
            "Google lỗi Google Translate giới hạn tốc độ (HTTP 429).")))
        self.assertFalse(_batch_split_retryable(Exception("lỗi khác")))


class RepairBatchCase(unittest.TestCase):
    def test_batch_repair_fixes_without_singles(self):
        # Batch đầu làm rơi token ở 3 dòng; bước sửa gom batch (1 lần gọi
        # batch thứ 2, giữ token) -> xong hết, không cần dịch đơn dòng nào.
        fake = _FakeJsDropFirstBatch()
        kv = {"a": "Unlock {VAR1} now",
              "b": "You have {0} points and %s",
              "c": "Pay $VAR and use {foo-bar} here"}
        res = _run(kv, fake)
        self.assertEqual(fake.batch_calls, 2)
        self.assertEqual(fake.single_calls, 0)
        self.assertEqual(res["warnings"], [])
        from sdk.gate import check_placeholders
        gate = check_placeholders(kv, res["translated"])
        self.assertEqual(gate["status"], "pass", gate)

    def test_repair_batches_chunked_at_25(self):
        # 30 dòng lệch -> bước sửa gom 2 batch (25 + 5), không dịch đơn.
        fake = _FakeJsDropFirstBatch()
        kv = _kv_with_placeholders(30)
        res = _run(kv, fake)
        self.assertEqual(fake.batch_calls, 3)
        self.assertEqual(fake.single_calls, 0)
        self.assertEqual(res["warnings"], [])
        from sdk.gate import check_placeholders
        gate = check_placeholders(kv, res["translated"])
        self.assertEqual(gate["status"], "pass", gate)

    def test_repair_batch_failure_falls_back_to_singles(self):
        # Batch sửa lỗi -> rớt về dịch đơn từng dòng như trước, cổng vẫn PASS.
        fake = _FakeJsRepairBatchFails()
        kv = {"a": "Unlock {VAR1} now",
              "b": "You have {0} points and %s",
              "c": "Pay $VAR and use {foo-bar} here"}
        res = _run(kv, fake)
        self.assertEqual(fake.batch_calls, 2)
        self.assertEqual(fake.single_calls, 3)
        self.assertEqual(res["warnings"], [])
        from sdk.gate import check_placeholders
        gate = check_placeholders(kv, res["translated"])
        self.assertEqual(gate["status"], "pass", gate)


if __name__ == "__main__":
    unittest.main()
