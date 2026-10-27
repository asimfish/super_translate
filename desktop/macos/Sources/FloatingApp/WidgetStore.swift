import AppKit
import Combine
import FloatingCore
import Foundation

enum WidgetPhase: String, Codable {
    case queued, uploading, starting, translating, waiting, repairing, ready, completed, failed, cancelled
    var active: Bool { [.queued, .uploading, .starting, .translating, .waiting].contains(self) }
}

struct WidgetJob: Identifiable, Codable {
    let id: UUID
    var paperID: String?
    var title: String
    var phase: WidgetPhase
    var progress: Double = 0
    var message: String = "等待上传"
    var hasDual: Bool = false
    var hasTranslated: Bool? = nil
    var hasQA: Bool = false
    var cancellationRequested: Bool = false
    var source: URL?
    var createdAt: String? = nil
    var versionNumber: Int? = nil
    var versionCount: Int? = nil

    var displayTitle: String {
        DocumentTitle.display(title: title, id: paperID ?? "", createdAt: createdAt,
            versionNumber: versionNumber, versionCount: versionCount)
    }
}

enum ReaderError: Error, LocalizedError {
    case unavailable(PDFReader), couldNotOpen
    var errorDescription: String? {
        switch self {
        case .unavailable(.adobe): return "未找到 Adobe Acrobat / Reader，请安装后再试，或在设置中选择浏览器。"
        case .unavailable: return "没有找到可用阅读器，请在设置中选择系统默认阅读器。"
        case .couldNotOpen: return "译文已保存，但阅读器未能打开；可使用文件所在位置按钮。"
        }
    }
}

@MainActor
enum ExternalPDF {
    static func application(for reader: PDFReader) -> URL? {
        let workspace = NSWorkspace.shared
        switch reader {
        case .browser:
            return workspace.urlForApplication(toOpen: URL(string: "https://localhost/")!)
        case .adobe:
            return ["com.adobe.Acrobat.Pro", "com.adobe.Reader", "com.adobe.Acrobat.Reader"]
                .compactMap { workspace.urlForApplication(withBundleIdentifier: $0) }.first
        case .system: return nil
        }
    }

    static func open(_ file: URL, reader: PDFReader) async throws {
        if reader == .system {
            guard NSWorkspace.shared.open(file) else { throw ReaderError.couldNotOpen }
            return
        }
        guard let app = application(for: reader) else { throw ReaderError.unavailable(reader) }
        let configuration = NSWorkspace.OpenConfiguration()
        configuration.activates = true
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            NSWorkspace.shared.open([file], withApplicationAt: app, configuration: configuration) { _, error in
                if let error { continuation.resume(throwing: error) }
                else { continuation.resume() }
            }
        }
    }
}

@MainActor
final class WidgetStore: ObservableObject {
    @Published var jobs: [WidgetJob] = []
    @Published var library: [PaperSnapshot] = []
    @Published var librarySearch = ""
    @Published var librarySort = "recent"
    @Published var libraryTotal = 0
    @Published var loadingLibrary = false
    @Published var libraryNotice = ""
    @Published var organizingLibrary = false
    private var libraryRequest = UUID()
    @Published var notice = "连接系统后，拖入 PDF 即可翻译"
    @Published var connected = false
    @Published var needsLogin = false
    @Published var connecting = false
    @Published var sessionLabel = "本机服务"
    @Published var opening: UUID?
    @Published var selectedProvider = "" {
        didSet {
            if let client = service { defaults.set(selectedProvider, forKey: "provider:\(client.namespace)") }
            if selectedProvider != oldValue {
                selectedModel = selectedCatalog?.selectedModel ?? ""
                modelNotice = ""
            }
        }
    }
    @Published var selectedModel = ""
    @Published private(set) var modelCatalogs: [ProviderModelCatalog] = []
    @Published private(set) var providerStatuses: [ProviderCredentialStatus] = []
    @Published private(set) var loadingModels = false
    @Published private(set) var savingModel = false
    @Published private(set) var modelNotice = ""
    @Published var collapsed: Bool {
        didSet { defaults.set(collapsed, forKey: "collapsed") }
    }
    @Published var idleOpacity: Double {
        didSet { defaults.set(idleOpacity, forKey: "idleOpacity") }
    }
    @Published var reader: PDFReader {
        didSet { defaults.set(reader.rawValue, forKey: "reader") }
    }
    @Published var variant: PDFVariant {
        didSet { defaults.set(variant.rawValue, forKey: "variant") }
    }
    @Published var quality: String {
        didSet { defaults.set(quality, forKey: "quality") }
    }
    @Published var autoOpen: Bool {
        didSet { defaults.set(autoOpen, forKey: "autoOpen") }
    }
    @Published private(set) var address: String
    private(set) var service: PDFService?
    private(set) var exportDirectory: URL?
    let defaults: UserDefaults
    private let storage: URL
    private var generation = UUID()
    private var uploadTask: Task<Void, Never>?
    private var pollTask: Task<Void, Never>?
    private var modelTask: Task<Void, Never>?

