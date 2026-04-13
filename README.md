# wechat-to-kb

> 把微信公众号、网页、视频、小红书、RSS 统一沉淀为本地 Markdown 知识库，随时供 AI 检索与问答。

---

## 为什么做这个

微信公众号文章读完就忘，收藏了也找不到。  
B 站视频、小红书笔记、RSS 订阅……碎片信息越积越多，却无法被 AI 统一检索。

**wechat-to-kb** 的目标：让你读过的每一篇内容都变成可搜索、可问答的本地知识。

---

## 包含四个模块

| 模块 | 功能 |
|---|---|
| `kb_collector` | 核心采集器，支持微信公众号、通用网页，自动路由到对应知识库 |
| `video_collector` | 视频转文本，支持 B 站、YouTube、小红书视频号等（yt-dlp + 字幕提取） |
| `xhs_collector` | 小红书收藏夹批量入库 |
| `rss_daily` | RSS 订阅聚合，微信公众号文章自动归档 |

所有内容统一存储为 **Markdown 文件**，按知识库分类管理，可直接接入任何支持本地文件的 AI 工具（Cursor、Obsidian、RAG 等）。

---

## 快速开始

### 1. 安装依赖

```bash
git clone git@github.com:careycao/wechat-to-kb.git
cd wechat-to-kb
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
playwright install chromium
```

### 2. 配置知识库

复制示例配置，按需修改知识库路径和分类：

```bash
cp kb_collector/kb_config.example.py kb_collector/kb_config.py
# 编辑 kb_config.py，设置你的知识库根目录
```

### 3. 保存第一篇公众号文章

```bash
cd kb_collector
./run.sh "https://mp.weixin.qq.com/s/xxxxxx"
```

首次运行会打开浏览器，扫码登录微信即可，登录态自动保存。

---

## 各模块使用

### kb_collector（公众号 / 网页）

```bash
cd kb_collector

# 保存单篇文章
./run.sh "https://mp.weixin.qq.com/s/xxxxx"

# 指定知识库
./run.sh --kb ai "https://..."

# 批量导入（urls.txt 每行一个链接）
./run.sh -f urls.txt

# 重建索引
./run.sh --reindex
```

详细说明见 [kb_collector/USAGE.md](kb_collector/USAGE.md)。

### video_collector（视频转文本）

```bash
cd video_collector

# 首次使用：登录 B 站获取 cookies
./run.sh --login

# 保存视频（提取字幕/简介）
./run.sh "https://www.bilibili.com/video/BVxxxxx"
./run.sh "https://www.youtube.com/watch?v=xxxxx"
```

详细说明见 [video_collector/README.md](video_collector/README.md)。

### xhs_collector（小红书收藏）

```bash
cd xhs_collector
./run.sh
```

### rss_daily（RSS 订阅日报）

```bash
cp rss_daily/rss_config.example.yaml rss_daily/config.yaml
# 编辑 config.yaml，填入你的 RSS 订阅源
cd rss_daily && ./run.sh
```

---

## 存储结构

所有文章以 Markdown 存储，按知识库分类：

```
~/knowledge_base/
├── ai/
│   ├── index.json
│   └── articles/
│       └── 2026-04-13_文章标题.md
├── engineering/
├── management/
└── pm/
```

---

## 环境要求

- Python 3.10+
- Playwright（用于微信公众号登录态保持）
- yt-dlp（视频字幕提取，video_collector 使用）

---

## License

MIT
