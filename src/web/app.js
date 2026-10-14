"use strict";

const FILTERS = {
  resolution: ["1080p", "2160p", "720p"],
  source: ["BDRip", "WEB-DL", "WEBRip"],
  codec: ["HEVC", "H.264", "AV1"],
  sub_lang: ["zh-hans", "zh-hant", "en"],
};
const LABELS = {
  resolution: "分辨率", source: "来源", codec: "编码", sub_lang: "字幕",
};
const SUB_LABEL = { "zh-hans": "简中", "zh-hant": "繁中", "en": "英文" };
const SOURCE_LABEL = { cache: "本地缓存", bangumi: "Bangumi", nekobt: "nekoBT 模糊搜索" };

const state = {
  savePath: "",      // 本次下载用的目录，空 = 用默认
  defaultPath: "",   // 服务端的默认目录
  candidates: [],
  selected: null,
  searchMeta: null,
  //: 搜索来源：nekobt / mikan（注意与下方 active.source 区分，后者是 BDRip 这类片源类型）
  source: "nekobt",
  searchTitle: "",
  assrtQuery: "",
  assrtCount: 0,
  data: null,
  tab: "matched",
  active: { resolution: [], source: [], codec: [], sub_lang: [] },
};

const $ = (id) => document.getElementById(id);

async function api(path, options) {
  const res = await fetch(path, options);
  let body = null;
  try { body = await res.json(); } catch (e) { /* 非 JSON */ }
  if (!res.ok) {
    // 404 基本都是「服务还是旧代码」——把话说明白，别让人猜
    if (res.status === 404) {
      throw new Error(
        "接口不存在（服务可能是旧版本）。请重启服务：关掉终端里的进程，重新运行 .\\run_web.ps1 -Reload"
      );
    }
    throw new Error(describeApiError(body, res));
  }
  return body;
}

/**
 * 把后端的错误变成人能读的字符串。
 *
 * FastAPI 的参数校验失败返回 {detail: [{loc, msg, type}, ...]}，
 * 早期直接 new Error(detail) 会被拼成「[object Object]」——信息全丢，
 * 排查时只能靠猜。这里显式处理数组与对象两种情况。
 */
function describeApiError(body, res) {
  const detail = body && body.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const parts = detail.map((item) => {
      const where = Array.isArray(item && item.loc) ? item.loc.join(".") : "";
      return `${where} ${(item && item.msg) || ""}`.trim();
    });
    return parts.filter(Boolean).join("；") || JSON.stringify(detail);
  }
  if (detail) return JSON.stringify(detail);
  return `${res.status} ${res.statusText}`;
}

function toast(message, kind) {
  const el = document.createElement("div");
  el.className = "toast" + (kind ? " " + kind : "");
  el.textContent = message;
  $("toasts").appendChild(el);
  setTimeout(() => el.remove(), kind === "bad" ? 9000 : 5000);
}

/* ---------------------------------------------------------------- 健康状态 */

async function loadHealth() {
  const el = $("health");
  el.className = "status";
  el.querySelector(".txt").textContent = "检查中…";
  try {
    const r = await api("/api/health");
    const n = r.nekobt || {};
    const b = r.bangumi || {};
    const parts = [
      `nekoBT ${n.ok ? "正常" : "异常"}`,
      `Bangumi ${b.ok ? "正常" : "不可用"}`,
      "qBittorrent 下载时按需启动",
    ];
    el.className = "status " + (r.ok ? "ok" : "bad");
    el.querySelector(".txt").textContent = parts.join(" · ");
    if (!r.ok) {
      if (!n.ok && n.error) toast("nekoBT：" + n.error, "bad");
    }
  } catch (err) {
    el.className = "status bad";
    el.querySelector(".txt").textContent = "自检失败：" + err.message;
  }
}

/* -------------------------------------------------------------- 本季连载 */

async function loadCalendar(refresh) {
  const box = $("calendar");
  box.innerHTML = '<div class="empty">加载中…</div>';
  try {
    const r = await api("/api/calendar" + (refresh ? "?refresh=true" : ""));
    if (!r.ok) {
      box.innerHTML = "";
      const note = document.createElement("div");
      note.className = "empty";
      note.textContent = r.hint || r.error || "暂时拿不到本季连载";
      box.appendChild(note);
      $("calCount").textContent = "";
      return;
    }
    renderCalendar(r.days || []);
  } catch (err) {
    box.innerHTML = "";
    const note = document.createElement("div");
    note.className = "empty";
    note.textContent = "加载失败：" + err.message;
    box.appendChild(note);
  }
}

function renderCalendar(days) {
  const box = $("calendar");
  box.innerHTML = "";
  const total = days.reduce((sum, day) => sum + (day.items || []).length, 0);
  $("calCount").textContent = total ? `${total} 部` : "";
  days
    .slice()
    .sort((a, b) => (a.weekday || 0) - (b.weekday || 0))
    .forEach((day) => {
      if (!(day.items || []).length) return;
      const row = document.createElement("div");
      row.className = "cal-row";
      const title = document.createElement("div");
      title.className = "cal-row-title";
      title.textContent = WEEKDAY_LABEL[day.weekday] || day.weekday_cn || "未知";
      row.appendChild(title);

      // 这一行横向滑动，避免整块被拉得很高
      const strip = document.createElement("div");
      strip.className = "cal-strip";
      day.items.forEach((item) => strip.appendChild(calendarItem(item)));
      enableHorizontalWheel(strip);
      row.appendChild(strip);
      box.appendChild(row);
    });
}

//: 周一到周日的短标签（Bangumi 的 weekday：1=周一 … 7=周日）
const WEEKDAY_LABEL = { 1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 7: "周日" };

/** 让鼠标滚轮在这一行里变成横向滑动（Windows 上原生要按 Shift+滚轮）。 */
function enableHorizontalWheel(strip) {
  strip.addEventListener(
    "wheel",
    (event) => {
      if (event.deltaY === 0) return;
      event.preventDefault();
      strip.scrollLeft += event.deltaY;
    },
    { passive: false }
  );
}

