"""
import_youdao.py — 有道精选 Markdown 本地文件迁入脚本。

用法：
  # 仅生成分类报告，不写任何文件
  python tools/import_youdao.py --dry-run

  # 实际执行迁移
  python tools/import_youdao.py

  # 更多选项
  python tools/import_youdao.py --help
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from common.kb_config import KB_BY_KEY, KBConfig
from common.kb_routing import pick_highest_score_route, route
from common.text_processing import safe_filename

# ── 默认路径 ────────────────────────────────────────────────────────────────

_KB_ROOT = Path.home() / "knowledge_base"
_DEFAULT_SOURCE = _KB_ROOT / "Archive" / "Youdao" / "Youdao_精选"
_DEFAULT_REPORT = Path.cwd() / "youdao_import_report.md"
_YOUDAO_SUBDIR = "youdao"  # 归类子目录名，固定为 youdao

# ── 数据结构 ─────────────────────────────────────────────────────────────────


@dataclass
class ArticleRecord:
    """单篇文章的路由结果。"""

    src_path: Path
    title: str
    kb_key: str
    kb_name: str
    category: str
    prefixed_category: str
    best_score: int
    confidence: str          # "高" | "⚠低" | "⚠未路由"
    image_count: int
    dest_path: Path
    skipped: bool = False    # dry-run 时始终 False；执行时若文件已存在且无 --overwrite 则 True
    conflict_note: str = ""


# ── 文本预处理 ───────────────────────────────────────────────────────────────

_RE_MD_IMAGE = re.compile(r"!\[.*?\]\(.*?\)")
_RE_MD_LINK = re.compile(r"\[([^\]]+)\]\([^\)]+\)")
_RE_FRONTMATTER = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)


def _strip_md_for_routing(text: str) -> str:
    """去掉图片/链接等 Markdown 标记，仅保留用于路由的纯文字。"""
    text = _RE_FRONTMATTER.sub("", text)
    text = _RE_MD_IMAGE.sub("", text)
    text = _RE_MD_LINK.sub(r"\1", text)
    return text


def _count_images(text: str) -> int:
    return len(_RE_MD_IMAGE.findall(text))


# ── 路由 ─────────────────────────────────────────────────────────────────────


def _route_article(text: str, title: str) -> tuple[str, str, int, str]:
    """
    返回 (kb_key, category, best_score, confidence)。
    """
    kb_key, category, scores = route(text, title)

    if kb_key is not None:
        best_score = scores[kb_key][0]
        return kb_key, category, best_score, "高"

    # 低置信度：取最高分路由
    kb_key, category = pick_highest_score_route(scores)
    best_score = scores[kb_key][0]

    if best_score == 0:
        return kb_key, category, 0, "⚠未路由"
    return kb_key, category, best_score, "⚠低"


# ── YAML frontmatter ─────────────────────────────────────────────────────────

_FRONTMATTER_TEMPLATE = """\
---
source: youdao_精选
original_path: {original_path}
migrated_at: {migrated_at}
---

