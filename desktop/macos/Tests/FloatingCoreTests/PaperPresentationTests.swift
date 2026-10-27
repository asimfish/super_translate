import Foundation
import XCTest
@testable import FloatingCore

final class PaperPresentationTests: XCTestCase {
    private func paper(status: String, translated: Bool = true, id: String = "012345abcde1",
                       version: Int? = nil, versionCount: Int? = nil) throws -> PaperSnapshot {
        var value: [String: Any] = ["id": id, "title": "Same paper title", "translation_status": status,
            "translation_progress": 0.9, "translation_stage": "等待系统修复", "translation_eta": "",
            "translation_error": "第 17 页 table_structure_mismatch；译文已保留", "has_translated": translated,
            "has_dual": translated, "has_qa_report": true, "created_at": "2026-10-11T03:12:00+00:00"]
        if let version { value["version_number"] = version }
        if let versionCount { value["version_count"] = versionCount }
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(PaperSnapshot.self, from: JSONSerialization.data(withJSONObject: value))
    }

    func testParkedRepairShowsCauseAndDraftWithoutCompletedClaim() throws {
        for status in ["repairing", "repair_pending"] {
            let presentation = try paper(status: status).taskPresentation()
            XCTAssertEqual(presentation.state, .repairing)
            XCTAssertTrue(presentation.message.contains("第 17 页"))
            XCTAssertTrue(presentation.message.contains("草稿"))
            XCTAssertTrue(presentation.message.contains("修复已暂停"))
            XCTAssertTrue(presentation.message.contains("引擎更新后自动重试"))
            XCTAssertFalse(presentation.message.contains("等待系统修复"))
            XCTAssertFalse(presentation.message.contains("已生成"))
        }
    }

    func testCompletedStatusWithoutTranslationIsNotPresentedAsCompleted() throws {
        XCTAssertEqual(try paper(status: "completed", translated: false).taskPresentation().state, .failed)
        XCTAssertEqual(try paper(status: "completed").taskPresentation().state, .completed)
    }

    func testSameTitleDifferentContentVersionsHaveStableDistinctLabels() throws {
        let first = try paper(status: "completed", version: 1, versionCount: 2)
        let second = try paper(status: "completed", id: "012345abcde2", version: 2, versionCount: 2)
        XCTAssertNotEqual(first.displayTitle, second.displayTitle)
        XCTAssertTrue(first.displayTitle.contains("版本 1/2"))
        XCTAssertTrue(second.displayTitle.contains("版本 2/2"))
        XCTAssertTrue(first.displayTitle.contains("012345abcde1"))
        XCTAssertEqual(first.title, second.title, "Display labels must preserve the actual stored paper title")
    }

    func testOldSinglePaperTitleAndCancellationRemainCompatible() throws {
        let legacy = try paper(status: "pending")
        XCTAssertEqual(legacy.displayTitle, legacy.title)
        XCTAssertNil(legacy.canonicalPaperId)
        XCTAssertEqual(legacy.taskPresentation(cancellationRequested: true).state, .cancelled)
    }
}
