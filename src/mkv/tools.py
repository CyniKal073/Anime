"""调用 MKVToolNix 提取 / 合并内封字幕。

为什么走 MKVToolNix 而不是 ffmpeg：

* 把字幕塞进 MKV 只是**复制轨道**，不重编码，几秒就能完成；
  mkvmerge 是这件事的标准工具，语言/轨道名/默认轨/强制轨这些标记都能直接设。
* 本机装的是 MKVToolNix v96（``F:\\MKVToolNix``），没有 ffmpeg。

只做两件事：读轨道、把字幕轨道搬过去。不碰视频/音频编码，也不删原文件。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

DEFAULT_TOOLS_DIR = r"F:\MKVToolNix"

#: 字幕编码 → 提取时的文件扩展名
_SUBTITLE_EXTENSIONS = {
    "S_TEXT/ASS": ".ass",
    "S_TEXT/SSA": ".ssa",
    "S_TEXT/UTF8": ".srt",
    "S_TEXT/USF": ".usf",
    "S_TEXT/WEBVTT": ".vtt",
    "S_HDMV/PGS": ".sup",
    "S_VOBSUB": ".sub",
    "S_DVBSUB": ".sub",
}

#: mkvmerge 的 ``codec`` 字段是**人名**而不是上面的 ID（实测 SubRip/SRT），
#: 所以两套名字都要认，否则 SRT 会被当成 ASS 提取出来。
_SUBTITLE_NAME_EXTENSIONS = {
    "subrip/srt": ".srt",
    "srt": ".srt",
    "ass": ".ass",
    "ssa": ".ssa",
    "usf": ".usf",
    "webvtt": ".vtt",
    "hdmv pgs": ".sup",
    "vobsub": ".sub",
    "dvb subtitles": ".sub",
}

#: 常见语言标记 → MKV 里使用的 ISO 639-2 代码
LANGUAGE_ALIASES = {
    "zh": "chi",
    "zh-cn": "chi",
    "zh-hans": "chi",
    "zh-tw": "chi",
    "zh-hant": "chi",
    "chs": "chi",
    "cht": "chi",
    "zh_chs": "chi",
    "zh_cht": "chi",
    "sc": "chi",
    "tc": "chi",
    "简中": "chi",
    "繁中": "chi",
    "中文": "chi",
    "en": "eng",
    "ja": "jpn",
}


class MkvToolError(RuntimeError):
    """MKVToolNix 调用失败。"""


def subtitle_extension(codec: str) -> str:
    """按字幕编码给出合适的扩展名（mkvextract 依赖扩展名决定格式）。"""
    raw = (codec or "").strip()
    by_id = _SUBTITLE_EXTENSIONS.get(raw.upper())
    if by_id:
        return by_id
    return _SUBTITLE_NAME_EXTENSIONS.get(raw.lower(), ".ass")


def normalize_language(value: Optional[str]) -> str:
    """把各种写法的语言标记归一成 MKV 用的三字母代码。"""
    if not value:
        return "und"
    text = value.strip().lower()
    return LANGUAGE_ALIASES.get(text, text[:3] if len(text) >= 3 else text)


def find_tools(tools_dir: Optional[str] = None) -> Dict[str, str]:
    """定位 mkvmerge / mkvextract。

    查找顺序：指定目录（配置里的 MKV_TOOLS_DIR）→ PATH。
    找不到就抛错，并把查找过的位置一并说明。
    """
    directories: List[Path] = []
    if tools_dir:
        directories.append(Path(tools_dir))
    directories.append(Path(DEFAULT_TOOLS_DIR))

    found: Dict[str, str] = {}
    for name in ("mkvmerge", "mkvextract"):
        for directory in directories:
            candidate = directory / f"{name}.exe"
            if candidate.is_file():
                found[name] = str(candidate)
                break
        else:
            from_path = shutil.which(name)
            if from_path:
                found[name] = from_path

    missing = [name for name in ("mkvmerge", "mkvextract") if name not in found]
    if missing:
        searched = "、".join(str(d) for d in directories) or "（未指定）"
        raise MkvToolError(
            f"找不到 {', '.join(missing)}。请安装 MKVToolNix，或在 .env 里设置 "
            f"MKV_TOOLS_DIR 指向安装目录。已查找：{searched}"
        )
    return found


class MkvTools:
    def __init__(self, tools_dir: Optional[str] = None, *, timeout: float = 600.0) -> None:
        self.tools_dir = tools_dir
        self.timeout = timeout
        self._paths: Optional[Dict[str, str]] = None

    @property
    def paths(self) -> Dict[str, str]:
        if self._paths is None:
            self._paths = find_tools(self.tools_dir)
        return self._paths

    # ------------------------------------------------------------------ 执行

    def _run(self, args: Sequence[str]) -> Tuple[int, str]:
        try:
            proc = subprocess.run(
                list(args),
                capture_output=True,
                timeout=self.timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise MkvToolError(f"找不到可执行文件：{args[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            raise MkvToolError(f"命令超时（{self.timeout:.0f}s）：{args[0]}") from exc

        stdout = proc.stdout.decode("utf-8", errors="replace")
        stderr = proc.stderr.decode("utf-8", errors="replace")
        # mkvmerge 的惯例：0=成功，1=有警告但成功，2=失败
        if proc.returncode >= 2:
            raise MkvToolError(
                f"{Path(args[0]).name} 执行失败（返回码 {proc.returncode}）：\n"
                f"{(stderr or stdout).strip()[:800]}"
            )
        return proc.returncode, stdout

    def version(self) -> str:
        _code, out = self._run([self.paths["mkvmerge"], "--version"])
        return out.strip().splitlines()[0] if out.strip() else "unknown"

    # ------------------------------------------------------------------ 读轨

    def probe(self, path: str) -> Dict[str, Any]:
        """读取容器信息（``mkvmerge -J`` 的 JSON 输出，已做归一化）。"""
        source = Path(path)
        if not source.is_file():
            raise MkvToolError(f"文件不存在：{path}")

        _code, out = self._run([self.paths["mkvmerge"], "-J", str(source)])
        try:
            raw = json.loads(out)
        except ValueError as exc:
            raise MkvToolError(f"无法解析 mkvmerge 输出：{out[:200]}") from exc

        tracks: List[Dict[str, Any]] = []
        for track in raw.get("tracks") or []:
            props = track.get("properties") or {}
            codec_id = props.get("codec_id") or track.get("codec")
            tracks.append(
                {
                    "id": track.get("id"),
                    "type": track.get("type"),
                    "codec": track.get("codec"),
                    "codec_id": codec_id,
                    "language": props.get("language") or "und",
                    "language_ietf": props.get("language_ietf"),
                    "name": props.get("track_name"),
                    "default": bool(props.get("default_track")),
                    "forced": bool(props.get("forced_track")),
                    "extension": subtitle_extension(codec_id or "")
                    if track.get("type") == "subtitles"
                    else None,
                }
            )

        return {
            "file": str(source),
            "name": source.name,
            "size": source.stat().st_size,
            "container": (raw.get("container") or {}).get("type"),
            "duration_ms": (raw.get("container") or {}).get("properties", {}).get("duration"),
            "tracks": tracks,
            "subtitle_tracks": [t for t in tracks if t["type"] == "subtitles"],
        }

    # ---------------------------------------------------------------- 提取

    def extract(self, path: str, track_ids: Sequence[int], out_dir: str) -> List[str]:
        """把指定字幕轨提取成独立文件，返回文件路径列表。"""
        source = Path(path)
        if not source.is_file():
            raise MkvToolError(f"文件不存在：{path}")

        info = self.probe(str(source))
        by_id = {track["id"]: track for track in info["tracks"]}
        target_dir = Path(out_dir)
        target_dir.mkdir(parents=True, exist_ok=True)

        args = [self.paths["mkvextract"], "tracks", str(source)]
        planned: List[str] = []
        for track_id in track_ids:
            track = by_id.get(track_id)
            if track is None:
                raise MkvToolError(f"{source.name} 里没有轨道 {track_id}")
            if track["type"] != "subtitles":
                raise MkvToolError(f"轨道 {track_id} 不是字幕轨（{track['type']}）")
            suffix = track["extension"] or ".ass"
            out_path = target_dir / f"{source.stem}.track{track_id}{suffix}"
            args.append(f"{track_id}:{out_path}")
            planned.append(str(out_path))

        self._run(args)
        return planned

    # ---------------------------------------------------------------- 合并

    def mux_subtitles(
        self,
        target: str,
        subtitle_files: Sequence[str],
        output: str,
        *,
        language: str = "chi",
        track_name: Optional[str] = None,
        make_default: bool = True,
        extra_args: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        """把字幕文件合并进目标视频，视频/音频流原样复制。

        ``--language`` / ``--track-name`` / ``--default-track`` 都写在对应输入文件之前，
        作用范围是紧随其后的那个文件的轨道 0。
        """
        target_path = Path(target)
        if not target_path.is_file():
            raise MkvToolError(f"目标视频不存在：{target}")
        for item in subtitle_files:
            if not Path(item).is_file():
                raise MkvToolError(f"字幕文件不存在：{item}")

        output_path = Path(output)
        if output_path.resolve() == target_path.resolve():
            raise MkvToolError("输出文件不能覆盖目标视频，请换一个文件名")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        lang = normalize_language(language)
        args = [self.paths["mkvmerge"], "-o", str(output_path), str(target_path)]
        for item in subtitle_files:
            args += ["--language", f"0:{lang}"]
            if track_name:
                args += ["--track-name", f"0:{track_name}"]
            args += ["--default-track", f"0:{'yes' if make_default else 'no'}"]
            args.append(str(item))
        if extra_args:
            args += list(extra_args)

        code, out = self._run(args)
        return {
            "output": str(output_path),
            "size": output_path.stat().st_size if output_path.exists() else None,
            "warnings": bool(code == 1),
            "message": out.strip().splitlines()[-1] if out.strip() else "",
        }
