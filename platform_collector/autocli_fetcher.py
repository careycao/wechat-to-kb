#!/usr/bin/env python3
"""
autocli_fetcher.py — 封装 autocli CLI，抓取各平台公开热榜/行情内容。

设计原则：
- 与 xhs_fetcher.py 对齐输出接口（返回 list[dict]），入库侧复用现有逻辑
- autocli 二进制不在 PATH 时给出明确错误提示和安装指引
- 支持 JSON / 纯文本两种 autocli 输出，自动降级
- 热榜类输出聚合为单条「快照」入库，而非每条目单独存

用法（调试）：
  python autocli_fetcher.py zhihu_hot
  python autocli_fetcher.py hackernews_hot --limit 15
  python autocli_fetcher.py yahoo_finance --symbol AAPL
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import ssl
import subprocess
import sys
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 直连 HTTP API（公开数据，无需 Chrome 扩展）
# autocli 连不上时自动 fallback 到此处
# ---------------------------------------------------------------------------

def _open_url(url: str, headers: dict, timeout: int = 12):
    """urlopen with SSL fallback（代理环境下 TLS 握手可能失败，降级到不验证证书）。"""
    req = urllib.request.Request(url, headers=headers)
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except ssl.SSLError:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return urllib.request.urlopen(req, timeout=timeout, context=ctx)
    except urllib.error.URLError as e:
        # EOF / reset 类错误也尝试降级
        if "EOF" in str(e) or "UNEXPECTED" in str(e):
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return urllib.request.urlopen(req, timeout=timeout, context=ctx)
        raise


def _fetch_zhihu_hot(limit: int = 20) -> list[dict]:
    """知乎热榜——多接口依次尝试，带 SSL 降级，无需登录。"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.zhihu.com/",
    }

    # (source_tag, url, parser_key)
    endpoints = [
        ("vvhan",          "https://api.vvhan.com/api/hotlist/zhihu",                      "vvhan"),
        ("imsyy",          "https://hot.imsyy.top/api/zhihu",                              "imsyy"),
        ("oick",           "http://api.oick.cn/zhihu/api.php",                             "oick"),
        ("tenapi",         "https://tenapi.cn/v2/zhihu",                                   "tenapi"),
        ("zhihu-official", "https://www.zhihu.com/api/v3/feed/topstory/hot-lists/total"
                           "?limit=50&desktop=true",                                        "official"),
    ]

    for source, url, parser in endpoints:
        try:
            with _open_url(url, headers, timeout=12) as resp:
                data = json.loads(resp.read())
            items = []

            if parser in ("vvhan", "imsyy", "oick"):
                # {"success": true, "data": [{title, url, hot/desc}, ...]}
                rows = data.get("data") or data.get("list") or []
                for entry in rows[:limit]:
                    title = (entry.get("title") or entry.get("name") or "").strip()
                    link  = (entry.get("url")   or entry.get("link") or "").strip()
                    hot   = str(entry.get("hot") or entry.get("desc") or entry.get("index") or "")
                    if title:
                        items.append({"title": title, "url": link, "description": hot})

            elif parser == "tenapi":
                # {"code": 200, "data": [{title, url, hot}, ...]}
                for entry in (data.get("data") or [])[:limit]:
                    title = (entry.get("title") or "").strip()
                    link  = (entry.get("url")   or "").strip()
                    hot   = str(entry.get("hot") or "")
                    if title:
                        items.append({"title": title, "url": link, "description": hot})

            else:  # official
                for entry in (data.get("data") or [])[:limit]:
                    target = entry.get("target", {})
                    title  = target.get("title", "").strip()
                    tid    = target.get("id", "")
                    ttype  = target.get("type", "")
                    link   = f"https://www.zhihu.com/question/{tid}" if ttype == "question" else ""
                    heat   = entry.get("detail_text", "")
                    if title:
                        items.append({"title": title, "url": link, "description": heat})

            if items:
                logger.info("知乎热榜 via %s: %d 条", source, len(items))
                return items

        except Exception as e:
            logger.warning("知乎热榜 [%s] 失败: %s", source, e)
            continue

    return []


