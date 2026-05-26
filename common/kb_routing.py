"""
kb_routing.py — 多知识库智能路由。
"""

from __future__ import annotations

import logging
import os
import sys

try:
    import jieba

    HAS_JIEBA = True
except ImportError:
    HAS_JIEBA = False

from common.kb_config import ALL_KBS, KB_BY_KEY, KBConfig

CONFLICT_THRESHOLD = 1
_LLM_MODEL = os.environ.get("KB_ROUTE_MODEL", "claude-haiku-4-5-20251001")
_EXCERPT_MAX_CHARS = 800

logger = logging.getLogger(__name__)

_ROUTE_KB_SYSTEM = """\
你是一个知识库分流助手。给定文章标题和节选，从以下知识库中选出最合适的一个，只返回知识库的 key，不要任何解释。

知识库说明：
- ai：所有与 AI/LLM 直接相关的内容，包括 AI 工具、AI 编程（Cursor/Claude Code/TRAE 等）、AI 实践案例、AI 战略、AI 研究论文、提示词工程、Agent、RAG 等。只要文章的核心主题是"AI"，就选 ai。
- engineering：纯软件工程内容，如系统架构、后端/前端开发、数据库、DevOps、工程效能——但不含 AI 辅助编程（那属于 ai）。
- management：团队管理、组织架构、OKR/KPI、招聘晋升、领导力等——但不含 AI 驱动的组织变革（那属于 ai）。
- pm：产品管理、用户研究、需求文档、数据分析、运营增长。
- life：健康运动、理财财务、家庭育儿等生活实用类。
- wisdom：哲学思维、传统文化、历史人文、经济学、读书感悟等人文类——与 AI 无关的纯思考类内容。

判断要点：只要文章核心提及 AI/LLM/大模型/AI 工具/AI 编程，优先选 ai，不要选 wisdom 或 management。\
"""

_ROUTE_CATEGORY_SYSTEM = """\
你是一个知识库内容分类助手。给定文章标题和节选，从提供的分类列表中选出最合适的一个。

规则：
- 只返回分类的原始名称（不含数字编号前缀），不要任何解释
- 如果文章核心在于介绍/对比/教程/选型某工具或方法，选"工具与方法"
- 如果文章核心在于某企业/团队的落地经验、结果、复盘，选"实践与案例"
- 如果文章核心在于战略思考、组织架构、AI 转型顶层设计，选"战略与框架"
- 如果文章核心在于 AI 辅助编程、IDE 工具链、代码生成，选"AI Coding"
- 如果文章核心在于模型发布、论文、评测、架构研究，选"前沿与研究"\
"""


def load_purpose(kb: KBConfig) -> str:
    """Return the content of purpose.md for this KB, or empty string if absent."""
    purpose_path = kb.path / "purpose.md"
    try:
        if purpose_path.exists():
            return purpose_path.read_text(encoding="utf-8").strip()
    except Exception as exc:
        import logging
        logging.getLogger(__name__).debug("无法读取 purpose.md (%s): %s", purpose_path, exc)
    return ""


def _title_bonus(title: str, keywords: list[str]) -> int:
    if not title:
        return 0

    bonus = 0
    for keyword in keywords:
        if keyword and keyword in title:
            bonus += 3
    return min(bonus, 10)


def score_kb(text: str, title: str, kb: KBConfig) -> tuple[int, str]:
    if not HAS_JIEBA:
        return 0, "未分类"

    title = (title or "").strip()
    body = (text or "").strip()

    # Boost scoring context with purpose.md when available
    purpose = load_purpose(kb)
    scored_text = f"{title}\n{title}\n{title}\n{body}"
    if purpose:
        scored_text = scored_text + "\n" + purpose
    words = set(jieba.lcut(scored_text))

    best_score = 0
    best_category = "未分类"
    for category, keywords in kb.category_keywords.items():
        if category == "未分类" or not keywords:
            continue
        score = sum(1 for keyword in keywords if keyword in scored_text or keyword in words)
        score += _title_bonus(title, keywords)
        if score > best_score:
            best_score = score
            best_category = category

    return best_score, best_category


