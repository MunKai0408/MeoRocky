// verify_speed_translate_worker.mjs — Kiểm chứng worker speed-translate:
// KV batch đọc/ghi song song + AI concurrency 10, bằng mock (KHÔNG mạng).
//
// Chạy: node exp/verify_speed_translate_worker.mjs (từ thư mục exp/)
// Worker thật (../cloudflare/worker.js) được import và gọi trực tiếp qua
// worker.fetch với request giả (chỉ method/json/headers — không URL) +
// KV giả (Map, delay 20ms/lần đọc-ghi để đo được song song) + AI giả
// (delay 30ms/lần gọi, đếm số lần gọi đồng thời, trả "[vi] " + text).
//
// Khẳng định [FIXTURE — mock, không phải worker live]:
//   1. Batch 25 chuỗi: bản dịch đúng thứ tự, đủ 25.
//   2. AI chạy tối đa 10 đồng thời (không còn 5).
//   3. Đọc KV song song (max get đồng thời > 1), ghi KV song song
//      (max put đồng thời > 1).
//   4. Gọi lại cùng batch: toàn cache, AI không gọi thêm lần nào.
import worker from "../cloudflare/worker.js";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function makeKv(delayMs, counters) {
  const store = new Map();
  return {
    store,
    async get(key) {
      counters.get.cur++;
      counters.get.max = Math.max(counters.get.max, counters.get.cur);
      try {
        await sleep(delayMs);
        const v = store.get(key);
        return v === undefined ? null : v;
      } finally {
        counters.get.cur--;
      }
    },
    async put(key, value) {
      counters.put.cur++;
      counters.put.max = Math.max(counters.put.max, counters.put.cur);
      try {
        await sleep(delayMs);
        store.set(key, value);
      } finally {
        counters.put.cur--;
      }
    },
  };
}

function fakePost(body) {
  return {
    method: "POST",
    headers: { get: () => null },
    async json() { return body; },
  };
}

let failures = 0;
function check(name, cond, extra = "") {
  console.log((cond ? "PASS " : "FAIL ") + name + (extra ? " — " + extra : ""));
  if (!cond) failures++;
}

const texts = Array.from({ length: 25 }, (_, i) => "Market stall number " + i);
const counters = { get: { cur: 0, max: 0 }, put: { cur: 0, max: 0 } };
const cache = makeKv(20, counters);
const usage = makeKv(0, { get: { cur: 0, max: 0 }, put: { cur: 0, max: 0 } });
const ai = { cur: 0, max: 0, calls: 0 };
const env = {
  TRANSLATE_PROVIDER: "workers-ai",
  TRANSLATE_CACHE: cache,
  TRANSLATE_USAGE: usage,
  AI: {
    async run(_model, { text }) {
      ai.cur++;
      ai.max = Math.max(ai.max, ai.cur);
      ai.calls++;
      try {
        await sleep(30);
        return { translated_text: "[vi] " + text };
      } finally {
        ai.cur--;
      }
    },
  },
};

const res1 = await worker.fetch(
  fakePost({ texts, source: "en", target: "vi" }), env, {});
const data1 = await res1.json();
check("batch 25 tra ok", data1.ok === true, JSON.stringify(data1).slice(0, 120));
check("du 25 ban dich dung thu tu",
  Array.isArray(data1.translated) && data1.translated.length === 25 &&
  data1.translated.every((t, i) => t === "[vi] " + texts[i]));
check("provider workers-ai", data1.provider === "workers-ai");
check("AI toi da 10 dong thoi", ai.max === 10, "max=" + ai.max);
check("AI goi dung 25 lan", ai.calls === 25, "calls=" + ai.calls);
check("doc KV song song", counters.get.max > 1, "max get=" + counters.get.max);
check("ghi KV song song", counters.put.max > 1, "max put=" + counters.put.max);

const aiCallsBefore = ai.calls;
const res2 = await worker.fetch(
  fakePost({ texts, source: "en", target: "vi" }), env, {});
const data2 = await res2.json();
check("goi lai toan cache", data2.cached === true);
check("cache khong goi AI them", ai.calls === aiCallsBefore,
  "calls=" + ai.calls);
check("cache tra dung thu tu",
  Array.isArray(data2.translated) &&
  data2.translated.every((t, i) => t === "[vi] " + texts[i]));

console.log(failures === 0
  ? "ALL PASS"
  : failures + " FAIL");
process.exit(failures === 0 ? 0 : 1);
