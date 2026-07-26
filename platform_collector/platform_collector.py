#!/usr/bin/env python3
"""
platform_collector.py — 平台内容自动采集调度入口。

负责：
  1. 按 task 名称调用对应采集器（autocli / 可扩展）
  2. 将快照 MD 写入合适的知识库目录（_stage/ 或指定分类）
  3. 提供 CLI 入口，可直接运行或由 openclaw 定时任务调用

用法：
  # 单次运行
  python platform_collector.py zhihu_hot
  python platform_collector.py hackernews_hot --kb ai
  python platform_collector.py yahoo_finance --limit 10

  # 列出可用任务
  python platform_collector.py --list

  # Dry-run（只打印，不写入）
  python platform_collector.py zhihu_hot --dry-run

设计：
  - 与 xhs_builder.py 保持一致：复用 common/kb_config、common/kb_indexing
  - 热榜快照写入 <KB>/_stage/，由人工或 AI 定期整理入正式分类
  - 写入前检查同名文件（按日期去重），避免重复运行时覆盖
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# 路径设置：让 common/ 和 platform_collector/ 都可导入
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT  = SCRIPT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from platform_collector.autocli_fetcher import fetch_task, get_kb_hint, list_tasks

from common.kb_config import ALL_KBS, KB_BY_KEY, warn_if_using_default_config
from common.kb_indexing import rebuild_index
from common.text_processing import safe_filename

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 存储
# ---------------------------------------------------------------------------

def _stage_dir(kb_key: str) -> Path:
    """返回指定 KB 的 _stage 目录路径，不存在时自动创建。"""
    kb = KB_BY_KEY.get(kb_key)
    if not kb:
        # fallback：取第一个 KB 的 _stage
        kb = ALL_KBS[0]
        logger.warning("未找到 KB key=%s，fallback 到 %s", kb_key, kb.name)
    stage = kb.path / "_stage"
    stage.mkdir(parents=True, exist_ok=True)
    return stage


def _dest_path(task: str, label: str, kb_key: str) -> Path:
    """构造目标文件路径（_stage/<task>-<date>.md）。"""
    date_str = datetime.now().strftime("%Y-%m-%d")
    safe_label = safe_filename(label)
    filename = f"{safe_label}-{date_str}.md"
    return _stage_dir(kb_key) / filename


def _write_snapshot(
    snapshot_md: str,
    dest: Path,
    overwrite: bool = False,
) -> bool:
    """
    将快照 MD 写入 dest。
    默认不覆盖同日文件（避免重复运行）。
    返回 True=成功写入，False=已跳过。
    """
    if dest.exists() and not overwrite:
        logger.info("同日文件已存在，跳过写入: %s", dest.name)
        return False

    dest.write_text(snapshot_md, encoding="utf-8")
    logger.info("已写入: %s", dest)
    return True


# ---------------------------------------------------------------------------
# 主采集流程
# ---------------------------------------------------------------------------

def collect(
    task: str,
    kb: str = "",
    limit: int = 20,
    dry_run: bool = False,
    overwrite: bool = False,
) -> bool:
    """
    执行单个 task 的采集 → 格式化 → 存储流程。

    参数：
      task     : 任务名（见 autocli_fetcher.TASK_REGISTRY）
      kb       : 强制指定目标 KB key；为空则由 task 注册表给出建议
      limit    : 最多条目数
      dry_run  : True 时只打印，不写文件
      overwrite: True 时覆盖同日已有文件

    返回：True=成功，False=失败或跳过
    """
    logger.info("开始采集 [%s]...", task)

    ok, content, label = fetch_task(task, limit=limit)
    if not ok:
        print(f"[ERROR] {task}: {content}", file=sys.stderr)
        return False

    # 确定目标 KB
    kb_key = kb or get_kb_hint(task)
    if not kb_key or kb_key not in KB_BY_KEY:
        kb_key = list(KB_BY_KEY.keys())[0]  # fallback 到第一个 KB
        logger.warning("无法确定目标 KB，使用 fallback: %s", kb_key)

    dest = _dest_path(task, label, kb_key)

    if dry_run:
        print(f"\n{'='*60}")
        print(f"[DRY-RUN] 任务: {label}")
        print(f"[DRY-RUN] 目标: {dest}")
        print(f"{'='*60}")
        print(content)
        return True

    written = _write_snapshot(content, dest, overwrite=overwrite)
    if written:
        # 重建该 KB 的索引
        kb_cfg = KB_BY_KEY[kb_key]
        rebuild_index(kb_cfg)
        print(f"✅ [{label}] → {dest.relative_to(REPO_ROOT.parent)}")
    else:
        print(f"⏭  [{label}] 今日快照已存在，跳过（用 --overwrite 强制覆盖）")

    return True


def collect_all(
    tasks: list[str],
    kb: str = "",
    limit: int = 20,
    dry_run: bool = False,
    overwrite: bool = False,
) -> dict[str, bool]:
    """批量执行多个 task，返回 {task: success} 结果字典。"""
    results = {}
    for task in tasks:
        try:
            results[task] = collect(task, kb=kb, limit=limit, dry_run=dry_run, overwrite=overwrite)
        except Exception as e:
            logger.error("任务 [%s] 异常: %s", task, e)
            results[task] = False
    return results


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="平台内容自动采集工具（autocli 调度层）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python platform_collector.py zhihu_hot
  python platform_collector.py hackernews_hot --limit 15
  python platform_collector.py zhihu_hot hackernews_hot --kb ai
  python platform_collector.py --all
  python platform_collector.py zhihu_hot --dry-run
""",
    )
    parser.add_argument(
        "tasks",
        nargs="*",
        help="要执行的任务名（可多个，空格分隔）",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="执行所有注册任务",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="列出所有可用任务",
    )
    parser.add_argument(
        "--kb",
        default="",
        help="强制指定目标 KB key（如 ai / engineering / life）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="每个任务最多抓取条目数（默认 20）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印内容，不写入文件",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="覆盖同日已有快照文件",
    )

    args = parser.parse_args()

    if args.list:
        print("可用任务：")
        for name in list_tasks():
            kb_hint = get_kb_hint(name)
            print(f"  {name:30s} → KB: {kb_hint or '自动'}")
        return

    warn_if_using_default_config()

    tasks_to_run: list[str] = []
    if args.all:
        tasks_to_run = list_tasks()
    elif args.tasks:
        tasks_to_run = args.tasks
    else:
        parser.print_help()
        sys.exit(1)

    if len(tasks_to_run) == 1:
        ok = collect(
            tasks_to_run[0],
            kb=args.kb,
            limit=args.limit,
            dry_run=args.dry_run,
            overwrite=args.overwrite,
        )
        sys.exit(0 if ok else 1)
    else:
        results = collect_all(
            tasks_to_run,
            kb=args.kb,
            limit=args.limit,
            dry_run=args.dry_run,
            overwrite=args.overwrite,
        )
        ok_count = sum(1 for v in results.values() if v)
        print(f"\n完成：{ok_count}/{len(results)} 个任务成功")
        if ok_count < len(results):
            for task, ok in results.items():
                if not ok:
                    print(f"  ❌ {task}")
        sys.exit(0 if ok_count == len(results) else 1)


if __name__ == "__main__":
    main()