function coverEl(url, className) {
  if (url) {
    const img = document.createElement("img");
    img.className = className;
    img.loading = "lazy";
    img.alt = "";
    img.referrerPolicy = "no-referrer";
    img.src = url;
    return img;
  }
  const box = document.createElement("div");
  box.className = className + " placeholder";
  box.textContent = "无图";
  return box;
}

function calendarItem(item) {
  const el = document.createElement("div");
  el.className = "cal-item";
  const label = item.name_cn || item.name || "";
  el.title = `${label}\n（点击搜索这部作品）`;
  el.appendChild(coverEl(item.image, "cover"));
  const name = document.createElement("div");
  name.className = "cal-name";
  name.textContent = label;
  el.appendChild(name);
  el.onclick = () => {
    $("q").value = item.name_cn || item.name || "";
    doSearch();
    window.scrollTo({ top: 0, behavior: "smooth" });
  };
  return el;
}

/* ------------------------------------------------------------------ 搜索 */

async function doSearch() {
  const title = $("q").value.trim();
  if (!title) { toast("请输入动漫名称"); return; }
  state.searchTitle = title;
  state.source = ($("sourceSelect") && $("sourceSelect").value) || "nekobt";
  localStorage.setItem("searchSource", state.source);
  const btn = $("searchBtn");
  btn.disabled = true; btn.textContent = "搜索中…";
  try {
    if (state.source === "mikan") {
      // 蜜柑没有「作品识别」这一步：按标题直接搜 Release
      state.candidates = [];
      state.selected = null;
      state.searchMeta = null;
      $("candidatesCard").classList.add("hidden");
      $("filtersCard").classList.remove("hidden");
      $("searchHint").textContent = `来源：蜜柑（中文站，偏老片源） · 标题检索「${title}」`;
      await loadReleases();
      return;
    }
    const r = await api("/api/search?title=" + encodeURIComponent(title));
    state.candidates = r.candidates || [];
    state.searchMeta = r;
    state.selected = null;
    renderCandidates();
    const bits = [`识别来源：${SOURCE_LABEL[r.source] || r.source || "未知"}`];
    if (r.normalized_query && r.normalized_query !== r.query) {
      bits.push(`检索用标题：${r.normalized_query}（已剥离季数后缀）`);
    }
    bits.push(`候选 ${state.candidates.length} 个`);
    $("searchHint").textContent = bits.join(" · ");
    (r.notes || []).forEach((note) => toast(note));
    if (!state.candidates.length) toast("没有找到对应作品");
  } catch (err) {
    toast("搜索失败：" + err.message, "bad");
  } finally {
    btn.disabled = false; btn.textContent = "搜索";
  }
}

function renderCandidates() {
  const box = $("candidates");
  box.innerHTML = "";
  $("candCount").textContent = state.candidates.length;
  const usable = state.candidates.filter((c) => c.media_id && c.confidence !== "low");
  const rest = state.candidates.filter((c) => !(c.media_id && c.confidence !== "low"));
  const addGroup = (title, items) => {
    if (!items.length) return;
    const heading = document.createElement("div");
    heading.className = "group-title";
    heading.textContent = title;
    box.appendChild(heading);
    items.forEach((item) => box.appendChild(candidateEl(item)));
  };
  addGroup("可信候选", usable);
  addGroup("低置信度 / 未匹配（多半是噪声，仅供参考）", rest);
  $("candidatesCard").classList.remove("hidden");
}

function candidateEl(c) {
  const el = document.createElement("div");
  el.className = "candidate";
  if (!c.media_id) el.classList.add("disabled");
  const row = document.createElement("div");
  row.className = "candidate-inner";
  if (c.image) row.appendChild(coverEl(c.image, "cover"));
  const info = document.createElement("div");
  info.className = "candidate-info";
  const meta = [
    c.media_id || "nekoBT 无对应媒体",
    c.year || "年份未知",
    `相似度 ${(c.similarity || 0).toFixed(4)}`,
    SOURCE_LABEL[c.origin] || c.origin || "",
  ].filter(Boolean);
  const title = document.createElement("div");
  title.className = "t";
  title.textContent = c.name_cn ? `${c.name_cn}｜${c.title}` : c.title;
  if (c.last_choice) {
    const badge = document.createElement("span");
    badge.className = "badge last-choice";
    badge.textContent = "上次选择";
    badge.title = "上次你选的就是这一条，可以改选其它候选";
    title.appendChild(badge);
  }
  info.appendChild(title);
  const metaEl = document.createElement("div");
  metaEl.className = "m";
  metaEl.textContent = meta.join(" · ");
  info.appendChild(metaEl);
  if (c.name && c.name !== c.title) {
    const original = document.createElement("div");
    original.className = "m";
    original.textContent = "原名：" + c.name;
    info.appendChild(original);
  }
  row.appendChild(info);
  el.appendChild(row);
  if (c.media_id || c.needs_bridge) {
    el.onclick = () => selectCandidate(c, el);
  }
  return el;
}

async function selectCandidate(candidate, el) {
  document.querySelectorAll(".candidate").forEach((n) => n.classList.remove("active"));
  el.classList.add("active");

  // 惰性桥接：Bangumi 候选在搜索阶段还没有 media_id，点选时才解析
  if (!candidate.media_id && candidate.needs_bridge) {
    const original = el.querySelector(".m");
    if (original) original.textContent = "正在解析 nekoBT 资源…";
    try {
      const r = await api("/api/bridge", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: candidate.name }),
      });
      if (!r.ok) {
        toast(r.error || "解析失败", "bad");
        if (original) original.textContent = "未找到 nekoBT 资源";
        el.classList.remove("active");
        return;
      }
      candidate.media_id = r.media_id;
      candidate.similarity = r.similarity;
      candidate.anilist_id = r.anilist_id;
      if (r.image && !candidate.image) candidate.image = r.image;
    } catch (err) {
      toast("解析失败：" + err.message, "bad");
      el.classList.remove("active");
      return;
    }
  }
  if (!candidate.media_id) {
    toast("这个候选没有对应的 nekoBT 资源");
    return;
  }
  state.selected = candidate;
  // 把这次确认沉淀到本地映射，下次同一输入直接命中缓存
  try {
    await api("/api/mapping", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        input_title: (state.searchMeta && state.searchMeta.query) || candidate.name_cn || candidate.title,
        media_id: candidate.media_id,
        anilist_id: candidate.anilist_id,
        bangumi_id: candidate.bangumi_id,
        confidence: candidate.similarity,
      }),
    });
  } catch (err) {
    /* 缓存写入失败不影响主流程 */
  }
  $("filtersCard").classList.remove("hidden");
  $("historyCard").classList.remove("hidden");
  await loadReleases();
  await loadHistory();
}

