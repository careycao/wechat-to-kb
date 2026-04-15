from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from html import escape
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

COMMENT_API_URL = "https://mp.weixin.qq.com/mp/appmsg_comment"


@dataclass(frozen=True)
class CommentContext:
    biz: str
    appmsgid: str
    idx: str
    comment_id: str


def _first_query_value(url: str, key: str) -> str:
    query = parse_qs(urlparse(url).query)
    values = query.get(key, [])
    return values[0].strip() if values else ""


def _match_first(text: str, patterns: list[str]) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def extract_comment_context(page_html: str, article_url: str) -> CommentContext | None:
    biz = _first_query_value(article_url, "__biz") or _match_first(
        page_html,
        [
            r"""var\s+biz\s*=\s*["']([^"']+)["']""",
            r"""(?:var|window\.)\s*__biz\s*=\s*["']([^"']+)["']""",
            r"""["']biz["']\s*:\s*["']([^"']+)["']""",
            r"""["']__biz["']\s*:\s*["']([^"']+)["']""",
        ],
    )
    appmsgid = _match_first(
        page_html,
        [
            r"""var\s+mid\s*=\s*["']([^"']+)["']""",
            r"""(?:var|window\.)\s*appmsgid\s*=\s*["']([^"']+)["']""",
            r"""["']appmsgid["']\s*:\s*["']([^"']+)["']""",
        ],
    ) or _first_query_value(article_url, "appmsgid") or _first_query_value(article_url, "mid")
    idx = _match_first(
        page_html,
        [
            r"""var\s+idx\s*=\s*["']([^"']+)["']""",
            r"""(?:var|window\.)\s*idx\s*=\s*["']([^"']+)["']""",
            r"""["']idx["']\s*:\s*["']([^"']+)["']""",
        ],
    ) or _first_query_value(article_url, "idx")
    comment_id = _match_first(
        page_html,
        [
            r"""(?:var|window\.)\s*comment_id\s*=\s*["']([^"']+)["']""",
            r"""["']comment_id["']\s*:\s*["']([^"']+)["']""",
            r"""comment_id\s*:\s*JsDecode\(['"]([^'"]+)['"]\)""",
        ],
    )
    if not all([biz, appmsgid, idx, comment_id]):
        return None
    return CommentContext(biz=biz, appmsgid=appmsgid, idx=idx, comment_id=comment_id)


def build_comments_api_url(context: CommentContext, offset: int = 0, limit: int = 100) -> str:
    params = urlencode(
        {
            "action": "getcomment",
            "scene": 0,
            "__biz": context.biz,
            "appmsgid": context.appmsgid,
            "idx": context.idx,
            "comment_id": context.comment_id,
            "offset": max(offset, 0),
            "limit": max(1, min(limit, 100)),
        }
    )
    return f"{COMMENT_API_URL}?{params}"


def _extract_json_blob(raw_payload: str) -> dict[str, Any]:
    text = raw_payload.strip()
    if text.startswith("{") and text.endswith("}"):
        return json.loads(text)
    match = re.search(r"(\{[\s\S]*\})\s*;?\s*$", text)
    if not match:
        raise ValueError("Unable to locate JSON payload in response")
    return json.loads(match.group(1))


def _format_created_at(value: Any) -> str | None:
    if value in (None, "", 0):
        return None
    try:
        return datetime.fromtimestamp(int(value)).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return None


def _extract_reply_content(comment: dict[str, Any]) -> str | None:
    reply = comment.get("reply")
    if isinstance(reply, dict):
        content = (reply.get("content") or "").strip()
        if content:
            return content
    reply_list = comment.get("reply_list")
    if isinstance(reply_list, list):
        for item in reply_list:
            if isinstance(item, dict):
                content = (item.get("content") or "").strip()
                if content:
                    return content
    reply_content = (comment.get("reply_content") or "").strip()
    return reply_content or None


