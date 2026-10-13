"""领域模型（对应设计文档第 13 节）。

字段原则：不确定就留 None，不要猜。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

#: nekoBT 的 video_codec 是数字枚举（实测）。
VIDEO_CODEC_BY_ID: Dict[int, Optional[str]] = {
    0: None,
    1: "H.264",
    2: "HEVC",
    3: "AV1",
}


def codec_name(codec_id: Optional[int]) -> Optional[str]:
    if codec_id is None:
        return None
    return VIDEO_CODEC_BY_ID.get(int(codec_id))


def human_size(num_bytes: Optional[int]) -> str:
    if not num_bytes:
        return "?"
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TB"


def split_langs(raw: Any) -> List[str]:
    """把 ``"zh-hans,en"`` 这类字段拆成列表。"""
    if not raw:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(item).strip() for item in raw if str(item).strip()]
    return [part.strip() for part in str(raw).split(",") if part.strip()]


@dataclass
class AnimeMetadata:
    nekobt_media_id: str
    anilist_id: Optional[int] = None
    bgm_id: Optional[int] = None

    title_cn: Optional[str] = None
    title_original: Optional[str] = None
    title_romaji: Optional[str] = None
    title_en: Optional[str] = None

    aliases: List[str] = field(default_factory=list)

    year: Optional[int] = None
    type: Optional[str] = None
    episode_count: Optional[int] = None

    @property
    def search_titles(self) -> List[str]:
        """用于兜底关键字检索的标题（只用罗马字/英文，中文对 Torrent 索引无效）。"""
        seen: List[str] = []
        for title in (self.title_romaji, self.title_en, self.title_original):
            if title and title not in seen:
                seen.append(title)
        return seen


@dataclass
class MediaMapping:
    input_title: str
    nekobt_media_id: str
    anilist_id: Optional[int] = None
    bgm_id: Optional[int] = None

    confidence: float = 0.0
    confirmed: bool = False


@dataclass
class Release:
    torrent_id: str
    title: str

    group: Optional[str] = None
    season: Optional[int] = None
    episode: Optional[str] = None

    source: Optional[str] = None
    resolution: Optional[str] = None

    video_codec: Optional[str] = None
    audio_codec: Optional[str] = None

    sub_lang: List[str] = field(default_factory=list)
    fsub_lang: List[str] = field(default_factory=list)

    batch: Optional[bool] = None
    upgraded: Optional[bool] = None

    filesize: Optional[int] = None
    seeders: Optional[int] = None
    leechers: Optional[int] = None

    infohash: Optional[str] = None
    magnet: Optional[str] = None

    media_id: Optional[str] = None
    video_type: Optional[int] = None
    hardsub: Optional[bool] = None
    uploaded_at: Optional[int] = None
    #: 数据来自哪个站（nekobt / mikan）。注意不要和上面的 `source`（BDRip/WEB-DL 这种片源类型）混淆。
    origin: str = "nekobt"
    #: 非 nekoBT 来源直接给的 .torrent 直链
    torrent_url: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    def has_subtitle(self, lang: str) -> bool:
        return lang in self.sub_lang

    @property
    def size_text(self) -> str:
        return human_size(self.filesize)

    def summary(self) -> str:
        bits = [
            self.resolution or "?",
            self.source or "Unknown",
            self.video_codec or "?",
        ]
        if self.sub_lang:
            bits.append("/".join(self.sub_lang))
        return " / ".join(bits)


@dataclass
class DownloadTask:
    release_id: str
    client: str = "qbittorrent"

    torrent_path: Optional[str] = None
    save_path: Optional[str] = None
    torrent_hash: Optional[str] = None

    status: str = "queued"
