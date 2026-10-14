"""CLI 与 Web UI 共用的业务逻辑层。

这一层只做编排：调用 nekoBT / qBittorrent / Release 解析，返回可直接序列化成
JSON 的普通字典，避免把业务逻辑写进路由或 CLI 里。
"""

from __future__ import annotations

import json
from urllib.parse import unquote
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests

from .config import Settings
from .bangumi.client import BangumiClient, BangumiError
from .nekobt.client import NekoBTClient, NekoBTError
from .nekobt.media import (
    HIGH_SIMILARITY,
    MediaCandidate,
    MediaSearchResult,
    classify_confidence,
    rank_candidates,
    search_media,
)
from .qbittorrent.client import QBittorrentClient, QBittorrentError
from .qbittorrent.launcher import ensure_running
from .mkv.tools import MkvToolError, MkvTools
from .mikan.client import MIKAN_SOURCE, MikanClient, MikanError
from .assrt.client import ASSRT_SOURCE, AssrtClient, AssrtError
from .net import build_session
from .release.matcher import (
    FilterResult,
    ReleasePreferences,
    apply_filter,
    default_preferences,
    rank_releases,
)
from .release.models import Release, human_size
from .release.parser import build_release
from .storage import JsonCache, MediaMappingRecord, Store

#: 存进 settings 表里的默认偏好
PREFS_KEY = "preferences"
#: 新番日历的缓存 key 与有效期（秒）
CALENDAR_KEY = "bangumi:calendar"
CALENDAR_TTL = 1800
#: Bangumi → nekoBT 的桥接结果、以及 Bangumi 搜索结果
BRIDGE_TTL = 7 * 24 * 3600
SEARCH_TTL = 600
#: 用户自定义的下载目录（存在 settings 表里，覆盖 .env 的 QBIT_SAVEPATH）
DOWNLOAD_PATH_KEY = "download_path"
#: 路径里不允许出现的字符。冒号单独判断——盘符必须是 X: 这种形式。
_INVALID_PATH_CHARS = set('<>"|?*')


class TorrentFetchError(RuntimeError):
    """从外部来源下载 .torrent 失败。"""


def effective_save_path(ctx: ServiceContext) -> str:
    """实际使用的下载目录：界面里设过的优先，其次 .env 的 QBIT_SAVEPATH。"""
    return ctx.store.get_setting(DOWNLOAD_PATH_KEY) or ctx.settings.qbit_savepath


def normalize_save_path(raw: Optional[str]) -> Optional[str]:
    """清理用户输入的路径，明显非法就返回 None。"""
    if raw is None:
        return None
    text = raw.strip().strip('"').strip("'")
    if not text:
        return None
    # 盘符（C:）是合法的，冒号只允许出现在这里
    prefix = text[:2] if len(text) >= 2 and text[0].isalpha() and text[1] == ":" else ""
    body = text[len(prefix):]
    if any(ch in _INVALID_PATH_CHARS or ch == ":" for ch in body):
        raise ValueError(f'路径里有 Windows 不允许的字符（< > : " | ? *）：{text}')
    return text.rstrip("\\/") or text


def download_paths(ctx: ServiceContext) -> Dict[str, Any]:
    """下载目录的当前值与候选项（给界面做自动补全）。"""
    current = effective_save_path(ctx)
    options: List[str] = [current]

    # 默认目录下已经存在的子文件夹
    try:
        root = Path(current)
        if root.is_dir():
            for item in sorted(root.iterdir()):
                if item.is_dir():
                    options.append(str(item))
    except OSError:
        pass

    # 历史下载用过的目录
    try:
        for row in ctx.store.list_downloads(50):
            value = row["save_path"]
            if value:
                options.append(value)
    except Exception:  # noqa: BLE001 - 仅仅是补全建议
        pass

    seen: List[str] = []
    for item in options:
        if item and item not in seen:
            seen.append(item)
    return {
        "path": current,
        "source": "custom" if ctx.store.get_setting(DOWNLOAD_PATH_KEY) else "env",
        "env_path": ctx.settings.qbit_savepath,
        "suggestions": seen[:30],
    }


def set_download_path(ctx: ServiceContext, path: str) -> Dict[str, Any]:
    """把下载目录设为默认（存进 settings 表，不动 .env）。"""
    cleaned = normalize_save_path(path)
    if not cleaned:
        ctx.store.set_setting(DOWNLOAD_PATH_KEY, "")
        return {"ok": True, "path": ctx.settings.qbit_savepath, "source": "env"}
    ctx.store.set_setting(DOWNLOAD_PATH_KEY, cleaned)
    return {"ok": True, "path": cleaned, "source": "custom"}


def _list_drives() -> List[str]:
    """Windows 上可用的盘符（非 Windows 会自然返回空）。"""
    drives: List[str] = []
    for letter in "CDEFGHIJ":
        root = f"{letter}:\\"
        try:
            if Path(root).exists():
                drives.append(root)
        except OSError:
            continue
    return drives


