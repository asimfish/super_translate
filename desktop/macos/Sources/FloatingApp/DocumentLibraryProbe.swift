import AppKit
import FloatingCore
import Foundation
import SwiftUI

enum DocumentLibraryProbe {
    @MainActor static func run(store: WidgetStore, source: URL, changed: URL,
                              artifacts: URL) async throws -> [String: Any] {
        guard let first = store.jobs.first, let firstID = first.paperID,
              let client = store.service, let directory = store.exportDirectory
        else { throw ClientError.invalidResponse }
        let copy = artifacts.appendingPathComponent("renamed-copy.pdf")
        try FileManager.default.copyItem(at: source, to: copy)
        guard FloatingWidgetView.contractDrop(store: store, file: copy) else { throw ClientError.invalidResponse }
        for _ in 0..<150 {
            if store.jobs.first?.phase == .failed { throw ClientError.invalidResponse }
            if store.jobs.first?.id != first.id && store.jobs.first?.phase == .completed &&
                store.jobs.first?.source == nil { break }
            try await Task.sleep(nanoseconds: 100_000_000)
        }
        guard store.jobs.first?.paperID == firstID, store.jobs.filter({ $0.paperID == firstID }).count == 1
        else { throw ClientError.invalidResponse }
        store.librarySearch = copy.lastPathComponent
        await store.refreshLibrary()
        guard store.library.count == 1, let paper = store.library.first,
              paper.id == firstID, paper.importCount == 2, first.title == paper.title
        else { throw ClientError.invalidResponse }
        let exported = try await client.export(firstID, variant: .translated, directory: directory,
            title: paper.title, createdAt: paper.createdAt ?? "")
        guard exported.lastPathComponent.contains(DocumentExportName.safeTitle(paper.title)),
              exported.path.contains("/\(DocumentExportName.safeTitle(paper.title))--\(paper.id)/")
        else { throw ClientError.invalidResponse }
        guard FloatingWidgetView.contractDrop(store: store, file: changed) else { throw ClientError.invalidResponse }
        for _ in 0..<150 {
            if store.jobs.first?.phase == .failed { throw ClientError.invalidResponse }
            if store.jobs.first?.phase == .completed && store.jobs.first?.paperID != firstID { break }
            try await Task.sleep(nanoseconds: 100_000_000)
        }
        guard let secondID = store.jobs.first?.paperID, secondID != firstID else { throw ClientError.invalidResponse }
        store.librarySearch = ""
        await store.refreshLibrary()
        guard store.libraryTotal == 2, store.library.allSatisfy({ $0.versionCount == 2 })
        else { throw ClientError.invalidResponse }
        let history = try await client.libraryHistory(firstID)
        guard history.imports.count == 2, history.versions.count == 2 else { throw ClientError.invalidResponse }
        store.clearFinished()
        await store.refreshLibrary()
        guard store.jobs.isEmpty, store.libraryTotal == 2 else { throw ClientError.invalidResponse }
        let window = NSWindow(contentRect: NSRect(x: 150, y: 150, width: 460, height: 490),
            styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.title = "文献库验收"
        let view = NSHostingView(rootView: DocumentLibraryView(store: store))
        window.contentView = view; window.makeKeyAndOrderFront(nil)
        try await Task.sleep(nanoseconds: 500_000_000)
        if let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds) {
            view.cacheDisplay(in: view.bounds, to: bitmap)
            try bitmap.representation(using: .png, properties: [:])?.write(
                to: artifacts.appendingPathComponent("library.png"))
        }
        window.close()
        return ["same_content_reuses_id": true, "same_content_one_local_job": true,
            "server_title_used": true, "filename_alias_search": true,
            "changed_content_kept_as_version": true, "import_history": true,
            "clear_task_cache_keeps_library": true, "title_month_export": true,
            "export_path": exported.path, "paper_ids": [firstID, secondID]]
    }
}
