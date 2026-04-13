"""字幕文件 → 纯文本（清洗时间轴与序号，不做翻译）。

支持可选：在每条字幕前保留 [mm:ss] / [h:mm:ss]，便于对照原视频（仅 SRT/VTT；
ASS 仍走无时间轴正文；无 ASR 时间戳）。
"""

from __future__ import annotations

import re
from pathlib import Path


_TS_LINE = re.compile(
    r"^\s*\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3}\s+-->\s+\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3}\s*$"
)

# SRT / VTT 起始时间（取 --> 左侧）
_TS_START_LONG = re.compile(
    r"^(\d{1,2}):(\d{2}):(\d{2})[.,](\d{3})\s+-->"
)
_TS_START_SHORT = re.compile(
    r"^(\d{2}):(\d{2})[.,](\d{3})\s+-->"
)


def _start_time_to_seconds(line: str) -> float | None:
    line = line.strip()
    m = _TS_START_LONG.match(line)
    if m:
        h, mi, s, ms = (int(m.group(i)) for i in range(1, 5))
        return h * 3600 + mi * 60 + s + ms / 1000.0
    m = _TS_START_SHORT.match(line)
    if m:
        mi, s, ms = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return mi * 60 + s + ms / 1000.0
    return None


def _format_line_timestamp(seconds: float) -> str:
    """人类可读 [m:ss] 或 [h:mm:ss]（取字幕块起始时间的整数秒）。"""
    s = int(seconds)
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    if h > 0:
        return f"[{h}:{m:02d}:{sec:02d}]"
    return f"[{m}:{sec:02d}]"


def parse_subtitle_file(path: Path, *, keep_line_timestamps: bool = False) -> str:
    suffix = path.suffix.lower()
    raw = path.read_text(encoding="utf-8", errors="replace")
    if keep_line_timestamps:
        if suffix == ".vtt":
            return vtt_to_plain_with_timestamps(raw)
        if suffix in (".srt",):
            return srt_to_plain_with_timestamps(raw)
        if suffix == ".ass":
            return ass_to_plain(raw)
    if suffix == ".vtt":
        return vtt_to_plain(raw)
    if suffix in (".srt", ".ass"):
        if suffix == ".ass":
            return ass_to_plain(raw)
        return srt_to_plain(raw)
    return srt_to_plain(raw)


def srt_to_plain(s: str) -> str:
    lines_out: list[str] = []
    for line in s.splitlines():
        line = line.strip()
        if not line:
            continue
        if "-->" in line:
            continue
        if line.isdigit():
            continue
        lines_out.append(line)
    return "\n".join(lines_out).strip()


def srt_to_plain_with_timestamps(s: str) -> str:
    """每条字幕块第一行前加 [mm:ss]，同一块后续行不加前缀。"""
    blocks = re.split(r"\n\s*\n", s.strip())
    lines_out: list[str] = []
    for block in blocks:
        lines = [ln.rstrip() for ln in block.splitlines()]
        lines = [ln for ln in lines if ln.strip()]
        if not lines:
            continue
        i = 0
        if lines[0].strip().isdigit():
            i = 1
        if i >= len(lines):
            continue
        ts_line = lines[i]
        if "-->" not in ts_line:
            continue
        start_sec = _start_time_to_seconds(ts_line)
        if start_sec is None:
            continue
        text_lines = [ln.strip() for ln in lines[i + 1 :] if ln.strip()]
        if not text_lines:
            continue
        prefix = _format_line_timestamp(start_sec)
        lines_out.append(f"{prefix} {text_lines[0]}")
        lines_out.extend(text_lines[1:])
    return "\n".join(lines_out).strip()


def vtt_to_plain(s: str) -> str:
    lines_out: list[str] = []
    for line in s.splitlines():
        line_stripped = line.strip()
        if not line_stripped:
            continue
        if line_stripped.upper().startswith("WEBVTT"):
            continue
        if line_stripped.startswith("NOTE"):
            continue
        if _TS_LINE.match(line_stripped):
            continue
        if "-->" in line_stripped:
            continue
        lines_out.append(line_stripped)
    return "\n".join(lines_out).strip()


def vtt_to_plain_with_timestamps(s: str) -> str:
    """VTT：按空行分块，块内找含 --> 的行作为时间轴。"""
    blocks = re.split(r"\n\s*\n", s.strip())
    lines_out: list[str] = []
    for block in blocks:
        lines = [ln.rstrip() for ln in block.splitlines()]
        lines = [ln for ln in lines if ln.strip()]
        if not lines:
            continue
        if lines[0].upper().startswith("WEBVTT"):
            continue
        ts_idx = None
        for j, ln in enumerate(lines):
            if "-->" in ln and _start_time_to_seconds(ln) is not None:
                ts_idx = j
                break
        if ts_idx is None:
            continue
        start_sec = _start_time_to_seconds(lines[ts_idx])
        if start_sec is None:
            continue
        text_lines = []
        for ln in lines[ts_idx + 1 :]:
            t = ln.strip()
            if not t or t.startswith("NOTE"):
                continue
            if "-->" in t:
                break
            if _TS_LINE.match(t):
                continue
            text_lines.append(t)
        if not text_lines:
            continue
        prefix = _format_line_timestamp(start_sec)
        lines_out.append(f"{prefix} {text_lines[0]}")
        lines_out.extend(text_lines[1:])
    return "\n".join(lines_out).strip()


def ass_to_plain(s: str) -> str:
    """粗略去除 ASS 标签，保留对白文本。"""
    text = re.sub(r"\{[^}]*\}", "", s)
    text = re.sub(
        r"Dialogue:[^,]*,[^,]*,[^,]*,[^,]*,[^,]*,[^,]*,[^,]*,[^,]*,[^,]*,",
        "",
        text,
    )
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("[")]
    return "\n".join(lines).strip()
