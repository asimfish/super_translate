# 对比素材制作笔记

生成日期：2026-08-25。渲染工具：`pdftoppm`（整页 `-r 120`，局部放大 `-r 240` + `-x/-y/-W/-H` 区域裁剪）；hero.gif 用系统 python3 + PIL。原文/译文页面几何完全一致（保版式翻译），同一裁剪框对两侧 PDF 直接可比。

## 许可裁定（协调者，2026-08-25）

按 `benchmarks/classic20/README.md` 许可政策：**仅 CC 许可论文（showcase_ok）公开展示完整翻译页**，arXiv 非独占许可论文仅展示局部 crop 与聚合指标。

- **attention / resnet**（arXiv 非独占）：整页图撤下（存档于内部 holdback，未入库），仅保留局部 crop。
- **cosmos / qwen_robotworld**（CC BY 4.0，showcase_ok=true）：整页对比 + hero.gif。
- **qwen_robotworld** 为补充篇目：p1（标题+摘要+管线彩图）、p4（大型数据混合图+图注+行内数学符号），选页时目检确认翻译与版式质量。
- 后续升级：`showcase_cc` 组六篇 CC 经典（llama/mistral/mamba/dpo/cot/vllm）翻译产物就绪后补充为完整页对比。

## 选篇与选页理由

### attention（Attention Is All You Need，15 页）·仅 crop 入库
- 许可裁定后仅保留 crops：`formula`（p4 公式(1) `Attention(Q,K,V)=softmax(QK^T/√dk)V` 区域）、`figure`（p4 图2 缩放点积/多头注意力结构图 + 图注）——一页同时验证图形与公式双重零破坏。
- 整页渲染（p1 标题摘要页、p4）与旧 hero.gif 已按政策撤下，存档于内部 holdback，未入库。

### resnet（Deep Residual Learning，12 页）·仅 crop 入库
- 入选理由：CVPR **双栏排版**代表（attention/cosmos/qwen 均为单栏），双栏保持是核心卖点之一；候选 ddpm 为单栏故让位。
- 许可裁定后仅保留 crop：`twocol`（p1 上半部：中文标题+作者+左栏摘要/右栏图1 训练曲线）。
- 整页渲染（p1、p5 密集表格页）已按政策撤下，存档于内部 holdback。

### qwen_robotworld（Qwen-RobotWorld Technical Report，25 页）·CC BY 4.0 整页
- 补充篇目（协调者渲染并目检选页）：**p1** 标题+摘要+管线彩图（品牌元素、链接、arXiv 侧边栏完整保留）；**p4** 大型数据混合结构图 + 图注翻译 + 行内数学符号（s_t、a_t、s_{t+1}）保留。
- hero.gif：p1 original↔ours 1.2s 轮播，880px 宽，600KB（替代原 attention hero）。

### cosmos（Cosmos World Foundation Model Platform for Physical AI，75 页）
- 入选理由：超长文档（75 页、43MB 原文）压力样本，NVIDIA 世界模型技术报告。
- **p1**：封面页（NVIDIA logo + 标题 + 摘要），品牌元素与版式完整保留。
- **p34**：位于文档中后段，图17 视频帧网格（4B/12B/5B/13B 四行生成结果）+ **Prompt 段落译文** + 加粗图注 + 正文/小节标题，验证长文档深处质量不衰减。
- crops：`figure`（p34 图17 下半组：5B/13B 网格 + Prompt 译文 + 图注）。

## 2026-08-26 用户反馈修订

