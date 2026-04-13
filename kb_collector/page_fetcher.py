"""
page_fetcher.py — 统一网页抓取模块。

支持：
  - 微信公众号文章（优先选择器 #js_content）
  - 普通网页（fallback: article → main → .content → body）

返回：{ title, html, plain_text, url }
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# 按优先级尝试的内容选择器
CONTENT_SELECTORS = [
    "#js_content",       # 微信公众号
    "article",           # 标准语义标签
    "main",
    ".article-content",
    ".post-content",
    ".content",
    "#content",
    "#article",
    "body",              # 最后兜底
]


def extract_plain_text(html_content: str) -> str:
    """从正文 HTML 提取纯文本。"""
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
    """Playwright 浏览器封装，支持微信与普通网页抓取。"""

    def __init__(self, state_path: Path):
        self.state_path = state_path
        self.browser = None
        self.context = None

    async def init(self):
        from playwright.async_api import async_playwright
        playwright = await async_playwright().start()
        self.browser = await playwright.chromium.launch(
            headless=os.environ.get("KB_HEADLESS", "").lower() in ("1", "true", "yes"),
            args=["--disable-blink-features=AutomationControlled"],
        )
        if self.state_path.exists():
            self.context = await self.browser.new_context(
                storage_state=str(self.state_path)
            )
        else:
            self.context = await self.browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1920, "height": 1080},
            )

    async def fetch(self, url: str) -> dict | None:
        """
        抓取单个页面，返回 { title, html, plain_text, url }。
        优先尝试微信选择器，fallback 到通用选择器。
        """
        page = await self.context.new_page()
        try:
            await page.goto(url, timeout=60000)
            await page.wait_for_load_state("networkidle")

            # 提取标题
            title = await page.title()
            og_title = await page.query_selector("meta[property='og:title']")
            if og_title:
                t = await og_title.get_attribute("content")
                if t:
                    title = t

            # 内容选择器 fallback
            content_el = None
            matched_selector = None
            for selector in CONTENT_SELECTORS:
                try:
                    el = await page.wait_for_selector(selector, timeout=3000)
                    if el:
                        content_el = el
                        matched_selector = selector
                        break
                except Exception:
                    continue

            if not content_el:
                logger.warning("未找到任何内容选择器: %s", url)
                return None

            logger.info("  使用选择器: %s", matched_selector)
            html_content = await content_el.inner_html()

            # 保存浏览器状态（微信登录态）
            await page.context.storage_state(path=str(self.state_path))

            plain_text = extract_plain_text(html_content)
            return {
                "title": title.strip(),
                "html": html_content,
                "plain_text": plain_text,
                "url": url,
            }

        except Exception as e:
            logger.exception("抓取出错 %s: %s", url, e)
            return None
        finally:
            await page.close()

    async def close(self):
        if self.browser:
            await self.browser.close()
            self.browser = None
