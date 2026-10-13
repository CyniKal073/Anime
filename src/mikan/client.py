"""蜜柑计划（Mikan）客户端。

为什么需要它
------------
nekoBT 的中文库很薄，老片源基本没有（实测《妄想学生会》在 nekoBT 上
``torrents/search`` 返回 0 条，``media/search`` 的最高相似度只有 0.0833）。
蜜柑是中文向的聚合站，RSS 一次请求就能拿到全部所需信息：

* ``<title>``        标题，含字幕组、规格、分辨率、编码，还有「简体内嵌」这类标记
* ``<enclosure>``    种子的 **.torrent 直链**（不需要再请求详情页）
* ``<contentLength>`` 体积
* ``<pubDate>``      发布时间

代价是 RSS 里**没有做种数**，所以蜜柑的候选在「按做种数排序」上没有意义。
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

from ..net import build_session

MIKAN_SOURCE = "mikan"
BASE_URL = "https://mikanani.me"
SEARCH_RSS_PATH = "/RSS/Search"
DEFAULT_USER_AGENT = "anime-release-manager/0.1 (https://github.com/CyniKal073)"

#: 标题里出现这些词就认为是中文字幕
_SUB_PATTERNS = (
    # 「简繁」表示同时提供简体和繁体，两个语言都要记上
    (re.compile(r"简体|简中|简日|简繁|chs\b|sc\b|gb\b", re.IGNORECASE), "zh-hans"),
    (re.compile(r"繁体|繁中|繁日|简繁|cht\b|tc\b|big5", re.IGNORECASE), "zh-hant"),
)
#: 「内嵌」表示字幕烧进画面（硬字幕），和「外挂 / 字幕」相对
_HARDSUB = re.compile(r"内嵌|硬字幕|hardsub", re.IGNORECASE)
#: 种子直链里那串 40 位十六进制就是 infohash
_INFOHASH = re.compile(r"/Download/[^/]+/([0-9a-fA-F]{40})\.torrent")


class MikanError(RuntimeError):
    """蜜柑请求或解析失败。"""


def guess_sub_langs(title: str) -> List[str]:
    """从标题里粗略判断字幕语言（蜜柑没有结构化字段，只能猜）。"""
    found: List[str] = []
    for pattern, lang in _SUB_PATTERNS:
        if pattern.search(title or "") and lang not in found:
            found.append(lang)
    return found


def _parse_pubdate(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


class MikanClient:
    def __init__(
        self,
        base_url: str = BASE_URL,
        *,
        session: Optional[Any] = None,
        timeout: float = 30.0,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = session if session is not None else build_session()
        self.timeout = timeout
        self.user_agent = user_agent

    def _headers(self) -> Dict[str, str]:
        return {"User-Agent": self.user_agent, "Accept": "application/rss+xml, */*"}

    def search(self, keyword: str, *, limit: int = 100) -> List[Dict[str, Any]]:
        """按关键词搜索，返回已归一化成 Release 原始行的列表。"""
        keyword = (keyword or "").strip()
        if not keyword:
            return []
        url = f"{self.base_url}{SEARCH_RSS_PATH}"
        try:
            response = self.session.get(
                url, params={"searchstr": keyword}, headers=self._headers(), timeout=self.timeout
            )
        except requests.RequestException as exc:
            raise MikanError(f"蜜柑请求失败：{exc}") from exc
        if getattr(response, "status_code", 200) >= 400:
            raise MikanError(f"蜜柑返回 {response.status_code}")
        return self.parse_feed(response.content if hasattr(response, "content") else response.text, limit)

    @staticmethod
    def parse_feed(payload, limit: int = 100) -> List[Dict[str, Any]]:
        """解析 RSS，转换成 ``build_release`` 能直接吃的行。"""
        if isinstance(payload, bytes):
            text = payload.decode("utf-8", errors="replace")
        else:
            text = str(payload)
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            raise MikanError(f"蜜柑 RSS 解析失败：{exc}") from exc

        rows: List[Dict[str, Any]] = []
        for item in root.findall(".//item")[:limit]:
            title = (item.findtext("title") or "").strip()
            if not title:
                continue

            enclosure = item.find("enclosure")
            torrent_url = (enclosure.get("url") if enclosure is not None else "") or ""
            size_text = (enclosure.get("length") if enclosure is not None else "") or ""

            content_length = None
            for child in item.iter():
                if child.tag.endswith("contentLength") and child.text:
                    content_length = child.text.strip()
                    break
            pub_date = None
            for child in item.iter():
                if child.tag.endswith("pubDate") and child.text:
                    pub_date = child.text.strip()
                    break

            try:
                filesize = int(content_length or size_text or 0) or None
            except ValueError:
                filesize = None

            match = _INFOHASH.search(torrent_url)
            infohash = match.group(1).lower() if match else None

            rows.append(
                {
                    "id": infohash or torrent_url,
                    "title": title,
                    "media_id": None,
                    "infohash": infohash,
                    "magnet": None,
                    "torrent_url": torrent_url,
                    "sub_lang": ",".join(guess_sub_langs(title)),
                    "fsub_lang": "",
                    "video_codec": 0,  # 交给标题解析
                    "video_type": 0,
                    "batch": None,
                    "upgraded": None,
                    "seeders": None,  # RSS 里没有
                    "leechers": None,
                    "filesize": filesize,
                    "hardsub": bool(_HARDSUB.search(title)),
                    "uploaded_at": _parse_pubdate(pub_date),
                    "origin": MIKAN_SOURCE,
                }
            )
        return rows
