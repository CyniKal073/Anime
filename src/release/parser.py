"""Release 标题解析。

原则（设计文档 7.1）：能可靠解析就保存，不能确定就留 None。
宁可 ``source=None``，也不要猜成 ``BDRip``。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .models import Release, codec_name, split_langs

_RESOLUTION_RE = re.compile(
    r"(?<![0-9])(2160p|1440p|1080p|720p|480p|4K)(?![0-9])", re.IGNORECASE
)
_DIMENSION_RE = re.compile(r"(?<![0-9])(3840x2160|1920x1080|1280x720)(?![0-9])", re.IGNORECASE)

_SOURCE_PATTERNS = (
    (re.compile(r"\bWEB[\s._-]?DL\b", re.IGNORECASE), "WEB-DL"),
    (re.compile(r"\bWEBRip\b", re.IGNORECASE), "WEBRip"),
    (re.compile(r"\bBDRemux\b|\bBlu-?ray\b|\bBDRip\b|\bBDMV\b|\bBD\b|\bJPBD\b", re.IGNORECASE), "BDRip"),
    (re.compile(r"\bDVDRip\b", re.IGNORECASE), "DVDRip"),
)

_VIDEO_CODEC_PATTERNS = (
    (re.compile(r"\bHEVC\b|\bx265\b|\bH[\s._-]?265\b", re.IGNORECASE), "HEVC"),
    (re.compile(r"\bAV1\b", re.IGNORECASE), "AV1"),
    (re.compile(r"\bH[\s._-]?264\b|\bx264\b|\bAVC\b", re.IGNORECASE), "H.264"),
    (re.compile(r"\bXviD\b", re.IGNORECASE), "XviD"),
    (re.compile(r"\bDivX\b", re.IGNORECASE), "DivX"),
)

_AUDIO_CODEC_PATTERNS = (
    (re.compile(r"\bE-?AC-?3\b|\bDDP\b|\bDD\+", re.IGNORECASE), "E-AC-3"),
    (re.compile(r"\bTrueHD\b", re.IGNORECASE), "TrueHD"),
    (re.compile(r"\bDTS(?:-HD)?\b", re.IGNORECASE), "DTS"),
    (re.compile(r"\bFLAC\b", re.IGNORECASE), "FLAC"),
    (re.compile(r"\bALAC\b", re.IGNORECASE), "ALAC"),
    (re.compile(r"\bOpus\b", re.IGNORECASE), "Opus"),
    (re.compile(r"\bAAC\b", re.IGNORECASE), "AAC"),
    (re.compile(r"\bAC-?3\b", re.IGNORECASE), "AC-3"),
    (re.compile(r"\bMP3\b", re.IGNORECASE), "MP3"),
    (re.compile(r"\bPCM\b", re.IGNORECASE), "PCM"),
)

_EPISODE_PATTERNS = (
    re.compile(r"\bS(?P<season>[0-9]{1,2})E(?P<episode>[0-9]{1,3})\b", re.IGNORECASE),
    re.compile(r"\bE(?:P)?(?P<episode>[0-9]{1,3})\b"),
    re.compile(r"第\s*(?P<episode>[0-9]{1,4})\s*[集话話]"),
    re.compile(r"-\s*(?P<episode>[0-9]{1,3})(?:\s*(?:END|v[0-9]))?(?![0-9])"),
    re.compile(r"\[(?P<episode>[0-9]{1,3})\]"),
)

_SEASON_PATTERNS = (
    re.compile(r"\bS(?P<season>[0-9]{1,2})\b", re.IGNORECASE),
    re.compile(r"\bSeason\s*(?P<season>[0-9]{1,2})\b", re.IGNORECASE),
    re.compile(r"第\s*(?P<season>[0-9一二三四五六七八九十]+)\s*[季部期]"),
)

_BATCH_PATTERNS = (
    re.compile(r"\bBatch\b|\bComplete\b|合集|全集|全[0-9]+\s*[集话話]", re.IGNORECASE),
    re.compile(r"S[0-9]{1,2}\s*-\s*S?[0-9]{1,2}\b"),
    re.compile(r"(?<![0-9])[0-9]{1,3}\s*-\s*[0-9]{1,3}(?![0-9])"),
    re.compile(r"\bS[0-9]{1,2}\s+[0-9]{1,3}\s*-\s*[0-9]{1,3}\b"),
)

_CN_NUMERALS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _cn_to_int(text: str) -> Optional[int]:
    if text.isdigit():
        return int(text)
    if text in _CN_NUMERALS:
        return _CN_NUMERALS[text]
    if len(text) == 2 and text[0] == "十" and text[1] in _CN_NUMERALS:
        return 10 + _CN_NUMERALS[text[1]]
    if len(text) == 2 and text[0] in _CN_NUMERALS and text[1] == "十":
        return _CN_NUMERALS[text[0]] * 10
    return None


def _first_group(title: str) -> Optional[str]:
    for match in re.finditer(r"\[([^\]]{1,40})\]", title):
        token = match.group(1).strip()
        if re.search(r"[0-9]{3,4}p|^\d+$|^(HEVC|AV1|H\.?264|x265|x264)$", token, re.IGNORECASE):
            continue
        return token
    return None


@dataclass
class ParsedTitle:
    group: Optional[str] = None
    season: Optional[int] = None
    episode: Optional[str] = None
    source: Optional[str] = None
    resolution: Optional[str] = None
    video_codec: Optional[str] = None
    audio_codec: Optional[str] = None
    batch: Optional[bool] = None
    upgraded: Optional[bool] = None
    hardsub: Optional[bool] = None
    notes: List[str] = field(default_factory=list)


def parse_title(title: str) -> ParsedTitle:
    parsed = ParsedTitle()
    if not title:
        return parsed

    match = _RESOLUTION_RE.search(title)
    if match:
        value = match.group(1)
        parsed.resolution = "2160p" if value.lower() == "4k" else value.lower()
    else:
        dim = _DIMENSION_RE.search(title)
        if dim:
            parsed.resolution = {"3840x2160": "2160p", "1920x1080": "1080p", "1280x720": "720p"}[dim.group(1)]

    for pattern, value in _SOURCE_PATTERNS:
        if pattern.search(title):
            parsed.source = value
            break

    for pattern, value in _VIDEO_CODEC_PATTERNS:
        if pattern.search(title):
            parsed.video_codec = value
            break

    for pattern, value in _AUDIO_CODEC_PATTERNS:
        if pattern.search(title):
            parsed.audio_codec = value
            break

    episode = season = None
    for pattern in _EPISODE_PATTERNS:
        hit = pattern.search(title)
        if hit:
            episode = hit.groupdict().get("episode")
            season = hit.groupdict().get("season")
            break

    if season is None:
        for pattern in _SEASON_PATTERNS:
            hit = pattern.search(title)
            if hit:
                season = _cn_to_int(hit.group("season"))
                break
    elif not isinstance(season, int):
        # SxxEyy 分支拿到的是字符串（如 "02"），统一成 int
        season = _cn_to_int(str(season))
    if season is not None:
        parsed.season = season
    if episode:
        parsed.episode = episode if len(episode) > 1 else f"0{episode}"

    if any(pattern.search(title) for pattern in _BATCH_PATTERNS):
        parsed.batch = True
    elif parsed.episode:
        parsed.batch = False

    if re.search(r"\bv[0-9]\b|\bREPACK\b|\bPROPER\b", title, re.IGNORECASE):
        parsed.upgraded = True

    if re.search(r"hardsub|内嵌|硬字幕", title, re.IGNORECASE):
        parsed.hardsub = True

    parsed.group = _first_group(title)
    return parsed


def build_release(row: Dict[str, Any]) -> Release:
    """把 nekoBT 的 Torrent 记录转成统一的 Release 模型。"""
    title = str(row.get("title") or "")
    parsed = parse_title(title)

    def _int(value: Any) -> Optional[int]:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    filesize = _int(row.get("filesize"))
    video_codec = parsed.video_codec or codec_name(_int(row.get("video_codec")))

    return Release(
        torrent_id=str(row.get("id") or ""),
        title=title,
        group=parsed.group,
        season=parsed.season,
        episode=parsed.episode,
        source=parsed.source,
        resolution=parsed.resolution,
        video_codec=video_codec,
        audio_codec=parsed.audio_codec,
        sub_lang=split_langs(row.get("sub_lang")),
        fsub_lang=split_langs(row.get("fsub_lang")),
        batch=row.get("batch") if isinstance(row.get("batch"), bool) else parsed.batch,
        upgraded=row.get("upgraded") if isinstance(row.get("upgraded"), bool) else parsed.upgraded,
        filesize=filesize,
        seeders=_int(row.get("seeders")),
        leechers=_int(row.get("leechers")),
        infohash=row.get("infohash"),
        magnet=row.get("magnet"),
        media_id=row.get("media_id"),
        video_type=_int(row.get("video_type")),
        hardsub=row.get("hardsub") if isinstance(row.get("hardsub"), bool) else parsed.hardsub,
        uploaded_at=_int(row.get("uploaded_at")),
        origin=row.get("origin") or "nekobt",
        torrent_url=row.get("torrent_url"),
        raw=row,
    )
