# macOS 悬窗客户端

原生 SwiftUI 客户端支持拖入 PDF、切换翻译供应商和模型、查看任务进度，以及用系统 PDF 阅读器打开译文。透明悬窗、任务记录和登录状态保存在本机；翻译由连接的 SuperTranslate 后端执行。

本分支提供可构建的客户端预览源码。后端必须具备下述接口能力；客户端构建成功不代表当前分支的后端已具备全部功能。

## 构建和安装

需要 macOS 13 或更新版本，以及支持 Swift tools 5.9 的 Xcode Command Line Tools。Swift 包没有第三方依赖。

在仓库根目录执行：

```bash
swift test --package-path desktop/macos
./desktop/macos/build.sh
```

构建脚本生成本机临时签名的 App，并在 `desktop/macos/build/Super Translate Floating.app` 建立链接。安装时执行：

```bash
./desktop/macos/install.sh
```

安装脚本将 App 放到 `~/Applications`，设置用户登录后自动启动，并保留被替换的同名 App 备份。首次打开后，在设置中填写服务器地址并登录；API 密钥在该用户的后端设置中管理。

本机服务可使用 `http://127.0.0.1:18001`；远程服务需要 HTTPS。客户端使用系统 Keychain 保存会话凭据，按服务器和会话隔离本地任务记录。

启动已安装的客户端：

```bash
./scripts/paperchina-floating
```

## 后端接口要求

客户端需要可用的健康检查、登录、论文上传、翻译提交、任务查询、取消、QA 报告及单语和双语 PDF 下载接口，也需要当前用户的供应商和模型目录接口。接口调用与数据模型定义见 `macos/Sources/FloatingCore/PDFService.swift`。

文档整理还需要以下能力：

- `POST /api/papers/organize`
- `GET /api/papers/{id}/library-history`
- 论文响应中的 canonical、版本和内容指纹元数据

本分支所基于的后端尚未包含上述文档整理能力。使用文档整理功能时，需要配套后端；前端源码不能替代后端升级。

任务为 `repairing` 时，客户端展示后端提供的暂停原因和保留草稿入口。自动修复及重试预算由后端控制，客户端不会绕过译后检查把失败任务改成完成。

## 验证

Swift 单元测试使用独立请求夹具验证服务调用、PDF 文件校验、任务呈现和文档记录。还可运行独立的任务恢复旅程：

```bash
python3 desktop/macos/Tests/task_reconciliation_journey.py /tmp/supertranslate-task-journey
```

输出目录须为尚不存在的目录。该旅程在本机启动临时 HTTP 夹具、使用临时任务记录，不调用翻译供应商，也不打开阅读器。真实 PDF 翻译质量需要另行连接配套后端验证。

## 源码范围

- `desktop/macos/Sources/FloatingCore`：HTTP 客户端、记录模型及文档库模型。
- `desktop/macos/Sources/FloatingApp`：悬窗、设置、任务状态和入口。入口引用的 probes 也参与编译。
- `desktop/macos/Tests`：随分支携带的单元测试和独立任务恢复夹具。
- `desktop/macos/build.sh`、`install.sh`：本机签名构建与登录启动设置。

用户凭据、任务记录、PDF、翻译缓存、App、构建目录和私人隧道配置不随源码提交。
