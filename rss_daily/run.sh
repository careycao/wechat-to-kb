#!/usr/bin/env bash
# rss_daily — RSS 技术订阅摘要日报
#
# 用法：
#   ./run.sh                        # 抓取今日文章，输出结构化列表
#   ./run.sh --no-content           # 只用 RSS 摘要，不抓全文（更快）
#   ./run.sh --no-fetch             # 不重新抓取，只输出已有数据
#   ./run.sh --top-n 15             # 精选条数（默认 10）
#   ./run.sh --date 2026-04-09      # 查看指定日期
#   ./run.sh --verbose              # 显示详细日志

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [[ ! -d .venv ]]; then
  echo "正在创建虚拟环境并安装依赖..."
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt -q
  .venv/bin/playwright install chromium
fi

exec .venv/bin/python3 main.py "$@"
