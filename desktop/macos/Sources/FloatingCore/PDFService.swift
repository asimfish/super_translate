import CryptoKit
import Foundation
import Security

public enum ClientError: Error, LocalizedError, Equatable {
    case invalidServer, invalidPDF, oversizedPDF, invalidResponse, http(Int), keychain(Int32)

    public var errorDescription: String? {
        switch self {
        case .invalidServer: return "服务地址须为 HTTPS 或本机 HTTP 地址，不含路径、账号或参数。"
        case .invalidPDF: return "请选择有效的本地 PDF 文件。"
        case .oversizedPDF: return "PDF 超过 100 MB，请拆分后再拖入。"
        case .invalidResponse: return "服务返回的结果无效，请在系统网页检查任务。"
        case .http(401): return "请在悬窗设置中登录你的系统账号。"
        case .http(403): return "当前账号无权访问这份论文。"
        case .http(429): return "请求较频繁，稍后会重新连接。"
        case .http(let code): return "服务请求失败（HTTP \(code)），请在系统网页检查模型配置和任务。"
        case .keychain: return "无法访问 macOS 钥匙串，登录信息未保存。"
        }
    }
}

public struct ServiceOrigin: Equatable, Sendable {
    public let url: URL

    public init(_ value: String) throws {
        guard var parts = URLComponents(string: value.trimmingCharacters(in: .whitespacesAndNewlines)),
              let scheme = parts.scheme?.lowercased(), let host = parts.host?.lowercased(),
              !host.isEmpty, parts.user == nil, parts.password == nil,
              parts.query == nil, parts.fragment == nil,
              parts.path.isEmpty || parts.path == "/",
              parts.port == nil || (1...65535).contains(parts.port!),
              scheme == "https" || (scheme == "http" &&
                  ["localhost", "127.0.0.1", "::1", "[::1]"].contains(host))
        else { throw ClientError.invalidServer }
        parts.scheme = scheme
        parts.host = host
        parts.path = ""
        if (scheme == "https" && parts.port == 443) || (scheme == "http" && parts.port == 80) {
            parts.port = nil
        }
        guard let url = parts.url else { throw ClientError.invalidServer }
        self.url = url
    }

    public func endpoint(_ path: String, query: [URLQueryItem] = []) -> URL {
        var parts = URLComponents(url: url, resolvingAgainstBaseURL: false)!
        parts.path = path
        parts.queryItems = query.isEmpty ? nil : query
        return parts.url!
    }

    public func accountNamespace(token: String) -> String {
        SHA256.hash(data: Data((url.absoluteString + "\u{0}" + token).utf8))
            .prefix(12).map { String(format: "%02x", $0) }.joined()
    }
}

public struct PaperSnapshot: Decodable, Sendable {
    public let id: String
    public let title: String
    public let translationStatus: String
    public let translationProgress: Double
    public let translationStage: String
    public let translationEta: String
    public let translationError: String?
    public let hasTranslated: Bool
    public let hasDual: Bool
    public let hasQaReport: Bool
    public let originalFilename: String?
    public let createdAt: String?
    public let lastImportedAt: String?
    public let importCount: Int?
    public let versionCount: Int?
    public let versionNumber: Int?
    public let duplicate: Bool?
    public let canonicalPaperId: String?
}

public struct LoginReceipt: Decodable, Sendable {
    public let token: String
    public let username: String
}

public struct QualityIssue: Decodable, Sendable {
    public let page: Int
    public let code: String
    public let message: String
    public let severity: String
}

public struct QualityReport: Decodable, Sendable {
    public let status: String
    public let issues: [QualityIssue]
}

public struct ProviderCredentialStatus: Decodable, Sendable {
    public let provider: String
    public let label: String
    public var model: String
    public let configured: Bool
    public let source: String
}

public struct ProviderModelOption: Decodable, Sendable {
    public let id: String
    public let description: String
    public let availability: String
}

public struct ProviderModelCatalog: Decodable, Sendable {
    public let provider: String
    public let label: String
    public let defaultModel: String
    public var selectedModel: String
    public let models: [String]
    public let modelOptions: [ProviderModelOption]
    public let source: String
    public let warning: String
}

