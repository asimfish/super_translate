# SuperTranslate 机制图交接

## 事实源状态

Owner 指定的 `docs/adr/0004-independent-review-repair-protocol.md` 在本次工作树中不存在，仓库内也没有其他 `0004` ADR。当前已接受且与现行代码一致的协议是 `docs/adr/0001-independent-translation-quality-loop.md`。因此，对抗图只画已经能由 ADR-0001、`docs/MECHANISM.md` 和当前代码证明的边界：

- 修复方只能产出候选，不能自行决定接受；
- QA 在隔离子进程中读取源 PDF 与候选 PDF，只返回结构化缺陷；
- 候选只有严格降低 `(error 数, issue 总数)` 才能替换旧结果；
- 修复前快照、跨尝试全局最佳和发布 strict gate 分别承担回滚、保底与终审。

图中的 `qa-readonly` 表示“只读复核职责”，不暗示当前产品另有一个可自行裁决的第二模型服务。正式补入 ADR-0004 后，应再对照其措辞复核本图。

## 1. 整体架构图

文件：`docs/assets/arch_overview.svg`

中文图注：SuperTranslate 先冻结原 PDF 中不可改的结构对象，只翻译可替换正文，再按原坐标回填并通过 QA 审计，输出纯中文与双语两种 PDF。

English caption: SuperTranslate freezes non-editable source objects, translates only replaceable prose, refills it in the original coordinates, and delivers monolingual and bilingual PDFs only after QA.

信息结构：一条从左到右的主链只保留五个阶段——源 PDF、解析与冻结、术语感知的多供应商翻译、原坐标回填、QA 审计；审计通过后再分叉为两种交付物。

- 文本块携带页码、`bbox`、字号和语义角色；公式、引用与 URL 会先变成可逆占位符，保护区不进入自由改写。（`pdf_zh_translator/pdf_layout.py:370-426`；`pdf_zh_translator/pdf_layout.py:19545-19601`）
- 术语按当前文本块注入提示词；标题、正文、图注使用结构提示，多供应商层共享同一约束。（`pdf_zh_translator/translators.py:543-647`；`pdf_zh_translator/translators.py:832-923`）
- 请求同时受批次数和字符数约束；JSONL 缓存、占位符校验与单块回退让同一输入可以确定性重放。（`pdf_zh_translator/translators.py:92-167`；`pdf_zh_translator/translators.py:390-450`）
- 渲染只擦除可替换文字，再用 CJK 字体链在原 `bbox` 内排版；页面尺寸、图像、矢量、链接与源公式继续沿用原页。（`pdf_zh_translator/pdf_layout.py:1338-1393`；`pdf_zh_translator/pdf_layout.py:8012-8042`；`pdf_zh_translator/pdf_layout.py:25343-25370`）
- QA 在隔离子进程中复核候选，纯中文与双语 PDF 由同一翻译结果生成并一起进入快照保护。（`app/api/papers.py:1763-1836`；`app/api/papers.py:2767-2802`）

## 2. QA 循环图

文件：`docs/assets/qa_loop.svg`

中文图注：每轮 QA 都重跑同一组检测器；候选只有严格减少错误与问题总数才会被接受，否则立即恢复修复前快照。

English caption: Every QA pass reruns the same detectors; a candidate is accepted only if it strictly reduces the error-and-issue score, otherwise the pre-repair snapshot is restored.

信息结构：围绕中心原则“只接受严格变好”顺时针组织检测、缺陷清单、有界修复和候选比较；“接受后复检”回到环内，“持平或变差”则从环外回滚并停止。

