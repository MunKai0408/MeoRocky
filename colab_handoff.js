/* Colab handoff — web -> Colab -> web cho file IPA lớn.
 *
 * Luồng: tab Trích xuất > mục Colab: nhập link/ID Drive (hoặc tải file
 * lên Drive) -> web tạo job JSON -> mở notebook Colab -> notebook đọc
 * job (không cần gõ tay ID) -> trích/dịch xong ghi manifest lên Drive
 * -> web poll manifest rồi tự nhập kết quả vào mục Mod (phần B).
 *
 * Hai đường đi (xem docs/COLAB_HANDOFF.md):
 * - Path A (không cài đặt): 'Sao chép job' / 'Tải job.json' rồi dán
 *   hoặc upload vào Colab; kết quả nhập tay qua 'Nhập kết quả'.
 * - Path B (cài đặt 1 lần: OAuth client ID trong Cài đặt): web tự
 *   upload file lên Drive (resumable, chunk 8 MB, lưu session để
 *   tiếp tục), ghi job lên Drive, poll manifest và tự nhập kết quả.
 *
 * TRUNG THỰC: Colab không tự chạy khi mở link — người dùng vẫn phải
 * bấm 2 lần trong tab Colab: (1) cho phép truy cập Drive, (2) Run All
 * (Ctrl+F9). Không có tham số URL nào thay được 2 lần bấm đó.
 *
 * Mod job (tab Mod > mục Colab, đường dự phòng khi Actions không trả
 * được IPA repack >400 MB): cùng máy móc trên, job kind="mod"
 * (buildModJob), panel mod-colab-*; kết quả nhập khác — báo cáo cổng
 * hiện ở Bước 3 qua window.MeoRockyMod.showGateReport (app.js), file
 * lớn chỉ đưa link tải Drive (không nạp blob vào RAM Safari).
 *
 * File này KHÔNG sửa app.js (tránh xung đột với nhánh Actions): mọi
 * logic nằm ở đây, panel trong index.html gọi qua window.ColabHandoff.
 * Nhập kết quả dùng DataTransfer nạp file vào đúng input có sẵn của
 * web (#mod-json-file) rồi phát sự kiện 'change' — không cần sửa
 * handler có sẵn. Token Google chỉ giữ trong bộ nhớ (không lưu
 * localStorage), chỉ gửi tới googleapis.com.
 */