def _fetch_hackernews_hot(limit: int = 20) -> list[dict]:
    """HackerNews Top Stories——Firebase 公开 API。"""
    _hn_headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    try:
        # 拉 top story id 列表
        with _open_url(
            "https://hacker-news.firebaseio.com/v0/topstories.json", _hn_headers, timeout=10
        ) as resp:
            ids = json.loads(resp.read())[:limit]

        items = []
        for sid in ids:
            try:
                with _open_url(
                    f"https://hacker-news.firebaseio.com/v0/item/{sid}.json", _hn_headers, timeout=8
                ) as resp:
                    item = json.loads(resp.read())
                title = (item.get("title") or "").strip()
                url_  = item.get("url") or f"https://news.ycombinator.com/item?id={sid}"
                score = item.get("score", 0)
                comments = item.get("descendants", 0)
                if title:
                    items.append({
                        "title": title,
                        "url":   url_,
                        "description": f"score: {score} · comments: {comments}",
                        "score": score,
                        "comments": comments,
                    })
            except Exception:
                continue
        return items
    except Exception as e:
        logger.warning("HackerNews API 直连失败: %s", e)
        return []


def _fetch_github_trending(limit: int = 20) -> list[dict]:
    """GitHub 本周 Star 增长最快的仓库——GitHub Search API，无需 token。"""
    from datetime import datetime, timedelta
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    url = (
        f"https://api.github.com/search/repositories"
        f"?q=created:%3E{week_ago}&sort=stars&order=desc&per_page={min(limit, 30)}"
    )
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        with _open_url(url, headers, timeout=15) as resp:
            data = json.loads(resp.read())
        items = []
        for repo in (data.get("items") or [])[:limit]:
            name  = repo.get("full_name", "")
            desc  = (repo.get("description") or "").strip()
            stars = repo.get("stargazers_count", 0)
            lang  = repo.get("language") or ""
            link  = repo.get("html_url", "")
            title = f"{name}（{lang}）" if lang else name
            meta  = f"⭐ {stars:,}"
            if desc:
                meta += f" · {desc[:80]}"
            if title:
                items.append({"title": title, "url": link, "description": meta, "score": stars})
        logger.info("GitHub Trending: %d 条", len(items))
        return items
    except Exception as e:
        logger.warning("GitHub Trending API 失败: %s", e)
        return []


# 直连 API 注册表：task_name → 函数
DIRECT_API: dict[str, Any] = {
    "zhihu_hot":       _fetch_zhihu_hot,
    "hackernews_hot":  _fetch_hackernews_hot,
    "github_trending": _fetch_github_trending,
}

# ---------------------------------------------------------------------------
# 平台任务注册表
# 格式：task_name → (autocli_args, source_label, kb_hint)
#   autocli_args : list[str]，传给 autocli 的参数（不含 --format json）
#   source_label : 显示用的来源名称
#   kb_hint      : 建议路由的 KB key（"ai" / "life" / ""=自动路由）
# ---------------------------------------------------------------------------

TASK_REGISTRY: dict[str, dict] = {
    # 技术热榜
    "zhihu_hot": {
        "args":   ["zhihu", "hot"],
        "label":  "知乎热榜",
        "kb":     "ai",
    },
    "github_trending": {
        "args":   ["gh", "trending"],
        "label":  "GitHub Trending",
        "kb":     "ai",
    },
    "hackernews_hot": {
        "args":   ["hackernews", "show"],
        "label":  "HackerNews Top",
        "kb":     "ai",
    },
    "reddit_ml": {
        "args":   ["reddit", "hot", "--subreddit", "MachineLearning"],
        "label":  "Reddit r/MachineLearning",
        "kb":     "ai",
    },
    "reddit_programming": {
        "args":   ["reddit", "hot", "--subreddit", "programming"],
        "label":  "Reddit r/programming",
        "kb":     "ai",
    },
    # 财经行情（供 ai_cfo agent 使用）
    "yahoo_finance": {
        "args":   ["yahoo-finance", "trending"],
        "label":  "Yahoo Finance 热门股",
        "kb":     "life",
    },
    "bilibili_hot": {
        "args":   ["bilibili", "hot"],
        "label":  "B站热榜",
        "kb":     "ai",
    },
}


# ---------------------------------------------------------------------------
# autocli 二进制查找
# ---------------------------------------------------------------------------

def find_autocli() -> str | None:
    """查找 autocli 可执行文件路径，优先 PATH，其次常见安装位置。"""
    if path := shutil.which("autocli"):
        return path
    candidates = [
        Path.home() / ".autocli" / "bin" / "autocli",
        Path.home() / ".local" / "bin" / "autocli",
        Path("/usr/local/bin/autocli"),
        Path("/opt/homebrew/bin/autocli"),
    ]
    for p in candidates:
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    return None


