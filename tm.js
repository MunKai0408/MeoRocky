/* tm.js — Bộ nhớ dịch (TM): xuất / nhập / quản lý (T3).
 *
 * TM bền ĐÃ CÓ từ trước (job dịch đã xong lưu trong IndexedDB qua
 * TranslateJobStore, kb-store.js): mỗi job done mang mảng `tm` là các
 * cặp [chuỗi gốc, bản dịch] thật sự đã dịch đổi (không gồm chuỗi giữ
 * nguyên / bị bỏ qua). File này thêm phần còn thiếu của T3:
 *   (1) xuất TM thành file .json (theo cặp ngôn ngữ),
 *   (2) nhập TM từ file .json (validate + chống nhân đôi),
 *   (3) quản lý TM: xem số mục theo cặp, xoá 1 mục, xoá toàn bộ 1 cặp.
 * TM theo game riêng CHƯA có (chỉ phân theo cặp ngôn ngữ) — follow-up,
 * xem AUTOPILOT.md mục T3.
 *
 * NGUYÊN TẮC GIỮ NGUYÊN (đừng đổi lặng lẽ):
 *  - TM tra theo VALUE GỐC chính xác (không chuẩn hoá khoảng trắng).
 *  - Chỉ dùng job status "done" CÙNG cặp from/to; job cũ KHÔNG có
 *    trường `from` bị cách ly hoàn toàn (không dùng làm TM).
 *  - Chỉ đọc 5 job done mới nhất, tối đa 20.000 cặp; cặp gặp trước
 *    (job mới hơn) thắng — đúng ngữ nghĩa buildTranslateTM cũ.
 *  - Nhãn "bộ nhớ dịch: N chuỗi" ở tab Dịch đếm job.tmCount — KHÔNG
 *    thuộc file này, không sửa.
 *
 * File này là script classic (như kb-store.js) để app.js dùng trực tiếp
 * qua tên toàn cục. Các hàm thuần (không chạm IndexedDB) còn export qua
 * module.exports khi chạy dưới Node, để test node dùng được mà không
 * cần trình duyệt: `node exp/tm_roundtrip_test.js`.
 *
 * ĐỊNH DẠNG FILE TM (.json), version 1:
 *   {"format":"meorocky-tm","version":1,
 *    "from":"en","to":"vi","exported_at":"<ISO 8601>",
 *    "count":N,
 *    "entries":[{"source":"...","target":"..."}, ...]}
 * entries xếp theo source (ổn định, dễ diff); count == entries.length.
 * Nhập: cặp trùng source đã có trong TM thì KHÔNG nhân đôi (đếm
 * duplicate, giữ bản cũ); cặp source==target / rỗng thì bỏ qua
 * (đếm skipped); quá 20.000 mục thì phần thừa đếm skipped.
 */
const TM_FORMAT = "meorocky-tm";
const TM_VERSION = 1;
const TM_MAX_ENTRIES = 20000; // khớp trần buildTranslateTM
const TM_MAX_JOBS_READ = 5; // khớp buildTranslateTM: 5 job done mới nhất
const TM_LIST_LIMIT = 100; // UI chỉ liệt kê tối đa 100 mục / cặp

function tmIsValidPair(src, dst) {
  return typeof src === "string" && typeof dst === "string" &&
    src.length > 0 && dst.length > 0 && src !== dst;
}

/* Thu cặp TM của 1 cặp ngôn ngữ từ job history — đúng ngữ nghĩa
 * buildTranslateTM: job done + cùng from/to + có mảng tm; job mới
 * hơn trước; cặp gặp trước thắng; tối đa TM_MAX_ENTRIES cặp. */
