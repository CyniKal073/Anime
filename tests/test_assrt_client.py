"""射手网字幕库客户端（离线）。"""

from __future__ import annotations

import pytest

from src.assrt.client import AssrtClient, AssrtError
from src.mkv.tools import normalize_language


class FakeResponse:
    def __init__(self, payload=None, *, status_code=200, content=b""):
        self._payload = payload
        self.status_code = status_code
        self.content = content
        self.text = ""

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return self.responses.pop(0)


SEARCH_PAYLOAD = {
    "status": 0,
    "sub": {
        "subs": [
            {
                "id": 123456,
                "native_name": "妄想学生会 第一季",
                "filename": "seitokai_01.ass",
                "format": "ass",
                "lang": {"langlist": ["zh_chs"], "desc": "简体"},
                "score": 3,
                "upload_time": "2020-01-01",
                "release_site": "dmhy",
            }
        ],
        "pos": 0,
        "cnt": 15,
        "total": 1,
    },
}


def test_search_normalizes_items():
    session = FakeSession([FakeResponse(SEARCH_PAYLOAD)])
    # 测试里关掉请求间隔，否则每次调用都要真等 12 秒
    client = AssrtClient("token123", session=session, min_interval=0)

    items = client.search("妄想学生会")

    assert len(items) == 1
    item = items[0]
    assert item["sub_id"] == 123456
    assert item["title"] == "妄想学生会 第一季"
    assert item["format"] == "ass"
    assert item["langs"] == ["zh_chs"]
    assert item["lang_desc"] == "简体"
    # token 走 Authorization 头（不进 URL，避免被日志记录），关键词走参数
    assert session.calls[0]["headers"]["Authorization"] == "Bearer token123"
    assert "token" not in session.calls[0]["params"]
    assert session.calls[0]["params"]["q"] == "妄想学生会"
    assert session.calls[0]["params"]["cnt"] <= 15  # 文档：cnt 最多 15


def test_search_rejects_too_short_query():
    """文档：q 至少 3 个字符。"""
    session = FakeSession([])
    client = AssrtClient("token123", session=session, min_interval=0)
    assert client.search("ab") == []
    assert session.calls == []


def test_count_is_clamped_to_documented_max():
    session = FakeSession([FakeResponse(SEARCH_PAYLOAD)])
    client = AssrtClient("token123", session=session, min_interval=0)
    client.search("妄想学生会", count=99)
    assert session.calls[0]["params"]["cnt"] == 15


def test_missing_token_says_what_to_do():
    client = AssrtClient("", session=FakeSession([]), min_interval=0)
    with pytest.raises(AssrtError) as excinfo:
        client.search("妄想学生会")
    message = str(excinfo.value)
    assert "ASSRT_TOKEN" in message
    assert client.is_configured() is False


def test_api_error_status_becomes_readable():
    session = FakeSession([FakeResponse({"status": 20001, "errmsg": "invalid token"})])
    client = AssrtClient("bad", session=session, min_interval=0)
    with pytest.raises(AssrtError) as excinfo:
        client.search("妄想学生会")
    assert "invalid token" in str(excinfo.value)


def test_detail_extracts_file_list():
    payload = {
        "status": 0,
        "sub": {
            "id": 123456,
            "native_name": "妄想学生会",
            "filelist": [{"f": "seitokai.ass", "url": "https://example.com/a.ass"}],
        },
    }
    session = FakeSession([FakeResponse(payload)])
    client = AssrtClient("token123", session=session, min_interval=0)

    detail = client.detail(123456)
    assert detail["files"] == [{"name": "seitokai.ass", "url": "https://example.com/a.ass"}]


def test_detail_tolerates_list_shaped_sub():
    payload = {"status": 0, "sub": [{"id": 1, "filelist": ["https://example.com/b.srt"]}]}
    session = FakeSession([FakeResponse(payload)])
    client = AssrtClient("token123", session=session, min_interval=0)
    detail = client.detail(1)
    assert detail["files"] == [{"name": "https://example.com/b.srt", "url": "https://example.com/b.srt"}]


def test_empty_search_returns_nothing_without_request():
    session = FakeSession([])
    client = AssrtClient("token123", session=session, min_interval=0)
    assert client.search("   ") == []
    assert session.calls == []


def test_assrt_language_codes_map_to_chinese_track_language():
    """射手网给的是 zh_chs / zh_cht，mkvmerge 要的是 chi。"""
    assert normalize_language("zh_chs") == "chi"
    assert normalize_language("zh_cht") == "chi"


def test_download_retries_transient_502(monkeypatch):
    """「字幕下载失败：HTTP 502」是射手网下载服务器偶发的，重试一次就能过。

    批量嵌入里一组失败就整批显示「有失败项」，所以这里必须自己重试。
    """
    monkeypatch.setattr("src.assrt.client.time.sleep", lambda _seconds: None)
    session = FakeSession(
        [
            FakeResponse(status_code=502),
            FakeResponse(status_code=200, content=b"subtitle-bytes"),
        ]
    )
    client = AssrtClient("token123", session=session, min_interval=0)
    assert client.download("https://example.com/a.ass") == b"subtitle-bytes"
    assert len(session.calls) == 2


def test_download_does_not_retry_404(monkeypatch):
    """4xx 是文件本身的问题（没了/没权限），重试只是白等。"""
    monkeypatch.setattr("src.assrt.client.time.sleep", lambda _seconds: None)
    session = FakeSession([FakeResponse(status_code=404)])
    client = AssrtClient("token123", session=session, min_interval=0)
    with pytest.raises(AssrtError) as excinfo:
        client.download("https://example.com/gone.ass")
    assert "HTTP 404" in str(excinfo.value)
    assert len(session.calls) == 1


def test_search_retries_5xx_then_succeeds(monkeypatch):
    """检索遇到 502 时不能直接抛「返回的不是 JSON（HTTP 502）」。"""
    monkeypatch.setattr("src.assrt.client.time.sleep", lambda _seconds: None)
    session = FakeSession([FakeResponse(status_code=502), FakeResponse(SEARCH_PAYLOAD)])
    client = AssrtClient("token123", session=session, min_interval=0)
    assert len(client.search("妄想学生会")) == 1
    assert len(session.calls) == 2
