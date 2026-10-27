# 项目展示：文案、视频素材与当前支持

README、[在线展示](https://asimfish.github.io/super_translate/)和[介绍幻灯片](https://asimfish.github.io/super_translate/slides.html)采用同一套叙事。本文记录素材出处及当前功能范围，方便后续维护。

## 视频与图文

- [哔哩哔哩：让 Agent 帮你成为 Native Reader，零压力读论文](https://b23.tv/dNg3JYK)，约 3 分钟。
- [小红书图文](https://xhslink.cn/o/95CUF7S0njW)。
- [原始横版视频封面](assets/promo/v15/cover-landscape.png)：来自作者提供的 `SuperTranslate-V15-final.zip` 中的 `super-translate-v15/cover-landscape.png`，保持原图。V15 制作于 2026-10-12。
- 文案参考同一素材包的 `unified-copy.md` 与 `posts/xiaohongshu.md`，并按当前主分支更新客户端信息。

GitHub README 使用可点击封面跳转视频；在线展示和幻灯片也提供视频、图文入口，不要求自动加载第三方播放器。完整成片、配音和开发补丁不进入源码仓库。

## 口号与六个痛点

主口号：**让 Agent 帮你成为 Native Reader，零压力读论文。**

一句话介绍：**开源的 Native 科研论文翻译工具。** 目标是无损的科研原生专业翻译，以版式对齐原文、专业科研术语和译后质检为持续改进方向。

1. 公式乱码、变成源码：保护数学片段及原始位置。
2. 双栏变单栏、图表错位：按原页坐标回填译文。
3. 标题、粗体、斜体丢失：识别结构并检查样式。
4. 正文漏翻或误改图中文字：区分翻译范围，逐项质检，尝试有界修复。
5. 术语歧义、译法不一致：按段落注入术语，按学科、领域、论文维护词库。
6. 来回切工具：Web、Agent Skill、原生 macOS 悬窗、Zotero 四种入口。

“无损”是产品目标，不能据此宣称任意论文均已通过验收；具体结果应附论文、版本和检查范围。

## 四种入口与当前安装范围

- **Web 工作台**：上传 PDF、查看进度、同步阅读原文和译文。[启动教程](TUTORIAL.zh-CN.md)。公开主页是效果展示，实际翻译需要自己的服务与模型 API Key。
- **Agent Skill**：在 Claude Code / Cursor 中调用本地引擎，执行翻译与质量检查。[安装说明](../skills/paper-translate/SKILL.md)。
- **原生 macOS 悬窗**：SwiftUI、半透明与置顶、拖入 PDF、切换供应商 / 模型、任务进度、文档整理和外部 PDF 阅读器打开。macOS 13+；当前源码已在主分支，需自行构建并连接服务。安装脚本提供登录自启，构建产物为本机签名，尚无公证的通用 App 安装包。[构建与安装](../desktop/README.md)。
- **Zotero 0.2.2 预览版**：右键、论文侧栏及 PDF 阅读器入口；百分比和进度条、取消任务、模型切换、共享文档查询与历史。只新增 `中文版《论文名字》` 附件。限定 Zotero 10.0.x，已实测 macOS / Zotero 10.0.4、10.0.6；其他版本与平台尚未原生验收。[下载 XPI](https://github.com/asimfish/super_translate/releases/download/zotero-v0.2.2/supertranslate-zotero-0.2.2.xpi) · [安装教程](ZOTERO.zh-CN.md)。

Web、悬窗与 Zotero 共用服务账号、供应商设置和文档库。Skill 可直接运行本地引擎，不应描述为自动共享服务端任务。插件及悬窗不会替用户创建翻译服务；首次使用先[启动服务、创建账号](ZOTERO.zh-CN.md#没有服务先在本机启动)。

API 设置支持 DeepSeek、Kimi、OpenAI、Anthropic、智谱、Gemini、Qwen、豆包，可刷新账号的可用模型目录；不承诺每个账号都有各家的所有模型。模型调用会向所选供应商发送翻译所需段落及命中的术语。内置科研词表之外，网页可维护学科、领域和单篇论文的词库，支持化学 / 生物模板及 CSV / JSON 导入导出。[术语管理](TERMINOLOGY.md)。

## 历史数据与画面范围

V15 封面上的“15 页论文 46 秒”等数字来自当时开发版本、特定机器与模型的一次测量。素材包明确说明结果依赖当时的引擎补丁；本次内容更新没有重新跑这组计时，也没有把它作为当前公开版本的通用性能指标。不同论文、供应商、模型、字体和机器会改变耗时与质量。

封面中“Cosmos 75/75 页”等为指定历史产物的 QA 记录，不能推广为任意论文无瑕疵。检查器的 0 个错误也不等于人工确认完全正确。当前验收范围以[客户端整合记录](CLIENTS_RELEASE_20261027.md)及[Zotero 验证记录](../zotero/VERIFICATION.md)为准；原 20 篇完整论文一次性验收尚未宣告全部通过。

V15 中的悬窗画面按旧客户端源码重绘，不是真实录屏。本轮展示用文字和流程说明当前客户端，没有把重绘图称为最新客户端截图。

## 后续更新

更新口号或支持范围时，同时检查 `README.md`、`README_en.md`、`docs/index.html` 与 `docs/slides.html`。幻灯片当前为 21 页 HTML 演示，保留键盘、滚轮、触摸与全屏导航；入口数量由页面实际幻灯片确定。发布新的性能数字时，先补上可追溯的版本与测量记录。