(function () {
  "use strict";

  var NOTEBOOK_URL = "https://colab.research.google.com/github/MunKai0408/MeoRocky/blob/main/colab/extract-large-ipa.ipynb";
  var DRIVE_API = "https://www.googleapis.com/drive/v3";
  var DRIVE_UPLOAD = "https://www.googleapis.com/upload/drive/v3/files";
  var CHUNK_SIZE = 8 * 1024 * 1024; // 8 MB/chunk — nhẹ RAM Safari
  var POLL_EVERY_MS = 60 * 1000;
  var POLL_MAX_TRIES = 120; // 2 giờ
  var LS_LAST_JOB = "meorocky_colab_last_job";
  var LS_CLIENT_ID = "meorocky_colab_oauth_client_id";
  var LS_UPLOAD = "meorocky_colab_upload_session";
  var LS_PENDING = "meorocky_colab_pending_job";
  var MANIFEST_NAME = "handoff-manifest.json";
  var JOB_DIR_NAME = "MeoRocky";
  var JOB_SUBDIR = "jobs";
  var SCOPES = "https://www.googleapis.com/auth/drive.file " +
    "https://www.googleapis.com/auth/drive.readonly";
  // drive.file: tạo/upload file (job, IPA). drive.readonly: đọc kết quả
  // do Colab ghi (không phải file web tạo nên drive.file không thấy).

  // Regex parse link Drive — PHẢI khớp pb3/sdk/colab_job.py (test
  // test_colab_job.py::test_js_drive_regex_matches_python canh lệch).
  var FILE_LINK_RE = /\/file\/d\/([A-Za-z0-9_-]{10,})/;
  var QUERY_ID_RE = /[?&]id=([A-Za-z0-9_-]{10,})/;
  var FOLDER_LINK_RE = /\/folders\/([A-Za-z0-9_-]{10,})/;
  var BARE_ID_RE = /^[A-Za-z0-9_-]{25,}$/;
  var SHORT_ID_RE = /^[A-Za-z0-9_-]{10,}$/;
  var JOB_ID_RE = /^[A-Za-z0-9][A-Za-z0-9_-]*$/;

  function fail(message) {
    var err = new Error(message);
    err.code = "E_CONFIG_INVALID";
    return err;
  }

  // ---------------- Hàm thuần (test được bằng node) ----------------

  function parseDriveFileId(text) {
    var raw = String(text == null ? "" : text).trim();
    if (!raw) {
      throw fail("Chưa nhập Drive file ID hoặc link Drive.");
    }
    if (FOLDER_LINK_RE.test(raw)) {
      throw fail("Đây là link thư mục Drive, cần link của FILE .ipa.");
    }
    var m = FILE_LINK_RE.exec(raw) || QUERY_ID_RE.exec(raw);
    if (m) return m[1];
    if (BARE_ID_RE.test(raw)) return raw;
    if (SHORT_ID_RE.test(raw)) {
      throw fail("ID Drive quá ngắn, có thể đã copy thiếu.");
    }
    throw fail("Không nhận ra ID/link Drive.");
  }

  function slugifyGame(name) {
    var text = String(name || "").trim().toLowerCase();
    try {
      // NFKD: tách dấu thành ký tự kết hợp, rồi lược bỏ chúng.
      text = text.normalize("NFKD").replace(/[̀-ͯ]/g, "");
    } catch (e) { /* normalize không có trên trình duyệt rất cũ */ }
    var slug = text.replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "")
      .slice(0, 40).replace(/-+$/g, "");
    return slug || "game";
  }

  function newJobId(game) {
    var d = new Date();
    function p(n) { return (n < 10 ? "0" : "") + n; }
    var stamp = d.getUTCFullYear() + p(d.getUTCMonth() + 1) + p(d.getUTCDate()) +
      "-" + p(d.getUTCHours()) + p(d.getUTCMinutes()) + p(d.getUTCSeconds());
    return slugifyGame(game) + "-" + stamp;
  }

  // opts: {game, driveInput, locales ("en,zh"|"en"|"zh"), fromLang,
  // toLang, translate, workerUrl, gameFileName, jobId?}
  function buildJob(opts) {
    opts = opts || {};
    var game = String(opts.game || "").trim();
    if (!game) throw fail("Chưa nhập tên game.");
    if (game.length > 120) throw fail("Tên game quá dài (tối đa 120 ký tự).");
    var fileId = parseDriveFileId(opts.driveInput || "");
    var locales = String(opts.locales || "en,zh").split(",")
      .map(function (s) { return s.trim(); }).filter(Boolean);
    if (!locales.length) throw fail("Chưa chọn locale.");
    locales.forEach(function (loc) {
      if (loc !== "en" && loc !== "zh") {
        throw fail("Locale chưa hỗ trợ trong job Colab: " + loc + ".");
      }
    });
    var fromLang = String(opts.fromLang || (locales.indexOf("en") >= 0 ? "en" : "zh"));
    var toLang = String(opts.toLang || "vi").trim().toLowerCase();
    if (!/^[a-z]{2,3}$/.test(toLang)) throw fail("Mã ngôn ngữ đích không hợp lệ.");
    if (fromLang === toLang) throw fail("Ngôn ngữ nguồn và đích trùng nhau.");
    var workerUrl = String(opts.workerUrl || "").trim();
    if (workerUrl && !/^https:\/\//.test(workerUrl)) {
      throw fail("WORKER_URL phải bắt đầu bằng https://.");
    }
    var jobId = String(opts.jobId || "").trim();
    if (jobId && !JOB_ID_RE.test(jobId)) throw fail("job_id không hợp lệ.");
    return {
      version: 1,
      job_id: jobId || newJobId(game),
      game: game,
      drive_file_id: fileId,
      game_file_name: String(opts.gameFileName || "").trim(),
      locales: locales,
      from_lang: fromLang,
      to_lang: toLang,
      translate: opts.translate !== false,
      worker_url: workerUrl,
      created_at: new Date().toISOString(),
      created_by: "MeoRocky web"
    };
  }

  function jobFileName(job) {
    return "meorocky-job-" + job.job_id + ".json";
  }

  // opts: {game, driveInput (IPA), originalInput, translationInput,
  // toLang, applyMode ("REBUILD_SECTION"|"PATCH_INPLACE"), repack,
  // gateOnly, displayFont ("donor"|"scan"), gameFileName, jobId?}
  // -> job kind="mod" (khop pb3/sdk/colab_job.py: build_mod_job).
  function buildModJob(opts) {
    opts = opts || {};
    var game = String(opts.game || "").trim();
    if (!game) throw fail("Chưa nhập tên game.");
    if (game.length > 120) throw fail("Tên game quá dài (tối đa 120 ký tự).");
    var fileId = parseDriveFileId(opts.driveInput || "");
    var originalId = parseDriveFileId(opts.originalInput || "");
    var translationId = parseDriveFileId(opts.translationInput || "");
    var toLang = String(opts.toLang || "vi").trim().toLowerCase();
    if (!/^[a-z]{2,3}$/.test(toLang)) throw fail("Mã ngôn ngữ đích không hợp lệ.");
    if (toLang === "en") throw fail("Ngôn ngữ nguồn và đích trùng nhau.");
    var applyMode = String(opts.applyMode || "REBUILD_SECTION").trim().toUpperCase();
    if (applyMode !== "REBUILD_SECTION" && applyMode !== "PATCH_INPLACE") {
      throw fail("Chế độ ép không hỗ trợ: " + applyMode + ".");
    }
    var displayFont = String(opts.displayFont || "donor").trim().toLowerCase();
    if (displayFont !== "donor" && displayFont !== "scan") {
      throw fail("Chế độ font không hỗ trợ: " + displayFont + ".");
    }
    var engine = String(opts.engine || "unity").trim().toLowerCase();
    if (engine !== "unity") {
      throw fail("Engine " + engine + " chưa hỗ trợ — hiện chỉ có Unity (Unreal/Native iOS sẽ thêm sau).");
    }
    var jobId = String(opts.jobId || "").trim();
    if (jobId && !JOB_ID_RE.test(jobId)) throw fail("job_id không hợp lệ.");
    return {
      version: 1,
      kind: "mod",
      engine: engine,
      job_id: jobId || newJobId(game),
      game: game,
      drive_file_id: fileId,
      game_file_name: String(opts.gameFileName || "").trim(),
      original_file_id: originalId,
      translation_file_id: translationId,
      from_lang: "en",
      to_lang: toLang,
      apply_mode: applyMode,
      repack: opts.repack !== false,
      gate_only: opts.gateOnly === true,
      display_font: displayFont,
      locales: [],
      translate: false,
      worker_url: "",
      created_at: new Date().toISOString(),
      created_by: "MeoRocky web"
    };
  }

  function jobToJson(job) {
    return JSON.stringify(job, null, 2) + "\n";
  }

  // Chia file thành chunk cho upload resumable: [{start, end, index, count}]
  function chunkRanges(total, chunkSize) {
    var size = chunkSize || CHUNK_SIZE;
    var out = [];
    var start = 0;
    var index = 0;
    while (start < total) {
      var end = Math.min(start + size, total);
      out.push({ start: start, end: end, index: index, count: 0 });
      start = end;
      index += 1;
    }
    out.forEach(function (c) { c.count = out.length; });
    return out;
  }

  // Tìm manifest đúng job_id trong list manifest tải từ Drive.
  function findManifestForJob(manifests, jobId) {
    for (var i = 0; i < (manifests || []).length; i++) {
      var m = manifests[i];
      if (m && m.job_id === jobId) return m;
    }
    return null;
  }

  // Tên file kết quả cần tải từ manifest: locale trước, bản dịch sau.
  function manifestResultFiles(manifest) {
    var out = [];
    (manifest.files || []).forEach(function (f) {
      out.push({ name: f.name, kind: "locale" });
    });
    (manifest.translated || []).forEach(function (f) {
      out.push({ name: f.name, kind: "translated" });
    });
    return out;
  }

  // File kết quả của manifest Mod: giữ nguyên kind trong manifest
  // ("gate-report" | "summary" | "applied" | "repacked") để web biết
  // file nào hiện báo cáo, file nào chỉ đưa link tải (file lớn).
  function modManifestResultFiles(manifest) {
    var out = [];
    (manifest.files || []).forEach(function (f) {
      out.push({ name: f.name, kind: f.kind || "file",
                 bytes: f.bytes || 0 });
    });
    return out;
  }

  // Body multipart/related để upload file JSON nhỏ lên Drive.
  function multipartBody(metadata, content) {
    var boundary = "meorocky-job-boundary";
    var body = "--" + boundary + "\r\n" +
      "Content-Type: application/json; charset=UTF-8\r\n\r\n" +
      JSON.stringify(metadata) + "\r\n" +
      "--" + boundary + "\r\n" +
      "Content-Type: application/json; charset=UTF-8\r\n\r\n" +
      content + "\r\n" +
      "--" + boundary + "--";
    return { boundary: boundary, body: body };
  }

  // ---------------- Tiện ích trình duyệt ----------------

  function $(id) {
    return typeof document !== "undefined" ? document.getElementById(id) : null;
  }

  function lsGet(key) {
    try { return window.localStorage.getItem(key); } catch (e) { return null; }
  }

  function lsSet(key, value) {
    try {
      if (value == null) window.localStorage.removeItem(key);
      else window.localStorage.setItem(key, value);
    } catch (e) { /* localStorage đầy/không có */ }
  }

  function setStatus(text) {
    var el = $("colab-handoff-status");
    if (el) el.textContent = text;
  }

  function showError(text) {
    setStatus(text);
  }

  function readPanel() {
    var gameEl = $("colab-game-name");
    var driveEl = $("colab-drive-input");
    var localesEl = $("colab-locales");
    var translateEl = $("colab-translate");
    var toEl = $("colab-to-lang");
    var workerEl = $("colab-worker-url");
    var fileNameEl = $("colab-ipa-name");
    var proxyEl = $("translate-proxy"); // URL worker đã cấu hình ở tab Dịch
    var workerUrl = workerEl ? workerEl.value : "";
    if (!workerUrl && proxyEl && proxyEl.value) workerUrl = proxyEl.value;
    return {
      game: gameEl ? gameEl.value : "",
      driveInput: driveEl ? driveEl.value : "",
      locales: localesEl ? localesEl.value : "en,zh",
      toLang: toEl ? toEl.value : "vi",
      translate: translateEl ? translateEl.checked : true,
      workerUrl: workerUrl,
      gameFileName: fileNameEl ? fileNameEl.value : ""
    };
  }

  function saveLastJob(job) {
    lsSet(LS_LAST_JOB, JSON.stringify(job));
  }

  function currentJob() {
    var opts;
    try {
      opts = readPanel();
      var job = buildJob(opts);
      saveLastJob(job);
      return job;
    } catch (err) {
      showError(err.message || String(err));
      return null;
    }
  }

  function downloadText(filename, text, mime) {
    var blob = new Blob([text], { type: mime || "application/json" });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    setTimeout(function () {
      URL.revokeObjectURL(a.href);
      a.remove();
    }, 1000);
  }

  // Nạp file kết quả vào đúng input có sẵn của web (#mod-json-file,
  // mục Mod phần B) rồi phát 'change' — chạy đúng handler có sẵn như
  // khi người dùng tự chọn file. Trả false nếu không nạp được.
  function dispatchToMod(files) {
    var input = $("mod-json-file");
    if (!input) return false;
    if (typeof DataTransfer === "undefined") return false;
    try {
      var dt = new DataTransfer();
      files.forEach(function (f) {
        dt.items.add(new File([f.blob], f.name, { type: "application/json" }));
      });
      input.files = dt.files;
      input.dispatchEvent(new Event("change", { bubbles: true }));
      return true;
    } catch (e) {
      return false;
    }
  }

  // ---------------- Drive API (Path B) ----------------

  var accessToken = null; // chỉ giữ trong bộ nhớ, không lưu localStorage
  var pollTimer = null;
  var pollTries = 0;

  function loadGis() {
    return new Promise(function (resolve, reject) {
      if (typeof window === "undefined") return reject(new Error("Không có window."));
      if (window.google && window.google.accounts && window.google.accounts.oauth2) {
        return resolve();
      }
      var s = document.createElement("script");
      s.src = "https://accounts.google.com/gsi/client";
      s.onload = function () { resolve(); };
      s.onerror = function () { reject(new Error("Không tải được Google Identity Services.")); };
      document.head.appendChild(s);
    });
  }

  function connectDrive() {
    var clientId = (lsGet(LS_CLIENT_ID) || "").trim();
    if (!clientId) {
      return Promise.reject(new Error("Chưa có OAuth client ID — nhập ở mục 'Tải lên Drive' bên dưới (xem docs/COLAB_HANDOFF.md)."));
    }
    return loadGis().then(function () {
      return new Promise(function (resolve, reject) {
        var client = window.google.accounts.oauth2.initTokenClient({
          client_id: clientId,
          scope: SCOPES,
          callback: function (resp) {
            if (resp && resp.access_token) {
              accessToken = resp.access_token;
              resolve(accessToken);
            } else {
              reject(new Error("Google không trả access token."));
            }
          },
          error_callback: function () {
            reject(new Error("Bị từ chối quyền Drive — bấm cho phép để tiếp tục."));
          }
        });
        client.requestAccessToken({ prompt: "" });
      });
    });
  }

  function driveFetch(path, opts) {
    opts = opts || {};
    var headers = { "Authorization": "Bearer " + accessToken };
    if (opts.headers) {
      Object.keys(opts.headers).forEach(function (k) { headers[k] = opts.headers[k]; });
    }
    return fetch(DRIVE_API + path, {
      method: opts.method || "GET",
      headers: headers,
      body: opts.body
    }).then(function (resp) {
      if (resp.status === 401) {
        accessToken = null;
        throw new Error("Token Drive hết hạn — bấm 'Kết nối Drive' lại.");
      }
      if (!resp.ok) {
        throw new Error("Drive API lỗi HTTP " + resp.status + ".");
      }
      return opts.raw ? resp : resp.json();
    });
  }

  function driveList(query) {
    var q = encodeURIComponent(query);
    return driveFetch("/files?q=" + q +
      "&fields=files(id,name,parents,modifiedTime)&pageSize=20")
      .then(function (data) { return data.files || []; });
  }

  function driveDownload(fileId) {
    return driveFetch("/files/" + encodeURIComponent(fileId) + "?alt=media",
      { raw: true }).then(function (resp) { return resp.blob(); });
  }

  function ensureFolder(name, parentId) {
    var q = "name = '" + name.replace(/'/g, "\\'") + "'" +
      " and mimeType = 'application/vnd.google-apps.folder'" +
      " and trashed = false";
    if (parentId) q += " and '" + parentId + "' in parents";
    return driveList(q).then(function (files) {
      if (files.length) return files[0].id;
      return fetch(DRIVE_API + "/files", {
        method: "POST",
        headers: {
          "Authorization": "Bearer " + accessToken,
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          name: name,
          mimeType: "application/vnd.google-apps.folder",
          parents: parentId ? [parentId] : undefined
        })
      }).then(function (resp) {
        if (!resp.ok) throw new Error("Không tạo được thư mục Drive.");
        return resp.json();
      }).then(function (data) { return data.id; });
    });
  }

  function uploadJsonToDrive(name, text, folderId) {
    var part = multipartBody(
      { name: name, parents: folderId ? [folderId] : undefined }, text);
    return fetch(DRIVE_UPLOAD + "?uploadType=multipart", {
      method: "POST",
      headers: {
        "Authorization": "Bearer " + accessToken,
        "Content-Type": "multipart/related; boundary=" + part.boundary
      },
      body: part.body
    }).then(function (resp) {
      if (!resp.ok) throw new Error("Không ghi được " + name + " lên Drive (HTTP " + resp.status + ").");
      return resp.json();
    });
  }

  function writeJobToDrive(job) {
    var text = jobToJson(job);
    return ensureFolder(JOB_DIR_NAME).then(function (rootId) {
      return ensureFolder(JOB_SUBDIR, rootId).then(function (jobsId) {
        return uploadJsonToDrive(jobFileName(job), text, jobsId)
          .then(function () {
            return uploadJsonToDrive("latest.json", text, jobsId);
          })
          .then(function () { return { jobsId: jobsId }; });
      });
    });
  }

  // Upload resumable 1 file: tạo session -> PUT từng chunk 8 MB.
  // Session URI + tiến độ lưu localStorage để tắt tab vẫn tiếp tục được.
  function uploadFileResumable(file, folderId, onProgress) {
    var meta = { name: file.name, parents: folderId ? [folderId] : undefined };
    var saved = null;
    try { saved = JSON.parse(lsGet(LS_UPLOAD) || "null"); } catch (e) { saved = null; }
    function createSession() {
      return fetch(DRIVE_UPLOAD + "?uploadType=resumable", {
        method: "POST",
        headers: {
          "Authorization": "Bearer " + accessToken,
          "Content-Type": "application/json"
        },
        body: JSON.stringify(meta)
      }).then(function (resp) {
        if (!resp.ok) throw new Error("Không tạo được phiên upload (HTTP " + resp.status + ").");
        var uri = resp.headers.get("Location");
        if (!uri) throw new Error("Drive không trả session upload.");
        return uri;
      });
    }
    function queryUploaded(sessionUri) {
      return fetch(sessionUri, {
        method: "PUT",
        headers: {
          "Content-Length": "0",
          "Content-Range": "bytes */" + file.size
        }
      }).then(function (resp) {
        if (resp.status === 308) {
          var range = resp.headers.get("Range") || "";
          var m = /bytes=0-(\d+)/.exec(range);
          return m ? parseInt(m[1], 10) + 1 : 0;
        }
        if (resp.status === 200 || resp.status === 201) return file.size;
        if (resp.status === 404 || resp.status === 410) return -1; // session hết hạn
        throw new Error("Hỏi tiến độ upload lỗi HTTP " + resp.status + ".");
      });
    }
    function putChunk(sessionUri, start) {
      var end = Math.min(start + CHUNK_SIZE, file.size);
      return fetch(sessionUri, {
        method: "PUT",
        headers: { "Content-Range": "bytes " + start + "-" + (end - 1) + "/" + file.size },
        body: file.slice(start, end)
      }).then(function (resp) {
        if (resp.status === 308) {
          var next = end;
          lsSet(LS_UPLOAD, JSON.stringify({
            sessionUri: sessionUri, name: file.name,
            size: file.size, uploaded: next
          }));
          if (onProgress) onProgress(next, file.size);
          return putChunk(sessionUri, next);
        }
        if (resp.status === 200 || resp.status === 201) {
          lsSet(LS_UPLOAD, null);
          if (onProgress) onProgress(file.size, file.size);
          return resp.json();
        }
        throw new Error("Upload chunk lỗi HTTP " + resp.status + ".");
      });
    }
    var sessionP;
    if (saved && saved.sessionUri && saved.name === file.name &&
        saved.size === file.size && saved.uploaded > 0) {
      sessionP = queryUploaded(saved.sessionUri).then(function (uploaded) {
        if (uploaded === -1) return createSession().then(function (u) { return { uri: u, start: 0 }; });
        return { uri: saved.sessionUri, start: uploaded };
      });
    } else {
      sessionP = createSession().then(function (u) { return { uri: u, start: 0 }; });
    }
    return sessionP.then(function (s) { return putChunk(s.uri, s.start); });
  }

  // Poll manifest theo job_id: tải các manifest, lọc đúng job, nếu
  // status 'done' thì tải file kết quả trong cùng thư mục rồi nhập.
  function pollOnce(jobId) {
    return driveList("name = '" + MANIFEST_NAME + "' and trashed = false")
      .then(function (files) {
        var jobs = files.map(function (f) {
          return driveDownload(f.id).then(function (blob) {
            return blob.text().then(function (text) {
              try { return JSON.parse(text); } catch (e) { return null; }
            });
          });
        });
        return Promise.all(jobs);
      })
      .then(function (manifests) {
        var man = findManifestForJob(manifests, jobId);
        if (!man) return { status: "waiting" };
        if (man.status !== "done") return { status: man.status || "waiting" };
        return { status: "done", manifest: man };
      });
  }

  function downloadResults(manifest) {
    // Tìm file kết quả theo tên trong Drive rồi tải từng file.
    var wanted = manifestResultFiles(manifest).map(function (r) { return r.name; });
    var lists = wanted.map(function (name) {
      return driveList("name = '" + name.replace(/'/g, "\\'") + "' and trashed = false");
    });
    return Promise.all(lists)
      .then(function (groups) {
        var found = {};
        groups.forEach(function (g, i) {
          if (g.length) found[wanted[i]] = g[0].id;
        });
        return found;
      })
      .then(function (byName) {
        var wanted = manifestResultFiles(manifest);
        var missing = wanted.filter(function (r) { return !byName[r.name]; })
          .map(function (r) { return r.name; });
        if (missing.length) {
          throw new Error("Chưa thấy đủ file kết quả trên Drive (thiếu: " + missing.join(", ") + ").");
        }
        var downs = wanted.map(function (r) {
          return driveDownload(byName[r.name]).then(function (blob) {
            return { name: r.name, blob: blob };
          });
        });
        return Promise.all(downs);
      });
  }

  function importDownloaded(files) {
    if (dispatchToMod(files)) {
      setStatus("Đã nhập " + files.length + " file kết quả vào mục Mod (phần B) — mở tab Mod để sửa.");
      return true;
    }
    // Dự phòng: tải file thật về máy để người dùng nhập tay.
    files.forEach(function (f) {
      var a = document.createElement("a");
      a.href = URL.createObjectURL(f.blob);
      a.download = f.name;
      document.body.appendChild(a);
      a.click();
      setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
    });
    setStatus("Trình duyệt không cho nạp file tự động — đã tải " + files.length +
      " file về, bấm 'Nhập file .json…' trong mục Mod (phần B) để chọn chúng.");
    return false;
  }

  function startAutoPoll(jobId) {
    stopAutoPoll();
    pollTries = 0;
    lsSet(LS_PENDING, JSON.stringify({ job_id: jobId, startedAt: Date.now() }));
    pollTimer = setInterval(function () {
      pollTries += 1;
      if (pollTries > POLL_MAX_TRIES) {
        stopAutoPoll();
        setStatus("Hết 2 giờ chờ kết quả — bấm 'Kiểm tra kết quả' để hỏi lại.");
        return;
      }
      pollOnce(jobId).then(function (r) {
        if (r.status === "done") {
          stopAutoPoll();
          setStatus("Colab xong rồi — đang tải kết quả về…");
          return downloadResults(r.manifest).then(importDownloaded)
            .catch(function (err) { showError(err.message || String(err)); });
        }
        setStatus("Đang chờ Colab… (lần hỏi " + pollTries + ", trạng thái: " + r.status + ")");
      }).catch(function (err) {
        if (err && /401|hết hạn/.test(err.message || "")) {
          stopAutoPoll();
          showError(err.message);
        }
      });
    }, POLL_EVERY_MS);
  }

  function stopAutoPoll() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    lsSet(LS_PENDING, null);
  }

  // ---------------- Mod job qua Colab (đường dự phòng) ----------------
  // Tái dùng đúng máy móc Drive/poll ở trên (connectDrive, ensureFolder,
  // uploadFileResumable, writeJobToDrive, pollOnce): chỉ khác panel
  // (mod-colab-*) + cách nhập kết quả (báo cáo cổng hiện ở Bước 3 qua
  // window.MeoRockyMod.showGateReport; file lớn chỉ đưa link tải Drive
  // chứ không nạp blob hàng trăm MB vào RAM Safari).

  var LS_MOD_LAST_JOB = "meorocky_mod_colab_last_job";
  var LS_MOD_PENDING = "meorocky_mod_colab_pending_job";
  var modPollTimer = null;
  var modPollTries = 0;

  function setModStatus(text) {
    var el = $("mod-colab-status");
    if (el) el.textContent = text;
  }

  function showModError(text) {
    setModStatus(text);
  }

  function readModPanel() {
    function v(id) {
      var el = $(id);
      return el ? el.value : "";
    }
    function checked(id) {
      var el = $(id);
      return el ? el.checked : false;
    }
    return {
      game: v("mod-colab-game-name"),
      driveInput: v("mod-colab-drive-ipa"),
      originalInput: v("mod-colab-drive-original"),
      translationInput: v("mod-colab-drive-translation"),
      toLang: v("mod-colab-to-lang") || "vi",
      applyMode: v("mod-colab-apply-mode") || "REBUILD_SECTION",
      repack: checked("mod-colab-repack"),
      gateOnly: checked("mod-colab-gate-only"),
      displayFont: v("mod-colab-display-font") || "donor",
      engine: v("mod-colab-engine") || "unity",
      gameFileName: v("mod-colab-ipa-name")
    };
  }

  function saveLastModJob(job) {
    lsSet(LS_MOD_LAST_JOB, JSON.stringify(job));
  }

  function currentModJob() {
    var job;
    try {
      job = buildModJob(readModPanel());
      saveLastModJob(job);
      return job;
    } catch (err) {
      showModError(err.message || String(err));
      return null;
    }
  }

  function fmtModBytes(n) {
    if (!n && n !== 0) return "";
    if (n < 1048576) return (n / 1024).toFixed(0) + " KB";
    return (n / 1048576).toFixed(1) + " MB";
  }

  // Tải kết quả Mod: JSON nhỏ (báo cáo cổng, summary) thì tải blob để
  // hiện ngay; file lớn (applied.zip, IPA repack) chỉ lấy Drive file ID
  // để hiện link tải — không giữ blob trong RAM.
  function downloadModResults(manifest) {
    var wanted = modManifestResultFiles(manifest);
    var small = wanted.filter(function (r) {
      return r.kind === "gate-report" || r.kind === "summary";
    });
    var big = wanted.filter(function (r) {
      return r.kind !== "gate-report" && r.kind !== "summary";
    });
    var lists = wanted.map(function (r) {
      return driveList("name = '" + r.name.replace(/'/g, "\\'") +
        "' and trashed = false");
    });
    return Promise.all(lists).then(function (groups) {
      var byName = {};
      groups.forEach(function (g, i) {
        if (g.length) byName[wanted[i].name] = g[0].id;
      });
      var missing = wanted.filter(function (r) { return !byName[r.name]; })
        .map(function (r) { return r.name; });
      if (missing.length) {
        throw new Error("Chưa thấy đủ file kết quả trên Drive (thiếu: " +
          missing.join(", ") + ").");
      }
      var downs = small.map(function (r) {
        return driveDownload(byName[r.name]).then(function (blob) {
          return { name: r.name, kind: r.kind, blob: blob };
        });
      });
      return Promise.all(downs).then(function (jsons) {
        var links = big.map(function (r) {
          return { name: r.name, kind: r.kind, bytes: r.bytes,
                   driveId: byName[r.name],
                   url: "https://drive.google.com/uc?id=" +
                     encodeURIComponent(byName[r.name]) +
                     "&export=download" };
        });
        return { jsons: jsons, links: links, manifest: manifest };
      });
    });
  }

  // Nhập kết quả Mod về web: báo cáo cổng hiện ở Bước 3 (tab Mod) qua
  // hook của app.js; summary hiện dòng trạng thái; file lớn đưa link.
  function importModResults(downloaded) {
    var manifest = downloaded.manifest;
    var box = $("mod-colab-results");
    var gate = null;
    var summary = null;
    var reads = downloaded.jsons.map(function (f) {
      return f.blob.text().then(function (text) {
        try { return { name: f.name, kind: f.kind, data: JSON.parse(text) }; }
        catch (e) { return { name: f.name, kind: f.kind, data: null }; }
      });
    });
    return Promise.all(reads).then(function (parsed) {
      parsed.forEach(function (p) {
        if (p.kind === "gate-report" && p.data && p.data.verdict) {
          if (!gate) gate = p.data;
        }
        if (p.kind === "summary" && p.data) summary = p.data;
      });
      var verdict = manifest.gate ? manifest.gate.verdict
        : (gate ? gate.verdict : "?");
      if (gate && typeof window !== "undefined" && window.MeoRockyMod &&
          typeof window.MeoRockyMod.showGateReport === "function") {
        window.MeoRockyMod.showGateReport(gate);
        setModStatus("Colab xong — cổng kiểm tra: " +
          (verdict === "PASS" ? "ĐẠT" : "CHƯA ĐẠT") +
          " (đã hiện ở Bước 3 bên trên). " +
          (summary && summary.note ? summary.note : ""));
      } else {
        setModStatus("Colab xong — cổng kiểm tra: " +
          (verdict === "PASS" ? "ĐẠT" : "CHƯA ĐẠT") +
          ". Mở tab Mod xem Bước 3 (cần tải lại trang nếu chưa thấy).");
      }
      if (box) {
        box.textContent = "";
        var title = document.createElement("p");
        title.textContent = "Kết quả Mod (job " + manifest.job_id + "):";
        box.appendChild(title);
        if (summary && summary.note) {
          var note = document.createElement("p");
          note.className = "note";
          note.textContent = summary.note;
          box.appendChild(note);
        }
        var ul = document.createElement("ul");
        downloaded.links.forEach(function (l) {
          var li = document.createElement("li");
          var a = document.createElement("a");
          a.href = l.url;
          a.target = "_blank";
          a.rel = "noopener";
          a.textContent = "⬇️ " + l.name + " (" + fmtModBytes(l.bytes) + ")";
          li.appendChild(a);
          li.appendChild(document.createTextNode(
            l.kind === "repacked"
              ? " — IPA đã repack (cần ký lại trước khi cài)"
              : " — các đơn vị đã ép"));
          ul.appendChild(li);
        });
        if (downloaded.links.length) box.appendChild(ul);
        var hint = document.createElement("p");
        hint.className = "note";
        hint.textContent = "Báo cáo cổng (gate-report.json) đã hiện ở Bước 3 bên trên — " +
          "bấm “Nhập báo cáo cổng…” ở mục Actions nếu muốn nhập lại từ file đã tải về.";
        box.appendChild(hint);
      }
      return true;
    });
  }

  function stopModAutoPoll() {
    if (modPollTimer) { clearInterval(modPollTimer); modPollTimer = null; }
    lsSet(LS_MOD_PENDING, null);
  }

  function startModAutoPoll(jobId) {
    stopModAutoPoll();
    modPollTries = 0;
    lsSet(LS_MOD_PENDING, JSON.stringify({ job_id: jobId, startedAt: Date.now() }));
    modPollTimer = setInterval(function () {
      modPollTries += 1;
      if (modPollTries > POLL_MAX_TRIES) {
        stopModAutoPoll();
        setModStatus("Hết 2 giờ chờ kết quả — bấm 'Kiểm tra kết quả' để hỏi lại.");
        return;
      }
      pollOnce(jobId).then(function (r) {
        if (r.status === "done") {
          stopModAutoPoll();
          setModStatus("Colab xong rồi — đang tải kết quả về…");
          return downloadModResults(r.manifest).then(importModResults)
            .catch(function (err) { showModError(err.message || String(err)); });
        }
        setModStatus("Đang chờ Colab… (lần hỏi " + modPollTries +
          ", trạng thái: " + r.status + ")");
      }).catch(function (err) {
        if (err && /401|hết hạn/.test(err.message || "")) {
          stopModAutoPoll();
          showModError(err.message);
        }
      });
    }, POLL_EVERY_MS);
  }

  // Upload 1 file đã chọn lên Drive rồi điền ID vào ô Drive tương ứng
  // (pickerId: input file; driveInputId: ô Drive cần điền).
  function uploadModPickedFile(pickerId, driveInputId, label) {
    var job = currentModJob();
    if (!job) return;
    var picker = $(pickerId);
    if (!picker) { showModError("Thiếu ô chọn file."); return; }
    if (!picker.files || !picker.files.length) {
      showModError("Chọn file " + label + " trên máy trước (nút 'Chọn file…').");
      return;
    }
    var file = picker.files[0];
    setModStatus("Đang kết nối Drive…");
    connectDrive().then(function () {
      return ensureFolder(JOB_DIR_NAME);
    }).then(function (rootId) {
      setModStatus("Đang tải " + file.name + " lên Drive (0%)…");
      return uploadFileResumable(file, rootId, function (done, total) {
        setModStatus("Đang tải " + file.name + " lên Drive (" +
          Math.round(100 * done / total) + "%)…");
      });
    }).then(function (meta) {
      var input = $(driveInputId);
      if (input) input.value = meta.id;
      if (driveInputId === "mod-colab-drive-ipa") {
        job.drive_file_id = meta.id;
        job.game_file_name = file.name;
        var nameEl = $("mod-colab-ipa-name");
        if (nameEl && !nameEl.value) nameEl.value = file.name;
      }
      saveLastModJob(job);
      setModStatus("Đã tải xong " + label + " — đang ghi job lên Drive…");
      return writeJobToDrive(job);
    }).then(function () {
      setModStatus("Xong: file + job đã lên Drive. Bấm 'Mở Colab' — notebook sẽ tự đọc job, không cần dán gì.");
    }).catch(function (err) { showModError(err.message || String(err)); });
  }

  function initMod() {
    if (typeof document === "undefined") return;
    try {
      var last = JSON.parse(lsGet(LS_MOD_LAST_JOB) || "null");
      if (last) {
        var fill = function (id, val) {
          var el = $(id);
          if (el && !el.value && val) el.value = val;
        };
        fill("mod-colab-game-name", last.game);
        fill("mod-colab-drive-ipa", last.drive_file_id);
        fill("mod-colab-drive-original", last.original_file_id);
        fill("mod-colab-drive-translation", last.translation_file_id);
        fill("mod-colab-ipa-name", last.game_file_name);
        var cid = $( "mod-colab-oauth-client-id");
        if (cid && !cid.value) cid.value = lsGet(LS_CLIENT_ID) || "";
      } else {
        var cid0 = $("mod-colab-oauth-client-id");
        if (cid0 && !cid0.value) cid0.value = lsGet(LS_CLIENT_ID) || "";
      }
    } catch (e) { /* job cũ hỏng thì bỏ qua */ }

    on("btn-mod-colab-copy-job", "click", function () {
      var job = currentModJob();
      if (job) copyText(jobToJson(job), "Đã sao chép Mod job (" + job.job_id + ") — dán vào ô JOB_JSON trong notebook Colab.");
    });
    on("btn-mod-colab-download-job", "click", function () {
      var job = currentModJob();
      if (job) {
        downloadText(jobFileName(job), jobToJson(job));
        setModStatus("Đã tải " + jobFileName(job) + " — upload file này vào khung Files của Colab (hoặc để web tự ghi lên Drive ở mục bên dưới).");
      }
    });
    on("btn-mod-colab-open", "click", function () {
      var job = currentModJob();
      if (!job) return;
      window.open(NOTEBOOK_URL, "_blank", "noopener");
      if (accessToken) {
        setModStatus("Đã mở notebook Colab — trong tab mới: bấm cho phép Drive, job đã có sẵn trên Drive nên không cần dán gì, rồi Run All (Ctrl+F9). Web sẽ tự hỏi kết quả mỗi phút.");
        startModAutoPoll(job.job_id);
      } else {
        setModStatus("Đã mở notebook Colab — trong tab mới: bấm cho phép Drive, dán job (đã sao chép sẵn? chưa — bấm 'Sao chép job' trước) hoặc upload " +
          jobFileName(job) + ", rồi Run All (Ctrl+F9). Muốn web tự hỏi kết quả thì kết nối Drive ở mục bên dưới trước.");
      }
    });
    on("btn-mod-colab-import-results", "click", function () {
      var input = $("mod-colab-import-file");
      if (input) input.click();
    });
    on("mod-colab-import-file", "change", function (ev) {
      var list = ev.target.files;
      if (!list || !list.length) return;
      var reads = [];
      for (var i = 0; i < list.length; i++) {
        (function (file) {
          reads.push(file.text().then(function (text) {
            try { return { name: file.name, data: JSON.parse(text) }; }
            catch (e) { return { name: file.name, data: null }; }
          }));
        })(list[i]);
      }
      Promise.all(reads).then(function (files) {
        var gate = null;
        files.forEach(function (f) {
          if (f.data && f.data.verdict &&
              /gate-report/i.test(f.name) && !gate) gate = f.data;
        });
        if (gate && typeof window !== "undefined" && window.MeoRockyMod &&
            typeof window.MeoRockyMod.showGateReport === "function") {
          window.MeoRockyMod.showGateReport(gate);
          setModStatus("Đã nhập báo cáo cổng (" +
            (gate.verdict === "PASS" ? "ĐẠT" : "CHƯA ĐẠT") +
            ") — xem ở Bước 3 bên trên.");
        } else {
          setModStatus("Không thấy báo cáo cổng trong các file đã chọn — chọn file gate-report.json (tải từ Colab/Actions).");
        }
      });
      ev.target.value = "";
    });
    on("btn-mod-colab-check-results", "click", function () {
      var job = currentModJob();
      if (!job) return;
      setModStatus("Đang hỏi Drive xem Mod job " + job.job_id + " xong chưa…");
      connectDrive().then(function () {
        return pollOnce(job.job_id);
      }).then(function (r) {
        if (r.status === "done") {
          setModStatus("Colab xong rồi — đang tải kết quả về…");
          return downloadModResults(r.manifest).then(importModResults);
        }
        setModStatus("Colab chưa xong (trạng thái: " + r.status + ") — cứ để tab mở, web sẽ hỏi lại mỗi phút.");
        startModAutoPoll(job.job_id);
      }).catch(function (err) { showModError(err.message || String(err)); });
    });
    on("btn-mod-colab-pick-ipa", "click", function () {
      var picker = $("mod-colab-ipa-file");
      if (picker) picker.click();
    });
    on("mod-colab-ipa-file", "change", function (ev) {
      var f = ev.target.files && ev.target.files[0];
      if (f) {
        setModStatus("Đã chọn: " + f.name + " (" + (f.size / 1048576).toFixed(1) +
          " MB) — bấm 'Tải IPA lên Drive' (có thể mất nhiều phút, cứ để tab mở).");
      }
    });
    on("btn-mod-colab-upload-ipa", "click", function () {
      uploadModPickedFile("mod-colab-ipa-file", "mod-colab-drive-ipa", "IPA");
    });
    on("btn-mod-colab-pick-original", "click", function () {
      var picker = $("mod-colab-original-file");
      if (picker) picker.click();
    });
    on("mod-colab-original-file", "change", function (ev) {
      var f = ev.target.files && ev.target.files[0];
      if (f) setModStatus("Đã chọn JSON gốc: " + f.name + " — bấm 'Tải JSON gốc lên Drive'.");
    });
    on("btn-mod-colab-upload-original", "click", function () {
      uploadModPickedFile("mod-colab-original-file", "mod-colab-drive-original", "JSON gốc");
    });
    on("btn-mod-colab-pick-translation", "click", function () {
      var picker = $("mod-colab-translation-file");
      if (picker) picker.click();
    });
    on("mod-colab-translation-file", "change", function (ev) {
      var f = ev.target.files && ev.target.files[0];
      if (f) setModStatus("Đã chọn JSON bản dịch: " + f.name + " — bấm 'Tải bản dịch lên Drive'.");
    });
    on("btn-mod-colab-upload-translation", "click", function () {
      uploadModPickedFile("mod-colab-translation-file", "mod-colab-drive-translation", "JSON bản dịch");
    });
    on("btn-mod-colab-save-client-id", "click", function () {
      var el = $("mod-colab-oauth-client-id");
      var v = el ? el.value.trim() : "";
      if (!v) { showModError("Chưa nhập OAuth client ID."); return; }
      lsSet(LS_CLIENT_ID, v);
      setModStatus("Đã lưu OAuth client ID (chỉ trong trình duyệt này). Bấm 'Kết nối Drive' để cấp quyền.");
    });
    try {
      var pending = JSON.parse(lsGet(LS_MOD_PENDING) || "null");
      if (pending && pending.job_id && Date.now() - pending.startedAt < 2 * 3600 * 1000) {
        setModStatus("Mod job " + pending.job_id + " đang chờ kết quả trên Drive — bấm 'Kiểm tra kết quả' để hỏi (cần kết nối Drive lại).");
      }
    } catch (e) { /* bỏ qua */ }
  }

  // ---------------- Gắn UI ----------------

  function on(id, event, fn) {
    var el = $(id);
    if (el) el.addEventListener(event, fn);
  }

  function copyText(text, okMsg) {
    function done() { setStatus(okMsg); }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, function () {
        fallbackCopy(text, done);
      });
    } else {
      fallbackCopy(text, done);
    }
  }

  function fallbackCopy(text, done) {
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); done(); }
    catch (e) { showError("Không sao chép được — bấm 'Tải job.json' thay thế."); }
    ta.remove();
  }

  function init() {
    if (typeof document === "undefined") return;
    // Điền lại job gần nhất + URL worker đã cấu hình ở tab Dịch.
    try {
      var last = JSON.parse(lsGet(LS_LAST_JOB) || "null");
      if (last) {
        var g = $("colab-game-name"); if (g && !g.value) g.value = last.game || "";
        var d = $("colab-drive-input"); if (d && !d.value) d.value = last.drive_file_id || "";
        var w = $("colab-worker-url");
        var p = $("translate-proxy");
        if (w && !w.value) w.value = last.worker_url || (p ? p.value : "");
      }
    } catch (e) { /* job cũ hỏng thì bỏ qua */ }

    on("btn-colab-copy-job", "click", function () {
      var job = currentJob();
      if (job) copyText(jobToJson(job), "Đã sao chép job (" + job.job_id + ") — dán vào ô JOB_JSON trong notebook Colab.");
    });
    on("btn-colab-download-job", "click", function () {
      var job = currentJob();
      if (job) {
        downloadText(jobFileName(job), jobToJson(job));
        setStatus("Đã tải " + jobFileName(job) + " — upload file này vào khung Files của Colab (hoặc để web tự ghi lên Drive ở mục bên dưới).");
      }
    });
    on("btn-colab-open", "click", function () {
      var job = currentJob();
      if (!job) return;
      window.open(NOTEBOOK_URL, "_blank", "noopener");
      if (accessToken) {
        setStatus("Đã mở notebook Colab — trong tab mới: bấm cho phép Drive, job đã có sẵn trên Drive nên không cần dán gì, rồi Run All (Ctrl+F9). Web sẽ tự hỏi kết quả mỗi phút.");
        startAutoPoll(job.job_id);
      } else {
        setStatus("Đã mở notebook Colab — trong tab mới: bấm cho phép Drive, dán job (đã sao chép sẵn? chưa — bấm 'Sao chép job' trước) hoặc upload " +
          jobFileName(job) + ", rồi Run All (Ctrl+F9). Muốn web tự hỏi kết quả thì kết nối Drive ở mục bên dưới trước.");
      }
    });
    on("btn-large-file-colab-job", "click", function () {
      var modal = $("large-file-modal");
      if (modal) modal.hidden = true;
      // Dùng đúng cơ chế chuyển tab có sẵn của app.js.
      if (typeof window.goTool === "function") window.goTool("extract");
      var sec = $("colab-handoff-section");
      if (sec) sec.scrollIntoView({ behavior: "smooth", block: "start" });
      setStatus("Điền link Drive + tên game rồi bấm 'Mở Colab'.");
    });
    on("btn-colab-import-results", "click", function () {
      var input = $("colab-import-file");
      if (input) input.click();
    });
    on("colab-import-file", "change", function (ev) {
      var list = ev.target.files;
      if (!list || !list.length) return;
      var reads = [];
      for (var i = 0; i < list.length; i++) {
        (function (file) {
          reads.push(file.arrayBuffer().then(function (buf) {
            return { name: file.name, blob: new Blob([buf], { type: "application/json" }) };
          }));
        })(list[i]);
      }
      Promise.all(reads).then(function (files) {
        if (dispatchToMod(files)) {
          setStatus("Đã nhập " + files.length + " file vào mục Mod (phần B) — mở tab Mod để sửa.");
        } else {
          setStatus("Trình duyệt không cho nạp file tự động — dùng nút 'Nhập file .json…' trong mục Mod (phần B).");
        }
      });
      ev.target.value = "";
    });
    on("btn-colab-check-results", "click", function () {
      var job = currentJob();
      if (!job) return;
      setStatus("Đang hỏi Drive xem job " + job.job_id + " xong chưa…");
      connectDrive().then(function () {
        return pollOnce(job.job_id);
      }).then(function (r) {
        if (r.status === "done") {
          setStatus("Colab xong rồi — đang tải kết quả về…");
          return downloadResults(r.manifest).then(importDownloaded);
        }
        setStatus("Colab chưa xong (trạng thái: " + r.status + ") — cứ để tab mở, web sẽ hỏi lại mỗi phút.");
        startAutoPoll(job.job_id);
      }).catch(function (err) { showError(err.message || String(err)); });
    });
    on("btn-colab-pick-ipa", "click", function () {
      var picker = $("colab-ipa-file");
      if (picker) picker.click();
    });
    on("colab-ipa-file", "change", function (ev) {
      var f = ev.target.files && ev.target.files[0];
      if (f) {
        setStatus("Đã chọn: " + f.name + " (" + (f.size / 1048576).toFixed(1) +
          " MB) — bấm 'Tải file lên Drive' (có thể mất nhiều phút, cứ để tab mở).");
      }
    });
    on("btn-colab-upload-drive", "click", function () {
      var job = currentJob();
      if (!job) return;
      var picker = $("colab-ipa-file");
      if (!picker) { showError("Thiếu ô chọn file IPA."); return; }
      if (!picker.files || !picker.files.length) {
        showError("Chọn file .ipa trên máy trước (nút 'Chọn file .ipa…'). File lớn nên upload có thể mất nhiều phút — cứ để tab mở.");
        return;
      }
      var file = picker.files[0];
      setStatus("Đang kết nối Drive…");
      connectDrive().then(function () {
        return ensureFolder(JOB_DIR_NAME);
      }).then(function (rootId) {
        setStatus("Đang tải " + file.name + " lên Drive (0%)…");
        return uploadFileResumable(file, rootId, function (done, total) {
          setStatus("Đang tải " + file.name + " lên Drive (" +
            Math.round(100 * done / total) + "%)…");
        });
      }).then(function (meta) {
        job.drive_file_id = meta.id;
        job.game_file_name = file.name;
        saveLastJob(job);
        setStatus("Đã tải xong — đang ghi job lên Drive…");
        return writeJobToDrive(job);
      }).then(function () {
        setStatus("Xong: file + job đã lên Drive. Bấm 'Mở Colab' — notebook sẽ tự đọc job, không cần dán gì.");
      }).catch(function (err) { showError(err.message || String(err)); });
    });
    on("btn-colab-save-client-id", "click", function () {
      var el = $("colab-oauth-client-id");
      var v = el ? el.value.trim() : "";
      if (!v) { showError("Chưa nhập OAuth client ID."); return; }
      lsSet(LS_CLIENT_ID, v);
      setStatus("Đã lưu OAuth client ID (chỉ trong trình duyệt này). Bấm 'Kết nối Drive' để cấp quyền.");
    });
    // Job đang chờ sau khi tải lại trang: token Google chỉ giữ trong bộ
    // nhớ nên tải lại là mất — không tự poll, chỉ nhắc người dùng bấm
    // 'Kiểm tra kết quả' (nút này sẽ kết nối Drive lại).
    try {
      var pending = JSON.parse(lsGet(LS_PENDING) || "null");
      if (pending && pending.job_id && Date.now() - pending.startedAt < 2 * 3600 * 1000) {
        setStatus("Job " + pending.job_id + " đang chờ kết quả trên Drive — bấm 'Kiểm tra kết quả' để hỏi (cần kết nối Drive lại).");
      }
    } catch (e) { /* bỏ qua */ }

    // Panel Mod job (tab Mod > mục Colab, đường dự phòng khi Actions
    // không trả được IPA repack >400 MB) — cùng máy móc Drive/poll.
    initMod();
  }

  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", init);
    } else {
      init();
    }
  }

  // Xuất hàm thuần để test bằng node (trình duyệt không có module).
  if (typeof module !== "undefined" && module.exports) {
    module.exports = {
      parseDriveFileId: parseDriveFileId,
      slugifyGame: slugifyGame,
      newJobId: newJobId,
      buildJob: buildJob,
      buildModJob: buildModJob,
      jobFileName: jobFileName,
      jobToJson: jobToJson,
      chunkRanges: chunkRanges,
      findManifestForJob: findManifestForJob,
      manifestResultFiles: manifestResultFiles,
      modManifestResultFiles: modManifestResultFiles,
      multipartBody: multipartBody,
      NOTEBOOK_URL: NOTEBOOK_URL,
      MANIFEST_NAME: MANIFEST_NAME
    };
  }

  if (typeof window !== "undefined") {
    window.ColabHandoff = {
      buildJob: buildJob,
      buildModJob: buildModJob,
      parseDriveFileId: parseDriveFileId,
      jobToJson: jobToJson
    };
  }
})();
