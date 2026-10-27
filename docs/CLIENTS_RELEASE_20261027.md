# Web、悬窗与 Zotero 整合验收（2026-10-27）

本次将悬窗、Zotero 和近期服务端改进整合到主分支。三个入口连接同一服务、使用同一账号时，共用模型配置、文档内容去重、导入历史、内容版本及任务状态。安装入口见 [Zotero 完整教程](ZOTERO.zh-CN.md)、[悬窗指南](../desktop/README.md) 和 [部署指南](DEPLOYMENT.md)。

## 交付内容

- Zotero **0.2.2 预览版**：保留右键、论文侧栏、阅读器入口和进度条；完成后只归档「中文版《论文名字》」。更新目录改为主分支，当前仍手动安装更新。
- macOS 悬窗源码及构建、登录自启脚本；与 Zotero 使用相同后端协议。
- 内容去重与同名版本、按学科/领域/论文扩展的术语库、八家供应商的账号模型目录和内置参考目录。
- 任务归属、取消、重启恢复及草稿隔离；保留最佳候选，停止无进展重试，不把待修复译文标为正式完成。
- 新安装可在首次启动服务前创建账号；Docker 镜像包含账号管理命令。
- 期刊/书籍版式与既有修复的语义合并，保留 TeX 字体的原始公式边界；正文向上借用空间时避让相邻公式；算法标题与参数说明采用窄删除带，避免误删邻行；视觉检查区分原图与新增公式贴图。

## 验证方法与结果

本次检查针对整合后的代码，命令在仓库根目录执行，Python 检查使用锁定依赖环境：

- 非黄金页完整回归：**2,092 项、12 项子检查全部通过**。
- 黄金页回归集合：**321 项**，覆盖公式、表格、算法及真实译文缓存回放；最终通过结果见 [0.2.2 发布验收结果](https://github.com/asimfish/super_translate/releases/tag/zotero-v0.2.2)。
- Zotero 协议与任务测试：**45 项通过**；JavaScript / XML 校验通过。
- Swift 单元测试：**31 项通过**；独立任务恢复旅程另有 **4 项通过**，未产生意外翻译提交；Release App 构建与本机签名校验通过。
- Ruff、1,066 条术语库检查、CLI / 服务冒烟测试通过；安装文档的本地链接均可解析。

测试期间首次账号命令检查曾在主机高负载下超过 30 秒等待上限；调整测试进程上限为 120 秒后，最终完整回归通过，账号、数据库、保密与输出断言均保留。

最终 Docker 镜像完成全新账号创建、列表查询和服务健康检查。Linux 容器内另外回放两页真实 DeepSeek 缓存和 Adam 四页文档，均能生成、渲染 PDF，质检 0 错误。

```sh
uv sync --frozen --extra dev
uv run ruff check app pdf_zh_translator tests scripts
uv run python -m pdf_zh_translator corpus-lint --strict
PYTHON=.venv/bin/python bash scripts/smoke_test.sh
uv run pytest -q --ignore=tests/test_golden_pages.py
uv run pytest -q tests/test_golden_pages.py
npm test --prefix zotero
python3 zotero/scripts/build.py
swift test --package-path desktop/macos
./desktop/macos/build.sh
docker build -t supertranslate:clients .
```

独立的新安装端到端验证已通过 **13 项检查**，覆盖创建账号、启动全新服务、登录、加密保存个人 API Key、读取八家模型目录、上传 PDF、同内容去重、文档历史、术语接口、由打包 XPI 中的客户端代码提交任务、真实 DeepSeek 完成翻译、两页中文 PDF 下载与渲染、译后 0 错误，以及完成后重复上传复用。验证使用临时数据库和合成科学文本，未修改日常文献库或生产任务。

打包只包含 11 个运行时文件，两次构建结果一致。安装包 SHA-256：

```text
239888e85556e7e8b1a4355c1b4a0791715ecd3c6432c1571c5dbc0c28a6ea5a
```

新增黄金缓存缺口使用真实 DeepSeek 补录 17 条，原缓存记录保留，来源与前后校验和见 [缓存补录记录](../tests/fixtures/cache-capture-20261027.json)。新增公开 PDF 测试页附来源、许可和校验和；合成字形测试保留 Inter 字形并附 OFL 许可。

代码图的整批变化超过工具 1,000 符号的列表上限。整批结果保留为风险提示，另按互不重叠的文件组对同一候选树完成 `all` / `compare` 分析，要求每组均无 `partial` / `truncated`。核心 API、恢复与排版路径属于高风险改动，以完整回归和安装流程验证约束交付。

## 适用范围与未覆盖项

- Zotero 原生证据来自 **macOS / Zotero 10.0.4、10.0.6**；插件安装范围限定 10.0.x。其他系统及版本尚未原生验收。
- 悬窗要求 macOS 13+，当前为源码构建与本机签名；未提供经过公证的通用 App 安装包。
- 此次新安装真实翻译是两页合成文档。黄金页回放和此前完整论文验证不替代原定的 **20 篇完整论文一次性验收**，该目标仍未完成；不承诺所有 PDF 均一次成功。
- 本次交付的是仓库主分支和可下载插件，没有重启生产服务或用户日常 Zotero。已有部署需备份 `data/` 与 `.env`、更新服务后，再安装新版插件。
- 插件不自带云服务、账号、API Key 或 SSH 隧道；按教程配置实际服务地址，8001 与插件默认的 18001 需按实际情况匹配。

此前完整论文和原生 Zotero 验证见 [插件历史验收记录](../zotero/VERIFICATION.md)。
