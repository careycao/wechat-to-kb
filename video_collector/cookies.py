"""解析 Cookie 文件路径（CLI / 环境变量 / 默认 cookies.txt）。"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

ENV_COOKIES = "VIDEO_COLLECTOR_COOKIES"


def resolve_cookies_path(script_dir: Path, cli_cookies: str | None) -> str | None:
    """
    优先级：--cookies > 环境变量 VIDEO_COLLECTOR_COOKIES > script_dir/cookies.txt
    仅当路径存在且为文件时返回绝对路径字符串。
    """
    if cli_cookies:
        p = Path(cli_cookies).expanduser()
        if not p.is_absolute():
            p = script_dir / p
        if p.is_file():
            return str(p.resolve())
        logger.warning("Cookie 文件不存在，将不使用 Cookie: %s", p)
        return None

    env = os.environ.get(ENV_COOKIES, "").strip()
    if env:
        p = Path(env).expanduser()
        if not p.is_absolute():
            p = script_dir / p
        if p.is_file():
            return str(p.resolve())
        logger.warning("环境变量 %s 指向的文件不存在，将不使用 Cookie: %s", ENV_COOKIES, p)

    default = script_dir / "cookies.txt"
    if default.is_file():
        return str(default.resolve())

    return None