def browse_directories(ctx: ServiceContext, path: Optional[str] = None) -> Dict[str, Any]:
    """列出某个目录下的子文件夹，供弹窗里逐层选择。"""
    # Windows 的「复制为路径」会带一对双引号，直接 Path() 会当成非法目录名
    path = (path or "").strip().strip('"').strip("'") or None
    try:
        target = Path(path) if path else Path(effective_save_path(ctx))
    except (TypeError, ValueError):
        return {"ok": False, "error": "路径无效"}

    if not target.is_dir():
        return {
            "ok": False,
            "path": str(target),
            "error": f"目录不存在：{target}",
            "drives": _list_drives(),
        }

    dirs: List[Dict[str, str]] = []
    try:
        for item in sorted(target.iterdir(), key=lambda p: p.name.lower()):
            if item.name.startswith(".") or not item.is_dir():
                continue
            dirs.append({"name": item.name, "path": str(item)})
            if len(dirs) >= 200:
                break
    except OSError as exc:
        return {
            "ok": False,
            "path": str(target),
            "error": f"无法读取目录：{exc}",
            "drives": _list_drives(),
        }

    parent = target.parent
    return {
        "ok": True,
        "path": str(target),
        "name": target.name or str(target),
        "parent": str(parent) if parent != target else None,
        "dirs": dirs,
        "is_default": str(target) == effective_save_path(ctx),
        "env_path": ctx.settings.qbit_savepath,
        "drives": _list_drives(),
    }


@dataclass
class ServiceContext:
    settings: Settings
    nekobt: NekoBTClient
    store: Store
    _qbit: Optional[QBittorrentClient] = field(default=None, repr=False)
    _bangumi: Optional[BangumiClient] = field(default=None, repr=False)
    _cache: Optional[JsonCache] = field(default=None, repr=False)
    _bridge_cache: Optional[JsonCache] = field(default=None, repr=False)
    _search_cache: Optional[JsonCache] = field(default=None, repr=False)
    _mkv: Optional[MkvTools] = field(default=None, repr=False)
    _mikan: Optional[MikanClient] = field(default=None, repr=False)
    _assrt: Optional[AssrtClient] = field(default=None, repr=False)
    #: 自检专用探针（短超时、不重试）；测试里可注入假实现
    nekobt_probe: Optional[Any] = None
    bangumi_probe: Optional[Any] = None

    @property
    def qbit(self) -> QBittorrentClient:
        if self._qbit is None:
            self._qbit = QBittorrentClient(
                self.settings.qbit_url,
                username=self.settings.qbit_username,
                password=self.settings.qbit_password,
                timeout=self.settings.http_timeout,
            )
        return self._qbit

    @property
    def bangumi(self) -> BangumiClient:
        if self._bangumi is None:
            self._bangumi = BangumiClient(
                user_agent=f"{self.settings.user_agent} (https://github.com/CyniKal073)",
                timeout=min(self.settings.http_timeout, 12.0),
            )
        return self._bangumi

    @property
    def cache(self) -> JsonCache:
        if self._cache is None:
            self._cache = JsonCache(self.settings.cache_dir, ttl_seconds=CALENDAR_TTL)
        return self._cache

    @property
    def bridge_cache(self) -> JsonCache:
        """「Bangumi 条目 → nekoBT media_id」的桥接结果，长期有效。"""
        if self._bridge_cache is None:
            self._bridge_cache = JsonCache(self.settings.cache_dir, ttl_seconds=BRIDGE_TTL)
        return self._bridge_cache

    @property
    def search_cache(self) -> JsonCache:
        """Bangumi 搜索结果，短缓存，避免同一关键词反复打接口。"""
        if self._search_cache is None:
            self._search_cache = JsonCache(self.settings.cache_dir, ttl_seconds=SEARCH_TTL)
        return self._search_cache

    @property
    def mkv(self) -> MkvTools:
        if self._mkv is None:
            self._mkv = MkvTools(self.settings.mkv_tools_dir)
        return self._mkv

    @property
    def mikan(self) -> MikanClient:
        if self._mikan is None:
            self._mikan = MikanClient(
                timeout=min(self.settings.http_timeout * 2, 40.0),
                user_agent=f"{self.settings.user_agent} (https://github.com/CyniKal073)",
            )
        return self._mikan

    @property
    def assrt(self) -> AssrtClient:
        if self._assrt is None:
            self._assrt = AssrtClient(
                self.settings.assrt_token,
                user_agent=f"{self.settings.user_agent} (https://github.com/CyniKal073)",
                min_interval=self.settings.assrt_min_interval,
            )
        return self._assrt


def build_context(settings: Optional[Settings] = None) -> ServiceContext:
    settings = settings or Settings.from_env()
    return ServiceContext(
        settings=settings,
        nekobt=NekoBTClient(
            settings.nekobt_base_url,
            api_key=settings.nekobt_api_key,
            timeout=settings.http_timeout,
            retries=settings.http_retries,
            user_agent=settings.user_agent,
        ),
        store=Store(settings.db_path),
    )


def preferences_to_dict(prefs: ReleasePreferences) -> Dict[str, Any]:
    return {
        "resolutions": list(prefs.resolutions),
        "sources": list(prefs.sources),
        "video_codecs": list(prefs.video_codecs),
        "sub_langs": list(prefs.sub_langs),
        "require_batch": prefs.require_batch,
        "min_seeders": prefs.min_seeders,
    }


def preferences_from_dict(data: Optional[Dict[str, Any]]) -> ReleasePreferences:
    data = data or {}
    return ReleasePreferences(
        resolutions=list(data.get("resolutions") or []),
        sources=list(data.get("sources") or []),
        video_codecs=list(data.get("video_codecs") or []),
        sub_langs=list(data.get("sub_langs") or []),
        require_batch=data.get("require_batch"),
        min_seeders=data.get("min_seeders"),
    )


def has_rules(prefs: ReleasePreferences) -> bool:
    return bool(
        prefs.resolutions
        or prefs.sources
        or prefs.video_codecs
        or prefs.sub_langs
        or prefs.require_batch is not None
        or prefs.min_seeders is not None
    )