/* ------------------------------------------------------------------ 筛选 */

function renderFilters() {
  const box = $("filterGroups");
  box.innerHTML = "";
  Object.keys(FILTERS).forEach((group) => {
    const row = document.createElement("div");
    row.className = "filter-row";
    const label = document.createElement("span");
    label.className = "label";
    label.textContent = LABELS[group];
    row.appendChild(label);
    FILTERS[group].forEach((value) => {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.textContent = SUB_LABEL[value] || value;
      chip.onclick = () => {
        const list = state.active[group];
        const i = list.indexOf(value);
        if (i >= 0) list.splice(i, 1); else list.push(value);
        chip.classList.toggle("on");
        loadReleases();
      };
      row.appendChild(chip);
    });
    box.appendChild(row);
  });
}

function buildQuery() {
  const params = new URLSearchParams();
  Object.keys(state.active).forEach((group) => {
    state.active[group].forEach((v) => params.append(group, v));
  });
  const batch = $("batch").value;
  if (batch !== "") params.append("batch", batch);
  const minSeeders = $("minSeeders").value;
  if (minSeeders !== "") params.append("min_seeders", minSeeders);
  params.append("sort", $("sort").value);
  return params;
}

async function loadReleases() {
  const card = $("resultsCard");
  card.classList.remove("hidden");
  card.querySelector("tbody").innerHTML = '<tr><td colspan="10" class="empty">加载中…</td></tr>';
  try {
    let r;
    if (state.source === "mikan") {
      if (!state.searchTitle) return;
      r = await api(
        "/api/mikan/releases?title=" +
          encodeURIComponent(state.searchTitle) +
          "&" +
          buildQuery().toString()
      );
      if (r.ok === false) toast("蜜柑搜索失败：" + (r.error || ""), "bad");
    } else {
      if (!state.selected) return;
      r = await api("/api/media/" + state.selected.media_id + "/releases?" + buildQuery().toString());
    }
    state.data = r;
    renderResults();
  } catch (err) {
    toast("获取 Release 失败：" + err.message, "bad");
    card.querySelector("tbody").innerHTML = '<tr><td colspan="10" class="empty">加载失败</td></tr>';
  }
}

function renderResults() {
  const r = state.data;
  const c = r.counts;
  $("resultSummary").textContent =
    `符合 ${c.matched} · 字段未知 ${c.unknown} · 不符合 ${c.excluded}（共 ${r.total}）`;

  const tabs = $("tabs");
  tabs.innerHTML = "";
  [["matched", "符合条件"], ["unknown", "字段未知"], ["excluded", "不符合"]].forEach(([key, text]) => {
    const el = document.createElement("span");
    el.className = "tab" + (state.tab === key ? " on" : "");
    el.textContent = `${text}（${c[key]}）`;
    el.onclick = () => { state.tab = key; renderResults(); };
    tabs.appendChild(el);
  });

  const rows = r[state.tab] || [];
  const tbody = $("releases").querySelector("tbody");
  tbody.innerHTML = "";
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="10" class="empty">这个分组里没有结果</td></tr>';
    return;
  }
  rows.forEach((item, index) => tbody.appendChild(renderRow(item, index)));
}

/** 毫秒时间戳 → 「2024-05-01」。来源没给时间就返回空串，让调用方过滤掉。 */
function formatDate(ms) {
  if (!ms) return "";
  const d = new Date(ms);
  if (isNaN(d.getTime())) return "";
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function renderRow(item, index) {
  const rel = item.release;
  const tr = document.createElement("tr");

  const subs = (rel.sub_lang || []).map((l) => {
    const isZh = l === "zh-hans" || l === "zh-hant";
    return `<span class="tag${isZh ? " zh" : ""}">${SUB_LABEL[l] || l}</span>`;
  }).join("") || '<span class="muted">未知</span>';

  const titleTd = document.createElement("td");
  titleTd.className = "title-cell";
  const name = document.createElement("div");
  name.className = "name";
  name.textContent = rel.title;
  const meta = document.createElement("div");
  meta.className = "meta";
  meta.textContent = [
    rel.group ? "组：" + rel.group : null,
    rel.episode ? "EP " + rel.episode : null,
    rel.season ? "S" + rel.season : null,
    rel.upgraded ? "升级版" : null,
    // 发布时间：既方便挑最新的，也是「按发布时间排序」的肉眼校验
    formatDate(rel.uploaded_at) || null,
  ].filter(Boolean).join(" · ");
  titleTd.appendChild(name);
  if (meta.textContent) titleTd.appendChild(meta);

  tr.innerHTML =
    `<td class="muted">${index + 1}</td>`;
  tr.appendChild(titleTd);
  tr.insertAdjacentHTML("beforeend",
    `<td>${rel.resolution || '<span class="muted">?</span>'}</td>` +
    `<td>${rel.source || '<span class="muted">Unknown</span>'}</td>` +
    `<td>${rel.video_codec || '<span class="muted">?</span>'}</td>` +
    `<td>${subs}</td>` +
    `<td>${rel.size_text || "?"}</td>` +
    `<td>${rel.seeders !== null && rel.seeders !== undefined ? rel.seeders : "?"}</td>` +
    `<td class="score">${item.score === null ? "—" : item.score}</td>`);

  const actionTd = document.createElement("td");
  const btn = document.createElement("button");
  btn.className = "ghost";
  btn.textContent = "下载";
  btn.onclick = () => doDownload(rel, btn);
  actionTd.appendChild(btn);
  tr.appendChild(actionTd);
  return tr;
}

async function doDownload(rel, btn) {
  btn.disabled = true;
  btn.textContent = "提交中…";
  try {
    const r = await api("/api/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        torrent_id: rel.torrent_id || null,
        torrent_url: rel.torrent_url || null,
        infohash: rel.infohash,
        title: rel.title,
        save_path: state.savePath || null,
        paused: $("pausedAdd").checked,
      }),
    });
    btn.textContent = "已提交";
    const launchNote = r.launched_client ? "（已自动启动 qBittorrent）" : "";
    toast(
      `已添加到 qBittorrent${launchNote}：${r.filename}\n保存目录：${r.save_path}` +
      (r.paused ? "（暂停中，需手动开始）" : ""),
      "ok"
    );
    loadHistory();
  } catch (err) {
    btn.disabled = false;
    btn.textContent = "下载";
    toast("提交失败：" + err.message, "bad");
  }
}

