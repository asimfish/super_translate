import Foundation
import XCTest
@testable import FloatingCore

final class DocumentLibraryTests: XCTestCase {
    func testExistingCacheMovesWithoutOverwritingExportsOrFollowingSymlinks() throws {
        let manager = FileManager.default
        let root = manager.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try manager.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? manager.removeItem(at: root) }
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        let paper = try decoder.decode(PaperSnapshot.self, from: Data("""
        {"id":"012345abcdef","title":"Chemical Systems","created_at":"2026-10-11T03:12:00Z",
         "translation_status":"completed","translation_progress":1,"translation_stage":"已完成",
         "translation_eta":"","translation_error":null,"has_translated":true,"has_dual":true,
         "has_qa_report":false}
        """.utf8))
        let old = root.appendingPathComponent("012345abcdef-translated.pdf")
        let bytes = Data("%PDF-1.7 cached translation".utf8)
        try bytes.write(to: old)
        let target = DocumentExportName.target(directory: root, title: paper.title,
            createdAt: paper.createdAt!, id: paper.id, variant: .translated)
        XCTAssertEqual(try DocumentExportName.organizeExisting(directory: root, papers: [paper]), 1)
        XCTAssertEqual(try Data(contentsOf: target), bytes)
        XCTAssertFalse(manager.fileExists(atPath: old.path))
        XCTAssertEqual(try manager.attributesOfItem(atPath: target.path)[.posixPermissions] as? Int, 0o600)
        try Data("newer cached content".utf8).write(to: old)
        XCTAssertEqual(try DocumentExportName.organizeExisting(directory: root, papers: [paper]), 0)
        XCTAssertEqual(try Data(contentsOf: target), bytes)
        XCTAssertTrue(manager.fileExists(atPath: old.path))
        let linked = root.appendingPathComponent("012345abcdef-dual.pdf")
        try manager.createSymbolicLink(at: linked, withDestinationURL: target)
        XCTAssertEqual(try DocumentExportName.organizeExisting(directory: root, papers: [paper]), 0)
        XCTAssertTrue(try linked.resourceValues(forKeys: [.isSymbolicLinkKey]).isSymbolicLink == true)
    }

    func testOldSnapshotsStillDecodeAndNewLibraryMetadataIsAvailable() throws {
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        let old = Data("""
        {"id":"012345abcdef","title":"Old paper","translation_status":"completed",
         "translation_progress":1,"translation_stage":"已完成","translation_eta":"",
         "translation_error":null,"has_translated":true,"has_dual":true,"has_qa_report":false}
        """.utf8)
        let paper = try decoder.decode(PaperSnapshot.self, from: old)
        XCTAssertNil(paper.lastImportedAt)
        XCTAssertNil(paper.versionCount)
        var value = try JSONSerialization.jsonObject(with: old) as! [String: Any]
        value["version_count"] = 3; value["version_number"] = 2
        value["import_count"] = 4; value["duplicate"] = true
        let updated = try decoder.decode(PaperSnapshot.self, from: JSONSerialization.data(withJSONObject: value))
        XCTAssertEqual(updated.versionCount, 3)
        XCTAssertEqual(updated.versionNumber, 2)
        XCTAssertEqual(updated.importCount, 4)
        XCTAssertEqual(updated.duplicate, true)
    }

    func testUnicodeTitleExportStaysUnderRootAndDifferentIDsCannotCollide() {
        let root = URL(fileURLWithPath: "/private/tmp/document-exports", isDirectory: true)
        let title = "../../化学: Biological / Systems\n" + String(repeating: "分子", count: 100)
        let a = DocumentExportName.target(directory: root, title: title,
            createdAt: "2026-10-11T03:12:00", id: "012345abcdef", variant: .translated)
        let b = DocumentExportName.target(directory: root, title: title,
            createdAt: "2026-10-11T03:12:00", id: "012345abcdee", variant: .translated)
        XCTAssertTrue(a.standardizedFileURL.path.hasPrefix(root.path + "/2026-10/"))
        XCTAssertTrue(a.lastPathComponent.contains("化学"))
        XCTAssertTrue(a.lastPathComponent.contains("012345abcdef"))
        XCTAssertFalse(a.lastPathComponent.contains(":"))
        XCTAssertLessThan(a.lastPathComponent.utf8.count, 240)
        XCTAssertNotEqual(a, b)
        XCTAssertEqual(a, DocumentExportName.target(directory: root, title: title,
            createdAt: "2026-10-11T03:12:00", id: "012345abcdef", variant: .translated))
    }

    func testLegacyNaiveAndExplicitUTCTimesHaveSameInstant() {
        XCTAssertEqual(LibraryDate.parse("2026-10-11T03:12:00.123"),
                       LibraryDate.parse("2026-10-11T03:12:00.123+00:00"))
        XCTAssertNil(LibraryDate.parse("invalid"))
        XCTAssertEqual(LibraryDate.label(""), "未注明时间")
    }
}
