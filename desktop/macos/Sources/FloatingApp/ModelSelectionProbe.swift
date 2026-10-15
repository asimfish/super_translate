import AppKit
import FloatingCore
import Foundation
import SwiftUI

// Uses real settings bindings, store, credential routes and drop callback on the isolated fixture.
@MainActor
enum ModelSelectionProbe {
    private struct Failure: Error, LocalizedError {
        let message: String
        var errorDescription: String? { message }
    }
    private static func require(_ condition: Bool, _ message: String) throws {
        if !condition { throw Failure(message: message) }
    }
    private static func catalogsReady(_ store: WidgetStore) async throws {
        for _ in 0..<100 {
            if store.modelCatalogs.count == 8 && !store.loadingModels { return }
            try await Task.sleep(nanoseconds: 50_000_000)
        }
        throw Failure(message: "Eight-provider catalog did not load")
    }
    private static func finished(_ store: WidgetStore, id: UUID, phase: WidgetPhase) async throws -> WidgetJob {
        for _ in 0..<100 {
            if let job = store.jobs.first(where: { $0.id == id }), job.phase == phase { return job }
            try await Task.sleep(nanoseconds: 100_000_000)
        }
        throw Failure(message: "Model selection task did not reach expected state")
    }

    static func run(origin: ServiceOrigin, token: String, storage: URL, source: URL) async throws -> [String: Bool] {
        var checks: [String: Bool] = [:]
        let suite = "SuperTranslateFloating.Models.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        let directory = storage.appendingPathComponent("model-selection")
        let store = WidgetStore(defaults: defaults, storage: directory)
        try await store.configure(origin: origin, token: token)
        try await catalogsReady(store)
        try require(store.providerStatuses.count == 8, "Provider credential status missing")
        checks["eight_provider_catalog"] = true
        store.selectedProvider = "deepseek"
        try require(store.modelChoices.contains("deepseek-flash"), "Flash is absent from model picker choices")
        store.selectedModel = "deepseek-flash"
        await store.applyModelSelection()
        try require(store.modelSelectionApplied && store.modelNotice.contains("deepseek-flash"), "Flash was not applied")
        let statuses = try await store.service!.providerCredentials()
        try require(statuses.first { $0.provider == "deepseek" }?.model == "deepseek-flash", "Server model differs from selection")
        checks["flash_saved_through_account_api"] = true

        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 520, height: 720),
            styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        let view = NSHostingView(rootView: WidgetSettingsView(store: store))
        window.contentView = view
        window.title = "Super Translate · 模型选择验收"
        window.center(); window.makeKeyAndOrderFront(nil)
        defer { window.close() }
        try await Task.sleep(nanoseconds: 300_000_000)
        view.layoutSubtreeIfNeeded()
        guard let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds) else {
            throw Failure(message: "Settings cannot be captured")
        }
        view.cacheDisplay(in: view.bounds, to: bitmap)
        guard let png = bitmap.representation(using: .png, properties: [:]) else {
            throw Failure(message: "Settings PNG missing")
        }
        try png.write(to: storage.deletingLastPathComponent().appendingPathComponent("model-settings.png"))

        let restored = WidgetStore(defaults: defaults, storage: directory)
        try await restored.configure(origin: origin, token: token)
        try await catalogsReady(restored)
        try require(restored.selectedProvider == "deepseek" && restored.selectedModel == "deepseek-flash", "Restart lost provider/model choice")
        checks["selection_survives_restart"] = true
        let selectedFile = directory.appendingPathComponent("flash-selection.pdf")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700])
        try DistinctFixturePDF.write(source: source, to: selectedFile)
        try require(FloatingWidgetView.contractDrop(store: restored, file: selectedFile), "Selected provider drop rejected")
        for _ in 0..<50 {
            if !restored.jobs.isEmpty { break }
            try await Task.sleep(nanoseconds: 50_000_000)
        }
        guard let submitted = restored.jobs.first else { throw Failure(message: "No model-selected task") }
        let completed = try await finished(restored, id: submitted.id, phase: .completed)
        try require(completed.paperID != nil, "Model-selected task was not submitted")
        checks["flash_selection_drop_completed"] = true

        let unapplied = directory.appendingPathComponent("unapplied.pdf")
        try DistinctFixturePDF.write(source: source, to: unapplied)
        restored.selectedModel = "deepseek-v4-pro"
        restored.acceptFiles([unapplied])
        let blocked = try await finished(restored, id: restored.jobs[0].id, phase: .failed)
        try require(blocked.paperID == nil && blocked.message.contains("应用厂家和模型"), "Unapplied model silently used old model")
        checks["unapplied_model_blocks_submission"] = true

        try await restored.configure(origin: origin, token: "desktop-other-fixture")
        try await catalogsReady(restored)
        try require(restored.selectedProvider.isEmpty && restored.selectedModel.isEmpty, "Account switch retained another account selection")
        try require(restored.providerStatuses.allSatisfy { !$0.configured }, "Account switch exposed personal credentials")
        checks["model_selection_account_isolation"] = true
        let delayed = WidgetStore(defaults: defaults, storage: directory)
        try await delayed.configure(origin: origin, token: "desktop-slow-fixture")
        for _ in 0..<20 {
            if delayed.loadingModels { break }
            try await Task.sleep(nanoseconds: 20_000_000)
        }
        try require(delayed.loadingModels, "Delayed model request was not started")
        try await delayed.configure(origin: origin, token: "desktop-other-fixture")
        try await catalogsReady(delayed)
        try await Task.sleep(nanoseconds: 1_000_000_000)
        try require(delayed.providerStatuses.allSatisfy { !$0.configured } && delayed.selectedModel.isEmpty,
                    "Late model response restored the old account")
        checks["late_model_response_ignored"] = true
        return checks
    }
}