1. **qwen_robotworld p4 trim 收窄**：用户指出页面底部编号列表（Task Goal Layer 等 5 条）的粗体导语在译文中丢失加粗、列表被排成段落——目检属实，属引擎待优化项（与此前撤下 p1 的摘要粗体错位同类）。源 PDF（/tmp/paper-china-repair-30-r3）已被系统清理无法重渲其它页，故将 trim 裁剪框收窄至干净上半区：整页 120dpi 图 y<900（原文正文段结束 y891、译文 y860，切线落在两侧公共空白带），内容 bbox 并集 + 24px 边距，同框裁两侧，最终 806×847。上半区（结构图 + 粗体图注 + 正文段）逐项目检忠实。主页文案同步从「整页渲染」改为「原样渲染」。
2. **新增迷你切片条**（主页滑块左列「切片速览」，填补左列空间）：
   - `cosmos/crop_title_*`：p1 论文大标题（从已入库整页图裁，box x85-867 y127-182），NVIDIA 品牌绿与字重保持；
   - `attention/crop_heading_*`：p4「3.2.2 Multi-Head Attention → 3.2.2 多头注意力（Multi-Head Attention）」小节标题（240dpi 重渲自 classic20_final_r2 PDF，box x340-1065 y2088-2144），编号与粗体保真；
   - `attention/crop_formula_tight_*`：crop_formula 内容紧缩版（box x445-1440 y33-140），窄栏可读。
   全部两侧同框、成对像素级可比。

## 2026-08-26 DPO 横向实测（showcase_cc 首篇 + pdf2zh 基线补齐）

用户要求：滑块右侧换一页能同时体现公式、大小标题、加粗的页面；且「与其他方法对比」要有其他工具的真实翻译效果。解法：在本地快盘（`~/Code`，非 iCloud）现场翻译 showcase_cc 组的 DPO（arXiv 2305.18290v3，**CC BY 4.0**，abs 页 license 链接 2026-08-26 核验），并用同一份原文跑通 pdf2zh 基线。

### SuperTranslate 侧
- 命令：`.venv/bin/python -m pdf_zh_translator translate dpo.pdf dpo-mono.pdf --api-mode deepseek --api-key-env PAPER_CHINA_DEEPSEEK_API_KEY --preserve-graphics-text`（模型默认 `deepseek-v4-pro`，reasoning effort high；引擎代码 = 源仓库当前工作区）
- 结果：27 页，**216 块翻译 / 301 块保护跳过**，1 块因供应商反复破坏保护占位符而按策略保留原文；全程约 24 分钟（含限速重试与逐条回退）。
- inspect 审计（`dpo.inspect.json`）：全篇 **3 处报告**——p17 展示公式横移 16pt（警告）、p22 附录 GPT-4 样例框 2 处 `font_size_drift`（错误，样例框内容为模型输出的重复字符退化）。**展示页 p1 / p4 零缺陷**。本次为单遍翻译，未跑 quality 档迭代 QA 修复循环（跑循环预期可修复 p22）。
- 产物：`local:.local-work/showcase/dpo-mono.pdf`（61.5MB，未入库）。

### pdf2zh 基线侧
- 环境：本地快盘全新 `uv venv`（Python 3.13），`pdf2zh v1.9.11`；修复已知依赖冲突 `uv pip install "tencentcloud-sdk-python==3.0.1200"`。
- 命令：`.venv/bin/pdf2zh dpo.pdf`（默认 Google 翻译后端），约 1.5 分钟完成，产出 dpo-mono.pdf / dpo-dual.pdf。
- 目检缺陷（p1/p4/p5）：「Abstract」→「抽象的」且丢标题粗体；标题重排左对齐（原文居中）；arXiv 侧边栏竖排字符乱序；加粗导语（Deriving the DPO objective. / What does the DPO update do?）粗体丢失；Eq./引用的 hyperref 链接框脱离文字漂浮成红绿空框（「Bradley-Te□y」「P□ackett-Luce」）；p5 定义 1 / 引理 1 / 引理 2 整段未翻译。展示公式 (4)–(7) 本体保留（其宣称能力）。
- 说明：上一条 2026-08-25 的失败记录归因于 iCloud I/O，本次换本地盘后一次通过，两条记录都保留以供追溯。

