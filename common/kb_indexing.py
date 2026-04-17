"""README and URL index generation for knowledge bases."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from common.kb_config import KBConfig
from common.text_processing import extract_keywords_for_index, extract_summary

logger = logging.getLogger(__name__)


def _extract_original_url(md_path: Path) -> str | None:
    """从 .md 文件的 YAML frontmatter 中提取原始 URL。"""
    if not md_path.exists():
        return None
    try:
        raw = md_path.read_text(encoding="utf-8", errors="replace")
        if raw.startswith("---"):
            end = raw.find("---", 3)
            if end != -1:
                for line in raw[3:end].splitlines():
                    if line.startswith("url:"):
                        return line.split(":", 1)[1].strip()
        return None
    except Exception:
        return None


def _folder_number(category: str) -> str:
    if len(category) >= 2 and category[:2].isdigit():
        return category[:2]
    return "00"


def collect_articles_meta(kb: KBConfig) -> list[dict]:
    rows: list[dict] = []
    for category_dir in kb.path.iterdir():
        if not category_dir.is_dir() or category_dir.name.startswith("_"):
            continue

        for md_path in category_dir.glob("*.md"):
            html_path = category_dir / f"{md_path.stem}.html"
            try:
                text = md_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                text = ""

            rows.append(
                {
                    "category": category_dir.name,
                    "title": md_path.stem,
                    "html_path": html_path,
                    "md_path": md_path,
                    "summary": extract_summary(text),
                    "keywords": extract_keywords_for_index(text),
                    "original_url": _extract_original_url(md_path),
                }
            )
    return rows


def generate_readme(kb: KBConfig, rows: list[dict]) -> str:
    ordered = sorted(rows, key=lambda row: (_folder_number(row["category"]), row["category"], row["title"]))
    lines = [
        f"# {kb.name}",
        "",
        "## 简介",
        "",
        kb.description,
        "",
        "## 文章索引",
        "",
        "| 分类 | 编号 | 文章标题 | 核心观点简介 | 关键词 |",
        "|------|------|----------|--------------|--------|",
    ]

    for index, row in enumerate(ordered, 1):
        code_6 = _folder_number(row["category"]) + f"{index:04d}"
        if row.get("original_url"):
            link = f"[{row['title']}]({row['original_url']})"
        else:
            rel_path = row["category"] + "/" + row["md_path"].name
            link = f"[{row['title']}]({rel_path})"
        summary = (row["summary"] or "-")[:80]
        keywords = (row["keywords"] or "-")[:60]
        lines.append(f"| {row['category']} | {code_6} | {link} | {summary} | {keywords} |")

    return "\n".join(lines)


def write_url_list(kb: KBConfig, rows: list[dict]) -> None:
    ordered = sorted(rows, key=lambda row: (_folder_number(row["category"]), row["category"], row["title"]))
    lines_out = []
    for index, row in enumerate(ordered, 1):
        url = row.get("original_url") or ""
        if not url:
            continue
        folder_num = _folder_number(row["category"])
        lines_out.append(f"{folder_num}{index:04d}\t{url}")

    path = kb.path / "des_url_list.txt"
    path.write_text("\n".join(lines_out) + ("\n" if lines_out else ""), encoding="utf-8")
    logger.info("已生成链接列表: %s（%d 条）", path, len(lines_out))


def rebuild_index(kb: KBConfig) -> None:
    rows = collect_articles_meta(kb)
    (kb.path / "README.md").write_text(generate_readme(kb, rows), encoding="utf-8")
    write_url_list(kb, rows)
    logger.info("索引重建完成：%s（%d 篇）", kb.name, len(rows))
