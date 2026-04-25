#!/usr/bin/env bash
# wechat-to-kb — 统一链接入库入口

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
KB_BROWSER_EXECUTABLE="${KB_BROWSER_EXECUTABLE:-}"
if [[ -z "$KB_BROWSER_EXECUTABLE" ]]; then
  for candidate in \
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
    "/Applications/Chromium.app/Contents/MacOS/Chromium"
  do
    if [[ -x "$candidate" ]]; then
      KB_BROWSER_EXECUTABLE="$candidate"
      export KB_BROWSER_EXECUTABLE
      break
    fi
  done
fi

PLAYWRIGHT_INSTALL_NEEDED=1
if [[ -n "$KB_BROWSER_EXECUTABLE" ]]; then
  PLAYWRIGHT_INSTALL_NEEDED=0
fi

REQ_HASH_FILE=".venv/.requirements.sha256"
CURRENT_REQ_HASH="$(
  shasum -a 256 \
    wechat_collector/requirements.txt \
    web_collector/requirements.txt \
    video_collector/requirements.txt \
    | shasum -a 256 | awk '{print $1}'
)"

if [[ ! -d .venv ]]; then
  echo "正在创建虚拟环境并安装依赖..."
  python3 -m venv .venv
fi

if [[ ! -f "$REQ_HASH_FILE" ]] || [[ "$(cat "$REQ_HASH_FILE")" != "$CURRENT_REQ_HASH" ]]; then
  .venv/bin/pip install -r wechat_collector/requirements.txt -q
  .venv/bin/pip install -r web_collector/requirements.txt -q
  .venv/bin/pip install -r video_collector/requirements.txt -q
  if [[ "$PLAYWRIGHT_INSTALL_NEEDED" == "1" ]]; then
    .venv/bin/playwright install chromium
  fi
  printf '%s\n' "$CURRENT_REQ_HASH" > "$REQ_HASH_FILE"
fi

if [[ $# -eq 0 ]]; then
  echo "用法:"
  echo "  ./run.sh <URL>                  自动分流保存内容链接"
  echo "  ./run.sh <URL1> <URL2> ...      批量保存"
  echo "  ./run.sh -f urls.txt            从文件批量保存"
  echo "  ./run.sh --kb ai <URL>          强制指定知识库"
  echo "  ./run.sh --comments <URL>       公众号文章额外尝试抓评论"
  echo "  ./run.sh --reindex              仅重建索引"
  echo "  ./run.sh --login                刷新微信登录 session（实验性，暂对付费文章无效）"
  exit 1
fi

exec .venv/bin/python3 unified_collector.py "$@"
