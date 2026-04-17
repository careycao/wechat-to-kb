"""Storage helpers for writing collected content into knowledge bases."""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path

from markdownify import markdownify as md_convert

from common.kb_config import KBConfig
from common.text_processing import safe_filename

logger = logging.getLogger(__name__)


class KBWriter:
    """负责将文章写入指定 KB 的分类目录。"""

    def __init__(self, kb: KBConfig):
        self.kb = kb
        self.stage_dir = kb.path / "_stage"
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        self.kb.path.mkdir(parents=True, exist_ok=True)
        self.stage_dir.mkdir(parents=True, exist_ok=True)
        for name in self.kb.ordered_prefixed:
            (self.kb.path / name).mkdir(parents=True, exist_ok=True)

    def _stage_paths(self, title: str) -> tuple[Path, Path]:
        safe = safe_filename(title)
        return self.stage_dir / f"{safe}.html", self.stage_dir / f"{safe}.md"

    def _category_paths(self, category: str, title: str) -> tuple[Path, Path]:
        safe = safe_filename(title)
        dir_name = self.kb.raw_to_prefixed.get(category, category)
        category_dir = self.kb.path / dir_name
        return category_dir / f"{safe}.html", category_dir / f"{safe}.md"

    def already_exists(self, title: str) -> bool:
        for category in self.kb.category_order:
            html_path, md_path = self._category_paths(category, title)
            if html_path.exists() or md_path.exists():
                return True
        return False

    def _html_to_md(self, html: str, title: str, url: str) -> str:
        """Convert HTML article content to Markdown with YAML frontmatter."""
        body_md = md_convert(
            html,
            heading_style="ATX",
            bullets="-",
            strip=["script", "style"],
        )
        date_str = datetime.now().strftime("%Y-%m-%d")
        frontmatter = f"---\ntitle: {title}\nurl: {url}\ndate: {date_str}\n---\n\n"
        return frontmatter + f"# {title}\n\n" + body_md.strip()

    def save_stage(self, data: dict) -> tuple[Path, Path]:
        title = data["title"]
        url = data.get("url", "")
        html_path, md_path = self._stage_paths(title)

        # 保留 HTML 作为原始存档
        url_comment = f"<!-- original_url: {url} -->"
        full_html = (
            '<!DOCTYPE html><html><head><meta charset="utf-8">'
            f"<title>{title}</title>{url_comment}</head>"
            f"<body><article>{data['html']}</article></body></html>"
        )
        html_path.write_text(full_html, encoding="utf-8")

        # 主文件改为 .md，供 AI 检索和 Obsidian 查看
        md_content = self._html_to_md(data["html"], title, url)
        md_path.write_text(md_content, encoding="utf-8")

        logger.info("已暂存: %s", title[:50])
        return html_path, md_path

    def classify_and_move(self, title: str, category: str) -> str:
        html_stage, md_stage = self._stage_paths(title)
        category_html, category_md = self._category_paths(category, title)

        if html_stage.exists():
            content = html_stage.read_text(encoding="utf-8", errors="replace")
            content = content.replace("_images/", "../_images/")
            category_html.write_text(content, encoding="utf-8")
            html_stage.unlink()

        if md_stage.exists():
            shutil.move(str(md_stage), str(category_md))

        prefixed = self.kb.raw_to_prefixed.get(category, category)
        logger.info("已归类 [%s] -> %s", prefixed, title[:50])
        return category