def load_preferences(store: Store) -> ReleasePreferences:
    """读取用户保存的偏好；没存过就返回空偏好（= 不筛选，只用默认偏好排序）。"""
    raw = store.get_setting(PREFS_KEY)
    if not raw:
        return ReleasePreferences()
    try:
        return preferences_from_dict(json.loads(raw))
    except (ValueError, TypeError):
        return ReleasePreferences()


def save_preferences(store: Store, prefs: ReleasePreferences) -> None:
    store.set_setting(PREFS_KEY, json.dumps(preferences_to_dict(prefs), ensure_ascii=False))


def _year_from_date(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    head = str(value)[:4]
    return int(head) if head.isdigit() else None


def _bridge_to_nekobt(
    ctx: ServiceContext, name: str, *, min_similarity: float = HIGH_SIMILARITY
) -> Tuple[Optional[MediaCandidate], float]:
    """用 Bangumi 的原名去 nekoBT 找对应媒体，返回 (候选, 最高相似度)。

    实测 6/6 精确命中：Bangumi 的 ``name`` 是日文原名，而 nekoBT 的索引里有
    AniList 的 ``native`` 字段，两边是对得上的。

    **必须带相似度门槛**：nekoBT 没有这部作品时，它仍然会返回一堆无关结果
    （实测《妄想学生会》最高只有 0.2857，第一名是《学生会也有洞》）。
    早期版本直接取第一名，于是把用户导向了完全不同的作品。
    """
    if not name:
        return None, 0.0

    cache_key = f"bridge:{name}"
    cached = ctx.bridge_cache.get(cache_key)
    if cached:
        cached_similarity = float(cached.get("similarity") or 0.0)
        # 旧版本没有阈值判断，可能把「最像的一条」当成命中缓存了下来，
        # 所以缓存读取也要过一遍门槛。
        if cached_similarity < min_similarity:
            return None, cached_similarity
        return (
            MediaCandidate(
                media_id=str(cached.get("media_id") or ""),
                title=cached.get("title") or name,
                year=cached.get("year"),
                similarity=cached_similarity,
                anilist_id=cached.get("anilist_id"),
                matched_by="bridge-cache",
            ),
            cached_similarity,
        )

    try:
        rows = ctx.nekobt.search_media(name, limit=3)
    except NekoBTError:
        return None, 0.0
    ranked = rank_candidates(rows)
    if not ranked:
        return None, 0.0

    best = ranked[0]
    best_similarity = best.similarity
    if not best.media_id or best_similarity < min_similarity:
        # 宁可不给结果，也不拿一个别的作品顶替
        return None, best_similarity

    ctx.bridge_cache.set(
        cache_key,
        {
            "media_id": best.media_id,
            "title": best.title,
            "year": best.year,
            "similarity": best.similarity,
            "anilist_id": best.anilist_id,
        },
    )
    return best, best_similarity


def _media_detail(ctx: ServiceContext, media_id: str) -> Dict[str, Any]:
    """取 nekoBT 媒体详情（用于标题与封面），带长期缓存。

    命中本地映射时如果还要为「显示标题」多打一次网络请求，缓存就白做了。
    """
    if not media_id:
        return {}
    cache_key = f"media:{media_id}"
    cached = ctx.bridge_cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        detail = ctx.nekobt.get_media(media_id)
    except Exception:  # noqa: BLE001 - 取不到详情不影响主流程
        return {}
    if detail:
        ctx.bridge_cache.set(
            cache_key,
            {
                "title": detail.get("title"),
                "year": detail.get("year"),
                "banner_url": detail.get("banner_url"),
            },
        )
    return detail


def _bangumi_search(ctx: ServiceContext, title: str, limit: int):
    """查 Bangumi（带 10 分钟缓存）。返回 (subjects, error)，subjects=None 表示不可用。

    这里刻意不用 ``is_available()`` 做前置判断：那个判断有 TTL 记忆，
    一次偶发失败会让接下来 60 秒的所有搜索都白白降级。
    直接尝试、失败再降级更准确，代价也只是同一个请求。
    """
    search_key = f"bangumi:search:{title}:{limit}"
    cached = ctx.search_cache.get(search_key)
    if cached is not None:
        return cached, None
    try:
        subjects = ctx.bangumi.search_subjects(title, limit=limit)
    except BangumiError as exc:
        return None, str(exc)
    ctx.search_cache.set(search_key, subjects)
    return subjects, None


def resolve_work(ctx: ServiceContext, title: str, *, top: int = 5) -> Dict[str, Any]:
    """作品识别：本地缓存 → Bangumi → nekoBT 模糊搜索。

    返回值里的 ``source`` 标明这次走的是哪条路，便于 UI 提示与排查。
    """
    title = (title or "").strip()
    payload: Dict[str, Any] = {
        "query": title,
        "source": None,
        "candidates": [],
        "notes": [],
    }

    # 1) 本地已确认过的映射
    #
    # 注意：它**只在 Bangumi 不可用时**才当作快速通道。
    # 早期版本无条件短路，导致「搜同一个名字只会得到上次选的那一部」——
    # 多季作品（第一季/第二季/特别篇）就再也选不了别的了。
    # 现在它退化为一个标记：正常搜索照做，只是把上次选的那条排到最前并打标。
    cached = ctx.store.get_mapping(title)
    cached_confirmed = bool(cached and cached.confirmed)

    # 2) Bangumi：中文检索的第一入口（直接尝试，失败才算不可用）
    subjects, bangumi_error = _bangumi_search(ctx, title, max(top, 3))
    if subjects is not None:
        candidates: List[Dict[str, Any]] = []
        for rank, subject in enumerate(subjects):
            name = (subject.get("name") or "").strip()
            item = {
                "bangumi_id": subject.get("id"),
                "name_cn": subject.get("name_cn"),
                "name": name,
                "title": subject.get("name_cn") or name or title,
                "year": _year_from_date(subject.get("date")),
                "similarity": None,
                "image": BangumiClient.pick_image(subject.get("images")),
                # media_id 留到用户点选时再解析（惰性桥接）：
                # 搜索阶段只需要 Bangumi 一次请求，冷启动从 5~6s 降到 ~2.2s。
                "media_id": None,
                "needs_bridge": bool(name),
                "anilist_id": None,
                "origin": "bangumi",
                "rank": rank,
                # 相关性由 Bangumi 的排序位置体现
                "confidence": "high" if rank < 2 else "low",
                "matched_by": f"bangumi:{subject.get('id')}",
                "last_choice": bool(cached_confirmed and subject.get("id") == cached.bgm_id),
            }
            candidates.append(item)

        if candidates:
            # 上次选过的那条排最前，其余仍按 Bangumi 的相关度排序
            candidates.sort(key=lambda item: (not item["last_choice"], item["rank"]))
            payload["source"] = "bangumi"
            payload["candidates"] = candidates
            return payload
        payload["notes"].append("Bangumi 没有命中条目，已退回 nekoBT 模糊搜索")
    else:
        payload["notes"].append(f"Bangumi 查询失败（{bangumi_error}），已降级")

    # 2.5) Bangumi 用不了时，先看有没有上次确认过的映射
    if cached_confirmed:
        detail = _media_detail(ctx, cached.nekobt_media_id)
        payload["source"] = "cache"
        payload["notes"].append("Bangumi 不可用，使用上次确认过的映射")
        payload["candidates"] = [
            {
                "media_id": cached.nekobt_media_id,
                "title": detail.get("title") or title,
                "year": detail.get("year"),
                "similarity": 1.0,
                "image": detail.get("banner_url"),
                "anilist_id": cached.anilist_id,
                "bangumi_id": cached.bgm_id,
                "name_cn": None,
                "name": None,
                "origin": "cache",
                "confidence": "high",
                "matched_by": "local-cache",
                "last_choice": True,
            }
        ]
        return payload

    # 3) 兜底：nekoBT 模糊搜索
    result: MediaSearchResult = search_media(ctx.nekobt, title, top=top)
    payload["source"] = "nekobt"
    payload["normalized_query"] = result.normalized_query
    payload["used_fallback"] = result.used_fallback
    payload["tried_queries"] = result.tried_queries
    fallback: List[Dict[str, Any]] = []
    for item in result.candidates:
        data = item.to_dict()
        data["last_choice"] = bool(
            cached_confirmed and item.media_id == cached.nekobt_media_id
        )
        fallback.append(data)
    fallback.sort(key=lambda item: not item["last_choice"])
    payload["candidates"] = fallback
    return payload


def confirm_mapping(
    ctx: ServiceContext,
    *,
    input_title: str,
    media_id: str,
    anilist_id: Optional[int] = None,
    bangumi_id: Optional[int] = None,
    confidence: float = 1.0,
) -> Dict[str, Any]:
    """用户确认候选后写入本地映射，之后同一输入直接命中缓存。"""
    ctx.store.save_mapping(
        MediaMappingRecord(
            input_title=input_title,
            nekobt_media_id=media_id,
            anilist_id=anilist_id,
            bgm_id=bangumi_id,
            confidence=confidence,
            confirmed=True,
        )
    )
    return {"ok": True, "input_title": input_title, "media_id": media_id}


def bridge_candidate(ctx: ServiceContext, name: str) -> Dict[str, Any]:
    """把一个 Bangumi 条目解析成 nekoBT media_id（用户点选时调用）。

    结果会缓存 7 天，所以同一部作品只会真正桥接一次。
    """
    if not name.strip():
        return {"ok": False, "error": "缺少条目原名，无法桥接"}
    bridged, best_similarity = _bridge_to_nekobt(ctx, name.strip())
    if bridged is None or not bridged.media_id:
        reason = (
            f"nekoBT 没有这部作品（最接近的一条相似度只有 {best_similarity:.2f}，"
            f"低于可信阈值 {HIGH_SIMILARITY}）。"
            if best_similarity
            else "nekoBT 里没有这部作品。"
        )
        return {
            "ok": False,
            "media_id": None,
            "best_similarity": round(best_similarity, 4),
            "error": reason + "可以换其它来源，或在 nekoBT 站内确认一下。",
        }
    return {
        "ok": True,
        "media_id": bridged.media_id,
        "title": bridged.title,
        "year": bridged.year,
        "similarity": round(bridged.similarity, 4),
        "anilist_id": bridged.anilist_id,
        "image": bridged.image,
        "cached": bridged.matched_by == "bridge-cache",
    }


def calendar_view(ctx: ServiceContext, *, refresh: bool = False) -> Dict[str, Any]:
    """本季连载（Bangumi 每日放送），带本地缓存。"""
    if not refresh:
        cached = ctx.cache.get(CALENDAR_KEY)
        if cached:
            return {"ok": True, "source": "cache", "days": cached}

    try:
        days = ctx.bangumi.calendar()
    except Exception as exc:  # noqa: BLE001 - 可降级项，不能把首页拖挂
        return {
            "ok": False,
            "source": "bangumi",
            "days": [],
            "error": str(exc)[:300],
            "hint": "Bangumi 不可用（通常需要开启 VPN）；开启后刷新即可看到本季连载。",
        }

    ctx.cache.set(CALENDAR_KEY, days)
    return {"ok": True, "source": "bangumi", "days": days}


def _fetch_torrent(url: str, *, user_agent: str) -> Tuple[bytes, str]:
    """自己把 .torrent 取回来（而不是让 qBittorrent 去拉）。

    这样和 nekoBT 那条路径行为一致：拿到字节后统一以 multipart 上传，
    避免依赖客户端自身的网络与 UA。
    """
    session = build_session()
    try:
        response = session.get(url, headers={"User-Agent": user_agent}, timeout=40)
    except requests.RequestException as exc:
        raise TorrentFetchError(f"下载种子失败：{exc}") from exc
    if response.status_code >= 400:
        raise TorrentFetchError(f"下载种子失败：HTTP {response.status_code}")
    content = response.content
    if not content.startswith(b"d"):
        raise TorrentFetchError("返回的内容不是合法的 .torrent（可能拿到了错误页）")
    filename = unquote(url.rsplit("/", 1)[-1]) or "release.torrent"
    return content, filename


# -------------------------------------------------------- 在线字幕（射手网）

#: 射手网的语言标记 → 界面上的中文标签
ASSRT_LANG_LABEL = {
    "zh_chs": "简体",
    "zh_cht": "繁体",
    "zh-hans": "简体",
    "zh-hant": "繁体",
    "en": "英文",
    "jpn": "日文",
}


def assrt_search(
    ctx: ServiceContext, query: str, *, count: int = 15, pos: int = 0
) -> Dict[str, Any]:
    """在射手网搜字幕。

    接口单次最多返回 15 条（文档写明的 cnt 上限），网站上看到更多是因为它分页。
    要拿后面的结果就调 pos——界面的「加载更多」走的就是这条路。
    """
    if not ctx.assrt.is_configured():
        return {
            "ok": False,
            "configured": False,
            "items": [],
            "error": "未配置射手网 token：请到 assrt.net 登录后获取，填进 .env 的 ASSRT_TOKEN",
        }
    try:
        items = ctx.assrt.search(query, count=max(1, min(count, 30)), page=max(0, pos))
    except AssrtError as exc:
        return {"ok": False, "configured": True, "items": [], "error": str(exc)}
    for item in items:
        item["lang_label"] = " / ".join(
            ASSRT_LANG_LABEL.get(code, code) for code in (item.get("langs") or [])
        )
    return {"ok": True, "configured": True, "query": query, "pos": pos, "items": items}


def embed_subtitle(
    ctx: ServiceContext,
    *,
    target: str,
    file_url: Optional[str] = None,
    sub_id: Optional[int] = None,
    file_name: Optional[str] = None,
    language: str = "chi",
    track_name: Optional[str] = None,
    output: Optional[str] = None,
    make_default: bool = True,
) -> Dict[str, Any]:
    """下载在线字幕并嵌入目标视频（复用 mkvmerge，不重编码）。"""
    target_path = Path(target)
    if not target_path.is_file():
        return {"ok": False, "error": f"目标视频不存在：{target}"}

    url = file_url
    name = file_name
    if not url:
        if sub_id is None:
            return {"ok": False, "error": "需要给出字幕文件地址，或射手网字幕 id"}
        try:
            detail = ctx.assrt.detail(int(sub_id))
        except AssrtError as exc:
            return {"ok": False, "error": str(exc)}
        files = detail.get("files") or []
        if not files:
            return {"ok": False, "error": "这个字幕条目里没有可下载的文件"}
        if len(files) > 1:
            # 早期会直接取第一个文件，结果是「目标视频是第 5 集、嵌进去第 1 集的字幕」
            # 这种静默错误。现在明确拒绝，让用户去批量嵌入里逐集配对。
            return {
                "ok": False,
                "error": f"这个条目里有 {len(files)} 个字幕文件（多半是整季打包），"
                "单条嵌入会取错集数。请改用「批量嵌入字幕…」左右配对。",
                "file_count": len(files),
            }
        url = files[0]["url"]
        name = files[0]["name"] or name

    try:
        content = ctx.assrt.download(url)
    except AssrtError as exc:
        return {"ok": False, "error": str(exc)}
    if not content:
        return {"ok": False, "error": "下载到的字幕是空文件"}

    # 扩展名决定 mkvmerge 怎么解析内容，必须给对
    suffix = Path(name or url).suffix.lower()
    if suffix not in (".ass", ".ssa", ".srt", ".vtt", ".sup", ".sub"):
        suffix = ".ass"
    work_dir = Path(ctx.settings.cache_dir).parent / "subtitles"
    work_dir.mkdir(parents=True, exist_ok=True)
    # 文件名里带上来源标识，便于在 data/subtitles/ 里认出这堆文件是哪来的
    if sub_id is not None:
        stamp = str(sub_id)
    else:
        stamp = Path(name or "file").stem[:30] or "url"
    subtitle_file = work_dir / f"assrt_{stamp}_{target_path.stem[:40]}{suffix}"
    subtitle_file.write_bytes(content)

    if output:
        output_path = Path(output)
        if not output_path.is_absolute():
            output_path = target_path.parent / output_path
    else:
        output_path = target_path.with_name(f"{target_path.stem}.subbed.mkv")
    if output_path.suffix.lower() != ".mkv":
        output_path = output_path.with_suffix(".mkv")

    try:
        result = ctx.mkv.mux_subtitles(
            str(target_path),
            [str(subtitle_file)],
            str(output_path),
            language=language,
            track_name=track_name or None,
            make_default=make_default,
        )
    except MkvToolError as exc:
        return {"ok": False, "error": str(exc)}

    return {
        "ok": True,
        "target": str(target_path),
        "output": result["output"],
        "output_size": result.get("size"),
        "output_size_text": human_size(result.get("size") or 0),
        "subtitle_file": str(subtitle_file),
        "subtitle_size": len(content),
        "warnings": result.get("warnings", False),
    }


def assrt_subtitle_files(ctx: ServiceContext, sub_id: int) -> Dict[str, Any]:
    """列出某个射手网条目里的所有字幕文件。

    射手网的条目常常是「一季打包」，filelist 里会有 01.ass / 02.ass / …
    所以不能像早期那样直接取第一个——必须先让用户看清有哪些文件。
    """
    try:
        detail = ctx.assrt.detail(int(sub_id))
    except (AssrtError, ValueError) as exc:
        return {"ok": False, "error": str(exc), "files": []}
    files = detail.get("files") or []
    return {
        "ok": True,
        "sub_id": sub_id,
        "title": detail.get("title") or "",
        "subtype": detail.get("subtype"),
        "package_url": detail.get("package_url") or "",
        "files": files,
    }


def pair_by_order(subtitles: Sequence[Any], videos: Sequence[Any]) -> Dict[str, Any]:
    """按选择顺序一一配对。

    数量不一致就拒绝——宁可让你回去改选择，也不要静默丢掉几个或者错位嵌入。
    """
    subs = [item for item in (subtitles or []) if item]
    vids = [item for item in (videos or []) if item]
    if not subs or not vids:
        return {"ok": False, "error": "字幕和视频都要至少选一个", "pairs": []}
    if len(subs) != len(vids):
        return {
            "ok": False,
            "error": f"数量不一致：选了 {len(subs)} 个字幕、{len(vids)} 个视频。"
            "请两边选成一样多（多出来的去掉），避免错位。",
            "pairs": [],
        }
    return {
        "ok": True,
        "pairs": [
            {"index": index + 1, "subtitle": sub, "video": vid}
            for index, (sub, vid) in enumerate(zip(subs, vids))
        ],
    }


def embed_subtitles_batch(ctx: ServiceContext, items: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """按给定顺序逐个嵌入（每个视频各自生成 <原名>.subbed.mkv）。"""
    results: List[Dict[str, Any]] = []
    for item in items or []:
        single = embed_subtitle(
            ctx,
            target=str(item.get("video") or ""),
            file_url=item.get("subtitle_url") or None,
            file_name=item.get("subtitle_name") or None,
            language=item.get("language") or "chi",
            track_name=item.get("track_name") or None,
            output=item.get("output") or None,
            make_default=bool(item.get("make_default", True)),
        )
        results.append(
            {
                "index": item.get("index"),
                "video": item.get("video"),
                "subtitle": item.get("subtitle_name") or item.get("subtitle_url"),
                "ok": bool(single.get("ok")),
                "output": single.get("output"),
                "error": single.get("error"),
            }
        )
    succeeded = sum(1 for item in results if item["ok"])
    return {
        "ok": bool(results) and succeeded == len(results),
        "total": len(results),
        "succeeded": succeeded,
        "results": results,
    }


# ---------------------------------------------------- 内封字幕（MKVToolNix）

#: 列目录时认为「可能是视频」的扩展名
VIDEO_SUFFIXES = (".mkv", ".mp4", ".m4v", ".avi", ".ts", ".m2ts", ".mov", ".wmv", ".flv")


def list_library(
    ctx: ServiceContext,
    path: Optional[str] = None,
    *,
    limit: int = 300,
    suffixes: Optional[Sequence[str]] = None,
    recursive: bool = True,
) -> Dict[str, Any]:
    """列出下载目录里的视频文件，供字幕工具选择。"""
    # 同上：粘贴来的路径可能带引号
    cleaned = (path or "").strip().strip('"').strip("'")
    root = Path(cleaned) if cleaned else Path(ctx.settings.qbit_savepath)
    if not root.is_dir():
        return {"ok": False, "root": str(root), "error": f"目录不存在：{root}", "files": []}

    wanted = tuple(s.lower() for s in suffixes) if suffixes else VIDEO_SUFFIXES
    files: List[Dict[str, Any]] = []
    # 批量嵌入只看当前目录：BD 资源的子目录里往往还有特典、NCOP 之类的其它视频，
    # 递归进来会把它们一起列出，反而添乱。
    candidates = sorted(root.rglob("*")) if recursive else sorted(root.iterdir())
    for item in candidates:
        if len(files) >= limit:
            break
        if not item.is_file() or item.suffix.lower() not in wanted:
            continue
        try:
            size = item.stat().st_size
        except OSError:
            size = 0
        files.append(
            {
                "path": str(item),
                "name": item.name,
                "relative": item.name if not recursive else str(item.relative_to(root)),
                "size": size,
                "size_text": human_size(size),
            }
        )
    return {"ok": True, "root": str(root), "files": files}


def subtitle_probe(ctx: ServiceContext, path: str) -> Dict[str, Any]:
    """读取一个视频文件的轨道信息。"""
    try:
        info = ctx.mkv.probe(path)
    except MkvToolError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, **info}


def subtitle_merge(
    ctx: ServiceContext,
    *,
    target: str,
    source: str,
    track_ids: Sequence[int],
    language: str = "chi",
    track_name: Optional[str] = None,
    output: Optional[str] = None,
    make_default: bool = True,
) -> Dict[str, Any]:
    """把来源视频里的字幕轨搬进目标视频。

    流程：mkvextract 提取 → mkvmerge 合并。视频/音频流原样复制，不重编码。
    提取出的字幕文件保留在 data/subtitles/ 下，方便单独取用。
    """
    if not track_ids:
        return {"ok": False, "error": "请至少选择一条字幕轨"}

    target_path = Path(target)
    if not target_path.is_file():
        return {"ok": False, "error": f"目标视频不存在：{target}"}
    if Path(source).resolve() == target_path.resolve():
        return {"ok": False, "error": "目标视频和来源视频不能是同一个文件"}

    if output:
        output_path = Path(output)
        if not output_path.is_absolute():
            output_path = target_path.parent / output_path
    else:
        output_path = target_path.with_name(f"{target_path.stem}.subbed.mkv")
    if output_path.suffix.lower() != ".mkv":
        output_path = output_path.with_suffix(".mkv")

    extract_dir = Path(ctx.settings.cache_dir).parent / "subtitles"
    try:
        extracted = ctx.mkv.extract(source, list(track_ids), str(extract_dir))
        result = ctx.mkv.mux_subtitles(
            str(target_path),
            extracted,
            str(output_path),
            language=language,
            track_name=track_name or None,
            make_default=make_default,
        )
    except MkvToolError as exc:
        return {"ok": False, "error": str(exc)}

    return {
        "ok": True,
        "target": str(target_path),
        "source": source,
        "output": result["output"],
        "output_size": result.get("size"),
        "output_size_text": human_size(result.get("size") or 0),
        "extracted": extracted,
        "warnings": result.get("warnings", False),
    }


def releases_to_dict(release: Release) -> Dict[str, Any]:
    return {
        "torrent_id": release.torrent_id,
        "title": release.title,
        "group": release.group,
        "season": release.season,
        "episode": release.episode,
        "source": release.source,
        "resolution": release.resolution,
        "video_codec": release.video_codec,
        "audio_codec": release.audio_codec,
        "sub_lang": release.sub_lang,
        "fsub_lang": release.fsub_lang,
        "batch": release.batch,
        "upgraded": release.upgraded,
        "filesize": release.filesize,
        "size_text": release.size_text,
        "seeders": release.seeders,
        "leechers": release.leechers,
        "infohash": release.infohash,
        "media_id": release.media_id,
        "source": release.source,
        "origin": release.origin,
        "torrent_url": release.torrent_url,
    }


def fetch_releases(
    ctx: ServiceContext,
    media_id: str,
    *,
    query: Optional[str] = None,
    limit: int = 100,
    sub_lang: Optional[str] = None,
) -> List[Release]:
    page = ctx.nekobt.search_torrents(
        media_id=media_id,
        query=query,
        limit=limit,
        sort_by="best",
        sub_lang=sub_lang,
    )
    return [build_release(row) for row in page.results]


def list_releases(
    ctx: ServiceContext,
    media_id: str,
    *,
    prefs: Optional[ReleasePreferences] = None,
    limit: int = 100,
    sort: str = "preference",
    include_unknown: bool = True,
) -> Dict[str, Any]:
    prefs = prefs or load_preferences(ctx.store)
    releases = fetch_releases(ctx, media_id, limit=limit)
    payload = _release_page(releases, prefs, sort, include_unknown)
    payload["media_id"] = media_id
    payload["source"] = "nekobt"
    return payload


def _release_page(
    releases: Sequence[Release],
    prefs: ReleasePreferences,
    sort: str,
    include_unknown: bool,
) -> Dict[str, Any]:
    """把一批 Release 变成前端的统一分页结构（nekoBT 与蜜柑共用）。"""
    # 没有任何偏好就不过滤，但仍按默认偏好排序
    filter_prefs = prefs if has_rules(prefs) else ReleasePreferences()
    rank_prefs = prefs if filter_prefs is prefs else default_preferences()

    filtered: FilterResult = apply_filter(releases, filter_prefs)

    def ranked(items: Sequence[Release]) -> List[Dict[str, Any]]:
        if sort == "seeders":
            ordered = sorted(items, key=lambda r: (r.seeders or 0), reverse=True)
            return [{"release": releases_to_dict(r), "score": None, "reasons": []} for r in ordered]
        if sort == "size":
            ordered = sorted(items, key=lambda r: (r.filesize or 0))
            return [{"release": releases_to_dict(r), "score": None, "reasons": []} for r in ordered]
        return [
            {
                "release": releases_to_dict(item.release),
                "score": item.score,
                "reasons": item.reasons,
            }
            for item in rank_releases(items, rank_prefs)
        ]

    return {
        "total": len(releases),
        "counts": {
            "matched": len(filtered.matched),
            "unknown": len(filtered.unknown),
            "excluded": len(filtered.excluded),
        },
        "matched": ranked(filtered.matched),
        "unknown": ranked(filtered.unknown) if include_unknown else [],
        "excluded": ranked(filtered.excluded) if include_unknown else [],
    }


def mikan_releases(
    ctx: ServiceContext,
    title: str,
    *,
    prefs: Optional[ReleasePreferences] = None,
    limit: int = 100,
    sort: str = "preference",
    include_unknown: bool = True,
) -> Dict[str, Any]:
    """从蜜柑按标题直接搜 Release。

    和 nekoBT 不同，蜜柑没有「作品」这一层，只有标题检索，
    所以这里跳过作品识别，直接返回分页结果（结构保持一致，前端可复用渲染）。
    """
    prefs = prefs or load_preferences(ctx.store)
    try:
        rows = ctx.mikan.search(title, limit=max(1, min(limit, 300)))
    except MikanError as exc:
        return {
            "ok": False,
            "source": MIKAN_SOURCE,
            "query": title,
            "error": str(exc),
            "total": 0,
            "counts": {"matched": 0, "unknown": 0, "excluded": 0},
            "matched": [],
            "unknown": [],
            "excluded": [],
        }
    releases = [build_release(row) for row in rows]
    payload = _release_page(releases, prefs, sort, include_unknown)
    payload["ok"] = True
    payload["source"] = MIKAN_SOURCE
    payload["query"] = title
    payload["neutral_sort"] = True  # RSS 里没有做种数，按做种排序没有意义
    return payload


def submit_download(
    ctx: ServiceContext,
    torrent_id: Optional[str] = None,
    *,
    torrent_url: Optional[str] = None,
    paused: bool = False,
    infohash: Optional[str] = None,
    title: Optional[str] = None,
    save_path: Optional[str] = None,
) -> Dict[str, Any]:
    settings = ctx.settings
    settings.require_download_settings()

    # 只有真要下载时才碰 qBittorrent：没在跑就尝试把它拉起来
    ready = ensure_running(settings)
    if not ready.get("ok"):
        raise QBittorrentError(ready.get("error") or "qBittorrent 未就绪")
    launched = bool(ready.get("launched"))

    # 两种取种子的方式：nekoBT 用 torrent_id 走它的下载接口；
    # 蜜柑这类来源直接给 .torrent 直链，我们自己取回来再交给客户端。
    if torrent_url:
        content, filename = _fetch_torrent(torrent_url, user_agent=settings.user_agent)
    elif torrent_id:
        content, filename = ctx.nekobt.download_torrent(torrent_id, public=True)
    else:
        raise TorrentFetchError("必须提供 torrent_id（nekoBT）或 torrent_url（其它来源）")
    # 优先级：本次请求指定的 > 界面里设过的默认 > .env 的 QBIT_SAVEPATH
    try:
        cleaned = normalize_save_path(save_path)
    except ValueError as exc:
        raise TorrentFetchError(str(exc)) from exc
    target = cleaned or effective_save_path(ctx)

    ctx.qbit.add_torrent(
        torrent_bytes=content,
        torrent_filename=filename,
        savepath=target,
        category=settings.qbit_category,
        tags=settings.qbit_tags,
        paused=paused,
    )

    # qBittorrent 的 hash 就是 torrent 的 infohash，能对上就回填
    torrent_hash = None
    if infohash:
        try:
            for row in ctx.qbit.torrents_info():
                if (row.get("hash") or "").lower() == infohash.lower():
                    torrent_hash = row.get("hash")
                    break
        except QBittorrentError:
            torrent_hash = None

    ctx.store.record_download(
        torrent_id=torrent_id or torrent_url or "",
        torrent_hash=torrent_hash or infohash,
        title=title or filename,
        save_path=target,
        client="qbittorrent",
        status="paused" if paused else "started",
    )

    return {
        "ok": True,
        "launched_client": launched,
        "torrent_id": torrent_id,
        "filename": filename,
        "size": len(content),
        "save_path": target,
        "paused": paused,
        "torrent_hash": torrent_hash or infohash,
    }


def _probe_nekobt(ctx: ServiceContext) -> Dict[str, Any]:
    settings = ctx.settings
    probe = ctx.nekobt_probe or NekoBTClient(
        settings.nekobt_base_url,
        api_key=settings.nekobt_api_key,
        timeout=min(settings.http_timeout, 6.0),
        retries=1,
        user_agent=settings.user_agent,
    )
    rows = probe.search_media("葬送的芙莉莲", limit=1)
    return {"ok": True, "sample": (rows[0].get("title") if rows else None)}


def _probe_bangumi(ctx: ServiceContext) -> Dict[str, Any]:
    settings = ctx.settings
    probe = ctx.bangumi_probe or BangumiClient(
        user_agent=f"{settings.user_agent} (https://github.com/CyniKal073)",
        timeout=5.0,
        availability_ttl=0,
    )
    available = probe.is_available(refresh=True)
    return {
        "ok": available,
        "url": getattr(probe, "base_url", "https://api.bgm.tv"),
        "error": None if available else "无法访问 api.bgm.tv（通常需要开启 VPN）",
    }


def health(ctx: ServiceContext) -> Dict[str, Any]:
    """自检。

    设计约束：

    * **任何一项失败都不能让接口报 5xx** —— 页面一打开就会调它；
    * 每项用短超时、不重试的探针，并且 catch 到最宽；
    * 三项并发探测，否则 nekoBT 与 Bangumi 都不可达时要串行等十几秒。
    """
    probes = {
        "nekobt": _probe_nekobt,
        "bangumi": _probe_bangumi,
    }
    report: Dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=len(probes)) as pool:
        futures = {name: pool.submit(func, ctx) for name, func in probes.items()}
        for name, future in futures.items():
            try:
                report[name] = future.result()
            except Exception as exc:  # noqa: BLE001 - 自检不能抛出去
                report[name] = {"ok": False, "error": str(exc)[:400]}

    # 只探测「识别链路」是否可用。
    # qBittorrent 不再在打开页面时检查——它只在真正下载时才需要，
    # 到那时由 qbittorrent/launcher.ensure_running() 按需拉起。
    report["ok"] = bool(report["nekobt"].get("ok"))
    report["qbittorrent"] = {"checked": False, "hint": "仅在下载时检查，未运行会自动尝试启动"}
    return report
