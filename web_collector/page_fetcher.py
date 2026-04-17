"""
page_fetcher.py — 普通网页抓取。

返回：{ title, html, plain_text, url }
"""

from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common.content_models import CollectedContent
from common.path_utils import redact_url_for_log

logger = logging.getLogger(__name__)

CONTENT_SELECTORS = [
    "article",
    "main",
    ".article-content",
    ".post-content",
    ".content",
    "#content",
    "#article",
    "body",
]


def _resolve_browser_executable() -> str | None:
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


def extract_plain_text(html_content: str) -> str:
    text = re.sub(r"<script[^>]*>[\s\S]*?</script>", "", html_content, flags=re.IGNORECASE)
    text = re.sub(r"<style[^>]*>[\s\S]*?</style>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"</(?:p|div|br|h[1-6]|li|tr)>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&quot;", '"')
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
    )
    return re.sub(r"\n{3,}", "\n\n", text).strip()


class PageFetcher:
    """Playwright 浏览器封装，抓取普通网页。"""

    def __init__(self):
        self.browser = None
        self.context = None
        self._playwright = None

    async def init(self) -> None:
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        launch_kwargs = {
            "headless": os.environ.get("KB_HEADLESS", "").lower() in ("1", "true", "yes"),
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        executable_path = _resolve_browser_executable()
        if executable_path:
            launch_kwargs["executable_path"] = executable_path

        self.browser = await self._playwright.chromium.launch(**launch_kwargs)
        self.context = await self.browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
        )

    async def fetch(self, url: str) -> dict | None:
        page = await self.context.new_page()
        try:
            await page.goto(url, timeout=60000)
            await page.wait_for_load_state("networkidle")

            title = await page.title()
            og_title = await page.query_selector("meta[property='og:title']")
            if og_title:
                meta_title = await og_title.get_attribute("content")
                if meta_title:
                    title = meta_title

            content_el = None
            for selector in CONTENT_SELECTORS:
                try:
                    element = await page.wait_for_selector(selector, timeout=3000)
                    if element:
                        content_el = element
                        break
                except Exception:
                    continue

            if not content_el:
                logger.warning("未找到正文节点: %s", redact_url_for_log(url))
                return None

            html_content = await content_el.inner_html()
            plain_text = extract_plain_text(html_content)
            return CollectedContent(
                title=title.strip(),
                html=html_content,
                plain_text=plain_text,
                url=url,
            ).to_dict()
        except Exception as exc:
            logger.exception("抓取网页失败 %s: %s", redact_url_for_log(url), exc)
            return None
        finally:
            await page.close()

    async def close(self) -> None:
        if self.context:
            await self.context.close()
            self.context = None
        if self.browser:
            await self.browser.close()
            self.browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
