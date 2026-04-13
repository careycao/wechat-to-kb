#!/usr/bin/env python3
"""
kb_builder.py — 多知识库采集主流程。

用法：
  python kb_builder.py <URL1> [URL2 ...]
  python kb_builder.py -f urls.txt
  python kb_builder.py --reindex --kb ai
  python kb_builder.py --no-skip "https://..."

依赖：
  cd ~/.openclaw/wechat-to-kb/kb_collector
  pip install -r requirements.txt && playwright install chromium

视频站（B 站/抖音/快手/小红书/视频号/YouTube 等）优先委托 ../video_collector（yt-dlp）；
小红书若视频管线正文过短则回退网页抓取（兼容图文笔记）。
"""

import asyncio
import logging
import os
import re
import shutil
import sys
from pathlib import Path

from kb_config import KBConfig, AI_KB, ALL_KBS, KB_BY_KEY
from kb_router import pick_highest_score_route, route, prompt_user_choice, score_kb
from page_fetcher import PageFetcher, extract_plain_text

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

SAFE_TITLE_CHARS = re.compile(r'[\\/:*?"<>|]')


def safe_filename(title: str, max_len: int = 80) -> str:
    s = SAFE_TITLE_CHARS.sub("_", title).strip()
    return (s[:max_len] or "untitled").strip("_")


def extract_summary(txt: str, max_sentences: int = 2, max_len: int = 120) -> str:
    if not txt or not txt.strip():
        return ""
    raw = txt.replace("\n", " ").strip()
    sentences = re.split(r"[。！？]\s*", raw)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 10]
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
        kws = jieba.analyse.extract_tags(txt, topK=top_k)
        return ", ".join(kws) if kws else ""
    except Exception:
        pass
    try:
        import jieba
        words = [w for w in jieba.lcut(txt) if len(w) >= 2][:top_k]
        return ", ".join(words)
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# 文件保存
# ---------------------------------------------------------------------------

class KBWriter:
    """负责将文章写入指定 KB 的分类目录。"""

    def __init__(self, kb: KBConfig):
        self.kb = kb
        self.stage_dir = kb.path / "_stage"
        self._ensure_dirs()

    def _ensure_dirs(self):
        self.kb.path.mkdir(parents=True, exist_ok=True)
        self.stage_dir.mkdir(parents=True, exist_ok=True)
        for name in self.kb.ordered_prefixed:
            (self.kb.path / name).mkdir(parents=True, exist_ok=True)

    def _stage_paths(self, title: str):
        safe = safe_filename(title)
        return self.stage_dir / f"{safe}.html", self.stage_dir / f"{safe}.txt"

    def _category_paths(self, category: str, title: str):
        safe = safe_filename(title)
        dir_name = self.kb.raw_to_prefixed.get(category, category)
        d = self.kb.path / dir_name
        return d / f"{safe}.html", d / f"{safe}.txt"

    def already_exists(self, title: str) -> bool:
        for cat in self.kb.category_order:
            h, t = self._category_paths(cat, title)
            if h.exists() or t.exists():
                return True
        return False

    def save_stage(self, data: dict) -> tuple[Path, Path]:
        title = data["title"]
        html_path, txt_path = self._stage_paths(title)
        url_comment = f"<!-- original_url: {data.get('url', '')} -->"
        full_html = (
            f'<!DOCTYPE html><html><head><meta charset="utf-8">'
            f"<title>{title}</title>{url_comment}</head>"
            f"<body><article>{data['html']}</article></body></html>"
        )
        html_path.write_text(full_html, encoding="utf-8")
        txt_path.write_text(data["plain_text"], encoding="utf-8")
        logger.info("已暂存: %s", title[:50])
        return html_path, txt_path

    def classify_and_move(self, title: str, category: str) -> str:
        html_stage, txt_path = self._stage_paths(title)
        cat_html, cat_txt = self._category_paths(category, title)

        if html_stage.exists():
            content = html_stage.read_text(encoding="utf-8", errors="replace")
            content = content.replace("_images/", "../_images/")
            cat_html.write_text(content, encoding="utf-8")
            html_stage.unlink()
        if txt_path.exists():
            shutil.move(str(txt_path), str(cat_txt))

        prefixed = self.kb.raw_to_prefixed.get(category, category)
        logger.info("已归类 [%s] -> %s", prefixed, title[:50])
        return category


# ---------------------------------------------------------------------------
# README / 索引
# ---------------------------------------------------------------------------

