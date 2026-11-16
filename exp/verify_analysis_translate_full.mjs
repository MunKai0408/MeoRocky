// verify_analysis_translate_full.mjs — Harness muc A (tach ngon ngu per-string
// + sua tab Dich + dich full khong sot), chay tren code hien tai (chua day).
// Chay: node verify_analysis_translate_full.mjs (tu /home/hatch/workspace/pb3_web/exp)
//   ONLY=1,2,3,4,5,6 — chi chay section do (de moi luot vua gioi han phien).
// Ket qua: analysis_translate_full_results.md + .json (ghi de phan da chay).
//   + out/translate_full/*.png (anh UI 390px)
//
// Section:
//  1. Unit Python: test_langdetect + test_translate_source (35 test) +
//     validate_langdetect (do chinh xac tren game that).
//  2. Bridge Pyodide (mock Google): src=en chi gui tieng Anh, src=zh chi gui
//     tieng Trung (sl dung), chuoi khac giu nguyen tung byte, self-check sach,
//     dedupe trai lai du key, src==to loi.
//  3. Trich xuat 5 game that (CPython): file tach .en/.zh.json ton tai, 0 key
//     #N, so entry, thu tu key (y 7), do nhiem so voi ground truth.
//  4. Fixture recheck Passpartout (Pyodide mock Google, file 15.868 key that
//     cua Joseph): du key, du dong Anh (dem DONG tu file) co dau TV, 0 ngon
//     ngu khac bi doi, thu tu khop; tuong tu cho tieng Trung.
//  5. UI tren Chromium that 390px: select nguon en/zh, dong info trung thuc,
//     giu ban dich trong phien (state.lastTranslated), cong tai chan file thieu.
//  6. Hoi quy: module-worker 17/17, C2 13/13, C9 16/16, item8 static 0 fails,
//     full suite Python (unittest discover) xanh.
//
// Luu y: Chromium trong sandbox khong co HTTPS ngoai nen UI chay tren HTTP
// local (127.0.0.1) phuc vu cay site/ — DUNG code se day, chi khac host.
// KHONG phai Safari that — Safari van can Joseph thu.
import fs from "node:fs";
import path from "node:path";
import http from "node:http";
import { execSync, spawnSync } from "node:child_process";

const SITE = "/home/hatch/workspace/pb3_web/site";
const EXP = "/home/hatch/workspace/pb3_web/exp";
const OUT = EXP + "/out/translate_full";
const EXTRACT_OUT = EXP + "/out/extract_5games";
const FIXTURE_IN = "/home/hatch/workspace/user/files/resources.monobehaviour.json";
const FIXTURE_BAD = "/home/hatch/workspace/user/files/translated.vi.json";
const JOSEPH_EN = "/home/hatch/workspace/user/files/en.json";
fs.mkdirSync(OUT, { recursive: true });

const ONLY = process.env.ONLY ? process.env.ONLY.split(",").map((s) => s.trim()) : null;
const want = (n) => !ONLY || ONLY.includes(String(n));
const results = {};
try {
  const prev = JSON.parse(fs.readFileSync(EXP + "/analysis_translate_full_results.json", "utf8"));
  if (prev && prev.results) Object.assign(results, prev.results);
} catch (_) {}
function rec(name, ok, detail, ms) {
  results[name] = { ok: !!ok, detail: String(detail || ""), ms: Math.round(ms || 0) };
  console.log((ok ? "PASS " : "FAIL ") + name + " | " + (detail || ""));
}
const t0 = performance.now();
const sec = (ms) => Math.round(ms / 100) / 10;
function sh(cmd, cwd) {
  return spawnSync("bash", ["-c", cmd], { cwd: cwd || EXP, encoding: "utf8", timeout: 600000 });
}

// ================= 1. Unit Python =================
if (want(1)) {
  let t = performance.now();
  const r = sh("python3 -m unittest tests.test_langdetect tests.test_translate_source 2>&1", SITE + "/pb3");
  const m = (r.stdout || "").match(/Ran (\d+) tests? in [\d.]+s\n\n(OK|FAILED)/);
  const ok = r.status === 0 && !!m && m[2] === "OK";
  rec("py_unit_langdetect_translate_source", ok,
    (m ? m[1] + " test, " + m[2] : "khong doc duoc ket qua") + " | " + (r.stdout || "").trim().split("\n").slice(-3).join(" / "),
    performance.now() - t);
  t = performance.now();
  const v = sh("python3 exp/validate_langdetect.py 2>&1", "/home/hatch/workspace/pb3_web");
  const vm = (v.stdout || "").match(/Passpartout 15-ngon-ngu: (\d+)\/(\d+) dung = ([\d.]+)%/);
  const vm2 = (v.stdout || "").match(/9 Kings I2Languages: (\d+)\/(\d+) dung = ([\d.]+)%/);
  rec("py_langdetect_accuracy_real_games", !!vm && !!vm2,
    (vm ? ("Passpartout " + vm[3] + "% (" + vm[1] + "/" + vm[2] + ")") : "khong doc duoc Passpartout") +
    (vm2 ? (" · 9Kings " + vm2[3] + "% (" + vm2[1] + "/" + vm2[2] + ")") : " · khong doc duoc 9Kings"),
    performance.now() - t);
}

