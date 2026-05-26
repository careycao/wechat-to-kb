"""Two-step CoT LLM enrichment for KB articles.

Enabled by environment variable KB_ENRICH=1 (default: off).

Step 1 — Analyze: article text + KB index → structured analysis JSON
Step 2 — Enrich:  analysis JSON → enhanced frontmatter (summary, concepts, related)

The related field also serves as the simplified wikilink graph (借鉴点 4).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from common.kb_config import KBConfig

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = os.environ.get("KB_ENRICH_MODEL", "claude-haiku-4-5-20251001")
_EXCERPT_MAX_CHARS = 4000

_STEP1_SYSTEM = """\
你是一个知识库内容分析助手。请分析给定的文章，输出结构化 JSON 分析结果。

分析维度：
- key_entities: 关键实体（人物/工具/项目名），最多 5 个字符串
- core_concepts: 核心概念/技术/方法论，最多 5 个字符串
- key_arguments: 文章核心论点或结论，1-2 句中文
- wiki_suggestion: 建议 "new"（新建页）或 "extend"（扩展已有），含简短理由
- related_titles: 从【现有文章列表】中选最相关的标题，最多 5 个；无则空列表

**严格要求**：只返回一个 JSON 对象，不要任何前后说明、不要 Markdown 代码块。\
"""

_STEP2_SYSTEM = """\
你是一个知识库内容增强助手。根据分析结果，为文章生成增强字段。

输出格式（JSON 对象，三个字段）：
- summary: 3 句以内的中文摘要，提炼核心观点
- concepts: 核心概念列表（字符串数组，最多 5 个）
- related: 相关文章标题列表（字符串数组，直接复制分析结果中的标题）

