#!/usr/bin/env python3
"""
main.py — RSS_Monitor 入口。

用法：
  python main.py                    # 抓取今日文章，输出结构化列表
  python main.py --date 2026-04-09  # 查看指定日期已抓取的文章
  python main.py --no-fetch         # 不重新抓取，只输出已有数据
  python main.py --top-n 15         # 精选条数（默认 10）

输出格式：=== RSS_MONITOR_OUTPUT === 块，供 OpenClaw 会话 LLM 消费。
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from fetcher import fetch_all
from store import ArticleStore

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"
EXCERPT_LEN = 150   # 输出时每篇正文摘录字数


# ---------------------------------------------------------------------------
# 配置加载
# ---------------------------------------------------------------------------

def load_config() -> dict:
    if not CONFIG_PATH.exists():
        print(f"[ERROR] 找不到配置文件: {CONFIG_PATH}", file=sys.stderr)
        sys.exit(1)
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ---------------------------------------------------------------------------
# 输出格式化
# ---------------------------------------------------------------------------

def _excerpt(content: str, length: int = EXCERPT_LEN) -> str:
    if not content:
        return "（无正文）"
    text = content.replace("\n", " ").strip()
    return text[:length] + ("…" if len(text) > length else "")


def _fmt_published(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso[:16]


def _parse_tags(tags_val) -> list[str]:
    """tags 字段在 DB 里是 JSON 字符串，读出后需反序列化。"""
    import json
    if not tags_val:
        return []
    if isinstance(tags_val, list):
        return tags_val
    try:
        result = json.loads(tags_val)
        return result if isinstance(result, list) else []
    except Exception:
        return []


def print_output(articles: list[dict], date_str: str, top_n: int) -> None:
    """
    输出 === RSS_MONITOR_OUTPUT === 块。
    OpenClaw 会话读取此块后，由用户配置的 LLM 生成摘要与精选。
    """
    total = len(articles)
    # 按发布时间倒序，最新的在前
    sorted_articles = sorted(
        articles,
        key=lambda a: a.get("published", ""),
        reverse=True,
    )
    candidates = sorted_articles[:top_n]

    print("=== RSS_MONITOR_OUTPUT ===")
    print(f"date:          {date_str}")
    print(f"total_fetched: {total}")
    print(f"top_n:         {top_n}")
    print(f"candidates:    {len(candidates)}")
    print()

    for i, a in enumerate(candidates, 1):
        print(f"  [{i}] 《{a.get('title', '（无标题）')}》")
        print(f"      source:  {a.get('source_name', '?')} | {_fmt_published(a.get('published', ''))}")
        print(f"      url:     {a.get('url', '')}")
        tags = _parse_tags(a.get("tags"))
        if tags:
            print(f"      tags:    {', '.join(tags)}")
        print(f"      excerpt: {_excerpt(a.get('content', ''))}")
        print(f"      id:      {a.get('id', '?')}  (保存时用此编号)")
        print()

    print("=== END ===")
    print()
    print("── 使用说明 ──────────────────────────────────────────────")
    print("1. 请对上方文章列表生成摘要日报（每篇：一句话核心观点 + 2~3 个要点）")
    print("2. 按话题或标签分类展示")
    print("3. 最后列出「今日精选」编号，等待用户确认是否保存到知识库")
    print("   保存命令示例：python save_to_kb.py 1 3 5")
    print("──────────────────────────────────────────────────────────")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="RSS_Monitor — 技术订阅摘要日报")
    parser.add_argument(
        "--date",
        default=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        help="指定日期 YYYY-MM-DD（默认今天）",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=10,
        help="精选条数（默认 10）",
    )
    parser.add_argument(
        "--no-fetch",
        action="store_true",
        help="跳过抓取，直接输出数据库中已有数据",
    )
    parser.add_argument(
        "--no-content",
        action="store_true",
        help="不抓取全文（只用 RSS 摘要），速度更快",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="显示详细日志",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.INFO)

    cfg = load_config()
    store = ArticleStore()
    date_str = args.date

    fetch_cfg = cfg.get("fetch", {})
    max_age_hours   = fetch_cfg.get("max_age_hours", 24)
    max_per_source  = fetch_cfg.get("max_articles_per_source", 20)
    request_timeout = fetch_cfg.get("request_timeout", 15)

    # ── 抓取 ──────────────────────────────────────────────────────────
    if not args.no_fetch:
        sources = cfg.get("sources", [])
        if not sources:
            print("[WARN] config.yaml 中没有配置 sources", file=sys.stderr)
        else:
            print(f"[RSS_Monitor] 开始抓取 {len(sources)} 个源…", file=sys.stderr)
            new_articles = fetch_all(
                sources=sources,
                store=store,
                max_age_hours=max_age_hours,
                max_per_source=max_per_source,
                fetch_full_content=not args.no_content,
                request_timeout=request_timeout,
                date_str=date_str,
            )
            print(
                f"[RSS_Monitor] 本次新增 {len(new_articles)} 篇",
                file=sys.stderr,
            )

    # ── 读取当日数据 ──────────────────────────────────────────────────
    articles = store.get_by_date(date_str)
    if not articles:
        print(f"[RSS_Monitor] {date_str} 暂无文章", file=sys.stderr)
        print("=== RSS_MONITOR_OUTPUT ===")
        print(f"date:          {date_str}")
        print(f"total_fetched: 0")
        print("candidates:    0")
        print("=== END ===")
        return

    # ── 输出 ─────────────────────────────────────────────────────────
    top_n = cfg.get("digest", {}).get("top_n", 10)
    if args.top_n:
        top_n = args.top_n

    print_output(articles, date_str, top_n)


if __name__ == "__main__":
    main()