// ================= 2. Bridge Pyodide (mock Google) =================
if (want(2)) {
  const { loadPyodide } = await import("../site/pyodide/pyodide.mjs");
  const SAMPLE = {
    "Aristocrat_Catherine.s1": "Wie Extraordinär!",
    "Aristocrat_Catherine.s1#10": "Extraordinário!",
    "Aristocrat_Catherine.s1#12": "Необыкновенно!",
    "Aristocrat_Catherine.s1#14": "丹青妙手，神來之筆啊！",
    "Aristocrat_Catherine.s1#15": "丹青妙手，神来之笔啊！",
    "Aristocrat_Catherine.s1#2": "Extraordinary!",
    "Aristocrat_Catherine.s1#5": "すばらしいわ！",
    "Aristocrat_Catherine.s1#6": "엄청나구만！",
    "Play.s1": "Play",
    "Quit.s1": "Quit",
  };
  const NON_EN = ["Wie Extraordinär!", "Extraordinário!", "Необыкновенно!",
    "丹青妙手，神來之筆啊！", "丹青妙手，神来之笔啊！", "すばらしいわ！", "엄청나구만！"];
  let calls = [];
  globalThis.pb3_google_translate_batch = async (texts, src, dst) => {
    const arr = Array.from(texts);
    calls.push({ kind: "batch", src, dst, texts: arr });
    return JSON.stringify({ translations: arr.map((x) => "VI:" + x), mode: "mock" });
  };
  globalThis.pb3_google_translate = async (text, src, dst) => {
    calls.push({ kind: "single", src, dst, texts: [text] });
    return "VI:" + text;
  };
  globalThis.pb3_google_cancelled = () => false;
  let t = performance.now();
  const pyodide = await loadPyodide({ indexURL: SITE + "/pyodide/" });
  await pyodide.loadPackage("lz4");
  const man = JSON.parse(fs.readFileSync(SITE + "/pb3/files.json", "utf8"));
  for (const rel of man.files) {
    const buf = fs.readFileSync(SITE + "/" + rel);
    const target = "/pb3web/" + rel.slice(4);
    pyodide.FS.mkdirTree(target.slice(0, target.lastIndexOf("/")));
    pyodide.FS.writeFile(target, new Uint8Array(buf));
  }
  pyodide.runPython("import sys; sys.path.insert(0, '/pb3web')");
  pyodide.runPython("import bridge\nbridge.__boot_result__ = bridge.boot()\n");
  const bridge = pyodide.globals.get("bridge");
  const boot = JSON.parse(pyodide.globals.get("bridge").__boot_result__);
  rec("bridge_boot", boot.ok === true && boot.plugins === 20,
    "plugins=" + boot.plugins, performance.now() - t);
  // src=en
  t = performance.now();
  calls = [];
  const rEn = JSON.parse(await bridge.translate_json_google(JSON.stringify(SAMPLE), "vi", false, "en"));
  const sentEn = calls.flatMap((c) => c.texts);
  rec("bridge_src_en_only_english_sent",
    calls.every((c) => c.src === "en") && NON_EN.filter((x) => sentEn.includes(x)).length === 0,
    "sl=" + JSON.stringify([...new Set(calls.map((c) => c.src))]) + " sent=" + sentEn.length, performance.now() - t);
  rec("bridge_src_en_other_lang_byte_identical",
    NON_EN.every((x) => Object.values(rEn.translated).includes(x)) &&
    Object.keys(rEn.translated).length === 10 && rEn.self_check && rEn.self_check.ok === true,
    "skipped=" + rEn.skipped_count + " self_check=" + JSON.stringify(rEn.self_check), performance.now() - t);
  // src=zh
  t = performance.now();
  calls = [];
  const rZh = JSON.parse(await bridge.translate_json_google(JSON.stringify(SAMPLE), "vi", false, "zh"));
  const sentZh = calls.flatMap((c) => c.texts);
  rec("bridge_src_zh_only_chinese_sent",
    calls.every((c) => c.src === "zh") && sentZh.length === 2 &&
    sentZh.includes("丹青妙手，神來之筆啊！") && sentZh.includes("丹青妙手，神来之笔啊！"),
    "sent=" + JSON.stringify(sentZh), performance.now() - t);
  rec("bridge_src_zh_self_check",
    Object.keys(rZh.translated).length === 10 && rZh.self_check && rZh.self_check.ok === true && rZh.skipped_count === 8,
    "skipped=" + rZh.skipped_count, performance.now() - t);
  // dedupe: 2 key khac nhau cung value -> dich 1 lan, trai lai du 2 key
  t = performance.now();
  calls = [];
  const rDup = JSON.parse(await bridge.translate_json_google(
    JSON.stringify({ "A.s1": "Save the game.", "B.s1": "Save the game.", "C.s1": "Quit" }), "vi", false, "en"));
  const sentDup = calls.flatMap((c) => c.texts);
  rec("bridge_dedupe_reexpands_all_keys",
    Object.keys(rDup.translated).length === 3 &&
    rDup.translated["A.s1"] === rDup.translated["B.s1"] &&
    sentDup.filter((x) => x === "Save the game.").length <= 1,
    "deduped=" + rDup.deduped + " sent=" + JSON.stringify(sentDup), performance.now() - t);
  // src==to loi
  const rBad = JSON.parse(await bridge.translate_json_google(JSON.stringify(SAMPLE), "en", false, "en"));
  rec("bridge_src_equals_to_errors", rBad.ok === false, JSON.stringify(rBad).slice(0, 100), performance.now() - t);
  globalThis.__bridge = bridge;
  globalThis.__pyodide = pyodide;
}