### 入库素材
- 整页：`dpo/original_p{1,4}.png`、`ours_p{1,4}.png`（120dpi 原样）+ `*_trim.png`（滑块用，p1 框 x6-866 y108-1260，p4 框 x155-1020 y100-1273，均为两侧内容 bbox 并集 + 24px）。
- 三方整页：`trio_p4_{original,pdf2zh,ours}.png`（三方内容 bbox 并集同框 x155-1020 y98-1273）。
- 细节三联（240dpi）：`crop_abstract_*`（摘要标题 + 完整第一行，三方统一固定框 x436-1608，顶部 64px 白边避开页面角标）、`crop_boldlead_*`（p4 加粗导语前三行，顶部同样加白边）。
- 已知小瑕疵（引擎待优化）：短标题在原 bbox 内偏右放置——「摘要」未严格居中（原文 Abstract 居中）。字重正确、术语正确，文案只声明这两点，不声明居中。
- pdf2zh 整页仅入库 trio p4 一张；其余 pdf2zh 产物留在本地工作区，需要时可按上述命令复现。

## pdf2zh 基线运行记录（2026-08-25，失败存档）

- 命令：`cd /tmp/st-compare && nice -n 19 <内部环境>/.venv/bin/pdf2zh <临时目录>/src/attention_original.pdf`（原文为 classic20 attention.pdf 的 /tmp 副本，避免 iCloud 重复读）
- 启动时间：2026-08-25 04:59（本地）。
- 结果：**失败放弃（超时，无任何输出）**。运行 70+ 分钟后 `pdf2zh_run.log` 仍为 0 字节、当前目录无输出 PDF，任务中止。
- 原因诊断：进程未真正开始翻译——ps 显示启动 12 分钟时累计 CPU 仅 0.62 秒（状态 SN，I/O 阻塞），卡在 Python 依赖导入阶段；源仓库 `.venv` 位于 iCloud 同步盘（Desktop），冷文件按需回迁导致 import 无限期拖延。旁证：并行的 `pdf2zh --version` 同样 70+ 分钟未返回（累计 CPU 0.84 秒）。无报错输出可录（日志空）。
- 结论（当时）：三篇均无 `pdf2zh_p<N>.png`。**2026-08-26 已在本地快盘补齐 DPO 基线（见上节）**，此失败记录仅作环境教训存档：跑基线务必避开 iCloud 同步盘。

## 可引用数字（全部带证据指针）

### classic20 批次（r2，20 篇全部完成）
- 批次状态：20/20 papers `status: completed`，批次 `status: completed`
  证据：`internal:classic20_final_r2_09f6992/batch-state.json`
- 翻译模型 `deepseek-v4-pro`，quality 模式，QA iterative（max 4 passes），`preserve_graphics_text: true`
  证据：同目录 `round.json`
- **attention**：15 页，visual_score **0.9041**（risk: low），issue_count 7（全部 `high_risk_layout`，无 error），strict_pass **True**，翻译耗时 135.1s（batch-state.json elapsed_seconds）
  证据：`.../reports/attention.json`
- **resnet**：12 页，visual_score **0.8621**（risk: high），issue_count 11（全部 `high_risk_layout`，无 error），strict_pass **True**，翻译耗时 240.2s
  证据：`.../reports/resnet.json`
- 注：`high_risk_layout` 为版面风险提示（非错误）；两篇 error_count 均为 0、legacy_pass 均为 True。

### Cosmos（r9 正式 QA，只读复核）
- **75/75 页、793 个对象、issue_count 0、error_count 0、passed: true**（policy `object-qa-2026.08-v1`，role `qa-readonly`）
  证据：`internal:paper-china-stabilize-proof/cosmos-r9-formal-v2/summary.json`
- QA 运行窗口：2026-08-24T17:38:27Z → 17:55:03Z，exit_code 0
  证据：`internal:paper-china-stabilize-proof/cosmos-r9-formal-v2.metadata.txt`
- 原文/译文 PDF：`internal:paper-china-repair-30-r3/worldmodel10/papers/cosmos.pdf`（43MB）/ `.../translations/cosmos-mono-r9.pdf`（133MB），sha256 已列入 summary.json 的 input_fingerprints。

## 体积

- 2026-08-26 加入 dpo 后 comparison 目录总计 **14MB**（上限 25MB）：dpo 4.0MB（17 张：4 整页 + 4 trim + 3 trio + 6 detail）、cosmos ~4.4MB、qwen_robotworld ~2.5MB、attention ~0.6MB、resnet 488KB。
- 最大单图 `cosmos/crop_figure_ours.png` 1.03MB（上限 1.5MB）。

