#!/usr/bin/env bash
# run_import_local_docs.sh — 本地文档（PDF / PPTX / DOCX）导入入口
#
# 用法示例：
#   ./run_import_local_docs.sh --source ~/Documents/Docs --dry-run
#   ./run_import_local_docs.sh --source ~/Documents/Docs
#   ./run_import_local_docs.sh --source ~/Documents/Docs --type pptx
#   ./run_import_local_docs.sh --file ~/Documents/a.pptx
#   ./run_import_local_docs.sh --source ~/Documents/Docs --overwrite
#
# 价值评估（默认开启）：
#   默认复用本机 Claude Code 的 `claude` CLI 登录态，**无需 API Key**。
#   若没装 Claude Code，则回退到 ANTHROPIC_API_KEY。
#   关闭评估可加 --no-value-check。
#   看完报告后把 review 条目的 hash 前 8 位拷贝出来，用 --force-include-hash 二次重跑。
#
# 例：
#   ./run_import_local_docs.sh --source ~/Documents/Docs --dry-run
#   ./run_import_local_docs.sh --source ~/Documents/Docs --force-include-hash 3b1b52d7,7418dd5d
#
#   # 没装 Claude Code 时的回退方式：
#   export ANTHROPIC_API_KEY=sk-ant-...
#   ./run_import_local_docs.sh --source ~/Documents/Docs

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

REQ_FILE="tools/requirements.txt"
REQ_HASH_FILE=".venv/.tools_requirements.sha256"

if [[ ! -d .venv ]]; then
  echo "正在创建虚拟环境..."
  python3 -m venv .venv
fi

CURRENT_REQ_HASH="$(shasum -a 256 "$REQ_FILE" | awk '{print $1}')"

if [[ ! -f "$REQ_HASH_FILE" ]] || [[ "$(cat "$REQ_HASH_FILE")" != "$CURRENT_REQ_HASH" ]]; then
  echo "安装/更新 tools 依赖（$REQ_FILE）..."
  .venv/bin/pip install -r "$REQ_FILE" -q
  printf '%s\n' "$CURRENT_REQ_HASH" > "$REQ_HASH_FILE"
fi

if [[ $# -eq 0 ]]; then
  echo "用法:"
  echo "  ./run_import_local_docs.sh --source DIR [--dry-run]        扫描目录批量导入（pdf/pptx/docx）"
  echo "  ./run_import_local_docs.sh --source DIR --type pptx        只处理 pptx"
  echo "  ./run_import_local_docs.sh --source DIR --type pdf,pptx    处理 pdf 和 pptx"
  echo "  ./run_import_local_docs.sh --file PATH                     导入单个文件（pdf/pptx/docx）"
  echo "  ./run_import_local_docs.sh --source DIR --overwrite        覆盖已存在的摘要卡"
  echo ""
  echo "其他参数："
  echo "  --type pdf,pptx,docx       文件类型过滤（默认全部）"
  echo "  --archive-root DIR         原件归档根目录（默认 ~/knowledge_base/Archive/LocalDocs/imported）"
  echo "  --report PATH              报告输出路径（默认：~/knowledge_base/Archive/LocalDocs/reports/local_docs_import_report_<目录名>_<YYYYMMDD>.md）"
  echo ""
  echo "价值评估（默认开启，优先用本机 claude CLI）："
  echo "  --no-value-check           关闭 LLM 价值评估，所有文件直接入库"
  echo "  --value-cache PATH         评估缓存文件（默认 ~/knowledge_base/Archive/LocalDocs/.value_cache.json）"
  echo "  --force-include-hash H1,H2 逗号分隔的 file_hash 前缀；命中即强制入库（review 条目二次确认用）"
  exit 1
fi

exec .venv/bin/python3 tools/import_local_docs.py "$@"