    init(defaults: UserDefaults = .standard, storage: URL? = nil) {
        self.defaults = defaults
        self.storage = storage ?? FileManager.default.urls(for: .applicationSupportDirectory,
            in: .userDomainMask)[0].appendingPathComponent("SuperTranslate/Floating")
        address = defaults.string(forKey: "server") ?? "http://127.0.0.1:18001"
        collapsed = defaults.bool(forKey: "collapsed")
        idleOpacity = defaults.object(forKey: "idleOpacity") as? Double ?? 0.55
        reader = PDFReader(rawValue: defaults.string(forKey: "reader") ?? "") ?? .browser
        variant = PDFVariant(rawValue: defaults.string(forKey: "variant") ?? "") ?? .translated
        quality = defaults.string(forKey: "quality") ?? "balanced"
        autoOpen = defaults.bool(forKey: "autoOpen")
    }

    var activeCount: Int { jobs.filter { $0.phase.active }.count }
    var panelWidth: CGFloat { collapsed ? 66 : 328 }
    var panelHeight: CGFloat { collapsed ? 72 : 142 + CGFloat(min(jobs.count, 4)) * 64 }

    var selectedCatalog: ProviderModelCatalog? { modelCatalogs.first { $0.provider == selectedProvider } }
    var selectedCredential: ProviderCredentialStatus? { providerStatuses.first { $0.provider == selectedProvider } }
    var modelChoices: [String] {
        var choices = selectedCatalog?.models ?? []
        if !selectedModel.isEmpty && !choices.contains(selectedModel) { choices.insert(selectedModel, at: 0) }
        return choices
    }
    var modelSelectionApplied: Bool {
        selectedProvider.isEmpty || (!selectedModel.isEmpty && selectedCatalog?.selectedModel == selectedModel)
    }
    var canApplyModel: Bool {
        connected && quality != "fast" && !loadingModels && !savingModel &&
            !selectedProvider.isEmpty && !selectedModel.isEmpty &&
            selectedCredential?.configured == true && selectedCredential?.source == "personal"
    }

    func refreshModels(force: Bool = false) async {
        guard let client = service, connected, !loadingModels, !savingModel else { return }
        let current = generation
        let provider = selectedProvider
        let keepDraft = !selectedModel.isEmpty && !modelSelectionApplied
        loadingModels = true
        defer { if current == generation { loadingModels = false } }
        do {
            async let statuses = client.providerCredentials()
            var catalogs: [ProviderModelCatalog]
            if force && !provider.isEmpty {
                let updated = try await client.refreshProviderModels(provider)
                catalogs = try await client.providerModels()
                if let index = catalogs.firstIndex(where: { $0.provider == provider }) { catalogs[index] = updated }
            } else { catalogs = try await client.providerModels() }
            let credentials = try await statuses
            guard current == generation, !Task.isCancelled else { return }
            modelCatalogs = catalogs.filter { TranslationProvider(rawValue: $0.provider) != nil }
            providerStatuses = credentials
            if selectedProvider != provider || !keepDraft {
                selectedModel = selectedCatalog?.selectedModel ?? selectedCatalog?.defaultModel ?? ""
            }
            modelNotice = force ? "模型列表已刷新" : ""
        } catch {
            guard current == generation, !Task.isCancelled else { return }
            modelNotice = "模型列表获取失败：\(error.localizedDescription)"
            if error as? ClientError == .http(401) {
                modelCatalogs = []; providerStatuses = []; selectedModel = ""
                report(error)
            }
        }
    }