"""


def _build_frontmatter(src_path: Path, source_root: Path) -> str:
    rel = src_path.relative_to(source_root)
    return _FRONTMATTER_TEMPLATE.format(
        original_path=rel.as_posix(),
        migrated_at=date.today().isoformat(),
    )


# ── 扫描源目录 ───────────────────────────────────────────────────────────────


def _collect_md_files(source_root: Path) -> list[Path]:
    return sorted(
        p for p in source_root.rglob("*.md")
        if p.name.lower() != "readme.md"
    )


# ── 构造目标路径 ─────────────────────────────────────────────────────────────


def _dest_path(kb: KBConfig, category: str, title: str) -> Path:
    prefixed = kb.raw_to_prefixed.get(category, category)
    safe = safe_filename(title)
    return kb.path / prefixed / _YOUDAO_SUBDIR / f"{safe}.md"


# ── 核心处理循环 ─────────────────────────────────────────────────────────────


def process_files(
    source_root: Path,
    dry_run: bool,
    overwrite: bool,
) -> list[ArticleRecord]:
    md_files = _collect_md_files(source_root)
    if not md_files:
        print(f"[警告] 源目录未找到任何 .md 文件：{source_root}", file=sys.stderr)
        return []

    records: list[ArticleRecord] = []

    for src in md_files:
        raw_text = src.read_text(encoding="utf-8", errors="replace")
        title = src.stem
        routing_text = _strip_md_for_routing(raw_text)
        image_count = _count_images(raw_text)

        kb_key, category, best_score, confidence = _route_article(routing_text, title)
        kb = KB_BY_KEY[kb_key]
        prefixed = kb.raw_to_prefixed.get(category, category)
        dest = _dest_path(kb, category, title)

        record = ArticleRecord(
            src_path=src,
            title=title,
            kb_key=kb_key,
            kb_name=kb.name,
            category=category,
            prefixed_category=prefixed,
            best_score=best_score,
            confidence=confidence,
            image_count=image_count,
            dest_path=dest,
        )

        if not dry_run:
            _write_article(record, src, raw_text, source_root, overwrite)

        records.append(record)
        _print_progress(record, dry_run)

    return records


def _write_article(
    record: ArticleRecord,
    src: Path,
    raw_text: str,
    source_root: Path,
    overwrite: bool,
) -> None:
    dest = record.dest_path

    if dest.exists() and not overwrite:
        record.skipped = True
        record.conflict_note = "SKIP（已存在）"
        return

    dest.parent.mkdir(parents=True, exist_ok=True)

    frontmatter = _build_frontmatter(src, source_root)
    dest.write_text(frontmatter + raw_text, encoding="utf-8")

    # 复制同级 attachments/ 目录
    src_attachments = src.parent / "attachments"
    if src_attachments.is_dir():
        dest_attachments = dest.parent / "attachments"
        if dest_attachments.exists() and overwrite:
            shutil.rmtree(dest_attachments)
        if not dest_attachments.exists():
            shutil.copytree(str(src_attachments), str(dest_attachments))


def _print_progress(record: ArticleRecord, dry_run: bool) -> None:
    tag = "[DRY]" if dry_run else ("[SKIP]" if record.skipped else "[OK]")
    conf_icon = "" if record.confidence == "高" else " ⚠"
    print(
        f"{tag} {record.kb_name}/{record.prefixed_category}"
        f"  score={record.best_score}{conf_icon}"
        f"  {record.title[:40]}"
    )


# ── 报告生成 ─────────────────────────────────────────────────────────────────


def _build_report(records: list[ArticleRecord], source_root: Path, dry_run: bool) -> str:
    today = date.today().isoformat()
    mode_label = "（Dry-run 预览，未写入文件）" if dry_run else "（实际执行结果）"
    total = len(records)

    # 汇总：按 KB 统计
    from collections import Counter
    kb_counter: Counter = Counter(r.kb_name for r in records)
    conf_counter: Counter = Counter(r.confidence for r in records)
    skipped = sum(1 for r in records if r.skipped)

    lines: list[str] = [
        f"# Youdao 精选导入报告 {mode_label}",
        f"",
        f"生成时间：{today}　　源目录：`{source_root}`　　总文件：{total}",
        f"",
        f"## 路由汇总",
        f"",
        f"| KB | 文章数 |",
        f"|----|--------|",
    ]
    for kb_name, count in sorted(kb_counter.items()):
        lines.append(f"| {kb_name} | {count} |")

    lines += [
        f"",
        f"## 置信度分布",
        f"",
        f"| 置信度 | 文章数 |",
        f"|--------|--------|",
    ]
    for conf, count in sorted(conf_counter.items()):
        lines.append(f"| {conf} | {count} |")

    if skipped:
        lines += [f"", f"> 跳过（文件已存在）：{skipped} 篇"]

    lines += [
        f"",
        f"## 明细清单",
        f"",
        f"| 原文件（相对路径） | 标题 | 目标 KB | 目标分类 | 得分 | 置信度 | 图片数 | 备注 |",
        f"|---|---|---|---|---|---|---|---|",
    ]
    for r in records:
        try:
            rel = r.src_path.relative_to(source_root).as_posix()
        except ValueError:
            rel = r.src_path.name
        note = r.conflict_note or ""
        lines.append(
            f"| {rel} | {r.title} | {r.kb_name} "
            f"| {r.prefixed_category} | {r.best_score} "
            f"| {r.confidence} | {r.image_count} | {note} |"
        )

    return "\n".join(lines) + "\n"


# ── CLI ──────────────────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="将有道精选 Markdown 文件批量迁入知识库。"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅生成分类清单，不写入任何文件",
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=_DEFAULT_SOURCE,
        metavar="PATH",
        help=f"源目录（默认：{_DEFAULT_SOURCE}）",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=_DEFAULT_REPORT,
        metavar="PATH",
        help=f"报告输出路径（默认：{_DEFAULT_REPORT}）",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="覆盖目标 KB 中已存在的同名文件",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    source_root = args.source.expanduser().resolve()
    if not source_root.exists():
        print(f"[错误] 源目录不存在：{source_root}", file=sys.stderr)
        sys.exit(1)

    mode = "Dry-run（仅预览）" if args.dry_run else "执行迁移"
    print(f"── import_youdao ── {mode}")
    print(f"源目录：{source_root}")
    if not args.dry_run:
        print(f"--overwrite：{'是' if args.overwrite else '否'}")
    print()

    records = process_files(source_root, dry_run=args.dry_run, overwrite=args.overwrite)

    if not records:
        print("未找到可处理的文件，退出。")
        return

    report_content = _build_report(records, source_root, dry_run=args.dry_run)
    report_path = args.report.expanduser().resolve()
    report_path.write_text(report_content, encoding="utf-8")

    print()
    print(f"── 完成 ── 共处理 {len(records)} 篇")
    print(f"报告已写入：{report_path}")

    low_conf = [r for r in records if r.confidence != "高"]
    if low_conf:
        print(f"\n⚠ 低置信度 / 未路由文章：{len(low_conf)} 篇，建议检查报告后决策。")


if __name__ == "__main__":
    main()
