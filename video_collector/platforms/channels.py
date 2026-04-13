"""微信视频号：依赖 yt-dlp 是否支持该 URL；多数情况仅元数据 + 简介。"""

from __future__ import annotations

from ._ytdlp import fetch_via_ytdlp


def fetch(url: str):
    return fetch_via_ytdlp(url, platform="channels")
