import AppKit
import FloatingCore
import PDFKit

// Explicit opt-in acceptance against the user's configured service and account.
// It uses the normal store, upload queue and onDrop callback; no fixture worker.
@MainActor
enum RealTranslationProbe {
    private struct Failure: Error, LocalizedError {
        let message: String
        var errorDescription: String? { message }
    }

    static func run(store: WidgetStore, file: URL, report: URL,
                    showLogin: () -> Void, snapshot: () -> Void,
                    openBrowser: Bool, openAdobe: Bool) async {
        var result: [String: Any] = ["status": "CONNECTING", "worker": "configured production service",
            "source": file.path, "server": store.address, "quality": store.quality]
        func save() {
            do {
                try FileManager.default.createDirectory(at: report.deletingLastPathComponent(),
                    withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
                let data = try JSONSerialization.data(withJSONObject: result, options: [.prettyPrinted, .sortedKeys])
                try data.write(to: report, options: .atomic)
                try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: report.path)
            } catch { /* Keep the user's translation running if evidence storage is unavailable. */ }
        }
        save()
        do {
            _ = try PDFService.validatedPDF(file)
            let origin = try ServiceOrigin(store.address)
            let (_, response) = try await URLSession.shared.data(from: origin.endpoint("/health"))
            guard let http = response as? HTTPURLResponse, http.statusCode == 200,
                  http.value(forHTTPHeaderField: "X-SuperTranslate-Desktop-Test") != "1"
            else { throw Failure(message: "真实验收需要已连接的正式翻译服务。") }
            await store.reconnect()
            var loginShown = false
            for _ in 0..<600 {
                if store.connected { break }
                if store.needsLogin && !loginShown { showLogin(); loginShown = true }
                result["status"] = store.needsLogin ? "WAITING_FOR_LOGIN" : "CONNECTING"
                save()
                try await Task.sleep(nanoseconds: 2_000_000_000)
            }
            guard store.connected else { throw Failure(message: "未完成连接或登录，未提交翻译。") }
            let existing = Set(store.jobs.map(\.id))
            guard FloatingWidgetView.contractDrop(store: store, file: file) else {
                throw Failure(message: "PDF 拖放回调未接受文件。")
            }
            var submitted: WidgetJob?
            for _ in 0..<600 {
                if let job = store.jobs.first(where: { !existing.contains($0.id) }) {
                    submitted = job
                    result["status"] = "TRANSLATING"
                    result["phase"] = job.phase.rawValue
                    result["progress"] = job.progress
                    if let id = job.paperID { result["paper_id"] = id }
                    save()
                    if job.phase == .completed { break }
                    if [.failed, .cancelled].contains(job.phase) {
                        throw Failure(message: "翻译任务未完成：\(job.message)")
                    }
                }
                try await Task.sleep(nanoseconds: 2_000_000_000)
            }
            guard let job = submitted, job.phase == .completed, let id = job.paperID,
                  let client = store.service, let directory = store.exportDirectory else {
                throw Failure(message: "验收等待超时；已提交任务仍会在服务端继续，可在悬窗查看。")
            }
            let pdf = try await client.export(id, variant: .translated, directory: directory)
            guard let document = PDFDocument(url: pdf), let text = document.string else {
                throw Failure(message: "下载的译文无法作为 PDF 读取。")
            }
            let chinese = text.unicodeScalars.filter { (0x4E00...0x9FFF).contains($0.value) }.count
            guard chinese >= 100 else { throw Failure(message: "译文缺少足够中文文本，不能通过真实翻译验收。") }
            result["pdf"] = pdf.path
            result["pages"] = document.pageCount
            result["chinese_characters"] = chinese
            result["drop_callback"] = true
            if openBrowser { try await ExternalPDF.open(pdf, reader: .browser) }
            if openAdobe { try await ExternalPDF.open(pdf, reader: .adobe) }
            result["browser_opened"] = openBrowser
            result["adobe_opened"] = openAdobe
            result["status"] = "PASSED"
            snapshot()
            save()
            NSApp.terminate(nil)
        } catch {
            result["status"] = "FAILED"
            result["error"] = error.localizedDescription
            save()
            // Leave the normal widget available so the user can inspect the task.
            store.notice = "真实验收未通过，可在任务中查看原因"
        }
    }
}
