"""
kb_config.py — 统一知识库配置入口。

优先级：
1. `KB_CONFIG_FILE` 指定的文件
2. `common/kb_config.local.py`
3. 仓库内迁移兜底配置
4. 内置示例默认值
"""

from __future__ import annotations

import importlib.util
import os
import sys
from dataclasses import dataclass
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent
_KB_ROOT = Path(os.environ.get("KB_ROOT", "")).expanduser() or Path.home() / "knowledge_base"


@dataclass
class KBConfig:
    key: str
    name: str
    path: Path
    description: str
    category_keywords: dict[str, list[str]]
    category_order: list[str]

    @property
    def ordered_prefixed(self) -> list[str]:
        return [f"{i:02d}-{c}" for i, c in enumerate(self.category_order, 1)]

    @property
    def raw_to_prefixed(self) -> dict[str, str]:
        return dict(zip(self.category_order, self.ordered_prefixed))


def _load_module_from_path(module_path: Path):
    spec = importlib.util.spec_from_file_location("kb_config_runtime", module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载配置文件: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _candidate_config_paths() -> list[Path]:
    candidates: list[Path] = []
    env_path = os.environ.get("KB_CONFIG_FILE", "").strip()
    if env_path:
        candidates.append(Path(env_path).expanduser())
    candidates.append(_THIS_DIR / "kb_config.local.py")
    candidates.append(_REPO_ROOT / "kb_collector" / "kb_config.py")
    return candidates


def _load_external_config():
    for path in _candidate_config_paths():
        if path.exists():
            return _load_module_from_path(path), path
    return None, None


def _default_config_values():
    ai_kb = KBConfig(
        key="ai",
        name="AI_KnowBase",
        path=_KB_ROOT / "AI_KnowBase",
        description="收集 AI 与技术相关的文章，涵盖工具、实践与前沿进展。",
        category_keywords={
            "工具与实践": ["工具", "实践", "教程", "使用", "案例", "落地"],
            "AI Coding": ["AI Coding", "Cursor", "Copilot", "Claude Code", "代码生成"],
            "前沿与研究": ["论文", "研究", "模型", "架构", "评测"],
            "未分类": [],
        },
        category_order=["工具与实践", "AI Coding", "前沿与研究", "未分类"],
    )
    engineering_kb = KBConfig(
        key="engineering",
        name="Engineering_KnowBase",
        path=_KB_ROOT / "Engineering_KnowBase",
        description="收集技术与工程领域的文章，涵盖架构、后端、DevOps 等方向。",
        category_keywords={
            "系统架构": ["架构", "微服务", "分布式", "高可用", "设计模式"],
            "后端与中间件": ["Kafka", "Redis", "MySQL", "API", "后端", "服务端"],
            "DevOps": ["CI/CD", "Docker", "Kubernetes", "监控", "部署"],
            "未分类": [],
        },
        category_order=["系统架构", "后端与中间件", "DevOps", "未分类"],
    )
    all_kbs = [ai_kb, engineering_kb]
    return {
        "AI_KB": ai_kb,
        "ENGINEERING_KB": engineering_kb,
        "ALL_KBS": all_kbs,
        "KB_BY_KEY": {kb.key: kb for kb in all_kbs},
    }


_external_config, CONFIG_SOURCE_PATH = _load_external_config()
if _external_config is not None:
    AI_KB = getattr(_external_config, "AI_KB", None)
    ENGINEERING_KB = getattr(_external_config, "ENGINEERING_KB", None)
    MANAGEMENT_KB = getattr(_external_config, "MANAGEMENT_KB", None)
    PM_KB = getattr(_external_config, "PM_KB", None)
    ALL_KBS = getattr(_external_config, "ALL_KBS")
    KB_BY_KEY = getattr(_external_config, "KB_BY_KEY")
    USING_DEFAULT_CONFIG = False
else:
    _defaults = _default_config_values()
    AI_KB = _defaults.get("AI_KB")
    ENGINEERING_KB = _defaults.get("ENGINEERING_KB")
    MANAGEMENT_KB = None
    PM_KB = None
    ALL_KBS = _defaults["ALL_KBS"]
    KB_BY_KEY = _defaults["KB_BY_KEY"]
    USING_DEFAULT_CONFIG = True
    CONFIG_SOURCE_PATH = None


def warn_if_using_default_config() -> None:
    if not USING_DEFAULT_CONFIG:
        return
    print(
        "[common.kb_config] 未找到 KB_CONFIG_FILE / common/kb_config.local.py，"
        "已回退到内置示例配置。建议复制 common/kb_config.example.py 为 common/kb_config.local.py。",
        file=sys.stderr,
    )
