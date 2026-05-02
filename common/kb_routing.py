"""
kb_routing.py — 多知识库智能路由。
"""

from __future__ import annotations

import sys

try:
    import jieba

    HAS_JIEBA = True
except ImportError:
    HAS_JIEBA = False

from common.kb_config import ALL_KBS, KB_BY_KEY, KBConfig

CONFLICT_THRESHOLD = 1


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
    print("⚠  路由置信度低，请手动选择保存位置")
    print(f"   标题：{title[:60]}")
    print()

    options: list[tuple[str, str]] = []
    for index, (kb_key, (score, category)) in enumerate(ranked, 1):
        kb = KB_BY_KEY[kb_key]
        prefixed = kb.raw_to_prefixed.get(category, category)
        print(f"  [{index}] {kb.name} / {prefixed}  (得分 {score})")
        options.append((kb_key, category))

    print("  [c] 自定义（手动输入 KB 和分类）")
    print("  [q] 跳过，不保存此文章")
    print("=" * 60)

    while True:
        try:
            raw = input("请选择 [1/2/3/c/q]: ").strip().lower()
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
            if 0 <= index < len(options):
                kb_key, category = options[index]
                kb = KB_BY_KEY[kb_key]
                prefixed = kb.raw_to_prefixed.get(category, category)
                print(f"✓ 已选择：{kb.name} / {prefixed}")
                return kb_key, category

        print("  无效输入，请重试。")


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
