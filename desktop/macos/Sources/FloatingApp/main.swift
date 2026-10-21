import AppKit
import FloatingCore
import SwiftUI

final class TranslationPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }
}

@MainActor
final class FloatingAppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    private var statusItem: NSStatusItem!
    private var panel: TranslationPanel!
    private var settingsWindow: NSWindow?
    private var hosting: NSHostingView<FloatingWidgetView>!
    private(set) var store: WidgetStore!
    private let args = CommandLine.arguments

    private func argument(_ name: String) -> String? {
        guard let index = args.firstIndex(of: name), index + 1 < args.count else { return nil }
        return args[index + 1]
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        if let file = argument("--transport-pdf"), let report = argument("--transport-report") {
            let address = argument("--server") ?? UserDefaults.standard.string(forKey: "server") ?? "http://127.0.0.1:18001"
            Task {
                await TransportProbe.run(address: address, file: URL(fileURLWithPath: file),
                    report: URL(fileURLWithPath: report))
            }
            return
        }
        let testing = argument("--smoke-pdf") != nil || argument("--snapshot") != nil
        let defaults = testing ? UserDefaults(suiteName: "SuperTranslateFloating.Preview.\(UUID().uuidString)")! : .standard
        if let address = argument("--server") ?? ProcessInfo.processInfo.environment["SUPER_TRANSLATE_SERVER_URL"] {
            defaults.set(address, forKey: "server")
        }
        let storage = testing ? URL(fileURLWithPath: argument("--test-storage") ??
            FileManager.default.temporaryDirectory.appendingPathComponent("SuperTranslate-\(UUID().uuidString)").path) : nil
        store = WidgetStore(defaults: defaults, storage: storage)
        if testing { store.collapsed = false }
        setupPanel()
        setupMenu()
        if let file = argument("--smoke-pdf"), let report = argument("--smoke-report") {
            Task { await runSmoke(file: URL(fileURLWithPath: file), report: URL(fileURLWithPath: report)) }
        } else if let file = argument("--verify-pdf"), let report = argument("--verify-report") {
            store.collapsed = false
            Task {
                await RealTranslationProbe.run(store: store, file: URL(fileURLWithPath: file),
                    report: URL(fileURLWithPath: report), showLogin: { self.showSettings() },
                    snapshot: {
                        if let path = self.argument("--verify-snapshot") {
                            self.writeSnapshot(URL(fileURLWithPath: path))
                        }
                    }, openBrowser: args.contains("--verify-open-browser"),
                    openAdobe: args.contains("--verify-open-adobe"))
            }
        } else if let snapshot = argument("--snapshot") {
            DispatchQueue.main.asyncAfter(deadline: .now() + 1) {
                self.writeSnapshot(URL(fileURLWithPath: snapshot))
                NSApp.terminate(nil)
            }
        } else { Task { await store.reconnect() } }
    }

    private func setupPanel() {
        panel = TranslationPanel(contentRect: NSRect(x: 0, y: 0, width: store.panelWidth,
            height: store.panelHeight), styleMask: [.borderless, .nonactivatingPanel, .fullSizeContentView],
            backing: .buffered, defer: false)
        panel.level = .floating
        panel.isFloatingPanel = true
        panel.hidesOnDeactivate = false
        panel.isReleasedWhenClosed = false
        panel.becomesKeyOnlyIfNeeded = true
        panel.backgroundColor = .clear
        panel.isOpaque = false
        panel.hasShadow = true
        panel.isMovableByWindowBackground = true
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.delegate = self
        hosting = NSHostingView(rootView: FloatingWidgetView(store: store,
            settings: { [weak self] in self?.showSettings() },
            layout: { [weak self] in self?.resizePanel() },
            emphasize: { [weak self] active in self?.setEmphasis(active) }))
        panel.contentView = hosting
        if let screen = NSScreen.main {
            let x = store.defaults.object(forKey: "windowX") as? Double ??
                (screen.visibleFrame.maxX - store.panelWidth - 24)
            let y = store.defaults.object(forKey: "windowY") as? Double ??
                (screen.visibleFrame.maxY - store.panelHeight - 60)
            panel.setFrameOrigin(NSPoint(x: x, y: y))
        }
        resizePanel()
        setEmphasis(false)
        panel.orderFrontRegardless()
    }

    private func resizePanel() {
        guard panel != nil else { return }
        let old = panel.frame
        let screen = panel.screen ?? NSScreen.main
        let visible = screen?.visibleFrame ?? old
        let width = store.panelWidth
        let height = min(store.panelHeight, visible.height - 24)
        var frame = NSRect(x: old.maxX - width, y: old.maxY - height, width: width, height: height)
        frame.origin.x = max(visible.minX + 8, min(frame.minX, visible.maxX - width - 8))
        frame.origin.y = max(visible.minY + 8, min(frame.minY, visible.maxY - height - 8))
        panel.setFrame(frame, display: true)
    }

    private func setEmphasis(_ active: Bool) {
        panel?.alphaValue = active ? 1 : max(0.3, min(1, store.idleOpacity))
    }

    func windowDidMove(_ notification: Notification) {
        guard (notification.object as? NSWindow) === panel else { return }
        store.defaults.set(panel.frame.origin.x, forKey: "windowX")
        store.defaults.set(panel.frame.origin.y, forKey: "windowY")
    }

    private func setupMenu() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        statusItem.button?.image = NSImage(systemSymbolName: "doc.text", accessibilityDescription: "Super Translate")
        statusItem.button?.toolTip = "Super Translate PDF 翻译悬窗"
        let menu = NSMenu()
        for (title, action) in [("显示悬窗", #selector(showPanel)),
                                ("隐藏悬窗", #selector(hidePanel)),
                                ("打开系统", #selector(openSystem)),
                                ("设置…", #selector(showSettings))] {
            let item = NSMenuItem(title: title, action: action, keyEquivalent: "")
            item.target = self
            menu.addItem(item)
        }
        menu.addItem(.separator())
        let quit = NSMenuItem(title: "退出悬窗", action: #selector(quit), keyEquivalent: "q")
        quit.target = self
        menu.addItem(quit)
        statusItem.menu = menu
    }

    @objc private func showPanel() { panel.orderFrontRegardless() }
    @objc private func hidePanel() { panel.orderOut(nil) }
    @objc private func openSystem() { store.openSystem() }
    @objc private func quit() { NSApp.terminate(nil) }

    @objc private func showSettings() {
        if settingsWindow == nil {
            let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 520, height: 720),
                styleMask: [.titled, .closable], backing: .buffered, defer: false)
            window.title = "Super Translate · 悬窗设置"
            window.isReleasedWhenClosed = false
            window.contentView = NSHostingView(rootView: WidgetSettingsView(store: store))
            window.center()
            settingsWindow = window
        }
        settingsWindow?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    private func writeSnapshot(_ url: URL) {
        hosting.layoutSubtreeIfNeeded()
        if let bitmap = hosting.bitmapImageRepForCachingDisplay(in: hosting.bounds) {
            hosting.cacheDisplay(in: hosting.bounds, to: bitmap)
            if let data = bitmap.representation(using: .png, properties: [:]) {
                try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(),
                    withIntermediateDirectories: true)
                try? data.write(to: url, options: .atomic)
            }
        }
    }

    private func runSmoke(file: URL, report: URL) async {
        var result: [String: Any] = ["status": "FAILED", "translation_worker": "isolated fixture",
            "panel_floating": panel.level == .floating,
            "panel_transparent": !panel.isOpaque && panel.backgroundColor == .clear,
            "panel_nonactivating": panel.styleMask.contains(.nonactivatingPanel),
            "panel_visible": panel.isVisible]
        do {
            let origin = try ServiceOrigin(store.address)
            let (_, response) = try await URLSession.shared.data(from: origin.endpoint("/health"))
            guard (response as? HTTPURLResponse)?.value(forHTTPHeaderField: "X-SuperTranslate-Desktop-Test") == "1"
            else { throw ClientError.invalidResponse }
            let token = ProcessInfo.processInfo.environment["SUPER_TRANSLATE_SMOKE_TOKEN"] ?? ""
            try await store.configure(origin: origin, token: token)
            guard FloatingWidgetView.contractDrop(store: store, file: file) else { throw ClientError.invalidResponse }
            for _ in 0..<100 {
                if store.jobs.first?.phase == .completed { break }
                if store.jobs.first?.phase == .failed { throw ClientError.invalidResponse }
                try await Task.sleep(nanoseconds: 500_000_000)
            }
            guard let job = store.jobs.first, job.phase == .completed, let id = job.paperID,
                  let client = store.service, let directory = store.exportDirectory
            else { throw ClientError.invalidResponse }
            let pdf = try await client.export(id, variant: .translated, directory: directory,
                title: job.title, createdAt: job.createdAt ?? "")
            guard let browser = ExternalPDF.application(for: .browser) else { throw ReaderError.unavailable(.browser) }
            if args.contains("--smoke-open-browser") { try await ExternalPDF.open(pdf, reader: .browser) }
            if args.contains("--smoke-open-adobe") { try await ExternalPDF.open(pdf, reader: .adobe) }
            result["status"] = "PASSED"
            result["paper_id"] = id
            result["url_provider_drop"] = true
            result["pdf"] = pdf.path
            result["browser_application"] = browser.lastPathComponent
            result["adobe_available"] = ExternalPDF.application(for: .adobe) != nil
            result["browser_opened"] = args.contains("--smoke-open-browser")
            result["adobe_opened"] = args.contains("--smoke-open-adobe")
            store.notice = "已完成 · 点击查看中文译文"
            setEmphasis(true)
            try await Task.sleep(nanoseconds: 200_000_000)
            if let path = argument("--smoke-snapshot") { writeSnapshot(URL(fileURLWithPath: path)) }
            store.collapsed = true
            try await Task.sleep(nanoseconds: 200_000_000)
            guard panel.frame.width == 66, panel.frame.height == 72 else { throw ClientError.invalidResponse }
            result["compact_panel"] = true
            if let path = argument("--smoke-compact-snapshot") { writeSnapshot(URL(fileURLWithPath: path)) }
            store.collapsed = false
            if args.contains("--smoke-contract"), let path = argument("--test-storage") {
                result["contract_checks"] = try await DesktopContractProbe.run(store: store, origin: origin,
                    token: token, storage: URL(fileURLWithPath: path), source: file, completed: job)
            }
            if let path = argument("--library-changed-pdf") {
                result["library_checks"] = try await DocumentLibraryProbe.run(store: store,
                    source: file, changed: URL(fileURLWithPath: path), artifacts: report.deletingLastPathComponent())
            }
        } catch { result["status"] = "FAILED"; result["error"] = error.localizedDescription }
        try? FileManager.default.createDirectory(at: report.deletingLastPathComponent(), withIntermediateDirectories: true)
        if let data = try? JSONSerialization.data(withJSONObject: result, options: [.prettyPrinted, .sortedKeys]) {
            try? data.write(to: report, options: .atomic)
        }
        if result["status"] as? String == "PASSED" { NSApp.terminate(nil) }
        else { exit(1) }
    }
}

MainActor.assumeIsolated {
    let application = NSApplication.shared
    let delegate = FloatingAppDelegate()
    application.delegate = delegate
    application.run()
}
