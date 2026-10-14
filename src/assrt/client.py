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

from typing import Any, Dict, List, Optional

import requests

from ..net import build_session

ASSRT_SOURCE = "assrt"
BASE_URL = "https://api.assrt.net/v1"
DEFAULT_USER_AGENT = "anime-release-manager/0.1 (https://github.com/CyniKal073)"


class AssrtError(RuntimeError):
    """射手网请求失败。"""


def _as_list(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if item]
    return [str(value)]


class AssrtClient:
    def __init__(
        self,
        token: Optional[str] = None,
        *,
        base_url: str = BASE_URL,
        session: Optional[Any] = None,
        timeout: float = 25.0,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self.token = (token or "").strip()
        self.base_url = base_url.rstrip("/")
        self.session = session if session is not None else build_session()
        self.timeout = timeout
        self.user_agent = user_agent

    # ------------------------------------------------------------------ 内部

    def _get(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        if not self.token:
            raise AssrtError(
                "未配置射手网 token。请到 assrt.net 登录后获取 API token，"
                "填进 .env 的 ASSRT_TOKEN。"
            )
        query = {"token": self.token, **{k: v for k, v in params.items() if v is not None}}
        url = f"{self.base_url}{path}"
        try:
            response = self.session.get(
                url,
                params=query,
                headers={"User-Agent": self.user_agent, "Accept": "application/json"},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise AssrtError(f"射手网请求失败：{exc}") from exc

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
        if not query:
            return []
        payload = self._get("/sub/search", {"q": query, "cnt": count, "pos": page})
        block = payload.get("sub") or {}
        rows = block.get("subs") or []

        items: List[Dict[str, Any]] = []
        for row in rows:
            lang = row.get("lang") or {}
            items.append(
                {
                    "sub_id": row.get("id"),
                    "title": row.get("native_name") or row.get("filename") or "",
                    "filename": row.get("filename") or "",
                    "format": (row.get("format") or "").lower() or None,
                    "langs": _as_list(lang.get("langlist") if isinstance(lang, dict) else lang),
                    "lang_desc": (lang.get("desc") if isinstance(lang, dict) else None) or "",
                    "score": row.get("score"),
                    "upload_time": row.get("upload_time") or "",
                    "release_site": row.get("release_site") or "",
                }
            )
        return items

    def detail(self, sub_id: int) -> Dict[str, Any]:
        """取字幕详情，重点是里面的文件列表（下载地址）。"""
        payload = self._get("/sub/detail", {"id": sub_id})
        block = payload.get("sub") or {}
        # 详情接口的 sub 有时是对象，有时是单元素列表
        if isinstance(block, list):
            block = block[0] if block else {}
        files = []
        for item in block.get("filelist") or []:
            if isinstance(item, dict):
                files.append({"name": item.get("f") or item.get("name") or "", "url": item.get("url") or ""})
            elif isinstance(item, str):
                files.append({"name": item, "url": item})
        return {
            "sub_id": block.get("id", sub_id),
            "title": block.get("native_name") or "",
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
