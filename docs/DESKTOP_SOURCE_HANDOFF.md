# 悬窗源码交接

仓库：`https://github.com/asimfish/super_translate`

源码分支：`feat/macos-floating-source-20261011`

源码目录：[`desktop/macos/`](../desktop/macos/README.md)

启动入口：[`scripts/paperchina-floating`](../scripts/paperchina-floating)

其他 agent 在分支推送后可执行：

```bash
git fetch origin feat/macos-floating-source-20261011
git switch --track origin/feat/macos-floating-source-20261011
swift test --package-path desktop/macos
```

若正在处理其他工作，请为该远端分支创建独立 worktree，保留已有修改。

本次交接包含 29 个现有源码、构建/安装脚本和测试文件，以及这两份入口文档。
不包含 `.build`、生成的 App、用户 PDF、账号数据、令牌或供应商密钥。
固定 20 篇完整论文的翻译及排版验收尚未通过；该分支用于客户端代码协作。

客户端调用主服务的上传、翻译、状态、下载、厂家目录和账号模型设置接口。
新版文档库的整理/导入历史接口依赖匹配的后端，当前远端主分支缺少本地的
`app/services/document_library.py` 和 `app/models/document.py`，因此仅切换此源码分支
不足以复现本机部署中的全部文档库功能。
