"""save_url 基于 run_urls 返回的结构化结果判断成败，不再靠抓 stdout 字样。"""

import asyncio
import sys
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import unified_collector  # noqa: E402
from unified_collector import SaveResult  # noqa: E402
from mcp_server import server  # noqa: E402


def _call_save(result: SaveResult | None):
    async def fake_run_urls(**kwargs):
        return [] if result is None else [result]

    with mock.patch.object(unified_collector, "run_urls", fake_run_urls):
        return asyncio.run(server.save_url("https://mp.weixin.qq.com/s/xxxxx"))


def test_saveresult_ok_property():
    assert SaveResult(url="u", status="saved").ok is True
    assert SaveResult(url="u", status="skipped").ok is True
    assert SaveResult(url="u", status="failed", error="boom").ok is False
    assert SaveResult(url="u", status="empty").ok is False


def test_save_url_success_message():
    out = _call_save(
        SaveResult(url="u", status="saved", title="标题", kb="AI_KnowBase", category="05-AI Coding", keywords="a, b")
    )
    assert "已保存完成" in out
    assert "标题" in out
    assert "AI_KnowBase" in out


def test_save_url_skipped_message():
    out = _call_save(SaveResult(url="u", status="skipped", title="旧文", kb="AI_KnowBase"))
    assert "已存在" in out


def test_save_url_failed_message_not_disguised_as_success():
    out = _call_save(SaveResult(url="u", status="failed", error="网络超时"))
    assert "保存失败" in out
    assert "网络超时" in out


def test_save_url_empty_results():
    out = _call_save(None)
    assert "无法获取内容" in out
