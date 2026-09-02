# Changelog

本文件记录项目的用户可见变更，格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [SemVer](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Added

- Agent Skill 模式：`skills/paper-translate/` 可直接软链进 Claude Code / Cursor 使用，含环境自检与「翻译 + 视觉检查」一键脚本
- 多用户账号体系：登录、按用户隔离的论文库、加密存储的每用户翻译供应商 API key（AES-GCM）
- 供应商模型目录与选型指引（DeepSeek / Kimi / OpenAI / Anthropic / GLM）
- 大文件断点续传上传（分块 SHA-256 校验、幂等续传、会话过期回收）
- 译后视觉检查器（`inspect` 子命令）与经典论文基准评测工具（`scripts/classic_benchmark.py`，fetch/translate/evaluate/report/gate 五步、`--isolate` 每篇进程隔离）
- worldmodel10 基准集与公开对比展示页
- 质量代理服务：翻译后自动诊断-修复循环，瞬时故障有界重试后进入持久恢复队列
- 架构文档与 ADR（`docs/ARCHITECTURE.md`、`docs/adr/`）

### Changed

- 翻译在隔离子进程中运行，QA 验证同样隔离，单篇故障不再拖垮服务进程
- 页面预览改用 WebP（约 90KB/页），重复图片流去重，译文 PDF 体积显著缩小
- 正文优先选用宋体系 CJK 字体，阅读观感接近纸质排版
- 响应式 Web 界面改进：移动端长标题收纳、可访问性优化

### Fixed

- Mistral 7B 横向实测暴露的四处引擎缺陷（[#2](https://github.com/asimfish/super_translate/issues/2)）：无框线两列小表按「列边严格对齐 + 数值列」几何证据识别为表格，不再重排为正文；回填加宽与下扩以矢量框线为硬边界，且加宽不越过所在文本列右缘 1.5em；列表项续行恢复源文件悬挂缩进；译文与原文完全相同的块保留原始字形
- `inspect` 新增两个检测器：`table_cells_reflowed`（源表行含多格、译文塌成散文）与 `text_outside_frame`（译文越出包围矢量框）
- `--api-mode cache-only` 新增 `--cache-segments`，可回放 live 运行写出的缓存（粗体导语与正文分段存储），零 API 成本重渲
- 大批量版式回归修复（classic20 / worldmodel10 两轮基准驱动）：公式段落缩放、跨页译文分配、表格结构保持、图注与浮动体环绕、行内公式碎片、双栏切分误判等
- QA 误报治理：作者行不再参与公式对齐检查、引用样例豁免、URL 脚注回显豁免
- 翻译任务僵尸状态在启动时被识别并归位（重排队或标记失败）
- 数学符号字体回退，公式中不再出现 notdef 方框

## [0.3.2] - 2026-07-20

### Fixed

- 双语 PDF 改为整文档插页组装，消除字体重复嵌入导致的体积膨胀
- 修复整节漏翻、作者墙误保留与约 4 倍输出膨胀问题

## [0.3.1] - 2026-07-20

### Fixed

- 公式 QA 识破脚本记号回退渲染，消除一类误报
- 占位符错乱的文本块先逐块重试再降级，减少整篇失败

## [0.3.0] - 2026-07-20

首个开源发布版本。

### Added

- AGPL-3.0 许可证、Docker + Caddy 部署与教程、CI
- 工作区级 token 隔离、可配置代理信任、API 文档开关

### Changed

- 表格保持端到端加固：游离单元格、标签碰撞、相互重叠块均保留原排版
- 术语匹配改为词边界匹配，同时作用于提示词与译后审计

### Fixed

- 公式贴图裁剪不再带出邻行字形；跨行连字符（如 vision-language）正确保留
- 表格数值篡改检测；URL 正文、公式禁区行、换行图注不再误报

[Unreleased]: https://github.com/asimfish/super_translate/compare/v0.3.2...HEAD
[0.3.2]: https://github.com/asimfish/super_translate/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/asimfish/super_translate/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/asimfish/super_translate/releases/tag/v0.3.0
