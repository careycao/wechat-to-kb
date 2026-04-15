"""
wechat_fetcher.py — 用 Playwright 登录态直接抓微信公众号文章列表。

不依赖 RSSHub，复用 wechat_collector 的 wechat_state.json 登录态。

接口兼容 fetcher.py 的 fetch_source()，对 main.py 完全透明。

原理：
  微信 MP 网页版有一个 getmsg JSON 接口，用于翻页加载公众号历史文章。
  URL: https://mp.weixin.qq.com/mp/profile_ext?action=getmsg&__biz={biz}&f=json&offset={offset}&count=10
  用 Playwright 携带登录态 cookie 调用此接口，返回标题 + 链接 + 发布时间。
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from wechat_collector.wechat_session import (  # noqa: E402
    DEFAULT_MOBILE_USER_AGENT,
    build_browser_launch_kwargs,
    ensure_wechat_state_migrated,
)


# ---------------------------------------------------------------------------
# 内部辅助：API 抓取 + DOM fallback
# ---------------------------------------------------------------------------

async def _fetch_via_api(page, biz, home_url, key, uin, pass_ticket,
                          max_count, cutoff, articles):
    offset = 0
    stop = False
    while not stop and len(articles) < max_count:
        api_url = (
            f"https://mp.weixin.qq.com/mp/profile_ext"
            f"?action=getmsg&__biz={biz}&f=json"
            f"&offset={offset}&count=10&is_ok=1"
            f"&key={key}&uin={uin}&pass_ticket={pass_ticket}"
        )
        try:
            data = await page.evaluate(f"""
                async () => {{
                    const resp = await fetch('{api_url}', {{
                        credentials: 'include',
                        headers: {{
                            'X-Requested-With': 'XMLHttpRequest',
                            'Referer': '{home_url}'
                        }}
                    }});
                    return await resp.json();
                }}
            """)
        except Exception as e:
            logger.warning("getmsg JS fetch 失败: %s", e)
            break

        if not isinstance(data, dict) or data.get("ret") != 0:
            logger.warning("getmsg ret=%s，停止", data.get("ret") if isinstance(data, dict) else "unknown")
            break

        raw = data.get("general_msg_list", "{}")
        try:
            msg_list = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            msg_list = {}

        items = msg_list.get("list", [])
        if not items:
            break

        for item in items:
            comm = item.get("comm_msg_info", {})
            ext  = item.get("app_msg_ext_info", {})
            title       = (ext.get("title") or "").strip()
            content_url = (ext.get("content_url") or "").strip()
            create_ts   = comm.get("datetime", 0)
            excerpt     = (ext.get("digest") or "").strip()
            if not title or not content_url:
                continue
            url = content_url.replace("&amp;", "&")
            if not url.startswith("http"):
                url = "https://mp.weixin.qq.com" + url
            pub_dt = datetime.fromtimestamp(create_ts, tz=timezone.utc)
            if pub_dt < cutoff:
                logger.info("文章超出时间窗口，停止: %s", title[:40])
                stop = True
                break
            articles.append({"title": title, "url": url,
                              "published": pub_dt.isoformat(), "excerpt": excerpt})
            if len(articles) >= max_count:
                stop = True
                break

        if stop:
            break
        next_offset = data.get("next_offset")
        if next_offset is None or next_offset <= offset:
            break
        offset = next_offset
    return articles


async def _fetch_via_dom(page, max_count, cutoff):
    """从页面 DOM 直接抓取已渲染的文章列表（无需 key/uin）。"""
    import asyncio as _asyncio
    await _asyncio.sleep(2)  # 等待列表渲染

    items = await page.evaluate("""
        () => {
            const results = [];
            // 文章列表项选择器（微信MP历史文章页面）
            const selectors = [
                'li.album__item',
                '.weui-media-box',
                'a[href*="mp.weixin.qq.com/s"]',
                '.js_wx_tap_highlight',
            ];
            for (const sel of selectors) {
                const els = document.querySelectorAll(sel);
                if (els.length > 0) {
                    els.forEach(el => {
                        const a = el.tagName === 'A' ? el : el.querySelector('a[href]');
                        if (!a) return;
                        const href = a.href || '';
                        if (!href.includes('mp.weixin.qq.com')) return;
                        const title = (
                            el.querySelector('h4,h3,.album__item-title,.weui-media-box__title')
                            || a
                        ).textContent.trim();
                        if (title && href) results.push({ title, url: href });
                    });
                    if (results.length > 0) break;
                }
            }
            return results;
        }
    """)

    now = datetime.now(timezone.utc)
    articles = []
    for item in items[:max_count]:
        title = (item.get("title") or "").strip()
        url   = (item.get("url") or "").strip()
        if not title or not url:
            continue
        articles.append({
            "title":     title,
            "url":       url,
            "published": now.isoformat(),
            "excerpt":   "",
        })
        logger.info("  DOM抓取: %s", title[:60])

    logger.info("DOM抓取完成，共 %d 篇", len(articles))
    return articles


# ---------------------------------------------------------------------------
# 内部：异步抓取
# ---------------------------------------------------------------------------

async def _fetch_async(
    biz: str,
    max_count: int = 20,
    max_age_hours: int = 24,
) -> list[dict]:
    """
    用 Playwright session 调用微信 MP getmsg API，返回文章列表。
    每个元素：{ title, url, published(ISO8601), excerpt }
    """
    state_path = ensure_wechat_state_migrated()
    if not state_path.exists():
        raise FileNotFoundError(
            f"找不到微信登录态: {state_path}\n"
            "请先运行 wechat_collector 完成微信网页版登录。"
        )

    from playwright.async_api import async_playwright

    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    articles: list[dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            **build_browser_launch_kwargs(headless=True),
        )
        context = await browser.new_context(
            storage_state=str(state_path),
            user_agent=DEFAULT_MOBILE_USER_AGENT,
            viewport={"width": 390, "height": 844},
            is_mobile=True,
        )
        # 隐藏 webdriver 特征
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = await context.new_page()

        # 先访问主页，确保 session 有效 + 拿到必要 cookie 与鉴权参数
        home_url = (
            f"https://mp.weixin.qq.com/mp/profile_ext"
            f"?action=home&__biz={biz}&scene=124#wechat_redirect"
        )
        logger.info("打开公众号主页: %s", home_url)
        await page.goto(home_url, wait_until="networkidle", timeout=30000)

        # 从页面 HTML 提取 key / uin / pass_ticket（getmsg 鉴权必须参数）
        auth_params = await page.evaluate(r"""
            () => {
                const html = document.documentElement.innerHTML;
                const get = (pattern) => {
                    const m = html.match(pattern);
                    return m ? m[1] : '';
                };
                return {
                    key:          get(/['""]key["'"]\s*:\s*["'"]([^"']+)["'"]/),
                    uin:          get(/['""]uin["'"]\s*:\s*["'"]([^"']+)["'"]/),
                    pass_ticket:  get(/pass_ticket\s*=\s*["'"]?([A-Za-z0-9+/=%]+)["'"]?/),
                };
            }
        """)
        key         = auth_params.get("key", "")
        uin         = auth_params.get("uin", "")
        pass_ticket = auth_params.get("pass_ticket", "")
        logger.debug("鉴权参数: key=%s uin=%s pass_ticket=%s",
                     key[:8] + "…" if key else "(空)",
                     uin[:8] + "…" if uin else "(空)",
                     pass_ticket[:8] + "…" if pass_ticket else "(空)")

        # 策略一：若有有效 key，使用 getmsg API（精确带时间戳）
        # 策略二：fallback 到 DOM 抓取页面渲染的文章列表
        if key:
            articles = await _fetch_via_api(
                page, biz, home_url, key, uin, pass_ticket,
                max_count, cutoff, articles
            )
        else:
            logger.warning("未能提取 key，改用 DOM 抓取页面文章列表")
            articles = await _fetch_via_dom(page, max_count, cutoff)

        await context.close()
        await browser.close()

    logger.info("微信抓取完成，共 %d 篇", len(articles))
    return articles


# ---------------------------------------------------------------------------
# 公开接口：同步包装，兼容 fetcher.py 的 fetch_source()
# ---------------------------------------------------------------------------

def fetch_wechat_source(
    source: dict,
    store,
    max_age_hours: int = 24,
    max_per_source: int = 20,
    date_str: str = "",
    **_kwargs,
) -> list[dict]:
    """
    抓取一个微信公众号 source，返回本次新增的文章列表。
    source 格式：
      name: "机器之心"
      type: wechat
      biz: "MzA3NDM4MzUwMQ=="
      tags: [ai]
    """
    biz  = source.get("biz", "").strip()
    name = source.get("name", biz)
    tags = source.get("tags", [])

    if not biz:
        logger.error("source '%s' 缺少 biz 字段", name)
        return []

    logger.info("微信抓取: %s  (biz=%s)", name, biz)

    try:
        raw_articles = asyncio.run(
            _fetch_async(biz=biz, max_count=max_per_source, max_age_hours=max_age_hours)
        )
    except FileNotFoundError as e:
        logger.error("%s", e)
        return []
    except Exception as e:
        logger.error("微信抓取异常 %s: %s", name, e)
        return []

    new_articles: list[dict] = []
    for a in raw_articles:
        if store.exists(a["url"]):
            logger.debug("已存在，跳过: %s", a["url"][:70])
            continue

        article = {
            "url":         a["url"],
            "title":       a["title"],
            "source_name": name,
            "source_url":  f"https://mp.weixin.qq.com/mp/profile_ext?action=home&__biz={biz}",
            "published":   a["published"],
            "content":     a["excerpt"],   # getmsg 只返回摘要，完整正文靠后续抓取
            "tags":        tags,
            "digest_date": date_str,
        }

        article_id = store.insert(article)
        if article_id > 0:
            article["id"] = article_id
            new_articles.append(article)
            logger.info("  + [%d] %s", article_id, a["title"][:60])

    return new_articles
