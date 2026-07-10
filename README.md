# DDB Order Downloader 插件

这是一个可迁移的 Codex 个人插件，用于处理 Deutsche Digitale Bibliothek（DDB）报纸检索订单，把 DDB 报纸搜索 URL 转成可交付的 OCR 文本、元数据表、关键词上下文、原始 JSON、README 和 ZIP。

## 插件能力

- 处理单个 DDB 报纸搜索链接。
- 处理批量订单 CSV。
- 支持全部下载、精确关键词过滤、正则/变格过滤。
- 输出 `texts/`、`metadata.csv`、`snippets.csv`、`manifest.json`、`results.json`、`README.md` 和 ZIP。
- 保持闲鱼为人工接单/付款/发货渠道，不自动登录闲鱼、不读取私信、不调用闲鱼隐藏接口。

## 目录结构

```text
ddb-order-downloader-plugin/
  .codex-plugin/
    plugin.json
  skills/
    ddb-order-downloader/
      SKILL.md
      scripts/
        ddb_mvp_tool.py
      agents/
        openai.yaml
  install.ps1
  README.md
```

## 注意

这个插件只自动化 DDB 下载和本地交付包生成，不自动化闲鱼账号、聊天、付款或发货。
