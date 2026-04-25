"""
value_assessor.py — 基于 Claude 的文档价值评估。

认证（_make_default_client）：
  * 默认走本机 `claude` CLI（Claude Code），复用已登录态，**不需要 ANTHROPIC_API_KEY**
  * 找不到 `claude` CLI 时回退 anthropic Python SDK + ANTHROPIC_API_KEY
  * 调试可用 KB_VALUE_BACKEND=sdk 强制走 SDK

核心原则：
  * 默认打开，但 LLM 不确定 / API 异常 → verdict = review，不自动 skip
  * 只有 verdict=keep 才允许进入 KB 索引
  * 所有打分按 file_hash 缓存到本地 JSON，重跑不重复付费

评估四个维度（0-10，高者更好）：
  - topic_decay       : 主题是否还未过时
  - ai_displacement   : AI 时代下这件事的独特价值（10=仍需要人类深度理解；0=LLM 轻易替代）
  - timelessness      : 是否抽象出可迁移的原则（10=第一性原理；0=版本细节）
  - personal_relevance: 与使用者关注领域的契合度

verdict ∈ {"keep", "review", "low-value"}
  - keep     ：四项综合高，值得入库
  - review   ：边界/不确定，人工裁决
  - low-value：明确过时或与方向不符，跳过入库
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 取段长度：前 3500 字符足够模型判断主题与时代感
_EXCERPT_MAX_CHARS = 3500

# Haiku 足够做这类判断，速度快成本低；可被环境变量覆盖
_DEFAULT_MODEL = os.environ.get("KB_VALUE_MODEL", "claude-haiku-4-5-20251001")


# ── 数据结构 ─────────────────────────────────────────────────────────────────


@dataclass
class ValueAssessment:
    """单篇文档的价值评估结果。"""

    topic_decay: int                  # 0-10
    ai_displacement: int              # 0-10
    timelessness: int                 # 0-10
    personal_relevance: int           # 0-10
    verdict: str                      # keep | review | low-value
    reason: str                       # 中文 1-2 句
    model: str = _DEFAULT_MODEL
    # 元数据：便于调试
    error: str = ""                   # 非空表示 API/解析异常
    cached: bool = False

    @property
    def score_summary(self) -> str:
        return (
            f"decay={self.topic_decay} ai={self.ai_displacement} "
            f"time={self.timelessness} rel={self.personal_relevance}"
        )


# ── Prompt 构造 ──────────────────────────────────────────────────────────────


def _build_kb_focus_description() -> str:
    """从 common.kb_config 读取使用者关注领域，动态拼进 Prompt。"""
    try:
        from common.kb_config import ALL_KBS  # type: ignore

        parts = []
        for kb in ALL_KBS:
            cats = "、".join(
                c for c in kb.category_order if c != "未分类"
            )
            parts.append(f"- {kb.name}：{cats}")
        return "\n".join(parts) if parts else "- 暂无"
    except Exception as exc:
        logger.debug("读取 KB 配置失败，使用兜底描述：%s", exc)
        return "- AI 与组织变革\n- 工程架构\n- 管理\n- 产品"


_SYSTEM_PROMPT_TEMPLATE = """\
你是一个帮助筛选知识库资料的评估助手。使用者希望在 AI 时代维护一个\
高质量个人知识库，只有真正有长期价值的资料才应该入库。

今天的日期：{today}

使用者关注的方向：
{kb_focus}

对用户提供的文档，请按以下 4 个维度各打 0-10 分（整数），分数越高越好：

1. topic_decay：主题本身是否未过时。10=基础原理/长期有效；0=涉及已淘汰的技术或方法论。
2. ai_displacement：AI 时代下是否仍有独特价值。10=LLM 难以替代、需要人类深度理解；\
0=AI 可以直接取代这份资料的作用。
3. timelessness：是否抽象出可迁移的原则，而非版本/框架细节。\
10=第一性原理/思维框架；0=特定版本的操作手册。
4. personal_relevance：与使用者关注方向的契合度。10=核心相关；0=完全无关。

综合给出 verdict：
- "keep"：明确值得入库（一般 4 项分数较均衡且至少 2 项 ≥ 7）
- "review"：边界/不确定，建议人工二次确认
- "low-value"：明显过时、已被 AI 替代或与方向不符，不建议入库

reason：用一两句中文说明判断依据，先说结论后说理由。

