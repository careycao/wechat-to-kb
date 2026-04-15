"""
kb_config.example.py — 知识库配置示例文件

使用方式：
  1. 复制本文件为 kb_config.local.py（已在 .gitignore 中排除）
  2. 按照你自己的知识体系修改知识库数量、分类名称和关键词
  3. 知识库数量不限，可以只保留一个，也可以扩展到更多
"""

from dataclasses import dataclass
from pathlib import Path
import os

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


AI_KB = KBConfig(
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

ENGINEERING_KB = KBConfig(
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

ALL_KBS: list[KBConfig] = [AI_KB, ENGINEERING_KB]
KB_BY_KEY: dict[str, KBConfig] = {kb.key: kb for kb in ALL_KBS}