def _extract_original_url(html_path: Path) -> str | None:
    if not html_path.exists():
        return None
    try:
        raw = html_path.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"<!--\s*original_url:\s*(\S+)\s*-->", raw)
        return m.group(1).strip() if m else None
    except Exception:
        return None


def _folder_number(category: str) -> str:
    if len(category) >= 2 and category[:2].isdigit():
        return category[:2]
    return "00"


def collect_articles_meta(kb: KBConfig) -> list[dict]:
    rows = []
    for cat_dir in kb.path.iterdir():
        if not cat_dir.is_dir() or cat_dir.name.startswith("_"):
            continue
        for txt_path in cat_dir.glob("*.txt"):
            html_path = cat_dir / f"{txt_path.stem}.html"
            try:
                text = txt_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                text = ""
            rows.append({
                "category": cat_dir.name,
                "title": txt_path.stem,
                "html_path": html_path,
                "txt_path": txt_path,
                "summary": extract_summary(text),
                "keywords": extract_keywords_for_index(text),
                "original_url": _extract_original_url(html_path),
            })
    return rows


def generate_readme(kb: KBConfig, rows: list[dict]) -> str:
    ordered = sorted(
        rows,
        key=lambda r: (_folder_number(r["category"]), r["category"], r["title"]),
    )
    lines = [
        f"# {kb.name}",
        "",
        "## 简介",
        "",
        kb.description,
        "",
        "## 文章索引",
        "",
        "| 分类 | 编号 | 文章标题 | 核心观点简介 | 关键词 |",
        "|------|------|----------|--------------|--------|",
    ]
    for idx, r in enumerate(ordered, 1):
        code_6 = _folder_number(r["category"]) + f"{idx:04d}"
        if r.get("original_url"):
            link = f"[{r['title']}]({r['original_url']})"
        else:
            rel = r["category"] + "/" + r["html_path"].name
            link = f"[{r['title']}]({rel})"
        summary = (r["summary"] or "-")[:80]
        kws = (r["keywords"] or "-")[:60]
        lines.append(f"| {r['category']} | {code_6} | {link} | {summary} | {kws} |")
    return "\n".join(lines)


def write_url_list(kb: KBConfig, rows: list[dict]) -> None:
    ordered = sorted(
        rows,
        key=lambda r: (_folder_number(r["category"]), r["category"], r["title"]),
    )
    lines_out = []
    for idx, r in enumerate(ordered, 1):
        url = r.get("original_url") or ""
        if not url:
            continue
        folder_num = _folder_number(r["category"])
        lines_out.append(f"{folder_num}{idx:04d}\t{url}")
    path = kb.path / "des_url_list.txt"
    path.write_text(
        "\n".join(lines_out) + ("\n" if lines_out else ""), encoding="utf-8"
    )
    logger.info("已生成链接列表: %s（%d 条）", path, len(lines_out))


def rebuild_index(kb: KBConfig) -> None:
    rows = collect_articles_meta(kb)
    (kb.path / "README.md").write_text(generate_readme(kb, rows), encoding="utf-8")
    write_url_list(kb, rows)
    logger.info("索引重建完成：%s（%d 篇）", kb.name, len(rows))


def git_commit_and_push(kb_root: Path, title: str, kb_name: str, category: str) -> None:
    """
    将知识库变更提交并推送到远端。
    仅在 kb_root 是 git 仓库且配置了 remote 时执行；否则静默跳过。
    """
    import subprocess

    def run(cmd: list[str]) -> tuple[int, str]:
        r = subprocess.run(cmd, cwd=kb_root, capture_output=True, text=True)
        return r.returncode, (r.stdout + r.stderr).strip()

    # 检查是否为 git 仓库
    code, _ = run(["git", "rev-parse", "--git-dir"])
    if code != 0:
        logger.debug("knowledge_base 非 git 仓库，跳过推送")
        return

    # 检查是否有 remote
    code, remotes = run(["git", "remote"])
    if code != 0 or not remotes.strip():
        logger.debug("git 无 remote，跳过推送")
        return

    run(["git", "add", "."])
    commit_msg = f"add: 《{title[:40]}》-> {kb_name}/{category}"
    code, out = run(["git", "commit", "-m", commit_msg])
    if code != 0 and "nothing to commit" in out:
        logger.info("git: 无新变更，跳过提交")
        return
    if code != 0:
        logger.warning("git commit 失败: %s", out[:200])
        return
    logger.info("git commit: %s", commit_msg)

    code, out = run(["git", "push"])
    if code == 0:
        logger.info("git push 成功")
    else:
        logger.warning("git push 失败: %s", out[:200])