    func applyModelSelection() async {
        guard let client = service, canApplyModel else { return }
        let current = generation
        let provider = selectedProvider
        let model = selectedModel
        savingModel = true
        defer { if current == generation { savingModel = false } }
        do {
            let saved = try await client.saveProviderModel(provider, model: model)
            guard current == generation, !Task.isCancelled else { return }
            guard saved.provider == provider, saved.model == model, saved.configured else {
                throw ClientError.invalidResponse
            }
            if let index = modelCatalogs.firstIndex(where: { $0.provider == provider }) {
                modelCatalogs[index].selectedModel = saved.model
            }
            if let index = providerStatuses.firstIndex(where: { $0.provider == provider }) {
                providerStatuses[index] = saved
            }
            modelNotice = "已应用 \(TranslationProvider(rawValue: provider)?.title ?? provider) · \(model)"
        } catch {
            guard current == generation, !Task.isCancelled else { return }
            modelNotice = "模型未保存：\(error.localizedDescription)"
            if error as? ClientError == .http(401) { report(error) }
        }
    }

    private struct SelectionError: Error, LocalizedError {
        let errorDescription: String?
    }

    private func translationBackend() throws -> String {
        if quality == "fast" || selectedProvider.isEmpty { return "" }
        guard TranslationProvider(rawValue: selectedProvider) != nil,
              selectedCredential?.configured == true else {
            throw SelectionError(errorDescription: "所选厂家尚未配置，请在网页 API 设置中保存密钥。")
        }
        guard !savingModel && modelSelectionApplied else {
            throw SelectionError(errorDescription: "请先在悬窗设置中点击「应用厂家和模型」，再开始翻译。")
        }
        return selectedProvider
    }

    func reconnect() async {
        do {
            let origin = try ServiceOrigin(address)
            let token = try SessionVault.read(origin: origin)
            try await configure(origin: origin, token: token)
        } catch { report(error) }
    }

    func login(address: String, username: String, password: String) async {
        connecting = true
        defer { connecting = false }
        do {
            let origin = try ServiceOrigin(address)
            let client = try PDFService(origin: origin)
            let receipt = try await client.login(username: username, password: password)
            try SessionVault.save(receipt.token, origin: origin)
            defaults.set(receipt.username, forKey: "username:\(origin.url.absoluteString)")
            try await configure(origin: origin, token: receipt.token)
        } catch { report(error) }
    }

    func saveAddress(_ value: String) async {
        do {
            let origin = try ServiceOrigin(value)
            try await configure(origin: origin, token: SessionVault.read(origin: origin))
        } catch { report(error) }
    }

    func logout() async {
        do {
            let origin = try ServiceOrigin(address)
            try SessionVault.remove(origin: origin)
            defaults.removeObject(forKey: "username:\(origin.url.absoluteString)")
            try await configure(origin: origin, token: "")
        } catch { report(error) }
    }

    func configure(origin: ServiceOrigin, token: String) async throws {
        generation = UUID()
        let current = generation
        uploadTask?.cancel()
        pollTask?.cancel()
        modelTask?.cancel()
        uploadTask = nil
        connected = false
        needsLogin = false
        opening = nil
        connecting = true
        defer { if current == generation { connecting = false } }
        let client = try PDFService(origin: origin, token: token)
        service = client
        modelCatalogs = []; providerStatuses = []; modelNotice = ""
        loadingModels = false; savingModel = false
        let savedProvider = defaults.string(forKey: "provider:\(client.namespace)") ?? ""
        selectedProvider = TranslationProvider(rawValue: savedProvider) == nil ? "" : savedProvider
        selectedModel = ""
        address = origin.url.absoluteString
        defaults.set(address, forKey: "server")
        sessionLabel = token.isEmpty ? "本机连接" :
            (defaults.string(forKey: "username:\(address)") ?? "已登录")
        exportDirectory = storage.appendingPathComponent(client.namespace).appendingPathComponent("PDFs")
        jobs = loadJobs()
        library = []; libraryTotal = 0; libraryNotice = ""; loadingLibrary = false
        librarySearch = ""; organizingLibrary = false
        libraryRequest = UUID()
        for i in jobs.indices where jobs[i].phase.active { jobs[i].phase = .waiting }
        startPolling()
        do { try await client.connect() }
        catch {
            if current != generation { return }
            throw error
        }
        guard current == generation else { return }
        connected = true
        notice = "拖入 PDF，使用悬窗设置中的翻译配置"
        // Ended local records may be stale after server recovery or organization.
        // Reconcile at connection time as well as polling active translations.
        await reconcileJobs()
        guard current == generation else { return }
        modelTask = Task { await refreshModels() }
        Task { await refreshLibrary() }
    }

