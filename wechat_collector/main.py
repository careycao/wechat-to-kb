#!/usr/bin/env python3
"""微信公众号文章入库主流程。"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common.kb_config import ALL_KBS, KB_BY_KEY, warn_if_using_default_config
from common.kb_indexing import rebuild_index
from common.kb_routing import pick_highest_score_route, prompt_user_choice, route, score_kb
from common.kb_storage import KBWriter
from common.path_utils import load_urls_from_file, redact_url_for_log, sanitize_url_input
from common.text_processing import extract_keywords_for_index
from wechat_collector.article_fetcher import ArticleFetcher

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def _is_wechat_article(url: str) -> bool:
    return "mp.weixin.qq.com/" in url


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

    fetcher = ArticleFetcher()
    await fetcher.init()
    try:
        for index, raw_url in enumerate(urls, 1):
            url = sanitize_url_input(raw_url)
            if not url or url.startswith("#"):
                continue
            if not _is_wechat_article(url):
                logger.warning("跳过非公众号文章链接: %s", redact_url_for_log(url))
                continue

            logger.info("[%d/%d] 处理: %s", index, len(urls), redact_url_for_log(url))
            try:
                data = await fetcher.fetch(
                    url,
                    include_comments=include_comments,
                    comments_limit=comments_limit,
                )
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
                                f"[wechat_collector] 置信度低，已自动选用得分最高：{kb.name} / {prefixed}",
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
        await fetcher.close()

    for kb_key in changed_kbs:
        rebuild_index(KB_BY_KEY[kb_key])


def _resolve_auto_route(args: argparse.Namespace) -> bool:
    if getattr(args, "interactive", False):
        return False
    if getattr(args, "non_interactive", False):
        return True
    if os.environ.get("KB_NON_INTERACTIVE", "").lower() in ("1", "true", "yes"):
        return True
    return not sys.stdin.isatty()


def main() -> None:
    parser = argparse.ArgumentParser(description="wechat_collector — 公众号文章入库工具")
    parser.add_argument("url", nargs="*", help="公众号文章 URL，可传多个")
    parser.add_argument("-f", "--file", dest="url_file", default="urls.txt")
    parser.add_argument("--no-skip", action="store_true", help="不跳过已存在的文章")
    parser.add_argument(
        "--kb",
        default=None,
        choices=list(KB_BY_KEY.keys()),
        help="强制指定目标知识库",
    )
    parser.add_argument("--reindex", action="store_true", help="仅重建索引，不下载")
    parser.add_argument(
        "--comments",
        action="store_true",
        help="尝试抓取公众号文章评论并附加到正文末尾（暂保留为 PoC）",
    )
    parser.add_argument(
        "--comments-limit",
        type=int,
        default=30,
        help="评论抓取上限（最大 100，默认 30）",
    )
    parser.add_argument("-n", "--non-interactive", action="store_true")
    parser.add_argument("--interactive", action="store_true")
    args = parser.parse_args()
    warn_if_using_default_config()

    if args.reindex:
        targets = [KB_BY_KEY[args.kb]] if args.kb else ALL_KBS
        for kb in targets:
            rebuild_index(kb)
        return

    url_file = Path(args.url_file)
    if not url_file.is_absolute():
        url_file = Path(__file__).resolve().parent / url_file

    urls = list(args.url)
    if not urls and url_file.exists():
        urls = load_urls_from_file(url_file)

    if not urls:
        print("用法: python main.py <URL> 或 -f urls.txt")
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
