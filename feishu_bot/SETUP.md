# 飞书机器人 → 知识库自动存档：配置指南

手机看到好文章 → 发给飞书机器人 → 存入多维表 → openclaw 定时批量下载。

---

## 整体架构

```
[手机] 发链接给机器人
           ↓ 飞书事件推送
  [Vercel Function]（免费公网中转）
           ↓ 写入记录
  [飞书多维表 Bitable]
           ↑ 每天定时轮询（outbound，不需要外网入站）
  [openclaw 本地脚本]
           ↓ 调用 unified_collector.py
      [本地知识库]
```

---

## 第一步：创建飞书自建应用

1. 打开 [飞书开放平台](https://open.feishu.cn/app) → **创建企业自建应用**
2. 填写名称（如"知识库助手"）、描述，上传图标
3. 进入应用详情，记录：
   - **App ID**（`cli_xxxxxxxxx`）
   - **App Secret**（点击查看）

### 开启机器人能力

- 左侧 **应用能力 → 机器人** → 开启

### 申请权限

- **权限管理** → 搜索并开通以下权限：
  - `im:message` （接收消息）
  - `im:message:send_as_bot` （发送消息）
  - `bitable:app` （读写多维表）

### 发布应用

- **版本管理与发布** → 创建版本 → 申请发布（企业内部应用即时生效）

---

## 第二步：创建飞书多维表

1. 在飞书中新建一个**多维表格**，命名如"待下载链接"
2. 创建以下字段：

| 字段名   | 类型     | 说明               |
|----------|----------|--------------------|
| URL      | 文本     | 文章链接（必填）   |
| 状态     | 单选     | 待处理/处理中/已完成/失败 |
| 备注     | 文本     | 失败原因等         |
| 完成时间 | 文本     | 脚本自动填入       |

3. 从多维表 URL 获取两个 ID：
   - URL 格式：`https://xxx.feishu.cn/base/【BITABLE_APP_TOKEN】?table=【BITABLE_TABLE_ID】`
   - 例：`https://xxx.feishu.cn/base/PqAbCdEfGhIj?table=tblXXXXXXXXXX`

---

## 第三步：部署 Vercel 函数

```bash
# 1. 安装 Vercel CLI（需要 Node.js）
npm i -g vercel

# 2. 进入 feishu_bot 目录
cd feishu_bot

# 3. 登录并部署
vercel login
vercel deploy --prod
```

部署完成后会得到一个 URL，如：`https://wechat-to-kb-feishu-bot.vercel.app`

### 配置环境变量

在 Vercel 控制台 → 项目 → Settings → Environment Variables，添加：

```
FEISHU_APP_ID       = cli_xxxxxxxxxxxxxxxxxx
FEISHU_APP_SECRET   = xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
FEISHU_VERIFY_TOKEN = （下一步配置事件后会生成，先留空）
BITABLE_APP_TOKEN   = xxxxxxxxxxxxxxxxxxxxxxxx
BITABLE_TABLE_ID    = xxxxxxxxxxxxxxxxx
```

---

## 第四步：配置飞书事件订阅

1. 飞书开放平台 → 你的应用 → **事件订阅**
2. **请求网址 URL** 填入：`https://your-project.vercel.app/api/feishu_webhook`
3. 点击验证，飞书会发一个 challenge 请求，Vercel 函数会自动响应
4. 记录页面上的 **Verification Token**，填入 Vercel 环境变量 `FEISHU_VERIFY_TOKEN`
5. 更新 Vercel 环境变量后重新部署一次：`vercel deploy --prod`
6. 订阅事件：**添加事件** → 搜索 `im.message.receive_v1`（接收消息）

---

## 第五步：配置机器人权限和使用

### 让自己能和机器人私聊

1. 飞书 → 搜索你的机器人名称 → 发起私聊
2. 发送一条消息测试（此时可能提示"未开通"，需在开放平台启用）

---

## 第六步：openclaw 配置定时任务

### 配置环境变量

在 openclaw 上，编辑 `~/.profile` 或 `~/.bashrc`（或 systemd service 的 EnvironmentFile）：

```bash
export FEISHU_APP_ID="cli_xxxxxxxxxxxxxxxxxx"
export FEISHU_APP_SECRET="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
export BITABLE_APP_TOKEN="xxxxxxxxxxxxxxxxxxxxxxxx"
export BITABLE_TABLE_ID="xxxxxxxxxxxxxxxxx"
```

### 测试脚本

```bash
cd /path/to/wechat-to-kb
source ~/.profile
python feishu_sync.py
```

### 配置 cron（每天凌晨 2 点执行）

```bash
crontab -e
```

添加：

```cron
0 2 * * * source ~/.profile && cd /path/to/wechat-to-kb && python feishu_sync.py >> logs/feishu_sync.log 2>&1
```

---

## 日常使用方式

1. 手机看到好文章 → 复制链接
2. 打开飞书 → 找到知识库助手机器人 → 粘贴链接发送
3. 机器人回复"✅ 已收录 1 条链接"
4. 第二天凌晨 openclaw 自动下载，状态更新为"已完成"

**也支持一次发多条链接**，机器人会逐个识别并全部收录。

---

## 查看多维表状态

在飞书多维表中可以按"状态"筛选查看所有链接的处理情况：
- 🟡 待处理 → 等待下次 cron 执行
- 🔵 处理中 → 正在下载（cron 运行中）
- ✅ 已完成 → 已保存到知识库
- ❌ 失败   → 查看"备注"列了解原因

---

## 故障排查

| 问题 | 检查项 |
|------|--------|
| 机器人不回复 | 检查 Vercel 函数日志；检查事件订阅是否成功 |
| 状态一直是"待处理" | 检查 cron 日志 `logs/feishu_sync.log` |
| 状态变"失败" | 查看多维表"备注"列，或检查 cron 日志 |
| Vercel 函数验证失败 | 确认 `FEISHU_VERIFY_TOKEN` 与飞书控制台一致 |