    func acceptFiles(_ urls: [URL]) {
        guard service != nil, connected else {
            notice = needsLogin ? "请先在设置中登录，然后拖入 PDF" : "服务未连接，请打开设置检查地址"
            return
        }
        var accepted = 0
        for url in urls.prefix(20) {
            guard activeCount < 20 else { notice = "最多同时保留 20 个任务"; break }
            guard url.isFileURL, url.pathExtension.lowercased() == "pdf" else {
                notice = "只接受本地 PDF 文件"
                continue
            }
            if jobs.contains(where: { $0.phase.active && $0.source == url }) { continue }
            jobs.insert(WidgetJob(id: UUID(), title: url.lastPathComponent, phase: .queued,
                                  source: url), at: 0)
            accepted += 1
        }
        if accepted > 0 {
            notice = "已加入 \(accepted) 个 PDF，正在上传并排队"
            processUploads()
        }
    }

    private func processUploads() {
        guard uploadTask == nil, let client = service else { return }
        let current = generation
        uploadTask = Task {
            defer { if generation == current { uploadTask = nil } }
            while !Task.isCancelled, generation == current,
                  let job = jobs.first(where: { $0.phase == .queued }), let file = job.source {
                update(job.id) { $0.phase = .uploading; $0.message = "上传 PDF…" }
                do {
                    _ = try translationBackend()
                    let paper = try await client.upload(file)
                    guard generation == current, !Task.isCancelled else { return }
                    update(job.id) {
                        $0.paperID = paper.id; $0.title = paper.title; $0.createdAt = paper.createdAt
                        $0.phase = .starting; $0.message = "提交翻译…"
                        $0.source = nil
                    }
                    // One local task per server paper, even after a repeated drop.
                    jobs.removeAll { $0.id != job.id && $0.paperID == paper.id }
                    if ["completed", "translating", "repairing", "repair_pending"].contains(paper.translationStatus) {
                        apply(paper, to: job.id)
                        notice = paper.duplicate == true ? "已找到相同内容，复用已有论文和译文" : notice
                        await refreshLibrary()
                        continue
                    }
                    try await client.start(paper.id, quality: quality, backend: translationBackend())
                    guard generation == current, !Task.isCancelled else { return }
                    update(job.id) { $0.phase = .translating; $0.message = "翻译已排队" }
                    await refreshLibrary()
                } catch {
                    guard generation == current, !Task.isCancelled else { return }
                    update(job.id) { $0.phase = .failed; $0.message = error.localizedDescription }
                    report(error)
                }
            }
        }
    }

    private func startPolling() {
        let current = generation
        pollTask = Task {
            while !Task.isCancelled, current == generation {
                if !needsLogin, let client = service {
                    let pending = jobs.filter { $0.paperID != nil &&
                        [.translating, .waiting, .repairing].contains($0.phase) }
                    if pending.isEmpty, !connected, !connecting {
                        do {
                            try await client.connect()
                            guard generation == current, !Task.isCancelled else { return }
                            connected = true
                            notice = "已重新连接 · 拖入 PDF 即可翻译"
                        } catch {
                            guard generation == current, !Task.isCancelled else { return }
                            report(error)
                        }
                    }
                    for job in pending {
                        if Task.isCancelled || generation != current { return }
                        do {
                            let snapshot = try await client.paper(job.paperID!)
                            guard generation == current, !Task.isCancelled else { return }
                            apply(snapshot, to: job.id)
                        } catch {
                            guard generation == current, !Task.isCancelled else { return }
                            if error as? ClientError == .http(404) || error as? ClientError == .http(403) {
                                update(job.id) { $0.phase = .failed; $0.message = error.localizedDescription }
                            } else {
                                if job.phase != .repairing {
                                    update(job.id) { $0.phase = .waiting; $0.message = "连接中断，等待重连…" }
                                }
                            }
                            report(error)
                        }
                    }
                }
                // At most ~360 polling requests/hour, also when several papers are active.
                let pollingCount = jobs.filter { [.translating, .waiting, .repairing].contains($0.phase) }.count
                let interval = max(10, Double(max(activeCount, pollingCount)) * 10)
                do { try await Task.sleep(nanoseconds: UInt64(interval * 1_000_000_000)) }
                catch { return }
            }
        }
    }