# ---------------------------------------------------------------------------
# 视频链接：委托 video_collector（与 OpenClaw / run.sh 保存链接时行为一致）
# ---------------------------------------------------------------------------

KB_ROOT = Path(__file__).resolve().parent
VIDEO_COLLECTOR_DIR = KB_ROOT.parent / "video_collector"


def _sanitize_url(url: str) -> str:
    """与 video_collector 一致，去掉终端误带的 \\? \\= 等。"""
    u = url.strip()
    for old, new in (("\\?", "?"), ("\\&", "&"), ("\\=", "="), ("\\#", "#")):
        u = u.replace(old, new)
    return u


# 优先走 yt-dlp 的站点（页面多为播放器/短视频，非文章 DOM）
_VIDEO_HOST_HINTS = (
    "bilibili.com",
    "b23.tv",
    "bilivideo.com",
    "channels.weixin.qq.com",
    "douyin.com",
    "iesdouyin.com",
    "ixigua.com",
    "kuaishou.com",
    "chenzhongtech.com",
    "xiaohongshu.com",
    "xhslink.com",
    "youtube.com",
    "youtu.be",
    "tiktok.com",
)


def _should_use_video_collector(url: str) -> bool:
    u = url.lower().strip()
    if not (u.startswith("http://") or u.startswith("https://")):
        return False
    return any(h in u for h in _VIDEO_HOST_HINTS)


def _is_ambiguous_note_host(url: str) -> bool:
    """小红书同一域名下既有图文也有视频，需根据结果决定是否回退网页。"""
    u = url.lower()
    return "xiaohongshu.com" in u or "xhslink.com" in u


def _video_plain_too_thin(data: dict) -> bool:
    """视频管线若只拿到极短文案（常见于纯图笔记），再尝试 Playwright 扒页。"""
    pt = (data.get("plain_text") or "").strip()
    if "---" in pt:
        body = pt.split("---", 1)[-1].strip()
    else:
        body = pt
    return len(body) < 120


def _fetch_via_video_collector(url: str) -> dict | None:
    """
    调用 ~/.openclaw/wechat-to-kb/video_collector：yt-dlp + cookies.txt。
    返回与 PageFetcher.fetch 相同字段；失败返回 None 以便回退网页抓取。
    """
    if not VIDEO_COLLECTOR_DIR.is_dir():
        return None
    vc = str(VIDEO_COLLECTOR_DIR)
    if vc not in sys.path:
        sys.path.insert(0, vc)
    try:
        from cookies import resolve_cookies_path
        from platforms import fetch_video
        from video_types import format_plain, html_fragment_for_kb
    except ImportError as e:
        logger.warning("video_collector 未就绪，回退网页抓取: %s", e)
        return None

    cookiefile = resolve_cookies_path(VIDEO_COLLECTOR_DIR, None)
    record = fetch_video(url, cookiefile=cookiefile)
    if not record:
        return None
    title = record.title
    plain = format_plain(record)
    return {
        "title": title,
        "plain_text": plain,
        "html": html_fragment_for_kb(title, plain, record.canonical_url),
        "url": record.canonical_url,
    }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

