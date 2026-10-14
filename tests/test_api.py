"""V1 Web API 测试（全部离线，用假客户端注入）。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app, get_context
from src.bangumi.client import BangumiUnavailable
from src.config import Settings
from src.nekobt.client import TorrentSearchPage
from src.qbittorrent.client import QBittorrentError
from src.service import ServiceContext, build_context
from src.storage import MediaMappingRecord, Store

from tests.fakes import media_result, torrent_row


class FakeNekoBT:
    def __init__(self, media_rows=None, torrent_rows=None, torrent_bytes=b"d4:infod4:name4:teste"):
        self.media_rows = media_rows if media_rows is not None else [
            media_result("s462", "Frieren: Beyond Journey's End", 0.5556),
            media_result("m641", "JoJo no Kimyou na Bouken", 0.0, year=2007),
        ]
        self.torrent_rows = torrent_rows if torrent_rows is not None else [
            torrent_row("1", "[BD] Frieren - S02 (BD 1080p HEVC)", sub_lang="zh-hans", video_codec=2, seeders=31),
            torrent_row("2", "[WEB] Frieren - S02 (WEB 1080p H.264)", sub_lang="en", video_codec=1, seeders=88),
            torrent_row("3", "[TV] Frieren - 38 (TV 1080p HEVC)", sub_lang="", video_codec=2, seeders=5),
        ]
        self.torrent_bytes = torrent_bytes
        self.downloaded = []

    def search_media(self, query, limit=None):
        return self.media_rows

    def search_torrents(self, **kwargs):
        return TorrentSearchPage(results=self.torrent_rows, meta={})

    def get_media(self, media_id):
        for row in self.media_rows:
            if row.get("id") == media_id:
                return {"id": media_id, "title": row.get("title"), "year": row.get("year")}
        return {"id": media_id}

    def download_torrent(self, torrent_id, public=True):
        self.downloaded.append(torrent_id)
        return self.torrent_bytes, f"{torrent_id}.torrent"


class FakeQbit:
    def __init__(self):
        self.added = []

    def add_torrent(self, **kwargs):
        self.added.append(kwargs)

    def torrents_info(self, **kwargs):
        return []

    def webapi_version(self):
        return "2.9.3"

    def app_version(self):
        return "v4.6.5.10"

    def probe_task_endpoints(self):
        return {"stop": "http 404", "pause": "ok"}


class FakeBangumi:
    base_url = "https://api.bgm.tv"

    def __init__(self, subjects=None, available=True):
        self.available = available
        self.subjects = subjects if subjects is not None else [
            {
                "id": 464376,
                "name_cn": "败犬女主太多了！",
                "name": "負けヒロインが多すぎる！",
                "date": "2024-07-13",
            },
            {
                "id": 550507,
                "name_cn": "败犬女主太多了！第二季",
                "name": "負けヒロインが多すぎる！ 第2期",
                "date": None,
            },
        ]
        self.calls = []

    def is_available(self, refresh=False):
        return self.available

    def search_subjects(self, keyword, limit=5):
        if not self.available:
            raise BangumiUnavailable("Bangumi 不可达：connection timed out")
        self.calls.append(keyword)
        return self.subjects[:limit]

    def calendar(self):
        if not self.available:
            raise BangumiUnavailable("Bangumi 不可达：connection timed out")
        return [
            {
                "weekday": 1,
                "weekday_cn": "星期一",
                "items": [
                    {
                        "bangumi_id": 400602,
                        "name_cn": "葬送的芙莉莲",
                        "name": "葬送のフリーレン",
                        "image": "https://lain.bgm.tv/pic/cover/c/frieren.jpg",
                        "air_date": "2023-09-29",
                        "url": "https://bgm.tv/subject/400602",
                    }
                ],
            }
        ]


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    # 下载流程里的「按需启动 qBittorrent」在测试中必须屏蔽，否则会真的去启动客户端
    monkeypatch.setattr(
        "src.service.ensure_running", lambda settings, **kwargs: {"ok": True, "launched": False}
    )
    settings = Settings(
        qbit_password="secret",
        qbit_savepath=r"D:\Anime\library",
        db_path=tmp_path / "test.db",
        cache_dir=tmp_path / "cache",
        assrt_min_interval=0,
    )
    context = ServiceContext(
        settings=settings,
        nekobt=FakeNekoBT(),
        store=Store(settings.db_path),
    )
    context._qbit = FakeQbit()
    context._bangumi = FakeBangumi()
    context.nekobt_probe = context.nekobt
    context.bangumi_probe = context._bangumi
    return context


@pytest.fixture
def offline_ctx(tmp_path, monkeypatch):
    """Bangumi 不可用的情况。"""
    monkeypatch.setattr(
        "src.service.ensure_running", lambda settings, **kwargs: {"ok": True, "launched": False}
    )
    settings = Settings(
        qbit_password="secret", db_path=tmp_path / "off.db", cache_dir=tmp_path / "cache"
    )
    context = ServiceContext(settings=settings, nekobt=FakeNekoBT(), store=Store(settings.db_path))
    context._qbit = FakeQbit()
    context._bangumi = FakeBangumi(available=False)
    context.nekobt_probe = context.nekobt
    context.bangumi_probe = context._bangumi
    return context


@pytest.fixture
def client(ctx):
    app = create_app()
    app.dependency_overrides[get_context] = lambda: ctx
    return TestClient(app)


def test_index_serves_html(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "动漫 Release 管理器" in res.text


def test_health_reports_both_services(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["bangumi"]["ok"] is True
    # 打开页面时不再探测 qBittorrent
    assert body["qbittorrent"]["checked"] is False


def test_health_never_touches_qbittorrent(tmp_path, monkeypatch):
    """qBittorrent 没运行也不该影响自检——它只在下载时才需要。"""
    monkeypatch.setattr(
        "src.service.ensure_running", lambda settings, **kwargs: {"ok": True, "launched": False}
    )
    settings = Settings(qbit_password="", db_path=tmp_path / "h.db", cache_dir=tmp_path / "c")
    context = ServiceContext(settings=settings, nekobt=FakeNekoBT(), store=Store(settings.db_path))
    context.nekobt_probe = context.nekobt
    context.bangumi_probe = FakeBangumi()
    app = create_app()
    app.dependency_overrides[get_context] = lambda: context
    res = TestClient(app).get("/api/health")

    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["qbittorrent"]["checked"] is False


def test_bangumi_unavailable_does_not_fail_overall_health(offline_ctx):
    app = create_app()
    app.dependency_overrides[get_context] = lambda: offline_ctx
    body = TestClient(app).get("/api/health").json()

    assert body["bangumi"]["ok"] is False
    assert body["bangumi"]["error"]
    # Bangumi 属于可降级项，不影响总体
    assert body["ok"] is True


def test_calendar_endpoint_returns_days_with_covers(client):
    body = client.get("/api/calendar").json()
    assert body["ok"] is True
    assert body["days"][0]["weekday_cn"] == "星期一"
    item = body["days"][0]["items"][0]
    assert item["name_cn"] == "葬送的芙莉莲"
    assert item["image"].startswith("https://")


def test_calendar_is_cached_after_first_call(client):
    assert client.get("/api/calendar").json()["source"] == "bangumi"
    assert client.get("/api/calendar").json()["source"] == "cache"


def test_calendar_degrades_when_bangumi_is_down(offline_ctx):
    app = create_app()
    app.dependency_overrides[get_context] = lambda: offline_ctx
    body = TestClient(app).get("/api/calendar").json()

    assert body["ok"] is False
    assert body["days"] == []
    assert "VPN" in body["hint"]


def test_search_returns_candidates_with_similarity(client):
    res = client.get("/api/search", params={"title": "葬送的芙莉莲"})
    assert res.status_code == 200
    body = res.json()
    assert body["source"] == "bangumi"
    first = body["candidates"][0]
    # 惰性桥接：搜索阶段先不解析 media_id，点选时才去 nekoBT
    assert first["media_id"] is None
    assert first["needs_bridge"] is True
    assert first["confidence"] == "high"
    assert first["name_cn"] == "败犬女主太多了！"
    assert first["origin"] == "bangumi"
    assert first["year"] == 2024


def test_search_falls_back_to_nekobt_when_bangumi_down(offline_ctx):
    app = create_app()
    app.dependency_overrides[get_context] = lambda: offline_ctx
    body = TestClient(app).get("/api/search", params={"title": "葬送的芙莉莲"}).json()

    assert body["source"] == "nekobt"
    assert any("Bangumi" in note and "降级" in note for note in body["notes"])
    assert body["candidates"][0]["media_id"] == "s462"
    # 0.5556 属于可信档；0.0 的噪声被标成 low
    assert body["candidates"][0]["confidence"] == "high"
    assert body["candidates"][1]["confidence"] == "low"


def test_confirmed_mapping_is_a_hint_not_a_shortcut(client, ctx):
    """确认过映射之后，再搜同一个名字仍要看到完整候选，只是上次那条被标记并置顶。

    早期实现会直接短路成单条结果，多季作品就没法改选了。
    """
    payload = {
        "input_title": "葬送的芙莉莲",
        "media_id": "s462",
        "anilist_id": 154587,
        "bangumi_id": 550507,  # 对应 FakeBangumi 的第二条（第二季）
        "confidence": 1.0,
    }
    assert client.post("/api/mapping", json=payload).json()["ok"] is True

    body = client.get("/api/search", params={"title": "葬送的芙莉莲"}).json()

    # 仍然走 Bangumi，候选数量不变
    assert body["source"] == "bangumi"
    assert len(body["candidates"]) == 2
    assert ctx._bangumi.calls, "应当照常查询 Bangumi"

    # 上次选的那条被标记并排到最前
    assert body["candidates"][0]["last_choice"] is True
    assert body["candidates"][0]["bangumi_id"] == 550507
    assert body["candidates"][1]["last_choice"] is False


def test_confirmed_mapping_is_fast_path_when_bangumi_is_down(offline_ctx):
    """Bangumi 不可用时，已确认的映射才是兜底通道。"""
    offline_ctx.store.save_mapping(
        MediaMappingRecord(
            input_title="葬送的芙莉莲",
            nekobt_media_id="s462",
            anilist_id=154587,
            confidence=1.0,
            confirmed=True,
        )
    )
    app = create_app()
    app.dependency_overrides[get_context] = lambda: offline_ctx
    body = TestClient(app).get("/api/search", params={"title": "葬送的芙莉莲"}).json()

    assert body["source"] == "cache"
    assert body["candidates"][0]["media_id"] == "s462"
    assert body["candidates"][0]["last_choice"] is True
    assert any("Bangumi 不可用" in note for note in body["notes"])


def test_bridge_endpoint_resolves_media_id(client):
    body = client.post("/api/bridge", json={"name": "負けヒロインが多すぎる！"}).json()
    assert body["ok"] is True
    assert body["media_id"] == "s462"  # FakeNekoBT 固定返回 s462


def test_bridge_endpoint_reports_failure(client, ctx):
    ctx.nekobt.media_rows = []
    body = client.post("/api/bridge", json={"name": "不存在的作品"}).json()
    assert body["ok"] is False
    assert "没有这部作品" in body["error"]


def test_bridge_rejects_low_similarity_match(client, ctx):
    """nekoBT 没有这部作品时会返回一堆无关结果，不能拿第一名顶替。

    实测《妄想学生会》在 nekoBT 的最高相似度只有 0.2857，而第一名是
    《学生会也有洞》——早期版本直接取第一名，于是把用户导向了别的作品。
    """
    ctx.nekobt.media_rows = [
        media_result("s12330", "Even the Student Council Has Its Holes!", 0.2857)
    ]
    body = client.post("/api/bridge", json={"name": "Seitokai Yakuindomo"}).json()

    assert body["ok"] is False
    assert body["media_id"] is None
    assert body["best_similarity"] == 0.2857
    assert "0.29" in body["error"]


def test_bridge_ignores_stale_low_similarity_cache(client, ctx):
    """旧版本可能已经把错误结果缓存下来了，读取缓存时也要过门槛。"""
    ctx.bridge_cache.set(
        "bridge:Seitokai Yakuindomo",
        {"media_id": "s12330", "title": "Even the Student Council Has Its Holes!",
         "similarity": 0.2857, "anilist_id": None},
    )
    body = client.post("/api/bridge", json={"name": "Seitokai Yakuindomo"}).json()

    assert body["ok"] is False
    assert body["media_id"] is None
    assert body["best_similarity"] == 0.2857


def test_bridge_endpoint_rejects_blank_name(client):
    body = client.post("/api/bridge", json={"name": "   "}).json()
    assert body["ok"] is False


def test_subtitle_search_without_token_is_graceful(client):
    """没配射手网 token 时给提示，不是报错。"""
    res = client.post("/api/subtitle/search", json={"query": "妄想学生会"})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert body["configured"] is False
    assert "ASSRT_TOKEN" in body["error"]


def test_subtitle_embed_rejects_missing_video(client):
    body = client.post("/api/subtitle/embed", json={"target": "D:/nope.mkv", "sub_id": 1}).json()
    assert body["ok"] is False
    assert "不存在" in body["error"]


def test_subtitle_embed_reports_error_when_unconfigured(client, tmp_path):
    video = tmp_path / "v.mkv"
    video.write_bytes(b"x")
    body = client.post("/api/subtitle/embed", json={"target": str(video), "sub_id": 1}).json()
    assert body["ok"] is False
    assert "ASSRT_TOKEN" in body["error"]


def test_single_embed_refuses_season_pack(client, monkeypatch, tmp_path):
    """整季打包的条目不能走单条嵌入——早期会静默取第一个文件，导致嵌错集数。"""
    video = tmp_path / "v.mkv"
    video.write_bytes(b"x")

    class PackAssrt:
        def is_configured(self):
            return True

        def detail(self, sub_id):
            return {
                "title": "整季",
                "files": [
                    {"name": "01.ass", "url": "http://x/01.ass"},
                    {"name": "02.ass", "url": "http://x/02.ass"},
                ],
            }

    ctx = client.app.dependency_overrides[get_context]()
    ctx._assrt = PackAssrt()

    body = client.post("/api/subtitle/embed", json={"target": str(video), "sub_id": 644387}).json()
    assert body["ok"] is False
    assert body["file_count"] == 2
    assert "批量嵌入" in body["error"]


# ---------------------------------------------------------------- 批量嵌入


def test_pair_by_order_keeps_selection_order():
    from src.service import pair_by_order

    result = pair_by_order(["01.ass", "02.ass"], ["E01.mkv", "E02.mkv"])
    assert result["ok"] is True
    assert [p["subtitle"] for p in result["pairs"]] == ["01.ass", "02.ass"]
    assert [p["video"] for p in result["pairs"]] == ["E01.mkv", "E02.mkv"]
    assert [p["index"] for p in result["pairs"]] == [1, 2]


def test_pair_by_order_rejects_count_mismatch():
    """数量不一致必须拒绝——静默丢掉几个就会错位嵌入。"""
    from src.service import pair_by_order

    result = pair_by_order(["01.ass"], ["E01.mkv", "E02.mkv"])
    assert result["ok"] is False
    assert "数量不一致" in result["error"]
    assert result["pairs"] == []


def test_pair_by_order_rejects_empty_sides():
    from src.service import pair_by_order

    assert pair_by_order([], ["E01.mkv"])["ok"] is False
    assert pair_by_order(["01.ass"], [])["ok"] is False


def test_batch_embed_pairs_each_subtitle_with_its_video(client, monkeypatch, tmp_path):
    videos = []
    for name in ("E01.mkv", "E02.mkv"):
        path = tmp_path / name
        path.write_bytes(b"x")
        videos.append(str(path))

    calls = []

    def fake_embed(ctx, **kwargs):
        calls.append(kwargs)
        return {"ok": True, "output": kwargs["target"] + ".subbed.mkv"}

    monkeypatch.setattr("src.service.embed_subtitle", fake_embed)

    res = client.post(
        "/api/subtitle/embed-batch",
        json={
            "subtitles": [
                {"name": "01.ass", "url": "http://x/01.ass"},
                {"name": "02.ass", "url": "http://x/02.ass"},
            ],
            "videos": videos,
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 2
    assert body["succeeded"] == 2

    # 第一个字幕配第一个视频，顺序不能错
    assert calls[0]["file_name"] == "01.ass"
    assert calls[0]["target"] == videos[0]
    assert calls[1]["file_url"] == "http://x/02.ass"
    assert calls[1]["target"] == videos[1]


def test_batch_embed_rejects_mismatched_counts(client, tmp_path):
    path = tmp_path / "E01.mkv"
    path.write_bytes(b"x")
    res = client.post(
        "/api/subtitle/embed-batch",
        json={"subtitles": [{"name": "01.ass", "url": "u"}], "videos": [str(path), str(path)]},
    )
    assert res.status_code == 400
    assert "数量不一致" in res.json()["detail"]


def test_videos_endpoint_tolerates_quoted_path(client, tmp_path):
    """Windows 的「复制为路径」带一对双引号，直接当路径会报「目录不存在」。"""
    (tmp_path / "E01.mkv").write_bytes(b"x")
    body = client.get("/api/videos", params={"path": f'"{tmp_path}"'}).json()
    assert body["ok"] is True
    assert [item["name"] for item in body["files"]] == ["E01.mkv"]


def test_videos_endpoint_only_lists_current_folder(client, tmp_path):
    """只看当前层：BD 资源的子目录里常有特典/NCOP，列出来只会添乱。"""
    (tmp_path / "E01.mkv").write_bytes(b"x")
    (tmp_path / "cover.jpg").write_bytes(b"x")
    (tmp_path / "sub.ass").write_text("x", encoding="utf-8")
    nested = tmp_path / "Extras"
    nested.mkdir()
    (nested / "NCOP.mkv").write_bytes(b"x")

    body = client.get("/api/videos", params={"path": str(tmp_path)}).json()
    assert [item["name"] for item in body["files"]] == ["E01.mkv"]


def test_videos_endpoint_reports_missing_dir_clearly(client, tmp_path):
    body = client.get("/api/videos", params={"path": str(tmp_path / "nope")}).json()
    assert body["ok"] is False
    assert "目录不存在" in body["error"]


def test_batch_embed_reports_partial_failures(client, monkeypatch, tmp_path):
    paths = []
    for name in ("E01.mkv", "E02.mkv"):
        item = tmp_path / name
        item.write_bytes(b"x")
        paths.append(str(item))

    calls = {"n": 0}

    def flaky_embed(ctx, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            return {"ok": False, "error": "模拟失败"}
        return {"ok": True, "output": "ok.mkv"}

    monkeypatch.setattr("src.service.embed_subtitle", flaky_embed)
    body = client.post(
        "/api/subtitle/embed-batch",
        json={
            "subtitles": [{"name": "01.ass", "url": "u1"}, {"name": "02.ass", "url": "u2"}],
            "videos": paths,
        },
    ).json()

    assert body["ok"] is False  # 有一组失败
    assert body["total"] == 2
    assert body["succeeded"] == 1
    assert body["results"][1]["error"] == "模拟失败"


def test_search_rejects_blank_title(client):
    assert client.get("/api/search", params={"title": "   "}).status_code == 400


def test_releases_returns_three_buckets(client):
    res = client.get("/api/media/s462/releases")
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 3
    assert sum(body["counts"].values()) == 3
    assert body["matched"][0]["release"]["torrent_id"]


def test_releases_filter_narrows_matched(client):
    res = client.get(
        "/api/media/s462/releases",
        params=[("resolution", "1080p"), ("source", "BDRip"), ("codec", "HEVC")],
    )
    body = res.json()
    assert [item["release"]["torrent_id"] for item in body["matched"]] == ["1"]
    # 2 号编码是 H.264，明确不符合 → 排除
    assert [item["release"]["torrent_id"] for item in body["excluded"]] == ["2"]
    # 3 号标题只写 TV，来源解析不出（不猜）→ 字段未知
    assert [item["release"]["torrent_id"] for item in body["unknown"]] == ["3"]


def test_releases_subtitle_filter_puts_missing_info_into_unknown(client):
    res = client.get("/api/media/s462/releases", params={"sub_lang": "zh-hans"})
    body = res.json()
    assert [i["release"]["torrent_id"] for i in body["matched"]] == ["1"]
    assert [i["release"]["torrent_id"] for i in body["excluded"]] == ["2"]
    assert [i["release"]["torrent_id"] for i in body["unknown"]] == ["3"]


def test_releases_sort_by_seeders(client):
    res = client.get("/api/media/s462/releases", params={"sort": "seeders"})
    body = res.json()
    seeders = [i["release"]["seeders"] for i in body["matched"]]
    assert seeders == sorted(seeders, reverse=True)


def test_download_submits_to_qbittorrent_and_records_history(client, ctx):
    res = client.post(
        "/api/download",
        json={"torrent_id": "1", "title": "Frieren S02", "paused": True},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["paused"] is True
    assert body["save_path"] == r"D:\Anime\library"

    added = ctx._qbit.added[0]
    assert added["savepath"] == r"D:\Anime\library"
    assert added["category"] == "anime"
    assert added["paused"] is True
    assert added["torrent_filename"] == "1.torrent"

    history = client.get("/api/downloads").json()["items"]
    assert history[0]["torrent_id"] == "1"
    assert history[0]["status"] == "paused"


def test_download_without_password_returns_400(tmp_path):
    settings = Settings(qbit_password="", db_path=tmp_path / "t.db")
    context = ServiceContext(settings=settings, nekobt=FakeNekoBT(), store=Store(settings.db_path))
    context._qbit = FakeQbit()
    app = create_app()
    app.dependency_overrides[get_context] = lambda: context
    res = TestClient(app).post("/api/download", json={"torrent_id": "1"})
    assert res.status_code == 400
    assert "QBIT_PASSWORD" in res.json()["detail"]


def test_preferences_roundtrip(client):
    payload = {
        "resolutions": ["1080p"],
        "sources": ["BDRip"],
        "video_codecs": ["HEVC"],
        "sub_langs": ["zh-hans"],
        "require_batch": False,
        "min_seeders": 10,
    }
    assert client.post("/api/preferences", json=payload).json()["ok"] is True

    got = client.get("/api/preferences").json()
    assert got["preferences"]["resolutions"] == ["1080p"]
    assert got["preferences"]["min_seeders"] == 10
    assert got["defaults"]["resolutions"][0] == "1080p"


def test_saved_preferences_are_used_when_no_query_filters(client, ctx):
    ctx.store.set_setting(
        "preferences",
        '{"resolutions": ["1080p"], "sources": ["BDRip"], "video_codecs": ["HEVC"], '
        '"sub_langs": [], "require_batch": null, "min_seeders": null}',
    )
    body = client.get("/api/media/s462/releases").json()
    assert [i["release"]["torrent_id"] for i in body["matched"]] == ["1"]


def test_build_context_wires_real_clients(tmp_path):
    settings = Settings(db_path=tmp_path / "x.db", cache_dir=tmp_path / "cache")
    context = build_context(settings)
    assert context.nekobt.base_url.startswith("https://")
    assert context.qbit.url.startswith("http://")
