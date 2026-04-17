"""
article_fetcher.py — 微信公众号文章抓取。

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
from wechat_collector.wechat_comments import (
    fetch_comments_for_page,
    render_comments_html,
    render_comments_text,
)
from wechat_collector.wechat_session import (
    DEFAULT_DESKTOP_USER_AGENT,
    WECHAT_STATE_PATH,
    build_browser_launch_kwargs,
    ensure_wechat_state_migrated,
)

logger = logging.getLogger(__name__)

CONTENT_SELECTORS = ["#js_content", "article", "main", ".content", "body"]


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


class ArticleFetcher:
    """Playwright 浏览器封装，抓取微信公众号文章。"""

    def __init__(self, state_path: Path | None = None):
        self.state_path = state_path or ensure_wechat_state_migrated()
        self.browser = None
        self.context = None
        self._playwright = None

    async def init(self) -> None:
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        self.browser = await self._playwright.chromium.launch(
            **build_browser_launch_kwargs(
                headless=os.environ.get("KB_HEADLESS", "").lower() in ("1", "true", "yes")
            )
        )
        context_kwargs = {
            "user_agent": DEFAULT_DESKTOP_USER_AGENT,
            "viewport": {"width": 1920, "height": 1080},
        }
        if self.state_path.exists():
            context_kwargs["storage_state"] = str(self.state_path)
        self.context = await self.browser.new_context(**context_kwargs)

    async def fetch(
        self,
        url: str,
        include_comments: bool = False,
        comments_limit: int = 100,
    ) -> dict | None:
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
            await page.context.storage_state(path=str(self.state_path))

            plain_text = extract_plain_text(html_content)
            if include_comments:
                try:
                    page_html = await page.content()
                    comments = await fetch_comments_for_page(
                        page,
                        article_url=url,
                        page_html=page_html,
                        limit=comments_limit,
                    )
                except Exception as exc:
                    logger.warning("抓取评论失败，继续保存正文: %s", exc)
                else:
                    if comments:
                        comments_text = render_comments_text(comments)
                        comments_html = render_comments_html(comments)
                        if comments_text:
                            plain_text = f"{plain_text}\n\n---\n\n{comments_text}"
                        if comments_html:
                            html_content = f"{html_content}<hr />{comments_html}"

            return CollectedContent(
                title=title.strip(),
                html=html_content,
                plain_text=plain_text,
                url=url,
            ).to_dict()
        except Exception as exc:
            logger.exception("抓取公众号文章失败 %s: %s", redact_url_for_log(url), exc)
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
