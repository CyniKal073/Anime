"""射手网（assrt.net）字幕库客户端。

实测可用性::

    GET https://api.assrt.net/v1/sub/search?q=...&token=...
    带上无效 token → HTTP 400 {"status":20001,"errmsg":"invalid token"}

也就是说接口本身是通的，只差 token。token 在 assrt.net 登录后于个人页面获取，
放进 .env 的 ASSRT_TOKEN。

注意：用户提供的文档地址 ``https://assrt.net/api/doc`` 实测返回 404，
站点根目录也是 404，所以下面的字段解析按 v1 接口的通行形态写成**容错取值**：
取不到就当空，不抛异常——拿到 token 后再按真实返回微调。
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import requests

from ..net import build_session

ASSRT_SOURCE = "assrt"
BASE_URL = "https://api.assrt.net/v1"
#: 文档里写明的替代域名（原文：「由于网络原因，也可以使用以下替代域名访问」）
ALTERNATE_BASE_URL = "https://api.makedie.me/v1"
DEFAULT_USER_AGENT = "anime-release-manager/0.1 (https://github.com/CyniKal073)"

#: 文档：q 至少 3 个字符；cnt 最多一次返回 15 个
MIN_QUERY_LENGTH = 3
MAX_COUNT = 15

#: langlist 里的键 → 我们内部用的语言代码
_LANG_KEYS = {
    "langchs": "zh_chs",
    "langcht": "zh_cht",
    "langeng": "en",
    "langjpn": "jpn",
    "langjap": "jpn",
}


class AssrtError(RuntimeError):
    """射手网请求失败。"""


def _as_list(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if item]
    return [str(value)]


def _langs_from(lang: Any) -> List[str]:
    """``langlist`` 实测是对象（{"langchs": true}），不是数组。"""
    if not isinstance(lang, dict):
        return []
    raw = lang.get("langlist")
    if isinstance(raw, dict):
        return [_LANG_KEYS.get(key, key) for key, enabled in raw.items() if enabled]
    return [_LANG_KEYS.get(item, item) for item in _as_list(raw)]


class AssrtClient:
    def __init__(
        self,
        token: Optional[str] = None,
        *,
        base_url: str = BASE_URL,
        session: Optional[Any] = None,
        timeout: float = 25.0,
        user_agent: str = DEFAULT_USER_AGENT,
        min_interval: float = 12.0,
    ) -> None:
        self.token = (token or "").strip()
        self.base_url = base_url.rstrip("/")
        self.session = session if session is not None else build_session()
        self.timeout = timeout
        self.user_agent = user_agent
        self.min_interval = max(0.0, float(min_interval))
        self._last_request = 0.0

    #: 两次请求之间的最小间隔（秒）。官方文档写「初始配额 20 次/分钟」，
    #: 但实际配额因人而异——实测账号可能只有 5 次/分钟（每 12 秒一次），
    #: 超了会报 "you are exceeding request limits"。默认按最保守的 12 秒，
    #: 可用 .env 的 ASSRT_MIN_INTERVAL 调整。
    DEFAULT_MIN_INTERVAL = 12.0

    def _bases(self) -> List[str]:
        bases = [self.base_url]
        if "api.assrt.net" in self.base_url:
            bases.append(ALTERNATE_BASE_URL)
        return bases

    # ------------------------------------------------------------------ 内部

    def _get(
        self, path: str, params: Dict[str, Any], *, attempts: int = 4
    ) -> Dict[str, Any]:
        if not self.token:
            raise AssrtError(
                "未配置射手网 token。请到 assrt.net 登录后获取 API token，"
                "填进 .env 的 ASSRT_TOKEN。"
            )
        # 文档给了两种传法，这里用 Authorization 头：token 不会出现在
        # URL 里，也就不会被打进日志或异常信息（之前就泄漏过一次）。
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
        }
        query = {k: v for k, v in params.items() if v is not None}

        gap = time.time() - self._last_request
        if gap < self.min_interval:
            time.sleep(self.min_interval - gap)

        # 实测这条链路不稳定：直连/代理都可能出现 SSL EOF，重试几次就能过；
        # 主域名持续失败时换文档给的替代域名再试。
        response = None
        last_error: Optional[Exception] = None
        for base in self._bases():
            url = f"{base}{path}"
            for attempt in range(1, max(1, attempts) + 1):
                try:
                    self._last_request = time.time()
                    response = self.session.get(
                        url, params=query, headers=headers, timeout=self.timeout
                    )
                    break
                except requests.RequestException as exc:
                    last_error = exc
                    if attempt < attempts:
                        time.sleep(1.5 * attempt)
            if response is not None:
                break
        if response is None:
            raise AssrtError(f"射手网请求失败（主备域名都试过了）：{last_error}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise AssrtError(f"射手网返回的不是 JSON（HTTP {response.status_code}）") from exc

        status = payload.get("status")
        if status not in (0, None):
            raise AssrtError(f"射手网接口报错：{payload.get('errmsg') or status}")
        return payload

    # ------------------------------------------------------------------ 检索

    def search(self, query: str, *, count: int = 15, page: int = 0) -> List[Dict[str, Any]]:
        """按关键词搜字幕，返回归一化后的条目列表。"""
        query = (query or "").strip()
        # 文档：q 至少 3 个字符，cnt 最多 15
        if len(query) < MIN_QUERY_LENGTH:
            return []
        payload = self._get(
            "/sub/search", {"q": query, "cnt": max(1, min(count, MAX_COUNT)), "pos": page}
        )
        block = payload.get("sub") or {}
        rows = block.get("subs") or []

        items: List[Dict[str, Any]] = []
        for row in rows:
            lang = row.get("lang") or {}
            items.append(
                {
                    "sub_id": row.get("id"),
                    # 实测字段：native_name 经常为空，退回 videoname / sub_name
                    "title": row.get("native_name")
                    or row.get("sub_name")
                    or row.get("videoname")
                    or "",
                    "filename": row.get("filename") or "",
                    "format": (row.get("subtype") or row.get("format") or "").lower() or None,
                    "langs": _langs_from(lang),
                    "lang_desc": (lang.get("desc") if isinstance(lang, dict) else None) or "",
                    "score": row.get("vote_score", row.get("score")),
                    "upload_time": row.get("upload_time") or "",
                    "release_site": row.get("release_site") or "",
                    # 其余原始字段也带上：界面可以展示，排查时也有据可查
                    "native_name": row.get("native_name") or "",
                    "videoname": row.get("videoname") or "",
                    "sub_name": row.get("sub_name") or "",
                    "revision": row.get("revision"),
                    "down_count": row.get("down_count"),
                    "view_count": row.get("view_count"),
                }
            )
        return items

    def detail(self, sub_id: int) -> Dict[str, Any]:
        """取字幕详情，重点是里面的文件列表（下载地址）。"""
        payload = self._get("/sub/detail", {"id": sub_id})
        block = payload.get("sub") or {}
        # 实测：详情也是 sub.subs 数组（和搜索一致），不是直接的 sub 对象
        if isinstance(block, dict):
            rows = block.get("subs")
            if isinstance(rows, list):
                block = rows[0] if rows else {}
            # 没有 subs 时，block 本身可能就是条目
        elif isinstance(block, list):
            block = block[0] if block else {}

        files = []
        for item in block.get("filelist") or []:
            if isinstance(item, dict):
                files.append({"name": item.get("f") or item.get("name") or "", "url": item.get("url") or ""})
            elif isinstance(item, str):
                files.append({"name": item, "url": item})
        return {
            "sub_id": block.get("id", sub_id),
            "title": block.get("title") or block.get("native_name") or "",
            "subtype": (block.get("subtype") or "").lower() or None,
            "size": block.get("size"),
            #: 整包下载地址（可能是 zip，所以优先用 filelist 里的单个文件）
            "package_url": block.get("url") or "",
            "files": [item for item in files if item["url"]],
        }

    def download(self, url: str) -> Any:
        """下载字幕文件内容（返回字节）。"""
        if not url:
            raise AssrtError("字幕下载地址为空")
        try:
            response = self.session.get(
                url, headers={"User-Agent": self.user_agent}, timeout=self.timeout
            )
        except requests.RequestException as exc:
            raise AssrtError(f"字幕下载失败：{exc}") from exc
        if response.status_code >= 400:
            raise AssrtError(f"字幕下载失败：HTTP {response.status_code}")
        return response.content

    def is_configured(self) -> bool:
        return bool(self.token)
