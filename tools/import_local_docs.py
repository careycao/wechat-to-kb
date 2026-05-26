"""
import_local_docs.py — 本地文档导入脚本（支持 PDF / PPTX / DOCX）。

Phase 1 范围：
  * 支持 PDF、PPTX、DOCX 格式
  * keep 文档归档到 Archive/LocalDocs/imported/<doc_type>/<KB>/<分类>/
  * 默认开启 Claude 价值评估，**只有 verdict=keep 才写摘要卡入库**
    - verdict=review / low-value 的文件：原件不归档，不入索引
    - 用户看报告后，可用 --force-include-hash 二次重跑把 review 的加进来
  * 复用 common.kb_routing 路由 + common.kb_indexing 索引
  * Dry-run 报告 + 简易 hash 去重（仅查归档目录 + 本批次）

价值评估后端（自动选择）：
  1. 本机 `claude` CLI（Claude Code 登录态）—— 推荐，**无需 API Key**
  2. ANTHROPIC_API_KEY —— 回退
  3. 两者都没有 → 报错退出（或加 --no-value-check 关闭评估）

用法：
  # 默认跑，自动开启价值评估（优先用本机 claude CLI）；扫描 pdf/pptx/docx
  python tools/import_local_docs.py --source ~/Documents/Docs

  # 只处理 pptx
  python tools/import_local_docs.py --source ~/Documents/Docs --type pptx

  # 关闭价值评估（回退到一期行为：所有文档都入库）
  python tools/import_local_docs.py --source ~/Documents/Docs --no-value-check

  # 仅生成报告，不写任何文件
  python tools/import_local_docs.py --source ~/Documents/Docs --dry-run

  # 看完报告后，手动把 2 篇 review 条目强制入库（按 hash 前 8 位）
  python tools/import_local_docs.py --source ~/Documents/Docs \\
      --force-include-hash 3b1b52d7,7418dd5d

  # 单文件 / 覆盖
  python tools/import_local_docs.py --file ~/Documents/a.pptx
  python tools/import_local_docs.py --source ~/Documents/Docs --overwrite

依赖：见 tools/requirements.txt
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from common.kb_config import ALL_KBS, KB_BY_KEY, KBConfig  # noqa: E402
from common.kb_indexing import rebuild_index  # noqa: E402
from common.kb_routing import pick_highest_score_route, route  # noqa: E402
from common.text_processing import (  # noqa: E402
    extract_keywords_for_index,
    extract_summary,
    safe_filename,
)
from tools.value_assessor import (  # noqa: E402
    ValueAssessment,
    ValueCache,
    assess_document,
)

logger = logging.getLogger(__name__)

# ── 默认路径 ────────────────────────────────────────────────────────────────

def _resolve_kb_root() -> Path:
    """
    解析 KB 根目录：优先读 KB_ROOT 环境变量（需非空），否则回退 ~/knowledge_base。

    注意：不能简单写成 `Path(os.environ.get("KB_ROOT", "")) or Path.home() / ...`，
    因为 Path("") 等同于 Path(".") 且为真值，会导致兜底分支永远走不到。
    """
    raw = os.environ.get("KB_ROOT", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / "knowledge_base"


_KB_ROOT = _resolve_kb_root()
_DEFAULT_ARCHIVE_ROOT = _KB_ROOT / "Archive" / "LocalDocs" / "imported"
_DEFAULT_VALUE_CACHE = _KB_ROOT / "Archive" / "LocalDocs" / ".value_cache.json"

_SUPPORTED_EXTENSIONS: set[str] = {".pdf", ".pptx", ".docx"}


def _parser_name(doc_type: str) -> str:
    """返回摘要卡中 parser 字段值。"""
    return f"markitdown[{doc_type}]"


_DEFAULT_REPORT_DIR = _KB_ROOT / "Archive" / "LocalDocs" / "reports"


def _default_report_path(source: Path | None, single_file: Path | None) -> Path:
    """
    根据来源自动生成报告路径，默认落在 Archive/LocalDocs/reports/ 下：
      --source ~/电脑资料/技术相关资料  →  .../reports/local_docs_import_report_技术相关资料_20260420.md
      --file   ~/Documents/a.pdf       →  .../reports/local_docs_import_report_a_20260420.md
      （fallback）                      →  .../reports/local_docs_import_report_20260420.md
    """
    today = date.today().strftime("%Y%m%d")
    if source is not None:
        tag = source.expanduser().resolve().name
    elif single_file is not None:
        tag = single_file.expanduser().resolve().stem
    else:
        return _DEFAULT_REPORT_DIR / f"local_docs_import_report_{today}.md"
    safe_tag = re.sub(r'[\\/:*?"<>|]', "_", tag).strip("_") or "import"
    return _DEFAULT_REPORT_DIR / f"local_docs_import_report_{safe_tag}_{today}.md"


# 归档目录下的就近参考 README。首次运行自动创建，之后不覆盖（用户可自由编辑）。
_LOCAL_DOCS_README_CONTENT = """\
# Local Docs 归档区

本目录是 `tools/import_local_docs.py` 为「本地文档导入」管理的归档区：

- `imported/pdf/<KB>/<分类>/` ：归档的 PDF 原件副本（按 SHA-256 去重）
- `imported/pptx/<KB>/<分类>/` ：归档的 PPTX 原件副本
- `imported/docx/<KB>/<分类>/` ：归档的 DOCX 原件副本
- `.value_cache.json` ：Claude 价值评估的本地缓存（按 file_hash 索引）

每张摘要卡位于 `~/knowledge_base/<KB 名>/<序号-分类>/<标题>.md`，
顶部 YAML frontmatter 字段说明如下。完整设计见
`~/knowledge_base/21-Projects/wechat-to-kb/Designs/20260419-local-pdf-import-design.md`。

---

## 摘要卡字段速查

### 基础元信息

| 字段 | 类型 / 取值 | 含义 |
|------|-------------|------|
| `title` | 字符串 | 文档标题（PDF 元数据优先，否则文件名） |
| `url` | `file://...` | 指向归档原件的本地链接，供 kb_indexing 生成跳转 |
| `source` | `local_doc` | 来源类型标识（固定） |
| `doc_type` | `pdf` / `pptx` / `docx` | 文档格式，从文件后缀动态派生 |
| `storage_mode` | `summary` / `fulltext` | 存储模式；一期固定 `summary`（摘要卡） |
| `original_path` | 绝对路径 | 用户本地原始文件位置 |
| `archived_path` | 绝对路径 | 归档副本位置（本目录下 `imported/pdf/...`） |
| `file_hash` | `sha256:<64>` | 原件 SHA-256，用于去重 + 评估缓存键 |
| `imported_at` | `YYYY-MM-DD` | 导入日期 |
| `parser` | `markitdown[pdf]` | 抽取器标识 |

