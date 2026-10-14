"""配置读取与校验。

优先级：构造参数 > 进程环境变量 > .env > 默认值。
python-dotenv 是可选依赖：装了就用它，没装也有一个最小解析器兜底。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

DEFAULT_SAVEPATH = r"D:\Anime\library"
DEFAULT_QBIT_EXE = r"F:\qBittorrentEE\qbittorrent_x64.exe"
DEFAULT_MKV_TOOLS_DIR = r"F:\MKVToolNix"
DEFAULT_DB_PATH = Path("data") / "anime_release_manager.db"
DEFAULT_CACHE_DIR = Path("data") / "cache"


class ConfigError(RuntimeError):
    """配置缺失或非法。"""


def _parse_dotenv_line(line: str) -> Optional[tuple]:
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        return None
    key, _, value = line.partition("=")
    return key.strip(), value.strip().strip('"').strip("'")


def load_dotenv_best_effort(env_file: Optional[Path] = None) -> None:
    """尽量加载 .env；不存在或缺依赖时静默跳过。"""
    path = Path(env_file) if env_file else Path(".env")
    if not path.exists():
        return
    try:
        from dotenv import load_dotenv  # type: ignore
    except ImportError:
        for raw in path.read_text(encoding="utf-8").splitlines():
            parsed = _parse_dotenv_line(raw)
            if parsed:
                # setdefault：真实环境变量优先于 .env
                os.environ.setdefault(parsed[0], parsed[1])
        return
    load_dotenv(path)


def _get_float(env: Mapping[str, str], key: str, default: float) -> float:
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} 必须是数字，当前值：{raw!r}") from exc


def _get_int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} 必须是整数，当前值：{raw!r}") from exc


@dataclass(frozen=True)
class Settings:
    nekobt_base_url: str = "https://nekobt.to"
    nekobt_api_key: Optional[str] = None

    qbit_url: str = "http://127.0.0.1:8080"
    qbit_username: str = "admin"
    qbit_password: str = ""
    qbit_savepath: str = DEFAULT_SAVEPATH
    qbit_category: str = "anime"
    qbit_tags: str = "nekobt"
    #: 需要下载时若 qBittorrent 没在运行，就尝试用这个可执行文件把它拉起来
    qbit_executable: str = DEFAULT_QBIT_EXE
    #: MKVToolNix 所在目录（mkvmerge / mkvextract）
    mkv_tools_dir: str = DEFAULT_MKV_TOOLS_DIR
    #: 射手网 API token（assrt.net 登录后获取）
    assrt_token: Optional[str] = None
    #: 射手网两次请求的最小间隔（秒）。官方文档说 20 次/分钟，实际配额因人而异
    #: （实测有 5 次/分钟 = 12 秒），默认取保守值。
    assrt_min_interval: float = 12.0

    http_timeout: float = 20.0
    http_retries: int = 3
    user_agent: str = "anime-release-manager/0.1"

    db_path: Path = DEFAULT_DB_PATH
    cache_dir: Path = DEFAULT_CACHE_DIR

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Settings":
        source: Mapping[str, str] = os.environ if env is None else env

        def raw(key: str, default: Optional[str] = None) -> Optional[str]:
            value = source.get(key)
            if value is None or value.strip() == "":
                return default
            return value.strip()

        return cls(
            nekobt_base_url=raw("NEKOBT_BASE_URL", "https://nekobt.to") or "https://nekobt.to",
            nekobt_api_key=raw("NEKOBT_API_KEY"),
            qbit_url=raw("QBIT_URL", "http://127.0.0.1:8080") or "http://127.0.0.1:8080",
            qbit_username=raw("QBIT_USERNAME", "admin") or "admin",
            qbit_password=raw("QBIT_PASSWORD", "") or "",
            qbit_savepath=raw("QBIT_SAVEPATH", DEFAULT_SAVEPATH) or DEFAULT_SAVEPATH,
            qbit_category=raw("QBIT_CATEGORY", "anime") or "anime",
            qbit_tags=raw("QBIT_TAGS", "nekobt") or "nekobt",
            qbit_executable=raw("QBIT_EXECUTABLE", DEFAULT_QBIT_EXE) or DEFAULT_QBIT_EXE,
            mkv_tools_dir=raw("MKV_TOOLS_DIR", DEFAULT_MKV_TOOLS_DIR) or DEFAULT_MKV_TOOLS_DIR,
            assrt_token=raw("ASSRT_TOKEN"),
            assrt_min_interval=_get_float(source, "ASSRT_MIN_INTERVAL", 12.0),
            http_timeout=_get_float(source, "HTTP_TIMEOUT", 20.0),
            http_retries=_get_int(source, "HTTP_RETRIES", 3),
            user_agent=raw("HTTP_USER_AGENT", "anime-release-manager/0.1") or "anime-release-manager/0.1",
            db_path=Path(raw("DB_PATH", str(DEFAULT_DB_PATH)) or str(DEFAULT_DB_PATH)),
            cache_dir=Path(raw("CACHE_DIR", str(DEFAULT_CACHE_DIR)) or str(DEFAULT_CACHE_DIR)),
        )

    @property
    def download_configured(self) -> bool:
        return bool(self.qbit_password)

    def require_download_settings(self) -> None:
        """下载前自检，失败时给出可执行的修复提示。"""
        if not self.qbit_password:
            raise ConfigError(
                "未配置 qBittorrent 密码。请在 .env 中设置 QBIT_URL / QBIT_USERNAME / QBIT_PASSWORD。\n"
                "qBittorrent 侧需要先启用 WebUI：工具 → 选项 → Web UI → 勾选「Web 用户界面」，设置端口与账号密码。"
            )
        if not self.qbit_savepath:
            raise ConfigError("未配置 QBIT_SAVEPATH（下载保存目录）。")
