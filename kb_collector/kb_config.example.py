"""
kb_config.example.py — 知识库配置示例文件

使用方式：
  1. 复制本文件为 kb_config.py（已在 .gitignore 中排除）
  2. 按照你自己的知识体系修改知识库数量、分类名称和关键词
  3. 知识库数量不限，可以只保留一个，也可以扩展到更多

知识库目录默认存放在 ~/knowledge_base/
可通过 .env 中的 KB_ROOT 环境变量自定义路径。
"""

from dataclasses import dataclass
from pathlib import Path
import os

# 知识库根目录：优先读取环境变量，否则默认 ~/knowledge_base
_KB_ROOT = Path(os.environ.get("KB_ROOT", "")).expanduser() or Path.home() / "knowledge_base"


@dataclass
class KBConfig:
    key: str                               # 简短标识，用于 --kb 参数，如 ai / engineering
    name: str                              # 知识库目录名
    path: Path                             # 完整路径（通常无需修改）
    description: str                       # README 简介
    category_keywords: dict[str, list[str]]  # 分类名 → 关键词列表（用于自动分类）
    category_order: list[str]              # 分类顺序，决定目录编号；最后一项通常是「未分类」

    @property
    def ordered_prefixed(self) -> list[str]:
        return [f"{i:02d}-{c}" for i, c in enumerate(self.category_order, 1)]

    @property
    def raw_to_prefixed(self) -> dict[str, str]:
        return dict(zip(self.category_order, self.ordered_prefixed))


# ── 示例知识库 1：AI 与技术 ────────────────────────────────────────────────
# 根据你的需要修改分类名称和关键词
AI_KB = KBConfig(
    key="ai",
    name="AI_KnowBase",
    path=_KB_ROOT / "AI_KnowBase",
    description="收集 AI 与技术相关的文章，涵盖工具、实践与前沿进展。",
    category_keywords={
        "工具与实践": [
            "工具", "实践", "教程", "使用", "案例", "落地",
            # 在这里添加更多关键词...
        ],
        "AI Coding": [
            "AI Coding", "Cursor", "Copilot", "Claude Code", "代码生成",
            # 在这里添加更多关键词...
        ],
        "前沿与研究": [
            "论文", "研究", "模型", "架构", "评测",
            # 在这里添加更多关键词...
        ],
        "未分类": [],  # 保留「未分类」作为兜底，建议不删除
    },
    category_order=["工具与实践", "AI Coding", "前沿与研究", "未分类"],
)

# ── 示例知识库 2：工程技术 ─────────────────────────────────────────────────
ENGINEERING_KB = KBConfig(
    key="engineering",
    name="Engineering_KnowBase",
    path=_KB_ROOT / "Engineering_KnowBase",
    description="收集技术与工程领域的文章，涵盖架构、后端、DevOps 等方向。",
    category_keywords={
        "系统架构": [
            "架构", "微服务", "分布式", "高可用", "设计模式",
        ],
        "后端与中间件": [
            "Kafka", "Redis", "MySQL", "API", "后端", "服务端",
        ],
        "DevOps": [
            "CI/CD", "Docker", "Kubernetes", "监控", "部署",
        ],
        "未分类": [],
    },
    category_order=["系统架构", "后端与中间件", "DevOps", "未分类"],
)

# ── 如需更多知识库，按上面的格式继续添加 ──────────────────────────────────
# MANAGEMENT_KB = KBConfig(key="management", ...)
# PM_KB = KBConfig(key="pm", ...)

# ── 注册所有知识库（必须更新这里）────────────────────────────────────────
ALL_KBS: list[KBConfig] = [AI_KB, ENGINEERING_KB]
KB_BY_KEY: dict[str, KBConfig] = {kb.key: kb for kb in ALL_KBS}
