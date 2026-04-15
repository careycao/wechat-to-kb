# video_collector

将哔哩哔哩、小红书、微信视频号等视频页链接转为可检索文本（简介 + 字幕，若有），经 `kb_router` 确认后写入与 `wechat_collector` / `web_collector` 相同结构的知识库。

## 依赖

- Python 3.10+
- `yt-dlp`（通过 `requirements.txt` 安装）
- 与 `../common` 共用 `kb_config` / `kb_routing` / `KBWriter`

不内置 LLM；不默认做 ASR（音视频转文字为 **P1 主线**，见方案第九节）。

## 使用

```bash
cd ~/.openclaw/wechat-to-kb/video_collector
./run.sh --login                                   # 推荐：打开浏览器登录 B 站，生成 cookies.txt
./run.sh "https://www.bilibili.com/video/BV1xx411c7mD"
./run.sh -f urls.txt
./run.sh --kb engineering "https://..."
./run.sh --dry-run "https://..."                    # 只打印，不写库、不交互
./run.sh --cookies ~/cookies.txt "https://..."      # 手动指定 Netscape Cookie
```

首次运行普通子命令会自动创建 `.venv` 并安装依赖（含 `yt-dlp`）。**`--login` 会确保安装 Playwright 并下载 Chromium**（与小红书收藏脚本类似）。

## B 站登录（推荐，与公众号/小红书同类体验）

部分 B 站字幕、流需**登录态**。本目录提供 **`./run.sh --login`**：

1. 自动拉起 Chromium，打开 bilibili.com  
2. 你在窗口内完成扫码或账号登录  
3. 回到终端按 Enter，将 **bilibili 域名** Cookie 写入本目录 **`cookies.txt`**（Netscape 格式，供 yt-dlp 使用）  
4. 之后直接 `./run.sh ...`，**无需再传 `--cookies`**（`resolve_cookies_path` 会默认读取 `cookies.txt`）

若你更习惯从浏览器扩展导出 Cookie，仍可使用下文「手动 Cookie」。

## Cookie（手动，可选）

若不用 `--login`，可将 **Netscape 格式** Cookie 提供给 yt-dlp：

1. **命令行**：`--cookies /path/to/cookies.txt`
2. **环境变量**：`export VIDEO_COLLECTOR_COOKIES=/path/to/cookies.txt`
3. **默认文件**：本目录下的 `cookies.txt`（勿提交 git）

导出方式见 [yt-dlp FAQ：Cookies](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp)。可参考 `cookies.txt.example`。

## `--dry-run`

仅用于预览与调试：拉取并打印 `plain_text`、路由建议（`route` 结果仅展示），**不**写入知识库、**不**出现确认交互。输出以 `=== VIDEO_COLLECTOR_DRY_RUN ===` 起止。

## 说明

- **粘贴链接时请用单引号包住**，避免 bash 把 `?`、`&` 转义成 `\?` 导致 404；脚本会自动修正常见 `\?` / `\=`，但推荐：  
  `./run.sh --dry-run 'https://www.bilibili.com/video/BVxxxx?vd_source=...'`
- 解析能力取决于 [yt-dlp](https://github.com/yt-dlp/yt-dlp) 与各站点策略；可 `pip install -U yt-dlp` 升级。
- 完整拉取失败时会**自动回退**为仅元数据（标题 + 简介等），`转写来源` 为「仅简介/文案」。
- **正式后续能力（含 ASR）**以 `Tech Design DocDir/Video_Collector_方案.md` **第九节**为准；Cookie、`--dry-run` 属**第十节**（工程便利，非 P1）。