/* -------------------------------------------------------------- 偏好与历史 */

async function savePrefs() {
  const payload = {
    resolutions: state.active.resolution,
    sources: state.active.source,
    video_codecs: state.active.codec,
    sub_langs: state.active.sub_lang,
    require_batch: $("batch").value === "" ? null : $("batch").value === "true",
    min_seeders: $("minSeeders").value === "" ? null : Number($("minSeeders").value),
  };
  try {
    await api("/api/preferences", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    toast("已保存为默认偏好", "ok");
  } catch (err) {
    toast("保存失败：" + err.message, "bad");
  }
}

async function loadHistory() {
  try {
    const r = await api("/api/downloads?limit=20");
    const tbody = $("history").querySelector("tbody");
    tbody.innerHTML = "";
    if (!r.items.length) {
      tbody.innerHTML = '<tr><td colspan="5" class="empty">还没有下载记录</td></tr>';
      return;
    }
    r.items.forEach((row) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td class="muted">${row.created_at || ""}</td>
        <td></td>
        <td class="muted">${row.save_path || ""}</td>
        <td>${row.status || ""}</td>
        <td class="muted">${(row.torrent_hash || "").slice(0, 12)}</td>`;
      tr.children[1].textContent = row.title || "";
      tbody.appendChild(tr);
    });
  } catch (err) {
    /* 历史加载失败不打扰用户 */
  }
}

/* -------------------------------------------------------------------- 启动 */

function resetFilters() {
  state.active = { resolution: [], source: [], codec: [], sub_lang: [] };
  $("batch").value = "";
  $("minSeeders").value = "";
  $("sort").value = "preference";
  renderFilters();
  loadReleases();
}

window.addEventListener("DOMContentLoaded", () => {
  renderFilters();
  loadHealth();
  applyCalendarCollapsed(localStorage.getItem("calendarCollapsed") === "1");
  loadCalendar(false);
  loadLibrary();
  loadHistory();
  $("historyCard").classList.remove("hidden");
  // 恢复上次选择的搜索来源
  const savedSource = localStorage.getItem("searchSource");
  if (savedSource && $("sourceSelect")) $("sourceSelect").value = savedSource;
  $("searchBtn").onclick = doSearch;
  $("q").addEventListener("keydown", (e) => { if (e.key === "Enter") doSearch(); });
  $("batch").onchange = loadReleases;
  $("sort").onchange = loadReleases;
  $("minSeeders").onchange = loadReleases;
  $("resetFilters").onclick = resetFilters;
  $("savePrefs").onclick = savePrefs;
  $("loadHistory").onclick = loadHistory;
  $("reloadCalendar").onclick = () => loadCalendar(true);
  $("toggleCalendar").onclick = () => {
    const collapsed = !$("calendar").classList.contains("collapsed");
    applyCalendarCollapsed(collapsed);
    localStorage.setItem("calendarCollapsed", collapsed ? "1" : "0");
    $("toggleCalendar").textContent = collapsed ? "展开" : "收起";
  };
  $("subProbe").onclick = probeSubtitles;
  $("subMerge").onclick = mergeSubtitles;
  loadDownloadPath();
  $("savePathDefault").onclick = saveDownloadPathAsDefault;
  $("savePathBtn").onclick = openPathDialog;
  $("pathUseCurrent").onclick = () => chooseSavePath(browsePath);
  $("pathUseManual").onclick = () => chooseSavePath($("pathManual").value.trim());
  $("pathCancel").onclick = () => $("pathDialog").close();
  $("assrtSearch").onclick = searchAssrt;
  $("assrtMore").onclick = loadMoreAssrt;
  $("batchCancel").onclick = () => $("batchDialog").close();
  $("batchLoadVideos").onclick = loadBatchVideos;
  $("batchRun").onclick = runBatchEmbed;
  $("batchPickDir").onclick = () =>
    openPathDialog(async (dir) => {
      $("batchDir").value = dir;
      await loadBatchVideos();
    });
});

/* ------------------------------------------------------------ 批量嵌入字幕 */
/*
  左：字幕文件（射手网条目可能是整季打包）  右：视频文件（某个文件夹下的 .mkv）
  两边都按【勾选顺序】编号，第 N 个配第 N 个；数量不一致直接拒绝，避免错位。
*/

let batchSubs = [];      // 左侧全部候选
let batchVideos = [];    // 右侧全部候选
let batchSubsSel = [];   // 已勾选的字幕（按顺序）
let batchVideosSel = []; // 已勾选的视频（按顺序）

function openBatchDialog() {
  // 目标视频填过的话，默认用它的目录
  if (!$("batchDir").value) {
    const target = $("subTarget").value.trim();
    if (target) {
      const parts = target.split(/[\\/]/);
      parts.pop();
      $("batchDir").value = parts.join("\\");
    }
  }
  updateBatchSummary();
  $("batchDialog").showModal();
}

function updateBatchSummary() {
  const subs = batchSubsSel.length;
  const vids = batchVideosSel.length;
  if (!subs && !vids) {
    $("batchSummary").textContent = "左右各勾选一样多的文件，按顺序一一配对";
    return;
  }
  const pairs = Math.min(subs, vids);
  const warn = subs === vids ? "" : ` ⚠ 数量不一致（差 ${Math.abs(subs - vids)} 个）`;
  $("batchSummary").textContent = `字幕 ${subs} 个 / 视频 ${vids} 个${warn}${
    warn ? "" : `　将配对 ${pairs} 组`
  }`;
}

function renderBatchList(box, items, selected, onToggle) {
  box.innerHTML = "";
  if (!items.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "（还没有内容）";
    box.appendChild(empty);
    return;
  }
  items.forEach((item) => {
    const row = document.createElement("label");
    row.className = "batch-row";
    const check = document.createElement("input");
    check.type = "checkbox";
    check.checked = selected.includes(item);
    const badge = document.createElement("span");
    badge.className = "n";
    const name = document.createElement("span");
    name.className = "f";
    name.textContent = item.label;
    name.title = item.label;
    row.appendChild(check);
    row.appendChild(badge);
    row.appendChild(name);
    check.onchange = () => {
      if (check.checked) selected.push(item);
      else selected.splice(selected.indexOf(item), 1);
      row.classList.toggle("on", check.checked);
      refreshBatchBadges(box, selected);
      onToggle();
    };
    row.classList.toggle("on", check.checked);
    box.appendChild(row);
  });
  refreshBatchBadges(box, selected);
}

/** 把「第几个勾选」显示到行首的圆标上——配对顺序就是它。 */
function refreshBatchBadges(box, selected) {
  const rows = box.querySelectorAll(".batch-row");
  rows.forEach((row, index) => {
    const item = box === $("batchSubs") ? batchSubs[index] : batchVideos[index];
    const badge = row.querySelector(".n");
    const position = selected.indexOf(item);
    badge.textContent = position >= 0 ? String(position + 1) : "";
  });
}


async function loadBatchVideos() {
  const dir = $("batchDir").value.trim();
  if (!dir) {
    toast("请填视频所在的文件夹");
    return;
  }
  try {
    const r = await api("/api/videos?path=" + encodeURIComponent(dir));
    if (!r.ok) {
      toast("载入失败：" + (r.error || ""), "bad");
      return;
    }
    batchVideos = (r.files || []).map((f) => ({
      label: f.name,
      path: f.path,
    }));
    batchVideosSel = [];
    renderBatchList($("batchVideos"), batchVideos, batchVideosSel, updateBatchSummary);
    if (!batchVideos.length) toast("这个文件夹里没有 .mkv");
  } catch (err) {
    toast("载入失败：" + err.message, "bad");
  }
}

/* 一次请求里嵌几组。

   嵌一组就要跑一次 mkvmerge，整季十几组装进一个请求的话，界面会长时间停在
   「嵌入中（0/12）…」一动不动，中途出问题还会把整批结果一起丢掉。
   拆成小批逐个请求：进度能一组一组地报，某一批出问题也不牵连其它批。 */
const BATCH_CHUNK = 3;

/**
 * 批量嵌入分两步走：
 *
 *   一、先把选中的字幕全部下到本地（几秒钟的事）；
 *   二、再一组一组地嵌。
 *
 * 下载是唯一受网络和对端影响的步骤（射手网的下载服务器偶发 502）。先集中做完，
 * 坏消息几秒内就能听到——不用等十几个 mkvmerge 跑完才发现第七集压根没字幕；
 * 而且第二阶段全是本地操作，中途网断了也不影响已经开始的嵌入。
 */
async function runBatchEmbed() {
  if (batchSubsSel.length !== batchVideosSel.length || !batchSubsSel.length) {
    toast("两边数量要一样多，且不能为空");
    return;
  }
  const total = batchSubsSel.length;
  const replace = $("batchReplace").checked;
  const btn = $("batchRun");
  btn.disabled = true;
  try {
    // ---- 第一阶段：下字幕 ----
    btn.textContent = `下载字幕（${total} 个）…`;
    let fetched;
    try {
      fetched = await api("/api/subtitle/fetch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          subtitles: batchSubsSel.map((s) => ({ name: s.file_name, url: s.file_url })),
          videos: batchVideosSel.map((v) => v.path),
        }),
      });
    } catch (err) {
      toast("下载字幕失败，没有开始嵌入：" + err.message, "bad");
      return;
    }

    const ready = (fetched.results || []).filter((item) => item.ok);
    const results = (fetched.results || [])
      .filter((item) => !item.ok)
      .map((item) => ({
        index: item.index,
        video: item.video,
        ok: false,
        error: "字幕没下下来：" + item.error,
      }));
    if (!ready.length) {
      toast("一个字幕都没下下来，没有开始嵌入", "bad");
      results.forEach((item) => toast(`第 ${item.index} 组：${item.error}`, "bad"));
      $("batchSummary").textContent = `0/${total} 组：字幕全部下载失败`;
      return;
    }
    if (results.length) {
      toast(`有 ${results.length} 个字幕没下下来，先跳过那几组`, "bad");
    }

    // ---- 第二阶段：嵌入（只嵌字幕已经到位的那些组）----
    for (let start = 0; start < ready.length; start += BATCH_CHUNK) {
      const batch = ready.slice(start, start + BATCH_CHUNK);
      btn.textContent = `嵌入中（${start + 1}–${start + batch.length}/${ready.length}）…`;
      try {
        const r = await api("/api/subtitle/embed-batch", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            // 传第一阶段的本地路径，服务端不会再下一遍
            subtitles: batch.map((p) => ({ name: p.name, path: p.path })),
            videos: batch.map((p) => p.video),
            language: $("subLang").value,
            make_default: $("subDefault").checked,
            replace_original: replace,
          }),
        });
        (r.results || []).forEach((item, i) => {
          // 每批的编号都是从 1 起算的，换回整批里的序号
          results.push({ ...item, index: batch[i].index });
        });
      } catch (err) {
        // 这一批没回话，但服务端很可能还在跑（mkvmerge 是子进程，连接断了它
        // 也不会停）。所以记成「不确定」而不是「失败」，免得让人以为白跑了。
        batch.forEach((pair) => {
          results.push({
            index: pair.index,
            video: pair.video,
            ok: false,
            error: `请求中断（${err.message}）——这一组可能仍在后台嵌，稍后刷新视频文件夹确认`,
          });
        });
      }
    }
    reportBatchResults(results);
  } finally {
    btn.disabled = false;
    btn.textContent = "开始批量嵌入";
  }
}

/** 把整批结果汇总成几句人话：嵌好几组、替换了几组、哪几组有问题。 */
function reportBatchResults(results) {
  const total = results.length;
  const failed = results.filter((item) => !item.ok);
  const replaceFailed = results.filter((item) => item.replace_error);
  const replaced = results.filter((item) => item.replaced).length;
  const ok = total - failed.length;
  const extra = [
    replaced ? `已替换原文件 ${replaced} 组` : null,
    replaceFailed.length ? `有 ${replaceFailed.length} 组没能替换` : null,
  ].filter(Boolean).join("，");

  toast(`完成 ${ok}/${total} 组${extra ? "（" + extra + "）" : ""}`, failed.length ? "bad" : "ok");
  failed.forEach((item) => toast(`第 ${item.index} 组：${item.error}`, "bad"));
  replaceFailed.forEach((item) => toast(`第 ${item.index} 组替换失败：${item.replace_error}`, "bad"));
  $("batchSummary").textContent = `完成 ${ok}/${total} 组${extra ? "，" + extra : ""}`;
  loadLibrary();
  if (!failed.length) $("batchDialog").close();
}

/* ---------------------------------------------------------- 在线字幕（射手网） */

const ASSRT_LANG = { zh_chs: "简", zh_cht: "繁", "zh-hans": "简", "zh-hant": "繁", en: "英", jpn: "日" };

/** 从射手网的语言标记推断 mkvmerge 要的三字母代码。 */
function assrtTrackLanguage(langs) {
  const first = (langs || [])[0] || "";
  if (first.startsWith("zh")) return "chi";
  if (first.startsWith("en")) return "eng";
  if (first.startsWith("ja")) return "jpn";
  return "chi";
}

async function searchAssrt() {
  const box = $("assrtResults");
  const query = $("assrtQuery").value.trim() || $("subTarget").value.trim();
  if (!query) {
    toast("请填片名，或先在上面填「目标视频」");
    return;
  }
  $("assrtHint").textContent = "检索中…";
  box.innerHTML = "";
  try {
    const r = await fetchAssrt(query, 0);
    if (!r.ok) {
      $("assrtHint").textContent = "";
      toast(r.configured === false ? r.error : "检索失败：" + (r.error || ""), "bad");
      return;
    }
    const items = r.items || [];
    $("assrtHint").textContent = `共 ${items.length} 条`;
    state.assrtQuery = query;
    state.assrtCount = items.length;
    // 接口单次上限 15 条，刚好满就说明后面可能还有
    $("assrtMore").classList.toggle("hidden", items.length < 15);
    if (!items.length) {
      toast("射手网没搜到字幕");
      return;
    }
    items.forEach((item) => box.appendChild(assrtRow(item)));
  } catch (err) {
    $("assrtHint").textContent = "";
    toast("检索失败：" + err.message, "bad");
  }
}

async function fetchAssrt(query, pos) {
  return api("/api/subtitle/search", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, count: 15, pos }),
  });
}

/** 单次最多 15 条，靠 pos 往下翻。 */
async function loadMoreAssrt() {
  if (!state.assrtQuery) return;
  const btn = $("assrtMore");
  btn.disabled = true;
  btn.textContent = "加载中…";
  try {
    const r = await fetchAssrt(state.assrtQuery, state.assrtCount);
    if (!r.ok) {
      toast("加载失败：" + (r.error || ""), "bad");
      return;
    }
    const items = r.items || [];
    items.forEach((item) => $("assrtResults").appendChild(assrtRow(item)));
    state.assrtCount += items.length;
    $("assrtHint").textContent = `已加载 ${state.assrtCount} 条`;
    btn.classList.toggle("hidden", items.length < 15);
    if (!items.length) toast("没有更多结果了");
  } catch (err) {
    toast("加载失败：" + err.message, "bad");
  } finally {
    btn.disabled = false;
    btn.textContent = "加载更多";
  }
}

function assrtRow(item) {
  const row = document.createElement("div");
  row.className = "assrt-row";
  const info = document.createElement("div");
  info.className = "a-title";
  const title = document.createElement("div");
  title.textContent = item.title || item.filename || "(无标题)";
  const meta = document.createElement("div");
  meta.className = "a-meta";
  meta.textContent = [
    // 把编号显示出来：点「嵌入」靠的就是它，缺了会当场看得出来
    item.sub_id ? `#${item.sub_id}` : "⚠ 无编号",
    item.lang_label || (item.langs || []).map((c) => ASSRT_LANG[c] || c).join("/"),
    (item.format || "").toUpperCase(),
    // 不显示 API 的 score：实测它与网站上的「字幕评分」对不上（网站是 10 分制、
    // 人数也不同）。一个和用户认知不符的数字，比没有数字更糟。
    null,
    item.upload_time ? item.upload_time.slice(0, 10) : null,
    item.release_site,
  ].filter(Boolean).join(" · ");
  info.appendChild(title);
  info.appendChild(meta);

  // 把接口给的其余信息也摊开：别的名字、修订版、下载/浏览数
  const extra = [];
  // 整包还是单集——老 BD 资源常常只能靠整包配字幕，这个信息很关键
  if (item.is_pack === true || item.is_pack === 1 || item.is_pack === "1") {
    extra.push("📦 整包");
  }
  const aliases = [item.native_name, item.videoname, item.sub_name].filter(
    (name) => name && name !== item.title
  );
  if (aliases.length) extra.push("别名：" + Array.from(new Set(aliases)).join(" / "));
  if (item.revision) extra.push(`修订 ${item.revision}`);
  if (item.down_count) extra.push(`下载 ${item.down_count}`);
  if (item.view_count) extra.push(`浏览 ${item.view_count}`);
  if (extra.length) {
    const more = document.createElement("div");
    more.className = "a-meta";
    more.textContent = extra.join(" · ");
    info.appendChild(more);
  }
  row.appendChild(info);

  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "ghost";
  btn.textContent = "嵌入";
  btn.title = "下载这条字幕并嵌进目标视频";
  btn.onclick = () => openBatchForItem(item);
  row.appendChild(btn);
  return row;
}

async function embedAssrt(item, btn) {
  const target = $("subTarget").value.trim();
  if (!target) {
    toast('请先在上面填「目标视频」的路径');
    return;
  }
  btn.disabled = true;
  btn.textContent = "嵌入中…";
  try {
    const r = await api("/api/subtitle/embed", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        target,
        sub_id: item.sub_id,
        file_name: item.filename || null,
        language: assrtTrackLanguage(item.langs),
        track_name: item.lang_label || null,
        output: $("subOutput").value.trim() || null,
        make_default: $("subDefault").checked,
        replace_original: $("subReplace").checked,
      }),
    });
    if (!r.ok) {
      toast("嵌入失败：" + (r.error || ""), "bad");
      btn.disabled = false;
      btn.textContent = "嵌入";
      return;
    }
    btn.textContent = "已嵌入";
    toast(
      `已生成：${r.output}${r.replaced ? "（已替换原文件）" : ""}\n` +
        `字幕 ${r.subtitle_size} 字节，成片 ${r.output_size_text}`,
      "ok"
    );
    if (r.replace_error) toast("替换原文件没成功：" + r.replace_error, "bad");
    loadLibrary();
  } catch (err) {
    toast("嵌入失败：" + err.message, "bad");
    btn.disabled = false;
    btn.textContent = "嵌入";
  }
}

