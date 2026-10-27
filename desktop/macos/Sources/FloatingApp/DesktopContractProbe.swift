import AppKit
import FloatingCore
import Foundation

private struct ProbeFailure: Error, LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

// Executed only by --smoke-pdf against a server explicitly marked as a desktop test fixture.
@MainActor
enum DesktopContractProbe {
    private static func require(_ condition: Bool, _ message: String) throws {
        if !condition { throw ProbeFailure(message: message) }
    }

    private static func wait(_ store: WidgetStore, id: UUID, phases: Set<WidgetPhase>) async throws -> WidgetJob {
        for _ in 0..<100 {
            if let job = store.jobs.first(where: { $0.id == id }), phases.contains(job.phase) { return job }
            try await Task.sleep(nanoseconds: 250_000_000)
        }
        throw ProbeFailure(message: "Fixture task did not reach \(phases): \(store.jobs.map { $0.phase.rawValue })")
    }

    static func run(store: WidgetStore, origin: ServiceOrigin, token: String,
                    storage: URL, source: URL, completed: WidgetJob) async throws -> [String: Bool] {
        var checks: [String: Bool] = [:]
        let suite = "SuperTranslateFloating.Contract.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }

        // Persist an ambiguous starting state, then recover by querying the same server paper.
        let recordsFile = storage.appendingPathComponent(origin.accountNamespace(token: token))
            .appendingPathComponent("jobs.json")
        var records = try JSONDecoder().decode([WidgetJob].self, from: Data(contentsOf: recordsFile))
        records[0].phase = .starting
        try JSONEncoder().encode(records).write(to: recordsFile, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: recordsFile.path)
        let restored = WidgetStore(defaults: defaults, storage: storage)
        try await restored.configure(origin: origin, token: token)
        let recovered = try await wait(restored, id: completed.id, phases: [.completed])
        try require(recovered.paperID == completed.paperID, "Restoration changed the server paper")
        checks["restored_without_submission"] = true

        try await restored.configure(origin: origin, token: "desktop-other-fixture")
        try require(restored.jobs.isEmpty, "Account switch exposed another session's jobs")
        checks["account_isolation"] = true
        try await restored.configure(origin: origin, token: token)

        restored.acceptFiles([source])
        let queued = restored.jobs[0]
        await restored.cancel(queued)
        try require(restored.jobs[0].phase == .cancelled && restored.jobs[0].paperID == nil,
                    "A cancelled queued file was uploaded")
        checks["cancel_before_upload"] = true

        let invalid = storage.appendingPathComponent("invalid.pdf")
        try Data("not a PDF".utf8).write(to: invalid)
        restored.acceptFiles([invalid])
        let rejected = try await wait(restored, id: restored.jobs[0].id, phases: [.failed])
        try require(rejected.paperID == nil, "Invalid PDF reached upload API")
        checks["invalid_pdf_rejected"] = true

        let lost = storage.appendingPathComponent("lost-start-response.pdf")
        try DistinctFixturePDF.write(source: source, to: lost)
        restored.acceptFiles([lost])
        let failed = try await wait(restored, id: restored.jobs[0].id, phases: [.failed])
        try require(failed.paperID != nil, "Lost-response fixture failed before submission")
        try await Task.sleep(nanoseconds: 1_000_000_000)
        await restored.retry(failed)
        _ = try await wait(restored, id: failed.id, phases: [.completed])
        checks["lost_response_reconciled"] = true

        let cancelFile = storage.appendingPathComponent("cancel-fixture.pdf")
        try DistinctFixturePDF.write(source: source, to: cancelFile)
        restored.acceptFiles([cancelFile])
        let running = try await wait(restored, id: restored.jobs[0].id, phases: [.translating])
        await restored.cancel(running)
        _ = try await wait(restored, id: running.id, phases: [.cancelled])
        checks["cancelled_task_restored"] = true

        let redirectClient = try PDFService(origin: origin, token: "desktop-redirect-fixture")
        do { try await redirectClient.connect(); throw ProbeFailure(message: "Redirect was followed") }
        catch ClientError.http(307) { checks["redirect_refused"] = true }

        let loginClient = try PDFService(origin: origin)
        let receipt = try await loginClient.login(username: "desktop_fixture", password: "fixture-password")
        let signedIn = try PDFService(origin: origin, token: receipt.token)
        try await signedIn.connect()
        try require(try SessionVault.read(origin: origin).isEmpty, "Fixture origin already has Keychain data")
        try SessionVault.save(receipt.token, origin: origin)
        defer { try? SessionVault.remove(origin: origin) }
        try require(try SessionVault.read(origin: origin) == receipt.token, "Keychain round-trip failed")
        checks["real_login_and_keychain"] = true

        let racing = WidgetStore(defaults: defaults, storage: storage)
        let first = Task { try await racing.configure(origin: origin, token: "desktop-slow-fixture") }
        try await Task.sleep(nanoseconds: 100_000_000)
        do { try await racing.configure(origin: origin, token: "invalid-fixture-token") }
        catch ClientError.http(401) {}
        try await first.value
        try require(!racing.connected && racing.service?.namespace == origin.accountNamespace(token: "invalid-fixture-token"),
                    "Old connection response changed the new account state")
        checks["stale_connection_ignored"] = true
        let reconnecting = WidgetStore(defaults: defaults, storage: storage)
        do { try await reconnecting.configure(origin: origin, token: "desktop-reconnect-fixture") }
        catch ClientError.http(503) {}
        for _ in 0..<60 {
            if reconnecting.connected { break }
            try await Task.sleep(nanoseconds: 250_000_000)
        }
        try require(reconnecting.connected, "Idle widget did not reconnect after temporary failure")
        checks["idle_reconnection"] = true
        let dataFile = storage.appendingPathComponent("provider-data.pdf")
        try DistinctFixturePDF.write(source: source, to: dataFile)
        let beforeDrop = Set(restored.jobs.map(\.id))
        try require(FloatingWidgetView.contractDrop(store: restored, file: dataFile, asData: true),
                    "Data URL provider was rejected by drop callback")
        var dropped: UUID?
        for _ in 0..<20 {
            dropped = restored.jobs.first(where: { !beforeDrop.contains($0.id) })?.id
            if dropped != nil { break }
            try await Task.sleep(nanoseconds: 100_000_000)
        }
        guard let dropped else { throw ProbeFailure(message: "Data URL provider never added a task") }
        _ = try await wait(restored, id: dropped, phases: [.completed])
        checks["data_provider_drop"] = true
        let modelChecks = try await ModelSelectionProbe.run(origin: origin, token: token, storage: storage, source: source)
        checks.merge(modelChecks) { _, new in new }
        let failureChecks = try await FailurePresentationProbe.run(origin: origin, token: token, storage: storage, source: source)
        checks.merge(failureChecks) { _, new in new }
        return checks
    }
}
