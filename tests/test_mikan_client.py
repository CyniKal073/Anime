"""蜜柑客户端：RSS 解析与字段映射（离线）。"""

from __future__ import annotations

from src.mikan.client import MikanClient, guess_sub_langs
from src.release.parser import build_release

SAMPLE_RSS = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel>
<item>
  <title>[Snow-Raws] 妄想学生会/Seitokai Yakuindomo (BD 1920x1080 HEVC-YUV420 FLAC)[简繁外挂]</title>
  <link>https://mikanani.me/Home/Episode/5668ac26acdb15714e2a68260a33b4d4f179cc54</link>
  <enclosure type="application/x-bittorrent" length="29957396480"
             url="https://mikanani.me/Download/20201102/5668ac26acdb15714e2a68260a33b4d4f179cc54.torrent" />
  <torrent xmlns="https://mikanani.me/0.1/">
    <contentLength>29957396480</contentLength><pubDate>2020-11-02T12:00:00</pubDate>
  </torrent>
</item>
<item>
  <title>[野生菌字幕组] 剧场版 妄想学生会 2 [WebRip 1080p AVC-8bit AAC][简体内嵌]</title>
  <link>https://mikanani.me/Home/Episode/a288a6011cb68872cb08234a8ebbc92bd5061f36</link>
  <enclosure type="application/x-bittorrent" length="1610612736"
             url="https://mikanani.me/Download/20210706/a288a6011cb68872cb08234a8ebbc92bd5061f36.torrent" />
</item>
</channel></rss>"""


def test_parse_feed_maps_all_fields():
    rows = MikanClient.parse_feed(SAMPLE_RSS)
    assert len(rows) == 2

    first = rows[0]
    assert first["infohash"] == "5668ac26acdb15714e2a68260a33b4d4f179cc54"
    assert first["torrent_url"].endswith(".torrent")
    assert first["filesize"] == 29957396480
    assert first["origin"] == "mikan"
    assert first["hardsub"] is False
    assert first["uploaded_at"] is not None

    second = rows[1]
    assert second["hardsub"] is True  # 「简体内嵌」
    assert "zh-hans" in second["sub_lang"]


def test_parse_feed_skips_broken_xml():
    import pytest

    from src.mikan.client import MikanError

    with pytest.raises(MikanError):
        MikanClient.parse_feed("<rss><channel>")


def test_empty_feed_returns_no_rows():
    assert MikanClient.parse_feed("<rss version='2.0'><channel></channel></rss>") == []


def test_guess_sub_langs():
    assert guess_sub_langs("xxx[简体内嵌]") == ["zh-hans"]
    assert guess_sub_langs("xxx[繁体外挂]") == ["zh-hant"]
    assert guess_sub_langs("xxx[简繁外挂]") == ["zh-hans", "zh-hant"]
    assert guess_sub_langs("xxx[1080p]") == []


def test_mikan_row_builds_into_release():
    """蜜柑的行要能直接喂给现有的 build_release（标题解析逻辑完全复用）。"""
    row = MikanClient.parse_feed(SAMPLE_RSS)[0]
    release = build_release(row)

    assert release.origin == "mikan"
    assert release.source == "BDRip"  # 从标题里的 BD 解析
    assert release.resolution == "1080p"  # 从 1920x1080 解析
    assert release.video_codec == "HEVC"
    assert release.group == "Snow-Raws"
    assert release.filesize == 29957396480
    assert release.torrent_url.endswith(".torrent")
    assert release.seeders is None  # RSS 里没有做种数
