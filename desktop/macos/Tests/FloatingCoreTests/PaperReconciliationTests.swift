import Foundation
import XCTest
@testable import FloatingCore

private final class ReconciliationProtocol: URLProtocol {
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

final class PaperReconciliationTests: XCTestCase {
    private let oldID = "ab1b0a7b2f5b"
    private let canonicalID = "f0354b8795f3"

    private func snapshot(id: String, canonical: String? = nil) throws -> Data {
        var body: [String: Any] = ["id": id, "title": "Full scientific paper title",
            "translation_status": "completed", "translation_progress": 1,
            "translation_stage": "已完成", "translation_eta": "",
            "has_translated": true, "has_dual": true, "has_qa_report": true]
        if let canonical { body["canonical_paper_id"] = canonical }
        return try JSONSerialization.data(withJSONObject: body)
    }

    private func service() throws -> PDFService {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [ReconciliationProtocol.self]
        return try PDFService(origin: ServiceOrigin("http://127.0.0.1:18001"),
            token: "fixture-token", configuration: configuration)
    }

    func testLegacyDuplicateResolvesToCanonicalPaperBeforePresentingStatus() async throws {
        let old = try snapshot(id: oldID, canonical: canonicalID)
        let canonical = try snapshot(id: canonicalID)
        var paths: [String] = []
        ReconciliationProtocol.handler = { request in
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer fixture-token")
            paths.append(request.url!.path)
            return (200, request.url!.path.hasSuffix(self.oldID) ? old : canonical)
        }
        let resolved = try await service().paper(oldID)
        XCTAssertEqual(resolved.id, canonicalID)
        XCTAssertEqual(paths, ["/api/papers/\(oldID)", "/api/papers/\(canonicalID)"])
    }

    func testCanonicalCycleIsRejectedWithoutSubmittingOrLooping() async throws {
        let old = try snapshot(id: oldID, canonical: canonicalID)
        let canonical = try snapshot(id: canonicalID, canonical: oldID)
        var calls = 0
        ReconciliationProtocol.handler = { request in
            XCTAssertEqual(request.httpMethod, "GET")
            calls += 1
            return (200, request.url!.path.hasSuffix(self.oldID) ? old : canonical)
        }
        do { _ = try await service().paper(oldID); XCTFail("Canonical cycle accepted") }
        catch { XCTAssertEqual(error as? ClientError, .invalidResponse) }
        XCTAssertEqual(calls, 2)
    }

    func testInvalidCanonicalIDNeverReachesTransport() async throws {
        let old = try snapshot(id: oldID, canonical: "../another-account")
        var calls = 0
        ReconciliationProtocol.handler = { _ in calls += 1; return (200, old) }
        do { _ = try await service().paper(oldID); XCTFail("Invalid canonical ID accepted") }
        catch { XCTAssertEqual(error as? ClientError, .invalidResponse) }
        XCTAssertEqual(calls, 1)
    }

    func testCanonicalAccessFailureIsNotReplacedWithOldCompletedResult() async throws {
        let old = try snapshot(id: oldID, canonical: canonicalID)
        ReconciliationProtocol.handler = { request in
            request.url!.path.hasSuffix(self.oldID) ? (200, old) : (403, Data())
        }
        do { _ = try await service().paper(oldID); XCTFail("Denied canonical result accepted") }
        catch { XCTAssertEqual(error as? ClientError, .http(403)) }
    }

    func testDeepCanonicalChainIsBoundedToFourRedirects() async throws {
        let ids = (1...6).map { String(format: "%012x", $0) }
        var responses: [String: Data] = [:]
        for index in 0..<5 { responses[ids[index]] = try snapshot(id: ids[index], canonical: ids[index + 1]) }
        var paths: [String] = []
        ReconciliationProtocol.handler = { request in
            paths.append(request.url!.lastPathComponent)
            return (200, try XCTUnwrap(responses[request.url!.lastPathComponent]))
        }
        do { _ = try await service().paper(ids[0]); XCTFail("Unbounded canonical chain accepted") }
        catch { XCTAssertEqual(error as? ClientError, .invalidResponse) }
        XCTAssertEqual(paths, Array(ids.prefix(5)))
    }

    func testMismatchedResponseIdentityCannotReplaceRequestedPaper() async throws {
        let mismatched = try snapshot(id: canonicalID)
        ReconciliationProtocol.handler = { _ in (200, mismatched) }
        do { _ = try await service().paper(oldID); XCTFail("Unexpected response identity accepted") }
        catch { XCTAssertEqual(error as? ClientError, .invalidResponse) }
    }
}