def route(text: str, title: str) -> tuple[str | None, str | None, dict]:
    scores: dict[str, tuple[int, str]] = {}
    for kb in ALL_KBS:
        score, category = score_kb(text, title, kb)
        scores[kb.key] = (score, category)

    ranked = sorted(scores.items(), key=lambda item: item[1][0], reverse=True)
    best_key, (best_score, best_category) = ranked[0]
    _, (second_score, _) = ranked[1]

    if best_score - second_score > CONFLICT_THRESHOLD:
        return best_key, best_category, scores
    return None, None, scores


def pick_highest_score_route(scores: dict[str, tuple[int, str]]) -> tuple[str, str]:
    ranked = sorted(scores.items(), key=lambda item: item[1][0], reverse=True)
    kb_key, (_score, category) = ranked[0]
    return kb_key, category


def prompt_user_choice(title: str, scores: dict[str, tuple[int, str]]) -> tuple[str, str]:
    ranked = sorted(scores.items(), key=lambda item: item[1][0], reverse=True)

    print("\n" + "=" * 60)
    print("⚠  路由置信度低，请手动选择知识库")
    print(f"   标题：{title[:60]}")
    print()

    kb_options: list[tuple[str, str]] = []  # (kb_key, suggested_category)
    for index, (kb_key, (score, category)) in enumerate(ranked, 1):
        kb = KB_BY_KEY[kb_key]
        prefixed = kb.raw_to_prefixed.get(category, category)
        print(f"  [{index}] {kb.name}  (推荐分类: {prefixed}, 得分 {score})")
        kb_options.append((kb_key, category))

    print("  [c] 自定义（手动输入 KB 和分类）")
    print("  [q] 跳过，不保存此文章")
    print("=" * 60)

    while True:
        try:
            raw = input("请选择知识库 [1/2/3/c/q]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n已跳过。")
            sys.exit(0)

        if raw == "q":
            print("已跳过。")
            sys.exit(0)

        if raw == "c":
            return _prompt_custom()

        if raw.isdigit():
            index = int(raw) - 1
            if 0 <= index < len(kb_options):
                kb_key, suggested_category = kb_options[index]
                return _prompt_category(kb_key, suggested_category)

        print("  无效输入，请重试。")


def _prompt_category(kb_key: str, suggested_category: str) -> tuple[str, str]:
    """第二步：在已选 KB 内选择具体分类，推荐分类高亮显示。"""
    kb = KB_BY_KEY[kb_key]
    valid_categories = [c for c in kb.category_order if c != "未分类"]

    print(f"\n知识库：{kb.name}  — 请选择分类：")
    for i, category in enumerate(valid_categories, 1):
        prefixed = kb.raw_to_prefixed.get(category, category)
        marker = "  ← 推荐" if category == suggested_category else ""
        print(f"  [{i}] {prefixed}{marker}")
    print("=" * 60)

    while True:
        try:
            raw = input(f"请选择分类 [1-{len(valid_categories)}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已跳过。")
            sys.exit(0)

        if raw.isdigit():
            index = int(raw) - 1
            if 0 <= index < len(valid_categories):
                category = valid_categories[index]
                prefixed = kb.raw_to_prefixed.get(category, category)
                print(f"✓ 已选择：{kb.name} / {prefixed}")
                return kb_key, category

        print("  无效输入，请重试。")