public enum TranslationProvider: String, CaseIterable, Sendable {
    case deepseek, kimi, openai, anthropic, glm, gemini, qwen, doubao
    public var title: String {
        switch self {
        case .deepseek: return "DeepSeek"
        case .kimi: return "Kimi"
        case .openai: return "OpenAI"
        case .anthropic: return "Anthropic"
        case .glm: return "智谱 GLM"
        case .gemini: return "Gemini"
        case .qwen: return "通义千问 Qwen"
        case .doubao: return "豆包"
        }
    }
}

private struct TranslationReceipt: Decodable {
    let ok: Bool
    let status: String
}

public enum PDFVariant: String, CaseIterable, Codable, Sendable {
    case translated, dual
    public var title: String { self == .translated ? "中文译文" : "双语对照" }
}

public enum PDFReader: String, CaseIterable, Codable {
    case browser, adobe, system
    public var title: String {
        switch self {
        case .browser: return "浏览器 PDF 阅读器"
        case .adobe: return "Adobe Acrobat / Reader"
        case .system: return "系统默认 PDF 阅读器"
        }
    }
}

public enum SessionVault {
    private static let service = "com.supertranslate.floating.session"

    private static func query(_ origin: ServiceOrigin) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: origin.url.absoluteString]
    }

    public static func read(origin: ServiceOrigin) throws -> String {
        var lookup = query(origin)
        lookup[kSecReturnData as String] = true
        lookup[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(lookup as CFDictionary, &result)
        if status == errSecItemNotFound { return "" }
        guard status == errSecSuccess, let data = result as? Data,
              let token = String(data: data, encoding: .utf8)
        else { throw ClientError.keychain(status) }
        return token
    }

    public static func save(_ token: String, origin: ServiceOrigin) throws {
        let lookup = query(origin)
        let attributes: [String: Any] = [kSecValueData as String: Data(token.utf8)]
        let status = SecItemUpdate(lookup as CFDictionary, attributes as CFDictionary)
        if status == errSecItemNotFound {
            var item = lookup.merging(attributes) { _, new in new }
            item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
            let added = SecItemAdd(item as CFDictionary, nil)
            guard added == errSecSuccess else { throw ClientError.keychain(added) }
        } else if status != errSecSuccess { throw ClientError.keychain(status) }
    }

    public static func remove(origin: ServiceOrigin) throws {
        let status = SecItemDelete(query(origin) as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound
        else { throw ClientError.keychain(status) }
    }
}

// Never forward a bearer token or an upload to an HTTP redirect destination.
private final class NoRedirects: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}