## 2026-08-26 晚 DPO 素材升级（200dpi 重渲 + 大模型直译列 + 定义/引理三联）

用户反馈三点，对应处理：

1. **公式观感模糊**：核查 240dpi 渲染，原文与译文公式锐度一致（公式冻结、矢量原样），模糊源于展示图仅 120dpi——retina 屏 ~800px 显示位需要 ~1600 物理像素，120dpi 图被放大近一倍。DPO 整页素材（original/ours 全页 + trim + trio 三方）全部重渲为 **200dpi**（`pdftoppm -r 200`，union-trim 同框），detail crops 维持 240dpi。qwen/cosmos 源 PDF 在内部仓库，暂维持 120dpi（后续同批升级）。
2. **新增「直接丢给大模型」列**（`trio_p4_rawllm.png`）：`pdftotext -f 4 -l 4 -layout` 提取 p4 全页文本，单轮直译（同后端 deepseek-v4-pro，提示词「请把这页论文翻译成中文：」，输出 2339 字符，存 `local:.local-work/p4_raw_llm.md`）。呈现方式：marked 解析 markdown + KaTeX auto-render（解析前保护 `\(\)\[\]` 定界符，避免 marked 吞反斜杠误伤模型输出），排成 1441×1959（与 trio 列同尺度）页面后 Playwright 截图。观察：正文流畅、公式 (4)(5) 可渲染；(6)(7) 模型自身输出的 LaTeX 破损退化为源码、`_` 触发整段误斜体；版式/双栏/页码/引用锚点全部丢失。渲染管线曾误吞定界符（marked 把 `\(` 转成 `(`），已修复后重截——失败处均为模型自身缺陷，非管线伪影。
3. **细节③ 定义与引理（p5）**：`crop_lemmas_{original,pdf2zh,ours}.png`，240dpi 固定框 x300-1790 y1930-2365。pdf2zh 将 Definition 1 / Lemma 1 / Lemma 2 正文整段留英（标签「定义 1.」却翻了）；我们全部译出、粗体标签保留、行内数学原样。诚实备注：我们的定义 1 因行内公式锚定原位，中文语序有迁就（「当且仅当存 …… 在某个函数 f」），内容正确可读，属已知取舍，未在展示文案中回避。
4. **机制图**：`arch_overview.svg` v2——新增术语库节点（绿色，1,066 条 · 用户可扩展，「逐块注入」箭头入翻译节点）与 Agentic 修复循环回环（金色虚线，QA 未过检 → 回翻译节点，标注「只接受严格变好 · 否则回滚快照」），画布 520→640 高。

体积：comparison/ 由 14MB 增至 18MB（200dpi 升级 + rawllm 列 + lemmas 三联）。

## 2026-09-02 Mistral 7B 第二篇横向实测（showcase_cc 第二篇 + 已知缺陷如实入册）

选篇理由：与 DPO 互补——几乎没有公式，难点换成专名（模型/机构/基准名）、密集结果表、加粗导语列表和两页参考文献。许可 CC BY 4.0（arXiv 2310.06825v1 abs 页 license 链接核验）。

**SuperTranslate 运行**
- 命令：`.venv/bin/python -m pdf_zh_translator translate mistral.pdf mistral-mono.pdf --api-mode deepseek --api-key-env PAPER_CHINA_DEEPSEEK_API_KEY --preserve-graphics-text`（模型默认 deepseek-v4-pro）。
- 9 页：59 块翻译 / 148 块保护跳过，约 2 分钟（9 个批次，无限速重试）。
- `inspect mistral.pdf mistral-mono.pdf`：**0 issue**。

**pdf2zh 基线**
- 命令：`.venv/bin/pdf2zh mistral.pdf`（v1.9.11，默认 Google 后端），同机 1 分 12 秒，产出 mistral-mono.pdf / mistral-dual.pdf。
- 观察缺陷：p1 标题「Mistral 7B」音译为「米斯特拉尔7B」；作者栏丢粗体、人名从单词中间断行（De/vendra、G/uillaume、P/ierre）、逗号变顿号；p3 加粗导语列表粗体丢失、「0-shot」与「5 次」术语不一致、脚注 4 整条未译；p4「表 2:」图注与「规模和效率。」导语粗体丢失、行首标点（「，」顶格）；p8-9 参考文献：每条标题译成中文、整节合并成一段、人名改写（Jianfeng Gao → Jianfeng Taka）、「Radfo/rd」断词。

