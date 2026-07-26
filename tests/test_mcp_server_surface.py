import asyncio
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mcp_server import server  # noqa: E402


def _registered_tool_names() -> list[str]:
    tools = asyncio.run(server.mcp.list_tools())
    return sorted(tool.name for tool in tools)


def test_mcp_server_keeps_only_high_frequency_tools():
    assert callable(server.fetch_url)
    assert callable(server.save_url)
    assert _registered_tool_names() == ["fetch_url", "save_url"]

    retired_tools = [
        "save_urls_batch",
        "import_local_file",
        "list_knowledge_bases",
        "rebuild_index",
    ]
    for tool_name in retired_tools:
        assert not hasattr(server, tool_name)