    private func apply(_ paper: PaperSnapshot, to id: UUID, allowAutoOpen: Bool = true) {
        let wasComplete = jobs.first(where: { $0.id == id })?.phase == .completed
        guard jobs.contains(where: { $0.id == id }) else { return }
        update(id) { job in
            job.paperID = paper.id
            job.title = paper.title
            job.createdAt = paper.createdAt
            job.versionNumber = paper.versionNumber
            job.versionCount = paper.versionCount
            job.progress = max(0, min(1, paper.translationProgress))
            job.hasDual = paper.hasDual
            job.hasTranslated = paper.hasTranslated
            job.hasQA = paper.hasQaReport
            let presentation = paper.taskPresentation(cancellationRequested: job.cancellationRequested)
            job.phase = WidgetPhase(rawValue: presentation.state.rawValue) ?? .failed
            job.message = presentation.message
            if job.phase == .completed { job.progress = 1 }
        }
        jobs.removeAll { $0.id != id && $0.paperID == paper.id }
        persistJobs()
        connected = true
        if paper.translationStatus == "completed", paper.hasTranslated {
            notice = activeCount == 0 ? "翻译已完成 · 点击查看译文" : "还有 \(activeCount) 个任务处理中"
        }
        if allowAutoOpen, !wasComplete, paper.translationStatus == "completed", paper.hasTranslated, autoOpen,
           let job = jobs.first(where: { $0.id == id }) { Task { await open(job) } }
    }

    private func reconcileJobs() async {
        guard let client = service else { return }
        let current = generation
        // The durable task cache is bounded to 50 records. No upload or translate
        // request occurs here, including after a lost submission response.
        for job in Array(jobs.prefix(50)) {
            guard current == generation, !Task.isCancelled else { return }
            guard let paperID = job.paperID, jobs.contains(where: { $0.id == job.id }) else { continue }
            do {
                let paper = try await client.paper(paperID)
                guard current == generation, !Task.isCancelled else { return }
                apply(paper, to: job.id, allowAutoOpen: false)
            } catch {
                guard current == generation, !Task.isCancelled else { return }
                if error as? ClientError == .http(404) || error as? ClientError == .http(403) {
                    update(job.id) { $0.phase = .failed; $0.message = error.localizedDescription }
                }
                if error is URLError || error as? ClientError == .http(401) {
                    report(error)
                    return
                }
            }
        }
    }

    func retry(_ job: WidgetJob) async {
        guard let client = service else { return }
        if job.paperID == nil, job.source != nil {
            update(job.id) { $0.phase = .queued; $0.message = "等待重新上传" }
            processUploads()
            return
        }
        guard let paperID = job.paperID else { return }
        let current = generation
        do {
            // A lost POST response may already have started the server job.
            // Reconcile first; never automatically create another translation.
            let paper = try await client.paper(paperID)
            guard current == generation else { return }
            apply(paper, to: job.id)
            if paper.translationStatus == "translating" || paper.translationStatus == "completed" {
                return
            }
            update(job.id) {
                $0.phase = .starting; $0.message = "提交翻译…"; $0.cancellationRequested = false
            }
            try await client.start(paper.id, quality: quality, backend: translationBackend())
            guard current == generation else { return }
            update(job.id) { $0.phase = .translating; $0.message = "翻译已排队" }
        } catch {
            guard current == generation else { return }
            update(job.id) { $0.phase = .failed; $0.message = error.localizedDescription }
            report(error)
        }
    }