def llm_route_kb(
    text: str,
    title: str,
    client=None,
    model: str = _LLM_MODEL,
) -> str | None:
    """用 LLM 选择最合适的知识库（KB 层级路由）。

    返回有效的 kb_key（如 "ai"、"engineering"），失败时返回 None（降级回关键词匹配）。
    通过环境变量 KB_ROUTE_LLM=1 启用。
    """
    if os.environ.get("KB_ROUTE_LLM", "").strip() != "1":
        return None

    try:
        from common.kb_enricher import _extract_text_from_response, _make_client
        if client is None:
            client = _make_client()
    except Exception as exc:
        logger.debug("LLM KB 路由：客户端初始化失败: %s", exc)
        return None

    excerpt = (text or "").strip()[:_EXCERPT_MAX_CHARS]
    user_content = f"文章标题：{title}\n\n文章节选：\n{excerpt}"

    try:
        response = client.messages.create(
            model=model,
            system=_ROUTE_KB_SYSTEM,
            messages=[{"role": "user", "content": user_content}],
        )
        result = _extract_text_from_response(response).strip().lower()

        if result in KB_BY_KEY:
            logger.debug("LLM KB 路由命中: %r", result)
            return result

        # 宽松匹配（模型可能返回带引号或空格）
        for key in KB_BY_KEY:
            if key in result:
                logger.debug("LLM KB 路由宽松命中: %r → %r", result, key)
                return key

        logger.debug("LLM KB 路由返回无效值: %r，降级回关键词匹配", result)
        return None
    except Exception as exc:
        logger.debug("LLM KB 路由调用失败: %s，降级回关键词匹配", exc)
        return None


def llm_route_category(
    text: str,
    title: str,
    kb: KBConfig,
    client=None,
    model: str = _LLM_MODEL,
) -> str | None:
    """用 LLM 在已确定的 KB 内选择最合适的子分类。

    返回有效的原始分类名（如 "实践与案例"），失败时返回 None（降级回关键词匹配）。
    通过环境变量 KB_ROUTE_LLM=1 启用。
    """
    if os.environ.get("KB_ROUTE_LLM", "").strip() != "1":
        return None

    try:
        from common.kb_enricher import _extract_text_from_response, _make_client
        if client is None:
            client = _make_client()
    except Exception as exc:
        logger.debug("LLM 子分类路由：客户端初始化失败: %s", exc)
        return None

    valid_categories = [c for c in kb.category_order if c != "未分类"]
    categories_block = "\n".join(
        f"- {c}：{', '.join(kb.category_keywords.get(c, [])[:6])}"
        for c in valid_categories
    )
    excerpt = (text or "").strip()[:_EXCERPT_MAX_CHARS]
    user_content = (
        f"知识库定位：{kb.description}\n\n"
        f"可用分类：\n{categories_block}\n\n"
        f"文章标题：{title}\n\n"
        f"文章节选：\n{excerpt}"
    )

    try:
        response = client.messages.create(
            model=model,
            system=_ROUTE_CATEGORY_SYSTEM,
            messages=[{"role": "user", "content": user_content}],
        )
        result = _extract_text_from_response(response).strip()

        # 精确匹配
        if result in kb.category_keywords:
            logger.debug("LLM 子分类路由命中: %r", result)
            return result

        # 宽松匹配：去掉数字前缀后比较
        for c in valid_categories:
            if c in result:
                logger.debug("LLM 子分类路由宽松命中: %r → %r", result, c)
                return c

        logger.debug("LLM 子分类路由返回无效值: %r，降级回关键词匹配", result)
        return None
    except Exception as exc:
        logger.debug("LLM 子分类路由调用失败: %s，降级回关键词匹配", exc)
        return None


def _prompt_custom() -> tuple[str, str]:
    print("\n可用 KB：" + " / ".join(f"{kb.key}({kb.name})" for kb in ALL_KBS))
    while True:
        kb_key = input("KB key: ").strip().lower()
        if kb_key in KB_BY_KEY:
            break
        print("  无效 KB key，请重输。")

    kb = KB_BY_KEY[kb_key]
    valid_categories = [category for category in kb.category_order if category != "未分类"]
    print("可用分类：" + " / ".join(valid_categories))
    while True:
        category = input("分类名: ").strip()
        if category in kb.category_keywords:
            return kb_key, category
        print("  无效分类名，请重输。")
