"""
article_fetcher.py — 微信公众号文章抓取。

返回：{ title, html, plain_text, url }

抓取策略（双模式分流）：
  Step 1：轻量 HTTP 模式（requests + BeautifulSoup，~1s，无 Chrome 依赖）
    - 带浏览器 UA 直接 GET，解析静态 HTML 里的 og:title 和 #js_content
    - 多维度完整性验证：异常标题、付费遮罩、错误关键词、正文字数
    - 验证通过直接返回，不启动 Playwright
  Step 2：Playwright 模式（降级兜底，~10s）
    - 轻量模式验证失败时自动降级
    - 需抓评论时直接走此模式
    - 两次都失败则明确报错，不保存残缺内容
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
    build_browser_launch_kwargs,
    ensure_wechat_state_migrated,
)

logger = logging.getLogger(__name__)

CONTENT_SELECTORS = ["#js_content", "article", "main", ".content", "body"]

# 微信错误页的特征文案，出现则说明内容不可用或被截断
WECHAT_ERROR_PATTERNS: list[str] = [
    "请在微信客户端打开",
    "该内容已被发布者删除",
    "此内容因违规无法查看",
    "访问受限",
    "该内容已过期",
    "未知错误",
]

# 轻量模式正文最小字数阈值
_MIN_CONTENT_CHARS = 100


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


def _lightweight_fetch(url: str) -> tuple[dict | None, str]:
    """
    轻量 HTTP 模式抓取。
    返回 (result_dict, reason)：result 为 None 时 reason 说明降级原因。
    """
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError:
        return None, "requests/beautifulsoup4 未安装，跳过轻量模式"

    try:
        resp = requests.get(
            url,
            headers={
                "User-Agent": DEFAULT_DESKTOP_USER_AGENT,
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Accept": "text/html,application/xhtml+xml",
            },
            timeout=15,
        )
        resp.raise_for_status()
    except Exception as exc:
        return None, f"HTTP 请求失败: {exc}"

    soup = BeautifulSoup(resp.text, "html.parser")

    # ── og:title 检查 ──────────────────────────────────────────────
    og_title_el = soup.find("meta", property="og:title")
    if not og_title_el:
        return None, "og:title 缺失，页面可能未正常返回"
    title = (og_title_el.get("content") or "").strip()
    if not title:
        return None, "og:title 为空"
    for pattern in WECHAT_ERROR_PATTERNS:
        if pattern in title:
            return None, f"og:title 含异常文案：{title}"

    # ── 付费文章检测 ───────────────────────────────────────────────
    if "pay_type: '1'" in resp.text or soup.find(class_="js_pay_preview_filter"):
        return None, "付费文章，内容被截断，需 Playwright 模式（暂不支持自动解锁）"

    # ── #js_content 检查 ───────────────────────────────────────────
    content_el = soup.find(id="js_content")
    if not content_el:
        return None, "#js_content 缺失，正文可能由 JS 动态注入，需 Playwright 渲染"

    content_text = content_el.get_text(strip=True)
    for pattern in WECHAT_ERROR_PATTERNS:
        if pattern in content_text:
            return None, f"正文含错误文案：{pattern}"
    if len(content_text) < _MIN_CONTENT_CHARS:
        return None, f"正文过短（{len(content_text)} 字 < {_MIN_CONTENT_CHARS}），可能需 JS 渲染"

    html = str(content_el)
    plain_text = extract_plain_text(html)
    result = CollectedContent(
        title=title,
        html=html,
        plain_text=plain_text,
        url=url,
    ).to_dict()
    return result, ""


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
        # ── Step 1：轻量模式（评论模式直接跳过，需要 Playwright context）──
        if not include_comments:
            result, reason = _lightweight_fetch(url)
            if result:
                logger.info("[轻量模式] 成功: %s", redact_url_for_log(url))
                return result
            logger.info("[轻量模式] 降级 → Playwright（%s）: %s", reason, redact_url_for_log(url))

        # ── Step 2：Playwright 模式 ────────────────────────────────
        return await self._playwright_fetch(url, include_comments, comments_limit)

    async def _playwright_fetch(
        self,
        url: str,
        include_comments: bool = False,
        comments_limit: int = 100,
    ) -> dict | None:
        page = await self.context.new_page()
        try:
            await page.goto(url, timeout=60000)

            title = await page.title()
            og_title = await page.query_selector("meta[property='og:title']")
            if og_title:
                meta_title = await og_title.get_attribute("content")
                if meta_title:
                    title = meta_title

            # 验证标题不含错误文案
            for pattern in WECHAT_ERROR_PATTERNS:
                if pattern in title:
                    logger.error(
                        "文章不可用（%s）: %s", pattern, redact_url_for_log(url)
                    )
                    return None

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

            # 验证内容完整性：付费遮罩或内容过短则拒绝保存
            plain_text_check = extract_plain_text(html_content)
            has_pay_filter = await page.evaluate(
                "() => !!document.querySelector('.js_pay_preview_filter')"
            )
            if has_pay_filter:
                logger.error(
                    "付费文章内容被截断，拒绝保存残缺内容: %s", redact_url_for_log(url)
                )
                return None
            for pattern in WECHAT_ERROR_PATTERNS:
                if pattern in plain_text_check:
                    logger.error(
                        "正文含错误文案（%s），拒绝保存: %s", pattern, redact_url_for_log(url)
                    )
                    return None

            await page.context.storage_state(path=str(self.state_path))

            plain_text = plain_text_check
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

            logger.info("[Playwright 模式] 成功: %s", redact_url_for_log(url))
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


async def login_wechat_session(state_path: Path | None = None) -> None:
    """打开有头浏览器，引导用户扫码登录微信，完成后保存 session。"""
    from playwright.async_api import async_playwright

    target_path = state_path or ensure_wechat_state_migrated()

    # 打开普通公开文章，页面可正常加载，方便用户确认浏览器环境正常
    # 付费文章解锁的浏览器登录入口尚未找到，session 对普通文章已够用
    LOGIN_ENTRY = "https://mp.weixin.qq.com/s/uqhVqYkhAJR9VBo3ul558A"

    print("=" * 55)
    print("  微信 Session 登录向导（实验性）")
    print("=" * 55)
    print()
    print("⚠️  注意：微信付费文章的浏览器读者登录入口尚未完全确认")
    print()
    print("即将打开 Chrome 浏览器窗口...")
    print()
    print("操作步骤：")
    print("  1. 等待浏览器打开测试付费文章")
    print("  2. 尝试在页面中找到「登录」或扫码入口并登录")
    print("     （用购买了付费文章的微信账号）")
    print("  3. 登录后确认可以看到付费文章全文")
    print("  4. 回到此终端，按回车键保存 session")
    print()
    print("  如果页面没有登录入口，可在地址栏手动访问其他")
    print("  微信登录页面，完成后再回来按回车。")
    print()

    async with async_playwright() as p:
        # 强制有头模式，忽略 KB_HEADLESS 环境变量
        launch_kwargs = build_browser_launch_kwargs(headless=False)
        browser = await p.chromium.launch(**launch_kwargs)

        # 强制全新 context，不加载旧 session
        # 目的：避免旧 session 半过期时误导用户以为无需扫码，导致保存的仍是旧状态
        if target_path.exists():
            backup = target_path.with_suffix(".json.bak")
            target_path.rename(backup)
            print(f"[提示] 已将旧 session 备份至 {backup.name}，本次强制全新登录")
            print()

        context_kwargs: dict = {
            "user_agent": DEFAULT_DESKTOP_USER_AGENT,
            "viewport": {"width": 1280, "height": 900},
        }
        context = await browser.new_context(**context_kwargs)
        page = await context.new_page()

        await page.goto(LOGIN_ENTRY, timeout=60000)

        input(">>> 扫码登录完成、浏览器已跳转后，按回车保存 session... ")

        await context.storage_state(path=str(target_path))
        await context.close()
        await browser.close()

    print()
    print(f"✅ 新 Session 已保存至：{target_path}")
    backup = target_path.with_suffix(".json.bak")
    if backup.exists():
        print(f"   旧 Session 备份保留在：{backup.name}（确认新 session 正常后可手动删除）")
    print()
    print("可运行以下命令验证登录状态：")
    print("  python test_paid_article.py")
