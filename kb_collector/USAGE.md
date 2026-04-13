# kb_collector 用法与交互说明

入口脚本：`~/.openclaw/wechat-to-kb/kb_collector/run.sh`（内部调用 `kb_builder.py`）。

---

## 飞书 / OpenClaw 里「说一句就保存」（不必开终端）

用户习惯在**对话**里说：「请把这个保存到知识库 `https://...`」。  
此时应由 **OpenClaw 助手直接 exec** 下面命令（路径按用户本机 `~` 展开），**不要**让用户去终端操作：

```bash
/bin/bash ~/.openclaw/wechat-to-kb/kb_collector/run.sh "https://..."
```

- 用户**没说哪个库**：不传 `--kb`，依赖脚本在无 TTY 下**自动选得分最高**的知识库。  
- 用户说「存 AI 知识库 / 工程库…」：加上 `--kb ai` 或 `engineering` / `management` / `pm`。

助手在保存成功后**简短确认**即可（例如已写入哪个库）。详细约定见 workspace **`TOOLS.md`** 中 kb_collector 小节。

---

## 基本命令（本机终端可选）

```bash
cd ~/.openclaw/wechat-to-kb/kb_collector

# 单个或多个 URL
./run.sh "https://mp.weixin.qq.com/s/xxxxx"
./run.sh "https://a.com/1" "https://a.com/2"

# 从文件读取（默认文件名 urls.txt，每行一个 URL，# 开头为注释）
./run.sh -f urls.txt
./run.sh -f /path/to/list.txt

# 仅重建索引（不抓取）
./run.sh --reindex
./run.sh --reindex --kb ai
```

---

## `--kb`：指定知识库（推荐在 OpenClaw / 自动化中使用）

| 取值 | 对应目录（默认在 `~/knowledge_base/` 下） |
|------|----------------------------------------|
| `ai` | AI_KnowBase |
| `engineering` | Engineering_KnowBase |
| `management` | Management_KnowBase |
| `pm` | PM_KnowBase |

指定后：**不再做跨库路由**，只在**该库内**按关键词选分类；也**不会出现**「选 1/2/3 哪个知识库」的交互。

示例：

```bash
./run.sh --kb ai "https://www.bilibili.com/video/BVxxxx"
./run.sh --kb engineering "https://example.com/post"
```

---

## 路由与交互（不指定 `--kb` 时）

1. 脚本根据正文 + 标题给四个知识库打分，分差大则**自动**落到某一库。
2. **分差小（置信度低）**时：
   - **本地终端**（有 TTY）：会提示在终端输入 `1/2/3/c/q` 选择。
   - **无 TTY**（如部分 OpenClaw exec）：默认**自动选得分最高的库 + 分类**，不等待输入。

---

## `-n` / `--non-interactive` 与 `KB_NON_INTERACTIVE`

即使在本机终端，也可**强制**「冲突时自动选最高分」，不进入选库交互：

```bash
./run.sh -n "https://..."
export KB_NON_INTERACTIVE=1
./run.sh "https://..."
```

---

## `--interactive`

在无 TTY 环境下仍**尝试**走交互选库（多数场景无效，仅特殊封装终端时使用）。

---

## `--no-skip`

默认若**同标题**已存在则跳过。加 `--no-skip` 会**再写一遍**（覆盖流程依实现而定，一般需确认是否已存在同名文件）。

```bash
./run.sh --no-skip "https://..."
```

---

## 重复或错误条目（删文件后重建索引）

同一链接若先按**普通网页**保存、再按**视频**保存，或误操作导致 `README.md` 里出现**两条**、其中一条正文异常（例如 B 站只抓到「首页 番剧…」导航）：

1. 在目标知识库下打开**分类目录**（如 `~/knowledge_base/AI_KnowBase/01-战略与框架/`）。
2. 用编辑器打开同名或极相似的 `.txt`，对照前几行：**保留**正文正确的那条（视频条目通常以 `【来源类型】视频` 开头；正常图文应为完整正文）。
3. **成对删除**错误条目对应的 `.txt` 与 `.html`（只删其一会留下不一致）。
4. 在本机执行**仅重建索引**（按目标库修改 `--kb`）：

```bash
~/.openclaw/wechat-to-kb/kb_collector/run.sh --reindex --kb ai
```

`engineering` / `management` / `pm` 与上表一致。重建后会更新该库根目录的 `README.md` 与 `des_url_list.txt`。

**提示**：两次保存若标题略有差异（例如标题末尾多空格），会出现两个极相似文件名；以 `.txt` 内容为准决定删哪一对。

---

## 视频链接（B 站 / 抖音 / 小红书等）

`kb_builder` 会按域名**优先**调用 `../video_collector`（yt-dlp）。B 站字幕常需登录：

```bash
cd ~/.openclaw/wechat-to-kb/video_collector
./run.sh --login    # 生成 cookies.txt 后，再回 kb_collector 保存链接
```

详见 `../video_collector/README.md`。

---

## OpenClaw 对话里怎么用

- **最省事**：先决定库，让助手执行  
  `~/.openclaw/wechat-to-kb/kb_collector/run.sh --kb ai "你的URL"`  
- **不指定库**：依赖自动路由；无 TTY 时冲突会**自动最高分**，无需在聊天里回答 `1/2/3`（聊天内容通常进不了脚本的 `input()`）。

---

## 环境变量（节选）

| 变量 | 作用 |
|------|------|
| `KB_NON_INTERACTIVE=1` | 等价于倾向自动选库（与 `-n` 配合逻辑见 `kb_builder._resolve_auto_route`） |
| `KB_HEADLESS=1` | Playwright 无头模式（网页抓取） |
