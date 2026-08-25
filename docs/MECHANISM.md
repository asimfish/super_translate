# SuperTranslate 实现机制交接稿

## A. README 用「实现机制」章节

### 实现机制

<img src="docs/assets/mechanism.svg" alt="SuperTranslate 从源 PDF 解析、翻译、原位回填到确定性 QA 质量循环的机制图" width="100%">

SuperTranslate 先从原 PDF 提取带页码、坐标、字号和语义角色的文本块，在内存中建立翻译单元与保护区映射；公式、引用和 URL 被替换为可逆的 `⟦n⟧` 占位符，图形、表格、算法与参考文献区域按规则冻结。（`pdf_zh_translator/pdf_layout.py:370-426`；`pdf_zh_translator/pdf_layout.py:8531-8801`；`pdf_zh_translator/pdf_layout.py:19545-19601`）

可翻译正文按标题、段落、图注等角色送入多供应商适配层；相关术语按块注入提示词，批处理受条数与字符数双重约束，JSONL 缓存支持确定性重放，占位符异常则降级为单块或分段重试。（`pdf_zh_translator/translators.py:92-167`；`pdf_zh_translator/translators.py:390-450`；`pdf_zh_translator/translators.py:832-923`）

渲染阶段只擦除可替换文字，在原 `bbox` 内用 CJK 字体回退链排版；原页尺寸、图像、矢量图形、链接及源公式字形继续沿用原 PDF。（`pdf_zh_translator/pdf_layout.py:1338-1393`；`pdf_zh_translator/pdf_layout.py:8012-8042`；`pdf_zh_translator/pdf_layout.py:25343-25370`）

译文随后在隔离子进程中重建同一源对象视图，检查漏翻、保护区变更、重叠与空页、图像/矢量/公式缺失、视觉墨迹、字号、表格和引用；术语审计当前只记录提示，不进入错误分。（`app/api/papers.py:1763-1836`；`pdf_zh_translator/pdf_layout.py:2706-2718`；`pdf_zh_translator/page_inspector.py:2295-2558`；`app/api/papers.py:2890-2922`）

迭代 QA 默认最多 4 轮（API 可配 1–8）：确定性规划器只选择登记过的动作；修复前快照纯中文与双语 PDF，修复后重跑全部检测器，仅当 `(error 数, issue 总数)` 严格下降才接受，否则原子回滚并停止无进展循环。无错误则交付，轮次历史写入 `*.qa.json`；漏翻类错误还可在外层恢复预算内重译，并最终恢复全局最佳快照。（`app/services/quality_agent.py:11-87`；`app/api/papers.py:1201-1241`；`app/api/papers.py:2306-2536`；`app/api/papers.py:2569-2603`；`app/api/papers.py:2746-2802`；`app/api/papers.py:2059-2206`）

Golden set 复用同一问题检测与视觉评分；发布基准当前为 50 篇清单，报告须齐全且默认至少 20 篇 strict pass，单篇要求 0 error、0 actionable warning、视觉分不低于 0.55，并校验证据来源与回归。（`pdf_zh_translator/golden_eval.py:125-152`；`benchmarks/classic20/manifest.json:1-16`；`scripts/classic_benchmark.py:185-200`；`scripts/classic_benchmark.py:1363-1529`；`scripts/classic_benchmark.py:1566-1567`）

## B. English version for README_en

### How it works

<img src="docs/assets/mechanism.svg" alt="SuperTranslate mechanism: source-PDF parsing, translation, in-place refill, and the deterministic QA loop" width="100%">

SuperTranslate first extracts text blocks with page, bounding-box, font-size, and semantic-role metadata, then builds an in-memory inventory of translation units and protected regions. Formulas, citations, and URLs become reversible `⟦n⟧` placeholders, while graphics, tables, algorithms, and bibliography regions are frozen by rule. (`pdf_zh_translator/pdf_layout.py:370-426`; `pdf_zh_translator/pdf_layout.py:8531-8801`; `pdf_zh_translator/pdf_layout.py:19545-19601`)

Translatable text is sent to a multi-provider adapter with title, body, and caption roles. Relevant terminology is injected per batch; item-count and character-count limits bound requests; a JSONL cache enables deterministic replay; placeholder failures fall back to single-item or prose-segment retries. (`pdf_zh_translator/translators.py:92-167`; `pdf_zh_translator/translators.py:390-450`; `pdf_zh_translator/translators.py:832-923`)