def _normalize_comment_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "author": (item.get("nick_name") or item.get("nickName") or "匿名用户").strip(),
                "content": (item.get("content") or "").strip(),
                "created_at": _format_created_at(item.get("create_time")),
                "likes": int(item.get("like_num") or item.get("likeNum") or 0),
                "reply_content": _extract_reply_content(item),
            }
        )
    return normalized


def normalize_comment_payload(raw_payload: dict[str, Any] | str) -> dict[str, Any]:
    payload = raw_payload if isinstance(raw_payload, dict) else _extract_json_blob(raw_payload)
    selected_items = payload.get("elected_comment") or payload.get("elected_comment_list") or []
    regular_items = payload.get("comment") or payload.get("comment_list") or []
    selected = _normalize_comment_items(selected_items)
    regular = _normalize_comment_items(regular_items)
    return {
        "selected_count": int(payload.get("elected_comment_total_cnt") or len(selected)),
        "total_count": int(payload.get("comment_total_cnt") or len(selected) + len(regular)),
        "selected": selected,
        "regular": regular,
    }


def _render_comment_lines(title: str, comments: list[dict[str, Any]]) -> list[str]:
    lines = [title]
    for index, comment in enumerate(comments, start=1):
        author = comment["author"] or "匿名用户"
        header = f"{index}. {author}"
        if comment.get("created_at"):
            header += f" | {comment['created_at']}"
        if comment.get("likes"):
            header += f" | 赞 {comment['likes']}"
        lines.append(header)
        lines.append(comment["content"] or "(空留言)")
        if comment.get("reply_content"):
            lines.append(f"作者回复：{comment['reply_content']}")
        lines.append("")
    return lines


def render_comments_text(normalized: dict[str, Any]) -> str:
    if not normalized["selected"] and not normalized["regular"]:
        return ""
    lines = ["【评论区】", f"评论总数：{normalized['total_count']}", ""]
    if normalized["selected"]:
        lines.extend(_render_comment_lines(f"精选留言（{len(normalized['selected'])}条）", normalized["selected"]))
    if normalized["regular"]:
        lines.extend(_render_comment_lines(f"普通留言（{len(normalized['regular'])}条）", normalized["regular"]))
    return "\n".join(line for line in lines).strip()


def render_comments_html(normalized: dict[str, Any]) -> str:
    if not normalized["selected"] and not normalized["regular"]:
        return ""
    parts = [
        '<section class="wechat-comments">',
        "<h2>评论区</h2>",
        f"<p>评论总数：{normalized['total_count']}</p>",
    ]
    for heading, comments in (("精选留言", normalized["selected"]), ("普通留言", normalized["regular"])):
        if not comments:
            continue
        parts.append(f"<h3>{escape(heading)}（{len(comments)}条）</h3>")
        parts.append("<ol>")
        for comment in comments:
            meta = [escape(comment["author"] or "匿名用户")]
            if comment.get("created_at"):
                meta.append(escape(comment["created_at"]))
            if comment.get("likes"):
                meta.append(f"赞 {comment['likes']}")
            parts.append("<li>")
            parts.append(f"<p><strong>{' | '.join(meta)}</strong></p>")
            parts.append(f"<p>{escape(comment['content'] or '(空留言)')}</p>")
            if comment.get("reply_content"):
                parts.append(f"<p>作者回复：{escape(comment['reply_content'])}</p>")
            parts.append("</li>")
        parts.append("</ol>")
    parts.append("</section>")
    return "".join(parts)


async def fetch_comments_for_page(
    page: Any,
    article_url: str,
    page_html: str,
    limit: int = 100,
) -> dict[str, Any] | None:
    context = extract_comment_context(page_html, article_url)
    if not context:
        return None
    api_url = build_comments_api_url(context, offset=0, limit=limit)
    raw_payload = await page.evaluate(
        """
        async ({ api_url, referer }) => {
            const response = await fetch(api_url, {
                credentials: "include",
                headers: {
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": referer,
                },
            });
            const text = await response.text();
            try {
                return JSON.parse(text);
            } catch (error) {
                return text;
            }
        }
        """,
        {"api_url": api_url, "referer": article_url},
    )
    return normalize_comment_payload(raw_payload)
