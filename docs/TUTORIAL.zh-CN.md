# Super Translate 使用教程

Super Translate 是一个把英文学术论文 PDF 翻译成中文的自托管系统：保留原始版面、
公式、图表和引用，输出单语（纯中文）和双语（原文+译文对照）两种 PDF。

## 一、三种使用方式

| 方式 | 适合场景 | 入口 |
|------|----------|------|
| 本机运行 | 自己电脑上用，无需公网 | `http://localhost:8001` |
| 部署为网站 | 手机/多设备随时用 | 见 [DEPLOYMENT.md](DEPLOYMENT.md) |
| 命令行 | 批量/脚本化翻译 | `python -m pdf_zh_translator ...` |

### 本机快速开始

```bash
git clone https://github.com/asimfish/super_translate.git
cd super_translate
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

export PAPER_CHINA_DEEPSEEK_API_KEY="你的 DeepSeek API Key"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

浏览器打开 <http://localhost:8001>。本机回环访问不需要配置 token。

## 二、日常使用流程

1. **上传**：点击「上传论文」或直接拖拽 PDF（可多选，单文件上限 100MB）。
   可以顺手打标签，方便以后筛选。
2. **翻译**：在论文卡片上点「翻译」。可选项：
   - **后端**：DeepSeek（默认）/ Kimi K3 / OpenAI / Google。
   - **质量档**：`fast`（Google，免 API key，速度快）/ `balanced`（默认）/
     `high`（更保守的版面处理）。
   - **保留图内文字**：默认开启。关闭后会尝试翻译图表内部的可编辑文字。
   - **OCR**：扫描版 PDF（纯图片）先做 OCR 再翻译。
3. **看进度**：卡片上有实时进度条和预计剩余时间；点开可以看逐步日志
   （术语提示、版面修复、QA 结果都在这里）。
4. **阅读**：翻译完成后有三种查看方式：
   - **译文**：纯中文 PDF；
   - **双语对照**：左右分栏、同步滚动，分割线可拖动；
   - **原文**：随时回看原版。
5. **下载**：`_zh.pdf`（纯中文）和 `_dual.pdf`（双语对照）都可以下载。

## 三、翻译质量机制（为什么值得信）

每次翻译后系统会自动做译后检查（QA），报告写入 `*.qa.json`：

- **漏翻检测**：正文、图注、公式说明、图框内多行说明仍是英文会被标记；
- **保护区检测**：表格、算法伪代码、参考文献里的内容被意外改动（包括
  实验数字被篡改）会报错；
- **版面检测**：文本重叠、图片丢失、空白页、视觉回归（首/中/末页采样）；
- **术语一致性**：内置 1000+ 条 AI 顶会术语表（NeurIPS/ICML/CVPR/ACL…），
  翻译时注入提示词，翻译后审计是否使用了标准译法。

出现「译后检查失败」时译文仍会保留，可以在日志里查看 QA 报告后决定是否重译。

## 四、常用配置

写在 `.env`（或环境变量，前缀 `PAPER_CHINA_`）：

| 配置 | 说明 |
|------|------|
| `DEEPSEEK_API_KEY` | 默认后端的 API key |
| `MOONSHOT_API_KEY` | Kimi K3 后端 |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` | OpenAI 或任意兼容端点 |
| `PAPER_CHINA_API_TOKEN` | 公网部署必填的访问令牌 |
| `PAPER_CHINA_MAX_CONCURRENT_TRANSLATIONS` | 同时翻译的论文数（默认 1） |
| `PAPER_CHINA_TRANSLATION_CONCURRENCY` | 单篇内部并行请求数；限速 key 设为 1 |
| `PAPER_CHINA_FEISHU_WEBHOOK_URL` | 翻译完成后发飞书通知 |

## 五、常见问题

**Q: 翻译到一半失败了怎么办？**
点卡片上的「重试」。已翻译片段有缓存（`*.translation-cache.jsonl`），大多数
失败场景下重试会复用已完成部分、不重复消耗对应的 API 额度（进程崩溃等极端
情况会清理输出目录，此时重试从头开始）。

**Q: 公式/表格会被翻译坏吗？**
公式、表格、算法伪代码属于保护区，原样保留；QA 还会二次校验这些区域没有
被改动（连数值被改都能查出来）。

**Q: 图里的英文为什么没翻译？**
默认策略是保护图内文字（避免破坏图表）。上传时关掉「保留图内文字」可以
翻译图内可编辑文本；烧在位图里的文字无法处理。

**Q: 扫描版 PDF 能翻吗？**
开启 OCR 选项即可（服务器需装 Tesseract；Docker 部署方式见部署文档）。

**Q: 手机上能用吗？**
部署成网站后直接用手机浏览器访问，界面是自适应的。

## 六、命令行用法（可选）

```bash
# 直接翻译一个 PDF（不经过 Web 界面）
python -m pdf_zh_translator translate paper.pdf out/ --backend deepseek

# 术语库一致性检查
python -m pdf_zh_translator corpus-lint --strict
```

更多命令见 `python -m pdf_zh_translator --help`。
