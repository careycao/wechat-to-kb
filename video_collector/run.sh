#!/usr/bin/env bash
# video_collector — 视频链接 → 文本 → 知识库
#
# 用法：
#   ./run.sh --login                  # 打开浏览器登录 B 站，生成 cookies.txt
#   ./run.sh "https://www.bilibili.com/video/BV..."
#   ./run.sh -f urls.txt
#   ./run.sh --kb engineering "https://..."
#   ./run.sh --dry-run "https://..."
#   ./run.sh --cookies /path/to/cookies.txt "https://..."

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [[ ! -d .venv ]]; then
  echo "正在创建虚拟环境并安装依赖..."
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt -q
  .venv/bin/playwright install chromium
fi

if [[ "${1:-}" == "--login" ]]; then
  if [[ ! -d .venv ]]; then
    echo "正在创建虚拟环境..."
    python3 -m venv .venv
  fi
  .venv/bin/pip install -r requirements.txt -q
  .venv/bin/playwright install chromium
  exec .venv/bin/python3 bilibili_login.py
fi

exec .venv/bin/python3 video_builder.py "$@"