/* ------------------------------------------------------------ 下载目录 */
/*
  「保存到」是一个弹窗选择器：
  · 点「保存到」→ GET /api/browse 逐层列目录
  · 点文件夹名 = 就选它；点行末 ▸ = 进入这个文件夹
  · 也能直接粘贴完整路径
  选中的路径存在 state.savePath（空 = 用默认目录），并记在 localStorage。
*/

let browsePath = "";

async function loadDownloadPath() {
  try {
    const r = await api("/api/download-path");
    state.defaultPath = r.path || "";
    state.savePath = localStorage.getItem("savePath") || "";
    updateSavePathButton();
    $("pathManual").placeholder = r.path || "";
  } catch (err) {
    /* 拿不到就用默认，提交时后端兜底 */
  }
}

function updateSavePathButton() {
  const btn = $("savePathBtn");
  if (!btn) return;
  btn.textContent = state.savePath || `默认（${state.defaultPath || "..."}）`;
  btn.title = state.savePath || state.defaultPath || "";
}

async function saveDownloadPathAsDefault() {
  try {
    const r = await api("/api/download-path", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: state.savePath || "" }),
    });
    state.defaultPath = r.path || "";
    state.savePath = "";
    localStorage.setItem("savePath", "");
    updateSavePathButton();
    toast(
      r.source === "custom" ? `默认保存目录已改为：${r.path}` : `已恢复默认目录：${r.path}`,
      "ok"
    );
  } catch (err) {
    toast("设置失败：" + err.message, "bad");
  }
}

