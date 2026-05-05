# wechat-to-kb MCP Server

采集和管理本地知识库。支持"只读不存"和"读取并入库"两种模式。

在 Claude Desktop、Cursor、Cowork 等任何支持 MCP 的 AI 工具中直接说：

> "帮我读一下这篇文章 https://mp.weixin.qq.com/s/xxxxx"（只读）
> "帮我把这篇文章存到知识库 https://mp.weixin.qq.com/s/xxxxx"（入库）

---

## 提供的工具

| 工具 | 说明 |
|------|------|
| `fetch_url` | **只读**：抓取公众号文章或网页正文并返回，不保存到知识库 |
| `save_url` | 保存公众号文章、网页、视频（自动识别类型） |
| `save_urls_batch` | 批量保存多个链接 |
| `import_local_file` | 导入本地 PDF / PPTX / DOCX |
| `list_knowledge_bases` | 查看已配置的知识库和分类 |
| `rebuild_index` | 重建知识库索引文件 |

---

## 安装

### 前置要求

1. Python 3.10+
2. 已完成 wechat-to-kb 基础配置（`kb_config.local.py` 存在）

如果还没配置，先看根目录 README.md 的"快速开始"章节。

### 方式一：本地安装（推荐，已 clone 项目的用户）

```bash
cd ~/DevProjects/wechat-to-kb      # 替换为你的项目路径
pip install -e ".[mcp]"
```

### 方式二：uvx（无需 clone，直接运行）

```bash
# 确保已安装 uv
# https://docs.astral.sh/uv/getting-started/installation/
uvx --from wechat-to-kb wechat-to-kb-mcp
```

---

## 配置 Claude Desktop

编辑 `~/Library/Application Support/Claude/claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "wechat-to-kb": {
      "command": "python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/Users/你的用户名/DevProjects/wechat-to-kb",
      "env": {
        "KB_ROOT": "/Users/你的用户名/knowledge_base",
        "KB_NON_INTERACTIVE": "1"
      }
    }
  }
}
```

**或者使用 uvx（无需 cwd）：**

```json
{
  "mcpServers": {
    "wechat-to-kb": {
      "command": "uvx",
      "args": ["--from", "wechat-to-kb", "wechat-to-kb-mcp"],
      "env": {
        "KB_ROOT": "/Users/你的用户名/knowledge_base",
        "KB_NON_INTERACTIVE": "1"
      }
    }
  }
}
```

修改后重启 Claude Desktop 生效。

---

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `KB_ROOT` | 知识库根目录 | `~/knowledge_base` |
| `KB_NON_INTERACTIVE` | 路由置信度低时自动选最高分，不询问（MCP 场景必须设为 1） | `0` |
| `KB_HEADLESS` | Playwright 无头模式（定时/后台场景建议设为 1） | `0` |

---

## 使用示例

在 Claude 对话中直接说：

```
读一下这篇文章，总结要点：https://mp.weixin.qq.com/s/xxxxx

保存这篇文章到知识库：https://mp.weixin.qq.com/s/xxxxx

把这几篇文章都存一下：
- https://mp.weixin.qq.com/s/aaaaa
- https://example.com/article
- https://www.bilibili.com/video/BVxxxxx

帮我导入 ~/Downloads/行业报告.pdf 到知识库

列出我的知识库有哪些分类
```

---

## 注意事项

- **付费公众号文章**：需要微信登录态，当前 MCP 版本会返回明确错误提示，请通过 CLI（`./run.sh --login`）完成登录后重试
- **评论抓取**：`--comments` 功能暂不在 MCP 工具中暴露，如需抓评论请使用 CLI
- **RSS 订阅 / 有道云笔记导入**：定时任务性质，请使用 CLI

---

## 本地测试

```bash
# 直接运行 MCP server（stdio 模式，正常看不到任何输出，等待 JSON-RPC 输入）
python -m mcp_server.server

# 用 mcp dev 工具调试（需安装 mcp[cli]）
mcp dev mcp_server/server.py
```