public actor PDFService {
    public nonisolated let origin: ServiceOrigin
    public nonisolated let namespace: String
    private let token: String
    private let session: URLSession

    public init(origin: ServiceOrigin, token: String = "",
                configuration: URLSessionConfiguration = .ephemeral) throws {
        guard !token.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) })
        else { throw ClientError.invalidResponse }
        self.origin = origin
        self.token = token
        self.namespace = origin.accountNamespace(token: token)
        configuration.timeoutIntervalForRequest = 30
        configuration.timeoutIntervalForResource = 180
        configuration.httpCookieStorage = nil
        configuration.urlCache = nil
        self.session = URLSession(configuration: configuration, delegate: NoRedirects(),
                                  delegateQueue: nil)
    }

    public static func validateID(_ id: String) throws {
        guard id.range(of: "^[0-9a-f]{12}$", options: .regularExpression) != nil
        else { throw ClientError.invalidResponse }
    }

    public static func validatedPDF(_ url: URL) throws -> Data {
        guard url.isFileURL, url.pathExtension.lowercased() == "pdf" else {
            throw ClientError.invalidPDF
        }
        let values = try url.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey])
        guard values.isRegularFile == true, values.isSymbolicLink != true else {
            throw ClientError.invalidPDF
        }
        guard let size = values.fileSize, size > 0 else { throw ClientError.invalidPDF }
        guard size <= 100 * 1024 * 1024 else { throw ClientError.oversizedPDF }
        let data = try Data(contentsOf: url, options: .mappedIfSafe)
        guard data.count <= 100 * 1024 * 1024 else { throw ClientError.oversizedPDF }
        guard data.starts(with: Data("%PDF".utf8)),
              data.suffix(1024).range(of: Data("%%EOF".utf8)) != nil
        else { throw ClientError.invalidPDF }
        return data
    }

    public static func multipart(data: Data, filename: String, boundary: String) -> Data {
        let safeName = filename.unicodeScalars.map { scalar -> String in
            if CharacterSet.controlCharacters.contains(scalar) || "\"\\/".unicodeScalars.contains(scalar) {
                return "_"
            }
            return String(scalar)
        }.joined()
        var body = Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"file\"; filename=\"\(safeName)\"\r\nContent-Type: application/pdf\r\n\r\n".utf8)
        body.append(data)
        body.append(Data("\r\n--\(boundary)--\r\n".utf8))
        return body
    }

    private func request(_ path: String, method: String = "GET",
                         query: [URLQueryItem] = []) -> URLRequest {
        var request = URLRequest(url: origin.endpoint(path, query: query))
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        // A slow SSH tunnel can outlive the HTTP server's idle keep-alive.
        // Use a fresh loopback connection rather than replaying a lost POST.
        if origin.url.scheme == "http" { request.setValue("close", forHTTPHeaderField: "Connection") }
        if !token.isEmpty { request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        return request
    }

    private func check(_ response: URLResponse) throws {
        guard let http = response as? HTTPURLResponse else { throw ClientError.invalidResponse }
        guard (200..<300).contains(http.statusCode) else { throw ClientError.http(http.statusCode) }
    }

    private func decode<T: Decodable>(_ type: T.Type, from request: URLRequest) async throws -> T {
        let (data, response) = try await session.data(for: request)
        try check(response)
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(type, from: data)
    }

    public func connect() async throws {
        let (_, response) = try await session.data(for: request("/api/papers/", query: [
            URLQueryItem(name: "limit", value: "1")]))
        try check(response)
    }

    public func login(username: String, password: String) async throws -> LoginReceipt {
        var payload = request("/api/auth/login", method: "POST")
        payload.setValue("application/json", forHTTPHeaderField: "Content-Type")
        payload.httpBody = try JSONSerialization.data(withJSONObject: [
            "username": username, "password": password])
        let receipt = try await decode(LoginReceipt.self, from: payload)
        guard !receipt.token.isEmpty,
              !receipt.token.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) })
        else { throw ClientError.invalidResponse }
        return receipt
    }

    public func upload(_ file: URL) async throws -> PaperSnapshot {
        let data = try Self.validatedPDF(file)
        let boundary = "SuperTranslate-\(UUID().uuidString)"
        var payload = request("/api/papers/upload", method: "POST")
        payload.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        payload.httpBody = Self.multipart(data: data, filename: file.lastPathComponent, boundary: boundary)
        let paper = try await decode(PaperSnapshot.self, from: payload)
        try Self.validateID(paper.id)
        return paper
    }

    public func start(_ id: String, quality: String = "balanced", backend: String = "") async throws {
        try Self.validateID(id)
        guard ["fast", "balanced", "quality"].contains(quality) else { throw ClientError.invalidResponse }
        guard backend.isEmpty || TranslationProvider(rawValue: backend) != nil
        else { throw ClientError.invalidResponse }
        var query = [
            URLQueryItem(name: "quality", value: quality),
            URLQueryItem(name: "preserve_graphics_text", value: "true"),
            URLQueryItem(name: "qa_mode", value: "single"),
        ]
        if !backend.isEmpty && quality != "fast" { query.append(URLQueryItem(name: "backend", value: backend)) }
        let receipt = try await decode(TranslationReceipt.self, from: request(
            "/api/papers/\(id)/translate", method: "POST", query: query))
        guard receipt.ok, receipt.status == "translating" else { throw ClientError.invalidResponse }
    }

    public func providerModels() async throws -> [ProviderModelCatalog] {
        try await decode([ProviderModelCatalog].self, from: request("/api/provider-credentials/models"))
    }

    public func providerCredentials() async throws -> [ProviderCredentialStatus] {
        try await decode([ProviderCredentialStatus].self, from: request("/api/provider-credentials"))
    }

    public func refreshProviderModels(_ provider: String) async throws -> ProviderModelCatalog {
        guard TranslationProvider(rawValue: provider) != nil else { throw ClientError.invalidResponse }
        return try await decode(ProviderModelCatalog.self,
            from: request("/api/provider-credentials/models/\(provider)/refresh", method: "POST"))
    }

    public func saveProviderModel(_ provider: String, model: String) async throws -> ProviderCredentialStatus {
        guard TranslationProvider(rawValue: provider) != nil,
              model.range(of: #"\A[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}\z"#, options: .regularExpression) != nil
        else { throw ClientError.invalidResponse }
        var payload = request("/api/provider-credentials/\(provider)", method: "PUT")
        payload.setValue("application/json", forHTTPHeaderField: "Content-Type")
        payload.httpBody = try JSONSerialization.data(withJSONObject: ["model": model])
        return try await decode(ProviderCredentialStatus.self, from: payload)
    }

    public func paper(_ id: String) async throws -> PaperSnapshot {
        try Self.validateID(id)
        var target = id
        var visited: Set<String> = []
        // Historical duplicate URLs stay valid; present the current canonical
        // paper instead of a stale failed or completed copy from the task cache.
        for _ in 0..<5 {
            guard visited.insert(target).inserted else { throw ClientError.invalidResponse }
            let paper = try await decode(PaperSnapshot.self, from: request("/api/papers/\(target)"))
            guard paper.id == target else { throw ClientError.invalidResponse }
            guard let canonical = paper.canonicalPaperId, canonical != target else { return paper }
            try Self.validateID(canonical)
            target = canonical
        }
        throw ClientError.invalidResponse
    }

    public func qualityReport(_ id: String) async throws -> QualityReport {
        try Self.validateID(id)
        return try await decode(QualityReport.self, from: request("/api/papers/\(id)/qa-report"))
    }

    public func cancel(_ id: String) async throws {
        try Self.validateID(id)
        let (_, response) = try await session.data(for: request("/api/papers/\(id)/cancel", method: "POST"))
        try check(response)
    }

    public func listPapers(search: String = "", sort: String = "recent", offset: Int = 0) async throws -> PaperLibraryPage {
        guard search.count <= 200, ["recent", "oldest", "title"].contains(sort), offset >= 0
        else { throw ClientError.invalidResponse }
        let page = try await decode(PaperLibraryPage.self, from: request("/api/papers/", query: [
            URLQueryItem(name: "search", value: search), URLQueryItem(name: "sort", value: sort),
            URLQueryItem(name: "offset", value: String(offset)), URLQueryItem(name: "limit", value: "50")]))
        for paper in page.papers { try Self.validateID(paper.id) }
        return page
    }

    public func organizeLibrary() async throws -> OrganizationReceipt {
        try await decode(OrganizationReceipt.self, from: request("/api/papers/organize", method: "POST"))
    }

    public func libraryHistory(_ id: String) async throws -> PaperLibraryHistory {
        try Self.validateID(id)
        return try await decode(PaperLibraryHistory.self, from: request("/api/papers/\(id)/library-history"))
    }

    public func export(_ id: String, variant: PDFVariant, directory: URL,
                       title: String = "", createdAt: String = "") async throws -> URL {
        try Self.validateID(id)
        var payload = request("/api/papers/\(id)/download/\(variant.rawValue)")
        payload.setValue("application/pdf", forHTTPHeaderField: "Accept")
        let (temporary, response) = try await session.download(for: payload)
        defer { try? FileManager.default.removeItem(at: temporary) }
        try check(response)
        guard response.mimeType?.lowercased() == "application/pdf" else { throw ClientError.invalidResponse }
        let size = try temporary.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
        guard size > 0, size <= 300 * 1024 * 1024 else { throw ClientError.invalidResponse }
        let data = try Data(contentsOf: temporary, options: .mappedIfSafe)
        guard data.starts(with: Data("%PDF".utf8)) else { throw ClientError.invalidResponse }
        let target = DocumentExportName.target(directory: directory, title: title,
            createdAt: createdAt, id: id, variant: variant)
        try FileManager.default.createDirectory(at: target.deletingLastPathComponent(), withIntermediateDirectories: true,
                                               attributes: [.posixPermissions: 0o700])
        try data.write(to: target, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: target.path)
        return target
    }
}
