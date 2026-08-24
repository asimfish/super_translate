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

## pdf2zh 基线运行记录

- 命令：`cd /tmp/st-compare && nice -n 19 <内部环境>/.venv/bin/pdf2zh <临时目录>/src/attention_original.pdf`（原文为 classic20 attention.pdf 的 /tmp 副本，避免 iCloud 重复读）
- 启动时间：2026-08-25 04:59（本地）。
- 结果：**失败放弃（超时，无任何输出）**。运行 70+ 分钟后 `pdf2zh_run.log` 仍为 0 字节、当前目录无输出 PDF，任务中止。
- 原因诊断：进程未真正开始翻译——ps 显示启动 12 分钟时累计 CPU 仅 0.62 秒（状态 SN，I/O 阻塞），卡在 Python 依赖导入阶段；源仓库 `.venv` 位于 iCloud 同步盘（Desktop），冷文件按需回迁导致 import 无限期拖延。旁证：并行的 `pdf2zh --version` 同样 70+ 分钟未返回（累计 CPU 0.84 秒）。无报错输出可录（日志空）。
- 结论：三篇均无 `pdf2zh_p<N>.png`；README/主页引用时请用「pdf2zh 基线因环境 I/O 限制未能在时限内产出」的保守表述，不做效果贬损。

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

- 许可重切后 comparison 目录总计 **7.7MB**（上限 25MB）：attention 432KB（4 crop）、resnet 488KB（2 crop）、cosmos 4.3MB（4 整页 + 2 crop）、qwen_robotworld 2.5MB（4 整页 + hero.gif 600KB）。
- 最大单图 `cosmos/crop_figure_ours.png` 1.03MB（上限 1.5MB）。全部 17 张 PNG + 1 张 GIF 达标。
