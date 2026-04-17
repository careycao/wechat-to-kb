#!/usr/bin/env python3
"""
migrate_txt_to_md.py — 将知识库中现有的 .txt 文件迁移为 .md 格式。

用法：
  python3 migrate_txt_to_md.py          # dry-run，只打印不执行
  python3 migrate_txt_to_md.py --apply  # 正式执行迁移
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from markdownify import markdownify as md_convert

from common.kb_config import ALL_KBS
from common.kb_indexing import rebuild_index


def _extract_url_from_html(html_path: Path) -> str:
    """从 HTML 文件的注释中提取原始 URL。"""
    try:
        raw = html_path.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"<!--\s*original_url:\s*(\S+)\s*-->", raw)
        return match.group(1).strip() if match else ""
    except Exception:
        return ""


def _extract_article_html(html_path: Path) -> str:
    """提取 HTML 文件中 <article> 或 <body> 的内容。"""
    try:
        raw = html_path.read_text(encoding="utf-8", errors="replace")
        # 优先提取 <article> 标签内容
        match = re.search(r"<article[^>]*>([\s\S]*?)</article>", raw, re.IGNORECASE)
        if match:
            return match.group(1)
        # 回退到 <body>
        match = re.search(r"<body[^>]*>([\s\S]*?)</body>", raw, re.IGNORECASE)
        if match:
            return match.group(1)
        return raw
    except Exception:
        return ""


def _html_to_md(article_html: str, title: str, url: str, date: str = "") -> str:
    """将 HTML 内容转为带 frontmatter 的 Markdown。"""
    body_md = md_convert(
        article_html,
        heading_style="ATX",
        bullets="-",
        strip=["script", "style"],
    ).strip()

    lines = ["---", f"title: {title}"]
    if url:
        lines.append(f"url: {url}")
    if date:
        lines.append(f"date: {date}")
    lines += ["---", "", f"# {title}", "", body_md]
    return "\n".join(lines)


def _txt_to_md(txt_path: Path, title: str, url: str) -> str:
    """将纯文本内容包装为带 frontmatter 的 Markdown（无 HTML 时的回退）。"""
    try:
        text = txt_path.read_text(encoding="utf-8", errors="replace").strip()
    except Exception:
        text = ""

    lines = ["---", f"title: {title}"]
    if url:
        lines.append(f"url: {url}")
    lines += ["---", "", f"# {title}", "", text]
    return "\n".join(lines)


def migrate_kb(kb, dry_run: bool) -> tuple[int, int, int]:
    """迁移单个知识库，返回 (转换数, 跳过数, 失败数)。"""
    converted = skipped = failed = 0

    for category_dir in sorted(kb.path.iterdir()):
        if not category_dir.is_dir() or category_dir.name.startswith("_"):
            continue

        for txt_path in sorted(category_dir.glob("*.txt")):
            title = txt_path.stem
            md_path = txt_path.with_suffix(".md")
            html_path = txt_path.with_suffix(".html")

            if md_path.exists():
                print(f"  [跳过] 已有 .md：{category_dir.name}/{title[:50]}")
                skipped += 1
                continue

            try:
                url = _extract_url_from_html(html_path) if html_path.exists() else ""

                if html_path.exists():
                    article_html = _extract_article_html(html_path)
                    md_content = _html_to_md(article_html, title, url)
                    source = "html"
                else:
                    md_content = _txt_to_md(txt_path, title, url)
                    source = "txt"

                print(f"  [{'dry-run' if dry_run else '转换'}] ({source}) {category_dir.name}/{title[:50]}")

                if not dry_run:
                    md_path.write_text(md_content, encoding="utf-8")
                    txt_path.unlink()

                converted += 1

            except Exception as e:
                print(f"  [失败] {category_dir.name}/{title[:50]} — {e}")
                failed += 1

    return converted, skipped, failed


def main() -> None:
    parser = argparse.ArgumentParser(description="迁移知识库 .txt → .md")
    parser.add_argument("--apply", action="store_true", help="正式执行（默认为 dry-run）")
    args = parser.parse_args()

    dry_run = not args.apply

    if dry_run:
        print("=== DRY-RUN 模式，不会实际修改文件，加 --apply 正式执行 ===\n")
    else:
        print("=== 正式执行迁移 ===\n")

    total_converted = total_skipped = total_failed = 0

    for kb in ALL_KBS:
        if not kb.path.exists():
            print(f"[{kb.name}] 目录不存在，跳过\n")
            continue

        print(f"[{kb.name}]")
        converted, skipped, failed = migrate_kb(kb, dry_run)
        print(f"  → 转换 {converted} 篇 / 跳过 {skipped} 篇 / 失败 {failed} 篇\n")
        total_converted += converted
        total_skipped += skipped
        total_failed += failed

    print(f"汇总：转换 {total_converted} 篇 / 跳过 {total_skipped} 篇 / 失败 {total_failed} 篇")

    if not dry_run and total_converted > 0:
        print("\n正在重建所有知识库索引...")
        for kb in ALL_KBS:
            if kb.path.exists():
                rebuild_index(kb)
        print("索引重建完成 ✅")


if __name__ == "__main__":
    main()