**SuperTranslate 已知缺陷（inspect 全部漏报，如实展示于主页与 README）**
1. p5 表 4（Guardrails / MT Bench，仅两条横线的无框线小表）未被识别为表格：单元格被当正文重排，「护栏机制 MT Bench 无系统提示 / 6.84 ± 0.07 Llama 2 系统提示 …」列对齐全乱。pdf2zh 此处正确（保留表格结构与粗体表头）。
2. p5 系统提示框（矢量矩形）内译文超出右边框约一个字宽。
3. p3 首条 bullet（常识推理）首行超出右栏边界、续行丢失悬挂缩进回到左边距。
4. p1 标题「Mistral 7B」内容未变但字体族由 Times 粗衬线变为 CJK 字体链的无衬线（标题块被当作译文重排）。
根因初判：(1) 表格检测依赖框线/网格密度，两条 booktabs 横线 + 三行文本的表未达阈值；(2) 回填时按 bbox 宽度断行，但提示框 bbox 取的是文本 bbox 而非包围矩形；(3) 列表项续行缩进未继承首行的 hanging indent；(4) 纯 ASCII 标题块也进入了 CJK 字体链。QA 盲区：inspect 目前检查字号漂移、公式位移、覆盖率，不检查「原文为表格结构 / 译文列对齐」与「文本是否越出所属矢量框」。→ 已开 GitHub issue 跟进。

**素材**
- 整页 200dpi：`mistral/original_p{1,4}.png`、`ours_p{1,4}.png` + `*_trim.png`（union-trim 同框，p1 box 10,180-1443,2036；p4 box 260,181-1444,2123）。
- 细节三联 240dpi 固定框 + 顶部 48px 白边：`crop_title_*`（x180-1860 y200-820）、`crop_refs_*`（x180-1860 y190-900）、`known_table4_*`（x180-1860 y1300-1900）。
- 未选用 p3 列表区作为优势细节：我们的粗体与术语一致性确实更好，但同一裁剪框内会露出上面第 3 条缺陷，作为「优势」展示不诚实；缺陷本身已在文字中记录。

体积：comparison/ 由 18MB 增至约 24MB。

## 2026-09-03 issue #2 修复轮：四处引擎缺陷 + 两个 QA 检测器

**根因（插桩 `prepare_translation_units` 逐块打印后确认）**
1. 表 4：PDF 原始提取为两个块——表头块（1 行 2 格）与表体块（3 行 × 2 格）。`record_is_table` 要求 ≥2 行有「宽间隙」（阈值 max(8, 1.6×行高)≈15pt），表体三行间隙为 21.3 / 12.0 / 13.9pt，只有一行过线；单行判据又要求 ≥3 格。于是两块都判为正文，随后 `merge_paragraph_blocks` 合并成 8 行段落。但几何证据其实很强：右列三行 x0 完全一致（454.6），左列居中对齐（中心 400.2±0.1），右列全是数值。
2. 提示框：`_expand_single_line_body_bbox` 的 `compact_two_line_body` 分支（两行、宽 ≥ 页宽 55%）把右边界借到 `page_width − margin` = 611.2，只用同行右侧的**文本块**做边界，不看矢量框线；两行译文放不进一行时仍然把块加宽，于是首行流到页边。
3. 列表续行：渲染器所有行都对齐到 `rect.x0`，没有悬挂缩进概念；同一块还被 (2) 加宽过。
4. 标题：译文与原文完全相同（"Mistral 7B"）的块仍被擦除并用 CJK 字体链重排。