During rendering, only replaceable source text is removed. Chinese is typeset inside the original `bbox` through a CJK fallback chain, while page geometry, images, vector graphics, links, and source formula glyphs remain anchored to the original PDF. (`pdf_zh_translator/pdf_layout.py:1338-1393`; `pdf_zh_translator/pdf_layout.py:8012-8042`; `pdf_zh_translator/pdf_layout.py:25343-25370`)

An isolated subprocess then reconstructs the same source-object view and checks untranslated text, protected-region changes, overlaps and blank pages, missing images/vectors/formulas, rendered-ink regression, font sizing, tables, and references. Terminology auditing is currently advisory and does not enter the issue score. (`app/api/papers.py:1763-1836`; `pdf_zh_translator/pdf_layout.py:2706-2718`; `pdf_zh_translator/page_inspector.py:2295-2558`; `app/api/papers.py:2890-2922`)

Iterative QA defaults to at most four rounds (the API accepts 1–8). A deterministic planner chooses only registered actions; mono and dual PDFs are snapshotted before repair; every detector reruns afterward; and a candidate is accepted only when the lexicographic score `(error count, total issue count)` strictly decreases. Otherwise, the snapshot is atomically restored and the no-progress loop stops. Clean output is delivered, pass history is written to `*.qa.json`, and untranslated-text errors may also trigger bounded outer retranslation while retaining the globally best snapshot. (`app/services/quality_agent.py:11-87`; `app/api/papers.py:1201-1241`; `app/api/papers.py:2306-2536`; `app/api/papers.py:2569-2603`; `app/api/papers.py:2746-2802`; `app/api/papers.py:2059-2206`)

Golden-set regression reuses the same issue detectors and visual score. The current release manifest contains 50 papers: reports must be complete, at least 20 papers must pass the strict gate by default, and each strict pass requires zero errors, zero actionable warnings, and a visual score of at least 0.55, with provenance and regression checks enforced. (`pdf_zh_translator/golden_eval.py:125-152`; `benchmarks/classic20/manifest.json:1-16`; `scripts/classic_benchmark.py:185-200`; `scripts/classic_benchmark.py:1363-1529`; `scripts/classic_benchmark.py:1566-1567`)

## C. 项目主页卡片要点

- **逐对象解析，不重排页面**：文本块携带页码、坐标、字号与语义角色，后续始终以原页为几何基准。（`pdf_zh_translator/pdf_layout.py:370-426`；`pdf_zh_translator/pdf_layout.py:8531-8614`）
- **公式与引用先冻结**：数学片段、引用和 URL 先转为可逆占位符，保护区不交给模型改写。（`pdf_zh_translator/pdf_layout.py:175-233`；`pdf_zh_translator/pdf_layout.py:19545-19601`）
- **多模型共用一套约束**：DeepSeek、OpenAI 兼容端点与 Anthropic 协议共享结构提示和术语注入。（`pdf_zh_translator/translators.py:543-647`；`pdf_zh_translator/translators.py:832-923`）
- **批处理缓存减少重复调用**：双阈值分批、JSONL 缓存和有界网络重试共同控制成本与失败面。（`pdf_zh_translator/translators.py:128-167`；`pdf_zh_translator/translators.py:412-450`；`pdf_zh_translator/translators.py:649-684`）
- **原坐标回填，字体自适应**：只擦除可替换文字，再以 CJK 字体链在原 `bbox` 内排版。（`pdf_zh_translator/pdf_layout.py:1338-1393`；`pdf_zh_translator/pdf_layout.py:25343-25370`）
- **全部检测器每轮重跑**：文本层、视觉墨迹与独立页面巡检共同输出结构化问题清单。（`pdf_zh_translator/pdf_layout.py:2706-2718`；`pdf_zh_translator/pdf_layout.py:3343-3423`）
- **只接受严格变好的候选**：修复前保存快照，问题分未严格下降就原子回滚并停止。（`app/api/papers.py:2445-2494`；`app/api/papers.py:2746-2802`）
- **Golden 回归守住发布线**：Golden case 与发布基准复用同一 QA，并由 strict gate 校验证据与回归。（`pdf_zh_translator/golden_eval.py:125-152`；`scripts/classic_benchmark.py:1363-1529`）