**严格要求**：只返回一个 JSON 对象，不要任何前后说明、不要 Markdown 代码块。\
JSON 格式示例：
{{"topic_decay": 8, "ai_displacement": 7, "timelessness": 9, "personal_relevance": 8, \
"verdict": "keep", "reason": "...."}}
"""


def _build_system_prompt() -> str:
    return _SYSTEM_PROMPT_TEMPLATE.format(
        today=date.today().isoformat(),
        kb_focus=_build_kb_focus_description(),
    )


def _build_user_message(title: str, excerpt: str) -> str:
    excerpt = (excerpt or "").strip()
    if len(excerpt) > _EXCERPT_MAX_CHARS:
        excerpt = excerpt[:_EXCERPT_MAX_CHARS] + "\n...(后续已截断)"
    return f"文档标题：{title}\n\n文档正文节选：\n{excerpt}"


# ── 缓存 ─────────────────────────────────────────────────────────────────────


class ValueCache:
    """按 file_hash 缓存评估结果。"""

    def __init__(self, path: Path):
        self.path = path
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("价值缓存读取失败（%s），重建。%s", self.path, exc)
            self._data = {}

    def get(self, file_hash: str) -> Optional[ValueAssessment]:
        raw = self._data.get(file_hash)
        if not raw:
            return None
        try:
            return ValueAssessment(**{**raw, "cached": True})
        except TypeError:
            return None

    def put(self, file_hash: str, assessment: ValueAssessment) -> None:
        payload = asdict(assessment)
        payload["cached"] = False
        self._data[file_hash] = payload
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


# ── LLM 调用 ─────────────────────────────────────────────────────────────────


_RE_JSON_BLOCK = re.compile(r"\{[^{}]*?\"verdict\"[^{}]*?\}", re.DOTALL)


def _parse_llm_json(text: str) -> dict:
    """从模型返回里提取 JSON 对象；找不到或解析失败抛 ValueError。"""
    text = text.strip()
    # 去掉可能的 Markdown 代码围栏
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL)

    # 直接尝试整串
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 回退：正则截取第一个 {...verdict...}
    m = _RE_JSON_BLOCK.search(text)
    if not m:
        raise ValueError(f"未在模型返回中找到 JSON：{text[:200]!r}")
    return json.loads(m.group(0))


def _normalize_assessment(data: dict, model: str) -> ValueAssessment:
    """从 JSON dict 构造 ValueAssessment；做基本的字段校验与取整。"""
    def _score(key: str) -> int:
        v = data.get(key, 0)
        try:
            v = int(round(float(v)))
        except (TypeError, ValueError):
            v = 0
        return max(0, min(10, v))

    verdict = str(data.get("verdict", "")).strip().lower()
    if verdict not in {"keep", "review", "low-value"}:
        # 未知 verdict 按保守策略 → review
        verdict = "review"

    reason = str(data.get("reason", "")).strip() or "（模型未给出 reason）"

    return ValueAssessment(
        topic_decay=_score("topic_decay"),
        ai_displacement=_score("ai_displacement"),
        timelessness=_score("timelessness"),
        personal_relevance=_score("personal_relevance"),
        verdict=verdict,
        reason=reason,
        model=model,
    )


# ── 主评估函数 ────────────────────────────────────────────────────────────────


def assess_document(
    *,
    title: str,
    text: str,
    file_hash: str,
    cache: Optional[ValueCache] = None,
    model: str = _DEFAULT_MODEL,
    client=None,  # 允许外部传入 mock 客户端用于测试
) -> ValueAssessment:
    """
    对单篇文档做价值评估。

    行为：
      1. 先查缓存（按 file_hash），命中直接返回
      2. 未命中时调用 Claude API
      3. 任何异常（缺 Key / 网络 / 解析） → 返回 verdict=review 的兜底结果
      4. 成功后写缓存
    """
    if cache is not None:
        cached = cache.get(file_hash)
        if cached is not None:
            return cached

    try:
        if client is None:
            client = _make_default_client()
        raw_text = _call_claude(client, title=title, text=text, model=model)
        data = _parse_llm_json(raw_text)
        assessment = _normalize_assessment(data, model=model)
    except Exception as exc:
        logger.warning("价值评估失败（%s），视作 review。", exc)
        assessment = ValueAssessment(
            topic_decay=0,
            ai_displacement=0,
            timelessness=0,
            personal_relevance=0,
            verdict="review",
            reason=f"(评估失败，已降级为 review) {exc}",
            model=model,
            error=str(exc),
        )

    if cache is not None and not assessment.error:
        cache.put(file_hash, assessment)
    return assessment


def _make_default_client():
    """
    选择 Claude 客户端，优先顺序：

      1. 本机 `claude` CLI（Claude Code）—— 复用已登录态，**无需 ANTHROPIC_API_KEY**
         （推荐。Claude Pro / Max 订阅直接用，调用计入订阅配额）
      2. 若 PATH 上找不到 `claude`，回退到 anthropic Python SDK + ANTHROPIC_API_KEY
      3. 两者都没有 → 抛错指引安装 Claude Code

    关闭评估：--no-value-check（此函数不会被调用）。
    强制使用 API：KB_VALUE_BACKEND=sdk （调试用途）
    """
    backend = os.environ.get("KB_VALUE_BACKEND", "").strip().lower()

    if backend != "sdk" and shutil.which("claude"):
        return ClaudeCLIClient()

    try:
        import anthropic  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "未找到 `claude` CLI，且未安装 anthropic SDK。\n"
            "推荐：安装 Claude Code（https://claude.com/claude-code），"
            "无需 API Key 即可复用登录态。\n"
            "或：pip install anthropic && export ANTHROPIC_API_KEY=..."
        ) from exc

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "未找到 `claude` CLI 且未设置 ANTHROPIC_API_KEY。\n"
            "推荐装 Claude Code 复用登录态；或设置 ANTHROPIC_API_KEY。"
        )
    return anthropic.Anthropic(api_key=api_key)


# ── `claude` CLI 适配器 ──────────────────────────────────────────────────────


class _TextBlock:
    __slots__ = ("type", "text")

    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class _ClaudeCLIResponse:
    """模拟 anthropic.Messages.create 返回对象：只需 .content = [TextBlock]。"""

    def __init__(self, text: str):
        self.content = [_TextBlock(text)]


class _ClaudeCLIMessages:
    """与 anthropic SDK 同形的 messages.create 接口，底层用 `claude -p` 子进程。"""

    # claude CLI 单次调用超时（秒）。个别文档较长时取段 3500 字，一般 <30s，给足余量。
    _TIMEOUT = 120

    def create(
        self,
        *,
        model: str,
        max_tokens: int = 400,
        temperature: float = 0,
        system: str = "",
        messages: list[dict],
        **_ignored,
    ):
        # max_tokens / temperature 在 CLI 侧无对应 flag，静默忽略（对本场景影响很小）
        user_content = messages[0]["content"] if messages else ""

        # claude CLI 没有独立的 system flag；把 system 拼进 prompt 头部，模型已被要求只返回 JSON
        full_prompt = (
            f"{system.strip()}\n\n---\n\n{user_content.strip()}"
            if system else user_content
        )

        cmd = [
            "claude",
            "-p", full_prompt,
            "--model", model,
            "--output-format", "json",
        ]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._TIMEOUT,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("未找到 `claude` CLI") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"`claude` CLI 超时 ({self._TIMEOUT}s)") from exc

        if proc.returncode != 0:
            raise RuntimeError(
                f"`claude` CLI 退出码 {proc.returncode}: {proc.stderr.strip()[:300]}"
            )

        # --output-format json 的标准载荷：{"type":"result","result":"<text>", ...}
        raw = proc.stdout.strip()
        try:
            payload = json.loads(raw)
            if isinstance(payload, dict):
                text = payload.get("result") or payload.get("content") or ""
                if not isinstance(text, str):
                    text = json.dumps(text, ensure_ascii=False)
                return _ClaudeCLIResponse(text)
        except json.JSONDecodeError:
            pass

        # 兜底：当作纯文本
        return _ClaudeCLIResponse(raw)


class ClaudeCLIClient:
    """让上层 `client.messages.create(...)` 调用能走到本机 `claude` CLI。"""

    def __init__(self):
        self.messages = _ClaudeCLIMessages()


def _call_claude(client, *, title: str, text: str, model: str) -> str:
    """调用 Claude Messages API（或 CLI 适配器），返回模型输出的纯文本。"""
    system = _build_system_prompt()
    user = _build_user_message(title, text)

    response = client.messages.create(
        model=model,
        max_tokens=400,
        temperature=0,
        system=system,
        messages=[{"role": "user", "content": user}],
    )

    # response.content 是 list[TextBlock]
    parts = []
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", ""))
    return "\n".join(parts).strip()
