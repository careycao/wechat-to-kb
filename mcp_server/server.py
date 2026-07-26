"""
mcp_server/server.py — wechat-to-kb MCP Server

把高频采集能力包装为标准 MCP 工具，支持 Claude Desktop、Cursor、Cowork 等
任何 MCP 兼容客户端直接调用，无需了解内部实现。

工具列表：
  fetch_url         — 读取 URL 正文并返回（不保存），用于"只是让 Claude 读一下"的场景
  save_url          — 保存单篇内容（公众号 / 网页 / 视频，自动分流）

启动方式：
  python -m mcp_server.server          # 本地开发
  uvx --from wechat-to-kb wechat-to-kb-mcp  # 无需 clone，直接运行
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

# ── 把 repo 根加入 sys.path，确保能 import 兄弟模块 ──────────────────────────
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "wechat-to-kb",
    instructions=(
        "高频 URL 读取和入库工具。\n"
        "只是想读一篇文章？用 fetch_url，不保存任何文件。\n"
        "想保存单篇内容到知识库？用 save_url。"
    ),
)


# ─────────────────────────────────────────────────────────────────────────────
# 工具 1：fetch_url
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool()
async def fetch_url(url: str, max_chars: int = 8000) -> str:
    """
    读取 URL 正文并返回，不保存到知识库。

    适合"只是想让 Claude 读一下"的场景：分析文章、提取要点、回答问题等。
    支持微信公众号文章（mp.weixin.qq.com）和普通网页。

    参数：
      url        内容链接
      max_chars  返回正文的最大字符数，默认 8000（约 4000 汉字）。设为 0 则返回全文。

    返回：「# 标题\\n\\n正文」格式的字符串，或失败原因。
    """
    url = url.strip()
    if not url:
        return "URL 不能为空"

    result: dict | None = None

    if "mp.weixin.qq.com/" in url:
        from wechat_collector.article_fetcher import _lightweight_fetch, ArticleFetcher

        result, reason = _lightweight_fetch(url)
        if not result:
            fetcher = ArticleFetcher()
            try:
                await fetcher.init()
                result = await fetcher.fetch(url)
            finally:
                await fetcher.close()
    else:
        from web_collector.page_fetcher import PageFetcher

        fetcher = PageFetcher()
        try:
            await fetcher.init()
            result = await fetcher.fetch(url)
        finally:
            await fetcher.close()

    if not result:
        return f"无法获取内容，请确认链接是否有效：{url}"

    title = result.get("title", "（无标题）")
    plain_text = result.get("plain_text", "")
    total_chars = len(plain_text)

    if max_chars and total_chars > max_chars:
        plain_text = plain_text[:max_chars] + f"\n\n…（已截断，原文共 {total_chars} 字）"

    return f"# {title}\n\n{plain_text}"


# ─────────────────────────────────────────────────────────────────────────────
# 工具 2：save_url
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool()
async def save_url(url: str, kb: str = "") -> str:
    """
    保存一篇内容到本地知识库。

    支持的内容类型（自动识别，无需指定）：
    - 微信公众号文章（mp.weixin.qq.com）— 绝大多数公开文章无需登录
    - 普通网页
    - 视频：B站、YouTube、抖音、小红书视频号、TikTok 等

    参数：
      url  内容链接
      kb   可选。强制指定目标知识库 key（如 ai / engineering / management / pm）。
           不填则根据内容自动路由到最匹配的知识库。

    返回：保存结果（标题、知识库、分类、关键词），或失败原因。
    """
    from unified_collector import run_urls
    from common.kb_config import KB_BY_KEY

    kb_hint = kb.strip() or None
    if kb_hint and kb_hint not in KB_BY_KEY:
        available = ", ".join(KB_BY_KEY.keys())
        return f"未知知识库 key：{kb_hint}\n可用 key：{available}"

    # 底层 print 的进度日志对 MCP 调用方无意义，重定向后靠返回的结构化结果判断成败
    buf = io.StringIO()
    try:
        with redirect_stdout(buf), redirect_stderr(buf):
            results = await run_urls(
                urls=[url],
                kb_hint=kb_hint,
                skip_existing=True,
                auto_route=True,
                include_comments=False,
            )
    except Exception as exc:
        return f"保存失败：{exc}"

    if not results:
        return f"无法获取内容，请确认链接是否有效：{url}"

    result = results[0]
    if result.status == "saved":
        lines = ["已保存完成 ✅", f"• 标题：{result.title}", f"• 知识库：{result.kb}", f"• 分类：{result.category}"]
        if result.keywords:
            lines.append(f"• 核心关键词：{result.keywords}")
        return "\n".join(lines)
    if result.status == "skipped":
        return f"已存在，跳过：{result.title}（{result.kb}）"
    if result.status == "empty":
        return f"无法获取内容，请确认链接是否有效：{url}"
    return f"保存失败：{result.error or '未知原因，详见日志'}"


# ─────────────────────────────────────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