function tmCollectPairs(jobs, from, to) {
  const tm = new Map();
  const done = (jobs || []).filter((j) => j && j.status === "done" &&
    j.to === to && j.from === from && Array.isArray(j.tm));
  done.sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0));
  let pairs = 0;
  for (const j of done.slice(0, TM_MAX_JOBS_READ)) {
    for (const pair of j.tm) {
      if (pairs >= TM_MAX_ENTRIES) break;
      const src = pair && pair[0], dst = pair && pair[1];
      if (tmIsValidPair(src, dst)) {
        if (!tm.has(src)) { tm.set(src, dst); pairs++; }
      }
    }
    if (pairs >= TM_MAX_ENTRIES) break;
  }
  return tm;
}

/* Số mục TM theo từng cặp ngôn ngữ (job không có `from` bị cách ly,
 * không hiện). Trả [{from, to, count}] xếp theo count giảm dần. */
function tmPairsByLang(jobs) {
  const keys = new Set();
  for (const j of (jobs || [])) {
    if (j && j.status === "done" && typeof j.from === "string" &&
        typeof j.to === "string" && j.from && j.to &&
        Array.isArray(j.tm)) {
      keys.add(j.from + ">" + j.to);
    }
  }
  const out = [];
  for (const k of keys) {
    const i = k.indexOf(">");
    const from = k.slice(0, i), to = k.slice(i + 1);
    const count = tmCollectPairs(jobs, from, to).size;
    if (count > 0) out.push({ from, to, count }); // cặp đã xoá sạch (0 mục) thì ẩn
  }
  out.sort((a, b) => (b.count - a.count) ||
    (a.from < b.from ? -1 : a.from > b.from ? 1 : 0) ||
    (a.to < b.to ? -1 : a.to > b.to ? 1 : 0));
  return out;
}

/* Dựng object file xuất cho 1 cặp ngôn ngữ. entries xếp theo source. */
function tmBuildExport(jobs, from, to, exportedAt) {
  const tm = tmCollectPairs(jobs, from, to);
  const entries = [...tm.entries()]
    .map(([source, target]) => ({ source, target }))
    .sort((a, b) => (a.source < b.source ? -1 : a.source > b.source ? 1 : 0));
  return {
    format: TM_FORMAT,
    version: TM_VERSION,
    from, to,
    exported_at: exportedAt || new Date().toISOString(),
    count: entries.length,
    entries,
  };
}

/* Validate + parse text file TM nhập. Trả:
 *   {ok:true, from, to, entries, duplicate, skipped}
 *   {ok:false, error}
 * entries: cặp hợp lệ, duy nhất theo source (gặp trước thắng),
 * tối đa TM_MAX_ENTRIES. duplicate: source lặp trong file.
 * skipped: source==target / rỗng / thừa trần 20.000.
 * Lỗi định dạng (JSON hỏng, sai format/version, thiếu from/to,
 * entries không phải mảng, mục không phải {source,target} chuỗi)
 * thì fail-closed cả file — không nhập nửa vời. */
function tmParseImportText(text) {
  let obj;
  try { obj = JSON.parse(text); } catch (e) {
    return { ok: false, error: "File không phải JSON hợp lệ: " + e.message };
  }
  if (!obj || typeof obj !== "object" || Array.isArray(obj)) {
    return { ok: false, error: "File TM phải là 1 object JSON." };
  }
  if (obj.format !== TM_FORMAT) {
    return { ok: false, error: "File không phải bộ nhớ dịch MeoRocky (thiếu \"format\": \"meorocky-tm\")." };
  }
  if (obj.version !== TM_VERSION) {
    return { ok: false, error: "Phiên bản file TM không hỗ trợ (cần version 1, gặp " + JSON.stringify(obj.version) + ")." };
  }
  if (typeof obj.from !== "string" || !obj.from ||
      typeof obj.to !== "string" || !obj.to) {
    return { ok: false, error: "File TM thiếu cặp ngôn ngữ (\"from\"/\"to\")." };
  }
  if (!Array.isArray(obj.entries)) {
    return { ok: false, error: "File TM thiếu danh sách mục (\"entries\" phải là mảng)." };
  }
  const entries = [];
  const seen = new Set();
  let duplicate = 0, skipped = 0;
  for (const e of obj.entries) {
    if (!e || typeof e !== "object" || Array.isArray(e) ||
        typeof e.source !== "string" || typeof e.target !== "string") {
      return { ok: false, error: "Mục TM không đúng định dạng (cần {\"source\": \"...\", \"target\": \"...\"} chuỗi)." };
    }
    if (!tmIsValidPair(e.source, e.target)) { skipped++; continue; }
    if (seen.has(e.source)) { duplicate++; continue; }
    seen.add(e.source);
    if (entries.length >= TM_MAX_ENTRIES) { skipped++; continue; }
    entries.push({ source: e.source, target: e.target });
  }
  return { ok: true, from: obj.from, to: obj.to, entries, duplicate, skipped };
}