def _autocli_install_hint() -> str:
    return (
        "autocli 未找到。安装方式：\n"
        "  1. 访问 https://autocli.ai 下载对应平台二进制\n"
        "  2. 放入 PATH（如 /usr/local/bin/autocli）\n"
        "  3. 同时安装 autocli Chrome 扩展（供 session 复用）"
    )


# ---------------------------------------------------------------------------
# 调用 autocli
# ---------------------------------------------------------------------------

def _run_autocli(args: list[str], timeout: int = 30) -> tuple[bool, str]:
    """
    执行 autocli 命令，返回 (success, output_text)。
    先尝试 --format json，失败时 fallback 到默认文本输出。
    """
    binary = find_autocli()
    if not binary:
        return False, _autocli_install_hint()

    for fmt in (["--format", "json"], []):
        cmd = [binary] + args + fmt
        logger.debug("执行: %s", " ".join(cmd))
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            stdout = result.stdout.strip()
            if result.returncode == 0 and stdout:
                return True, stdout
            if result.returncode != 0:
                err = result.stderr.strip() or stdout
                logger.warning("autocli 返回非零退出码 %d: %s", result.returncode, err[:200])
                if fmt:  # JSON 模式失败，尝试文本模式
                    continue
                return False, f"autocli 失败（exit {result.returncode}）: {err[:300]}"
        except subprocess.TimeoutExpired:
            return False, f"autocli 超时（{timeout}s）：{' '.join(args)}"
        except Exception as e:
            return False, f"autocli 执行异常: {e}"

    return False, "autocli 无有效输出"


# ---------------------------------------------------------------------------
# 输出解析
# ---------------------------------------------------------------------------

