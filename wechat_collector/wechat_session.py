"""WeChat-specific browser/session helpers."""

from __future__ import annotations

import os
from pathlib import Path

WECHAT_STATE_PATH = Path(__file__).resolve().parent / "wechat_state.json"
LEGACY_WECHAT_STATE_PATH = Path(__file__).resolve().parent.parent / "kb_collector" / "wechat_state.json"

DEFAULT_DESKTOP_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

DEFAULT_MOBILE_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Mobile/15E148 MicroMessenger/8.0.47(0x18002f2d) NetType/WIFI Language/zh_CN"
)


def ensure_wechat_state_migrated() -> Path:
    if WECHAT_STATE_PATH.exists():
        return WECHAT_STATE_PATH
    if LEGACY_WECHAT_STATE_PATH.exists():
        WECHAT_STATE_PATH.write_text(
            LEGACY_WECHAT_STATE_PATH.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    return WECHAT_STATE_PATH


def resolve_browser_executable() -> str | None:
    explicit = os.environ.get("KB_BROWSER_EXECUTABLE", "").strip()
    if explicit:
        return explicit

    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    return None


def build_browser_launch_kwargs(headless: bool) -> dict:
    kwargs = {
        "headless": headless,
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    executable_path = resolve_browser_executable()
    if executable_path:
        kwargs["executable_path"] = executable_path
    return kwargs
