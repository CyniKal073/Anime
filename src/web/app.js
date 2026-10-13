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
  candidates: [],
  selected: null,
  searchMeta: null,
  //: 搜索来源：nekobt / mikan（注意与下方 active.source 区分，后者是 BDRip 这类片源类型）
  source: "nekobt",
  searchTitle: "",
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
    const detail = body && body.detail ? body.detail : res.status + " " + res.statusText;
    throw new Error(detail);
  }
  return body;
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
        save_path: ($("savePath") && $("savePath").value.trim()) || null,
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
  $("savePath").onchange = () => localStorage.setItem("savePath", $("savePath").value.trim());
});

/* ------------------------------------------------------------ 下载目录 */

async function loadDownloadPath() {
  try {
    const r = await api("/api/download-path");
    const options = $("savePathOptions");
    options.innerHTML = "";
    (r.suggestions || []).forEach((item) => {
      const option = document.createElement("option");
      option.value = item;
      options.appendChild(option);
    });
    // 优先用上次手填的，其次用服务端的默认
    const remembered = localStorage.getItem("savePath");
    $("savePath").value = remembered || r.path || "";
    $("savePath").title =
      r.source === "custom" ? `默认目录（界面设置）：${r.path}` : `默认目录（.env）：${r.path}`;
  } catch (err) {
    /* 拿不到就用空值，下载时交给后端兜底 */
  }
}

async function saveDownloadPathAsDefault() {
  const path = $("savePath").value.trim();
  try {
    const r = await api("/api/download-path", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    localStorage.setItem("savePath", r.path || "");
    $("savePath").value = r.path || "";
    toast(
      r.source === "custom"
        ? `默认保存目录已改为：${r.path}`
        : `已恢复为 .env 里的默认目录：${r.path}`,
      "ok"
    );
    loadDownloadPath();
  } catch (err) {
    toast("设置失败：" + err.message, "bad");
  }
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
      }),
    });
    if (!r.ok) {
      toast("合并失败：" + r.error, "bad");
      return;
    }
    toast(`已生成：${r.output}\n体积 ${r.output_size_text}`, "ok");
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