**严格要求**：只返回一个 JSON 对象，不要任何前后说明、不要 Markdown 代码块。\
"""

_RE_FRONTMATTER = re.compile(r"^(---\s*\n)(.*?)(---\s*\n)", re.DOTALL)
_RE_JSON_BLOCK = re.compile(r"\{.*?\}", re.DOTALL)


# ── LLM client (mirrors value_assessor.py pattern) ───────────────────────────


class _TextBlock:
    __slots__ = ("type", "text")

    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class _CLIResponse:
    def __init__(self, text: str):
        self.content = [_TextBlock(text)]


class _CLIMessages:
    _TIMEOUT = 120

    def create(self, *, model: str, system: str = "", messages: list[dict], **_ignored):
        user_content = messages[0]["content"] if messages else ""
        full_prompt = (
            f"{system.strip()}\n\n---\n\n{user_content.strip()}"
            if system else user_content
        )
        cmd = ["claude", "-p", full_prompt, "--model", model, "--output-format", "json"]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=self._TIMEOUT)
        except FileNotFoundError as exc:
            raise RuntimeError("未找到 `claude` CLI") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"`claude` CLI 超时 ({self._TIMEOUT}s)") from exc

        if proc.returncode != 0:
            raise RuntimeError(
                f"`claude` CLI 退出码 {proc.returncode}: {proc.stderr.strip()[:300]}"
            )

        raw = proc.stdout.strip()
        try:
            payload = json.loads(raw)
            if isinstance(payload, dict):
                text = payload.get("result") or payload.get("content") or ""
                if not isinstance(text, str):
                    text = json.dumps(text, ensure_ascii=False)
                return _CLIResponse(text)
        except json.JSONDecodeError:
            pass
        return _CLIResponse(raw)


class _CLIClient:
    def __init__(self):
        self.messages = _CLIMessages()


class _DeepSeekMessages:
    _BASE_URL = "https://api.deepseek.com/v1/chat/completions"
    _TIMEOUT = 60

    def __init__(self, api_key: str):
        self._api_key = api_key

    def create(self, *, model: str, system: str = "", messages: list[dict], **_ignored):
        import requests  # already in project deps

        payload_messages: list[dict] = []
        if system:
            payload_messages.append({"role": "system", "content": system})
        payload_messages.extend(messages)

        resp = requests.post(
            self._BASE_URL,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json={"model": model, "messages": payload_messages},
            timeout=self._TIMEOUT,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
        return _CLIResponse(text)


class _DeepSeekClient:
    def __init__(self, api_key: str):
        self.messages = _DeepSeekMessages(api_key)


def _make_client():
    backend = os.environ.get("KB_ENRICH_BACKEND", "").strip().lower()

    # DeepSeek 优先（只要有 key 且未强制指定其他 backend）
    deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if deepseek_key and backend not in ("sdk", "cli"):
        return _DeepSeekClient(deepseek_key)

    # Claude CLI
    if backend != "sdk" and shutil.which("claude"):
        return _CLIClient()

    # Anthropic SDK
    try:
        import anthropic  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "未找到可用 LLM 后端：需要 DEEPSEEK_API_KEY、claude CLI 或 ANTHROPIC_API_KEY 之一。"
        ) from exc
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "未找到可用 LLM 后端：需要 DEEPSEEK_API_KEY、claude CLI 或 ANTHROPIC_API_KEY 之一。"
        )
    return anthropic.Anthropic(api_key=api_key)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _extract_text_from_response(response) -> str:
    parts = []
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", ""))
    return "\n".join(parts).strip()


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = _RE_JSON_BLOCK.search(text)
    if not m:
        raise ValueError(f"未在模型返回中找到 JSON：{text[:200]!r}")
    return json.loads(m.group(0))


def _get_kb_article_titles(kb: KBConfig) -> list[str]:
    """Collect existing article titles from KB category directories."""
    titles: list[str] = []
    try:
        for category_dir in kb.path.iterdir():
            if not category_dir.is_dir() or category_dir.name.startswith("_"):
                continue
            for md_path in category_dir.glob("*.md"):
                if md_path.stem != "README":
                    titles.append(md_path.stem)
    except Exception as exc:
        logger.debug("读取 KB 文章列表失败: %s", exc)
    return titles


def _inject_enrichment(md_content: str, enrichment: dict) -> str:
    """Insert summary/concepts/related into existing YAML frontmatter."""
    summary = str(enrichment.get("summary", "")).strip()
    concepts = [str(c).strip() for c in (enrichment.get("concepts") or []) if c]
    related = [str(r).strip() for r in (enrichment.get("related") or []) if r]

    extra_lines: list[str] = []
    if summary:
        extra_lines.append(f"summary: {summary}")
    if concepts:
        concepts_yaml = "\n".join(f"  - {c}" for c in concepts)
        extra_lines.append(f"concepts:\n{concepts_yaml}")
    if related:
        related_yaml = "\n".join(f'  - "[[{r}]]"' for r in related)
        extra_lines.append(f"related:\n{related_yaml}")

    if not extra_lines:
        return md_content

    extra_block = "\n".join(extra_lines)

    m = _RE_FRONTMATTER.match(md_content)
    if m:
        # Insert extra fields before the closing ---
        return m.group(1) + m.group(2) + extra_block + "\n" + m.group(3) + md_content[m.end():]

    # No frontmatter — prepend a new one
    return f"---\n{extra_block}\n---\n\n" + md_content


def _append_wikilinks_section(md_content: str, related: list[str]) -> str:
    """Append a Related section with [[wikilink]] syntax to the body (advanced mode)."""
    if not related:
        return md_content
    links = "\n".join(f"- [[{title}]]" for title in related)
    section = f"\n\n## 相关文章\n\n{links}\n"
    return md_content.rstrip() + section


# ── Public API ────────────────────────────────────────────────────────────────


def enrich_with_llm(
    md_content: str,
    plain_text: str,
    title: str,
    kb: KBConfig,
    client=None,
    model: str = _DEFAULT_MODEL,
) -> str:
    """Run two-step CoT enrichment and return enhanced markdown content.

    Step 1: analyze the article to extract entities/concepts/related articles.
    Step 2: generate summary, concepts list, and related titles for frontmatter.
    """
    if client is None:
        client = _make_client()

    excerpt = (plain_text or "").strip()
    if len(excerpt) > _EXCERPT_MAX_CHARS:
        excerpt = excerpt[:_EXCERPT_MAX_CHARS] + "\n...(后续已截断)"

    existing_titles = _get_kb_article_titles(kb)
    titles_block = (
        "\n".join(f"- {t}" for t in existing_titles[:100])
        if existing_titles else "（暂无已有文章）"
    )

    # Step 1: analysis
    step1_user = (
        f"【现有文章列表】\n{titles_block}\n\n"
        f"【文章标题】{title}\n\n【文章节选】\n{excerpt}"
    )
    resp1 = client.messages.create(
        model=model,
        system=_STEP1_SYSTEM,
        messages=[{"role": "user", "content": step1_user}],
    )
    analysis_text = _extract_text_from_response(resp1)
    analysis = _parse_json(analysis_text)
    logger.debug("Step 1 分析完成: %s", str(analysis)[:120])

    # Step 2: enrichment
    step2_user = f"分析结果：\n{json.dumps(analysis, ensure_ascii=False)}\n\n文章标题：{title}"
    resp2 = client.messages.create(
        model=model,
        system=_STEP2_SYSTEM,
        messages=[{"role": "user", "content": step2_user}],
    )
    enrichment_text = _extract_text_from_response(resp2)
    enrichment = _parse_json(enrichment_text)
    logger.debug("Step 2 增强完成: %s", str(enrichment)[:120])

    result = _inject_enrichment(md_content, enrichment)

    # Advanced wikilinks mode: append [[title]] section when KB_WIKILINKS=1
    if os.environ.get("KB_WIKILINKS", "").strip() == "1":
        related = [str(r).strip() for r in (enrichment.get("related") or []) if r]
        result = _append_wikilinks_section(result, related)

    return result