- 检测覆盖漏翻、保护区变更、重叠与空页、图片/矢量/公式缺失、视觉墨迹、字号、表格与引用；术语审计当前只给提示，不进入问题分。（`pdf_zh_translator/pdf_layout.py:2706-2718`；`pdf_zh_translator/page_inspector.py:2295-2558`；`app/api/papers.py:2890-2922`）
- QA 输出稳定的 `TranslationIssue[]`；规划器只允许 `accept`、`repair_layout`、`retranslate`、`stop` 四类登记动作。（`app/services/quality_agent.py:11-87`；`docs/adr/0001-independent-translation-quality-loop.md:68-75`）
- 迭代模式默认最多 4 轮，API 明确限制为 1–8；每轮都从隔离检测开始。（`app/api/papers.py:1201-1241`；`app/api/papers.py:2306-2382`）
- 修复前同时快照 mono 与 dual PDF；修复后重跑检测器，并按 `(error 数, issue 总数)` 做字典序比较。（`app/api/papers.py:2445-2475`；`app/api/papers.py:2746-2788`）
- 候选持平或变差会被原子回滚并停止无进展循环；外层恢复尝试另行保留跨尝试全局最佳。（`app/api/papers.py:2476-2494`；`app/api/papers.py:2059-2206`；`app/api/papers.py:2791-2802`）

## 3. 独立审查—修复图

文件：`docs/assets/adversarial_review.svg`

中文图注：修复方只写候选、审查方只读挑错，双方都不能自行放行；快照比较保留全局最佳，最终由 strict gate 决定是否交付。

English caption: The repairer may write candidates while the reviewer may only inspect and report issues; neither can self-approve, with snapshots preserving the global best and the strict gate issuing the final verdict.

信息结构：上半区用对峙构图明确“可写修复方”和“只读审查方”的权限分离，中间盾牌强调禁止自评；下半区把快照保险库、候选裁决和发布 strict gate 排成三段裁判席。

- ADR 明确要求视觉或模型审查只能提出问题、不能直接修改 PDF；规范输出是独立的 `TranslationIssue[]`，成为阻断项前还要经过确定性验证。（`docs/adr/0001-independent-translation-quality-loop.md:20-27`；`docs/adr/0001-independent-translation-quality-loop.md:56-62`）
- 当前 QA 通过隔离子进程读取 `original_path` 与 `translated_path`，仅把检测结果反序列化为问题清单；公开验收产物也使用 `qa-readonly` 角色记录。（`app/api/papers.py:1763-1836`；`docs/assets/comparison/NOTES.md:56-61`）
- 修复方只能执行代码登记的动作；布局修复写候选 mono PDF，并在需要时重建 dual PDF，不能自行判定通过。（`app/services/quality_agent.py:11-87`；`app/api/papers.py:2421-2470`；`app/api/papers.py:2767-2780`）
- 修复前快照负责单轮原子回滚；外层恢复循环持续保存 `best_result`、`best_snapshots` 与 `best_score`，预算耗尽也恢复全局最佳。（`app/api/papers.py:2445-2494`；`app/api/papers.py:2059-2206`；`app/api/papers.py:2783-2802`）
- 单篇 strict pass 要求 0 error、0 actionable warning、视觉分不低于 0.55；发布 gate 还检查报告齐全、来源、布局轴与回归，并默认要求至少 20 篇 strict pass。（`scripts/classic_benchmark.py:185-200`；`scripts/classic_benchmark.py:1363-1529`；`scripts/classic_benchmark.py:1566-1567`）

## 机制区标题候选

### 候选一：从原页到可信译文

副标：结构先冻结、正文再翻译，最后由独立 QA 与 strict gate 验收。

### 候选二：只接受严格变好的翻译

副标：每次修复都重跑同一组检测器；持平或变差，立即回滚。

### 候选三：修复者不能给自己打分

副标：可写的修复方与只读审查方分权，是否交付由证据裁决。

推荐候选一“从原页到可信译文”。它能同时覆盖整体架构、QA loop 和独立审查三张图，又延续标杆页面“对象 + 结果”的直接标题感；候选二和三更适合作为后两张图各自的页标题。

## 旧图处置建议

`docs/assets/mechanism.svg` 不再适合承担 README 或主页首屏总图：它把架构、循环和发布门禁压在同一画布，字号与路径密度都高于快速阅读阈值。建议由本次三图替代公开机制区，其中 `arch_overview.svg` 就是新的总图；旧图暂时保留为“完整技术细节附录”和兼容旧引用，待 README/主页全部切换后再决定是否改名归档，不要继续与新总图并列展示。
