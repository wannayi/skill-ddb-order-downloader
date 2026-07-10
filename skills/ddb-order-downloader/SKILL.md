---
name: ddb-order-downloader
description: Use when the user provides a Deutsche Digitale Bibliothek newspaper search URL, DDB order details, Xianyu customer request, OCR download request, keyword filtering request, or asks to process DDB newspaper orders into TXT/CSV/ZIP deliverables.
---

# DDB Order Downloader

## Overview

Use this skill to process DDB newspaper search orders with the local downloader. The deliverable is a folder and zip containing page-level OCR text, metadata CSV, snippets CSV, manifest JSON, raw API JSON, and a customer-facing note.

## Safety Boundary

Do not automate Xianyu login, private-message scraping, hidden Xianyu APIs, or auto-sending messages. Keep Xianyu as the manual customer/payment channel; automate only the DDB download and local packaging workflow.

## Choose The Mode

| User request | Mode |
|---|---|
| One DDB URL and one requirement | `single` |
| Multiple orders in a CSV/table | `batch` |
| "全部/所有/下载这个页面全部" | `--filter-mode none` |
| "只要内容里有 X" | `--filter-mode exact --filter-value "X"` |
| "变格/正则/匹配多种写法" | `--filter-mode regex --filter-value "PATTERN"` |
| "先测试几条" | add `--limit 3` |

## Find The Script

Prefer the workspace copy when present:

```powershell
outputs/ddb_mvp_tool/ddb_mvp_tool.py
```

If it is missing, use this skill's bundled script:

```powershell
<skill_dir>\scripts\ddb_mvp_tool.py
```

When using the bundled script, pass an explicit output base in the workspace:

```powershell
--output-base outputs/ddb_mvp_runs
```

## Single Order Workflow

1. Extract the DDB URL, output name, and filtering requirement from the user's message.
2. If the output name is missing, create a short stable name such as `order-YYYYMMDD-keyword`.
3. Run the downloader.
4. Read the JSON result from stdout.
5. Verify the generated zip and the counts before reporting completion.

Command shape:

```powershell
python -X utf8 outputs/ddb_mvp_tool/ddb_mvp_tool.py single "DDB_URL" --name order-001
```

Exact phrase filter:

```powershell
python -X utf8 outputs/ddb_mvp_tool/ddb_mvp_tool.py single "DDB_URL" --name order-001 --filter-mode exact --filter-value "chinesische Studenten"
```

Regex filter:

```powershell
python -X utf8 outputs/ddb_mvp_tool/ddb_mvp_tool.py single "DDB_URL" --name order-001 --filter-mode regex --filter-value "chinesisch(e|en)\s+Studierend(e|en)"
```

## Batch Order Workflow

Use batch mode when the user provides or asks for an order table.

CSV fields:

```text
order_id,customer,ddb_url,output_name,filter_mode,filter_value,status,limit
```

Rules:

- `status` blank or `pending`: process the row.
- `status` `done`, `skip`, or any other value: skip the row.
- `filter_mode`: `none`, `exact`, or `regex`.
- `limit`: optional test cap; leave blank for full orders.

Command shape:

```powershell
python -X utf8 outputs/ddb_mvp_tool/ddb_mvp_tool.py batch outputs/ddb_mvp_tool/sample_orders.csv
```

Batch outputs:

- `outputs/ddb_mvp_runs/<output_name>/`
- `outputs/ddb_mvp_runs/<output_name>.zip`
- `order_status.csv`
- `customer_replies.txt`

## Verification

Before saying the order is complete, verify:

- stdout JSON reports `missing_text_files: 0` for single orders, or batch `failed: 0` when appropriate.
- `metadata.csv` row count equals the number of saved records.
- `texts/` contains the same number of `.txt` files.
- the zip opens and `testzip()` returns `None`.

Useful check:

```powershell
python -X utf8 -c "import zipfile; z=zipfile.ZipFile('PATH_TO_ZIP'); print(z.testzip()); z.close()"
```

## Response Template

Return concise results:

```text
已处理订单 <order_id>。
输出目录：<folder>
压缩包：<zip>
保存 OCR 文本页数：<count>
说明：DDB 返回的是命中报纸页 OCR，不是人工切分后的单篇文章。
```

For Xianyu delivery, include the customer reply from `customer_replies.txt` when batch mode produced it.

## Common Mistakes

- Do not promise "single article extraction" unless a separate manual article-splitting step was requested.
- Do not say the output is complete until zip and count checks pass.
- Do not overwrite existing order folders; choose a new `--name`.
- Do not download ALTO XML or scans by default; the MVP is text/metadata/zip unless the user explicitly asks for heavier assets.
