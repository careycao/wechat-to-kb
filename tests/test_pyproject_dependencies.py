try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # Python 3.10 回退到第三方 tomli
    import tomli as tomllib
from pathlib import Path


PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _dependency_names(dependencies: list[str]) -> set[str]:
    names = set()
    for dependency in dependencies:
        normalized = dependency.split("[", 1)[0]
        for separator in (">=", "==", "<", ">", "~=", "!="):
            normalized = normalized.split(separator, 1)[0]
        names.add(normalized.strip().lower())
    return names


def test_mcp_extra_contains_only_daily_mcp_dependencies():
    data = tomllib.loads(PYPROJECT.read_text())

    project_dependencies = _dependency_names(data["project"].get("dependencies", []))
    assert project_dependencies == set()

    mcp_dependencies = _dependency_names(data["project"]["optional-dependencies"]["mcp"])
    assert {
        "mcp",
        "playwright",
        "requests",
        "beautifulsoup4",
        "markdownify",
        "jieba",
        "yt-dlp",
    }.issubset(mcp_dependencies)

    assert "feedparser" not in mcp_dependencies
    assert "anthropic" not in mcp_dependencies
    assert "lxml" not in mcp_dependencies
    assert "pyyaml" not in mcp_dependencies
    assert "aiofiles" not in mcp_dependencies
    assert "aiohttp" not in mcp_dependencies