async function openPathDialog(handler, startPath) {
  // handler 是回调（批量嵌入用它取文件夹）；不传就按「下载目录」处理。
  // 注意：直接挂在 onclick 上时第一个参数是事件对象，所以要判类型。
  pickerHandler = typeof handler === 'function' ? handler : null;
  $("pathManual").value = "";
  await browseTo(state.savePath || state.defaultPath || "");
  $("pathDialog").showModal();
}

async function browseTo(path) {
  try {
    const r = await api("/api/browse" + (path ? "?path=" + encodeURIComponent(path) : ""));
    if (!r.ok) {
      toast("无法打开：" + (r.error || ""), "bad");
      return;
    }
    browsePath = r.path;
    renderBrowse(r);
  } catch (err) {
    toast("浏览失败：" + err.message, "bad");
  }
}

function renderBrowse(r) {
  $("pathCurrent").textContent = r.path;
  const up = $("pathUp");
  up.disabled = !r.parent;
  up.onclick = () => r.parent && browseTo(r.parent);

  const drives = $("pathDrives");
  drives.innerHTML = "";
  (r.drives || []).forEach((drive) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "ghost small";
    btn.textContent = drive;
    btn.onclick = () => browseTo(drive);
    drives.appendChild(btn);
  });

  const list = $("pathList");
  list.innerHTML = "";
  if (!(r.dirs || []).length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "（没有子文件夹，可直接点「选当前目录」）";
    list.appendChild(empty);
  }
  (r.dirs || []).forEach((dir) => {
    const row = document.createElement("div");
    row.className = "pd-row";
    const name = document.createElement("button");
    name.type = "button";
    name.className = "pd-name";
    name.textContent = dir.name;
    name.title = "就选这个：" + dir.path;
    name.onclick = () => chooseSavePath(dir.path);
    const enter = document.createElement("button");
    enter.type = "button";
    enter.className = "ghost small";
    enter.textContent = "▸";
    enter.title = "进入这个文件夹";
    enter.onclick = (event) => {
      event.stopPropagation();
      browseTo(dir.path);
    };
    row.appendChild(name);
    row.appendChild(enter);
    list.appendChild(row);
  });
}

