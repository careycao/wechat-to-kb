# web_collector 用法

入口脚本：`~/.openclaw/wechat-to-kb/web_collector/run.sh`

## 常用命令

```bash
cd ~/.openclaw/wechat-to-kb/web_collector

# 保存普通网页
./run.sh "https://example.com/article"

# 批量保存
./run.sh -f urls.txt

# 指定知识库
./run.sh --kb engineering "https://example.com/article"

# 仅重建索引
./run.sh --reindex
```

## 说明

- 该入口只处理普通网页；检测到 `mp.weixin.qq.com` 时会提示改用 `wechat_collector`
- 知识库配置统一从 `common/kb_config.local.py` 读取
