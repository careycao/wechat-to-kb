#!/usr/bin/env python3
"""获取知识库多维表的 app_token，并测试读写权限"""
import json, os, urllib.request, urllib.error, urllib.parse

app_id     = os.environ["FEISHU_APP_ID"]
app_secret = os.environ["FEISHU_APP_SECRET"]

WIKI_NODE_TOKEN = "K0GzwwEPKiLILRkum47clrN1nRe"
TABLE_ID        = "tblA4zxPtRL5kCGI"
FEISHU_BASE     = "https://open.feishu.cn"

# 1. 获取 tenant_access_token
r = urllib.request.urlopen(
    urllib.request.Request(
        f"{FEISHU_BASE}/open-apis/auth/v3/tenant_access_token/internal",
        data=json.dumps({"app_id": app_id, "app_secret": app_secret}).encode(),
        headers={"Content-Type": "application/json"},
    )
)
token = json.loads(r.read())["tenant_access_token"]
print(f"✅ Token: {token[:20]}...")

# 2. 通过 Wiki API 获取多维表 app_token
print(f"\n--- 获取 Wiki 节点信息（node_token={WIKI_NODE_TOKEN}）---")
params = urllib.parse.urlencode({"token": WIKI_NODE_TOKEN, "obj_type": "wiki"})
req = urllib.request.Request(
    f"{FEISHU_BASE}/open-apis/wiki/v2/spaces/get_node?{params}",
    headers={"Authorization": f"Bearer {token}"},
)
try:
    resp = urllib.request.urlopen(req)
    result = json.loads(resp.read())
    print(json.dumps(result, indent=2, ensure_ascii=False))
    node = result.get("data", {}).get("node", {})
    app_token = node.get("obj_token", "")
    obj_type  = node.get("obj_type", "")
    print(f"\nobj_type  = {obj_type}")
    print(f"app_token = {app_token}")
except urllib.error.HTTPError as e:
    body = e.read().decode()
    print(f"❌ Wiki API 失败 {e.code}: {body}")
    print("\n⚠️  可能需要在飞书开放平台添加 wiki:wiki:readonly 权限并重新发布")
    app_token = ""

if not app_token:
    print("\n无法获取 app_token，退出")
    exit(1)

print(f"\n✅ 正确的 app_token = {app_token}")
print(f"✅ table_id         = {TABLE_ID}")

# 3. 测试 POST 写入
print("\n--- 测试 POST（新建记录）---")
req2 = urllib.request.Request(
    f"{FEISHU_BASE}/open-apis/bitable/v1/apps/{app_token}/tables/{TABLE_ID}/records",
    data=json.dumps({"fields": {"链接 URL": "https://debug-test.com", "状态": "待处理"}}).encode(),
    headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
)
try:
    resp2 = urllib.request.urlopen(req2)
    result2 = json.loads(resp2.read())
    record_id = result2.get("data", {}).get("record", {}).get("record_id", "")
    print(f"✅ POST 成功，record_id: {record_id}")
except urllib.error.HTTPError as e:
    print(f"❌ POST 失败 {e.code}: {e.read().decode()}")