let pickerHandler = null;

function chooseSavePath(path) {
  const handler = pickerHandler;
  pickerHandler = null;
  const dialog = $("pathDialog");
  if (dialog && dialog.open) dialog.close();
  if (handler) {
    handler(path || "");
    return;
  }
  state.savePath = path || "";
  localStorage.setItem("savePath", state.savePath);
  updateSavePathButton();
}

/* ------------------------------------------------------------ 字幕合并 */

const CHINESE_LANGS = ["chi", "zh", "zho", "chs", "cht", "zh-cn", "zh-tw"];

async function loadLibrary() {
  try {
    const r = await api("/api/library");
    const list = $("libraryFiles");
    list.innerHTML = "";
    (r.files || []).forEach((file) => {
      const option = document.createElement("option");
      option.value = file.path;
      option.label = `${file.name}  (${file.size_text})`;
      list.appendChild(option);
    });
    $("libraryHint").textContent = r.ok
      ? `下载目录：${r.root}（找到 ${(r.files || []).length} 个视频文件，输入框里可以直接选）`
      : r.error || "";
  } catch (err) {
    $("libraryHint").textContent = "读取下载目录失败：" + err.message;
  }
}

function isChineseTrack(track) {
  const lang = (track.language || "").toLowerCase();
  const name = (track.name || "").toLowerCase();
  return (
    CHINESE_LANGS.includes(lang) ||
    name.includes("简") || name.includes("繁") || name.includes("中") ||
    name.includes("chs") || name.includes("cht") || name.includes("chi")
  );
}