/* Gộp mục đã parse vào TM hiện có (Map source->target): cặp trùng
 * source đã có thì KHÔNG nhân đôi, giữ bản cũ (đếm duplicate).
 * Trả {added:[{source,target}], duplicate, skipped} — skipped ở đây
 * luôn 0 (parse đã lọc), giữ trường cho đối xứng báo cáo. */
function tmMergeImport(existingMap, importedEntries) {
  const added = [];
  let duplicate = 0;
  for (const e of (importedEntries || [])) {
    if (existingMap.has(e.source)) { duplicate++; continue; }
    existingMap.set(e.source, e.target);
    added.push({ source: e.source, target: e.target });
  }
  return { added, duplicate, skipped: 0 };
}

/* Job tổng hợp để cất các cặp TM vừa nhập vào IndexedDB, sao cho
 * buildTranslateTM đọc được ngay (job done, cùng from/to, mảng tm).
 * results rỗng: TM tra theo value gốc, không theo key. updatedAt =
 * lúc nhập nên job nhập luôn nằm trong 5 job done mới nhất. */
function tmMakeImportJob(from, to, pairs, now) {
  const at = (typeof now === "number" && now) || Date.now();
  return {
    id: "tm-import-" + at.toString(36) + "-" +
      Math.floor(Math.random() * 1e6).toString(36),
    name: "TM nhập từ file",
    to, from, status: "done",
    items: [], results: {},
    tm: (pairs || []).map((p) => [p.source, p.target]),
    order: [], total: 0, tmCount: 0, glossaryCount: 0,
    createdAt: at, updatedAt: at,
  };
}

/* Xoá 1 mục TM (theo value gốc chính xác) khỏi mọi job done của cặp
 * ngôn ngữ. Trả {jobs: [...job đã đổi...], removedPairs}. Caller lưu
 * lại các job trả về qua TranslateJobStore.save.
 * removedPairs đếm mục thô trong các mảng tm (1 source có thể nằm ở
 * nhiều job); số mục TM khử trùng mà UI hiện thì đo bằng
 * tmCollectPairs(...).size, không phải số này. */
function tmRemovePair(jobs, from, to, source) {
  const touched = [];
  let removedPairs = 0;
  for (const j of (jobs || [])) {
    if (!j || j.status !== "done" || j.to !== to || j.from !== from ||
        !Array.isArray(j.tm)) continue;
    const before = j.tm.length;
    j.tm = j.tm.filter((p) => !(p && p[0] === source));
    if (j.tm.length !== before) {
      removedPairs += before - j.tm.length;
      touched.push(j);
    }
  }
  return { jobs: touched, removedPairs };
}

/* Xoá toàn bộ TM của 1 cặp ngôn ngữ (chỉ dọn mảng tm của các job
 * done; job + results + lịch sử dịch giữ nguyên). Trả
 * {jobs: [...job đã đổi...], clearedPairs} — clearedPairs đếm mục thô
 * trong các mảng tm; số mục TM khử trùng đo bằng tmCollectPairs. */
function tmClearPair(jobs, from, to) {
  const touched = [];
  let clearedPairs = 0;
  for (const j of (jobs || [])) {
    if (!j || j.status !== "done" || j.to !== to || j.from !== from ||
        !Array.isArray(j.tm) || !j.tm.length) continue;
    clearedPairs += j.tm.length;
    j.tm = [];
    touched.push(j);
  }
  return { jobs: touched, clearedPairs };
}