### 解析质量

| 字段 | 类型 / 取值 | 含义 |
|------|-------------|------|
| `parse_quality` | `high` / `medium` / `low` | 三指标裁决 |
| `chars_per_page` | 数字 | **每页字符数：≥400 好 · 80–400 中 · <80 警觉** |
| `chinese_ratio` | 0–1 | 中文字符占比 |
| `garbage_ratio` | 0–1 | 乱码 / 控制字符占比；**≥0.02 自动打为 low** |
| `page_count` | 数字 | 页数（pypdf 优先，回退估算） |

判档规则：`chars_per_page ≥ 400` 且 `garbage_ratio < 0.005` → `high`；
`chars_per_page < 80` 或 `garbage_ratio ≥ 0.02` → `low`；其余 `medium`。

### 价值评估（--no-value-check 时不写入）

| 字段 | 类型 / 取值 | 含义 |
|------|-------------|------|
| `value_verdict` | `keep` / `review` / `low-value` | LLM 判决；**只有 keep 才真正写摘要卡入库** |
| `value_topic_decay` | 0–10 | 主题未过时程度（10=基础原理 / 长期有效） |
| `value_ai_displacement` | 0–10 | AI 时代独特价值（10=LLM 难替代、需人类深度理解） |
| `value_timelessness` | 0–10 | 抽象出可迁移原则（10=第一性原理 / 思维框架） |
| `value_personal_relevance` | 0–10 | 与 KB 关注方向契合度 |
| `value_reason` | 中文一句话 | 判断依据 |
| `value_model` | 模型 ID 字符串 | 打分模型（默认 claude-haiku-4-5-20251001） |
| `value_force_included` | `true`（缺省即 false） | 是否经 --force-include-hash 二次确认入库 |

---

## 二次确认工作流

看完 `local_docs_import_report.md` 后，如果 `❓ 待人工确认` 段某条想留：

```bash
./run_import_local_docs.sh --source <你的目录> --force-include-hash <hash8>[,<hash8>...]
```

`<hash8>` 即报告表格里的前 8 位哈希。value cache 会命中，LLM 不会重算。

---

