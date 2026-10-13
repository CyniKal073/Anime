"""qBittorrent WebUI API（WebAPI v2）客户端。

版本兼容（实测本机为 qBittorrent Enhanced Edition v5.1.0.11）：

* 暂停/恢复：5.0 起改名 ``stop`` / ``start``，旧 ``pause`` / ``resume`` 已废弃。
  这里优先用新名，404/405 时自动回退旧名，两个版本都能跑。
* 添加任务的暂停字段：4.x / 5.0 用 ``paused``，5.1 起改为 ``stopped``。
  服务端会忽略不认识的字段，所以两个都发，不用探测版本。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

import requests

#: 5.0+ 的接口名
STOP_PATH = "/api/v2/torrents/stop"
START_PATH = "/api/v2/torrents/start"
#: 5.0 之前的接口名，作为回退
PAUSE_PATH = "/api/v2/torrents/pause"
RESUME_PATH = "/api/v2/torrents/resume"


class QBittorrentError(RuntimeError):
    def __init__(self, message: str, *, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


class QBittorrentClient:
    def __init__(
        self,
        url: str = "http://127.0.0.1:8080",
        *,
        username: str = "admin",
        password: str = "",
        session: Optional[Any] = None,
        timeout: float = 20.0,
    ) -> None:
        self.url = url.rstrip("/")
        self.username = username
        self.password = password
        self.session = session if session is not None else requests.Session()
        # 本地服务绝不能走代理（用户的系统代理可能是全局模式）
        self.session.trust_env = False
        self.timeout = timeout
        self._logged_in = False

    # ------------------------------------------------------------------ 鉴权

    def _base_headers(self) -> Dict[str, str]:
        # 登录需要 Referer（CSRF 保护），否则可能被拒。
        return {"Referer": self.url, "Origin": self.url}

    def login(self) -> None:
        try:
            response = self.session.post(
                f"{self.url}/api/v2/auth/login",
                data={"username": self.username, "password": self.password},
                headers=self._base_headers(),
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise QBittorrentError(
                f"连不上 qBittorrent（{self.url}）：{exc}\n"
                "  请确认客户端已启动，且 工具 → 选项 → Web UI 已启用、端口与 .env 中的 QBIT_URL 一致。"
            ) from exc
        status = getattr(response, "status_code", 200)
        body = (getattr(response, "text", "") or "").strip()
        if status == 403 or body == "Fails.":
            raise QBittorrentError(
                "qBittorrent 登录失败：用户名或密码错误。"
                "请检查 .env 中的 QBIT_USERNAME / QBIT_PASSWORD。",
                status=status,
            )
        if status >= 400:
            raise QBittorrentError(f"qBittorrent 登录请求失败 ({status})", status=status)
        self._logged_in = True

    def _ensure_login(self) -> None:
        if not self._logged_in:
            self.login()

    def _request(self, method: str, path: str, **kwargs: Any):
        self._ensure_login()
        headers = {**self._base_headers(), **kwargs.pop("headers", {})}
        try:
            response = self.session.request(
                method, f"{self.url}{path}", headers=headers, timeout=self.timeout, **kwargs
            )
        except requests.RequestException as exc:
            # 连接被拒/超时也要变成可识别的错误，否则会一路冒成 HTTP 500
            raise QBittorrentError(f"连不上 qBittorrent（{self.url}）：{exc}") from exc
        status = getattr(response, "status_code", 200)
        if status == 403:
            # SID 过期或被 CSRF 拒绝，重新登录一次
            self._logged_in = False
            self.login()
            headers = {**self._base_headers(), **kwargs.pop("headers", {})}
            try:
                response = self.session.request(
                    method, f"{self.url}{path}", headers=headers, timeout=self.timeout, **kwargs
                )
            except requests.RequestException as exc:
                raise QBittorrentError(f"连不上 qBittorrent（{self.url}）：{exc}") from exc
            status = getattr(response, "status_code", 200)
        if status >= 400:
            raise QBittorrentError(
                f"qBittorrent 请求失败 ({status})：{getattr(response, 'text', '')[:200]}",
                status=status,
            )
        return response

    # ------------------------------------------------------------------ 自检

    def webapi_version(self) -> str:
        return self._request("GET", "/api/v2/app/webapiVersion").text.strip()

    def app_version(self) -> str:
        return self._request("GET", "/api/v2/app/version").text.strip()

    def check_connection(self) -> str:
        """启动自检：WebUI 未启用 / 端口错误 / 账号密码错误都要在这里就暴露。"""
        return self.webapi_version()

    # ------------------------------------------------------------------ 任务

    def add_torrent(
        self,
        *,
        torrent_bytes: Optional[bytes] = None,
        torrent_filename: str = "release.torrent",
        url: Optional[str] = None,
        savepath: Optional[str] = None,
        category: Optional[str] = None,
        tags: Optional[str] = None,
        paused: bool = False,
    ) -> None:
        if not torrent_bytes and not url:
            raise ValueError("必须提供 torrent_bytes 或 url 之一")

        data: Dict[str, Any] = {}
        if url:
            data["urls"] = url
        if savepath:
            data["savepath"] = savepath
        if category:
            data["category"] = category
        if tags:
            data["tags"] = tags
        # 4.x/5.0 读 paused，5.1+ 读 stopped；多余的字段会被忽略
        state = "true" if paused else "false"
        data["paused"] = state
        data["stopped"] = state

        files = None
        if torrent_bytes:
            files = {
                "torrents": (
                    torrent_filename,
                    torrent_bytes,
                    "application/x-bittorrent",
                )
            }

        self._request("POST", "/api/v2/torrents/add", data=data, files=files)

    def torrents_info(
        self,
        *,
        hashes: Optional[Iterable[str]] = None,
        category: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {}
        if hashes:
            params["hashes"] = "|".join(hashes)
        if category:
            params["category"] = category
        response = self._request("GET", "/api/v2/torrents/info", params=params)
        return response.json()

    @staticmethod
    def _hashes_value(hashes: Sequence[str]) -> str:
        """注意：必须显式给出 hash。

        以前这里空列表会退化成 ``all``，那意味着一次误调用就能停掉/删掉所有任务，
        太危险，所以直接拒绝空输入。
        """
        if not hashes:
            raise ValueError("必须提供至少一个 torrent hash")
        return "|".join(hashes)

    def _post_with_fallback(self, paths: Sequence[str], data: Dict[str, Any]) -> str:
        last_error: Optional[QBittorrentError] = None
        for path in paths:
            try:
                self._request("POST", path, data=data)
                return path
            except QBittorrentError as exc:
                if exc.status not in (404, 405):
                    raise
                last_error = exc
        raise last_error or QBittorrentError("没有可用的接口")

    def stop(self, hashes: Sequence[str]) -> str:
        return self._post_with_fallback([STOP_PATH, PAUSE_PATH], {"hashes": self._hashes_value(hashes)})

    def start(self, hashes: Sequence[str]) -> str:
        return self._post_with_fallback([START_PATH, RESUME_PATH], {"hashes": self._hashes_value(hashes)})

    def delete(self, hashes: Sequence[str], *, delete_files: bool = False) -> None:
        self._request(
            "POST",
            "/api/v2/torrents/delete",
            data={"hashes": self._hashes_value(hashes), "deleteFiles": "true" if delete_files else "false"},
        )

    def probe_task_endpoints(self) -> Dict[str, str]:
        """探测服务端支持哪组接口名，用于自检。

        用一个不存在的 hash 探测：qBittorrent 对未知 hash 是空操作，不会影响真实任务。
        """
        bogus = "0" * 40
        result: Dict[str, str] = {}
        for label, path in (("stop", STOP_PATH), ("pause", PAUSE_PATH)):
            try:
                self._request("POST", path, data={"hashes": bogus})
                result[label] = "ok"
            except QBittorrentError as exc:
                result[label] = f"http {exc.status}"
        return result
