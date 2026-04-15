"""
fetcher.py — RSS 抓取 + 正文提取 + 增量入库。

流程：
  1. 读取 config.yaml 中的 sources
  2. feedparser 解析每个 RSS/Atom URL
  3. 过滤 max_age_hours 之外的文章
  4. store.exists() 跳过已处理的 URL
  5. 抓取正文（普通网页用 requests+BS4，微信链接用 Playwright）
  6. store.insert() 入库
"""

from __future__ import annotations

import logging
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import feedparser
import requests
from bs4 import BeautifulSoup

from store import ArticleStore

logger = logging.getLogger(__name__)

# 微信正文选择器优先级（与 wechat_collector/article_fetcher.py 保持一致）
CONTENT_SELECTORS = [
    "#js_content",
    "article",
    "main",
    ".article-content",
    ".post-content",
    ".content",
    "#content",
    "#article",
    "body",
]

WECHAT_PATTERN = re.compile(r"mp\.weixin\.qq\.com")


# ---------------------------------------------------------------------------
# 正文抓取
# ---------------------------------------------------------------------------

def _extract_text_from_html(html: str) -> str:
    """从 HTML 提取纯文本。"""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()
    return re.sub(r"\n{3,}", "\n\n", soup.get_text(separator="\n")).strip()


def _fetch_via_requests(url: str, timeout: int = 15) -> tuple[str, str]:
    """普通网页：requests + BeautifulSoup，返回 (title, content_text)。"""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or "utf-8"
    soup = BeautifulSoup(resp.text, "lxml")

    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()

    content_html = ""
    for selector in CONTENT_SELECTORS:
        tag = None
        if selector.startswith("#"):
            tag = soup.find(id=selector[1:])
        elif selector.startswith("."):
            tag = soup.find(class_=selector[1:])
        else:
            tag = soup.find(selector)
        if tag:
            content_html = str(tag)
            break

    return title, _extract_text_from_html(content_html or resp.text)


def _fetch_via_playwright(url: str) -> tuple[str, str]:
    """微信文章：复用 wechat_collector 的 ArticleFetcher（同步包装）。"""
    import asyncio
    import sys

    repo_root = Path(__file__).resolve().parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from wechat_collector.article_fetcher import ArticleFetcher  # type: ignore

    async def _run():
        fetcher = ArticleFetcher()
        await fetcher.init()
        data = await fetcher.fetch(url)
        await fetcher.close()
        return data

    data = asyncio.run(_run())
    if not data:
        return "", ""
    return data.get("title", ""), data.get("plain_text", "")


def fetch_article_content(url: str, timeout: int = 15) -> tuple[str, str]:
    """
    统一入口：返回 (title, content_text)。
    微信链接走 Playwright，其余走 requests。
    """
    if WECHAT_PATTERN.search(url):
        try:
            return _fetch_via_playwright(url)
        except Exception as e:
            logger.warning("Playwright 抓取失败 %s: %s，降级到 requests", url[:60], e)

    try:
        return _fetch_via_requests(url, timeout)
    except Exception as e:
        logger.warning("requests 抓取失败 %s: %s", url[:60], e)
        return "", ""


# ---------------------------------------------------------------------------
# RSS 解析工具
# ---------------------------------------------------------------------------

def _parse_published(entry: Any) -> str:
    """从 feedparser entry 提取发布时间，返回 ISO8601 字符串。"""
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        t = getattr(entry, attr, None)
        if t:
            try:
                import calendar
                ts = calendar.timegm(t)
                return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
            except Exception:
                pass
    return datetime.now(timezone.utc).isoformat()


def _is_within_age(published_iso: str, max_age_hours: int) -> bool:
    """判断文章是否在 max_age_hours 时间窗口内。"""
    try:
        pub = datetime.fromisoformat(published_iso)
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=timezone.utc)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        return pub >= cutoff
    except Exception:
        return True  # 解析失败时保守处理，视为有效


def _entry_url(entry: Any) -> str:
    return getattr(entry, "link", "") or ""


