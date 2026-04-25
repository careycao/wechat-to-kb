"""
MCP Server 功能验证脚本（不依赖 MCP Inspector）
直接调用 server.py 里的工具函数，验证核心逻辑是否正常。

运行方式：
  .venv/bin/python test_mcp_tools.py
"""

import asyncio
import io
import os
import sys
from pathlib import Path
from contextlib import redirect_stdout, redirect_stderr

sys.path.insert(0, str(Path(__file__).parent))

# ── 颜色输出 ──────────────────────────────────────────────────────────────────
def ok(msg): print(f"  ✅ {msg}")
def fail(msg): print(f"  ❌ {msg}")
def section(msg): print(f"\n{'─'*50}\n🧪 {msg}\n{'─'*50}")

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

def run_and_capture(func, *args, **kwargs) -> tuple[str, str]:
    """捕获 stdout/stderr，避免终端被 Playwright/Chrome 噪声刷屏导致截断。"""
    out = io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(out):
            result = func(*args, **kwargs)
        return out.getvalue(), result
    except Exception as e:
        return out.getvalue(), f"__EXCEPTION__:{e}"


# ── Test 1: list_knowledge_bases ──────────────────────────────────────────────
section("list_knowledge_bases")
try:
    from mcp_server.server import list_knowledge_bases
    result = list_knowledge_bases()
    print(result)
    ok("list_knowledge_bases 返回正常")
except Exception as e:
    fail(f"list_knowledge_bases 失败：{e}")


# ── Test 2: save_url — 无效 kb key ────────────────────────────────────────────
section("save_url — 无效 kb key 应返回错误提示")
try:
    result = asyncio.run(
        __import__("mcp_server.server", fromlist=["save_url"]).save_url(
            url="https://mp.weixin.qq.com/s/test",
            kb="invalid_kb_key"
        )
    )
    if "未知知识库" in result:
        ok(f"正确返回错误提示：{result}")
    else:
        fail(f"预期错误提示，实际返回：{result}")
except Exception as e:
    fail(f"异常：{e}")


# ── Test 3: save_url — 真实公众号文章（轻量 HTTP 模式）────────────────────────
section("save_url — 真实公众号文章（请确保网络可访问）")
TEST_URL = "https://mp.weixin.qq.com/s/8QyX6wngPAl0mvNVNPUmBw"  # 智码探路示例文章
print(f"  URL: {TEST_URL}")
print('  （首次保存约 3-5 秒，已存在则显示"已存在，跳过"）\n')
try:
    from mcp_server.server import save_url
    # 单独把 Test 3 的完整输出落盘，避免终端输出过长被系统截断
    log_path = LOG_DIR / "test3_save_url_full_output.txt"
    result = asyncio.run(save_url(url=TEST_URL))
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(result + "\n")
    print(result)
    print(f"\n  [完整输出已写入] {log_path}")
    if "失败" not in result and "错误" not in result and "Traceback" not in result:
        ok("save_url 执行完成")
    else:
        fail(f"save_url 返回失败信息：{result}")
except Exception as e:
    fail(f"save_url 异常：{e}")


# ── Test 4: save_urls_batch — 空列表 ──────────────────────────────────────────
section("save_urls_batch — 空列表应返回提示")
try:
    from mcp_server.server import save_urls_batch
    result = asyncio.run(save_urls_batch(urls=[]))
    if "为空" in result:
        ok(f"正确处理空列表：{result}")
    else:
        fail(f"预期空列表提示，实际：{result}")
except Exception as e:
    fail(f"异常：{e}")


# ── Test 5: rebuild_index — 无效 kb key ───────────────────────────────────────
section("rebuild_index — 无效 kb key 应返回错误提示")
try:
    from mcp_server.server import rebuild_index
    result = rebuild_index(kb="bad_key")
    if "未知知识库" in result:
        ok(f"正确返回错误提示：{result}")
    else:
        fail(f"预期错误提示，实际：{result}")
except Exception as e:
    fail(f"异常：{e}")


# ── Test 6: import_local_file — 文件不存在 ────────────────────────────────────
section("import_local_file — 文件不存在应返回错误提示")
try:
    from mcp_server.server import import_local_file
    result = import_local_file(path="/tmp/not_exist.pdf")
    if "不存在" in result:
        ok(f"正确返回错误提示：{result}")
    else:
        fail(f"预期错误提示，实际：{result}")
except Exception as e:
    fail(f"异常：{e}")


print("\n" + "="*50)
print("测试完成。重点看 Test 3（save_url）的实际保存结果。")
print("="*50)
