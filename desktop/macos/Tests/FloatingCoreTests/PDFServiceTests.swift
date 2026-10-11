import Foundation
import XCTest
@testable import FloatingCore

private final class ContractProtocol: URLProtocol {
    static var handler: ((URLRequest) throws -> (Int, Data))!
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        do {
            let (code, data) = try Self.handler(request)
            let response = HTTPURLResponse(url: request.url!, statusCode: code,
                httpVersion: "HTTP/1.1", headerFields: ["Content-Type": "application/json"])!
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch { client?.urlProtocol(self, didFailWithError: error) }
    }
    override func stopLoading() {}
}

final class PDFServiceTests: XCTestCase {
    private let id = "012345abcdef"
    private let pdf = Data("%PDF-1.7\nfixture\n%%EOF\n".utf8)
    private let snapshot = Data("""
    {"id":"012345abcdef","title":"Fixture","translation_status":"pending",
     "translation_progress":0,"translation_stage":"","translation_eta":"",
     "translation_error":null,"has_translated":false,"has_dual":false,"has_qa_report":false}
    """.utf8)

    func testSelectedProviderIsSubmittedAndFastDoesNotUseAIProvider() async throws {
        let client = try service()
        for provider in TranslationProvider.allCases {
            ContractProtocol.handler = { request in
                let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)!.queryItems!
                XCTAssertEqual(query.first { $0.name == "backend" }?.value, provider.rawValue)
                XCTAssertEqual(query.first { $0.name == "quality" }?.value, "balanced")
                XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer fixture-token")
                return (200, Data("{\"ok\":true,\"status\":\"translating\"}".utf8))
            }
            try await client.start(id, backend: provider.rawValue)
        }
        ContractProtocol.handler = { request in
            let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)!.queryItems!
            XCTAssertNil(query.first { $0.name == "backend" })
            XCTAssertEqual(query.first { $0.name == "quality" }?.value, "fast")
            return (200, Data("{\"ok\":true,\"status\":\"translating\"}".utf8))
        }
        try await client.start(id, quality: "fast", backend: "deepseek")
    }

    func testModelOnlySavePreservesCredentialsAndEndpointID() async throws {
        ContractProtocol.handler = { request in
            XCTAssertEqual(request.httpMethod, "PUT")
            XCTAssertEqual(request.url?.path, "/api/provider-credentials/doubao")
            let stream = try XCTUnwrap(request.httpBodyStream)
            stream.open()
            defer { stream.close() }
            var bytes = [UInt8](repeating: 0, count: 1024)
            let count = stream.read(&bytes, maxLength: bytes.count)
            XCTAssertGreaterThan(count, 0)
            let payload = try JSONSerialization.jsonObject(with: Data(bytes.prefix(max(0, count)))) as! [String: String]
            XCTAssertEqual(payload, ["model": "ep-20261010-fixture"])
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer fixture-token")
            return (200, Data("{\"provider\":\"doubao\",\"label\":\"豆包\",\"model\":\"ep-20261010-fixture\",\"configured\":true,\"source\":\"personal\"}".utf8))
        }
        let saved = try await service().saveProviderModel("doubao", model: "ep-20261010-fixture")
        XCTAssertEqual(saved.model, "ep-20261010-fixture")
        XCTAssertTrue(saved.configured)
    }

    func testProviderCatalogDecodingAndAuthenticatedRefresh() async throws {
        let entry = """
        {"provider":"deepseek","label":"DeepSeek","default_model":"deepseek-flash",
         "selected_model":"deepseek-v4-pro","models":["deepseek-flash","deepseek-v4-pro"],
         "model_options":[{"id":"deepseek-flash","description":"Flash","availability":"available"}],
         "source":"api","warning":""}
        """
        let client = try service()
        ContractProtocol.handler = { request in
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer fixture-token")
            if request.httpMethod == "POST" {
                XCTAssertEqual(request.url?.path, "/api/provider-credentials/models/deepseek/refresh")
                return (200, Data(entry.utf8))
            }
            XCTAssertEqual(request.url?.path, "/api/provider-credentials/models")
            return (200, Data("[\(entry)]".utf8))
        }
        let catalog = try await client.providerModels()
        XCTAssertEqual(catalog[0].selectedModel, "deepseek-v4-pro")
        XCTAssertTrue(catalog[0].models.contains("deepseek-flash"))
        let refreshed = try await client.refreshProviderModels("deepseek")
        XCTAssertEqual(refreshed.modelOptions[0].availability, "available")
    }

    func testProviderStatusUsesCanonicalPathWithoutRedirect() async throws {
        ContractProtocol.handler = { request in
            XCTAssertEqual(request.url?.path, "/api/provider-credentials")
            XCTAssertEqual(request.httpMethod, "GET")
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer fixture-token")
            return (200, Data("[{\"provider\":\"deepseek\",\"label\":\"DeepSeek\",\"model\":\"deepseek-flash\",\"configured\":true,\"source\":\"personal\"}]".utf8))
        }
        let statuses = try await service().providerCredentials()
        XCTAssertEqual(statuses[0].model, "deepseek-flash")
    }

    func testInvalidProviderAndModelRejectedBeforeNetwork() async throws {
        ContractProtocol.handler = { _ in
            XCTFail("Invalid model settings reached the network")
            return (500, Data())
        }
        let client = try service()
        do { try await client.start(id, backend: "other/server"); XCTFail("Unknown provider accepted") }
        catch { XCTAssertEqual(error as? ClientError, .invalidResponse) }
        for model in ["", "model\nsecret", "model\n", "model\r\n", " model ", String(repeating: "a", count: 101)] {
            do { _ = try await client.saveProviderModel("deepseek", model: model); XCTFail("Invalid model accepted") }
            catch { XCTAssertEqual(error as? ClientError, .invalidResponse) }
        }
    }

    private func service(token: String = "fixture-token") throws -> PDFService {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [ContractProtocol.self]
        return try PDFService(origin: ServiceOrigin("http://127.0.0.1:18001"),
                              token: token, configuration: configuration)
    }

    private func withFile(_ data: Data, action: (URL) throws -> Void) throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let file = directory.appendingPathComponent("fixture.pdf")
        try data.write(to: file)
        try action(file)
    }

    func testOriginNormalizationAndUnsafeOrigins() throws {
        XCTAssertEqual(try ServiceOrigin(" HTTPS://EXAMPLE.com:443/ ").url.absoluteString, "https://example.com")
        for value in ["http://example.com", "https://user:pass@example.com", "https://example.com/api",
                      "https://example.com?token=secret", "https://example.com#fragment", "file:///tmp", "http://127.0.0.1:0"] {
            XCTAssertThrowsError(try ServiceOrigin(value), value)
        }
        XCTAssertNoThrow(try ServiceOrigin("http://[::1]:18001"))
        XCTAssertNoThrow(try ServiceOrigin("http://localhost:8000"))
    }

    func testAccountCacheIsolation() throws {
        let first = try ServiceOrigin("https://example.com")
        let equivalent = try ServiceOrigin("https://EXAMPLE.com:443/")
        let other = try ServiceOrigin("https://other.example.com")
        XCTAssertEqual(first.accountNamespace(token: "a"), equivalent.accountNamespace(token: "a"))
        XCTAssertNotEqual(first.accountNamespace(token: "a"), first.accountNamespace(token: "b"))
        XCTAssertNotEqual(first.accountNamespace(token: "a"), other.accountNamespace(token: "a"))
        XCTAssertFalse(first.accountNamespace(token: "secret-token").contains("secret-token"))
    }

    func testPDFValidationRejectsSpoofedInputAndSymlinks() throws {
        try withFile(pdf) { file in
            XCTAssertEqual(try PDFService.validatedPDF(file), pdf)
            let link = file.deletingLastPathComponent().appendingPathComponent("link.pdf")
            try FileManager.default.createSymbolicLink(at: link, withDestinationURL: file)
            XCTAssertThrowsError(try PDFService.validatedPDF(link))
        }
        for invalid in [Data(), Data("not a PDF %%EOF".utf8), Data("%PDF missing end".utf8)] {
            try withFile(invalid) { XCTAssertThrowsError(try PDFService.validatedPDF($0)) }
        }
        XCTAssertThrowsError(try PDFService.validatedPDF(URL(string: "https://example.com/paper.pdf")!))
    }

    func testOversizedPDFRejectedBeforeReading() throws {
        try withFile(pdf) { file in
            let handle = try FileHandle(forWritingTo: file)
            defer { try? handle.close() }
            try handle.truncate(atOffset: 100 * 1024 * 1024 + 1)
            XCTAssertThrowsError(try PDFService.validatedPDF(file)) { XCTAssertEqual($0 as? ClientError, .oversizedPDF) }
        }
    }

    func testMultipartPreservesPDFAndRejectsHeaderInjection() {
        let body = PDFService.multipart(data: pdf, filename: "论文\"\r\nX-Evil: yes.pdf", boundary: "fixture")
        let text = String(decoding: body, as: UTF8.self)
        XCTAssertTrue(text.contains("name=\"file\"; filename=\"论文___X-Evil: yes.pdf\""))
        XCTAssertFalse(text.contains("\r\nX-Evil:"))
        XCTAssertTrue(body.range(of: pdf) != nil)
        XCTAssertTrue(text.hasSuffix("\r\n--fixture--\r\n"))
    }

    func testConnectionSendsTokenOnlyInHeader() async throws {
        ContractProtocol.handler = { request in
            XCTAssertEqual(URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.path, "/api/papers/")
            XCTAssertEqual(request.url?.query, "limit=1")
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer fixture-token")
            XCTAssertFalse(request.url!.absoluteString.contains("fixture-token"))
            return (200, Data("[]".utf8))
        }
        try await service().connect()
    }

    func testLoginPayloadAndReceipt() async throws {
        ContractProtocol.handler = { request in
            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertEqual(request.url?.path, "/api/auth/login")
            let stream = request.httpBodyStream!
            stream.open()
            defer { stream.close() }
            var bytes = [UInt8](repeating: 0, count: 4096)
            let count = stream.read(&bytes, maxLength: bytes.count)
            let json = try JSONSerialization.jsonObject(with: Data(bytes.prefix(count))) as! [String: String]
            XCTAssertEqual(json, ["username": "fixture", "password": "test-password"])
            return (200, Data("{\"token\":\"login-token\",\"username\":\"fixture\"}".utf8))
        }
        let receipt = try await service(token: "").login(username: "fixture", password: "test-password")
        XCTAssertEqual(receipt.token, "login-token")
    }

    func testStatusDecodesServerContract() async throws {
        let expected = snapshot
        ContractProtocol.handler = { request in
            XCTAssertEqual(request.url?.path, "/api/papers/012345abcdef")
            return (200, expected)
        }
        let paper = try await service().paper(id)
        XCTAssertEqual(paper.translationStatus, "pending")
        XCTAssertEqual(paper.translationProgress, 0)
        XCTAssertFalse(paper.hasQaReport)
    }

    func testTranslationUsesQueryParametersAndServerDefaultBackend() async throws {
        ContractProtocol.handler = { request in
            XCTAssertEqual(request.httpMethod, "POST")
            let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)!.queryItems!
            XCTAssertEqual(Set(query.map(\.name)), Set(["quality", "preserve_graphics_text", "qa_mode"]))
            XCTAssertEqual(query.first(where: { $0.name == "quality" })?.value, "balanced")
            XCTAssertEqual(query.first(where: { $0.name == "qa_mode" })?.value, "single")
            return (200, Data("{\"ok\":true,\"status\":\"translating\",\"job_id\":\"fixture\"}".utf8))
        }
        try await service().start(id)
    }

    func testInvalidPaperIDsAndTokensNeverReachTransport() async throws {
        ContractProtocol.handler = { _ in XCTFail("Invalid input must not send a request"); return (200, Data()) }
        for value in ["../secret", "012345ABCDEF", "", "012345abcdef/other"] {
            do { _ = try await service().paper(value); XCTFail("Accepted \(value)") }
            catch { XCTAssertEqual(error as? ClientError, .invalidResponse) }
        }
        XCTAssertThrowsError(try service(token: "header\r\ninjection"))
    }

    func testUnauthorizedAndOfflineErrorsPropagate() async throws {
        ContractProtocol.handler = { _ in (401, Data("{\"detail\":\"private diagnostics\"}".utf8)) }
        do { try await service().connect(); XCTFail("Expected unauthorized") }
        catch { XCTAssertEqual(error as? ClientError, .http(401)); XCTAssertFalse(error.localizedDescription.contains("private diagnostics")) }
        ContractProtocol.handler = { _ in throw URLError(.cannotConnectToHost) }
        do { try await service().connect(); XCTFail("Expected offline") }
        catch { XCTAssertEqual((error as? URLError)?.code, .cannotConnectToHost) }
    }

    func testPaperIDsRejectTrailingLineEndings() {
        for value in ["012345abcdef\n", "012345abcdef\r\n", "012345abcdef\u{2028}"] {
            XCTAssertThrowsError(try PDFService.validateID(value))
        }
    }
}
