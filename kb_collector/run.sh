#!/usr/bin/env bash
# kb_collector — 多知识库采集入口
# 完整参数与交互说明见同目录 USAGE.md
#
# 用法：
#   ./run.sh "https://mp.weixin.qq.com/s/xxxxx"
#   ./run.sh "https://example.com/article"
#   ./run.sh -f urls.txt
#   ./run.sh --kb engineering "https://..."
#   ./run.sh --kb management "https://..."
#   ./run.sh --reindex
#   ./run.sh --reindex --kb ai

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [[ ! -d .venv ]]; then
  echo "正在创建虚拟环境并安装依赖..."
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt -q
  .venv/bin/playwright install chromium
fi

# 与 requirements.txt 同步（含新增 yt-dlp，供视频链接委托 video_collector 使用）
.venv/bin/pip install -r requirements.txt -q

if [[ $# -eq 0 ]]; then
  echo "用法:"
  echo "  ./run.sh <URL>                  保存单篇文章"
  echo "  ./run.sh <URL1> <URL2> ...      批量保存"
  echo "  ./run.sh -f urls.txt            从文件批量保存"
  echo "  ./run.sh --kb ai <URL>          强制指定知识库"
  echo "  ./run.sh --reindex              仅重建索引"
  echo "  ./run.sh -n <URL>               冲突时自动选得分最高 KB（无 TTY 时默认如此）"
  exit 1
fi

exec .venv/bin/python3 kb_builder.py "$@"
