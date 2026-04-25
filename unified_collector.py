#!/usr/bin/env python3
"""仓库根统一入口：按链接类型自动分流到公众号 / 视频 / 网页采集链路。"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

VIDEO_COLLECTOR_DIR = REPO_ROOT / "video_collector"
if str(VIDEO_COLLECTOR_DIR) not in sys.path:
    sys.path.insert(0, str(VIDEO_COLLECTOR_DIR))

from common.kb_config import ALL_KBS, KB_BY_KEY, warn_if_using_default_config
from common.kb_indexing import rebuild_index
from common.kb_routing import pick_highest_score_route, prompt_user_choice, route, score_kb
from common.kb_storage import KBWriter
from common.path_utils import load_urls_from_file, redact_url_for_log, sanitize_url_input
from common.text_processing import extract_keywords_for_index
from cookies import resolve_cookies_path
from platforms import fetch_video
from video_types import format_plain, html_fragment_for_kb
from web_collector.page_fetcher import PageFetcher
from wechat_collector.article_fetcher import ArticleFetcher, login_wechat_session

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

VIDEO_HOST_HINTS = (
    "bilibili.com",
    "b23.tv",
    "bilivideo.com",
    "channels.weixin.qq.com",
    "douyin.com",
    "iesdouyin.com",
    "ixigua.com",
    "kuaishou.com",
    "chenzhongtech.com",
    "xiaohongshu.com",
    "xhslink.com",
    "youtube.com",
    "youtu.be",
    "tiktok.com",
)


def _normalized_host(url: str) -> str:
    try:
        netloc = urlsplit(url).netloc.lower()
    except ValueError:
        return ""
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


def should_use_video_collector(url: str) -> bool:
    lowered = url.lower().strip()
    if not (lowered.startswith("http://") or lowered.startswith("https://")):
        return False
    host = _normalized_host(lowered)
    return any(host == candidate or host.endswith(f".{candidate}") for candidate in VIDEO_HOST_HINTS)


def is_ambiguous_note_host(url: str) -> bool:
    host = _normalized_host(url)
    return host == "xiaohongshu.com" or host.endswith(".xiaohongshu.com") or host == "xhslink.com"


def video_plain_too_thin(data: dict) -> bool:
    plain_text = (data.get("plain_text") or "").strip()
    body = plain_text.split("---", 1)[-1].strip() if "---" in plain_text else plain_text
    return len(body) < 120


def decide_fetch_mode(url: str) -> str:
    host = _normalized_host(url)
    if host == "mp.weixin.qq.com":
        return "wechat"
    if should_use_video_collector(url):
        return "video"
    return "web"


def _fetch_via_video_collector(url: str) -> dict | None:
    cookiefile = resolve_cookies_path(VIDEO_COLLECTOR_DIR, None)
    record = fetch_video(url, cookiefile=cookiefile)
    if not record:
        return None

    plain_text = format_plain(record)
    return {
        "title": record.title,
        "plain_text": plain_text,
        "html": html_fragment_for_kb(record.title, plain_text, record.canonical_url),
        "url": record.canonical_url,
    }


def _resolve_auto_route(args: argparse.Namespace) -> bool:
    if getattr(args, "interactive", False):
        return False
    if getattr(args, "non_interactive", False):
        return True
    if os.environ.get("KB_NON_INTERACTIVE", "").lower() in ("1", "true", "yes"):
        return True
    return not sys.stdin.isatty()


async def run_urls(
    urls: list[str],
    kb_hint: str | None = None,
    skip_existing: bool = True,
    auto_route: bool = False,
    include_comments: bool = False,
    comments_limit: int = 30,
) -> None:
    writers = {kb.key: KBWriter(kb) for kb in ALL_KBS}
    changed_kbs: set[str] = set()
    wechat_fetcher: ArticleFetcher | None = None
    web_fetcher: PageFetcher | None = None

    try:
        for index, raw_url in enumerate(urls, 1):
            url = sanitize_url_input(raw_url)
            if not url or url.startswith("#"):
                continue

            mode = decide_fetch_mode(url)
            logger.info("[%d/%d] 处理: %s -> %s", index, len(urls), redact_url_for_log(url), mode)

            data = None
            try:
                if mode == "video":
                    data = await asyncio.to_thread(_fetch_via_video_collector, url)
                    if data and is_ambiguous_note_host(url) and video_plain_too_thin(data):
                        logger.info("小红书正文过短，回退网页抓取: %s", redact_url_for_log(url))
                        data = None
                        mode = "web"

                if mode == "wechat":
                    if wechat_fetcher is None:
                        wechat_fetcher = ArticleFetcher()
                        await wechat_fetcher.init()
                    data = await wechat_fetcher.fetch(
                        url,
                        include_comments=include_comments,
                        comments_limit=comments_limit,
                    )
                elif mode == "web" and not data:
                    if web_fetcher is None:
                        web_fetcher = PageFetcher()
                        await web_fetcher.init()
                    data = await web_fetcher.fetch(url)

                if not data:
                    continue

                title = data["title"]
                plain_text = data["plain_text"]

                if kb_hint and kb_hint in writers:
                    kb_key = kb_hint
                    _, category = score_kb(plain_text, title, KB_BY_KEY[kb_key])
                    if not category:
                        category = "未分类"
                else:
                    kb_key, category, scores = route(plain_text, title)
                    if kb_key is None:
                        if auto_route:
                            kb_key, category = pick_highest_score_route(scores)
                            kb = KB_BY_KEY[kb_key]
                            prefixed = kb.raw_to_prefixed.get(category, category)
                            print(
                                f"[unified_collector] 置信度低，已自动选用得分最高：{kb.name} / {prefixed}",
                                file=sys.stderr,
                            )
                        else:
                            kb_key, category = prompt_user_choice(title, scores)

                writer = writers[kb_key]
                kb = KB_BY_KEY[kb_key]
                if skip_existing and writer.already_exists(title):
                    print(f"已存在，跳过：{title[:60]}")
                    continue

                writer.save_stage(data)
                writer.classify_and_move(title, category)
                changed_kbs.add(kb_key)

                prefixed = kb.raw_to_prefixed.get(category, category)
                keywords = extract_keywords_for_index(plain_text)
                save_path = f"~/knowledge_base/{kb.name}/{prefixed}/"
                print("\n已保存完成 ✅")
                print("文章信息：")
                print(f"• 标题： {title[:60]}")
                print(f"• 知识库： {kb.name}")
                print(f"• 分类： {prefixed}")
                print(f"• 核心关键词： {keywords}")
                print(f"已同步更新知识库索引。后续可以在 {save_path} 下找到原文 MD/HTML 版本。\n")
            except Exception:
                logger.exception("处理出错: %s", redact_url_for_log(url))
    finally:
        if wechat_fetcher is not None:
            await wechat_fetcher.close()
        if web_fetcher is not None:
            await web_fetcher.close()

    for kb_key in changed_kbs:
        rebuild_index(KB_BY_KEY[kb_key])


def main() -> None:
    parser = argparse.ArgumentParser(description="wechat-to-kb — 统一链接入库入口")
    parser.add_argument("url", nargs="*", help="内容 URL，可传多个")
    parser.add_argument("-f", "--file", dest="url_file", default="urls.txt")
    parser.add_argument("--no-skip", action="store_true", help="不跳过已存在的文章")
    parser.add_argument(
        "--kb",
        default=None,
        choices=list(KB_BY_KEY.keys()),
        help="强制指定目标知识库",
    )
    parser.add_argument("--reindex", action="store_true", help="仅重建索引，不下载")
    parser.add_argument("--login", action="store_true", help="打开浏览器刷新微信 session（实验性，暂对付费文章无效）")
    parser.add_argument(
        "--comments",
        action="store_true",
        help="仅对公众号文章尝试抓取评论并附加到正文末尾",
    )
    parser.add_argument(
        "--comments-limit",
        type=int,
        default=30,
        help="公众号评论抓取上限（最大 100，默认 30）",
    )
    parser.add_argument("-n", "--non-interactive", action="store_true")
    parser.add_argument("--interactive", action="store_true")
    args = parser.parse_args()

    warn_if_using_default_config()

    if args.login:
        asyncio.run(login_wechat_session())
        return

    if args.reindex:
        targets = [KB_BY_KEY[args.kb]] if args.kb else ALL_KBS
        for kb in targets:
            rebuild_index(kb)
        return

    url_file = Path(args.url_file)
    if not url_file.is_absolute():
        url_file = REPO_ROOT / url_file

    urls = list(args.url)
    if not urls and url_file.exists():
        urls = load_urls_from_file(url_file)

    if not urls:
        print("用法: python unified_collector.py <URL> 或 -f urls.txt")
        sys.exit(1)

    try:
        asyncio.run(
            run_urls(
                urls,
                kb_hint=args.kb,
                skip_existing=not args.no_skip,
                auto_route=_resolve_auto_route(args),
                include_comments=args.comments,
                comments_limit=max(1, min(args.comments_limit, 100)),
            )
        )
    except KeyboardInterrupt:
        logger.info("用户中断")
        sys.exit(130)


if __name__ == "__main__":
    main()
