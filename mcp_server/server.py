"""
mcp_server/server.py — wechat-to-kb MCP Server

把现有采集能力包装为标准 MCP 工具，支持 Claude Desktop、Cursor、Cowork 等
任何 MCP 兼容客户端直接调用，无需了解内部实现。

工具列表：
  save_url          — 保存单篇内容（公众号 / 网页 / 视频，自动分流）
  save_urls_batch   — 批量保存 URL 列表
  import_local_file — 导入本地 PDF / PPTX / DOCX
  list_knowledge_bases — 列出所有知识库和分类
  rebuild_index     — 重建知识库索引

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
        "把微信公众号文章、网页、视频、本地文档统一保存到本地知识库。\n"
        "使用 list_knowledge_bases 查看可用的知识库和分类，\n"
        "再用 save_url 保存内容。"
    ),
)


# ─────────────────────────────────────────────────────────────────────────────
# 工具 1：save_url
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
           可用 key 可通过 list_knowledge_bases 查询。

    返回：保存结果（标题、知识库、分类、关键词），或失败原因。
    """
    from unified_collector import run_urls
    from common.kb_config import KB_BY_KEY

    kb_hint = kb.strip() or None
    if kb_hint and kb_hint not in KB_BY_KEY:
        available = ", ".join(KB_BY_KEY.keys())
        return f"未知知识库 key：{kb_hint}\n可用 key：{available}"

    buf = io.StringIO()
    try:
        with redirect_stdout(buf), redirect_stderr(buf):
            await run_urls(
                urls=[url],
                kb_hint=kb_hint,
                skip_existing=True,
                auto_route=True,
                include_comments=False,
            )
        output = buf.getvalue().strip()
        if not output:
            return "已保存（内容已存在或无输出）"
        # 如果底层有异常栈/错误字样，不要伪装成成功
        lowered = output.lower()
        if "traceback" in lowered or "error" in lowered or "exception" in lowered:
            return f"保存失败（详见日志）：\n{output}"
        return output
    except Exception as exc:
        return f"保存失败：{exc}"


# ─────────────────────────────────────────────────────────────────────────────
# 工具 2：save_urls_batch
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool()
async def save_urls_batch(urls: list[str], kb: str = "") -> str:
    """
    批量保存多个 URL 到本地知识库。

    参数：
      urls  URL 列表，每个元素为一个链接
      kb    可选。强制指定目标知识库 key，不填则自动路由。

    返回：逐条保存结果汇总。
    """
    from unified_collector import run_urls
    from common.kb_config import KB_BY_KEY

    if not urls:
        return "URL 列表为空，未执行任何操作。"

    kb_hint = kb.strip() or None
    if kb_hint and kb_hint not in KB_BY_KEY:
        available = ", ".join(KB_BY_KEY.keys())
        return f"未知知识库 key：{kb_hint}\n可用 key：{available}"

    buf = io.StringIO()
    try:
        with redirect_stdout(buf), redirect_stderr(buf):
            await run_urls(
                urls=urls,
                kb_hint=kb_hint,
                skip_existing=True,
                auto_route=True,
                include_comments=False,
            )
        output = buf.getvalue().strip()
        if not output:
            return f"批量处理完成（共 {len(urls)} 条）"
        lowered = output.lower()
        if "traceback" in lowered or "error" in lowered or "exception" in lowered:
            return f"批量保存失败（详见日志）：\n{output}"
        return output
    except Exception as exc:
        return f"批量保存失败：{exc}"


