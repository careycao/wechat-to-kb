#!/usr/bin/env python3
"""
xhs_builder.py — 小红书收藏入库主流程。

抓取收藏 → kb_router 路由 → 写入对应 KnowBase。

用法：
  python xhs_builder.py --login          # 首次登录
  python xhs_builder.py                  # 增量抓取（跳过已存在）
  python xhs_builder.py --limit 50       # 限制数量
  python xhs_builder.py --no-skip        # 重新处理所有
"""

from __future__ import annotations

import logging
import re
import shutil
import sys
from pathlib import Path

# 引入 kb_collector 模块
KB_COLLECTOR = Path(__file__).resolve().parent.parent / "kb_collector"
sys.path.insert(0, str(KB_COLLECTOR))

VIDEO_COLLECTOR = Path(__file__).resolve().parent.parent / "video_collector"

from kb_config import ALL_KBS, KB_BY_KEY, KBConfig
from kb_router import route, prompt_user_choice
from xhs_fetcher import fetch_collections, login

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

SAFE_CHARS = re.compile(r'[\\/:*?"<>|]')


def _try_enhance_video_note(post: dict) -> dict:
    """
    视频笔记：经 video_collector（yt-dlp）拉字幕/简介，替换 content 供路由与入库。
    失败时保持原标题+desc。
    """
    if not post.get("is_video"):
        return post
    try:
        from cookies import resolve_cookies_path
        from platforms import fetch_video
        from video_types import format_plain
    except ImportError as e:
        logger.warning("无法加载 video_collector，跳过视频增强: %s", e)
        return post

    cookiefile = resolve_cookies_path(VIDEO_COLLECTOR, None)
    try:
        record = fetch_video(post["url"], cookiefile=cookiefile)
    except Exception as e:
        logger.warning("video_collector 拉取失败，使用原正文: %s", e)
        return post
    if not record:
        logger.info("视频笔记未解析到 VideoRecord，保持原正文")
        return post
    body = (record.body_text or "").strip()
    fallback_hint = "（未能获取简介或字幕，请仅依赖标题与外链）"
    if not body or body == fallback_hint:
        logger.info("视频笔记无可用字幕/简介增强，保持原正文")
        return post
    plain = format_plain(record)
    out = dict(post)
    out["content"] = plain
    out["video_enhanced"] = True
    logger.info("已用 video_collector 增强视频笔记正文: %s", post["title"][:50])
    return out


def safe_filename(title: str, max_len: int = 80) -> str:
    s = SAFE_CHARS.sub("_", title).strip()
    return (s[:max_len] or "untitled").strip("_")


# ---------------------------------------------------------------------------
# 存储
# ---------------------------------------------------------------------------

def _already_exists(title: str) -> bool:
    """检查任意 KB 下是否已有同名文件。"""
    safe = safe_filename(title)
    for kb in ALL_KBS:
        for cat in kb.category_order:
            prefixed = kb.raw_to_prefixed.get(cat, cat)
            if (kb.path / prefixed / f"{safe}.txt").exists():
                return True
    return False


def _save_post(post: dict, kb: KBConfig, category: str) -> None:
    """将帖子写入指定 KB 的分类目录。"""
    title   = post["title"]
    content = post["content"]
    url     = post["url"]
    tags    = post.get("tags", [])

    safe    = safe_filename(title)
    prefixed = kb.raw_to_prefixed.get(category, category)
    dest_dir = kb.path / prefixed
    dest_dir.mkdir(parents=True, exist_ok=True)

    # TXT
    txt_lines = [title, "", content, ""]
    if tags:
        txt_lines.append("标签：" + " / ".join(tags))
    txt_lines.append(f"来源：{url}")
    (dest_dir / f"{safe}.txt").write_text("\n".join(txt_lines), encoding="utf-8")

    # HTML（轻量，保留原文链接）
    tag_html = " ".join(f'<span class="tag">{t}</span>' for t in tags)
    html = (
        f'<!DOCTYPE html><html><head><meta charset="utf-8">'
        f"<!-- original_url: {url} -->"
        f"<title>{title}</title></head><body>"
        f"<h1>{title}</h1>"
        f"<p>{content.replace(chr(10), '<br>')}</p>"
        f"<p>{tag_html}</p>"
        f'<p><a href="{url}">原文链接</a></p>'
        f"</body></html>"
    )
    (dest_dir / f"{safe}.html").write_text(html, encoding="utf-8")

    logger.info("已保存 [%s / %s] %s", kb.name, prefixed, title[:50])


