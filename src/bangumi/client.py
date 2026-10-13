"""Bangumi API 客户端（可选）。

实测：开发机上 bgm.tv / api.bgm.tv 均不可达，因此这里的一切调用都必须是「可失败」的，
Bangumi 不可用时主流程照常运行。
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import requests

from ..net import build_session


class BangumiError(RuntimeError):
    pass


class BangumiUnavailable(BangumiError):
    """网络不可达或服务不可用。"""


class BangumiClient:
    def __init__(
        self,
        base_url: str = "https://api.bgm.tv",
        *,
        user_agent: str = "anime-release-manager/0.1 (https://github.com/)",
        session: Optional[Any] = None,
        timeout: float = 8.0,
        availability_ttl: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.user_agent = user_agent
        # 走系统代理：api.bgm.tv 直连会被污染，必须和浏览器一样走代理
        self.session = session if session is not None else build_session()
        self.timeout = timeout
        self.availability_ttl = availability_ttl
        self._available: Optional[bool] = None
        self._checked_at = 0.0

    def _headers(self) -> Dict[str, str]:
        return {"User-Agent": self.user_agent, "Accept": "application/json"}

    def is_available(self, *, refresh: bool = False) -> bool:
        """带 TTL 的可用性判断，避免每次搜索都去探一次。"""
        now = time.time()
        if (
            not refresh
            and self._available is not None
            and now - self._checked_at < self.availability_ttl
        ):
            return self._available
        try:
            response = self.session.get(self.base_url, headers=self._headers(), timeout=self.timeout)
        except Exception:  # noqa: BLE001
            self._available = False
        else:
            self._available = getattr(response, "status_code", 0) < 500
        self._checked_at = now
        return self._available

    def search_subjects(self, keyword: str, *, limit: int = 5) -> List[Dict[str, Any]]:
        try:
            response = self.session.post(
                f"{self.base_url}/v0/search/subjects",
                params={"limit": limit},
                json={"keyword": keyword, "filter": {"type": [2]}},
                headers=self._headers(),
                timeout=self.timeout,
            )
        except Exception as exc:  # noqa: BLE001
            self._available = False
            self._checked_at = time.time()
            raise BangumiUnavailable(f"Bangumi 不可达：{exc}") from exc
        if getattr(response, "status_code", 0) >= 400:
            raise BangumiError(f"Bangumi 搜索失败 ({response.status_code})")
        return list((response.json() or {}).get("data") or [])

    def get_subject(self, subject_id: int) -> Dict[str, Any]:
        try:
            response = self.session.get(
                f"{self.base_url}/v0/subjects/{subject_id}",
                headers=self._headers(),
                timeout=self.timeout,
            )
        except Exception as exc:  # noqa: BLE001
            raise BangumiUnavailable(f"Bangumi 不可达：{exc}") from exc
        if getattr(response, "status_code", 0) >= 400:
            raise BangumiError(f"Bangumi 详情失败 ({response.status_code})")
        return response.json() or {}

    # ------------------------------------------------------------------ 新番

    @staticmethod
    def pick_image(images: Optional[Dict[str, Any]]) -> Optional[str]:
        """从 images 里挑一张适合列表展示的图（优先 common）。"""
        if not images:
            return None
        for key in ("common", "medium", "large", "small", "grid"):
            url = images.get(key)
            if url:
                return str(url).replace("http://", "https://", 1)
        return None

    def calendar(self) -> List[Dict[str, Any]]:
        """每日放送（正在连载的番剧），按星期分组。

        返回结构已归一化，只保留界面需要的字段：

            [{"weekday": 1, "weekday_cn": "星期一",
              "items": [{"bangumi_id", "name_cn", "name", "image", "air_date", "url"}]}]
        """
        try:
            response = self.session.get(
                f"{self.base_url}/calendar", headers=self._headers(), timeout=self.timeout
            )
        except Exception as exc:  # noqa: BLE001
            self._available = False
            self._checked_at = time.time()
            raise BangumiUnavailable(f"Bangumi 不可达：{exc}") from exc
        if getattr(response, "status_code", 0) >= 400:
            raise BangumiError(f"Bangumi 每日放送失败 ({response.status_code})")

        days: List[Dict[str, Any]] = []
        for day in response.json() or []:
            weekday = day.get("weekday") or {}
            if isinstance(weekday, dict):
                weekday_id = weekday.get("id")
                weekday_cn = weekday.get("cn") or weekday.get("en")
            else:
                weekday_id, weekday_cn = None, str(weekday)
            items = []
            for item in day.get("items") or []:
                items.append(
                    {
                        "bangumi_id": item.get("id"),
                        "name_cn": item.get("name_cn") or None,
                        "name": item.get("name"),
                        "image": self.pick_image(item.get("images")),
                        "air_date": item.get("air_date"),
                        "url": item.get("url"),
                    }
                )
            days.append({"weekday": weekday_id, "weekday_cn": weekday_cn, "items": items})
        return days