_本文件由 tools/import_local_docs.py 首次运行时自动生成；之后不覆盖，可自由编辑。_
"""


def _ensure_local_docs_readme(archive_root: Path) -> None:
    """
    首次运行时在 `~/knowledge_base/33-Archive/LocalDocs/README.md` 生成就近参考 README。
    已存在则不动（尊重用户可能已做的编辑）。
    """
    # archive_root = ~/knowledge_base/33-Archive/LocalDocs/imported，所以上一级是 LocalDocs/
    readme_path = archive_root.parent / "README.md"
    try:
        readme_path.parent.mkdir(parents=True, exist_ok=True)
        if not readme_path.exists():
            readme_path.write_text(_LOCAL_DOCS_README_CONTENT, encoding="utf-8")
            print(f"[i] 已在归档区生成字段速查：{readme_path}")
    except Exception as exc:
        logger.warning("生成归档区 README 失败（可忽略）：%s", exc)

# ── 数据结构 ─────────────────────────────────────────────────────────────────


@dataclass
class LocalDocRecord:
    """单个本地 PDF 的导入记录。"""

    src_path: Path
    title: str
    file_hash: str
    page_count: int
    parse_quality: str           # "high" | "medium" | "low"
    quality_metrics: dict        # 三指标原始值
    kb_key: str
    kb_name: str
    category: str
    prefixed_category: str
    best_score: int
    confidence: str              # "高" | "⚠低" | "⚠未路由"
    storage_mode: str            # phase1 固定 "summary"
    card_path: Path
    archived_path: Path | None = None  # low-value 不归档时为 None
    skipped: bool = False
    skip_reason: str = ""
    duplicate_of: str = ""       # 去重命中时记录原文件路径
    value: ValueAssessment | None = None  # 关闭评估时为 None
    force_included: bool = False


# ── 文档抽取 + 质量评估 ──────────────────────────────────────────────────────

_RE_GARBAGE = re.compile(r"[■□�\ufffd\x00-\x08\x0b\x0c\x0e-\x1f]")
_RE_CHINESE = re.compile(r"[\u4e00-\u9fff]")
_RE_MD_FRONTMATTER = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)
_RE_MD_IMAGE = re.compile(r"!\[.*?\]\(.*?\)")
_RE_MD_LINK = re.compile(r"\[([^\]]+)\]\([^\)]+\)")


def _strip_md_for_routing(text: str) -> str:
    text = _RE_MD_FRONTMATTER.sub("", text)
    text = _RE_MD_IMAGE.sub("", text)
    text = _RE_MD_LINK.sub(r"\1", text)
    return text


def _compute_file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _extract_document(path: Path) -> tuple[str, str, int]:
    """
    统一文档抽取入口，按后缀分发到格式专属逻辑。

    返回 (title, markdown_text, page_count)。
    """
    try:
        from markitdown import MarkItDown  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            "缺少依赖：请先安装 markitdown\n  pip install \"markitdown[pdf]\""
        ) from exc

    md = MarkItDown()
    result = md.convert(str(path))
    text_md = (getattr(result, "text_content", "") or "").strip()
    title = (getattr(result, "title", None) or path.stem).strip()

    page_count = _estimate_page_count(path, text_md)
    return title, text_md, page_count


def _estimate_page_count(path: Path, text_md: str) -> int:
    """
    按文件格式估算页数：
      - PDF  → pypdf 真实页数，失败时用 \\f 分页符或字数估算
      - PPTX → python-pptx 幻灯片数
      - DOCX → 按 1500 字/页估算（python-docx 无直接页数 API）
    """
    ext = path.suffix.lower()

    if ext == ".pptx":
        try:
            from pptx import Presentation  # type: ignore
            prs = Presentation(str(path))
            count = len(prs.slides)
            return max(1, count)
        except Exception:
            pass
        # 回退：按字数估算
        plain = _strip_md_for_routing(text_md)
        return max(1, len(plain) // 200)  # pptx 每页字少，用 200 字/页

    if ext == ".docx":
        plain = _strip_md_for_routing(text_md)
        return max(1, len(plain) // 1500)

    # PDF（默认）
    try:
        from pypdf import PdfReader  # type: ignore
        reader = PdfReader(str(path))
        return len(reader.pages)
    except Exception:
        pass

    if "\f" in text_md:
        return text_md.count("\f") + 1

    plain = _strip_md_for_routing(text_md)
    return max(1, len(plain) // 1500)


def _quality_thresholds(doc_type: str) -> tuple[float, float]:
    """
    返回 (high_min_chars_per_page, low_max_chars_per_page) 阈值。

    PPTX 幻灯片文字天然偏少（大量图表），阈值需显著降低，否则几乎全判 low。
    """
    if doc_type == "pptx":
        return 100.0, 20.0
    # pdf / docx 使用同一套阈值
    return 400.0, 80.0


def _score_parse_quality(text_md: str, page_count: int, doc_type: str = "pdf") -> tuple[str, dict]:
    """
    三指标打分 → high / medium / low。

    指标：
      - chars_per_page：抽取后纯文本字符数 / 页数
      - chinese_ratio：中文字符占比（针对中文场景）
      - garbage_ratio：乱码符号占比

    阈值由 _quality_thresholds(doc_type) 决定，PPTX 使用更低阈值。
    固定阈值：garbage_ratio >= 0.02 → low；garbage_ratio < 0.005 辅助判 high。
    """
    plain = _strip_md_for_routing(text_md)
    total = max(1, len(plain))
    pages = max(1, page_count)

    chars_per_page = len(plain) / pages
    chinese_chars = len(_RE_CHINESE.findall(plain))
    chinese_ratio = chinese_chars / total
    garbage_chars = len(_RE_GARBAGE.findall(plain))
    garbage_ratio = garbage_chars / total

    metrics = {
        "chars_per_page": round(chars_per_page, 1),
        "chinese_ratio": round(chinese_ratio, 3),
        "garbage_ratio": round(garbage_ratio, 4),
        "total_chars": len(plain),
        "page_count": pages,
    }

    high_thresh, low_thresh = _quality_thresholds(doc_type)

    if chars_per_page >= high_thresh and garbage_ratio < 0.005:
        return "high", metrics
    if chars_per_page < low_thresh or garbage_ratio >= 0.02:
        return "low", metrics
    return "medium", metrics


# ── 路由 ─────────────────────────────────────────────────────────────────────


def _route(text: str, title: str) -> tuple[str, str, int, str]:
    kb_key, category, scores = route(text, title)
    if kb_key is not None:
        return kb_key, category, scores[kb_key][0], "高"

    kb_key, category = pick_highest_score_route(scores)
    best = scores[kb_key][0]
    return kb_key, category, best, "⚠未路由" if best == 0 else "⚠低"


# ── 归档 ─────────────────────────────────────────────────────────────────────


def _archive_doc(
    src: Path,
    file_hash: str,
    archive_root: Path,
    doc_type: str,
    kb_key: str,
    prefixed_category: str,
    archive_hash_index: dict[str, Path],
    dry_run: bool,
) -> Path:
    """
    把原始文档复制到归档目录，按 doc_type / KB / 分类建子目录：
      <archive_root>/<doc_type>/<kb_key>/<prefixed_category>/<safe_name><ext>

    调用前须已确认不是重复文件（dedup 由 process_files 在评估前处理）。
    同名但内容不同 → 用 __<hash8> 后缀区分。
    """
    sub_dir = archive_root / doc_type / kb_key / prefixed_category
    sub_dir.mkdir(parents=True, exist_ok=True)

    ext = src.suffix.lower()
    safe_stem = safe_filename(src.stem)
    candidate = sub_dir / f"{safe_stem}{ext}"
    if candidate.exists():
        candidate = sub_dir / f"{safe_stem}__{file_hash[:8]}{ext}"

    if not dry_run:
        shutil.copy2(str(src), str(candidate))

    archive_hash_index[file_hash] = candidate
    return candidate


def _build_archive_hash_index(archive_root: Path) -> dict[str, Path]:
    """递归扫描归档根目录下所有支持格式的文件，建立 hash → path 索引（用于跨批次去重）。"""
    index: dict[str, Path] = {}
    if not archive_root.exists():
        return index
    for ext in _SUPPORTED_EXTENSIONS:
        for p in archive_root.rglob(f"*{ext}"):
            try:
                index[_compute_file_hash(p)] = p
            except Exception as exc:
                logger.warning("hash 失败：%s（%s）", p, exc)
    return index


# ── 摘要卡渲染 ───────────────────────────────────────────────────────────────


def _build_summary_card(
    *,
    title: str,
    src_path: Path,
    doc_type: str,
    archived_path: Path | None,
    file_hash: str,
    text_md: str,
    parse_quality: str,
    metrics: dict,
    value: ValueAssessment | None,
    force_included: bool,
) -> str:
    plain = _strip_md_for_routing(text_md)
    summary = extract_summary(plain, max_sentences=3, max_len=240) or "(无摘要)"
    keywords = extract_keywords_for_index(plain, top_k=8) or "-"

    # 选段：取前两段非空段落
    paragraphs = [p.strip() for p in plain.split("\n\n") if len(p.strip()) >= 30]
    excerpt = "\n\n".join(paragraphs[:2]) if paragraphs else ""

    today = date.today().isoformat()
    parser = _parser_name(doc_type)

    fm_lines = ["---", f"title: {title}"]
    if archived_path is not None:
        # url 字段填 file:// 形式，让 kb_indexing 能识别为可链接来源
        fm_lines.append("url: file://" + str(archived_path))
    fm_lines += [
        "source: local_doc",
        f"doc_type: {doc_type}",
        "storage_mode: summary",
        f"original_path: {src_path}",
    ]
    if archived_path is not None:
        fm_lines.append(f"archived_path: {archived_path}")
    fm_lines += [
        f"file_hash: sha256:{file_hash}",
        f"imported_at: {today}",
        f"parser: {parser}",
        f"parse_quality: {parse_quality}",
        f"chars_per_page: {metrics['chars_per_page']}",
        f"chinese_ratio: {metrics['chinese_ratio']}",
        f"garbage_ratio: {metrics['garbage_ratio']}",
        f"page_count: {metrics['page_count']}",
    ]
    if value is not None:
        fm_lines += [
            f"value_verdict: {value.verdict}",
            f"value_topic_decay: {value.topic_decay}",
            f"value_ai_displacement: {value.ai_displacement}",
            f"value_timelessness: {value.timelessness}",
            f"value_personal_relevance: {value.personal_relevance}",
            f"value_reason: {_yaml_escape(value.reason)}",
            f"value_model: {value.model}",
        ]
    if force_included:
        fm_lines.append("value_force_included: true")
    fm_lines += ["---", ""]
    fm = "\n".join(fm_lines) + "\n"

    body = [
        f"# {title}",
        "",
        "## 摘要",
        "",
        summary,
        "",
        "## 关键词",
        "",
        f"- {keywords}",
        "",
        "## 文件信息",
        "",
        f"- 原始路径：`{src_path}`",
        f"- 归档路径：`{archived_path}`" if archived_path else "- 归档路径：（未归档）",
        f"- 页数：{metrics['page_count']}",
        f"- 解析质量：{parse_quality}（chars/page={metrics['chars_per_page']}, "
        f"中文占比={metrics['chinese_ratio']}, 乱码占比={metrics['garbage_ratio']}）",
        f"- 解析器：{parser}",
    ]
    if value is not None:
        body += [
            "",
            "## 价值评估",
            "",
            f"- 结论：**{value.verdict}**"
            + ("（用户确认后强制入库）" if force_included else ""),
            f"- 得分：{value.score_summary}",
            f"- 判断依据：{value.reason}",
            f"- 评估模型：{value.model}",
        ]
    if excerpt:
        body += ["", "## 选段", "", excerpt]
    return fm + "\n".join(body) + "\n"


def _yaml_escape(s: str) -> str:
    """极简 YAML 字符串转义：把换行压成空格，含特殊字符时加引号。"""
    s = (s or "").replace("\n", " ").replace("\r", " ").strip()
    if any(ch in s for ch in ':#"\'') or s.startswith(("[", "{", "!", "&", "*", "|", ">", "-")):
        s = '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


# ── 写卡片 ───────────────────────────────────────────────────────────────────


def _card_dest_path(kb: KBConfig, category: str, title: str) -> Path:
    prefixed = kb.raw_to_prefixed.get(category, category)
    safe = safe_filename(title)
    return kb.path / prefixed / f"{safe}.md"


def _write_card(card_path: Path, content: str) -> None:
    card_path.parent.mkdir(parents=True, exist_ok=True)
    card_path.write_text(content, encoding="utf-8")


# ── 扫描源 ───────────────────────────────────────────────────────────────────


def _collect_files(
    source: Path | None,
    single_file: Path | None,
    allowed_exts: set[str] | None = None,
) -> list[Path]:
    """
    收集待处理文件。

    - `single_file`：直接使用，校验后缀是否在支持列表内。
    - `source`：递归扫描目录，匹配 allowed_exts（默认 _SUPPORTED_EXTENSIONS）。
    - `allowed_exts`：由 --type 参数确定，None 时等同于 _SUPPORTED_EXTENSIONS。
    """
    exts = allowed_exts if allowed_exts is not None else _SUPPORTED_EXTENSIONS
    if single_file is not None:
        if single_file.name.startswith("~$"):
            print(f"[警告] --file 是 Office 临时文件，跳过：{single_file}", file=sys.stderr)
            return []
        if single_file.suffix.lower() not in exts:
            print(
                f"[警告] --file 后缀 {single_file.suffix!r} 不在支持列表 {sorted(exts)} 内",
                file=sys.stderr,
            )
        return [single_file] if single_file.exists() else []
    if source is None:
        return []
    files: list[Path] = []
    for ext in exts:
        files.extend(
            p for p in source.rglob(f"*{ext}")
            if not p.name.startswith("~$")  # 过滤 Office 临时锁文件
        )
    return sorted(set(files))


# ── 核心处理循环 ─────────────────────────────────────────────────────────────


def process_files(
    *,
    doc_files: list[Path],
    archive_root: Path,
    dry_run: bool,
    overwrite: bool,
    value_check: bool,
    value_cache: ValueCache | None,
    force_include_hash_prefixes: list[str],
    value_client=None,  # 测试时可注入 mock
) -> list[LocalDocRecord]:
    archive_hash_index = _build_archive_hash_index(archive_root)
    batch_seen_hashes: dict[str, Path] = {}

    records: list[LocalDocRecord] = []
    for src in doc_files:
        doc_type = src.suffix.lstrip(".").lower()

        try:
            file_hash = _compute_file_hash(src)
        except Exception as exc:
            print(f"[错误] hash 失败：{src} ({exc})", file=sys.stderr)
            continue

        # ── 去重检查（评估前，避免为重复文件付出 LLM 开销）────────────────

        # 本批次内重复
        if file_hash in batch_seen_hashes:
            seen = batch_seen_hashes[file_hash]
            label = seen.name if seen else src.name
            print(f"[SKIP] 本批次重复（hash 同 {label}）：{src.name}")
            continue

        # 归档目录内重复
        dup_of = ""
        if file_hash in archive_hash_index:
            dup_of = str(archive_hash_index[file_hash])

        try:
            title, text_md, page_count = _extract_document(src)
        except SystemExit:
            raise
        except Exception as exc:
            print(f"[错误] 抽取失败：{src} ({exc})", file=sys.stderr)
            continue

        parse_quality, metrics = _score_parse_quality(text_md, page_count, doc_type)
        routing_text = _strip_md_for_routing(text_md)
        kb_key, category, best_score, confidence = _route(routing_text, title)
        kb = KB_BY_KEY[kb_key]
        prefixed = kb.raw_to_prefixed.get(category, category)
        card_path = _card_dest_path(kb, category, title)

        record = LocalDocRecord(
            src_path=src,
            title=title,
            file_hash=file_hash,
            page_count=metrics["page_count"],
            parse_quality=parse_quality,
            quality_metrics=metrics,
            kb_key=kb_key,
            kb_name=kb.name,
            category=category,
            prefixed_category=prefixed,
            best_score=best_score,
            confidence=confidence,
            storage_mode="summary",
            card_path=card_path,
            duplicate_of=dup_of,
        )

        # 归档目录内重复 → 记录已归档路径，跳过后续评估/写入
        if dup_of:
            record.archived_path = Path(dup_of)
            batch_seen_hashes[file_hash] = record.archived_path
            record.skipped = True
            record.skip_reason = f"DUP（hash 同 {Path(dup_of).name}）"
            _print_progress(record, dry_run)
            records.append(record)
            continue

        # 价值评估（默认开）
        # parse_quality=low 的文档内容基本不可读，直接跳过 LLM 调用
        if value_check:
            if parse_quality == "low":
                record.value = ValueAssessment(
                    topic_decay=0,
                    ai_displacement=0,
                    timelessness=0,
                    personal_relevance=0,
                    verdict="review",
                    reason=(
                        f"解析质量 low（chars/page={metrics['chars_per_page']}，"
                        f"garbage={metrics['garbage_ratio']}），"
                        "内容不可读，已跳过 LLM 评估，请人工确认。"
                    ),
                    model="(parse_quality=low, skipped)",
                )
            else:
                record.value = assess_document(
                    title=title,
                    text=routing_text,
                    file_hash=file_hash,
                    cache=value_cache,
                    client=value_client,
                )

        # 判断是否强制入库（用户从上轮报告拷贝的 hash 前缀）
        force = _match_force_include(file_hash, force_include_hash_prefixes)
        record.force_included = force

        # ── 归档（评估后执行，不入库的文件不归档）────────────────────────────
        # 规则：关闭评估 / verdict=keep / force → 归档
        #       verdict=review 且未强制入库 → 不归档
        #       verdict=low-value 且未强制入库 → 不归档
        skip_archive = (
            value_check
            and record.value is not None
            and record.value.verdict in ("low-value", "review")
            and not force
        )
        if not skip_archive:
            record.archived_path = _archive_doc(
                src, file_hash, archive_root, doc_type, kb_key, prefixed,
                archive_hash_index, dry_run,
            )
            batch_seen_hashes[file_hash] = record.archived_path
        else:
            batch_seen_hashes[file_hash] = None  # 仍需记录 hash，防止批次内重复处理

        # 决定是否写摘要卡
        should_write_card = _should_write_card(record, force)

        if not should_write_card:
            record.skipped = True
            record.skip_reason = _skip_reason_from_value(record)
            _print_progress(record, dry_run)
            records.append(record)
            continue

        if card_path.exists() and not overwrite:
            record.skipped = True
            record.skip_reason = "SKIP（卡片已存在）"
            _print_progress(record, dry_run)
            records.append(record)
            continue

        if not dry_run:
            content = _build_summary_card(
                title=title,
                src_path=src,
                doc_type=doc_type,
                archived_path=record.archived_path,
                file_hash=file_hash,
                text_md=text_md,
                parse_quality=parse_quality,
                metrics=metrics,
                value=record.value,
                force_included=force,
            )
            _write_card(card_path, content)

        _print_progress(record, dry_run)
        records.append(record)

    return records


def _match_force_include(file_hash: str, prefixes: list[str]) -> bool:
    for pfx in prefixes:
        pfx = pfx.strip().lower()
        if pfx and file_hash.lower().startswith(pfx):
            return True
    return False


def _should_write_card(record: LocalDocRecord, force_included: bool) -> bool:
    """
    决定这条记录是否应该写摘要卡。

    规则：
      - 关闭价值评估（value 为 None）→ 全部写（一期默认行为）
      - verdict=keep → 写
      - force_included=True → 写（用户二次确认）
      - 其他（review / low-value / error 降级）→ 不写
    """
    if record.value is None:
        return True
    if record.value.verdict == "keep":
        return True
    if force_included:
        return True
    return False


def _skip_reason_from_value(record: LocalDocRecord) -> str:
    if record.value is None:
        return ""
    v = record.value
    tag = {"review": "待确认", "low-value": "低价值", "keep": "keep"}.get(v.verdict, v.verdict)
    return f"价值 {tag}（{v.score_summary}）"


def _print_progress(record: LocalDocRecord, dry_run: bool) -> None:
    if record.skipped:
        tag = "[DRY-SKIP]" if dry_run else "[SKIP]"
    else:
        tag = "[DRY]" if dry_run else "[OK]"
    conf_icon = "" if record.confidence == "高" else " ⚠"
    value_part = ""
    if record.value is not None:
        mark = {"keep": "✓", "review": "?", "low-value": "✗"}.get(record.value.verdict, "?")
        value_part = f"  v={mark}{record.value.verdict}"
        if record.force_included:
            value_part += "(force)"
    note = f"  {record.skip_reason}" if record.skip_reason else ""
    print(
        f"{tag} {record.kb_name}/{record.prefixed_category}"
        f"  q={record.parse_quality}  score={record.best_score}{conf_icon}"
        f"{value_part}"
        f"  {record.title[:40]}{note}"
    )


# ── 报告 ─────────────────────────────────────────────────────────────────────


def _build_report(
    records: list[LocalDocRecord],
    source_label: str,
    dry_run: bool,
    value_check: bool,
    source_dir_counts: Counter | None = None,
    scanned_exts: set[str] | None = None,
) -> str:
    today = date.today().isoformat()
    mode_label = "（Dry-run，未写入文件）" if dry_run else "（实际执行结果）"

    # 分桶
    kept: list[LocalDocRecord] = []          # verdict=keep 或关闭评估 → 已入库
    review: list[LocalDocRecord] = []        # verdict=review（不含强制入库）
    low_value: list[LocalDocRecord] = []     # verdict=low-value（不含强制入库）
    dup_or_exists: list[LocalDocRecord] = [] # 去重/卡片已存在

    for r in records:
        if r.skip_reason.startswith("DUP") or r.skip_reason.startswith("SKIP"):
            dup_or_exists.append(r)
            continue
        if r.value is None:
            kept.append(r)
        elif r.value.verdict == "keep" or r.force_included:
            kept.append(r)
        elif r.value.verdict == "review":
            review.append(r)
        else:  # low-value
            low_value.append(r)

    low_conf_n = sum(1 for r in records if r.confidence != "高" or r.parse_quality == "low")
    ext_scanned = scanned_exts if scanned_exts is not None else _SUPPORTED_EXTENSIONS

    lines: list[str] = [
        f"# Local Docs 导入报告 {mode_label}",
        "",
        f"生成时间：{today}　　源：`{source_label}`　　总文件：{len(records)}",
        "",
        f"价值评估：{'✅ 开启' if value_check else '⛔ 关闭（所有文件直接入库）'}",
        "",
    ]

    # ── 顶部摘要（始终在最前，方便快速核对）────────────────────────────────────
    lines += [
        "## 📊 运行摘要",
        "",
    ]

    # 源目录文件统计
    if source_dir_counts is not None:
        total_all = sum(source_dir_counts.values())
        lines += [
            "### 源目录文件统计",
            "",
            f"源目录总文件：**{total_all}** 个",
            "",
            "| 格式 | 文件数 | 本次处理 |",
            "|------|--------|---------|",
        ]
        for ext in sorted(source_dir_counts):
            cnt = source_dir_counts[ext]
            ext_label = ext if ext else "(无后缀)"
            if ext in ext_scanned:
                tag = "✅ 已处理"
            elif ext in _SUPPORTED_EXTENSIONS:
                tag = "⏭ 被 --type 跳过"
            else:
                tag = "⚠ 格式不支持"
            lines.append(f"| `{ext_label}` | {cnt} | {tag} |")
        lines.append("")

        unhandled = sum(
            v for k, v in source_dir_counts.items()
            if k not in ext_scanned
        )
        if unhandled == 0:
            lines += ["> ✅ 所有文件格式均已纳入本次处理，确认入库结果后可安全删除源目录。", ""]
        else:
            lines += [
                f"> ⚠ **尚有 {unhandled} 个文件未处理**（见上表 ⏭/⚠ 行），"
                "**删除源目录前请确认已处理完毕！**",
                "",
            ]

    # 处理结果汇总
    lines += [
        "### 处理结果汇总",
        "",
        f"| 分类 | 数量 |",
        f"|------|------|",
        f"| ✅ 已入库（keep / 强制入库） | **{len(kept)}** |",
    ]
    if value_check:
        lines += [
            f"| ❓ 待人工确认（review） | **{len(review)}** |",
            f"| ❌ 低价值跳过（low-value） | **{len(low_value)}** |",
        ]
    lines += [
        f"| ⏭ 去重 / 卡片已存在 | **{len(dup_or_exists)}** |",
    ]
    if low_conf_n:
        lines.append(f"| ⚠ 低置信度 / 低解析质量 | **{low_conf_n}** |")
    if value_check and review:
        lines += [
            "",
            "> ❓ 有待确认文件 —— 查看下方「待人工确认」表，"
            "确认入库可用 `--force-include-hash <hash8>,...` 重跑。",
        ]
    lines.append("")

    # ── 入库统计（按 KB 分布）────────────────────────────────────────────────
    kb_counter: Counter = Counter(r.kb_name for r in kept)
    if kb_counter:
        lines += [
            "### 入库文件路由汇总",
            "",
            "| KB | 文件数 |",
            "|----|--------|",
        ]
        for kb_name, count in sorted(kb_counter.items()):
            lines.append(f"| {kb_name} | {count} |")
        lines.append("")

    # 第 1 段：已入库（keep）
    if kept:
        lines += _render_kept_table(kept)

    # 第 2 段：待确认（review） —— 核心：用户要看这段
    if review:
        lines += _render_review_table(review)

    # 第 3 段：低价值
    if low_value:
        lines += _render_low_value_table(low_value)

    # 第 4 段：去重/已存在（简短）
    if dup_or_exists:
        lines += [
            "## ⏭ 去重 / 卡片已存在（未处理）",
            "",
            "| 文件 | 备注 |",
            "|------|------|",
        ]
        for r in dup_or_exists:
            lines.append(f"| `{r.src_path.name}` | {r.skip_reason} |")
        lines.append("")

    # 第 5 段：低置信度 / 低解析质量（需人工核查）
    low_quality_records = [
        r for r in records
        if r.confidence != "高" or r.parse_quality == "low"
    ]
    if low_quality_records:
        lines += [
            "## ⚠ 低置信度 / 低解析质量（建议人工核查）",
            "",
            "> - **低置信度**：路由匹配分数较低，文件可能被归入了不够准确的 KB/分类，建议确认。",
            "> - **低解析质量**：文件文本抽取效果差（扫描件、加密PDF等），内容摘要可信度低。",
            "",
            "| 文件 | 标题 | 建议 KB/分类 | 置信度 | 解析质量 | chars/page | 处置结果 |",
            "|------|------|------------|--------|---------|-----------|---------|",
        ]
        for r in low_quality_records:
            if r.skip_reason.startswith("DUP"):
                disposition = "已归档（去重）"
            elif r.skip_reason.startswith("SKIP"):
                disposition = "卡片已存在"
            elif r.skipped:
                disposition = (
                    "待确认" if r.value and r.value.verdict == "review" else
                    "低价值" if r.value and r.value.verdict == "low-value" else
                    r.skip_reason
                )
            else:
                disposition = "已入库"
            if r.force_included:
                disposition += "(force)"
            lines.append(
                f"| `{r.src_path.name}` | {r.title[:28]} "
                f"| {r.kb_name}/{r.prefixed_category} "
                f"| {r.confidence} | {r.parse_quality} "
                f"| {r.quality_metrics.get('chars_per_page', '—')} "
                f"| {disposition} |"
            )
        lines.append("")

    return "\n".join(lines) + "\n"


def _render_kept_table(kept: list[LocalDocRecord]) -> list[str]:
    out = [
        "## ✅ 已入库（verdict=keep 或强制入库）",
        "",
        "| 文件 | 标题 | 目标 KB | 分类 | 页数 | 质量 | verdict | 得分 |",
        "|------|------|--------|------|------|------|---------|------|",
    ]
    for r in kept:
        verdict = r.value.verdict if r.value else "—"
        if r.force_included:
            verdict += "(force)"
        score = r.value.score_summary if r.value else "—"
        out.append(
            f"| `{r.src_path.name}` | {r.title[:36]} | {r.kb_name} "
            f"| {r.prefixed_category} | {r.page_count} | {r.parse_quality} "
            f"| {verdict} | {score} |"
        )
    out.append("")
    return out


def _render_review_table(review: list[LocalDocRecord]) -> list[str]:
    out = [
        "## ❓ 待人工确认（review）",
        "",
        "> 这些文件**未归档、未写入知识库索引**。",
        "> 看完判断依据后，如确认想入库，把对应 hash 前 8 位拷贝出来，下次加 "
        "`--force-include-hash <hash8>,<hash8>...` 重跑即可（届时会重新归档并写摘要卡）。",
        "",
        "| hash8 | 文件 | 标题 | 建议 KB/分类 | 得分(decay/ai/time/rel) | 判断依据 |",
        "|-------|------|------|--------------|--------------------------|----------|",
    ]
    for r in review:
        v = r.value
        hash8 = r.file_hash[:8]
        scores = f"{v.topic_decay}/{v.ai_displacement}/{v.timelessness}/{v.personal_relevance}" if v else "—"
        reason = (v.reason.replace("|", "\\|") if v else "").strip()
        out.append(
            f"| `{hash8}` | `{r.src_path.name}` | {r.title[:28]} "
            f"| {r.kb_name}/{r.prefixed_category} | {scores} | {reason[:80]} |"
        )
    out.append("")
    return out


def _render_low_value_table(low: list[LocalDocRecord]) -> list[str]:
    out = [
        "## ❌ 低价值跳过（low-value）",
        "",
        "> 原件**未归档**（低价值文件不复制到 Archive）。如误判可通过 `--force-include-hash` 重跑，届时会重新归档并入库。",
        "",
        "| hash8 | 文件 | 标题 | 得分(decay/ai/time/rel) | 判断依据 |",
        "|-------|------|------|--------------------------|----------|",
    ]
    for r in low:
        v = r.value
        hash8 = r.file_hash[:8]
        scores = f"{v.topic_decay}/{v.ai_displacement}/{v.timelessness}/{v.personal_relevance}" if v else "—"
        reason = (v.reason.replace("|", "\\|") if v else "").strip()
        out.append(
            f"| `{hash8}` | `{r.src_path.name}` | {r.title[:28]} "
            f"| {scores} | {reason[:80]} |"
        )
    out.append("")
    return out


# ── 索引重建 ─────────────────────────────────────────────────────────────────


def _rebuild_indices(records: list[LocalDocRecord]) -> None:
    affected_keys = {r.kb_key for r in records if not r.skipped}
    for kb in ALL_KBS:
        if kb.key in affected_keys:
            try:
                rebuild_index(kb)
            except Exception as exc:
                print(f"[警告] 重建索引失败 {kb.name}：{exc}", file=sys.stderr)


# ── 最终汇总 ─────────────────────────────────────────────────────────────────


def _scan_source_dir(source: Path) -> Counter:
    """递归扫描源目录，统计所有文件（含不支持格式）的后缀分布。"""
    counts: Counter = Counter()
    for p in source.rglob("*"):
        if p.is_file():
            counts[p.suffix.lower()] += 1
    return counts


def _print_final_summary(
    *,
    records: list[LocalDocRecord],
    source_path: Path | None,
    single_file: Path | None,
    doc_files: list[Path],
    allowed_exts: set[str] | None,
    source_dir_counts: Counter | None,
    report_path: Path,
    value_check: bool,
) -> None:
    """
    运行结束后打印结构化摘要（与写入报告的摘要块内容对齐），帮助用户判断：
      1. 源目录中各格式文件的全貌（含不支持格式）
      2. 本次实际处理了哪些格式/数量
      3. 处理结果（入库 / 待确认 / 低价值 / 去重）
      4. 是否还有未处理文件，是否可以安全删除源目录
    """
    SEP = "─" * 50

    kept_n = sum(
        1 for r in records
        if not r.skipped and (r.value is None or r.value.verdict == "keep" or r.force_included)
    )
    review_n = sum(
        1 for r in records
        if r.value is not None and r.value.verdict == "review" and not r.force_included
    )
    low_n = sum(
        1 for r in records
        if r.value is not None and r.value.verdict == "low-value" and not r.force_included
    )
    dup_n = sum(1 for r in records if r.skip_reason.startswith("DUP"))
    exists_n = sum(1 for r in records if r.skip_reason.startswith("SKIP"))
    low_conf_n = sum(1 for r in records if r.confidence != "高" or r.parse_quality == "low")
    scanned_exts = allowed_exts if allowed_exts is not None else _SUPPORTED_EXTENSIONS

    print(f"── 完成 ── 共处理 {len(records)} 个文档")
    print(f"报告：{report_path}")
    print()

    # ── 源目录全量统计 ────────────────────────────────────────────────────────
    if source_dir_counts is not None and source_path is not None:
        print(SEP)
        print(f"【源目录统计】{source_path}")
        total_all = sum(source_dir_counts.values())
        print(f"  目录总文件：{total_all} 个")
        for ext in sorted(source_dir_counts):
            count = source_dir_counts[ext]
            if ext in scanned_exts:
                tag = "✅ 已纳入处理"
            elif ext in _SUPPORTED_EXTENSIONS:
                tag = "⏭ 支持但被 --type 跳过"
            else:
                tag = "⚠ 格式不支持，未处理"
            print(f"    {ext or '(无后缀)':12s} × {count:4d}    {tag}")

        unhandled = sum(v for k, v in source_dir_counts.items() if k not in scanned_exts)
        if unhandled == 0:
            print("  → 所有文件格式均已纳入本次处理 ✅")
        else:
            print(f"  ⚠ 尚有 {unhandled} 个文件未处理（见上方 ⏭/⚠ 行），删除源目录前请确认！")

    elif single_file is not None:
        print(SEP)
        print(f"【单文件模式】{single_file}")

    # ── 本次处理结果 ──────────────────────────────────────────────────────────
    print(SEP)
    print("【本次处理结果】")
    print(f"  ✅ 已入库（keep / 强制入库）：{kept_n:4d} 篇")
    if value_check:
        print(f"  ❓ 待人工确认（review）  ：{review_n:4d} 篇"
              + ("  → 看报告后可 --force-include-hash 重跑" if review_n else ""))
        print(f"  ❌ 低价值（low-value）   ：{low_n:4d} 篇")
    print(f"  ⏭ 去重跳过              ：{dup_n:4d} 篇")
    if exists_n:
        print(f"  ⏭ 卡片已存在            ：{exists_n:4d} 篇")
    if low_conf_n:
        print(f"  ⚠ 低置信度/低解析质量  ：{low_conf_n:4d} 篇  → 建议逐一核查报告")
    print(SEP)


# ── 后端探测 ─────────────────────────────────────────────────────────────────


def _detect_value_backend_or_exit() -> str:
    """
    确定价值评估的 LLM 后端。

    优先级：
      1. 本机 `claude` CLI（Claude Code）—— 复用登录态，无需 API Key
      2. ANTHROPIC_API_KEY —— 回退
      两者都没有 → 打印安装建议并退出 1

    返回值：用于日志打印的后端标签。
    """
    backend_override = os.environ.get("KB_VALUE_BACKEND", "").strip().lower()

    if backend_override != "sdk" and shutil.which("claude"):
        return "claude CLI（复用 Claude Code 登录态）"

    if os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return "anthropic SDK（ANTHROPIC_API_KEY）"

    print(
        "[错误] 已开启价值评估，但：\n"
        "       - 未在 PATH 找到 `claude` CLI（推荐装 Claude Code 复用登录态，无需 API Key）\n"
        "       - 也未设置 ANTHROPIC_API_KEY\n"
        "       解决方法任选其一：\n"
        "         • 安装 Claude Code：https://claude.com/claude-code\n"
        "         • 或 export ANTHROPIC_API_KEY=sk-ant-...\n"
        "         • 或用 --no-value-check 关闭价值评估",
        file=sys.stderr,
    )
    sys.exit(1)


# ── CLI ──────────────────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="将本地文档（PDF / PPTX / DOCX）批量导入知识库（摘要卡）。"
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--source", type=Path, metavar="DIR", help="扫描目录（递归 *.pdf / *.pptx / *.docx）")
    src.add_argument("--file", type=Path, metavar="FILE", help="导入单个文件（pdf / pptx / docx）")
    parser.add_argument(
        "--type",
        type=str,
        default="",
        metavar="pdf,pptx,docx",
        help="逗号分隔的文件类型过滤（默认全部：pdf,pptx,docx）",
    )

    parser.add_argument("--dry-run", action="store_true", help="仅生成报告，不写文件")
    parser.add_argument("--overwrite", action="store_true", help="覆盖已存在的同名摘要卡")
    parser.add_argument(
        "--archive-root",
        type=Path,
        default=_DEFAULT_ARCHIVE_ROOT,
        metavar="DIR",
        help=f"原件归档根目录（默认：{_DEFAULT_ARCHIVE_ROOT}）",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        metavar="PATH",
        help="报告输出路径（默认：按来源目录名自动生成，如 local_docs_import_report_技术相关资料.md）",
    )

    # 价值评估相关
    parser.add_argument(
        "--no-value-check",
        dest="value_check",
        action="store_false",
        default=True,
        help="关闭 LLM 价值评估（默认开启；关闭后所有文档直接入库）",
    )
    parser.add_argument(
        "--value-cache",
        type=Path,
        default=_DEFAULT_VALUE_CACHE,
        metavar="PATH",
        help=f"价值评估缓存文件（默认：{_DEFAULT_VALUE_CACHE}）",
    )
    parser.add_argument(
        "--force-include-hash",
        type=str,
        default="",
        metavar="H1,H2,...",
        help="逗号分隔的 file_hash 前缀列表；命中的文档即使 verdict 非 keep 也会入库（用于上轮报告中 review 条目的二次确认）",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    source_path = args.source.expanduser().resolve() if args.source else None
    single_file = args.file.expanduser().resolve() if args.file else None

    if source_path and not source_path.exists():
        print(f"[错误] 源目录不存在：{source_path}", file=sys.stderr)
        sys.exit(1)
    if single_file and not single_file.exists():
        print(f"[错误] 文件不存在：{single_file}", file=sys.stderr)
        sys.exit(1)

    # 解析 --type 过滤参数
    allowed_exts: set[str] | None = None
    if args.type:
        raw_types = [t.strip().lower().lstrip(".") for t in args.type.split(",") if t.strip()]
        allowed_exts = {f".{t}" for t in raw_types if f".{t}" in _SUPPORTED_EXTENSIONS}
        if not allowed_exts:
            print(
                f"[错误] --type 中无有效格式（支持：{sorted(_SUPPORTED_EXTENSIONS)}）",
                file=sys.stderr,
            )
            sys.exit(1)

    doc_files = _collect_files(source_path, single_file, allowed_exts)
    if not doc_files:
        print("未发现可处理的文档（pdf / pptx / docx），退出。", file=sys.stderr)
        return

    archive_root = args.archive_root.expanduser().resolve()
    # 首次运行时在归档区生成字段速查 README（已存在不覆盖；dry-run 也生成，方便查阅）
    _ensure_local_docs_readme(archive_root)

    mode = "Dry-run（仅预览）" if args.dry_run else "执行导入"
    source_label = str(single_file) if single_file else str(source_path)

    # 文件类型分布统计（用于提示）
    ext_counts: Counter = Counter(p.suffix.lower() for p in doc_files)
    ext_summary = "  ".join(f"{ext}×{n}" for ext, n in sorted(ext_counts.items()))

    # 准备价值评估参数
    value_check: bool = args.value_check
    force_include_hash_prefixes: list[str] = [
        p.strip() for p in (args.force_include_hash or "").split(",") if p.strip()
    ]
    value_cache: ValueCache | None = None
    value_backend_label = ""
    if value_check:
        value_backend_label = _detect_value_backend_or_exit()
        value_cache_path = args.value_cache.expanduser().resolve()
        value_cache = ValueCache(value_cache_path)

    print(f"── import_local_docs ── {mode}")
    print(f"源：{source_label}")
    print(f"归档根目录：{archive_root}")
    print(f"待处理：{len(doc_files)} 个文档（{ext_summary}）")
    if value_check:
        print(f"价值评估：✅ 开启（后端：{value_backend_label}；只有 verdict=keep 才写摘要卡）")
        print("           parse_quality=low 的文件跳过 LLM，自动标为 review（原件仍归档）")
    else:
        print("价值评估：⛔ 关闭（所有文件直接入库）")
    if force_include_hash_prefixes:
        print(f"强制入库 hash 前缀：{', '.join(force_include_hash_prefixes)}")
    if value_cache is not None:
        print(f"评估缓存：{value_cache.path}")
    print()

    records = process_files(
        doc_files=doc_files,
        archive_root=archive_root,
        dry_run=args.dry_run,
        overwrite=args.overwrite,
        value_check=value_check,
        value_cache=value_cache,
        force_include_hash_prefixes=force_include_hash_prefixes,
    )

    if not records:
        print("无任何处理结果。", file=sys.stderr)
        return

    # 扫描源目录全量统计（供报告 + 终端摘要共用，避免重复 IO）
    source_dir_counts: Counter | None = None
    if source_path is not None:
        source_dir_counts = _scan_source_dir(source_path)
    scanned_exts = allowed_exts if allowed_exts is not None else _SUPPORTED_EXTENSIONS

    report = _build_report(
        records, source_label, args.dry_run, value_check,
        source_dir_counts=source_dir_counts,
        scanned_exts=scanned_exts,
    )
    report_path = (args.report or _default_report_path(args.source, args.file)).expanduser().resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")

    if not args.dry_run:
        _rebuild_indices(records)

    print()
    _print_final_summary(
        records=records,
        source_path=source_path,
        single_file=single_file,
        doc_files=doc_files,
        allowed_exts=allowed_exts,
        source_dir_counts=source_dir_counts,
        report_path=report_path,
        value_check=value_check,
    )


if __name__ == "__main__":
    main()
