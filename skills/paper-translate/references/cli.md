# pdf_zh_translator CLI 完整参考

以下内容与 `pdf_zh_translator/cli.py` 保持同步（如有出入以 `--help` 实际输出为准）。所有命令在仓库根执行：

```bash
.venv/bin/python -m pdf_zh_translator <子命令> [参数]
```

## translate — 翻译 PDF

```bash
.venv/bin/python -m pdf_zh_translator translate INPUT.pdf OUTPUT.pdf [选项]
```

| 选项 | 默认 | 说明 |
|---|---|---|
| `--api-mode` | `deepseek` | 供应商协议：`generic` / `openai-compatible` / `deepseek` / `cache-only` |
| `--api-url` | deepseek 模式为 `https://api.deepseek.com` | 供应商地址；也可用环境变量 `PDF_TRANSLATOR_API_URL`（deepseek 模式为 `DEEPSEEK_API_URL`） |
| `--api-key-env` | `PDF_TRANSLATOR_API_KEY` | 存放 API key 的环境变量名（推荐方式） |
| `--api-key` | — | 直接传 key（不推荐，会进 shell 历史） |
| `--model` | deepseek 模式为 `deepseek-v4-pro` | 模型名 |
| `--source-lang` / `--target-lang` | `en` / `zh` | 语言代码 |
| `--auth-header` / `--auth-scheme` | `Authorization` / `Bearer` | 认证头定制；`--auth-scheme ''` 表示裸 key |
| `--batch-size` | `8` | 每次请求的文本块数 |
| `--max-batch-chars` | `2500` | 每次请求的最大源字符数 |
| `--max-output-tokens` | `8192` | 单次请求最大输出 token |
| `--timeout` | `60.0` | 请求超时秒数 |
| `--retries` | `2` | 请求重试次数 |
| `--deepseek-thinking` | `disabled` | DeepSeek 思考模式：`disabled` / `enabled` |
| `--reasoning-effort` | `high` | DeepSeek 推理力度：`low` / `medium` / `high` |
| `--font-name` | `china-s` | 中文字体别名（PyMuPDF 内置） |
| `--font-file` | — | 自定义 TTF/OTF 字体文件 |
| `--min-font-size` | `5.0` | 最小字号 |
| `--font-scale` | `0.92` | 相对原字号的缩放 |
| `--margin` | `0.8` | 涂销/插入的内边距（PDF pt） |
| `--cache-file` | 自动 `<输出名>.translation-cache.jsonl` | JSONL 翻译缓存路径 |
| `--quiet` | — | 关闭逐批进度输出 |
| `--dry-run` | — | 不调 API，插入占位中文（验证排版用，无需 key） |
| `--preserve-graphics-text` | — | 保留图表内部文字与数学密集标签，仍翻译图注与正文 |
| `--skip-overflow` | — | 译文放不进原 bbox 时保留原文 |

约束：输入输出不能是同一文件；`cache-only` 模式必须提供 `--cache-file`。

## inspect — 译后视觉检查

```bash
.venv/bin/python -m pdf_zh_translator inspect ORIGINAL.pdf TRANSLATED.pdf \
  [--max-pages N] [--json-out report.json]
```

逐页比对，打印 `[severity] p页码 code: message`；存在 error 级 issue 时退出码 1。`--json-out` 写出 `{issue_count, issues[]}` 结构的 JSON 报告。

## export — 导出翻译块

```bash
.venv/bin/python -m pdf_zh_translator export INPUT.pdf blocks.jsonl
```

把可翻译文本块导出为 JSONL（`{key, page, source}`），用于人工翻译或排查抽取问题。

## 术语库（corpus-*）

| 命令 | 用途 |
|---|---|
| `corpus-stats` | 按领域统计术语数量 |
| `corpus-lint [--json] [--strict]` | 检查跨领域翻译冲突与质量问题；`--strict` 发现问题即退出非零（CI 门槛） |
| `corpus-health [--candidates-jsonl F] [--json]` | 顶会术语覆盖度与待审候选数 |
| `corpus-add FIELD English=中文 [...] [--source S] [--corpus-file F]` | 添加/更新已批准术语 |
| `corpus-review CANDIDATES.jsonl REVIEW.json` | 候选术语去重出审阅文件 |
| `corpus-audit CANDIDATES.jsonl REVIEW.json` | 审计并自动分类候选术语 |
| `corpus-promote REVIEW.json FIELD [--source S] [--corpus-file F]` | 把已审阅术语提升进正式术语库（`FIELD` 可为 `auto`） |
| `corpus-release VERSION [--corpus-file F]` | 给审阅后的术语库打版本 |

## 金标回归（golden-*）

| 命令 | 用途 |
|---|---|
| `golden-init MANIFEST [--target-cases N]` | 生成金标回归 manifest 模板（默认 100 篇） |
| `golden-discover ROOT MANIFEST [--original-suffix S] [--translated-suffix S] [--min-visual-score X]` | 扫描目录配对原文/译文并写 manifest |
| `golden-eval MANIFEST` | 评测 manifest 内所有论文，未达发布标准退出非零 |

## 版式模板（layout-learn）

```bash
.venv/bin/python -m pdf_zh_translator layout-learn TEMPLATE_NAME out.json 代表性1.pdf 代表性2.pdf [--max-pages-per-pdf 6]
```

从代表性 PDF 学习模板级版式画像（ACM/IEEE/Springer/ACL 等）。

## 可编辑图形 PPT（figure-ppt-*）

| 命令 | 用途 |
|---|---|
| `figure-ppt-extract INPUT.pdf OUT_ROOT [--paper-id ID] [--dpi 200] [--max-figures N]` | 提取 PDF 图形区域为图片素材并写 source manifest |
| `figure-ppt-batch-prepare SOURCE_MANIFEST [--limit N] [--with-text-hints]` | 为每个素材建 editppt 运行目录 |
| `figure-ppt-batch-register SOURCE_MANIFEST [--limit N]` | 注册所有已 finalize 的图形 PPT |
| `figure-ppt-source-audit SOURCE_MANIFEST [--require-prepared] [--require-registered] [--allow-empty]` | 审计素材与注册状态 |
| `figure-ppt-prepare IMAGE OUT_ROOT [--figure-id ID]` / `figure-ppt-register ID IMAGE RUN OUT_DIR [--pptx F]` / `figure-ppt-audit ROOT [--allow-empty]` | 单图版流程与目录级审计 |

## 批量基准（scripts/classic_benchmark.py）

```bash
.venv/bin/python scripts/classic_benchmark.py {fetch|translate|evaluate|report|gate} \
  --manifest MANIFEST.json --workdir WORKDIR [--only id1,id2] [--force] [--isolate] \
  [--qa-mode iterative] [--qa-max-passes 4] \
  [--min-evaluated N] [--min-strict-passes N] [--baseline-reports DIR]
```

`fetch` 下载论文 → `translate` 逐篇翻译（`--isolate` 每篇独立进程，限住内存）→ `evaluate` 严格 QA → `report` 汇总 → `gate` 按阈值放行。
