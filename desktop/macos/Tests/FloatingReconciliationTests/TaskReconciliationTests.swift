import FloatingCore
import Foundation
import XCTest
@testable import FloatingAppSupport

final class TaskReconciliationTests: XCTestCase {
    @MainActor
    private func configuredStore(records: [[String: Any]]) async throws -> (WidgetStore, URL) {
        let origin = try ServiceOrigin(ProcessInfo.processInfo.environment["PAPER_TASK_FIXTURE_ORIGIN"]!)
        let token = "fixture-token"
        let storage = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let cache = storage.appendingPathComponent(origin.accountNamespace(token: token))
        try FileManager.default.createDirectory(at: cache, withIntermediateDirectories: true)
        try JSONSerialization.data(withJSONObject: records).write(to: cache.appendingPathComponent("jobs.json"))
        let suite = "SuperTranslateFloating.Reconcile.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defaults.set(false, forKey: "autoOpen")
        let store = WidgetStore(defaults: defaults, storage: storage)
        try await store.configure(origin: origin, token: token)
        defaults.removePersistentDomain(forName: suite)
        return (store, storage)
    }

    private func legacy(id: String, title: String, phase: String = "failed") -> [String: Any] {
        ["id": UUID().uuidString, "paperID": id, "title": title, "phase": phase,
         "progress": 0.9, "message": "结果尚不可用，请在系统网页检查", "hasDual": true,
         "hasTranslated": true, "hasQA": true, "cancellationRequested": false]
    }

    @MainActor
    func testLegacyEndedTasksRefreshAndCanonicalCopiesCollapse() async throws {
        let (store, storage) = try await configuredStore(records: [
            legacy(id: "ab1b0a7b2f5b", title: "s43588-025-00924-4.pdf", phase: "completed"),
            legacy(id: "f29440f9d7ba", title: "s43588-025-00924-4.pdf", phase: "completed"),
            legacy(id: "f0354b8795f3", title: "s43588-025-00924-4.pdf"),
            legacy(id: "012345abcde1", title: "Same title"),
            legacy(id: "012345abcde2", title: "Same title")])
        defer { try? FileManager.default.removeItem(at: storage) }
        XCTAssertEqual(store.jobs.count, 3, "Repeated content must have one task; different contents stay separate")
        XCTAssertEqual(store.jobs.filter { $0.paperID == "f0354b8795f3" }.count, 1)
        let canonical = try XCTUnwrap(store.jobs.first { $0.paperID == "f0354b8795f3" })
        XCTAssertEqual(canonical.phase.rawValue, "completed")
        XCTAssertEqual(canonical.title, "Full scientific paper title")
        XCTAssertEqual(Set(store.jobs.compactMap(\.paperID)), ["f0354b8795f3", "012345abcde1", "012345abcde2"])
        let cache = try XCTUnwrap(store.service).namespace
        let saved = try JSONDecoder().decode([WidgetJob].self, from: Data(contentsOf:
            storage.appendingPathComponent(cache).appendingPathComponent("jobs.json")))
        XCTAssertEqual(saved.count, 3, "Repaired task identity must survive app restart")
    }

    @MainActor
    func testRepairingDraftKeepsActualFailureReasonAndNeverClaimsCompleted() async throws {
        let (store, storage) = try await configuredStore(records: [
            legacy(id: "012345abcde3", title: "Repair paper")])
        defer { try? FileManager.default.removeItem(at: storage) }
        let job = try XCTUnwrap(store.jobs.first)
        XCTAssertEqual(job.phase.rawValue, "repairing")
        XCTAssertFalse(job.phase.active, "A parked repair is not an actively translating worker")
        XCTAssertTrue(job.hasTranslated == true)
        XCTAssertTrue(job.message.contains("第 17 页"))
        XCTAssertTrue(job.message.contains("草稿"))
        let details = await store.taskDetails(job)
        XCTAssertTrue(details.contains("尚未确认通过质检"))
        XCTAssertTrue(details.contains("table_structure_mismatch"))
    }

    @MainActor
    func testDroppingParkedDuplicateDoesNotSilentlyStartAnotherTranslation() async throws {
        let (store, storage) = try await configuredStore(records: [])
        defer { try? FileManager.default.removeItem(at: storage) }
        let file = storage.appendingPathComponent("duplicate-repair.pdf")
        try Data("%PDF-1.7\nfixture\n%%EOF\n".utf8).write(to: file)
        store.acceptFiles([file])
        for _ in 0..<100 {
            if let job = store.jobs.first, !["queued", "uploading", "starting"].contains(job.phase.rawValue) { break }
            try await Task.sleep(nanoseconds: 20_000_000)
        }
        XCTAssertEqual(store.jobs.first?.phase.rawValue, "repairing")
        XCTAssertTrue(store.jobs.first?.message.contains("第 17 页") == true)
    }

    @MainActor
    func testMissingDraftDownloadExplainsUnavailableAndKeepsRepairState() async throws {
        let (store, storage) = try await configuredStore(records: [
            legacy(id: "012345abcde3", title: "Repair paper")])
        defer { try? FileManager.default.removeItem(at: storage) }
        let job = try XCTUnwrap(store.jobs.first)
        await store.open(job)
        XCTAssertEqual(store.jobs.first?.phase.rawValue, "repairing")
        XCTAssertEqual(store.jobs.first?.hasTranslated, false)
        XCTAssertTrue(store.notice.contains("草稿不可用"))
        XCTAssertTrue(store.notice.contains("重试修复"))
    }
}
