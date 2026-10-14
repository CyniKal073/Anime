"""V1：本地 Web UI。

启动::

    python -m src.api.app            # 默认 http://127.0.0.1:8765
    python -m uvicorn src.api.app:app --port 8765
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..config import ConfigError, Settings, load_dotenv_best_effort
from ..nekobt.client import NekoBTError
from ..qbittorrent.client import QBittorrentError
from ..release.matcher import ReleasePreferences, default_preferences
from ..service import (
    ServiceContext,
    TorrentFetchError,
    bridge_candidate,
    browse_directories,
    assrt_subtitle_files,
    embed_subtitles_batch,
    build_context,
    calendar_view,
    confirm_mapping,
    download_paths,
    embed_subtitle,
    fetch_subtitles_batch,
    health,
    list_library,
    list_releases,
    load_preferences,
    mikan_releases,
    pair_by_order,
    preferences_from_dict,
    preferences_to_dict,
    resolve_work,
    save_preferences,
    set_download_path,
    subtitle_merge,
    subtitle_probe,
    submit_download,
    assrt_search,
)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
DEFAULT_PORT = 8765

_CONTEXT: Optional[ServiceContext] = None


def get_context() -> ServiceContext:
    """依赖注入点：测试里可以用 app.dependency_overrides 替换。"""
    global _CONTEXT
    if _CONTEXT is None:
        _CONTEXT = build_context()
    return _CONTEXT


class DownloadRequest(BaseModel):
    #: nekoBT 路径用 torrent_id；蜜柑这类来源直接给 .torrent 直链
    torrent_id: Optional[str] = None
    torrent_url: Optional[str] = None
    infohash: Optional[str] = None
    title: Optional[str] = None
    #: 本次下载的保存目录；留空则用界面里设过的默认
    save_path: Optional[str] = None
    paused: bool = False


class DownloadPathRequest(BaseModel):
    path: str = ""


class SubtitleSearchRequest(BaseModel):
    query: str
    count: int = 15
    pos: int = 0


class SubtitleEmbedRequest(BaseModel):
    target: str
    sub_id: Optional[int] = None
    file_url: Optional[str] = None
    #: 已经下到本地的字幕文件（批量嵌入第一阶段下载的结果），给了就不再下载
    subtitle_path: Optional[str] = None
    file_name: Optional[str] = None
    language: str = "chi"
    track_name: Optional[str] = None
    output: Optional[str] = None
    make_default: bool = True
    #: 嵌入成功后用新文件顶替原视频（省空间，但不可逆）
    replace_original: bool = False


class SubtitleFilesRequest(BaseModel):
    sub_id: int


class FetchSubtitlesRequest(BaseModel):
    """批量嵌入第一阶段：只下字幕，不碰视频。"""

    subtitles: List[Dict[str, Any]] = []
    videos: List[str] = []


class BatchEmbedRequest(BaseModel):
    """批量嵌入：字幕与视频按选择顺序一一配对。"""

    subtitles: List[Dict[str, Any]] = []
    videos: List[str] = []
    language: str = "chi"
    make_default: bool = True
    #: 每一组嵌入成功后都用新文件顶替原视频
    replace_original: bool = False


class PreferencesRequest(BaseModel):
    resolutions: List[str] = []
    sources: List[str] = []
    video_codecs: List[str] = []
    sub_langs: List[str] = []
    require_batch: Optional[bool] = None
    min_seeders: Optional[int] = None


class MappingRequest(BaseModel):
    input_title: str
    media_id: str
    anilist_id: Optional[int] = None
    bangumi_id: Optional[int] = None
    confidence: float = 1.0


class BridgeRequest(BaseModel):
    name: str


class SubtitleProbeRequest(BaseModel):
    path: str


class SubtitleMergeRequest(BaseModel):
    target: str
    source: str
    track_ids: List[int] = []
    language: str = "chi"
    track_name: Optional[str] = None
    output: Optional[str] = None
    make_default: bool = True
    #: 合并成功后用新文件顶替原视频（省空间，但不可逆）
    replace_original: bool = False


def create_app() -> FastAPI:
    app = FastAPI(title="Anime Release Manager", version="0.2.0")

    @app.get("/api/health")
    def api_health(ctx: ServiceContext = Depends(get_context)) -> Dict[str, Any]:
        return health(ctx)

    @app.get("/api/search")
    def api_search(
        title: str,
        top: int = 5,
        ctx: ServiceContext = Depends(get_context),
    ) -> Dict[str, Any]:
        if not title.strip():
            raise HTTPException(status_code=400, detail="title 不能为空")
        try:
            return resolve_work(ctx, title, top=max(1, min(top, 20)))
        except NekoBTError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/api/mapping")
    def api_confirm_mapping(
        req: MappingRequest,
        ctx: ServiceContext = Depends(get_context),
    ) -> Dict[str, Any]:
        """用户确认候选后落库，下次同一输入直接命中缓存。"""
        return confirm_mapping(
            ctx,
            input_title=req.input_title,
            media_id=req.media_id,
            anilist_id=req.anilist_id,
            bangumi_id=req.bangumi_id,
            confidence=req.confidence,
        )

    @app.post("/api/bridge")
    def api_bridge(
        req: BridgeRequest,
        ctx: ServiceContext = Depends(get_context),
    ) -> Dict[str, Any]:
        """把 Bangumi 条目解析成 nekoBT media_id（惰性桥接，点选时才调用）。"""
        return bridge_candidate(ctx, req.name)

    @app.get("/api/media/{media_id}/releases")
    def api_releases(
        media_id: str,
        resolution: List[str] = Query(default=[]),
        source: List[str] = Query(default=[]),
        codec: List[str] = Query(default=[]),
        sub_lang: List[str] = Query(default=[]),
        batch: Optional[bool] = None,
        min_seeders: Optional[int] = None,
        limit: int = 100,
        sort: str = "preference",
        include_unknown: bool = True,
        ctx: ServiceContext = Depends(get_context),
    ) -> Dict[str, Any]:
        provided = any(
            [resolution, source, codec, sub_lang, batch is not None, min_seeders is not None]
        )
        if provided:
            prefs = ReleasePreferences(
                resolutions=list(resolution),
                sources=list(source),
                video_codecs=list(codec),
                sub_langs=list(sub_lang),
                require_batch=batch,
                min_seeders=min_seeders,
            )
        else:
            prefs = load_preferences(ctx.store)
        try:
            return list_releases(
                ctx,
                media_id,
                prefs=prefs,
                limit=max(1, min(limit, 300)),
                sort=sort,
                include_unknown=include_unknown,
            )
        except NekoBTError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/api/download")
    def api_download(
        req: DownloadRequest,
        ctx: ServiceContext = Depends(get_context),
    ) -> Dict[str, Any]:
        try:
            return submit_download(
                ctx,
                req.torrent_id,
                torrent_url=req.torrent_url,
                paused=req.paused,
                infohash=req.infohash,
                title=req.title,
                save_path=req.save_path,
            )
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (NekoBTError, QBittorrentError, TorrentFetchError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/api/downloads")
    def api_downloads(
        limit: int = 20,
        ctx: ServiceContext = Depends(get_context),
    ) -> Dict[str, Any]:
        rows = ctx.store.list_downloads(max(1, min(limit, 200)))
        return {"items": [dict(row) for row in rows]}

    @app.get("/api/download-path")
    def api_download_path(ctx: ServiceContext = Depends(get_context)) -> Dict[str, Any]:
        """当前下载目录与候选项（界面里可以改，比 .env 更灵活）。"""
        return download_paths(ctx)

    @app.post("/api/download-path")
    def api_set_download_path(
        req: DownloadPathRequest,
        ctx: ServiceContext = Depends(get_context),
    ) -> Dict[str, Any]:
        """把下载目录设为默认；传空字符串表示恢复成 .env 里的值。"""
        try:
            return set_download_path(ctx, req.path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/browse")
    def api_browse(
        path: Optional[str] = None,
        ctx: ServiceContext = Depends(get_context),
    ) -> Dict[str, Any]:
        """列出目录下的子文件夹，供下载位置的弹窗逐层挑选。"""
        return browse_directories(ctx, path)

    @app.get("/api/mikan/releases")
    def api_mikan_releases(
        title: str,
        resolution: List[str] = Query(default=[]),
        source: List[str] = Query(default=[]),
        codec: List[str] = Query(default=[]),
        sub_lang: List[str] = Query(default=[]),
        batch: Optional[bool] = None,
        min_seeders: Optional[int] = None,
        limit: int = 100,
        sort: str = "preference",
        include_unknown: bool = True,
        ctx: ServiceContext = Depends(get_context),
    ) -> Dict[str, Any]:
        """蜜柑（老片源补充）：按标题直接搜 Release，没有作品识别这一步。

        返回结构与 nekoBT 的 /api/media/{id}/releases 一致，前端可复用渲染。
        """
        if not title.strip():
            raise HTTPException(status_code=400, detail="title 不能为空")
        provided = any(
            [resolution, source, codec, sub_lang, batch is not None, min_seeders is not None]
        )
        if provided:
            prefs = ReleasePreferences(
                resolutions=list(resolution),
                sources=list(source),
                video_codecs=list(codec),
                sub_langs=list(sub_lang),
                require_batch=batch,
                min_seeders=min_seeders,
            )
        else:
            prefs = load_preferences(ctx.store)
        return mikan_releases(
            ctx,
            title,
            prefs=prefs,
            limit=max(1, min(limit, 300)),
            sort=sort,
            include_unknown=include_unknown,
        )

    @app.get("/api/calendar")
    def api_calendar(
        refresh: bool = False,
        ctx: ServiceContext = Depends(get_context),
    ) -> Dict[str, Any]:
        """本季连载（Bangumi 每日放送）。Bangumi 不可用时返回 ok=false 而不是报错。"""
        return calendar_view(ctx, refresh=refresh)

    # ------------------------------------------------------- 内封字幕工具

    @app.get("/api/library")
    def api_library(
        path: Optional[str] = None,
        ctx: ServiceContext = Depends(get_context),
    ) -> Dict[str, Any]:
        """列出下载目录里的视频文件，供字幕工具选择。"""
        return list_library(ctx, path)

    @app.post("/api/subtitle/probe")
    def api_subtitle_probe(
        req: SubtitleProbeRequest,
        ctx: ServiceContext = Depends(get_context),
    ) -> Dict[str, Any]:
        """读取视频文件的轨道列表（含字幕轨的语言、格式、是否默认）。"""
        return subtitle_probe(ctx, req.path)

    @app.post("/api/subtitle/merge")
    def api_subtitle_merge(
        req: SubtitleMergeRequest,
        ctx: ServiceContext = Depends(get_context),
    ) -> Dict[str, Any]:
        """把来源视频的字幕轨合并进目标视频（不重编码）。"""
        return subtitle_merge(
            ctx,
            target=req.target,
            source=req.source,
            track_ids=req.track_ids,
            language=req.language,
            track_name=req.track_name,
            output=req.output,
            make_default=req.make_default,
            replace_original=req.replace_original,
        )

    # ------------------------------------------------------- 在线字幕（射手网）

    @app.post("/api/subtitle/search")
    def api_subtitle_search(
        req: SubtitleSearchRequest,
        ctx: ServiceContext = Depends(get_context),
    ) -> Dict[str, Any]:
        """在射手网检索字幕。未配置 token 时返回 configured=false，不报错。"""
        if not req.query.strip():
            raise HTTPException(status_code=400, detail="query 不能为空")
        return assrt_search(ctx, req.query, count=req.count, pos=max(0, req.pos))

    @app.post("/api/subtitle/embed")
    def api_subtitle_embed(
        req: SubtitleEmbedRequest,
        ctx: ServiceContext = Depends(get_context),
    ) -> Dict[str, Any]:
        """下载在线字幕并嵌入目标视频（不重编码）。"""
        return embed_subtitle(
            ctx,
            target=req.target,
            sub_id=req.sub_id,
            file_url=req.file_url,
            subtitle_path=req.subtitle_path,
            file_name=req.file_name,
            language=req.language,
            track_name=req.track_name,
            output=req.output,
            make_default=req.make_default,
            replace_original=req.replace_original,
        )

    @app.post("/api/subtitle/fetch")
    def api_subtitle_fetch(
        req: FetchSubtitlesRequest,
        ctx: ServiceContext = Depends(get_context),
    ) -> Dict[str, Any]:
        """批量嵌入的第一阶段：按配对顺序把整批字幕先下到本地。

        下载是唯一受网络影响的步骤，先集中做完——失败的话几秒内就知道，而不是
        等十几个 mkvmerge 跑完才发现某一集没字幕。返回的 path 交给第二阶段。
        """
        paired = pair_by_order(req.subtitles, req.videos)
        if not paired["ok"]:
            raise HTTPException(status_code=400, detail=paired["error"])
        items = [
            {
                "index": pair["index"],
                "video": pair["video"],
                "name": pair["subtitle"].get("name"),
                "url": pair["subtitle"].get("url"),
            }
            for pair in paired["pairs"]
        ]
        return fetch_subtitles_batch(ctx, items)

    @app.post("/api/subtitle/files")
    def api_subtitle_files(
        req: SubtitleFilesRequest,
        ctx: ServiceContext = Depends(get_context),
    ) -> Dict[str, Any]:
        """列出某个射手网条目里的全部字幕文件（一季打包时会有多个）。"""
        return assrt_subtitle_files(ctx, req.sub_id)

    @app.get("/api/videos")
    def api_videos(
        path: Optional[str] = None,
        ctx: ServiceContext = Depends(get_context),
    ) -> Dict[str, Any]:
        """列出某个文件夹**当前层**的视频文件（只认 mkv，不看子目录）。

        BD 资源的子目录里常有特典、NCOP 等别的视频，递归进来只会添乱。
        """
        result = list_library(ctx, path, suffixes=(".mkv",), recursive=False)
        result["dir"] = result.pop("root", None)
        return result

    @app.post("/api/subtitle/embed-batch")
    def api_subtitle_embed_batch(
        req: BatchEmbedRequest,
        ctx: ServiceContext = Depends(get_context),
    ) -> Dict[str, Any]:
        """按选择顺序把字幕一一嵌进对应视频。数量不一致直接拒绝。"""
        paired = pair_by_order(req.subtitles, req.videos)
        if not paired["ok"]:
            raise HTTPException(status_code=400, detail=paired["error"])

        items = []
        for pair in paired["pairs"]:
            subtitle = pair["subtitle"]
            items.append(
                {
                    "index": pair["index"],
                    "video": pair["video"],
                    "subtitle_path": subtitle.get("path") or subtitle.get("subtitle_path"),
                    "subtitle_url": subtitle.get("url"),
                    "subtitle_name": subtitle.get("name"),
                    "language": subtitle.get("language") or req.language,
                    "track_name": subtitle.get("track_name"),
                    "make_default": req.make_default,
                    "replace_original": req.replace_original,
                }
            )
        return embed_subtitles_batch(ctx, items)

    @app.get("/api/preferences")
    def api_get_preferences(ctx: ServiceContext = Depends(get_context)) -> Dict[str, Any]:
        return {
            "preferences": preferences_to_dict(load_preferences(ctx.store)),
            "defaults": preferences_to_dict(default_preferences()),
        }

    @app.post("/api/preferences")
    def api_set_preferences(
        req: PreferencesRequest,
        ctx: ServiceContext = Depends(get_context),
    ) -> Dict[str, Any]:
        payload = req.model_dump() if hasattr(req, "model_dump") else req.dict()
        prefs = preferences_from_dict(payload)
        save_preferences(ctx.store, prefs)
        return {"ok": True, "preferences": preferences_to_dict(prefs)}

    @app.get("/static/{path:path}")
    def static_file(path: str) -> FileResponse:
        """本地工具，静态资源一律不缓存。

        用 StaticFiles 的话缓存会咬人：改了 app.js / style.css 但版本号没跟着
        改，浏览器就一直用旧的，表现为「新功能看不见」。这里直接关掉缓存。
        """
        root = WEB_DIR.resolve()
        target = (root / path).resolve()
        if not str(target).startswith(str(root)) or not target.is_file():
            raise HTTPException(status_code=404, detail="not found")
        return FileResponse(target, headers={"Cache-Control": "no-store"})

    @app.get("/")
    def index() -> FileResponse:
        index_file = WEB_DIR / "index.html"
        if not index_file.exists():
            raise HTTPException(status_code=500, detail=f"缺少 {index_file}")
        # 页面结构会随版本变化，缓存住旧 HTML 会让新功能看不见
        return FileResponse(index_file, headers={"Cache-Control": "no-store"})

    return app


app = create_app()


def main() -> int:
    import uvicorn

    parser = argparse.ArgumentParser(prog="anime-release-manager-web")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument(
        "--reload-dir",
        action="append",
        default=[],
        help="热重载只监听这些目录（默认监听整个工作目录，会被 data/ 写入频繁触发）",
    )
    parser.add_argument("--no-access-log", action="store_true", help="关闭每条请求的访问日志")
    args = parser.parse_args()

    load_dotenv_best_effort()

    # 日志被重定向到文件时，中文和 ANSI 颜色码都会变成乱码。
    # 这里固定 UTF-8，并在下面关掉颜色输出。
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass

    print(f"Web UI: http://{args.host}:{args.port}")
    uvicorn.run(
        "src.api.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        reload_dirs=args.reload_dir or None,
        log_level="info",
        use_colors=False,
        access_log=not args.no_access_log,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
