"""哔哩哔哩：元数据 + 字幕 / 简介，底层见 _ytdlp。"""

from __future__ import annotations

from ._ytdlp import fetch_via_ytdlp


def fetch(url: str):
    return fetch_via_ytdlp(url, platform="bilibili")
