#!/usr/bin/env bash
# platform_collector — 平台内容自动采集入口
#
# 用法：
#   ./run.sh --list                         列出所有可用任务
#   ./run.sh zhihu_hot                      抓取知乎热榜
#   ./run.sh hackernews_hot --limit 15      抓取 HN Top15
#   ./run.sh zhihu_hot hackernews_hot       同时抓取多个任务
#   ./run.sh --all                          执行所有注册任务
#   ./run.sh zhihu_hot --dry-run            只打印不写入
#
# 依赖：
#   - autocli 二进制（需在 PATH 中，或在常见安装路径下）
#   - 根目录 .venv（与 unified_collector 共用，含 common/ 依赖）

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# 使用根目录虚拟环境（含 common/ 所有依赖）
VENV="$REPO_ROOT/.venv"
if [[ ! -d "$VENV" ]]; then
  echo "[ERROR] 未找到根目录 .venv，请先在仓库根目录执行："
  echo "  python3 -m venv .venv && .venv/bin/pip install -e ."
  exit 1
fi

exec "$VENV/bin/python3" "$SCRIPT_DIR/platform_collector.py" "$@"
