# wechat_collector 用法

入口脚本：`~/.openclaw/wechat-to-kb/wechat_collector/run.sh`

## 常用命令

```bash
cd ~/.openclaw/wechat-to-kb/wechat_collector

# 保存公众号文章
./run.sh "https://mp.weixin.qq.com/s/xxxxx"

# 批量保存
./run.sh -f urls.txt

# 指定知识库
./run.sh --kb ai "https://mp.weixin.qq.com/s/xxxxx"

# 仅重建索引
./run.sh --reindex
```

## 评论参数

- `--comments`：暂时保留的评论 PoC 开关，仅对公众号文章生效。
- `--comments-limit`：评论抓取上限，默认 `30`，最大 `100`。
- 评论抓取失败不会影响正文保存。

## 配置文件

- 推荐复制 `common/kb_config.example.py` 为 `common/kb_config.local.py`
