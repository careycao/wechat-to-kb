#!/usr/bin/env bash
# wechat_collector — 微信公众号文章入库入口

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [[ ! -d .venv ]]; then
  echo "正在创建虚拟环境并安装依赖..."
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt -q
  .venv/bin/playwright install chromium
fi

.venv/bin/pip install -r requirements.txt -q

if [[ $# -eq 0 ]]; then
  echo "用法:"
  echo "  ./run.sh <URL>                  保存单篇公众号文章"
  echo "  ./run.sh <URL1> <URL2> ...      批量保存"
  echo "  ./run.sh -f urls.txt            从文件批量保存"
  echo "  ./run.sh --kb ai <URL>          强制指定知识库"
  echo "  ./run.sh --comments <URL>       尝试抓取公众号评论（PoC）"
  echo "  ./run.sh --reindex              仅重建索引"
  exit 1
fi

exec .venv/bin/python3 main.py "$@"
