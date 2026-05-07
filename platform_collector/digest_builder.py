#!/usr/bin/env python3
"""
digest_builder.py — 每日技术动态摘要生成器。

流程：
  1. 调用 autocli_fetcher 抓取各平台热榜（HackerNews、GitHub Trending 等）
  2. 调用 DeepSeek API 对热榜内容做聚合摘要分析
  3. 输出结构化 Markdown 到 Inbox/daily_digest/，不写入知识库

设计原则：
  - 热榜是「信息雷达」，不是知识库内容；看完即走
  - AI 摘要聚焦「为什么值得看」，而不是复述标题
  - 原文链接全保留，方便按需点进去

用法：
  python digest_builder.py                    # 抓取默认任务，生成今日摘要
  python digest_builder.py --tasks hackernews_hot github_trending
  python digest_builder.py --dry-run          # 只打印，不写文件
  python digest_builder.py --no-ai            # 跳过 AI 摘要，只输出原始热榜
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT  = SCRIPT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from autocli_fetcher import list_tasks, TASK_REGISTRY

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# 默认每日运行的任务（有直连 API 的优先）
DEFAULT_TASKS = ["hackernews_hot", "github_trending"]

# Inbox 目录（相对于知识库根目录）
INBOX_SUBDIR = "Inbox/daily_digest"


# ---------------------------------------------------------------------------
# DeepSeek API
# ---------------------------------------------------------------------------

def _load_api_key() -> str:
    """从 .env 文件或环境变量加载 DeepSeek API Key。"""
    # 优先环境变量
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if key:
        return key

    # 从仓库根目录 .env 加载
    env_path = REPO_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("DEEPSEEK_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
                if key:
                    return key

    return ""


def _call_deepseek(prompt: str, api_key: str, model: str = "deepseek-chat") -> str:
    """调用 DeepSeek Chat API，返回回复文本。"""
    url = "https://api.deepseek.com/v1/chat/completions"
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 1500,
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "digest-builder/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"].strip()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"DeepSeek API 错误 {e.code}: {body}") from e


def _build_summary_prompt(sections: list[dict]) -> str:
    """构建摘要 prompt。sections 是 {label, items} 的列表。"""
    lines = [
        "你是一个技术信息分析助手。以下是今日各平台热榜内容，请完成：\n",
        "1. **今日主题**（2-3 句）：概括今天热榜整体在讨论什么方向，不要罗列，要归纳",
        "2. **值得关注的亮点**（每个平台选 3 条）：说明为什么值得看，不是复述标题",
        "3. **如果只有 10 分钟**：给出 1-2 条优先阅读推荐，附原文链接",
        "\n输出格式：Markdown，简洁，中文。\n",
        "---\n",
    ]
    for section in sections:
        lines.append(f"## {section['label']}\n")
        for i, item in enumerate(section["items"][:20], 1):
            title = item.get("title", "")
            url   = item.get("url", "")
            desc  = item.get("description", "")
            line  = f"{i}. {title}"
            if url:
                line = f"{i}. [{title}]({url})"
            if desc:
                line += f"\n   {desc[:100]}"
            lines.append(line)
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 摘要文件格式化
# ---------------------------------------------------------------------------

def _format_digest(
    sections: list[dict],
    ai_summary: str,
    date_str: str,
    time_str: str,
) -> str:
    """将各平台数据 + AI 摘要格式化为最终 Markdown。"""
    lines = [
        "---",
        f"date: {date_str}",
        f"generated_at: {time_str}",
        f"sources: {', '.join(s['label'] for s in sections)}",
        "type: daily_digest",
        "---",
        "",
        f"# 技术动态摘要 · {date_str}",
        "",
        f"> 生成时间：{date_str} {time_str}",
        "",
    ]

    # AI 摘要部分
    if ai_summary:
        lines += [
            "## AI 摘要",
            "",
            ai_summary,
            "",
            "---",
            "",
        ]

    # 各平台原始列表（保留链接，供点进去用）
    for section in sections:
        lines.append(f"## {section['label']}（{len(section['items'])} 条）")
        lines.append("")
        for i, item in enumerate(section["items"], 1):
            title = item.get("title", "（无标题）")
            url   = item.get("url", "")
            desc  = item.get("description", "")
            if url:
                lines.append(f"**{i}.** [{title}]({url})")
            else:
                lines.append(f"**{i}.** {title}")
            if desc:
                lines.append(f"> {desc[:120]}")
            lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Inbox 目录
# ---------------------------------------------------------------------------

def _inbox_dir() -> Path:
    """返回 Inbox 目录，自动创建。"""
    # 从 common/kb_config 获取知识库根路径
    try:
        from common.kb_config import ALL_KBS
        kb_root = ALL_KBS[0].path.parent  # 知识库根目录（各 KB 的上级）
    except Exception:
        # fallback：使用仓库根目录同级
        kb_root = REPO_ROOT.parent / "knowledge_base"

    inbox = kb_root / INBOX_SUBDIR
    inbox.mkdir(parents=True, exist_ok=True)
    return inbox


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def build_digest(
    tasks: list[str],
    dry_run: bool = False,
    use_ai: bool = True,
    overwrite: bool = False,
) -> bool:
    """
    抓取指定任务，生成每日摘要。

    返回 True=成功，False=失败。
    """
    now      = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")

    # 确定输出路径
    dest = _inbox_dir() / f"{date_str}.md"
    if dest.exists() and not overwrite and not dry_run:
        logger.info("今日摘要已存在，跳过（用 --overwrite 强制重建）: %s", dest)
        return True

    # 抓取各平台
    sections = []
    for task in tasks:
        label = TASK_REGISTRY.get(task, {}).get("label", task)
        logger.info("抓取 [%s]...", task)
        items = _get_items_for_task(task, limit=20)
        if not items:
            logger.warning("[%s] 抓取失败或无结果，跳过", task)
            continue
        logger.info("[%s] 获取 %d 条", label, len(items))
        sections.append({"label": label, "items": items, "task": task})

    if not sections:
        logger.error("所有任务均失败，无法生成摘要")
        return False

    # AI 摘要
    ai_summary = ""
    if use_ai:
        api_key = _load_api_key()
        if not api_key:
            logger.warning("未找到 DEEPSEEK_API_KEY，跳过 AI 摘要")
        else:
            try:
                logger.info("调用 DeepSeek 生成摘要...")
                prompt = _build_summary_prompt(sections)
                ai_summary = _call_deepseek(prompt, api_key)
                logger.info("AI 摘要生成完成（%d 字）", len(ai_summary))
            except Exception as e:
                logger.warning("AI 摘要失败，继续输出原始列表: %s", e)

    # 格式化
    digest_md = _format_digest(sections, ai_summary, date_str, time_str)

    if dry_run:
        print(f"\n{'='*60}")
        print(f"[DRY-RUN] 输出路径: {dest}")
        print(f"{'='*60}\n")
        print(digest_md)
        return True

    dest.write_text(digest_md, encoding="utf-8")
    logger.info("已写入: %s", dest)
    print(f"\n✅ 今日摘要 → {dest}")
    if ai_summary:
        print("\n── AI 摘要预览 ──")
        print(ai_summary[:500] + ("…" if len(ai_summary) > 500 else ""))
    return True


def _get_items_for_task(task: str, limit: int = 20) -> list[dict]:
    """直接从 DIRECT_API 或 autocli 获取条目列表（不经过 snapshot 格式化）。"""
    from autocli_fetcher import DIRECT_API, TASK_REGISTRY, _run_autocli, _parse_items

    if task in DIRECT_API:
        items = DIRECT_API[task](limit)
        if items:
            return items

    # fallback: autocli
    cfg  = TASK_REGISTRY.get(task, {})
    args = cfg.get("args", task.split()) + ["--limit", str(limit)]
    ok, raw = _run_autocli(args)
    if ok:
        return _parse_items(raw)
    return []


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="每日技术动态摘要生成器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python digest_builder.py
  python digest_builder.py --tasks hackernews_hot github_trending
  python digest_builder.py --dry-run
  python digest_builder.py --no-ai
""",
    )
    parser.add_argument(
        "--tasks", nargs="*", default=DEFAULT_TASKS,
        help=f"要抓取的任务（默认：{' '.join(DEFAULT_TASKS)}）",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="只打印，不写文件",
    )
    parser.add_argument(
        "--no-ai", action="store_true",
        help="跳过 AI 摘要，只输出原始热榜列表",
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="覆盖今日已有摘要",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="列出可用任务",
    )
    args = parser.parse_args()

    if args.list:
        print("可用任务：")
        for name in list_tasks():
            print(f"  {name}")
        return

    ok = build_digest(
        tasks=args.tasks,
        dry_run=args.dry_run,
        use_ai=not args.no_ai,
        overwrite=args.overwrite,
    )
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
