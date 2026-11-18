/* exp/tm_roundtrip_test.js — Test node cho T3 (TM xuất/nhập/quản lý).
 *
 * Chạy: node exp/tm_roundtrip_test.js [--values <file.json>]
 *   --values: file JSON mảng chuỗi nguồn (vd 200 value đầu của en.json
 *   9K thật) để test round-trip ở quy mô lớn hơn; không có thì dùng
 *   fixture nhỏ có sẵn. Không cần trình duyệt / IndexedDB thật:
 *   TranslateJobStore được giả lập bằng object trong bộ nhớ.
 *
 * Dữ liệu THẬT trong test này [ĐÃ ĐO THẬT]:
 *   5 cặp en→vi dưới đây dịch thật qua Cloudflare Worker
 *   beartea-translate-proxy (Workers AI @cf/meta/m2m100-1.2b),
 *   POST batch 5 chuỗi lúc 2026-11-18 ~05:47 +07: month_used 150→233,
 *   ai_daily_used 2.14755→3.4299. Các cặp còn lại là [FIXTURE].
 */
"use strict";
const fs = require("fs");
const path = require("path");
const TM = require("../tm.js");

let pass = 0, fail = 0;
const failures = [];
function check(name, cond, extra) {
  if (cond) { pass++; console.log("  PASS " + name); }
  else { fail++; failures.push(name); console.log("  FAIL " + name + (extra ? " — " + extra : "")); }
}
function eq(a, b) { return JSON.stringify(a) === JSON.stringify(b); }
function sortedPairs(map) {
  return [...map.entries()].sort((x, y) => (x[0] < y[0] ? -1 : x[0] > y[0] ? 1 : 0));
}

// ---- Job giả lập -------------------------------------------------------
// 5 cặp THẬT qua worker [ĐÃ ĐO THẬT] (xem header). updatedAt mới nhất.
const REAL_9K_JOB = {
  id: "tr-real-9k", name: "en.json", to: "vi", from: "en", status: "done",
  items: [], results: {},
  tm: [
    ["His Winningness", "Chiến thắng của"],
    ["Win one game.", "Chiến thắng 1 game"],
    ["True Royalty", "Hoàng gia đích thực"],
    ["Beat the game on King difficulty.", "Đánh bại trò chơi trên King khó khăn."],
    ["Cheapskate", "giá rẻ"],
  ],
  order: [], total: 5, tmCount: 0, glossaryCount: 0,
  createdAt: 1794510000000, updatedAt: 1794510000000,
};
function makeJobs() {
  return [
    Object.assign({}, REAL_9K_JOB, { tm: REAL_9K_JOB.tm.map((p) => p.slice()) }),
    { // job A cũ hơn: "Cheapskate" dịch khác — job mới hơn phải thắng
      id: "tr-a", name: "en.json", to: "vi", from: "en", status: "done",
      items: [], results: {},
      tm: [
        ["Cheapskate", "Kẻ keo kiệt"],
        ["Play", "Chơi"], ["Quit", "Thoát"], ["Settings", "Cài đặt"],
        ["Victory!", "Chiến thắng!"], ["Defeat", "Thất bại"],
        ["Gold", "Vàng"], ["King", "Vua"], ["Battle", "Trận chiến"],
        ["Shop", "Cửa hàng"],
      ],
      order: [], total: 10, tmCount: 0, glossaryCount: 0,
      createdAt: 1794500000000, updatedAt: 1794500000000,
    },
    { // job B: 2 cặp trùng job A (giữ nguyên), 3 cặp mới
      id: "tr-b", name: "en.json", to: "vi", from: "en", status: "done",
      items: [], results: {},
      tm: [["Play", "Chơi"], ["Gold", "Vàng"],
        ["Castle", "Lâu đài"], ["Dragon", "Rồng"], ["Sword", "Kiếm"]],
      order: [], total: 5, tmCount: 0, glossaryCount: 0,
      createdAt: 1794490000000, updatedAt: 1794490000000,
    },
    { // cặp ngôn ngữ khác — không được lẫn vào en→vi
      id: "tr-de", name: "en.json", to: "de", from: "en", status: "done",
      items: [], results: {},
      tm: [["Play", "Spielen"], ["Quit", "Beenden"], ["Castle", "Schloss"]],
      order: [], total: 3, tmCount: 0, glossaryCount: 0,
      createdAt: 1794490000000, updatedAt: 1794490000000,
    },
    { // job đang dở — không phải TM
      id: "tr-run", name: "en.json", to: "vi", from: "en", status: "running",
      items: [["x", "X"]], results: {}, tm: [["Unfinished", "Chưa xong"]],
      order: [], total: 1, tmCount: 0, glossaryCount: 0,
      createdAt: 1794490000000, updatedAt: 1794490000000,
    },
    { // job cũ KHÔNG có `from` — bị cách ly hoàn toàn
      id: "tr-legacy", name: "en.json", to: "vi", status: "done",
      items: [], results: {},
      tm: [["LegacyOne", "Cũ một"], ["LegacyTwo", "Cũ hai"]],
      order: [], total: 2, tmCount: 0, glossaryCount: 0,
      createdAt: 1794480000000, updatedAt: 1794480000000,
    },
  ];
}
const EXPECTED_EN_VI = new Set([
  "His Winningness", "Win one game.", "True Royalty",
  "Beat the game on King difficulty.", "Cheapskate",
  "Play", "Quit", "Settings", "Victory!", "Defeat",
  "Gold", "King", "Battle", "Shop", "Castle", "Dragon", "Sword",
]);
const N_EN_VI = EXPECTED_EN_VI.size; // 17

