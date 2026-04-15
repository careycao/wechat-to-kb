"""Storage helpers for writing collected content into knowledge bases."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

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
        return self.stage_dir / f"{safe}.html", self.stage_dir / f"{safe}.txt"

    def _category_paths(self, category: str, title: str) -> tuple[Path, Path]:
        safe = safe_filename(title)
        dir_name = self.kb.raw_to_prefixed.get(category, category)
        category_dir = self.kb.path / dir_name
        return category_dir / f"{safe}.html", category_dir / f"{safe}.txt"

    def already_exists(self, title: str) -> bool:
        for category in self.kb.category_order:
            html_path, text_path = self._category_paths(category, title)
            if html_path.exists() or text_path.exists():
                return True
        return False

    def save_stage(self, data: dict) -> tuple[Path, Path]:
        title = data["title"]
        html_path, txt_path = self._stage_paths(title)
        url_comment = f"<!-- original_url: {data.get('url', '')} -->"
        full_html = (
            '<!DOCTYPE html><html><head><meta charset="utf-8">'
            f"<title>{title}</title>{url_comment}</head>"
            f"<body><article>{data['html']}</article></body></html>"
        )
        html_path.write_text(full_html, encoding="utf-8")
        txt_path.write_text(data["plain_text"], encoding="utf-8")
        logger.info("已暂存: %s", title[:50])
        return html_path, txt_path

    def classify_and_move(self, title: str, category: str) -> str:
        html_stage, text_stage = self._stage_paths(title)
        category_html, category_txt = self._category_paths(category, title)

        if html_stage.exists():
            content = html_stage.read_text(encoding="utf-8", errors="replace")
            content = content.replace("_images/", "../_images/")
            category_html.write_text(content, encoding="utf-8")
            html_stage.unlink()

        if text_stage.exists():
            shutil.move(str(text_stage), str(category_txt))

        prefixed = self.kb.raw_to_prefixed.get(category, category)
        logger.info("已归类 [%s] -> %s", prefixed, title[:50])
        return category
