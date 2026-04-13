#!/usr/bin/env bash
# xhs_collector — 小红书收藏入库工具
#
# 用法：
#   ./run.sh --login          首次登录（打开浏览器扫码）
#   ./run.sh                  增量抓取收藏，存入知识库
#   ./run.sh --limit 50       限制抓取数量
#   ./run.sh --no-skip        重新处理所有收藏（含已入库）

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [[ ! -d .venv ]]; then
  echo "正在创建虚拟环境并安装依赖..."
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt -q
  .venv/bin/playwright install chromium
fi

exec .venv/bin/python3 xhs_builder.py "$@"
