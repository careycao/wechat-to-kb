"""Shared text processing helpers for collectors."""

from __future__ import annotations

import re

SAFE_TITLE_CHARS = re.compile(r'[\\/:*?"<>|]')


def safe_filename(title: str, max_len: int = 80) -> str:
    sanitized = SAFE_TITLE_CHARS.sub("_", title).strip()
    return (sanitized[:max_len] or "untitled").strip("_")


def extract_summary(txt: str, max_sentences: int = 2, max_len: int = 120) -> str:
    if not txt or not txt.strip():
        return ""

    raw = txt.replace("\n", " ").strip()
    sentences = re.split(r"[。！？]\s*", raw)
    sentences = [sentence.strip() for sentence in sentences if len(sentence.strip()) > 10]
    if not sentences:
        return raw[:max_len] + ("..." if len(raw) > max_len else "")

    chosen = "。".join(sentences[:max_sentences])
    if chosen and not chosen.endswith("。"):
        chosen += "。"
    return (chosen[:max_len] + "...") if len(chosen) > max_len else chosen


def extract_keywords_for_index(txt: str, top_k: int = 6) -> str:
    if not txt:
        return ""
    try:
        import jieba.analyse

        keywords = jieba.analyse.extract_tags(txt, topK=top_k)
        return ", ".join(keywords) if keywords else ""
    except Exception:
        pass

    try:
        import jieba

        words = [word for word in jieba.lcut(txt) if len(word) >= 2][:top_k]
        return ", ".join(words)
    except Exception:
        return ""
