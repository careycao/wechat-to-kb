"""分平台入口：统一通过 yt-dlp 拉取（支持 B 站、小红书、视频号及 yt-dlp 支持的其它站点）。"""

from __future__ import annotations

from ._ytdlp import fetch_via_ytdlp


def detect_platform(url: str) -> str:
    u = url.lower().strip()
    if "bilibili.com" in u or "b23.tv" in u or "bilivideo.com" in u:
        return "bilibili"
    if "xiaohongshu.com" in u or "xhslink.com" in u:
        return "xiaohongshu"
    if "channels.weixin.qq.com" in u:
        return "channels"
    return "unknown"


def fetch_video(url: str, cookiefile: str | None = None):
    """
    根据 URL 解析平台标签并拉取 VideoRecord。
    unknown 时仍尝试 yt-dlp 通用提取（如 YouTube）。

    cookiefile: Netscape 格式 Cookie 文件路径，传给 yt-dlp（可选）。
    """
    plat = detect_platform(url)
    label = plat if plat != "unknown" else "generic"
    return fetch_via_ytdlp(url, platform=label, cookiefile=cookiefile)
