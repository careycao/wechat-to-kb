#!/usr/bin/env python3
"""
xhs_fetcher.py — 小红书收藏帖子抓取模块。

流程：
  1. Playwright 打开小红书网页版
  2. 首次运行：有头浏览器扫码登录，保存 xhs_state.json
  3. 从主页 URL 提取 user_id
  4. 调用收藏 API（cursor 翻页），获取所有收藏帖子
  5. 返回标准化帖子列表，供 kb_router 路由入库

用法：
  python xhs_fetcher.py --login          # 首次登录
  python xhs_fetcher.py                  # 抓取收藏
  python xhs_fetcher.py --limit 50       # 限制抓取数量
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

SCRIPT_DIR    = Path(__file__).resolve().parent
STATE_PATH    = SCRIPT_DIR / "xhs_state.json"
USER_ID_CACHE = SCRIPT_DIR / "xhs_user_id.txt"

XHS_HOME = "https://www.xiaohongshu.com"
XHS_COLLECT_API = (
    "https://edith.xiaohongshu.com/api/sns/web/v2/note/collect/page"
)

# ---------------------------------------------------------------------------
# 登录
# ---------------------------------------------------------------------------

async def _do_login() -> None:
    """打开有头浏览器，等用户扫码登录后保存 session。"""
    from playwright.async_api import async_playwright

    print("正在打开浏览器，请扫码登录小红书...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        page = await context.new_page()
        await page.goto(XHS_HOME, wait_until="networkidle", timeout=30000)

        print("请在浏览器中完成登录，登录后按 Enter 继续...")
        input()

        # 保存登录态
        await context.storage_state(path=str(STATE_PATH))
        print(f"登录态已保存: {STATE_PATH}")
        await browser.close()


def login() -> None:
    asyncio.run(_do_login())


# ---------------------------------------------------------------------------
# 获取 user_id
# ---------------------------------------------------------------------------

async def _get_user_id(page) -> str:
    """获取当前登录用户的 user_id，优先从缓存读，否则调用 /me 接口。"""
    # 先尝试从缓存读
    if USER_ID_CACHE.exists():
        uid = USER_ID_CACHE.read_text().strip()
        if uid:
            logger.debug("从缓存读取 user_id: %s", uid)
            return uid

    # 调用 /me 接口
    logger.info("正在获取 user_id...")
    try:
        result = await page.evaluate("""
            async () => {
                try {
                    const resp = await fetch(
                        'https://www.xiaohongshu.com/api/sns/web/v2/user/me',
                        { credentials: 'include' }
                    );
                    const text = await resp.text();
                    try {
                        return { ok: true, data: JSON.parse(text) };
                    } catch(e) {
                        return { ok: false, text: text.slice(0, 200) };
                    }
                } catch(e) {
                    return { ok: false, text: String(e) };
                }
            }
        """)
        if result.get("ok"):
            data = result["data"]
            uid = (
                data.get("data", {}).get("userId")
                or data.get("data", {}).get("user_id")
                or data.get("data", {}).get("id")
                or ""
            )
            if uid:
                USER_ID_CACHE.write_text(uid)
                logger.info("user_id: %s", uid)
                return uid
            logger.debug("/me 接口返回: %s", json.dumps(data)[:200])
        else:
            logger.debug("/me 接口非 JSON 响应: %s", result.get("text", ""))
    except Exception as e:
        logger.debug("/me 接口异常: %s", e)

    # fallback：从页面 JS state 提取
    uid = await page.evaluate("""
        () => {
            try {
                const s = window.__INITIAL_STATE__;
                if (s) {
                    const u = s.user?.userInfo || s.userInfo || {};
                    return u.userId || u.user_id || u.id || '';
                }
            } catch(e) {}
            return '';
        }
    """)
    if uid:
        USER_ID_CACHE.write_text(uid)
        logger.info("user_id (from page state): %s", uid)
        return uid

    # fallback：从页面个人主页链接提取
    uid = await page.evaluate("""
        () => {
            const links = [...document.querySelectorAll('a[href*="/user/profile/"]')];
            for (const a of links) {
                const m = a.href.match(/\\/user\\/profile\\/([a-f0-9]{24})/);
                if (m) return m[1];
            }
            return '';
        }
    """)
    if uid:
        USER_ID_CACHE.write_text(uid)
        logger.info("user_id (from profile link): %s", uid)
        return uid

    raise RuntimeError(
        "无法获取 user_id。\n"
        "请尝试：python xhs_fetcher.py --user-id <你的小红书用户ID>\n"
        "user_id 获取方式：登录小红书网页版，进入个人主页，URL 中的 24 位字符串即为 user_id"
    )


# ---------------------------------------------------------------------------
# 笔记类型
# ---------------------------------------------------------------------------

def _is_video_note(note: dict) -> bool:
    """收藏 API 返回的 note 是否视频笔记（用于走 video_collector 增强正文）。"""
    t = note.get("type")
    if isinstance(t, str) and t.strip().lower() == "video":
        return True
    for key in ("note_card", "noteCard"):
        nc = note.get(key)
        if isinstance(nc, dict):
            t2 = nc.get("type")
            if isinstance(t2, str) and t2.strip().lower() == "video":
                return True
    if note.get("video") or note.get("video_info") or note.get("videoInfo"):
        return True
    return False


# ---------------------------------------------------------------------------
# 抓取收藏列表
# ---------------------------------------------------------------------------

async def _fetch_collections_async(
    limit: int = 200,
    since_url: str | None = None,
) -> list[dict]:
    """
    调用小红书收藏 API，返回标准化帖子列表。
    每个元素：{ title, url, content, tags, source_name }
    """
    if not STATE_PATH.exists():
        raise FileNotFoundError(
            f"未找到登录态: {STATE_PATH}\n请先运行: python xhs_fetcher.py --login"
        )

    from playwright.async_api import async_playwright

    posts: list[dict] = []
    seen_urls: set[str] = set()
    if since_url:
        seen_urls.add(since_url)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            storage_state=str(STATE_PATH),
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = await context.new_page()

        # 先访问主页激活 session
        logger.info("初始化 session...")
        await page.goto(XHS_HOME, wait_until="networkidle", timeout=30000)

        # 检查是否登录
        title = await page.title()
        if "登录" in title or "注册" in title:
            raise RuntimeError("session 已失效，请重新运行 --login 扫码登录")

        user_id = await _get_user_id(page)
        logger.info("开始抓取收藏，user_id=%s", user_id)

        # 通过网络拦截捕获 XHS 自身发出的收藏 API 响应（含完整鉴权）
        logger.info("正在打开收藏页面...")
        intercepted_pages: list[dict] = []

        async def _on_response(resp):
            if "collect/page" in resp.url:
                try:
                    body = await resp.text()
                    data = json.loads(body)
                    intercepted_pages.append(data)
                    logger.debug("拦截到收藏 API 响应: %s", resp.url[:80])
                except Exception as e:
                    logger.debug("拦截响应解析失败: %s", e)

        page.on("response", _on_response)

        # 通过自然点击导航到个人主页（避免直接 goto 触发安全验证）
        me_link = await page.query_selector(f'a[href*="/user/profile/{user_id}"]')
        if me_link:
            async with page.expect_navigation(wait_until="networkidle", timeout=15000):
                await me_link.click()
            logger.info("已导航到个人主页")
        else:
            # fallback：直接访问
            await page.goto(f"{XHS_HOME}/user/profile/{user_id}", wait_until="networkidle", timeout=30000)

        await asyncio.sleep(1)

        # 点击"收藏"标签，触发第一次 API 调用
        tab_els = await page.query_selector_all(".reds-tab-item")
        clicked = False
        for tab in tab_els:
            text = await tab.text_content()
            if "收藏" in (text or ""):
                await tab.click()
                clicked = True
                logger.info("已点击收藏标签")
                await asyncio.sleep(2)
                break
        if not clicked:
            logger.warning("未找到收藏标签，可能收藏列表无法加载")

        # 通过滚动触发翻页，直到获取足够数量或无更多内容
        prev_intercepted = 0
        no_new_rounds = 0

        while len(posts) < limit:
            # 处理所有新的拦截数据
            for data in intercepted_pages[prev_intercepted:]:
                if not data.get("success"):
                    logger.warning("API success=false: %s", json.dumps(data)[:200])
                    continue

                notes = data.get("data", {}).get("notes", [])
                for note in notes:
                    note_id = note.get("note_id") or note.get("id") or note.get("noteId") or ""
                    note_title = (note.get("title") or note.get("display_title") or "").strip()
                    desc       = (note.get("desc") or "").strip()
                    tags       = [t.get("name", "") for t in note.get("tag_list", []) if t.get("name")]

                    if not note_id:
                        continue

                    url = f"https://www.xiaohongshu.com/explore/{note_id}"
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)

                    is_video = _is_video_note(note)
                    posts.append({
                        "title":       note_title or desc[:30] or note_id,
                        "url":         url,
                        "content":     f"{note_title}\n{desc}",
                        "tags":        tags,
                        "source_name": "小红书收藏",
                        "is_video":    is_video,
                    })
                    logger.info("  + [%d] %s", len(posts), (note_title or desc)[:50])

                    if len(posts) >= limit:
                        break

                # 检查是否有更多
                has_more = data.get("data", {}).get("has_more", False)
                if not has_more:
                    logger.info("已到最后一页")
                    prev_intercepted = len(intercepted_pages)
                    break

            new_count = len(intercepted_pages) - prev_intercepted
            prev_intercepted = len(intercepted_pages)

            if len(posts) >= limit:
                break

            # 若没有新数据，尝试滚动到底部触发加载更多
            if new_count == 0:
                no_new_rounds += 1
                if no_new_rounds >= 3:
                    logger.info("连续 3 次滚动无新数据，结束抓取")
                    break
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(2)
            else:
                no_new_rounds = 0
                # 继续滚动加载更多
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(2)

        await context.close()
        await browser.close()

    logger.info("共抓取收藏帖子: %d 篇", len(posts))
    return posts


def fetch_collections(limit: int = 200, user_id: str = "") -> list[dict]:
    if user_id:
        USER_ID_CACHE.write_text(user_id)
    return asyncio.run(_fetch_collections_async(limit=limit))


# ---------------------------------------------------------------------------
# 入口（独立调试用）
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    parser = argparse.ArgumentParser(description="小红书收藏抓取")
    parser.add_argument("--login",  action="store_true", help="重新登录")
    parser.add_argument("--limit",  type=int, default=200, help="最多抓取数量")
    args = parser.parse_args()

    if args.login:
        login()
        sys.exit(0)

    posts = fetch_collections(limit=args.limit)
    print(f"\n共 {len(posts)} 篇收藏：")
    for p in posts[:5]:
        print(f"  [{p['title'][:30]}] {p['url']}")
    if len(posts) > 5:
        print(f"  ... 共 {len(posts)} 篇")
