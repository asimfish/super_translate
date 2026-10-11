# macOS PDF 翻译悬窗源码

这是 Super Translate 的 SwiftUI / AppKit 客户端，最低支持 macOS 13。
客户端连接现有翻译服务，支持透明置顶、折叠、拖放 PDF、账号登录、厂家和模型选择、任务恢复、文档库和阅读器打开。

## 给其他 agent 的入口

- App 入口：[Sources/FloatingApp/main.swift](Sources/FloatingApp/main.swift)
- 悬窗和设置界面：[WidgetViews.swift](Sources/FloatingApp/WidgetViews.swift)
- 上传、任务队列与恢复：[WidgetStore.swift](Sources/FloatingApp/WidgetStore.swift)
- HTTP、登录和模型接口：[PDFService.swift](Sources/FloatingCore/PDFService.swift)
- 本地文档与导出命名：[DocumentLibrary.swift](Sources/FloatingCore/DocumentLibrary.swift)
- 构建：[build.sh](build.sh)；安装和登录自启：[install.sh](install.sh)

## 构建与启动

需要 macOS、Xcode Command Line Tools 和 Swift。项目根目录执行：

```bash
swift test --package-path desktop/macos
./desktop/macos/build.sh
./scripts/paperchina-floating
```

首次启动会安装到 `~/Applications/Super Translate Floating.app`，并加载
`~/Library/LaunchAgents/com.supertranslate.floating.plist`。已有安装需要升级时，执行
`./desktop/macos/install.sh`。客户端为本机临时签名，尚无公证安装包。

默认服务地址为 `http://127.0.0.1:18001`。需要可连接的翻译服务；若使用现有 SSH 隧道，
安装脚本会加载已有 `com.liyufeng.supertranslate.tunnel` 任务，但不会创建该隧道。
在设置里登录账号，服务端需为该账号配置翻译供应商。

登录令牌存入 macOS 钥匙串，供应商密钥由服务端管理。文档库版本整理接口需要对应的后端实现；
本分支交付客户端源码。Python 服务集成测试需要主项目开发依赖和匹配的后端接口。

## 验证状态

2026-10-11，在 macOS arm64 对本分支所复制的相同源码执行 Swift 构建和 31 项 XCTest，
全部通过。覆盖 HTTP 请求、账号隔离、模型选择、PDF 校验、导出命名、草稿状态和规范文档身份。

这些检查不代表真实论文的译文及版式验收通过。固定 20 篇完整论文必须在同一冻结版本下全部通过，
目前该验收仍在推进。安装脚本会修改本机应用和登录任务，应在需要安装时执行。

更多交接信息见 [DESKTOP_SOURCE_HANDOFF.md](../../docs/DESKTOP_SOURCE_HANDOFF.md)。
