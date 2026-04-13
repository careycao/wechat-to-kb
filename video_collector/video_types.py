"""视频入库统一数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class VideoRecord:
    canonical_url: str
    platform: str
    title: str
    duration_sec: int | None
    body_text: str
    transcript_source: str
    extra: dict = field(default_factory=dict)


_SOURCE_LABEL = {
    "subtitle_cc": "人工/投稿字幕或官方 CC",
    "subtitle_auto": "自动字幕",
    "description_only": "仅简介/文案（无可用字幕）",
    "asr": "语音识别（P1，未启用）",
}


def format_plain(record: VideoRecord) -> str:
    """生成写入 KB 的纯文本（含固定头）。"""
    lines = [
        "【来源类型】视频",
        f"【平台】{record.platform}",
        f"【转写来源】{_SOURCE_LABEL.get(record.transcript_source, record.transcript_source)}",
    ]
    if record.duration_sec is not None:
        lines.append(f"【时长】{record.duration_sec} 秒")
    lines.append(f"【链接】{record.canonical_url}")
    lines.append("---")
    lines.append(record.body_text.strip())
    return "\n".join(lines)


def html_fragment_for_kb(title: str, plain: str, url: str) -> str:
    """供 KBWriter.save_stage：仅 <article> 内片段（外层由 kb_builder 包装）。"""
    import html as html_module

    esc_title = html_module.escape(title)
    body = html_module.escape(plain).replace("\n", "<br>\n")
    return (
        f"<h1>{esc_title}</h1>"
        f"<div class=\"video-kb\">{body}</div>"
        f'<p><a href="{html_module.escape(url)}">原文链接</a></p>'
    )