# ─────────────────────────────────────────────────────────────────────────────
# 工具 3：import_local_file
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool()
def import_local_file(
    path: str,
    kb: str = "",
    skip_value_check: bool = False,
) -> str:
    """
    导入本地文档到知识库，支持 PDF、PPTX、DOCX 格式。

    默认开启 Claude 价值评估（四维打分：时效性、AI可替代性、常青程度、个人相关性），
    只有 verdict=keep 的文档才入库；verdict=review 原件归档等待人工确认；
    verdict=low-value 只归档不入索引。

    参数：
      path             本地文件绝对路径
      kb               可选，强制指定目标知识库 key
      skip_value_check 跳过价值评估直接入库，适合批量导入已知高价值文档

    返回：导入结果（verdict、知识库、分类）或失败原因。
    """
    import asyncio
    from pathlib import Path as _Path
    from tools.import_local_docs import process_files, _collect_files
    from tools.value_assessor import ValueCache
    from common.kb_config import KB_BY_KEY, ALL_KBS

    src = _Path(path).expanduser()
    if not src.exists():
        return f"文件不存在：{path}"
    if src.suffix.lower() not in {".pdf", ".pptx", ".docx"}:
        return f"不支持的格式：{src.suffix}（仅支持 PDF / PPTX / DOCX）"

    kb_hint = kb.strip() or None
    if kb_hint and kb_hint not in KB_BY_KEY:
        available = ", ".join(KB_BY_KEY.keys())
        return f"未知知识库 key：{kb_hint}\n可用 key：{available}"

    from tools.import_local_docs import _resolve_kb_root, _DEFAULT_VALUE_CACHE
    archive_root = _resolve_kb_root() / "Archive" / "LocalDocs" / "imported"
    value_cache = ValueCache(_DEFAULT_VALUE_CACHE) if not skip_value_check else None

    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            records = process_files(
                doc_files=[src],
                archive_root=archive_root,
                dry_run=False,
                overwrite=False,
                value_check=not skip_value_check,
                value_cache=value_cache,
                force_include_hash_prefixes=[],
            )
        if not records:
            return f"处理完成，但未产生记录（可能已存在或解析失败）：{src.name}"

        record = records[0]
        verdict = getattr(record, "verdict", "unknown")
        kb_key = getattr(record, "kb_key", "")
        category = getattr(record, "category", "")

        verdict_zh = {
            "keep": "✅ 已入库",
            "review": "⚠️ 待确认（归档但未入索引）",
            "low-value": "❌ 低价值（仅归档）",
        }.get(verdict, verdict)

        lines = [f"导入完成：{src.name}", f"• 评估结果：{verdict_zh}"]
        if kb_key:
            kb_obj = KB_BY_KEY.get(kb_key)
            lines.append(f"• 知识库：{kb_obj.name if kb_obj else kb_key}")
        if category:
            lines.append(f"• 分类：{category}")

        output = buf.getvalue().strip()
        if output:
            lines.append(f"\n详细输出：\n{output}")
        return "\n".join(lines)
    except Exception as exc:
        return f"导入失败：{exc}"


# ─────────────────────────────────────────────────────────────────────────────
# 工具 4：list_knowledge_bases
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool()
def list_knowledge_bases() -> str:
    """
    列出所有已配置的知识库，包括名称、key、分类列表和本地路径。
    在使用 save_url 或 import_local_file 指定 kb 参数前，可先调用本工具确认可用 key。
    """
    from common.kb_config import ALL_KBS

    if not ALL_KBS:
        return "未找到任何知识库配置。请确认 KB_ROOT 环境变量或 kb_config.local.py 是否正确设置。"

    lines = [f"已配置的知识库（{len(ALL_KBS)} 个）：\n"]
    for i, kb in enumerate(ALL_KBS, 1):
        categories = [c for c in kb.category_order if c != "未分类"]
        lines.append(f"{i}. {kb.name}（key: {kb.key}）")
        lines.append(f"   路径：{kb.path}")
        lines.append(f"   分类：{' / '.join(categories)}")
        lines.append("")

    return "\n".join(lines).strip()


# ─────────────────────────────────────────────────────────────────────────────
# 工具 5：rebuild_index
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool()
def rebuild_index(kb: str = "") -> str:
    """
    重建知识库的 README.md 索引文件（文章标题、摘要、关键词汇总）。

    参数：
      kb  可选。指定单个知识库 key 只重建该库的索引；不填则重建所有知识库。

    通常在批量导入后调用，或感觉索引与文件内容不一致时使用。
    """
    from common.kb_config import ALL_KBS, KB_BY_KEY
    from common.kb_indexing import rebuild_index as _rebuild

    targets = []
    if kb:
        kb_hint = kb.strip()
        if kb_hint not in KB_BY_KEY:
            available = ", ".join(KB_BY_KEY.keys())
            return f"未知知识库 key：{kb_hint}\n可用 key：{available}"
        targets = [KB_BY_KEY[kb_hint]]
    else:
        targets = list(ALL_KBS)

    results = []
    for kb_obj in targets:
        try:
            _rebuild(kb_obj)
            results.append(f"✅ {kb_obj.name}（{kb_obj.key}）")
        except Exception as exc:
            results.append(f"❌ {kb_obj.name}（{kb_obj.key}）：{exc}")

    return "索引重建完成：\n" + "\n".join(results)


# ─────────────────────────────────────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
