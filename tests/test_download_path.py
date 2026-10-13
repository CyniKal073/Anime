"""自定义下载目录：优先级与校验。"""

from __future__ import annotations

import pytest

from src.config import Settings
from src.service import (
    DOWNLOAD_PATH_KEY,
    ServiceContext,
    download_paths,
    effective_save_path,
    normalize_save_path,
    set_download_path,
)
from src.storage import Store


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.service.ensure_running", lambda settings, **kwargs: {"ok": True, "launched": False}
    )
    settings = Settings(
        qbit_password="secret",
        qbit_savepath=r"D:\Anime\library",
        db_path=tmp_path / "t.db",
        cache_dir=tmp_path / "cache",
    )
    return ServiceContext(settings=settings, nekobt=object(), store=Store(settings.db_path))


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("D:\\Anime\\library", "D:\\Anime\\library"),
        ('  "D:\\Anime\\library"  ', "D:\\Anime\\library"),
        ("D:\\Anime\\library\\", "D:\\Anime\\library"),
        ("   ", None),
        (None, None),
    ],
)
def test_normalize_save_path(raw, expected):
    assert normalize_save_path(raw) == expected


def test_normalize_save_path_rejects_invalid_windows_chars():
    with pytest.raises(ValueError) as excinfo:
        normalize_save_path("D:\\Anime\\bad?name")
    assert "不允许的字符" in str(excinfo.value)


def test_default_comes_from_env_then_override(ctx):
    # 一开始用 .env 里的值
    assert effective_save_path(ctx) == ctx.settings.qbit_savepath
    assert download_paths(ctx)["source"] == "env"

    # 设过之后用界面里的值
    set_download_path(ctx, r"E:\Anime\old-shows")
    assert effective_save_path(ctx) == r"E:\Anime\old-shows"
    payload = download_paths(ctx)
    assert payload["source"] == "custom"
    assert payload["env_path"] == ctx.settings.qbit_savepath
    assert payload["path"] == r"E:\Anime\old-shows"

    # 传空字符串表示恢复 .env 的值
    set_download_path(ctx, "")
    assert effective_save_path(ctx) == ctx.settings.qbit_savepath
    assert ctx.store.get_setting(DOWNLOAD_PATH_KEY) == ""


def test_download_paths_suggests_history_and_subfolders(ctx, tmp_path):
    root = tmp_path / "library"
    (root / "某部番").mkdir(parents=True)
    set_download_path(ctx, str(root))
    ctx.store.record_download(
        torrent_id="1",
        torrent_hash=None,
        title="x",
        save_path=r"D:\别的地方",
        client="qbittorrent",
        status="started",
    )

    payload = download_paths(ctx)
    assert str(root) in payload["suggestions"]
    assert str(root / "某部番") in payload["suggestions"]  # 已存在的子文件夹
    assert r"D:\别的地方" in payload["suggestions"]  # 历史用过


def test_submit_download_uses_custom_path(monkeypatch, ctx):
    """本次请求指定 > 界面默认 > .env。"""
    from src.service import submit_download

    captured = {}

    class FakeQbit:
        def add_torrent(self, **kwargs):
            captured.update(kwargs)

        def torrents_info(self, **kwargs):
            return []

    ctx._qbit = FakeQbit()
    monkeypatch.setattr("src.service.ensure_running", lambda settings, **kw: {"ok": True})
    monkeypatch.setattr(
        "src.service._fetch_torrent", lambda url, user_agent: (b"d4:infod4:name4:teste", "x.torrent")
    )

    # 1) 界面设过默认 → 用它
    set_download_path(ctx, r"E:\Anime\old-shows")
    submit_download(ctx, torrent_url="https://example.com/x.torrent", title="t")
    assert captured["savepath"] == r"E:\Anime\old-shows"

    # 2) 本次显式指定 → 覆盖默认
    submit_download(
        ctx, torrent_url="https://example.com/x.torrent", title="t", save_path=r"F:\这次的"
    )
    assert captured["savepath"] == r"F:\这次的"

    # 3) 都没设 → 回到 .env
    set_download_path(ctx, "")
    submit_download(ctx, torrent_url="https://example.com/x.torrent", title="t")
    assert captured["savepath"] == ctx.settings.qbit_savepath
