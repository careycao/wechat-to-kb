"""使用 yt-dlp 拉取元数据与字幕（不下载视频流）。各平台共用。"""

from __future__ import annotations

import logging
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from transcript import parse_subtitle_file
from video_types import VideoRecord

logger = logging.getLogger(__name__)

try:
    import yt_dlp
except ImportError:
    yt_dlp = None  # type: ignore

_LANG_ORDER = ["zh-hans", "zh-cn", "zh-hant", "zh-tw", "zh", "en"]


def _score_sub_path(p: Path) -> tuple[int, int]:
    name = p.name.lower()
    for i, lang in enumerate(_LANG_ORDER):
        if lang in name.replace("_", "-"):
            return (0, i)
    return (1, 99)


def _pick_best_subtitle(files: list[Path]) -> Path | None:
    if not files:
        return None
    return sorted(files, key=_score_sub_path)[0]


def _guess_transcript_source(info: dict, sub_path: Path | None) -> str:
    if not sub_path:
        return "description_only"
    name_lower = sub_path.name.lower()
    if ".auto." in name_lower:
        return "subtitle_auto"
    subs = info.get("subtitles") or {}
    autos = info.get("automatic_captions") or {}
    stem = sub_path.stem.lower()
    for key in list(subs.keys()) + list(autos.keys()):
        if key.lower().replace("-", "")[:2] in stem.replace("-", ""):
            if key in subs and key not in autos:
                return "subtitle_cc"
            if key in autos and key not in subs:
                return "subtitle_auto"
    if subs and not autos:
        return "subtitle_cc"
    if autos and not subs:
        return "subtitle_auto"
    return "subtitle_auto"


def _ytdlp_base_opts(cookiefile: str | None) -> dict:
    o: dict = {}
    if cookiefile:
        o["cookiefile"] = cookiefile
    return o


def _fallback_extract_info(url: str, cookiefile: str | None = None) -> dict | None:
    """仅元数据，不写字幕文件；部分站点在「无可用视频流」时仍返回标题与简介。"""
    if yt_dlp is None:
        return None
    minimal: dict = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "ignoreerrors": False,
        **_ytdlp_base_opts(cookiefile),
    }
    try:
        with yt_dlp.YoutubeDL(minimal) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as e:
        logger.debug("fallback extract_info 失败: %s", e)
        return None


def _build_record(
    info: dict,
    url: str,
    platform: str,
    tmpdir: Path | None,
) -> VideoRecord | None:
    if not info:
        return None

    vid = info.get("id") or "unknown"
    title = (info.get("title") or "无标题").strip()
    canonical = (info.get("webpage_url") or info.get("url") or url).strip()
    duration = info.get("duration")
    if duration is not None:
        try:
            duration = int(duration)
        except (TypeError, ValueError):
            duration = None

    description = (info.get("description") or "").strip()
    uploader = (info.get("uploader") or info.get("channel") or "").strip()

    sub_files: list[Path] = []
    if tmpdir is not None:
        sub_files = (
            list(tmpdir.glob("*.srt"))
            + list(tmpdir.glob("*.vtt"))
            + list(tmpdir.glob("*.ass"))
        )
        if not sub_files:
            sub_files = list(tmpdir.glob(f"*{vid}*.srt")) + list(tmpdir.glob(f"*{vid}*.vtt"))

    best_sub = _pick_best_subtitle(sub_files)
    sub_text = ""
    if best_sub and best_sub.exists():
        try:
            sub_text = parse_subtitle_file(
                best_sub, keep_line_timestamps=True
            ).strip()
        except Exception as e:
            logger.warning("解析字幕文件失败: %s", e)

    transcript_source = _guess_transcript_source(info, best_sub if sub_text else None)

    parts: list[str] = []
    if sub_text:
        parts.append(sub_text)
    if description:
        if parts:
            parts.append("\n---\n【简介】\n" + description)
        else:
            parts.append(description)
            transcript_source = "description_only"

    if not parts:
        parts.append("（未能获取简介或字幕，请仅依赖标题与外链）")
        transcript_source = "description_only"

    body = "\n".join(parts).strip()

    extra: dict = {
        "extractor": info.get("extractor") or "",
        "id": vid,
    }
    if uploader:
        extra["uploader"] = uploader
    if info.get("view_count") is not None:
        extra["view_count"] = info.get("view_count")

    return VideoRecord(
        canonical_url=canonical,
        platform=platform,
        title=title,
        duration_sec=duration,
        body_text=body,
        transcript_source=transcript_source,
        extra=extra,
    )


def fetch_via_ytdlp(
    url: str,
    platform: str,
    cookiefile: str | None = None,
) -> VideoRecord | None:
    if yt_dlp is None:
        logger.error("未安装 yt-dlp，请执行: pip install -r requirements.txt")
        return None

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        outtmpl = str(tmpdir / "%(id)s")
        opts: dict = {
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": ["zh-Hans", "zh-CN", "zh-Hant", "zh", "en", "all"],
            "outtmpl": outtmpl,
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": False,
            "noplaylist": True,
            **_ytdlp_base_opts(cookiefile),
        }
        info: dict | None = None
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
        except Exception as e:
            logger.warning("yt-dlp 字幕/元数据拉取失败，尝试仅元数据: %s", e)
            info = _fallback_extract_info(url, cookiefile=cookiefile)
            if info:
                return _build_record(info, url, platform, None)
            logger.error("yt-dlp 无法解析该 URL")
            return None

        if not info:
            return None

        return _build_record(info, url, platform, tmpdir)
