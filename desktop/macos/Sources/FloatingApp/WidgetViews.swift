import AppKit
import FloatingCore
import SwiftUI
import UniformTypeIdentifiers

struct GlassMaterial: NSViewRepresentable {
    func makeNSView(context: Context) -> NSVisualEffectView {
        let view = NSVisualEffectView()
        view.material = .hudWindow
        view.blendingMode = .behindWindow
        view.state = .active
        view.wantsLayer = true
        view.layer?.cornerRadius = 20
        view.layer?.masksToBounds = true
        return view
    }
    func updateNSView(_ nsView: NSVisualEffectView, context: Context) {}
}

struct FloatingWidgetView: View {
    @ObservedObject var store: WidgetStore
    let settings: () -> Void
    let layout: () -> Void
    let emphasize: (Bool) -> Void
    @State private var targeted = false
    @State private var hovering = false
    @State private var showingLibrary = false
    private let accent = Color(red: 0.08, green: 0.68, blue: 0.51)

    var body: some View {
        Group {
            if store.collapsed { compact }
            else { expanded }
        }
        .background(GlassMaterial())
        .overlay(RoundedRectangle(cornerRadius: 20).stroke(
            targeted ? accent : Color.primary.opacity(0.14), lineWidth: targeted ? 2 : 0.7))
        .padding(6)
        .frame(width: store.panelWidth, height: store.panelHeight)
        .onHover { hovering = $0; emphasize(hovering || targeted) }
        .onDrop(of: [UTType.fileURL.identifier], isTargeted: $targeted, perform: receiveDrop)
        .onChange(of: targeted) { _ in emphasize(hovering || targeted) }
        .onChange(of: store.panelHeight) { _ in layout() }
        .onChange(of: store.panelWidth) { _ in layout() }
        .onChange(of: store.idleOpacity) { _ in emphasize(hovering || targeted) }
        .accessibilityLabel("Super Translate PDF 翻译悬窗")
    }

