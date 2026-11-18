"""Test tăng tốc dịch (feat/translate-speed) — đo bằng fixture/mock, không
gọi Google thật.

- Split-on-failure: batch lỗi do mất marker thì chia đôi đệ quy thay vì
  rớt cả batch về dịch đơn (đo thật: 1 batch 100 chuỗi rớt marker = 52s
  thay vì 2,3s); 429 thì KHÔNG chia (chia cũng lỗi như cũ).
- Batch 25 chuỗi + chạy song song tối đa 3 batch (asyncio.gather theo wave):
  đo live 2026-11-18 [ĐÃ ĐO THẬT] batch 10 chuỗi Workers AI chưa cache mất
  6,3–12,5s nên batch 100 chuỗi vượt timeout 20s của proxy; 3 request x 10
  chuỗi song song xong trong 14,8s so với 25,6s tuần tự (~1,7x).
  Kết quả dịch phải giống hệt chạy tuần tự (cùng provider, cùng thứ tự key).
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
import time
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
        # 30 chuỗi = 2 batch (25+5); batch 25 lỗi marker 1 lần -> chia
        # 12+13, cả 2 nửa đều xong qua batch, KHÔNG chuỗi nào phải dịch đơn.
        # Tổng: 1(lỗi) + 2(chia) + 1(batch 5) = 4 lần gọi batch.
        fake = _FakeJsSplitOnce()
        kv = _kv_plain(30)
        res = _run(kv, fake)
        self.assertEqual(fake.batch_calls, 4)
        self.assertEqual(fake.single_calls, 0)
        self.assertEqual(res["batch_requests"], 3)
        self.assertEqual(res["single_requests"], 0)
        self.assertEqual(res["glossary_count"], 0)
        for key, value in kv.items():
            self.assertEqual(res["translated"][key], "[vi] " + value, key)
        self.assertTrue(res["self_check"]["ok"])

    def test_split_isolates_persistent_bad_string(self):
        # Chỉ chuỗi ZZZBAD làm lỗi marker: batch 25 (chứa nó) chia 12+13,
        # nửa 12 chia tiếp 6+6; nửa 6 chứa ZZZBAD (k000-k005) <= 10 nên
        # không chia nữa mà về dịch đơn 6 chuỗi đó; 24 chuỗi còn lại vẫn
        # qua batch. Batch gọi: 25(lỗi) + 12(lỗi) + 6(lỗi) + 6(ok) + 13(ok)
        # + 5(ok) = 6 lần.
        fake = _FakeJsBadString()
        kv = _kv_plain(30)
        kv["k004"] = "Market stall ZZZBAD {0} number 4"
        res = _run(kv, fake)
        self.assertEqual(fake.batch_calls, 6)
        self.assertEqual(fake.single_calls, 6)
        for key, value in kv.items():
            self.assertEqual(res["translated"][key], "[vi] " + value, key)
        self.assertTrue(res["self_check"]["ok"])

    def test_no_split_on_429_falls_back_to_singles(self):
        # Batch 429 (splitRetryable=False) thì KHÔNG chia — rớt về dịch
        # đơn cả batch như trước (2 batch 25+5, mỗi batch 1 lần gọi).
        fake = _FakeJs429()
        kv = _kv_plain(30)
        res = _run(kv, fake)
        self.assertEqual(fake.batch_calls, 2)
        self.assertEqual(fake.single_calls, 30)
        for key, value in kv.items():
            self.assertEqual(res["translated"][key], "[vi] " + value, key)
        self.assertTrue(res["self_check"]["ok"])

    def test_split_uses_message_fallback_without_flag(self):
        # Lỗi không gắn cờ splitRetryable nhưng message có "thiếu marker"
        # thì vẫn chia (phòng thuộc tính JS không qua được Pyodide).
        # 30 chuỗi = 2 batch (25+5): 1(lỗi) + 2(chia) + 1(batch 5).
        fake = _FakeJsMarkerNoFlag()
        kv = _kv_plain(30)
        res = _run(kv, fake)
        self.assertEqual(fake.batch_calls, 4)
        self.assertEqual(fake.single_calls, 0)
        for key, value in kv.items():
            self.assertEqual(res["translated"][key], "[vi] " + value, key)

    def test_latch_disables_batch_after_two_failures(self):
        # 250 chuỗi = 10 batch x 25, batch nào cũng 429: sau 2 lần
        # lỗi liên tiếp thì latch tắt batch, các batch sau đi thẳng về đơn
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
        # 30 dòng lệch: batch đầu (25) làm rơi token, batch sau (5) giữ
        # token; bước sửa gom 25 dòng lệch thành 1 batch sửa (25) -> xong
        # hết, không cần dịch đơn. Tổng 3 lần gọi batch như trước.
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


class _FakeJsOk(_FakeJsBase):
    """Batch luôn thành công (như proxy/Google khỏe)."""

    async def pb3_google_translate_batch(self, texts, src, dst):
        self.batch_calls += 1
        return _batch_ok(texts)


class _FakeJsSlowParallel(_FakeJsBase):
    """Mỗi lần gọi batch tốn delay_s (mô phỏng chờ worker/AI): đo được
    mức song song thật qua số lần gọi batch đang bay cùng lúc."""

    def __init__(self, delay_s=0.15):
        super().__init__()
        self.delay_s = delay_s
        self.in_flight = 0
        self.max_in_flight = 0
        self.sizes = []

    async def pb3_google_translate_batch(self, texts, src, dst):
        self.batch_calls += 1
        self.sizes.append(len(texts))
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.delay_s)
        finally:
            self.in_flight -= 1
        return _batch_ok(texts)


class ParallelBatchCase(unittest.TestCase):
    def test_250_keys_use_25_string_batches(self):
        # 250 chuỗi = 10 batch x 25 (không còn 3 batch x 100 như trước).
        fake = _FakeJsOk()
        kv = _kv_plain(250)
        res = _run(kv, fake)
        self.assertEqual(res["batches"], 10)
        self.assertEqual(res["batch_requests"], 10)
        self.assertEqual(res["batch_max_strings"], 25)
        self.assertEqual(res["batch_concurrency"], 3)
        self.assertEqual(res["single_requests"], 0)
        for key, value in kv.items():
            self.assertEqual(res["translated"][key], "[vi] " + value, key)
        self.assertTrue(res["self_check"]["ok"])

    def test_three_batches_run_concurrently(self):
        # 250 chuỗi = 10 batch x 25, mỗi lần gọi tốn 0,15s: chạy tuần tự
        # hết 1,5s; chạy song song 3 batch/wave hết ~4 wave x 0,15s.
        # Khẳng định cấu trúc (3 bay cùng lúc) + thời gian < 1,0s.
        fake = _FakeJsSlowParallel(delay_s=0.15)
        kv = _kv_plain(250)
        t0 = time.monotonic()
        res = _run(kv, fake)
        elapsed = time.monotonic() - t0
        self.assertEqual(fake.batch_calls, 10)
        self.assertEqual(fake.max_in_flight, 3)
        self.assertEqual(sorted(fake.sizes), [25] * 10)
        self.assertLess(elapsed, 1.0, "elapsed=%.2fs" % elapsed)
        for key, value in kv.items():
            self.assertEqual(res["translated"][key], "[vi] " + value, key)
        self.assertTrue(res["self_check"]["ok"])

    def test_parallel_results_match_sequential(self):
        # Song song không đổi kết quả: 60 chuỗi (25+25+10), không sleep,
        # so từng key với bản dịch kỳ vọng + cổng placeholder PASS.
        fake = _FakeJsOk()
        kv = _kv_with_placeholders(60)
        res = _run(kv, fake)
        self.assertEqual(res["batches"], 3)
        self.assertEqual(res["batch_requests"], 3)
        self.assertEqual(res["single_requests"], 0)
        self.assertEqual(res["warnings"], [])
        for key, value in kv.items():
            self.assertEqual(res["translated"][key], "[vi] " + value, key)
        from sdk.gate import check_placeholders
        gate = check_placeholders(kv, res["translated"])
        self.assertEqual(gate["status"], "pass", gate)


if __name__ == "__main__":
    unittest.main()