    func cancel(_ job: WidgetJob) async {
        if job.phase == .queued {
            update(job.id) { $0.phase = .cancelled; $0.message = "已取消" }
            return
        }
        guard let id = job.paperID, let client = service else { return }
        let current = generation
        do {
            try await client.cancel(id)
            guard current == generation else { return }
            update(job.id) { $0.cancellationRequested = true; $0.message = "正在取消…" }
        } catch {
            guard current == generation else { return }
            if error as? ClientError == .http(409), let paper = try? await client.paper(id) {
                guard current == generation else { return }
                apply(paper, to: job.id)
            } else { report(error) }
        }
    }

    func open(_ job: WidgetJob, reveal: Bool = false) async {
        guard let id = job.paperID, let client = service, let directory = exportDirectory else { return }
        guard variant != .dual || job.hasDual else { notice = "此任务没有双语 PDF，请在设置中选择中文译文"; return }
        let requestedVariant = variant
        let current = generation
        opening = job.id
        defer { if current == generation { opening = nil } }
        do {
            let file = try await client.export(id, variant: requestedVariant, directory: directory,
                title: job.title, createdAt: job.createdAt ?? "")
            guard current == generation else { return }
            if reveal { NSWorkspace.shared.activateFileViewerSelecting([file]) }
            else { try await ExternalPDF.open(file, reader: reader) }
        } catch {
            guard current == generation else { return }
            if error as? ClientError == .http(404) {
                let message = job.phase == .completed ?
                    "译文文件不可用，请在系统网页重新翻译" : "草稿不可用，请重试修复或在系统网页查看任务"
                update(job.id) {
                    if requestedVariant == .translated { $0.hasTranslated = false }
                    else { $0.hasDual = false }
                    if !$0.message.contains(message) { $0.message += " · " + message }
                }
                notice = message
            } else { report(error) }
        }
    }

    func openSystem() {
        guard let origin = try? ServiceOrigin(address) else { return }
        NSWorkspace.shared.open(origin.url)
    }

    func taskDetails(_ job: WidgetJob) async -> String {
        let current = generation
        var detail = "当前状态：\(job.message)"
        if job.phase != .completed, job.hasTranslated == true {
            detail += "\n已保留的译文可作为草稿查看，尚未确认通过质检。"
        }
        if job.hasQA, let id = job.paperID, let client = service {
            do {
                let report = try await client.qualityReport(id)
                guard current == generation else { return "账号已切换，请重新打开任务详情。" }
                let errors = report.issues.filter { $0.severity == "error" }
                detail += "\n\n最近一次质检：\(report.status)，\(errors.count) 个错误"
                for issue in (errors.isEmpty ? report.issues : errors).prefix(8) {
                    detail += "\n第 \(issue.page) 页 · \(issue.code)\n\(issue.message)"
                }
            } catch {
                guard current == generation else { return "账号已切换，请重新打开任务详情。" }
                detail += "\n质检报告读取失败：\(error.localizedDescription)"
                if error as? ClientError == .http(401) { report(error) }
            }
        }
        return detail
    }

    func showTaskDetails(_ job: WidgetJob) async {
        let current = generation
        let detail = await taskDetails(job)
        guard current == generation else { return }
        let alert = NSAlert()
        alert.messageText = job.title
        alert.informativeText = detail
        alert.addButton(withTitle: "关闭")
        alert.addButton(withTitle: "打开系统网页")
        NSApp.activate(ignoringOtherApps: true)
        if alert.runModal() == .alertSecondButtonReturn { openSystem() }
    }

    func clearFinished() {
        jobs.removeAll { !$0.phase.active }
        persistJobs()
    }

    private func update(_ id: UUID, _ change: (inout WidgetJob) -> Void) {
        guard let index = jobs.firstIndex(where: { $0.id == id }) else { return }
        change(&jobs[index])
        persistJobs()
    }

    private var jobsURL: URL? {
        guard let client = service else { return nil }
        return storage.appendingPathComponent(client.namespace).appendingPathComponent("jobs.json")
    }