    private var compact: some View {
        VStack(spacing: 4) {
            Image(systemName: store.activeCount > 0 ? "doc.text.magnifyingglass" : "doc.text")
                .font(.system(size: 21, weight: .medium)).foregroundStyle(accent)
            Text(store.activeCount > 0 ? "\(store.activeCount)" : "译")
                .font(.system(size: 10, weight: .semibold))
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .contentShape(RoundedRectangle(cornerRadius: 20))
        .onTapGesture { store.collapsed = false }
        .help("拖入 PDF 开始翻译；点击展开；拖动背景移动悬窗")
        .contextMenu {
            Button("展开悬窗") { store.collapsed = false }
            Button("设置…", action: settings)
            Button("打开系统") { store.openSystem() }
        }
    }

    private var expanded: some View {
        VStack(spacing: 9) {
            HStack(spacing: 7) {
                Image(systemName: "doc.text").foregroundStyle(accent)
                Text("SUPER TRANSLATE").font(.system(size: 11, weight: .semibold, design: .rounded))
                    .tracking(1.2)
                Spacer()
                icon("books.vertical", help: "文献库：按时间查找论文、版本与导入记录") {
                    showingLibrary = true
                }
                .popover(isPresented: $showingLibrary) { DocumentLibraryView(store: store) }
                icon("gearshape", help: "连接与阅读器设置", action: settings)
                icon("arrow.down.right.and.arrow.up.left", help: "折叠悬窗") { store.collapsed = true }
            }
            .padding(.horizontal, 2)

            Button(action: chooseFiles) {
                HStack(spacing: 12) {
                    Image(systemName: targeted ? "arrow.down.doc.fill" : "arrow.down.doc")
                        .font(.system(size: 24, weight: .light)).foregroundStyle(accent)
                    VStack(alignment: .leading, spacing: 4) {
                        Text(targeted ? "松开，开始翻译" : "拖入 PDF · 自动翻译")
                            .font(.system(size: 14, weight: .medium))
                        Text(store.needsLogin ? "点击设置，登录已有账号" : "点击选文件，也可一次拖入多个")
                            .font(.system(size: 10)).foregroundStyle(.secondary)
                    }
                    Spacer(minLength: 0)
                }
                .padding(.horizontal, 12).padding(.vertical, 12)
                .background(accent.opacity(targeted ? 0.13 : 0.045))
                .clipShape(RoundedRectangle(cornerRadius: 12))
                .overlay(RoundedRectangle(cornerRadius: 12)
                    .stroke(accent.opacity(0.35), style: StrokeStyle(lineWidth: 0.8, dash: [4, 4])))
            }
            .buttonStyle(.plain)

            if !store.jobs.isEmpty {
                ScrollView {
                    LazyVStack(spacing: 0) {
                        ForEach(store.jobs) { job in
                            jobRow(job)
                            if job.id != store.jobs.last?.id { Divider().opacity(0.45) }
                        }
                    }
                }
                .frame(height: CGFloat(min(store.jobs.count, 4)) * 64)
            }

            HStack(spacing: 5) {
                Circle().fill(store.connected ? accent : .orange).frame(width: 5, height: 5)
                Text(store.notice).font(.system(size: 9)).foregroundStyle(.secondary)
                    .lineLimit(1).help(store.notice)
                Spacer(minLength: 0)
                if !store.connected {
                    Button(store.needsLogin ? "登录" : "连接") { settings() }
                        .font(.system(size: 10)).buttonStyle(.plain).foregroundStyle(accent)
                } else {
                    Menu {
                        Button("打开系统网页") { store.openSystem() }
                        Button("清除已结束记录") { store.clearFinished() }
                        Button("连接与阅读器设置…", action: settings)
                    } label: { Image(systemName: "ellipsis") }
                    .menuStyle(.borderlessButton).frame(width: 22)
                }
            }
        }
        .padding(12)
    }

    private func jobRow(_ job: WidgetJob) -> some View {
        HStack(spacing: 9) {
            Image(systemName: job.phase == .completed ? "checkmark.circle.fill" :
                (job.phase == .repairing ? "wrench.and.screwdriver" :
                    (job.phase == .failed ? "exclamationmark.circle" : "doc")))
                .foregroundStyle([.failed, .repairing].contains(job.phase) ? Color.orange : accent)
                .font(.system(size: 15))
            VStack(alignment: .leading, spacing: 5) {
                Text(job.displayTitle).font(.system(size: 11, weight: .medium)).lineLimit(1).help(job.displayTitle)
                Text(job.message).font(.system(size: 9)).foregroundStyle(.secondary)
                    .lineLimit(1).help(job.message)
                if job.phase.active {
                    ProgressView(value: job.progress).tint(accent).scaleEffect(y: 0.65)
                }
            }
            Spacer(minLength: 2)
            if job.phase == .completed || job.hasTranslated == true {
                if store.opening == job.id { ProgressView().controlSize(.small) }
                else {
                    Button(job.phase == .completed ? "查看" : "草稿") { Task { await store.open(job) } }
                        .font(.system(size: 10, weight: .medium)).buttonStyle(.plain).foregroundStyle(accent)
                        .help(job.phase == .completed ? "在\(store.reader.title)中打开\(store.variant.title)" : "查看已保留的译文草稿，尚未确认通过质检")
                }
                Menu {
                    Button("文件所在位置") { Task { await store.open(job, reveal: true) } }
                    Button("任务详情与质检报告") { Task { await store.showTaskDetails(job) } }
                    if [.failed, .ready, .cancelled, .repairing].contains(job.phase) {
                        Button(job.phase == .repairing ? "重试修复" : "重试") { Task { await store.retry(job) } }
                    } else if job.phase.active {
                        Button("取消任务") { Task { await store.cancel(job) } }.disabled(job.cancellationRequested)
                    }
                } label: { Image(systemName: "ellipsis") }
                .menuStyle(.borderlessButton).frame(width: 15)
            } else if [.failed, .ready, .cancelled, .repairing].contains(job.phase) {
                Button(job.phase == .ready ? "开始" : (job.phase == .repairing ? "重试修复" : "重试")) { Task { await store.retry(job) } }
                    .font(.system(size: 10)).buttonStyle(.plain).foregroundStyle(accent)
                Button("详情") { Task { await store.showTaskDetails(job) } }
                    .font(.system(size: 10)).buttonStyle(.plain).foregroundStyle(accent)
            } else if [.queued, .translating, .waiting].contains(job.phase) {
                icon("xmark", help: "取消这项任务") { Task { await store.cancel(job) } }
                    .disabled(job.cancellationRequested)
            }
        }
        .padding(.horizontal, 3).frame(height: 63)
    }

    private func icon(_ name: String, help: String, action: @escaping () -> Void) -> some View {
        Button(action: action) { Image(systemName: name).font(.system(size: 11)) }
            .buttonStyle(.plain).foregroundStyle(.secondary).help(help)
    }

    private func chooseFiles() {
        if !store.connected { settings(); return }
        let chooser = NSOpenPanel()
        chooser.allowedContentTypes = [.pdf]
        chooser.allowsMultipleSelection = true
        chooser.canChooseDirectories = false
        chooser.prompt = "开始翻译"
        NSApp.activate(ignoringOtherApps: true)
        if chooser.runModal() == .OK { store.acceptFiles(chooser.urls) }
    }

    private func receiveDrop(_ providers: [NSItemProvider]) -> Bool {
        guard !providers.isEmpty else { return false }
        for provider in providers.prefix(20) {
            provider.loadItem(forTypeIdentifier: UTType.fileURL.identifier, options: nil) { item, _ in
                let url: URL?
                if let data = item as? Data { url = URL(dataRepresentation: data, relativeTo: nil) }
                else { url = item as? URL }
                guard let file = url, file.isFileURL else { return }
                DispatchQueue.main.async { store.acceptFiles([file]) }
            }
        }
        return true
    }
}

struct DocumentLibraryView: View {
    @ObservedObject var store: WidgetStore

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Label("文献库", systemImage: "books.vertical").font(.headline)
                Spacer()
                Text("\(store.libraryTotal) 份文档").font(.caption).foregroundStyle(.secondary)
                Button { Task { await store.refreshLibrary() } } label: { Image(systemName: "arrow.clockwise") }
                    .help("刷新文献库").disabled(store.loadingLibrary)
            }
            TextField("搜索论文标题或原文件名", text: $store.librarySearch)
                .textFieldStyle(.roundedBorder)
            HStack {
                Picker("排序", selection: $store.librarySort) {
                    Text("最近导入").tag("recent")
                    Text("最早导入").tag("oldest")
                    Text("论文标题").tag("title")
                }.labelsHidden().frame(width: 140)
                Spacer()
                Button(store.organizingLibrary ? "整理中…" : "整理已有文档") {
                    Task { await store.organizeLibrary() }
                }.disabled(store.organizingLibrary || !store.connected)
            }
            if !store.libraryNotice.isEmpty {
                Text(store.libraryNotice).font(.caption).foregroundStyle(.orange)
            }
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 0) {
                    if store.library.isEmpty && !store.loadingLibrary {
                        Text(store.librarySearch.isEmpty ? "拖入 PDF 后会自动收录到这里" : "没有找到匹配的文档")
                            .foregroundStyle(.secondary).padding(.vertical, 24)
                    }
                    ForEach(Array(store.library.enumerated()), id: \.element.id) { index, paper in
                        let day = LibraryDate.label(paper.lastImportedAt ?? paper.createdAt ?? "")
                        if store.librarySort != "title" && (index == 0 || day != LibraryDate.label(
                            store.library[index-1].lastImportedAt ?? store.library[index-1].createdAt ?? "")) {
                            Text(day).font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                                .padding(.top, 12).padding(.bottom, 6)
                        }
                        HStack(alignment: .top, spacing: 8) {
                            Image(systemName: "doc.text").foregroundStyle(.secondary).padding(.top, 2)
                            VStack(alignment: .leading, spacing: 4) {
                                Text(paper.displayTitle).font(.system(size: 12, weight: .medium)).lineLimit(2).help(paper.displayTitle)
                                Text("\(paper.originalFilename ?? "") · 导入 \(paper.importCount ?? 1) 次" +
                                    ((paper.versionCount ?? 1) > 1 ? " · 版本 \(paper.versionNumber ?? 1)/\(paper.versionCount ?? 1)" : ""))
                                    .font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                                Text(paper.taskPresentation().message)
                                    .font(.caption2).foregroundStyle(
                                        paper.translationStatus == "completed" ? Color.secondary : Color.orange)
                                    .lineLimit(2).help(paper.taskPresentation().message)
                            }
                            Spacer(minLength: 4)
                            if paper.hasTranslated {
                                Button(paper.translationStatus == "completed" ? "查看" : "草稿") {
                                    Task { await store.openLibraryPaper(paper) }
                                }.font(.caption).help(paper.translationStatus == "completed" ?
                                    "查看已完成译文" : "查看已保留草稿，尚未确认通过质检")
                            }
                            Menu {
                                Button("导入记录与版本") { Task { await store.showLibraryHistory(paper) } }
                                if paper.hasTranslated {
                                    Button("文件所在位置") { Task { await store.openLibraryPaper(paper, reveal: true) } }
                                }
                                Button("打开系统网页") { store.openSystem() }
                            } label: { Image(systemName: "ellipsis") }
                            .menuStyle(.borderlessButton).frame(width: 18)
                        }.padding(.vertical, 10)
                        Divider()
                    }
                    if store.library.count < store.libraryTotal {
                        Button("加载更多") { Task { await store.refreshLibrary(reset: false) } }
                            .disabled(store.loadingLibrary).padding(.vertical, 12)
                    }
                    if store.loadingLibrary { ProgressView().controlSize(.small).padding() }
                }
            }
            Text("相同内容复用译文；内容不同分别保留。文件按月份和论文标题归档。")
                .font(.caption2).foregroundStyle(.secondary)
        }
        .padding(18).frame(width: 460, height: 490)
        .task(id: store.librarySearch + "|" + store.librarySort) {
            do { try await Task.sleep(nanoseconds: 300_000_000) } catch { return }
            await store.refreshLibrary()
        }
    }
}

