import FloatingCore
import Foundation

@MainActor
enum FailurePresentationProbe {
    static func run(origin: ServiceOrigin, token: String, storage: URL, source: URL) async throws -> [String: Bool] {
        let suite = "SuperTranslateFloating.Failure.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        let directory = storage.appendingPathComponent("failure-presentation")
        let store = WidgetStore(defaults: defaults, storage: directory)
        try await store.configure(origin: origin, token: token)
        let input = storage.appendingPathComponent("qa-failure.pdf")
        try DistinctFixturePDF.write(source: source, to: input)
        store.acceptFiles([input])
        var failed: WidgetJob?
        for _ in 0..<160 {
            if let job = store.jobs.first, job.phase == .failed, job.hasQA {
                failed = job; break
            }
            try await Task.sleep(nanoseconds: 100_000_000)
        }
        guard let job = failed, let id = job.paperID, let client = store.service,
              let exports = store.exportDirectory, job.hasTranslated == true,
              job.message.contains("第 17 页"), job.message.contains("译文已保留") else {
            throw ClientError.invalidResponse
        }
        let details = await store.taskDetails(job)
        guard details.contains("table_structure_mismatch"), details.contains("第 17 页"),
              details.contains("草稿"), details.contains("尚未确认") else {
            throw ClientError.invalidResponse
        }
        let draft = try await client.export(id, variant: .translated, directory: exports)
        guard FileManager.default.fileExists(atPath: draft.path) else { throw ClientError.invalidResponse }
        let encoded = try JSONEncoder().encode(job)
        var legacy = try JSONSerialization.jsonObject(with: encoded) as! [String: Any]
        legacy.removeValue(forKey: "hasTranslated")
        let restored = try JSONDecoder().decode(WidgetJob.self, from: JSONSerialization.data(withJSONObject: legacy))
        guard restored.paperID == id, restored.hasTranslated == nil else { throw ClientError.invalidResponse }
        return ["failed_qa_reason_and_draft_visible": true,
                "authenticated_qa_details_and_draft_download": true,
                "legacy_jobs_remain_decodable": true]
    }
}