def _parse_items(raw: str) -> list[dict[str, Any]]:
    """
    解析 autocli 输出。优先 JSON，降级为按行解析文本。
    返回 list[dict]，每个 dict 至少有 title 字段。
    """
    # 尝试 JSON
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [_normalize_item(item) for item in data if item]
        if isinstance(data, dict):
            # 部分命令返回 {"items": [...]} 或 {"results": [...]}
            for key in ("items", "results", "data", "posts", "stories"):
                if isinstance(data.get(key), list):
                    return [_normalize_item(i) for i in data[key] if i]
            # 单条目
            return [_normalize_item(data)]
    except (json.JSONDecodeError, ValueError):
        pass

    # 降级：按行解析文本（Markdown 表格 / 带序号列表）
    items = []
    for i, line in enumerate(raw.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#") or set(line) <= set("|-: "):
            continue
        # 去掉 Markdown 表格分隔符和行首序号
        line = line.lstrip("|").rstrip("|").strip()
        if line.startswith(("0", "1", "2", "3", "4", "5", "6", "7", "8", "9")):
            # "1. 标题" 或 "1 标题"
            parts = line.split(None, 1)
            title = parts[1].strip() if len(parts) > 1 else line
        else:
            title = line
        if title:
            items.append({"title": title, "url": "", "description": ""})
    return items


def _normalize_item(raw: Any) -> dict[str, Any]:
    """把 autocli 返回的单条目规范化为统一结构。"""
    if not isinstance(raw, dict):
        return {"title": str(raw), "url": "", "description": ""}

    title = (
        raw.get("title")
        or raw.get("name")
        or raw.get("headline")
        or raw.get("text")
        or ""
    ).strip()

    url = (
        raw.get("url")
        or raw.get("link")
        or raw.get("href")
        or raw.get("permalink")
        or ""
    ).strip()

    description = (
        raw.get("description")
        or raw.get("excerpt")
        or raw.get("snippet")
        or raw.get("content")
        or raw.get("selftext")
        or ""
    ).strip()

    # 热度/分数等附加信息
    extras = {}
    for key in ("score", "points", "heat", "hot", "comments", "rank", "views"):
        if key in raw:
            extras[key] = raw[key]

    return {
        "title":       title,
        "url":         url,
        "description": description,
        **extras,
    }


# ---------------------------------------------------------------------------
# 快照格式化
# ---------------------------------------------------------------------------

def _format_snapshot_md(items: list[dict], label: str, task: str) -> str:
    """
    将多条热榜条目格式化为单份快照 Markdown，
    供 platform_collector.py 写入知识库。
    """
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")

    lines = [
        "---",
        f"title: {label} · {date_str}",
        f"source: {label}",
        f"task: {task}",
        f"date: {date_str}",
        f"fetched_at: {time_str}",
        f"count: {len(items)}",
        "---",
        "",
        f"# {label} · {date_str}",
        "",
        f"> 抓取时间：{date_str} {time_str}，共 {len(items)} 条",
        "",
    ]

    for i, item in enumerate(items, 1):
        title = item.get("title", "（无标题）")
        url   = item.get("url", "")
        desc  = item.get("description", "")

        # 标题行（有链接就加链接）
        if url:
            lines.append(f"## {i}. [{title}]({url})")
        else:
            lines.append(f"## {i}. {title}")

        # 附加数据（热度、分数等）
        meta_parts = []
        for key, label_str in [
            ("score", "分数"), ("points", "分"), ("heat", "热度"),
            ("hot", "热度"), ("comments", "评论"), ("views", "浏览"),
        ]:
            if item.get(key):
                meta_parts.append(f"{label_str}: {item[key]}")
        if meta_parts:
            lines.append(f"> {' · '.join(meta_parts)}")

        if desc:
            lines.append("")
            lines.append(desc[:300] + ("…" if len(desc) > 300 else ""))

        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 公开接口
# ---------------------------------------------------------------------------

def fetch_task(
    task: str,
    limit: int = 20,
    extra_args: list[str] | None = None,
) -> tuple[bool, str, str]:
    """
    执行指定 task，返回 (success, snapshot_md, label)。

    参数：
      task      : TASK_REGISTRY 中的 key，或自定义 autocli 命令（空格分隔）
      limit     : 最多条目数
      extra_args: 追加到 autocli 命令的额外参数

    返回：
      (True,  snapshot_md_str, source_label)   成功
      (False, error_message,   source_label)   失败
    """
    if task in TASK_REGISTRY:
        cfg        = TASK_REGISTRY[task]
        args       = cfg["args"] + ["--limit", str(limit)]
        label      = cfg["label"]
        kb_hint    = cfg["kb"]
    else:
        # 允许传原始命令，如 "bilibili hot --limit 10"
        args  = task.split()
        label = task
        kb_hint = ""

    if extra_args:
        args += extra_args

    # 优先走直连 API（快且无需 Chrome 扩展）
    if task in DIRECT_API:
        logger.info("[%s] 尝试直连 API...", task)
        items = DIRECT_API[task](limit)
        if items:
            logger.info("[%s] 直连 API 成功，抓取 %d 条", task, len(items))
            snapshot_md = _format_snapshot_md(items, label, task)
            return True, snapshot_md, label
        logger.warning("[%s] 直连 API 无结果，回退到 autocli...", task)

    ok, raw = _run_autocli(args)
    if not ok:
        logger.error("autocli 失败 [%s]: %s", task, raw[:200])
        return False, raw, label

    items = _parse_items(raw)
    if not items:
        return False, f"autocli 返回空结果（原始输出：{raw[:200]}）", label

    logger.info("[%s] 抓取 %d 条", label, len(items))
    snapshot_md = _format_snapshot_md(items, label, task)
    return True, snapshot_md, label


def get_kb_hint(task: str) -> str:
    """返回 task 对应的建议 KB key（供 platform_collector 路由用）。"""
    return TASK_REGISTRY.get(task, {}).get("kb", "")


def list_tasks() -> list[str]:
    """返回所有已注册的 task 名称。"""
    return list(TASK_REGISTRY.keys())


# ---------------------------------------------------------------------------
# 调试入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="autocli 平台抓取调试工具")
    parser.add_argument("task", help=f"任务名，可选: {', '.join(TASK_REGISTRY.keys())}")
    parser.add_argument("--limit", type=int, default=20, help="最多条目数")
    parser.add_argument("--list", action="store_true", help="列出所有可用任务")
    args = parser.parse_args()

    if args.list:
        print("可用任务：")
        for name, cfg in TASK_REGISTRY.items():
            print(f"  {name:25s} → {cfg['label']}")
        sys.exit(0)

    ok, content, label = fetch_task(args.task, limit=args.limit)
    if ok:
        print(f"\n{'='*60}")
        print(f"任务: {label}")
        print(f"{'='*60}")
        print(content)
    else:
        print(f"[ERROR] {content}", file=sys.stderr)
        sys.exit(1)