struct WidgetSettingsView: View {
    @ObservedObject var store: WidgetStore
    @State private var server = ""
    @State private var username = ""
    @State private var password = ""

    var body: some View {
        ScrollView {
        VStack(alignment: .leading, spacing: 18) {
            HStack {
                Image(systemName: "doc.text").font(.system(size: 25)).foregroundStyle(.green)
                VStack(alignment: .leading, spacing: 3) {
                    Text("Super Translate 悬窗").font(.title3.weight(.semibold))
                    Text("\(store.sessionLabel) · 复用已有系统和翻译配置")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            GroupBox("连接翻译系统") {
                VStack(alignment: .leading, spacing: 9) {
                    TextField("服务地址", text: $server).textFieldStyle(.roundedBorder)
                    Text("原系统隧道：127.0.0.1:18001；本地运行可使用 127.0.0.1:8000")
                        .font(.system(size: 10)).foregroundStyle(.secondary)
                    HStack {
                        Button("连接") { Task { await store.saveAddress(server) } }
                        Button("打开系统网页") { store.openSystem() }
                        Spacer()
                        if store.connecting { ProgressView().controlSize(.small) }
                    }
                    Divider()
                    HStack {
                        TextField("已有账号用户名", text: $username).textFieldStyle(.roundedBorder)
                        SecureField("密码", text: $password).textFieldStyle(.roundedBorder)
                    }
                    HStack {
                        Button("登录并记住") {
                            Task {
                                await store.login(address: server, username: username, password: password)
                                password = ""
                            }
                        }
                        .disabled(username.isEmpty || password.isEmpty || store.connecting)
                        Button("退出登录") { Task { await store.logout() } }
                        Spacer()
                        Text("登录凭据保存到 macOS 钥匙串").font(.system(size: 10)).foregroundStyle(.secondary)
                    }
                }
                .padding(7)
            }
            GroupBox("翻译厂家与模型") {
                VStack(alignment: .leading, spacing: 10) {
                    Picker("API 厂家", selection: $store.selectedProvider) {
                        Text("沿用系统默认").tag("")
                        ForEach(TranslationProvider.allCases, id: \.rawValue) { provider in
                            let configured = store.providerStatuses.first { $0.provider == provider.rawValue }?.configured == true
                            Text(provider.title + (configured ? "" : " · 未配置")).tag(provider.rawValue)
                        }
                    }
                    .accessibilityIdentifier("translation-provider")
                    .disabled(store.loadingModels || store.savingModel || store.quality == "fast")
                    if !store.selectedProvider.isEmpty {
                        Picker("模型", selection: $store.selectedModel) {
                            if store.modelChoices.isEmpty { Text("等待模型列表").tag("") }
                            ForEach(store.modelChoices, id: \.self) { model in Text(model).tag(model) }
                        }
                        .accessibilityIdentifier("translation-model")
                        .disabled(store.loadingModels || store.savingModel || store.quality == "fast")
                        DisclosureGroup("自定义模型 / 豆包 Endpoint ID") {
                            TextField("模型 ID", text: $store.selectedModel).textFieldStyle(.roundedBorder)
                                .disabled(store.loadingModels || store.savingModel || store.quality == "fast")
                        }
                        if store.selectedCredential?.configured != true {
                            Text("请先在网页 API 设置中保存这家厂商的密钥。")
                                .font(.caption).foregroundStyle(.secondary)
                        } else if store.selectedCredential?.source == "server" {
                            Text("当前沿用服务端配置；更改模型前请在网页保存账号 API 配置。")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        if let warning = store.selectedCatalog?.warning, !warning.isEmpty {
                            Text(warning).font(.caption).foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    HStack {
                        Button("应用厂家和模型") { Task { await store.applyModelSelection() } }
                            .disabled(!store.canApplyModel)
                            .accessibilityIdentifier("apply-translation-model")
                        Button("刷新模型列表") { Task { await store.refreshModels(force: true) } }
                            .disabled(!store.connected || store.loadingModels || store.savingModel)
                        Spacer()
                        if store.loadingModels || store.savingModel { ProgressView().controlSize(.small) }
                    }
                    if store.quality == "fast" {
                        Text("快速模式使用 Google；使用 DeepSeek Flash 请选标准或精细质量。")
                            .font(.caption).foregroundStyle(.secondary)
                    } else {
                        Text("厂家选择自动记住；模型点击应用后与该账号的网页 API 设置同步。")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    if !store.modelNotice.isEmpty {
                        Text(store.modelNotice).font(.caption).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }.padding(7)
            }
            GroupBox("阅读与悬窗") {
                VStack(spacing: 12) {
                    LabeledContent("查看译文时打开") {
                        Picker("阅读器", selection: $store.reader) {
                            ForEach(PDFReader.allCases, id: \.self) { Text($0.title).tag($0) }
                        }.labelsHidden().frame(width: 235)
                    }
                    LabeledContent("PDF 版本") {
                        Picker("版本", selection: $store.variant) {
                            ForEach(PDFVariant.allCases, id: \.self) { Text($0.title).tag($0) }
                        }.labelsHidden().frame(width: 235)
                    }
                    LabeledContent("翻译质量") {
                        Picker("质量", selection: $store.quality) {
                            Text("标准").tag("balanced")
                            Text("精细").tag("quality")
                            Text("快速 · Google").tag("fast")
                        }.labelsHidden().frame(width: 235)
                    }
                    LabeledContent("常态透明度") {
                        HStack {
                            Slider(value: $store.idleOpacity, in: 0.3...1).frame(width: 170)
                            Text("\(Int(store.idleOpacity * 100))%").monospacedDigit().frame(width: 40)
                        }
                    }
                    Toggle("翻译完成后自动打开译文", isOn: $store.autoOpen)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .padding(9)
            }
            Text(store.notice).font(.caption).foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, alignment: .leading).fixedSize(horizontal: false, vertical: true)
            Text("拖动悬窗背景可移动；折叠后仍可接收 PDF。退出悬窗后，已提交的服务端翻译继续运行。")
                .font(.system(size: 10)).foregroundStyle(.secondary)
        }
        .padding(22).frame(width: 520)
        }
        .onAppear {
            server = store.address
            username = store.defaults.string(forKey: "username:\(store.address)") ?? ""
            Task { await store.refreshModels() }
        }
    }
}

// Feeds a real NSItemProvider through the same callback registered with onDrop.
// Called only by the isolated desktop contract harness.
extension FloatingWidgetView {
    @MainActor static func contractDrop(store: WidgetStore, file: URL, asData: Bool = false) -> Bool {
        let item: NSSecureCoding = asData ? NSData(data: file.dataRepresentation) : file as NSURL
        let provider = NSItemProvider(item: item, typeIdentifier: UTType.fileURL.identifier)
        return FloatingWidgetView(store: store, settings: {}, layout: {}, emphasize: { _ in })
            .receiveDrop([provider])
    }
}