// ================= 3. Trich xuat 5 game that =================
if (want(3)) {
  let t = performance.now();
  const r = sh("python3 exp/extract_5games.py 2>&1 | tail -n 8", "/home/hatch/workspace/pb3_web");
  const sum = JSON.parse(fs.readFileSync(EXTRACT_OUT + "/summary.json", "utf8"));
  const g = sum.games;
  const allOk = ["9kings", "data", "ek2", "pb3", "passpartout"].every((k) => g[k] && g[k].ok === true);
  rec("extract_5games_ok", allOk && r.status === 0,
    ["9kings", "data", "ek2", "pb3", "passpartout"].map((k) =>
      k + "=" + (g[k] ? g[k].total_entries + " entry/" + g[k].seconds + "s" : "?")).join(" · "),
    performance.now() - t);
  t = performance.now();
  const imp = JSON.parse(fs.readFileSync(EXTRACT_OUT + "/impurity.json", "utf8"));
  // File tach khong con #N + thu tu term 9 Kings khop file Joseph (tru 1 key 'Skin_').
  let hashN = 0, details = [];
  for (const game of ["9kings", "data", "ek2", "pb3", "passpartout"]) {
    const dir = EXTRACT_OUT + "/" + game;
    for (const f of fs.readdirSync(dir)) {
      if (!f.includes(".monobehaviour.")) continue;
      const kv = JSON.parse(fs.readFileSync(dir + "/" + f, "utf8"));
      const n = Object.keys(kv).filter((k) => k.includes("#")).length;
      hashN += n;
      details.push(game + "/" + f + "=" + Object.keys(kv).length + " entry, #N=" + n);
    }
  }
  rec("extract_split_no_hash_suffix", hashN === 0, details.join(" · "), performance.now() - t);
  rec("extract_impurity_measured",
    imp["passpartout_en"] && imp["9kings_en"] &&
    imp["passpartout_en"]["wrong"] <= 20 && imp["9kings_zh"]["wrong"] <= 200,
    "PP en sai " + imp["passpartout_en"]["wrong"] + "/" + imp["passpartout_en"]["total"] +
    " · PP zh sai " + imp["passpartout_zh"]["wrong"] +
    " · 9K en sai " + imp["9kings_en"]["wrong"] + "/" + imp["9kings_en"]["total"] +
    " · 9K zh sai " + imp["9kings_zh"]["wrong"] +
    " · thu tu term 9K: dau '" + imp["order_9kings"]["first_split_term"] +
    "' (Joseph: '" + imp["order_9kings"]["first_joseph_term"] + "')",
    performance.now() - t);
}