# ---------------------------------------------------------------------------
# README 更新（复用 kb_builder 逻辑）
# ---------------------------------------------------------------------------

def _rebuild_index(kb: KBConfig) -> None:
    sys.path.insert(0, str(KB_COLLECTOR))
    from kb_builder import rebuild_index
    rebuild_index(kb)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def _confirm_save(post: dict, kb_key: str, category: str, scores: dict, idx: int, total: int) -> tuple[str, str] | None:
    """
    展示帖子信息和路由结果，等待用户确认。
    返回 (kb_key, category) 表示确认保存；返回 None 表示跳过。
    """
    kb = KB_BY_KEY[kb_key]
    prefixed = kb.raw_to_prefixed.get(category, category)
    score_str = " / ".join(f"{k}={v[0]}" for k, v in scores.items()) if scores else ""
    tags = post.get("tags", [])
    content_preview = post.get("content", "")[:80].replace("\n", " ")

    print("\n" + "─" * 60)
    print(f"[{idx}/{total}] {post['title'][:55]}")
    if tags:
        print(f"  标签：{' / '.join(tags[:5])}")
    if content_preview:
        print(f"  摘要：{content_preview}...")
    print(f"  链接：{post['url']}")
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
            return prompt_user_choice(post["title"], scores)
        print("  无效输入，请重试。")


def run(limit: int = 200, skip_existing: bool = True, user_id: str = "") -> None:
    logger.info("开始抓取小红书收藏...")
    posts = fetch_collections(limit=limit, user_id=user_id)

    if str(VIDEO_COLLECTOR) not in sys.path:
        sys.path.insert(0, str(VIDEO_COLLECTOR))

    if not posts:
        print("未抓取到任何收藏帖子。")
        return

    total = len(posts)
    print(f"\n共抓取到 {total} 篇收藏，逐篇确认后入库。\n")

    changed_kbs: set[str] = set()
    saved = 0
    skipped_exist = 0
    skipped_user = 0

    for idx, post in enumerate(posts, 1):
        post = _try_enhance_video_note(post)

        title = post["title"]

        if skip_existing and _already_exists(title):
            logger.debug("已存在，跳过: %s", title[:50])
            skipped_exist += 1
            continue

        text = post["content"]
        kb_key, category, scores = route(text, title)

        # 路由冲突先解决
        if kb_key is None:
            kb_key, category = prompt_user_choice(title, scores)
            scores = {}  # 冲突已解决，确认环节不再展示得分

        # 人工确认
        result = _confirm_save(post, kb_key, category, scores, idx, total)
        if result is None:
            skipped_user += 1
            continue

        kb_key, category = result
        kb = KB_BY_KEY[kb_key]
        _save_post(post, kb, category)
        changed_kbs.add(kb_key)
        saved += 1

        prefixed = kb.raw_to_prefixed.get(category, category)
        print(f"  ✓ 已存入 {kb.name} / {prefixed}")

    # 重建有变动的 KB 索引
    for kb_key in changed_kbs:
        _rebuild_index(KB_BY_KEY[kb_key])

    print(f"\n完成：新增 {saved} 篇 / 用户跳过 {skipped_user} 篇 / 已存在跳过 {skipped_exist} 篇")


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="小红书收藏 → 知识库")
    parser.add_argument("--login",   action="store_true", help="重新扫码登录")
    parser.add_argument("--limit",   type=int, default=200, help="最多处理数量")
    parser.add_argument("--no-skip",  action="store_true", help="重新处理已存在的帖子")
    parser.add_argument("--user-id",  default="",          help="手动指定小红书 user_id（24位）")
    args = parser.parse_args()

    if args.login:
        login()
        sys.exit(0)

    run(limit=args.limit, skip_existing=not args.no_skip, user_id=args.user_id)