def _entry_title(entry: Any) -> str:
    return getattr(entry, "title", "").strip()


def _entry_excerpt(entry: Any) -> str:
    """从 RSS entry 提取摘要文本（不抓全文时的兜底）。"""
    for attr in ("summary", "description", "content"):
        val = getattr(entry, attr, None)
        if isinstance(val, list) and val:
            val = val[0].get("value", "")
        if val:
            return _extract_text_from_html(str(val))[:500]
    return ""


# ---------------------------------------------------------------------------
# 核心：抓取单个 source
# ---------------------------------------------------------------------------

def fetch_source(
    source: dict,
    store: ArticleStore,
    max_age_hours: int = 24,
    max_per_source: int = 20,
    fetch_full_content: bool = True,
    request_timeout: int = 15,
    date_str: str = "",
) -> list[dict]:
    """
    抓取一个 RSS source，返回本次新增的文章列表。
    """
    url = source.get("url", "")
    name = source.get("name", url)
    tags = source.get("tags", [])

    logger.info("抓取: %s  (%s)", name, url)

    try:
        feed = feedparser.parse(url)
    except Exception as e:
        logger.error("feedparser 解析失败 %s: %s", url, e)
        return []

    if feed.bozo and not feed.entries:
        logger.warning("RSS 解析异常 %s: %s", name, feed.bozo_exception)
        return []

    new_articles: list[dict] = []

    for entry in feed.entries[:max_per_source]:
        article_url = _entry_url(entry)
        if not article_url:
            continue

        published = _parse_published(entry)

        if not _is_within_age(published, max_age_hours):
            continue

        if store.exists(article_url):
            logger.debug("已存在，跳过: %s", article_url[:70])
            continue

        title = _entry_title(entry)
        content = ""

        if fetch_full_content:
            fetched_title, content = fetch_article_content(
                article_url, timeout=request_timeout
            )
            # RSS title 通常比网页 title 更干净，优先用 RSS title
            if not title and fetched_title:
                title = fetched_title

        # 正文抓取失败时降级到 RSS 摘要
        if not content:
            content = _entry_excerpt(entry)

        article = {
            "url": article_url,
            "title": title,
            "source_name": name,
            "source_url": url,
            "published": published,
            "content": content,
            "tags": tags,
            "digest_date": date_str,
        }

        article_id = store.insert(article)
        if article_id > 0:
            article["id"] = article_id
            new_articles.append(article)
            logger.info("  + [%d] %s", article_id, title[:60])

    return new_articles


# ---------------------------------------------------------------------------
# 入口：抓取所有 sources（RSS + 微信统一路由）
# ---------------------------------------------------------------------------

def fetch_all(
    sources: list[dict],
    store: ArticleStore,
    max_age_hours: int = 24,
    max_per_source: int = 20,
    fetch_full_content: bool = True,
    request_timeout: int = 15,
    date_str: str = "",
) -> list[dict]:
    """
    抓取所有 sources，返回所有新增文章的列表。
    根据 source.type 路由：
      type: wechat → wechat_fetcher.fetch_wechat_source()
      type: rss（默认）→ fetch_source()（feedparser）
    """
    from wechat_fetcher import fetch_wechat_source

    all_new: list[dict] = []
    for source in sources:
        try:
            source_type = source.get("type", "rss").lower()
            if source_type == "wechat":
                new = fetch_wechat_source(
                    source=source,
                    store=store,
                    max_age_hours=max_age_hours,
                    max_per_source=max_per_source,
                    date_str=date_str,
                )
            else:
                new = fetch_source(
                    source=source,
                    store=store,
                    max_age_hours=max_age_hours,
                    max_per_source=max_per_source,
                    fetch_full_content=fetch_full_content,
                    request_timeout=request_timeout,
                    date_str=date_str,
                )
            all_new.extend(new)
        except Exception as e:
            logger.error("source 处理异常 %s: %s", source.get("name", "?"), e)
    return all_new