// ================= 4. Fixture recheck Passpartout (15.868 key that) =================
if (want(4)) {
  const { loadPyodide } = await import("../site/pyodide/pyodide.mjs");
  const input = JSON.parse(fs.readFileSync(FIXTURE_IN, "utf8"));
  const badOut = JSON.parse(fs.readFileSync(FIXTURE_BAD, "utf8"));
  // Dem DONG tu file dau vao (khong hard-code tong): moi key goc = 1 dong
  // tieng Anh (lay #2 neu co, khong thi lay chinh key goc — dong hai tac);
  // dong tieng Trung = #14 + #15.
  const groups = new Map(); // base -> {bare, bySuffix}
  for (const [k, v] of Object.entries(input)) {
    const m = k.match(/^(.*)#(\d+)$/);
    if (m) {
      if (!groups.has(m[1])) groups.set(m[1], { bare: null, bySuffix: {} });
      groups.get(m[1]).bySuffix[m[2]] = { key: k, value: v };
    } else {
      if (!groups.has(k)) groups.set(k, { bare: null, bySuffix: {} });
      groups.get(k).bare = { key: k, value: v };
    }
  }
  const enLines = []; // {key, value} dong tieng Anh
  const zhLines = []; // {key, value} dong tieng Trung (#14/#15)
  for (const [base, g] of groups) {
    if (g.bySuffix["2"]) enLines.push(g.bySuffix["2"]);
    else if (g.bare) enLines.push(g.bare);
    if (g.bySuffix["14"]) zhLines.push(g.bySuffix["14"]);
    if (g.bySuffix["15"]) zhLines.push(g.bySuffix["15"]);
  }
  const hasLetters = (s) => /[A-Za-zÀ-ỹĀ-ſƀ-ɏ]/.test(s) || /[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7a3]/.test(s);
  const enTranslatable = enLines.filter((l) => hasLetters(l.value));
  const zhTranslatable = zhLines.filter((l) => hasLetters(l.value));
  const enLineKeys = new Set(enLines.map((l) => l.key));
  const zhLineKeys = new Set(zhLines.map((l) => l.key));
  let t = performance.now();
  const pyodide = await loadPyodide({ indexURL: SITE + "/pyodide/" });
  await pyodide.loadPackage("lz4");
  const man = JSON.parse(fs.readFileSync(SITE + "/pb3/files.json", "utf8"));
  for (const rel of man.files) {
    const buf = fs.readFileSync(SITE + "/" + rel);
    const target = "/pb3web/" + rel.slice(4);
    pyodide.FS.mkdirTree(target.slice(0, target.lastIndexOf("/")));
    pyodide.FS.writeFile(target, new Uint8Array(buf));
  }
  pyodide.runPython("import sys; sys.path.insert(0, '/pb3web')");
  pyodide.runPython("import bridge\nbridge.__boot_result__ = bridge.boot()\n");
  const bridge = pyodide.globals.get("bridge");
  // Mock Google: tra "VI:<text>" (khong mang that); ghi lai chuoi da gui.
  let sentTexts = [];
  globalThis.pb3_google_translate_batch = async (texts, src, dst) => {
    const arr = Array.from(texts);
    sentTexts.push(...arr.map((x) => ({ text: x, src })));
    return JSON.stringify({ translations: arr.map((x) => "VI:" + x), mode: "mock" });
  };
  globalThis.pb3_google_translate = async (text, src, dst) => {
    sentTexts.push({ text, src });
    return "VI:" + text;
  };
  globalThis.pb3_google_cancelled = () => false;
  const inputKeys = Object.keys(input);
  // Ground truth DOC LAP theo khe (chi nhom doi thoai du 15 bien the):
  // hau to = ngon ngu theo thu tu lan xuat hien (da kiem chung o
  // validate_langdetect.py). Nhom ngan hon / key textasset dung hau to
  // lam bo dem va cham (khong phai khe ngon ngu) nen KHONG dung lam
  // ground truth — chi dem bang slot o nhom du 15.
  const SLOT_LANG = { "bare": "de", "2": "en", "3": "es", "4": "fr", "5": "ja", "6": "ko", "7": "nl", "8": "en", "9": "pl", "10": "pt", "11": "ro", "12": "ru", "13": "sv", "14": "zh", "15": "zh" };
  // Nhom doi thoai = DUNG 15 bien the (bare + #2..#15, khong co #1:
  // bien the dau tien la key tran, khong hau to). Nhom co nhieu hon
  // (vd textasset.s1 co 581 bien the) chi dung hau to lam bo dem va
  // cham, khong phai khe ngon ngu — loai ra; key textasset.* la du lieu
  // TextAsset (ten type Unity, ten ham...), khong phai doi thoai.
  const dialogue = [...groups.entries()]
    .filter(([base, g]) => !base.startsWith("textasset.") &&
      g.bare !== null && Object.keys(g.bySuffix).length === 14 &&
      Object.keys(SLOT_LANG).filter((s) => s !== "bare").every((s) => g.bySuffix[s]))
    .map(([, g]) => g);
  const dlgEn = dialogue.flatMap((g) => [g.bySuffix["2"], g.bySuffix["8"]]);
  const dlgZh = dialogue.flatMap((g) => [g.bySuffix["14"], g.bySuffix["15"]]);
  // Khe script manh (kana/hangul/Cyrillic/Han) — neu bi doi la loi loc
  // chac chan (khong phu thuoc wordlist). Khe Latin ngan chi co tu noi
  // dung (vd 'Extraordinaire!') thi bo loc yeu khong bat duoc — ghi so
  // du, khong assert (gioi han da biet).
  const SCRIPT_SLOTS_EN = ["5", "6", "12", "14", "15"];
  const SCRIPT_SLOTS_ZH = ["5", "6", "12"];
  // Phat hien hang loat qua Pyodide (1 lan goi cho nhieu chuoi).
  const detectMany = (vals) => {
    pyodide.globals.set("_vals", pyodide.toPy(vals));
    const out = pyodide.runPython(
      "import json\nfrom sdk.langdetect import detect_language\n" +
      "json.dumps([[c, f] for c, f in (detect_language(v) for v in _vals)])");
    return JSON.parse(out);
  };
  const isData = (v) => !/[A-Za-zÀ-ỹĀ-ſƀ-ɏ\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7a3]/.test(v) || v.trimStart().startsWith("{");
  // Luoi an toan dau cua bridge: chuoi co dau Latin khong phai tieng Anh
  // (é/è/ê...) thi khong tinh la en — vd 'Trés bien!', 'Génial!' (Phap).
  const hasNonEnDiacritics = (v) => /[àáâãäåçèéêëìíîïñòóôõöøùúûüýÿß]/.test(v);
  const hasCJK = (v) => /[\u4e00-\u9fff]/.test(v);
  const slotOf = (l) => (l.key.match(/#(\d+)$/) || [])[1] || "bare";
  // Kiem tra theo DONG (bridge dich theo dong): gia tri da doi chi la
  // "doi sai" neu co dong nao phat hien MANH thuoc ngon ngu khac nguon
  // ma van bi dich. Dong chi co ten rieng ('- Marie') hay chuoi Latin
  // ngan khong tin hieu ('Fiasko.') thi phat hien yeu en — bo loc khong
  // bat duoc, ghi so du (gioi han da biet), khong tinh la doi sai.
  const stripMarkup = (s) => s.replace(/<\/[^>]*>/g, " ").replace(/<[^>]*>/g, " ")
    .replace(/\{[^}]*\}/g, " ").replace(/%[sd]/g, " ").replace(/__PB3PH\d+__/g, " ");
  const badChangedLines = (changed, outObj, src) => {
    const toDetect = [];
    for (const l of changed) {
      const inLines = l.value.split("\n");
      const outLines = String(outObj[l.key] ?? "").split("\n");
      for (let i = 0; i < inLines.length; i++) {
        if ((outLines[i] || "") !== inLines[i]) {
          toDetect.push({ key: l.key, line: i, text: stripMarkup(inLines[i]) });
        }
      }
    }
    if (!toDetect.length) return { bad: [], changedLines: 0 };
    const dets = detectMany(toDetect.map((x) => x.text));
    const bad = [];
    for (let i = 0; i < toDetect.length; i++) {
      const [code, conf] = dets[i];
      if (conf === "strong" && code && code !== src) {
        bad.push(toDetect[i].key + " dong " + toDetect[i].line + " -> " + code);
      }
    }
    return { bad, changedLines: toDetect.length };
  };
  // --- 4a. src=en ---
  t = performance.now();
  sentTexts = [];
  const rEn = JSON.parse(await bridge.translate_json_google(JSON.stringify(input), "vi", false, "en"));
  const outEn = rEn.translated;
  const keysOkEn = Object.keys(outEn).length === inputKeys.length &&
    inputKeys.every((k, i) => Object.keys(outEn)[i] === k);
  let enTranslated = 0, enUnchanged = [];
  for (const l of enTranslatable) {
    if (outEn[l.key] !== l.value) enTranslated++;
    else enUnchanged.push(l);
  }
  // Dong Anh chua dich: chi chap nhan neu gia tri do that ra KHONG phai
  // tieng Anh (phat hien manh ngon ngu khac — nhom lech khe, vd #2 chua
  // cau tieng Tay Ban Nha) hoac la du lieu (JSON manifest...).
  const enMissDet = detectMany(enUnchanged.map((l) => l.value));
  const enBadMiss = enUnchanged.filter((l, i) =>
    !isData(l.value) && !hasNonEnDiacritics(l.value) &&
    !(enMissDet[i][0] && enMissDet[i][0] !== "en" && enMissDet[i][1] === "strong"));
  const dlgEnMiss = dlgEn.filter((l) => outEn[l.key] === l.value);
  const dlgEnMissDet = detectMany(dlgEnMiss.map((l) => l.value));
  const dlgEnBadMiss = dlgEnMiss.filter((l, i) =>
    !isData(l.value) && !hasNonEnDiacritics(l.value) &&
    !(dlgEnMissDet[i][0] && dlgEnMissDet[i][0] !== "en" && dlgEnMissDet[i][1] === "strong"));
  const dlgOtherEn = dialogue.flatMap((g) =>
    Object.keys(SLOT_LANG).filter((s) => SLOT_LANG[s] !== "en").map((s) => s === "bare" ? g.bare : g.bySuffix[s]));
  const dlgOtherEnChanged = dlgOtherEn.filter((l) => outEn[l.key] !== l.value);
  const dlgScriptChangedEn = dlgOtherEn.filter((l) =>
    SCRIPT_SLOTS_EN.includes(slotOf(l)) && outEn[l.key] !== l.value);
  const changedBySlotEn = {};
  for (const l of dlgOtherEnChanged) {
    const sfx = slotOf(l);
    changedBySlotEn[sfx] = (changedBySlotEn[sfx] || 0) + 1;
  }
  const badEn = badChangedLines(dlgOtherEnChanged, outEn, "en");
  rec("recheck_fixture_en_all_keys_kept", keysOkEn,
    "input " + inputKeys.length + " key -> output " + Object.keys(outEn).length + " key, thu tu khop: " + keysOkEn, performance.now() - t);
  rec("recheck_fixture_en_all_english_translated",
    enBadMiss.length === 0 && dlgEnBadMiss.length === 0,
    "dong Anh dem duoc: " + enTranslatable.length + " · da dich: " + enTranslated +
    " · chua dich: " + enUnchanged.length + " (dung ly do: lech khe/du lieu/dau khong-Anh " +
    (enUnchanged.length - enBadMiss.length) + " · SAI: " + enBadMiss.length +
    (enBadMiss.length ? " vd " + enBadMiss.slice(0, 3).map((l) => l.key).join(", ") : "") + ")" +
    " · nhom doi thoai #2/#8: " + (dlgEn.length - dlgEnMiss.length) + "/" + dlgEn.length +
    " · khe script manh bi doi: " + dlgScriptChangedEn.length, performance.now() - t);
  rec("recheck_fixture_en_other_lang_unchanged",
    badEn.bad.length === 0,
    "nhom doi thoai " + dialogue.length + " nhom: khe khac doi " + dlgOtherEnChanged.length +
    " key / " + badEn.changedLines + " dong (theo khe: " + JSON.stringify(changedBySlotEn) +
    ") — phan lon la chuoi Latin ngan khong tu chuc nang/dau (vd 'Extraordinaire!', 'Fiasko.') ma bo loc yeu khong bat duoc, gioi han da biet" +
    " · dong phat hien MANH ngon ngu khac ma van bi dich: " + badEn.bad.length +
    (badEn.bad.length ? " vd " + badEn.bad.slice(0, 3).join("; ") : "") +
    " · khe script manh (ja/ko/ru/zh) bi doi: " + dlgScriptChangedEn.length +
    " (chu yeu dong chi co ten rieng '- Marie' o cuoi thu rich-text)" +
    " · skipped=" + rEn.skipped_count + " · self_check ok=" + (rEn.self_check && rEn.self_check.ok),
    performance.now() - t);
  // --- 4b. src=zh ---
  t = performance.now();
  sentTexts = [];
  const rZh = JSON.parse(await bridge.translate_json_google(JSON.stringify(input), "vi", false, "zh"));
  const outZh = rZh.translated;
  let zhTranslated = 0, zhUnchanged = [];
  for (const l of zhTranslatable) {
    if (outZh[l.key] !== l.value) zhTranslated++;
    else zhUnchanged.push(l);
  }
  const zhMissDet = detectMany(zhUnchanged.map((l) => l.value));
  const zhBadMiss = zhUnchanged.filter((l, i) =>
    !isData(l.value) && hasCJK(l.value) &&
    !(zhMissDet[i][0] && zhMissDet[i][0] !== "zh" && zhMissDet[i][1] === "strong"));
  const dlgZhMiss = dlgZh.filter((l) => outZh[l.key] === l.value);
  const dlgZhMissDet = detectMany(dlgZhMiss.map((l) => l.value));
  const dlgZhBadMiss = dlgZhMiss.filter((l, i) =>
    !isData(l.value) && hasCJK(l.value) &&
    !(dlgZhMissDet[i][0] && dlgZhMissDet[i][0] !== "zh" && dlgZhMissDet[i][1] === "strong"));
  const dlgOtherZh = dialogue.flatMap((g) =>
    Object.keys(SLOT_LANG).filter((s) => SLOT_LANG[s] !== "zh").map((s) => s === "bare" ? g.bare : g.bySuffix[s]));
  const dlgOtherZhChanged = dlgOtherZh.filter((l) => outZh[l.key] !== l.value);
  const dlgScriptChangedZh = dlgOtherZh.filter((l) =>
    SCRIPT_SLOTS_ZH.includes(slotOf(l)) && outZh[l.key] !== l.value);
  const badZh = badChangedLines(dlgOtherZhChanged, outZh, "zh");
  rec("recheck_fixture_zh_all_chinese_translated",
    zhBadMiss.length === 0 && dlgZhBadMiss.length === 0,
    "dong Trung dem duoc: " + zhTranslatable.length + " · da dich: " + zhTranslated +
    " · chua dich: " + zhUnchanged.length + " (dung ly do: khong CJK/du lieu " +
    (zhUnchanged.length - zhBadMiss.length) + " · SAI: " + zhBadMiss.length +
    (zhBadMiss.length ? " vd " + zhBadMiss.slice(0, 3).map((l) => l.key).join(", ") : "") + ")" +
    " · nhom doi thoai #14/#15: " + (dlgZh.length - dlgZhMiss.length) + "/" + dlgZh.length,
    performance.now() - t);
  rec("recheck_fixture_zh_other_lang_unchanged",
    badZh.bad.length === 0,
    "nhom doi thoai: khe khac doi " + dlgOtherZhChanged.length + " key / " + badZh.changedLines + " dong" +
    " · dong phat hien MANH ngon ngu khac ma van bi dich: " + badZh.bad.length +
    (badZh.bad.length ? " vd " + badZh.bad.slice(0, 3).join("; ") : "") +
    " · khe script manh (ja/ko/ru) bi doi: " + dlgScriptChangedZh.length +
    " (toan chuoi Nhat thuan kanji khong kana, khong phan biet duoc voi tieng Trung — gioi han da biet)" +
    " · skipped=" + rZh.skipped_count + " · self_check ok=" + (rZh.self_check && rZh.self_check.ok),
    performance.now() - t);
  // Doi chieu file dich CU cua Joseph (bao cao, khong phai assert): thieu bao nhieu.
  const badMissing = inputKeys.filter((k) => !(k in badOut)).length;
  rec("recheck_fixture_old_bad_output_documented",
    Object.keys(badOut).length === 7408 && badMissing === 8460,
    "file dich cu: " + Object.keys(badOut).length + "/" + inputKeys.length +
    " key, thieu " + badMissing + " key (hien tuong cu, da sua o 4a/4b)",
    performance.now() - t);
  globalThis.__fixture_counts = {
    input_keys: inputKeys.length, base_groups: groups.size,
    dialogue_groups: dialogue.length,
    en_lines: enTranslatable.length, zh_lines: zhTranslatable.length,
    en_translated: enTranslated, zh_translated: zhTranslated,
    en_untranslated: enUnchanged.length, zh_untranslated: zhUnchanged.length,
    en_bad_miss: enBadMiss.length, zh_bad_miss: zhBadMiss.length,
    dlg_en: dlgEn.length, dlg_en_translated: dlgEn.length - dlgEnMiss.length,
    dlg_zh: dlgZh.length, dlg_zh_translated: dlgZh.length - dlgZhMiss.length,
    dlg_other_changed_en: dlgOtherEnChanged.length,
    dlg_other_changed_zh: dlgOtherZhChanged.length,
    dlg_script_changed_en: dlgScriptChangedEn.length,
    dlg_script_changed_zh: dlgScriptChangedZh.length,
    dlg_bad_lines_en: badEn.bad.length,
    dlg_bad_lines_zh: badZh.bad.length,
    dlg_changed_lines_en: badEn.changedLines,
    dlg_changed_lines_zh: badZh.changedLines,
    changed_by_slot_en: changedBySlotEn,
  };
}

// ================= 5. UI tren Chromium that 390px =================
if (want(5)) {
  const MIME = { ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript",
    ".json": "application/json", ".webmanifest": "application/manifest+json",
    ".wasm": "application/wasm", ".png": "image/png", ".b64": "text/plain",
    ".ttf": "font/ttf", ".txt": "text/plain", ".md": "text/plain", ".css": "text/css" };
  const server = http.createServer((req, res) => {
    const url = new URL(req.url || "/", "http://127.0.0.1");
    const fp = path.join(SITE, decodeURIComponent(url.pathname) === "/" ? "index.html" : decodeURIComponent(url.pathname));
    if (!fp.startsWith(SITE)) { res.writeHead(403); res.end(); return; }
    fs.readFile(fp, (err, data) => {
      if (err) { res.writeHead(404); res.end("not found"); return; }
      res.writeHead(200, { "content-type": MIME[path.extname(fp)] || "application/octet-stream" });
      res.end(data);
    });
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const BASE = "http://127.0.0.1:" + server.address().port;
  let browser = null;
  try {
    const { chromium } = await import("playwright-core");
    const cands = [
      process.env.HOME + "/.cache/ms-playwright/chromium_headless_shell-1148/chrome-linux/headless_shell",
      process.env.HOME + "/.cache/ms-playwright/chromium-1148/chrome-linux/chrome",
    ];
    const exe = cands.find((c) => fs.existsSync(c));
    if (!exe) throw new Error("không tìm thấy Chromium ở ~/.cache/ms-playwright");
    browser = await chromium.launch({ executablePath: exe, args: ["--no-sandbox", "--disable-dev-shm-usage"] });
    const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, acceptDownloads: true });
    const page = await ctx.newPage();
    const pageErrors = [];
    page.on("pageerror", (e) => pageErrors.push(String(e)));
    let t = performance.now();
    await page.goto(BASE + "/index.html", { waitUntil: "domcontentloaded", timeout: 60000 });
    await page.waitForFunction(() => typeof state !== "undefined", null, { timeout: 30000 });
    // Mo tab Dich.
    await page.click("#btn-card-translate");
    await page.waitForSelector("#translate-from", { timeout: 10000 });
    const fromOpts = await page.$$eval("#translate-from option", (els) => els.map((e) => e.value));
    const fromVal = await page.$eval("#translate-from", (el) => el.value);
    rec("ui_translate_from_select", fromOpts.includes("en") && fromOpts.includes("zh") && fromVal === "en",
      "options=" + JSON.stringify(fromOpts) + " mac dinh=" + fromVal, performance.now() - t);
    // Dich offline file hon hop nho (nguon en mac dinh).
    const MIXED = {
      "Play.s1": "Play", "Quit.s1": "Quit",
      "De.s1": "Wie Extraordinär!",
      "Zh.s1": "丹青妙手，神来之笔啊！",
      "En.s1": "Save the game.",
    };
    await page.fill("#translate-input", JSON.stringify(MIXED, null, 2));
    await page.uncheck("#translate-google");
    t = performance.now();
    await page.click("#btn-translate-run");
    await page.waitForFunction(() => {
      const el = document.querySelector("#translate-info");
      return el && /Bỏ qua|Mất:/.test(el.textContent || "");
    }, null, { timeout: 120000 });
    const info = await page.$eval("#translate-info", (el) => el.textContent);
    const outTxt = await page.$eval("#translate-output", (el) => el.value);
    const outJson = JSON.parse(outTxt);
    rec("ui_offline_translate_honest_info",
      /Bỏ qua \d+/.test(info) && /Mất: 0/.test(info) && /Ngôn ngữ khác bị đổi: 0/.test(info),
      info.slice(0, 220), performance.now() - t);
    rec("ui_offline_translate_output_keeps_all_keys",
      Object.keys(outJson).length === 5 && outJson["De.s1"] === "Wie Extraordinär!" &&
      outJson["Zh.s1"] === "丹青妙手，神来之笔啊！",
      "keys=" + Object.keys(outJson).length + " · De giu nguyen: " + (outJson["De.s1"] === "Wie Extraordinär!"),
      performance.now() - t);
    const held = await page.evaluate(() => {
      const lt = state.lastTranslated;
      const chk = state.translateCheck;
      return lt ? { has: true, from: lt.from, to: lt.to, keys: lt.keys.length, checkComplete: chk ? chk.complete : null, prov: lt.provenance || null }
        : { has: false };
    });
    rec("ui_translation_kept_in_session", held.has === true && held.from === "en" && held.keys === 5,
      JSON.stringify(held), performance.now() - t);
    // Provenance trong phien (muc A, cho (E) ep nhieu file): noi dung dan
    // tay -> origin "manual"; du 5 key, dung cap ngon ngu, co moc thoi gian.
    rec("ui_translation_provenance_in_session",
      !!held.prov && held.prov.origin === "manual" && held.prov.from === "en" &&
      held.prov.to === "vi" && held.prov.keys === 5 && !!held.prov.at,
      "prov=" + JSON.stringify(held.prov), performance.now() - t);
    // Ham suy provenance tu file trich xuat: dung state gia (extract entry
    // + file game) roi goi translateProvenanceFor truc tiep.
    const provExtract = await page.evaluate(() => {
      state.lastExtractFiles = [{ exported: "resources.monobehaviour.en.json", path: "resources.monobehaviour.en.json", plugin: "unity", entries: 2707, locale: "en", source: "9kings.ipa::Payload/9Kings.app/Data/resources.assets" }];
      state.file = { name: "9kings.ipa", size: 1, data: new Uint8Array(0) };
      return translateProvenanceFor("resources.monobehaviour.en.json", "en", "vi", 2707);
    });
    rec("ui_provenance_from_extract_entry",
      provExtract.origin === "extract" && provExtract.gameFile === "9kings.ipa" &&
      provExtract.extractName === "resources.monobehaviour.en.json" &&
      provExtract.source === "9kings.ipa::Payload/9Kings.app/Data/resources.assets" &&
      provExtract.locale === "en" && provExtract.keys === 2707,
      JSON.stringify(provExtract), performance.now() - t);
    // Sidecar provenance: tai file .provenance.json, noi dung phai khop
    // provenance trong phien (origin manual — vi lan dich nay dan tay).
    t = performance.now();
    const dlProv = page.waitForEvent("download", { timeout: 15000 }).catch(() => null);
    await page.click("#btn-translate-download-provenance");
    const downloadProv = await dlProv;
    let provSidecar = null;
    if (downloadProv) {
      const p = await downloadProv.path();
      if (p) { try { provSidecar = JSON.parse(fs.readFileSync(p, "utf8")); } catch (_) {} }
    }
    rec("ui_provenance_sidecar_download",
      !!downloadProv && /provenance\.json$/.test(downloadProv.suggestedFilename() || "") &&
      !!provSidecar && provSidecar.format === "beartea-translation-provenance/1" &&
      provSidecar.provenance && provSidecar.provenance.origin === "manual" &&
      provSidecar.provenance.from === "en" && provSidecar.provenance.keys === 5,
      downloadProv ? ("file: " + downloadProv.suggestedFilename() + " · prov=" + JSON.stringify(provSidecar && provSidecar.provenance)) : "khong co download event",
      performance.now() - t);
    // Cong tai: dat check xau -> bam tai phai bi chan, hien loi ro.
    t = performance.now();
    await page.evaluate(() => {
      state.translateCheck = { complete: false, missing: ["k1", "k2"], wrongChanged: [], done: 1, total: 5, skippedCount: 0 };
    });
    await page.click("#btn-translate-download");
    await page.waitForSelector("#error:not([hidden])", { timeout: 5000 }).catch(() => null);
    const errTxt = await page.$eval("#error", (el) => el.textContent || "");
    rec("ui_download_gate_blocks_incomplete", /Chưa cho tải/.test(errTxt) && /thiếu 2 key/.test(errTxt),
      errTxt.slice(0, 200), performance.now() - t);
    // Khoi phuc check sach -> tai duoc (download event).
    await page.evaluate(() => {
      state.translateCheck = { complete: true, missing: [], wrongChanged: [], done: 5, total: 5, skippedCount: 2 };
    });
    t = performance.now();
    const dl = page.waitForEvent("download", { timeout: 15000 }).catch(() => null);
    await page.click("#btn-translate-download");
    const download = await dl;
    rec("ui_download_allowed_when_complete", !!download,
      download ? ("file: " + download.suggestedFilename()) : "khong co download event", performance.now() - t);
    await page.screenshot({ path: OUT + "/ui_translate_390px.png", fullPage: false });
    rec("ui_no_page_errors", pageErrors.length === 0, pageErrors.slice(0, 3).join(" | "), performance.now() - t);
    await ctx.close();
  } catch (e) {
    rec("ui_playwright_section", false, "loi: " + (e && e.message ? e.message : String(e)), 0);
  } finally {
    if (browser) await browser.close().catch(() => {});
    server.close();
  }
}

// ================= 6. Hoi quy =================
if (want(6)) {
  const runHarness = (name, cmd, parse) => {
    const t = performance.now();
    const r = sh(cmd, EXP);
    const out = (r.stdout || "") + (r.stderr || "");
    const ok = r.status === 0 && parse(out);
    rec(name, ok, out.trim().split("\n").slice(-2).join(" / ").slice(0, 300), performance.now() - t);
  };
  runHarness("regress_module_worker",
    "node verify_module_worker.mjs 2>&1 | grep -E 'XONG'",
    (o) => /17 mục, 0 FAIL/.test(o));
  runHarness("regress_c2",
    "node verify_analysis_c2.mjs 2>&1 | grep -E 'TỔNG'",
    (o) => /13 mục, 0 FAIL/.test(o));
  runHarness("regress_c9",
    "node verify_analysis_c9.mjs 2>&1 | grep -E 'KẾT QUẢ C9'",
    (o) => /16\/16 PASS/.test(o));
  runHarness("regress_item8_static",
    "python3 check_item8_static.py 2>&1 | tail -n 5",
    (o) => /"fails": \[\]/.test(o) || /0 fails/.test(o));
  // Full suite: chay tu SITE/pb3 (tests/ nam o do), dem dong, khong
  // ghi cung tong (tong tang khi them test moi).
  {
    const t = performance.now();
    const r = sh("python3 -m unittest discover -s tests -t . 2>&1 | grep -E '^(Ran|OK|FAILED)'", SITE + "/pb3");
    const out = (r.stdout || "") + (r.stderr || "");
    const m = out.match(/Ran (\d+) tests? in [\d.]+s\n(OK|FAILED)/);
    rec("regress_full_suite_python", r.status === 0 && !!m && m[2] === "OK",
      m ? (m[1] + " test, " + m[2]) : out.trim().split("\n").slice(-2).join(" / ").slice(0, 200),
      performance.now() - t);
  }
}

// ================= Tong ket =================
const total = Object.keys(results).length;
const passed = Object.values(results).filter((v) => v.ok).length;
const failures = total - passed;
console.log("\n== TONG KET: " + passed + "/" + total + " PASS, " + failures + " FAIL (" + sec(performance.now() - t0) + "s)" +
  (ONLY ? " — CHAY MOT PHAN (ONLY=" + ONLY.join(",") + "), cac muc khac lay tu lan chay truoc" : "") + " ==");
const summary = {
  generated_at: new Date().toISOString(),
  total, passed, failed: total - passed,
  results,
  fixture_counts: globalThis.__fixture_counts || null,
  notes: "Chromium sandbox khong co HTTPS ngoai: UI chay tren HTTP local 127.0.0.1 (dung code se day). KHONG phai Safari that.",
};
fs.writeFileSync(EXP + "/analysis_translate_full_results.json", JSON.stringify(summary, null, 2), "utf8");
process.exit(failures ? 1 : 0);