    private func loadJobs() -> [WidgetJob] {
        guard let file = jobsURL,
              let size = try? file.resourceValues(forKeys: [.fileSizeKey]).fileSize,
              size <= 512_000,
              let data = try? Data(contentsOf: file), data.count <= 512_000,
              let records = try? JSONDecoder().decode([WidgetJob].self, from: data) else { return [] }
        return Array(records.filter {
            guard let id = $0.paperID else { return false }
            return (try? PDFService.validateID(id)) != nil
        }.prefix(50))
    }

    private func persistJobs() {
        guard let file = jobsURL else { return }
        let records = jobs.filter { $0.paperID != nil }.prefix(50).map { job -> WidgetJob in
            var result = job; result.source = nil; return result
        }
        do {
            try FileManager.default.createDirectory(at: file.deletingLastPathComponent(),
                withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
            try JSONEncoder().encode(records).write(to: file, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: file.path)
        } catch { notice = "任务仍在服务端运行，但本机记录未能保存" }
    }

    func refreshLibrary(reset: Bool = true) async {
        guard let client = service, !needsLogin else { return }
        let current = generation
        let request = UUID(); libraryRequest = request
        loadingLibrary = true
        defer { if current == generation && request == libraryRequest { loadingLibrary = false } }
        do {
            let page = try await client.listPapers(search: String(librarySearch.prefix(200)),
                sort: librarySort, offset: reset ? 0 : library.count)
            guard current == generation, request == libraryRequest else { return }
            library = reset ? page.papers : library + page.papers
            libraryTotal = page.total; libraryNotice = ""
            if let directory = exportDirectory {
                // A cache rename failure must not prevent browsing the server library.
                _ = try? DocumentExportName.organizeExisting(directory: directory, papers: page.papers)
            }
        } catch {
            guard current == generation, request == libraryRequest else { return }
            libraryNotice = error.localizedDescription
            report(error)
        }
    }

    func organizeLibrary() async {
        guard let client = service, !organizingLibrary else { return }
        let current = generation
        organizingLibrary = true
        defer { if current == generation { organizingLibrary = false } }
        do {
            let result = try await client.organizeLibrary()
            guard current == generation else { return }
            notice = "整理完成：\(result.indexed) 份旧文档，\(result.duplicates) 份重复，\(result.renamed) 个标题已更新"
            await refreshLibrary()
        } catch { if current == generation { libraryNotice = error.localizedDescription; report(error) } }
    }

    func openLibraryPaper(_ paper: PaperSnapshot, reveal: Bool = false) async {
        let job = WidgetJob(id: UUID(), paperID: paper.id, title: paper.title,
            phase: WidgetPhase(rawValue: paper.taskPresentation().state.rawValue) ?? .failed,
            hasDual: paper.hasDual, hasTranslated: paper.hasTranslated, hasQA: paper.hasQaReport,
            createdAt: paper.createdAt, versionNumber: paper.versionNumber, versionCount: paper.versionCount)
        await open(job, reveal: reveal)
    }

    func showLibraryHistory(_ paper: PaperSnapshot) async {
        guard let client = service else { return }
        let current = generation
        do {
            let history = try await client.libraryHistory(paper.id)
            guard current == generation else { return }
            let lines = history.imports.map { "\(LibraryDate.label($0.importedAt, withTime: true)) · \($0.filename)" }
            let versions = history.versions.map { "\(LibraryDate.label($0.createdAt, withTime: true)) · \($0.title) · \($0.translationStatus)" }
            let alert = NSAlert()
            alert.messageText = paper.title
            alert.informativeText = "导入记录\n" + lines.joined(separator: "\n") + "\n\n内容版本\n" + versions.joined(separator: "\n")
            alert.addButton(withTitle: "关闭")
            NSApp.activate(ignoringOtherApps: true); alert.runModal()
        } catch { if current == generation { libraryNotice = error.localizedDescription; report(error) } }
    }

    private func report(_ error: Error) {
        if error as? ClientError == .http(401) { needsLogin = true; connected = false }
        else if error is URLError { connected = false }
        notice = error is URLError ? "无法连接系统，请检查服务、隧道和悬窗设置" : error.localizedDescription
    }
}
