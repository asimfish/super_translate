import AppKit
import FloatingCore

@MainActor
enum TransportProbe {
    static func run(address: String, file: URL, report: URL) async {
        var result: [String: Any] = ["status": "RUNNING", "checks": [[String: Any]]()]
        func save() {
            guard let data = try? JSONSerialization.data(withJSONObject: result, options: [.prettyPrinted, .sortedKeys]) else { return }
            try? data.write(to: report, options: .atomic)
            try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: report.path)
        }
        save()
        do {
            let origin = try ServiceOrigin(address)
            guard origin.url.scheme == "http", ["127.0.0.1", "localhost"].contains(origin.url.host ?? "") else {
                throw ClientError.invalidServer
            }
            let token = try SessionVault.read(origin: origin)
            guard !token.isEmpty else { throw ClientError.http(401) }
            for direct in [false, true] {
                var check: [String: Any] = ["proxy": direct ? "disabled for loopback" : "system"]
                let configuration = URLSessionConfiguration.ephemeral
                if direct {
                    configuration.connectionProxyDictionary = ["HTTPEnable": 0, "HTTPSEnable": 0,
                        "SOCKSEnable": 0, "ProxyAutoConfigEnable": 0, "ProxyAutoDiscoveryEnable": 0]
                }
                let client = try PDFService(origin: origin, token: token, configuration: configuration)
                let started = Date()
                do {
                    try await client.connect()
                    check["authenticated"] = true
                    check["connect_seconds"] = Date().timeIntervalSince(started)
                    result["active_proxy"] = check["proxy"]
                    save()
                    let uploadedAt = Date()
                    let paper = try await client.upload(file)
                    check["upload_seconds"] = Date().timeIntervalSince(uploadedAt)
                    check["paper_id"] = paper.id
                    check["upload"] = "PASSED"
                } catch {
                    let error = error as NSError
                    check["upload"] = "FAILED"
                    check["error"] = error.localizedDescription
                    check["error_domain"] = error.domain
                    check["error_code"] = error.code
                    check["elapsed_seconds"] = Date().timeIntervalSince(started)
                }
                var checks = result["checks"] as? [[String: Any]] ?? []
                checks.append(check)
                result["checks"] = checks
                save()
            }
            result["status"] = "FINISHED"
        } catch { result["status"] = "FAILED"; result["error"] = error.localizedDescription }
        save()
        NSApp.terminate(nil)
    }
}