// ---- TM dùng lúc dịch (chuyển nguyên từ app.js — giữ đúng ngữ nghĩa) ----
// TM (bộ nhớ dịch): value giống hệt trong các job ĐÃ XONG cùng ngôn ngữ
// đích -> dùng luôn, khỏi gửi Google. KB game-profile KHÔNG chứa bản dịch
// nên không dùng làm TM (đã kiểm tra sdk/kb.py — KB chỉ lưu hồ sơ game).
// TM tra theo VALUE GỐC chính xác (không chuẩn hoá khoảng trắng — khớp
// chính xác mới dùng, tránh dịch sai ngữ cảnh).
// Mục A (an toàn ngôn ngữ nguồn): TM chỉ dùng job có cùng `from`
// (ngôn ngữ nguồn). Job cũ KHÔNG có trường `from` bị cách ly hoàn toàn
// (không dùng làm TM) — vì TM đó có thể chứa cặp Đức/Nga đã bị dịch sai
// khi bridge còn hard-code nguồn "en" (Joseph 21:24).
// (typeof-guard: dưới Node test chưa có biến toàn cục translateJobStore.)
async function buildTranslateTM(to, from) {
  const tm = new Map();
  if (typeof translateJobStore === "undefined" || !translateJobStore) return tm;
  try {
    const jobs = await translateJobStore.getAll();
    const done = jobs.filter((j) => j && j.status === "done" && j.to === to &&
      j.from === from && Array.isArray(j.tm));
    done.sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0));
    let pairs = 0;
    for (const j of done.slice(0, 5)) {
      for (const pair of j.tm) {
        if (pairs >= 20000) break;
        const src = pair && pair[0], dst = pair && pair[1];
        if (typeof src === "string" && typeof dst === "string" && src && dst) {
          if (!tm.has(src)) tm.set(src, dst);
          pairs++;
        }
      }
      if (pairs >= 20000) break;
    }
  } catch (e) { /* IndexedDB lỗi: bỏ qua TM, dịch như thường */ }
  return tm;
}
// Dùng TM cho các key còn lại trong job (value khớp -> ghi results luôn).
function applyJobTM(job, tm) {
  if (!job.items.length || tm.size === 0) return;
  const rest = [];
  for (const pair of job.items) {
    const hit = tm.get(pair[1]);
    if (hit != null) {
      job.results[pair[0]] = hit;
      job.tmCount++;
    } else rest.push(pair);
  }
  job.items = rest;
}

if (typeof window !== "undefined") {
  window.TM_FORMAT = TM_FORMAT;
  window.TM_VERSION = TM_VERSION;
  window.TM_MAX_ENTRIES = TM_MAX_ENTRIES;
  window.TM_MAX_JOBS_READ = TM_MAX_JOBS_READ;
  window.TM_LIST_LIMIT = TM_LIST_LIMIT;
  window.tmIsValidPair = tmIsValidPair;
  window.tmCollectPairs = tmCollectPairs;
  window.tmPairsByLang = tmPairsByLang;
  window.tmBuildExport = tmBuildExport;
  window.tmParseImportText = tmParseImportText;
  window.tmMergeImport = tmMergeImport;
  window.tmMakeImportJob = tmMakeImportJob;
  window.tmRemovePair = tmRemovePair;
  window.tmClearPair = tmClearPair;
  window.buildTranslateTM = buildTranslateTM;
  window.applyJobTM = applyJobTM;
}
if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    TM_FORMAT, TM_VERSION, TM_MAX_ENTRIES, TM_MAX_JOBS_READ, TM_LIST_LIMIT,
    tmIsValidPair, tmCollectPairs, tmPairsByLang, tmBuildExport,
    tmParseImportText, tmMergeImport, tmMakeImportJob, tmRemovePair,
    tmClearPair, buildTranslateTM, applyJobTM,
  };
}
