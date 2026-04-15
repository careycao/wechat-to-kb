#!/usr/bin/env python3
"""
video_builder.py — 视频链接 → 文本化 → kb_router → 逐篇确认 → 入库。

用法：
  python video_builder.py <URL> [URL2 ...]
  python video_builder.py -f urls.txt
  python video_builder.py --kb engineering <URL>
  python video_builder.py --dry-run <URL>
  python video_builder.py --cookies /path/to/cookies.txt <URL>
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

REPO_ROOT = SCRIPT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common.kb_config import ALL_KBS, KB_BY_KEY, warn_if_using_default_config
from common.kb_indexing import rebuild_index
from common.kb_routing import prompt_user_choice, route, score_kb as score_kb_one
from common.kb_storage import KBWriter
from common.path_utils import load_urls_from_file
from cookies import resolve_cookies_path
from platforms import fetch_video
from video_types import format_plain, html_fragment_for_kb

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logging.getLogger("jieba").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def sanitize_pasted_url(url: str) -> str:
    """
    去掉从终端复制时误带的反斜杠（如 \\?vd_source= 写成 \\?vd_source\\=），
    否则 HTTP 请求会 404。
    """
    u = url.strip()
    for old, new in (("\\?", "?"), ("\\&", "&"), ("\\=", "="), ("\\#", "#")):
        u = u.replace(old, new)
    return u


def _confirm_save(record, kb_key: str, category: str, scores: dict, idx: int, total: int):
    """返回 (kb_key, category) 或 None（跳过）。"""
    kb = KB_BY_KEY[kb_key]
    prefixed = kb.raw_to_prefixed.get(category, category)
    score_str = " / ".join(f"{k}={v[0]}" for k, v in scores.items()) if scores else ""
    plain = format_plain(record)
    preview = plain[:200].replace("\n", " ")

    print("\n" + "─" * 60)
    print(f"[{idx}/{total}] {record.title[:55]}")
    print(f"  平台：{record.platform}  来源：{record.transcript_source}")
    if preview:
        print(f"  预览：{preview}...")
    print(f"  链接：{record.canonical_url}")
    print(f"  → 建议存入：{kb.name} / {prefixed}" + (f"  ({score_str})" if score_str else ""))
    print()
    print("  [y] 确认保存  [n] 跳过  [c] 改变 KB/分类  [q] 退出")
    print("─" * 60)

    while True:
        try:
            choice = input("请选择 [y/n/c/q，直接回车=y]: ").strip().lower() or "y"
        except (EOFError, KeyboardInterrupt):
            print("\n已退出。")
            sys.exit(0)

        if choice == "y":
            return kb_key, category
        if choice == "n":
            print("  已跳过。")
            return None
        if choice == "q":
            print("已退出。")
            sys.exit(0)
        if choice == "c":
            return prompt_user_choice(record.title, scores)
        print("  无效输入，请重试。")


def run_dry_run(
    urls: list[str],
    cookiefile: str | None,
    kb_hint: str | None,
) -> None:
    """只打印：拉取结果、路由建议、完整 plain_text；不写库、不 input。"""
    clean = [u.strip() for u in urls if u.strip() and not u.strip().startswith("#")]
    n = len(clean)
    print("=== VIDEO_COLLECTOR_DRY_RUN ===")
    print(f"cookiefile: {cookiefile or '(未使用)'}")
    print(f"count: {n}")

    for idx, url in enumerate(clean, 1):
        logger.info("[%d/%d] dry-run 拉取: %s", idx, n, url[:80])
        record = fetch_video(url, cookiefile=cookiefile)
        if not record:
            print(f"\n--- [{idx}/{n}] FAILED ---")
            print(f"url: {url}")
            print("(yt-dlp 未返回可用数据)")
            continue

        plain = format_plain(record)
        title = record.title

        print(f"\n--- [{idx}/{n}] ---")
        print(f"url: {url}")
        print(f"title: {title}")
        print(f"platform: {record.platform}  transcript_source: {record.transcript_source}")

        if kb_hint and kb_hint in KB_BY_KEY:
            _, cat = score_kb_one(plain, title, KB_BY_KEY[kb_hint])
            if not cat:
                cat = "未分类"
            kb = KB_BY_KEY[kb_hint]
            pref = kb.raw_to_prefixed.get(cat, cat)
            print(f"route (--kb {kb_hint}): {kb.name} / {pref}")
        else:
            kb_key, category, scores = route(plain, title)
            if kb_key is None:
                print("route: 置信度低，候选：")
                ranked = sorted(scores.items(), key=lambda x: x[1][0], reverse=True)
                for k, (sc, cat) in ranked:
                    kb = KB_BY_KEY[k]
                    pref = kb.raw_to_prefixed.get(cat, cat)
                    print(f"  {k}: {kb.name} / {pref}  (score={sc})")
            else:
                kb = KB_BY_KEY[kb_key]
                pref = kb.raw_to_prefixed.get(category, category)
                print(f"route: {kb.name} / {pref}")

        print("--- plain_text ---")
        print(plain)
        print("--- end ---")

    print("\n=== VIDEO_COLLECTOR_DRY_RUN_END ===")


def run(
    urls: list[str],
    kb_hint: str | None = None,
    skip_existing: bool = True,
    cookiefile: str | None = None,
    dry_run: bool = False,
) -> None:
    if dry_run:
        run_dry_run(urls, cookiefile, kb_hint)
        return

    writers: dict[str, KBWriter] = {kb.key: KBWriter(kb) for kb in ALL_KBS}
    changed_kbs: set[str] = set()
    total = len(urls)
    saved = 0
    skipped = 0
    failed = 0

    for idx, url in enumerate(urls, 1):
        url = url.strip()
        if not url or url.startswith("#"):
            continue

        logger.info("[%d/%d] 拉取: %s", idx, total, url[:80])
        record = fetch_video(url, cookiefile=cookiefile)
        if not record:
            print(f"\n✗ 无法解析（跳过）: {url[:70]}")
            failed += 1
            continue

        title = record.title
        plain_for_route = format_plain(record)

        scores_full = {
            kb.key: score_kb_one(plain_for_route, title, kb) for kb in ALL_KBS
        }

        if kb_hint and kb_hint in writers:
            kb_key = kb_hint
            _, category = score_kb_one(plain_for_route, title, KB_BY_KEY[kb_key])
            if not category:
                category = "未分类"
        else:
            kb_key, category, _ = route(plain_for_route, title)
            if kb_key is None:
                kb_key, category = prompt_user_choice(title, scores_full)

        result = _confirm_save(record, kb_key, category, scores_full, idx, total)
        if result is None:
            skipped += 1
            continue

        kb_key, category = result
        writer = writers[kb_key]
        kb = KB_BY_KEY[kb_key]

        if skip_existing and writer.already_exists(title):
            logger.info("已存在，跳过: %s", title[:50])
            skipped += 1
            continue

        plain_text = format_plain(record)
        data = {
            "title": title,
            "plain_text": plain_text,
            "html": html_fragment_for_kb(title, plain_text, record.canonical_url),
            "url": record.canonical_url,
        }
        writer.save_stage(data)
        writer.classify_and_move(title, category)
        changed_kbs.add(kb_key)
        saved += 1

        prefixed = kb.raw_to_prefixed.get(category, category)
        print(f"\n✓ 已存入 {kb.name} / {prefixed}")
        print(f"  标题：{title[:60]}\n")

    for kb_key in changed_kbs:
        rebuild_index(KB_BY_KEY[kb_key])

    print(
        f"\n完成：成功 {saved} 条 / 跳过或放弃 {skipped} 条 / 解析失败 {failed} 条"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="video_collector — 视频链接入库知识库")
    parser.add_argument("url", nargs="*", help="视频页 URL，可多个")
    parser.add_argument("-f", "--file", dest="url_file", default="", help="从文件读取 URL，每行一个")
    parser.add_argument(
        "--kb",
        default=None,
        choices=list(KB_BY_KEY.keys()),
        help="强制指定目标知识库（ai / engineering / management）",
    )
    parser.add_argument("--no-skip", action="store_true", help="不跳过已存在同标题的条目")
    parser.add_argument(
        "--cookies",
        default=None,
        metavar="PATH",
        help="Netscape Cookie 文件；也可设环境变量 VIDEO_COLLECTOR_COOKIES 或使用本目录 cookies.txt",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅打印拉取结果与路由建议（plain_text），不写知识库、不交互",
    )
    args = parser.parse_args()
    warn_if_using_default_config()

    urls: list[str] = list(args.url)
    if args.url_file:
        path = Path(args.url_file)
        if not path.is_absolute():
            path = SCRIPT_DIR / path
        urls.extend(load_urls_from_file(path))

    if not urls:
        print("用法: python video_builder.py <URL> 或 -f urls.txt")
        sys.exit(1)

    urls = [sanitize_pasted_url(u) for u in urls]

    cookiefile = resolve_cookies_path(SCRIPT_DIR, args.cookies)

    run(
        urls,
        kb_hint=args.kb,
        skip_existing=not args.no_skip,
        cookiefile=cookiefile,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("用户中断")
        sys.exit(130)