**修复（`pdf_zh_translator/pdf_layout.py`）**
- 新增 `_record_has_aligned_value_column_rows`：≥3 行、每行格数一致（≤4）、每列 x0/x1/中心任一边跨行极差 ≤3pt、列间每行正间隙 ≥4pt、存在「测量值列」（小数/%/±/千分位/单位，排除纯小整数与公式编号，避免误伤目录页码与编号公式）、无整句行。接入 `record_is_table`。表头提升放宽：表体是单个 ≥3 行表块时视同两个单元格邻居。
- 新增 `_page_frame_rule_bboxes`（item 级提取细线与描边矩形四边，去重闭合路径的重复边）与 `_frame_right_limit`；`_expand_single_line_body_bbox` 加宽止于最近的右侧竖线、不越过所在文本列右缘 1.5em，两行块放不进一行时保持源 bbox；横线作为 `_expand_multiline_block_bbox` 与 `_cascade_expand_page_items` 的障碍（穿过候选块自身行区的细线视为下划线而非框线，不计入）。
- 新增 `_list_hanging_indent`：项目符号/编号开头且源第二行 x0 比首行大 3pt～max(24, 3em) 的块，`break_lines` 首行全宽、续行减去缩进，续行渲染 rect 右移；`translated_text_fits` 同步。
- 新增 `_translation_repeats_source`：译文去空白后与原文相同且不含 CJK 的块不进入 page_items（不擦除、不重排），warning 汇总计数。
- `CacheOnlyTranslator(segment_source_styles=)` + CLI `--cache-segments`：live 缓存把粗体导语与正文分开存，旧 cache-only 模式按整块查键必然 miss；现在可零成本回放。golden 夹具缓存仍按整块存，默认行为不变。

**QA（`pdf_zh_translator/page_inspector.py`）**
- `table_cells_reflowed`：源页 ≥2 条对齐横线（≥60pt，不再要求 ≥40% 页宽）围成的带内，比较源/译文的「行 × 格」结构：格数上限下降、多格行数减少 ≥2、译文出现 ≥85% 带宽的整行、或第二列 x0 跨行极差由 ≤3pt 变为 >8pt（两端对齐散文的假单元格不会跨行对齐）→ error。位于保护区内的表交给 `preserved_ink_mismatch`。
- `text_outside_frame`：源页成对竖边（≥40pt 宽、纵向重叠 ≥80%、内有文字、不在图区）构成的框，译文行中心在框内而 x1/x0/y1 越出 >1.5pt → error，报越出量与文本片段。
- 验证：旧 p5 产物报 `text_outside_frame`（越右 107.7pt）与 `preserved_ink_mismatch`（表 4 现被识别为保护区）；以空排除集直接调用时 `table_cells_reflowed` 亦触发；修复后产物两项均安静。
- golden 门禁抓到一次误报（`hdflow_p5_formula_explanations.pdf`）：一张图的坐标框（x=291–715，超出页宽）被当成文本框，其下方正文行的字形盒越过框底 2pt。修正：框必须在页面内、内部不能有 ≥2 个非细线图形（曲线/填充/图像），且越出量以**源页文字与同一框的贴合程度为基准**——只计译文比源文多越出的部分（字形盒本就会伸出下划线一个下伸部）。修正后该页安静、Mistral 旧产物仍报 107.7pt。两个新 code 有意**不**加入 `INSPECTOR_ISSUE_CODES` 排除集，让 204 页 golden 门禁直接守着它们。

**验证**
- `tests/test_frames_tables_lists.py` 24 个单测（表格判据正/反例、框线提取与三条扩展路径、悬挂缩进、原文复现、分段缓存回放、两个检测器的合成 PDF 正/反例）。
- 核心套件 926 通过（translators / page_inspector / pdf_layout_preserve / layout_fix / native_engine / pdf_layout_e2e / gutter / cli_layout）；golden 回归见下方结果行。
- live 重跑 Mistral：58 块翻译 / 151 块保护（+3：表 4 三块；−1：标题保留原字形），inspect 0 issue；p1/p3/p5 目检四处均正常。
- 调试回路：`.local-work/dev/dump_blocks.py`（逐块分类 + 页面矢量线）与 `trace_insert.py`（追踪 bbox 在各扩展步的变化），配合单页 PDF + `--cache-segments`，单次迭代 ~10s。
