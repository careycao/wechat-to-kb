"""小红书视频链接：依赖 yt-dlp  extractor，失败时见日志。"""

from __future__ import annotations

from ._ytdlp import fetch_via_ytdlp


def fetch(url: str):
    return fetch_via_ytdlp(url, platform="xiaohongshu")