async function main() {
  console.log("tm_roundtrip_test (T3):");
  const jobs = makeJobs();
  // Fake TranslateJobStore cho buildTranslateTM (tm.js đọc biến toàn cục).
  globalThis.translateJobStore = { getAll: async () => jobs };

  // 1. Liệt kê theo cặp ngôn ngữ.
  const byLang = TM.tmPairsByLang(jobs);
  check("pairsByLang: en->vi = 17 mục", byLang.some((p) => p.from === "en" && p.to === "vi" && p.count === N_EN_VI),
    JSON.stringify(byLang));
  check("pairsByLang: en->de = 3 mục", byLang.some((p) => p.from === "en" && p.to === "de" && p.count === 3));
  check("pairsByLang: không có cặp legacy (thiếu from)", byLang.length === 2, JSON.stringify(byLang));

  // 2. buildTranslateTM: đúng ngữ nghĩa cũ (job mới thắng, cách ly legacy/running).
  const tm = await TM.buildTranslateTM("vi", "en");
  check("buildTranslateTM: 17 cặp en->vi", tm.size === N_EN_VI, "size=" + tm.size);
  check("buildTranslateTM: cặp worker thật dùng được", tm.get("His Winningness") === "Chiến thắng của");
  check("buildTranslateTM: job mới hơn thắng (Cheapskate -> giá rẻ)", tm.get("Cheapskate") === "giá rẻ",
    JSON.stringify(tm.get("Cheapskate")));
  check("buildTranslateTM: không lẫn de/running/legacy",
    !tm.has("LegacyOne") && !tm.has("Unfinished") && tm.get("Play") === "Chơi");

  // 3. Xuất file: đúng định dạng, đủ mục, xếp theo source.
  const expObj = TM.tmBuildExport(jobs, "en", "vi", "2026-11-18T00:00:00.000Z");
  check("export: format/version/from/to", expObj.format === "meorocky-tm" && expObj.version === 1 &&
    expObj.from === "en" && expObj.to === "vi");
  check("export: count == entries.length == 17", expObj.count === N_EN_VI && expObj.entries.length === N_EN_VI);
  check("export: entries xếp theo source", eq(expObj.entries.map((e) => e.source),
    [...expObj.entries.map((e) => e.source)].sort()));
  check("export: cặp worker thật có trong file",
    expObj.entries.some((e) => e.source === "True Royalty" && e.target === "Hoàng gia đích thực"));
  const expText = JSON.stringify(expObj, null, 2);
  check("export: JSON round-trip qua stringify/parse", eq(JSON.parse(expText), expObj));

  // 4. Parse: file sai bị từ chối (fail-closed cả file).
  const badCases = [
    ["không phải JSON", "{oops"],
    ["sai format", JSON.stringify({ format: "other", version: 1, from: "en", to: "vi", entries: [] })],
    ["sai version", JSON.stringify({ format: "meorocky-tm", version: 2, from: "en", to: "vi", entries: [] })],
    ["thiếu from/to", JSON.stringify({ format: "meorocky-tm", version: 1, entries: [] })],
    ["entries không phải mảng", JSON.stringify({ format: "meorocky-tm", version: 1, from: "en", to: "vi", entries: {} })],
    ["mục target không phải chuỗi", JSON.stringify({ format: "meorocky-tm", version: 1, from: "en", to: "vi", entries: [{ source: "A", target: 3 }] })],
  ];
  for (const [name, text] of badCases) {
    const r = TM.tmParseImportText(text);
    check("parse từ chối: " + name, r.ok === false && typeof r.error === "string");
  }

  // 5. Parse file hợp lệ: đếm trùng trong file + bỏ qua src==target.
  const dupFile = JSON.stringify({
    format: "meorocky-tm", version: 1, from: "en", to: "vi",
    entries: [
      { source: "Play", target: "Chơi" },
      { source: "Play", target: "Chơi khác" },
      { source: "Same", target: "Same" },
      { source: "", target: "Rỗng" },
      { source: "New", target: "Mới" },
    ],
  });
  const dupParsed = TM.tmParseImportText(dupFile);
  check("parse: trùng trong file đếm duplicate=1, giữ bản đầu",
    dupParsed.ok && dupParsed.duplicate === 1 && dupParsed.entries.length === 2 &&
    dupParsed.entries[0].target === "Chơi");
  check("parse: src==target/rỗng đếm skipped=2", dupParsed.ok && dupParsed.skipped === 2);

  // 6. Xoá toàn bộ cặp en→vi rồi nhập lại: khôi phục đúng số mục + nội dung.
  const cleared = TM.tmClearPair(jobs, "en", "vi");
  // clearedPairs đếm mục thô trong các mảng tm của job (5+10+5=20),
  // còn TM dùng lại đã khử trùng chỉ còn 17 — cả hai đều đúng ở tầng
  // của chúng; UI báo số khử trùng (xem refreshTmBox).
  check("clearPair: xoá 20 mục thô en->vi", cleared.clearedPairs === 20, "cleared=" + cleared.clearedPairs);
  check("clearPair: job de giữ nguyên 3 cặp", jobs.find((j) => j.id === "tr-de").tm.length === 3);
  const tmAfterClear = await TM.buildTranslateTM("vi", "en");
  check("sau xoá: TM en->vi rỗng", tmAfterClear.size === 0);
  check("sau xoá: export count 0", TM.tmBuildExport(jobs, "en", "vi").count === 0);
  const parsed = TM.tmParseImportText(expText);
  check("parse file đã xuất: ok, 17 mục, 0 trùng, 0 bỏ qua",
    parsed.ok && parsed.entries.length === N_EN_VI && parsed.duplicate === 0 && parsed.skipped === 0);
  const emptyMap = TM.tmCollectPairs(jobs, "en", "vi");
  const merged = TM.tmMergeImport(emptyMap, parsed.entries);
  check("merge vào TM rỗng: added 17, duplicate 0", merged.added.length === N_EN_VI && merged.duplicate === 0);
  const importJob = TM.tmMakeImportJob("en", "vi", merged.added);
  check("makeImportJob: job done, id tm-import-, tm là mảng cặp",
    importJob.status === "done" && importJob.id.indexOf("tm-import-") === 0 &&
    Array.isArray(importJob.tm) && importJob.tm.length === N_EN_VI && Array.isArray(importJob.tm[0]));
  jobs.push(importJob);
  const tmRestored = await TM.buildTranslateTM("vi", "en");
  check("sau nhập: khôi phục đúng 17 cặp", tmRestored.size === N_EN_VI);
  check("sau nhập: nội dung khớp file xuất (kể cả cặp worker thật)",
    eq(sortedPairs(tmRestored), sortedPairs(tm)));
  check("pairsByLang sau nhập: en->vi lại 17", TM.tmPairsByLang(jobs).some((p) => p.from === "en" && p.to === "vi" && p.count === N_EN_VI));

  // 7. Nhập lại cùng file: không nhân đôi.
  const parsed2 = TM.tmParseImportText(expText);
  const merged2 = TM.tmMergeImport(TM.tmCollectPairs(jobs, "en", "vi"), parsed2.entries);
  check("nhập lại cùng file: added 0, duplicate 17", merged2.added.length === 0 && merged2.duplicate === N_EN_VI);

  // 8. Xoá 1 mục: chỉ mất đúng mục đó.
  const removed = TM.tmRemovePair(jobs, "en", "vi", "Cheapskate");
  check("removePair: xoá đúng 1 cặp Cheapskate", removed.removedPairs === 1);
  const tmAfterDel = await TM.buildTranslateTM("vi", "en");
  check("sau xoá 1 mục: còn 16 cặp, mất Cheapskate",
    tmAfterDel.size === N_EN_VI - 1 && !tmAfterDel.has("Cheapskate") && tmAfterDel.has("Play"));

  // 9. Dịch lại sau khi nhập phải dùng TM đã nhập (đo tmCount).
  const newJob = {
    id: "tr-new", name: "en.json", to: "vi", from: "en", status: "running",
    items: [["k1", "Play"], ["k2", "True Royalty"], ["k3", "Dragon"], ["k4", "Chưa từng có"], ["k5", "Cũng mới"]],
    results: {}, tm: [], order: ["k1", "k2", "k3", "k4", "k5"],
    total: 5, tmCount: 0, glossaryCount: 0,
  };
  TM.applyJobTM(newJob, tmAfterDel);
  check("applyJobTM: tmCount = 3 (Play/True Royalty/Dragon)", newJob.tmCount === 3, "tmCount=" + newJob.tmCount);
  check("applyJobTM: còn 2 key chưa có trong TM", newJob.items.length === 2);
  check("applyJobTM: results ghi đúng bản dịch TM (kể cả cặp worker thật)",
    newJob.results.k1 === "Chơi" && newJob.results.k2 === "Hoàng gia đích thực" && newJob.results.k3 === "Rồng");

  // 10. Quy mô 9K [FIXTURE dịch, value nguồn THẬT nếu --values được truyền]:
  //     job done 200 cặp -> xuất -> xoá -> nhập -> TM dùng lại đủ 200.
  let values = null;
  const vi = process.argv.indexOf("--values");
  if (vi >= 0 && process.argv[vi + 1]) {
    values = JSON.parse(fs.readFileSync(process.argv[vi + 1], "utf-8"));
  }
  if (!values) values = Array.from({ length: 200 }, (_, i) => "Fixture string number " + (i + 1));
  const bigPairs = values.slice(0, 200).map((s) => [s, "VI: " + s]);
  const bigJobs = [{
    id: "tr-big", name: "en.json", to: "vi", from: "en", status: "done",
    items: [], results: {}, tm: bigPairs, order: [], total: bigPairs.length,
    tmCount: 0, glossaryCount: 0, createdAt: 1, updatedAt: 1,
  }];
  const bigExp = TM.tmBuildExport(bigJobs, "en", "vi");
  check("9K-scale: export 200 mục", bigExp.count === 200 && bigExp.entries.length === 200, "count=" + bigExp.count);
  const bigParsed = TM.tmParseImportText(JSON.stringify(bigExp));
  check("9K-scale: parse lại ok, 200 mục", bigParsed.ok && bigParsed.entries.length === 200);
  TM.tmClearPair(bigJobs, "en", "vi");
  check("9K-scale: sau xoá còn 0", TM.tmCollectPairs(bigJobs, "en", "vi").size === 0);
  const bigMap = TM.tmCollectPairs(bigJobs, "en", "vi");
  const bigMerged = TM.tmMergeImport(bigMap, bigParsed.entries);
  check("9K-scale: nhập lại added 200, duplicate 0", bigMerged.added.length === 200 && bigMerged.duplicate === 0);
  bigJobs.push(TM.tmMakeImportJob("en", "vi", bigMerged.added));
  globalThis.translateJobStore = { getAll: async () => bigJobs };
  const bigTm = await TM.buildTranslateTM("vi", "en");
  check("9K-scale: TM sau nhập đủ 200 cặp", bigTm.size === 200);
  const bigJob = {
    id: "tr-big2", name: "en.json", to: "vi", from: "en", status: "running",
    items: values.slice(0, 50).map((s, i) => ["bk" + i, s]),
    results: {}, tm: [], order: [], total: 50, tmCount: 0, glossaryCount: 0,
  };
  TM.applyJobTM(bigJob, bigTm);
  check("9K-scale: dịch lại 50 key dùng TM hết (tmCount 50)", bigJob.tmCount === 50 && bigJob.items.length === 0,
    "tmCount=" + bigJob.tmCount);

  console.log("tm_roundtrip_test: " + pass + " PASS, " + fail + " FAIL" +
    (failures.length ? " — " + failures.join("; ") : ""));
  process.exit(fail ? 1 : 0);
}
main().catch((e) => { console.error("tm_roundtrip_test ERROR: " + (e && e.stack || e)); process.exit(1); });
