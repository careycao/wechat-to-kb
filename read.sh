#!/usr/bin/env bash
# read.sh — 只读不存，抓取微信文章正文并打印到终端
# 用法: ./read.sh "https://mp.weixin.qq.com/s/xxxxx"

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [[ $# -eq 0 ]]; then
  echo "用法: ./read.sh <微信文章URL>"
  exit 1
fi

URL="$1"

# 复用 wechat_collector 的虚拟环境（如已存在），否则用系统 Python
if [[ -x "$SCRIPT_DIR/wechat_collector/.venv/bin/python3" ]]; then
  PYTHON="$SCRIPT_DIR/wechat_collector/.venv/bin/python3"
else
  PYTHON="python3"
fi

"$PYTHON" - "$URL" <<'EOF'
import sys
import re

sys.path.insert(0, sys.argv[0].rsplit('/', 1)[0] if '/' in sys.argv[0] else '.')

# 直接内联轻量抓取，不依赖内部模块
import sys
url = sys.argv[1]

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("❌ 缺少依赖，请先运行: pip3 install requests beautifulsoup4")
    sys.exit(1)

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

try:
    resp = requests.get(url, headers={
        "User-Agent": UA,
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept": "text/html,application/xhtml+xml",
    }, timeout=15)
    resp.raise_for_status()
except Exception as e:
    print(f"❌ 请求失败: {e}")
    sys.exit(1)

soup = BeautifulSoup(resp.text, "html.parser")

og = soup.find("meta", property="og:title")
title = og.get("content", "").strip() if og else ""

content_el = soup.find(id="js_content")
if not content_el:
    print("❌ 未找到正文（js_content），可能需要登录或 JS 渲染")
    sys.exit(1)

# 转纯文本
text = content_el.get_text(separator="\n", strip=True)
text = re.sub(r"\n{3,}", "\n\n", text)

print(f"# {title}\n")
print(f"原文: {url}\n")
print("---\n")
print(text)
EOF
