"""Storage helpers for writing collected content into knowledge bases."""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path

from markdownify import markdownify as md_convert

from common.kb_config import KBConfig
from common.text_processing import safe_filename
from common.url_utils import normalize_url

logger = logging.getLogger(__name__)


class KBWriter:
    """负责将文章写入指定 KB 的分类目录。"""

    def __init__(self, kb: KBConfig):
        self.kb = kb
        self.stage_dir = kb.path / "_stage"
        self._url_index_path = kb.path / "_url_index.json"
        self._url_index: dict[str, dict] = self._load_url_index()
        self._ensure_dirs()

    def _load_url_index(self) -> dict[str, dict]:
        if not self._url_index_path.exists():
            return {}
        try:
            return json.loads(self._url_index_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("URL 索引读取失败（%s），重建。%s", self._url_index_path, exc)
            return {}

    def _save_url_index(self) -> None:
        self._url_index_path.parent.mkdir(parents=True, exist_ok=True)
        self._url_index_path.write_text(
            json.dumps(self._url_index, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _ensure_dirs(self) -> None:
        self.kb.path.mkdir(parents=True, exist_ok=True)
        self.stage_dir.mkdir(parents=True, exist_ok=True)
        for name in self.kb.ordered_prefixed:
            (self.kb.path / name).mkdir(parents=True, exist_ok=True)
        self.ensure_purpose_md()

    def _stage_paths(self, title: str) -> tuple[Path, Path]:
        safe = safe_filename(title)
        return self.stage_dir / f"{safe}.html", self.stage_dir / f"{safe}.md"

    def _category_paths(self, category: str, title: str) -> tuple[Path, Path]:
        safe = safe_filename(title)
        dir_name = self.kb.raw_to_prefixed.get(category, category)
        category_dir = self.kb.path / dir_name
        return category_dir / f"{safe}.html", category_dir / f"{safe}.md"

    def already_exists(self, title: str, url: str = "") -> bool:
        """Check if article already exists by URL index (preferred) or title/filename."""
        if url:
            norm = normalize_url(url)
            if norm and norm in self._url_index:
                return True
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
        raw_url = data.get("url", "")
        norm_url = normalize_url(raw_url) if raw_url else raw_url
        html_path, md_path = self._stage_paths(title)

        # 保留 HTML 作为原始存档
        url_comment = f"<!-- original_url: {raw_url} -->"
        full_html = (
            '<!DOCTYPE html><html><head><meta charset="utf-8">'
            f"<title>{title}</title>{url_comment}</head>"
            f"<body><article>{data['html']}</article></body></html>"
        )
        html_path.write_text(full_html, encoding="utf-8")

        # 主文件改为 .md，供 AI 检索和 Obsidian 查看（frontmatter 存归一化 URL）
        md_content = self._html_to_md(data["html"], title, norm_url or raw_url)

        # 可选两步 CoT 增强（KB_ENRICH=1 开启）
        if os.environ.get("KB_ENRICH", "").strip() == "1":
            try:
                from common.kb_enricher import enrich_with_llm
                md_content = enrich_with_llm(
                    md_content=md_content,
                    plain_text=data.get("plain_text", ""),
                    title=title,
                    kb=self.kb,
                )
                logger.info("CoT 增强完成: %s", title[:50])
            except Exception as exc:
                logger.warning("CoT 增强失败，保留原始 MD。%s", exc)

        md_path.write_text(md_content, encoding="utf-8")

        # 更新 URL 去重索引
        if norm_url:
            self._url_index[norm_url] = {
                "title": title,
                "path": str(md_path),
                "date": datetime.now().strftime("%Y-%m-%d"),
            }
            self._save_url_index()

        logger.info("已暂存: %s", title[:50])
        return html_path, md_path

    def ensure_purpose_md(self) -> Path:
        """Create a purpose.md template in the KB root if one doesn't exist."""
        purpose_path = self.kb.path / "purpose.md"
        if purpose_path.exists():
            return purpose_path
        template = (
            f"# {self.kb.name} Purpose\n\n"
            "## 为什么建立这个知识库\n\n"
            f"{self.kb.description}\n\n"
            "## 核心问题\n\n"
            "- （请填写：这个知识库主要用于回答什么问题？）\n\n"
            "## 范围边界\n\n"
            "- 收录：（请填写值得收录的内容类型）\n"
            "- 排除：（请填写不值得收录的内容类型）\n"
        )
        purpose_path.write_text(template, encoding="utf-8")
        logger.info("已创建 purpose.md 模板: %s", purpose_path)
        return purpose_path

    def delete_article(self, url: str) -> bool:
        """按 URL 删除已入库的文章文件（.md + .html）并清除索引记录。

        Returns True if an entry was found and removed, False otherwise.
        """
        from common.url_utils import normalize_url

        norm = normalize_url(url) if url else ""
        entry = self._url_index.pop(norm, None) if norm else None

        if entry is None:
            return False

        # 删除索引里记录的 .md 文件及同名 .html
        md_path = Path(entry["path"])
        html_path = md_path.with_suffix(".html")
        deleted: list[str] = []
        for p in (md_path, html_path):
            if p.exists():
                p.unlink()
                deleted.append(p.name)

        self._save_url_index()
        logger.info(
            "已删除 [%s]: %s（文件: %s）",
            self.kb.name,
            entry.get("title", ""),
            ", ".join(deleted) if deleted else "无对应文件",
        )
        return True

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


def delete_from_all_kbs(url: str) -> bool:
    """在所有知识库中按 URL 搜索并删除文章（文件 + 索引记录）。

    Returns True if found and deleted in at least one KB, False if not found anywhere.
    """
    from common.kb_config import ALL_KBS

    found = False
    for kb in ALL_KBS:
        writer = KBWriter(kb)
        if writer.delete_article(url):
            found = True
    return found
