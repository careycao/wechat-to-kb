"""
kb_router.py — 多知识库智能路由。

流程：
  1. 对三套 KB 的关键词分别打分
  2. 最高分与第二高分差 > CONFLICT_THRESHOLD → 自动路由
  3. 差值 ≤ CONFLICT_THRESHOLD → 打印候选项，等待用户手工选择
"""

from __future__ import annotations

import re
import sys

try:
    import jieba
    import jieba.analyse
    HAS_JIEBA = True
except ImportError:
    HAS_JIEBA = False

from kb_config import KBConfig, ALL_KBS, KB_BY_KEY

# 最高分与第二高分之差 ≤ 该值时触发人工确认
CONFLICT_THRESHOLD = 1


# ---------------------------------------------------------------------------
# 单 KB 打分
# ---------------------------------------------------------------------------

def _title_bonus(title: str, keywords: list[str]) -> int:
    """标题强信号加分：标题命中关键词时额外加权。"""
    if not title:
        return 0
    bonus = 0
    for kw in keywords:
        if kw and kw in title:
            bonus += 3
    return min(bonus, 10)


def score_kb(text: str, title: str, kb: KBConfig) -> tuple[int, str]:
    """
    对单个 KB 打分，返回 (最高分, 对应分类名)。
    分类名为逻辑名（不含编号前缀）。
    """
    if not HAS_JIEBA:
        return 0, "未分类"

    title = (title or "").strip()
    body = (text or "").strip()
    scored_text = f"{title}\n{title}\n{title}\n{body}" if title else body
    words = set(jieba.lcut(scored_text))

    best_score = 0
    best_category = "未分类"

    for category, keywords in kb.category_keywords.items():
        if category == "未分类" or not keywords:
            continue
        score = sum(1 for k in keywords if k in scored_text or k in words)
        score += _title_bonus(title, keywords)
        if score > best_score:
            best_score = score
            best_category = category

    return best_score, best_category


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------

def route(text: str, title: str) -> tuple[str | None, str | None, dict]:
    """
    路由文章到最合适的 KB 与分类。

    返回值：
      (kb_key, category, scores)
        - kb_key / category 均为 None 时表示需要人工选择（调用方负责交互）
        - scores: { kb_key: (score, category) }
    """
    scores: dict[str, tuple[int, str]] = {}
    for kb in ALL_KBS:
        score, category = score_kb(text, title, kb)
        scores[kb.key] = (score, category)

    # 按分数降序排列
    ranked = sorted(scores.items(), key=lambda x: x[1][0], reverse=True)
    best_key, (best_score, best_cat) = ranked[0]
    second_key, (second_score, _) = ranked[1]

    gap = best_score - second_score
    if gap > CONFLICT_THRESHOLD:
        return best_key, best_cat, scores

    # 得分接近，返回 None 表示需要人工选择
    return None, None, scores


def pick_highest_score_route(
    scores: dict[str, tuple[int, str]],
) -> tuple[str, str]:
    """
    无交互场景（OpenClaw exec、CI）：取得分最高的 KB 与分类。
    与 route() 冲突时使用同一套 scores 即可。
    """
    ranked = sorted(scores.items(), key=lambda x: x[1][0], reverse=True)
    kb_key, (_score, category) = ranked[0]
    return kb_key, category


def prompt_user_choice(title: str, scores: dict[str, tuple[int, str]]) -> tuple[str, str]:
    """
    命令行交互：展示候选项，等待用户输入选择。
    返回 (kb_key, category)；用户选 q 则退出进程。
    """
    ranked = sorted(scores.items(), key=lambda x: x[1][0], reverse=True)

    print("\n" + "=" * 60)
    print(f"⚠  路由置信度低，请手动选择保存位置")
    print(f"   标题：{title[:60]}")
    print()

    options: list[tuple[str, str]] = []  # (kb_key, category)
    for i, (kb_key, (score, category)) in enumerate(ranked, 1):
        kb = KB_BY_KEY[kb_key]
        prefixed = kb.raw_to_prefixed.get(category, category)
        print(f"  [{i}] {kb.name} / {prefixed}  (得分 {score})")
        options.append((kb_key, category))

    print(f"  [c] 自定义（手动输入 KB 和分类）")
    print(f"  [q] 跳过，不保存此文章")
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
            idx = int(raw) - 1
            if 0 <= idx < len(options):
                kb_key, category = options[idx]
                kb = KB_BY_KEY[kb_key]
                prefixed = kb.raw_to_prefixed.get(category, category)
                print(f"✓ 已选择：{kb.name} / {prefixed}")
                return kb_key, category

        print(f"  无效输入，请重试。")


def _prompt_custom() -> tuple[str, str]:
    """引导用户手动输入 KB key 和分类名。"""
    print("\n可用 KB：" + " / ".join(f"{kb.key}({kb.name})" for kb in ALL_KBS))
    while True:
        kb_key = input("KB key: ").strip().lower()
        if kb_key in KB_BY_KEY:
            break
        print("  无效 KB key，请重输。")

    kb = KB_BY_KEY[kb_key]
    valid_cats = [c for c in kb.category_order if c != "未分类"]
    print("可用分类：" + " / ".join(valid_cats))
    while True:
        cat = input("分类名: ").strip()
        if cat in kb.category_keywords:
            return kb_key, cat
        print("  无效分类名，请重输。")
