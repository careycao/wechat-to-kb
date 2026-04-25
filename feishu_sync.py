#!/usr/bin/env python3
"""
feishu_sync.py — 从飞书多维表读取"待处理"链接，调用 unified_collector.py 下载，完成后更新状态。

在 openclaw 上配置 cron 定时执行，例如每天凌晨 2 点：
  0 2 * * * cd /path/to/wechat-to-kb && python feishu_sync.py >> logs/feishu_sync.log 2>&1

所需环境变量（可写入 .env 或 ~/.profile）：
  FEISHU_APP_ID          飞书应用 App ID
  FEISHU_APP_SECRET      飞书应用 App Secret
  BITABLE_APP_TOKEN      多维表 App Token
  BITABLE_TABLE_ID       数据表 Table ID
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

FEISHU_BASE = "https://open.feishu.cn"
REPO_ROOT = Path(__file__).resolve().parent


def _load_dotenv() -> None:
    """加载项目根目录的 .env 文件（cron 环境不读 shell 配置，需显式加载）。"""
    env_file = REPO_ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()
COLLECTOR = REPO_ROOT / "unified_collector.py"


def _require_env(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        logger.error("缺少环境变量：%s", name)
        sys.exit(1)
    return val


# ── 飞书 API 工具 ─────────────────────────────────────────────────────────────

def _json_request(
    url: str,
    data: dict | None = None,
    token: str = "",
    method: str | None = None,
) -> dict:
    body = json.dumps(data).encode() if data is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        logger.error("HTTP %s %s → %s: %s", e.code, url, e.reason, error_body)
        raise


def get_tenant_access_token(app_id: str, app_secret: str) -> str:
    result = _json_request(
        f"{FEISHU_BASE}/open-apis/auth/v3/tenant_access_token/internal",
        {"app_id": app_id, "app_secret": app_secret},
    )
    return result["tenant_access_token"]


def get_pending_records(app_token: str, table_id: str, token: str) -> list[dict]:
    """读取状态为"待处理"的所有记录，自动处理分页。"""
    records: list[dict] = []
    page_token = ""
    filter_expr = 'CurrentValue.[状态]="待处理"'

    while True:
        params: dict[str, str | int] = {
            "filter": filter_expr,
            "page_size": 100,
        }
        if page_token:
            params["page_token"] = page_token

        url = (
            f"{FEISHU_BASE}/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"
            f"?{urllib.parse.urlencode(params)}"
        )
        result = _json_request(url, token=token)
        data = result.get("data", {})
        records.extend(data.get("items", []))

        if not data.get("has_more"):
            break
        page_token = data.get("page_token", "")

    return records


def update_record(
    app_token: str,
    table_id: str,
    record_id: str,
    status: str,
    token: str,
    note: str = "",
    title: str = "",
) -> None:
    fields: dict[str, str | int] = {"状态": status}
    if note:
        fields["备注"] = note
    if title:
        fields["标题"] = title
    if status == "已完成":
        # 飞书日期字段需要毫秒时间戳
        fields["完成时间"] = int(datetime.now().timestamp() * 1000)
    _json_request(
        f"{FEISHU_BASE}/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}",
        {"fields": fields},
        token=token,
        method="PUT",
    )


# ── 文章下载 ──────────────────────────────────────────────────────────────────

def process_url(url: str) -> tuple[bool, str, str]:
    """调用 unified_collector.py 处理单个 URL，返回 (成功, 错误信息, 文章标题)。"""
    env = os.environ.copy()
    env["KB_NON_INTERACTIVE"] = "1"

    try:
        venv_python = REPO_ROOT / ".venv" / "bin" / "python3"
        python_bin = str(venv_python) if venv_python.exists() else sys.executable
        result = subprocess.run(
            [python_bin, str(COLLECTOR), url, "--non-interactive"],
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode == 0:
            # 从输出中提取标题，格式：「• 标题： xxx」
            title = ""
            for line in result.stdout.splitlines():
                if line.strip().startswith("• 标题："):
                    title = line.strip().removeprefix("• 标题：").strip()
                    break
            return True, "", title
        stderr_snippet = (result.stderr or result.stdout or "").strip()[-300:]
        return False, stderr_snippet, ""
    except subprocess.TimeoutExpired:
        return False, "处理超时（>5min）", ""
    except Exception as exc:
        return False, str(exc)[:300], ""


# ── 主流程 ────────────────────────────────────────────────────────────────────

def main() -> None:
    app_id = _require_env("FEISHU_APP_ID")
    app_secret = _require_env("FEISHU_APP_SECRET")
    app_token = _require_env("BITABLE_APP_TOKEN")
    table_id = _require_env("BITABLE_TABLE_ID")

    logger.info("═══ 开始同步飞书多维表 ═══")

    access_token = get_tenant_access_token(app_id, app_secret)
    records = get_pending_records(app_token, table_id, access_token)

    if not records:
        logger.info("没有待处理链接，退出。")
        return

    logger.info("找到 %d 条待处理链接", len(records))
    success_count = 0
    fail_count = 0

    for record in records:
        record_id = record["record_id"]
        fields = record.get("fields", {})

        # 飞书多维表文本字段可能返回 list[dict] 格式
        url_field = fields.get("链接 URL", "")
        if isinstance(url_field, list):
            url = "".join(seg.get("text", "") for seg in url_field).strip()
        else:
            url = str(url_field).strip()

        if not url or not url.startswith("http"):
            logger.warning("记录 %s 的 URL 无效，跳过：%r", record_id, url)
            update_record(app_token, table_id, record_id, "失败", access_token, "URL 无效")
            fail_count += 1
            continue

        logger.info("处理：%s", url[:100])

        # 先标记为"处理中"，防止并发重复处理
        try:
            update_record(app_token, table_id, record_id, "处理中", access_token)
        except Exception:
            pass

        ok, err, title = process_url(url)
        if ok:
            update_record(app_token, table_id, record_id, "已完成", access_token, title=title)
            logger.info("  ✅ 完成：%s", title or url[:60])
            success_count += 1
        else:
            update_record(app_token, table_id, record_id, "失败", access_token, err)
            logger.error("  ❌ 失败：%s", err[:120])
            fail_count += 1

    logger.info(
        "═══ 同步完成：成功 %d 条，失败 %d 条 ═══",
        success_count,
        fail_count,
    )


if __name__ == "__main__":
    main()