async def run_urls(
    urls: list[str],
    kb_hint: str | None = None,
    skip_existing: bool = True,
    auto_route: bool = False,
):
    """
    采集 URL 列表，自动路由到对应 KB。
    kb_hint: 强制指定 KB key（ai / engineering / management），跳过路由。
    auto_route: True 时路由置信度低不调用 input()，直接选得分最高的 KB（供 OpenClaw exec 等无 TTY 场景）。
    """
    writers: dict[str, KBWriter] = {kb.key: KBWriter(kb) for kb in ALL_KBS}
    state_path = Path(__file__).resolve().parent / "wechat_state.json"
    changed_kbs: set[str] = set()

    for i, url in enumerate(urls, 1):
        url = _sanitize_url(url)
        if not url or url.startswith("#"):
            continue
        logger.info("[%d/%d] 处理: %s", i, len(urls), url[:70])

        try:
            data = None
            if _should_use_video_collector(url):
                data = await asyncio.to_thread(_fetch_via_video_collector, url)
                if data:
                    logger.info("已用 video_collector 拉取视频元数据/字幕")
                if (
                    data
                    and _is_ambiguous_note_host(url)
                    and _video_plain_too_thin(data)
                ):
                    logger.info("小红书：视频管线正文过短，回退网页抓取")
                    data = None

            if not data:
                tmp_fetcher = PageFetcher(state_path=state_path)
                await tmp_fetcher.init()
                data = await tmp_fetcher.fetch(url)
                await tmp_fetcher.close()

            if not data:
                continue

            title = data["title"]
            plain_text = data["plain_text"]

            # 路由
            if kb_hint and kb_hint in writers:
                kb_key = kb_hint
                _, category = score_kb(plain_text, title, KB_BY_KEY[kb_key])
                if not category:
                    category = "未分类"
            else:
                kb_key, category, scores = route(plain_text, title)
                if kb_key is None:
                    if auto_route:
                        kb_key, category = pick_highest_score_route(scores)
                        kb = KB_BY_KEY[kb_key]
                        pref = kb.raw_to_prefixed.get(category, category)
                        logger.info(
                            "自动路由（无交互）：%s / %s",
                            kb.name,
                            pref,
                        )
                        print(
                            f"[kb_collector] 置信度低，已自动选用得分最高：{kb.name} / {pref}",
                            file=sys.stderr,
                        )
                    else:
                        kb_key, category = prompt_user_choice(title, scores)

            writer = writers[kb_key]
            kb = KB_BY_KEY[kb_key]

            if skip_existing and writer.already_exists(title):
                logger.info("已存在，跳过: %s", title[:50])
                continue

            writer.save_stage(data)
            writer.classify_and_move(title, category)
            changed_kbs.add(kb_key)

            prefixed = kb.raw_to_prefixed.get(category, category)
            keywords = extract_keywords_for_index(plain_text)
            save_path = f"~/knowledge_base/{kb.name}/{prefixed}/"
            print(f"\n已保存完成 ✅")
            print(f"文章信息：")
            print(f"• 标题： {title[:60]}")
            print(f"• 知识库： {kb.name}")
            print(f"• 分类： {prefixed}")
            print(f"• 核心关键词： {keywords}")
            print(f"已同步更新知识库索引。后续可以在 {save_path} 下找到原文 HTML/TXT 版本。\n")

        except Exception:
            logger.exception("处理出错: %s", url)

    # 重建有变动的 KB 索引
    for kb_key in changed_kbs:
        rebuild_index(KB_BY_KEY[kb_key])


def load_urls_from_file(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def _resolve_auto_route(args) -> bool:
    """无 TTY（如 OpenClaw）默认自动选 KB；可用 --interactive 强制询问。"""
    if getattr(args, "interactive", False):
        return False
    if getattr(args, "non_interactive", False):
        return True
    if os.environ.get("KB_NON_INTERACTIVE", "").lower() in ("1", "true", "yes"):
        return True
    return not sys.stdin.isatty()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="kb_collector — 多知识库采集工具")
    parser.add_argument("url", nargs="*", help="文章 URL，可传多个")
    parser.add_argument("-f", "--file", dest="url_file", default="urls.txt")
    parser.add_argument("--no-skip", action="store_true", help="不跳过已存在的文章")
    parser.add_argument(
        "--kb",
        default=None,
        choices=list(KB_BY_KEY.keys()),
        help="强制指定目标知识库：ai / engineering / management / pm（跳过路由与选库交互）",
    )
    parser.add_argument("--reindex", action="store_true", help="仅重建索引，不下载")
    parser.add_argument(
        "-n",
        "--non-interactive",
        action="store_true",
        help="路由冲突时直接选得分最高 KB（不显式需要时：无 TTY 已默认开启）",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="强制交互选择 KB（即使在无终端环境下）",
    )
    args = parser.parse_args()

    if args.reindex:
        target_kbs = [KB_BY_KEY[args.kb]] if args.kb else ALL_KBS
        for kb in target_kbs:
            rebuild_index(kb)
        sys.exit(0)

    url_file = Path(args.url_file)
    if not url_file.is_absolute():
        url_file = Path(__file__).resolve().parent / url_file

    urls = list(args.url)
    if not urls and url_file.exists():
        urls = load_urls_from_file(url_file)
        logger.info("从 %s 读取 %d 个 URL", url_file, len(urls))

    if not urls:
        print("用法: python kb_builder.py <URL> 或 -f urls.txt")
        sys.exit(1)

    auto_route = _resolve_auto_route(args)

    try:
        asyncio.run(
            run_urls(
                urls,
                kb_hint=args.kb,
                skip_existing=not args.no_skip,
                auto_route=auto_route,
            )
        )
    except KeyboardInterrupt:
        logger.info("用户中断")
        sys.exit(130)