async function probeSubtitles() {
  const path = $("subSource").value.trim();
  const box = $("subTracks");
  if (!path) {
    toast("请先填「来源视频」的路径");
    return;
  }
  $("subHint").textContent = "读取中…";
  box.innerHTML = "";
  try {
    const r = await api("/api/subtitle/probe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    if (!r.ok) {
      $("subHint").textContent = "";
      toast("读取失败：" + r.error, "bad");
      return;
    }
    const tracks = r.subtitle_tracks || [];
    $("subHint").textContent = `${r.name}：共 ${r.tracks.length} 条轨道，其中字幕 ${tracks.length} 条`;
    if (!tracks.length) {
      toast("这个文件里没有内封字幕");
      return;
    }
    tracks.forEach((track) => box.appendChild(trackRow(track)));
  } catch (err) {
    $("subHint").textContent = "";
    toast("读取失败：" + err.message, "bad");
  }
}

function trackRow(track) {
  const row = document.createElement("label");
  row.className = "track-row";
  const check = document.createElement("input");
  check.type = "checkbox";
  check.value = track.id;
  check.checked = isChineseTrack(track);
  check.onchange = () => row.classList.toggle("on", check.checked);
  row.classList.toggle("on", check.checked);
  row.appendChild(check);

  const name = document.createElement("span");
  name.className = "tname";
  name.textContent = track.name || `轨道 ${track.id}`;
  row.appendChild(name);

  const meta = document.createElement("span");
  meta.className = "tmeta";
  meta.textContent = [
    `#${track.id}`,
    track.language || "und",
    track.codec,
    track.default ? "默认" : null,
    track.forced ? "强制" : null,
  ].filter(Boolean).join(" · ");
  row.appendChild(meta);
  return row;
}

async function mergeSubtitles() {
  const target = $("subTarget").value.trim();
  const source = $("subSource").value.trim();
  const ids = Array.from($("subTracks").querySelectorAll("input:checked")).map((n) =>
    Number(n.value)
  );
  if (!target || !source) {
    toast("目标视频和来源视频都要填");
    return;
  }
  if (!ids.length) {
    toast("请先点「读取来源的字幕轨」，并勾选要搬运的轨道");
    return;
  }
  const btn = $("subMerge");
  btn.disabled = true;
  btn.textContent = "合并中…";
  try {
    const r = await api("/api/subtitle/merge", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        target,
        source,
        track_ids: ids,
        language: $("subLang").value,
        track_name: $("subTrackName").value.trim() || null,
        output: $("subOutput").value.trim() || null,
        make_default: $("subDefault").checked,
        replace_original: $("subReplace").checked,
      }),
    });
    if (!r.ok) {
      toast("合并失败：" + r.error, "bad");
      return;
    }
    toast(
      `已生成：${r.output}${r.replaced ? "（已替换原文件）" : ""}\n体积 ${r.output_size_text}`,
      "ok"
    );
    if (r.replace_error) toast("替换原文件没成功：" + r.replace_error, "bad");
    loadLibrary();
  } catch (err) {
    toast("合并失败：" + err.message, "bad");
  } finally {
    btn.disabled = false;
    btn.textContent = "开始合并";
  }
}

/** 收起/展开连载区：收起后只留标题一行，方便直接够到下面的搜索栏。 */
function applyCalendarCollapsed(collapsed) {
  $("calendar").classList.toggle("collapsed", collapsed);
  $("toggleCalendar").textContent = collapsed ? "展开" : "收起";
}

/** 从搜索结果进入批量嵌入：先把该条目的字幕文件载进来，再让用户配视频。 */
async function openBatchForItem(item) {
  // 没有有效字幕 id 就没法载入文件，先挡住并说清原因（而不是发个空请求换回 422）
  const subId = Number(item && item.sub_id);
  if (!subId) {
    // 把实际收到的字段一并打出来，免得继续靠猜
    const keys = item ? Object.keys(item).join(", ") : "(item 为空)";
    toast(`这条结果没有字幕编号（收到的字段：${keys}）`, "bad");
    return;
  }
  try {
    const r = await api("/api/subtitle/files", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sub_id: subId }),
    });
    if (!r.ok) {
      toast("载入字幕文件失败：" + (r.error || ""), "bad");
      return;
    }
    batchSubs = (r.files || []).map((f) => ({
      label: f.name || f.url,
      file_name: f.name,
      file_url: f.url,
    }));
    // 只有一个文件（单集字幕）就默认勾上，省一次点击
    batchSubsSel = batchSubs.length === 1 ? [batchSubs[0]] : [];
    renderBatchList($("batchSubs"), batchSubs, batchSubsSel, updateBatchSummary);
    $("batchSubHint").textContent = `${r.title || "字幕"}：共 ${batchSubs.length} 个文件`;
    openBatchDialog();
  } catch (err) {
    toast("载入失败：" + err.message, "bad");
  }
}
