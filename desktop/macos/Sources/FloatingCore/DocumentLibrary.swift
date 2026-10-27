import Foundation

public enum PaperTaskState: String, Sendable {
    case ready, translating, repairing, completed, failed, cancelled
}

public struct PaperTaskPresentation: Sendable {
    public let state: PaperTaskState
    public let message: String
}

public enum DocumentTitle {
    public static func display(title: String, id: String, createdAt: String?,
                               versionNumber: Int?, versionCount: Int?) -> String {
        guard (versionCount ?? 1) > 1 else { return title }
        let date = LibraryDate.label(createdAt ?? "", withTime: true)
        return "版本 \(versionNumber ?? 1)/\(versionCount ?? 1) · \(title) · \(date) · \(id)"
    }
}

public extension PaperSnapshot {
    var displayTitle: String {
        DocumentTitle.display(title: title, id: id, createdAt: createdAt,
            versionNumber: versionNumber, versionCount: versionCount)
    }

    func taskPresentation(cancellationRequested: Bool = false) -> PaperTaskPresentation {
        let reason = translationError?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        switch translationStatus {
        case "completed" where hasTranslated:
            return PaperTaskPresentation(state: .completed, message: "译文已生成 · 点击查看")
        case "repairing", "repair_pending":
            var message = "自动修复已暂停"
            message += "：" + (reason.isEmpty ? "译文尚未通过质检" : reason)
            if hasTranslated { message += " · 可查看保留草稿" }
            message += " · 引擎更新后自动重试"
            return PaperTaskPresentation(state: .repairing, message: message)
        case "failed":
            return PaperTaskPresentation(state: cancellationRequested ? .cancelled : .failed,
                message: cancellationRequested ? "已取消" :
                    (reason.isEmpty ? "翻译失败 · 查看详情或质检报告" : "翻译失败：\(reason)"))
        case "pending":
            return PaperTaskPresentation(state: cancellationRequested ? .cancelled : .ready,
                message: cancellationRequested ? "已取消" : "PDF 已上传 · 点击开始翻译")
        case "translating":
            var message = cancellationRequested ? "正在取消…" :
                [translationStage, translationEta].filter { !$0.isEmpty }.joined(separator: " · ")
            if message.isEmpty { message = "翻译进行中…" }
            if hasTranslated && ["等待恢复", "等待系统修复"].contains(translationStage) {
                message += " · 可查看保留草稿和质检"
            }
            return PaperTaskPresentation(state: .translating, message: message)
        default:
            return PaperTaskPresentation(state: .failed, message: "结果尚不可用，请在系统网页检查")
        }
    }
}

public struct PaperLibraryPage: Decodable, Sendable {
    public let papers: [PaperSnapshot]
    public let total: Int
    public let offset: Int
    public let limit: Int
}

public struct OrganizationReceipt: Decodable, Sendable {
    public let indexed: Int
    public let duplicates: Int
    public let renamed: Int
}

public struct PaperLibraryHistory: Decodable, Sendable {
    public struct Import: Decodable, Sendable {
        public let filename: String
        public let title: String
        public let importedAt: String
    }
    public struct Version: Decodable, Sendable {
        public let id: String
        public let title: String
        public let createdAt: String
        public let translationStatus: String
    }
    public let imports: [Import]
    public let versions: [Version]
}

public enum LibraryDate {
    public static func parse(_ value: String) -> Date? {
        let formatter = ISO8601DateFormatter()
        let normalized = value.range(of: #"(Z|[+-]\d\d:\d\d)$"#, options: .regularExpression) == nil
            ? value + "Z" : value
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: normalized) { return date }
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.date(from: normalized)
    }

    public static func label(_ value: String, withTime: Bool = false) -> String {
        guard let date = parse(value) else { return "未注明时间" }
        let formatter = DateFormatter()
        formatter.dateFormat = withTime ? "yyyy-MM-dd HH:mm" : "yyyy-MM-dd"
        return formatter.string(from: date)
    }
}

public enum DocumentExportName {
    @discardableResult
    public static func organizeExisting(directory: URL, papers: [PaperSnapshot]) throws -> Int {
        let manager = FileManager.default
        let root = directory.resolvingSymlinksInPath().standardizedFileURL
        guard manager.fileExists(atPath: root.path) else { return 0 }
        var moved = 0
        for paper in papers {
            guard !paper.title.isEmpty,
                  paper.id.range(of: #"^[0-9a-f]{12}$"#, options: .regularExpression) != nil else { continue }
            for variant in PDFVariant.allCases {
                let old = root.appendingPathComponent("\(paper.id)-\(variant.rawValue).pdf")
                guard manager.fileExists(atPath: old.path) else { continue }
                let attributes = try old.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey])
                guard attributes.isRegularFile == true, attributes.isSymbolicLink != true else { continue }
                let destination = target(directory: root, title: paper.title,
                    createdAt: paper.createdAt ?? "", id: paper.id, variant: variant)
                guard destination.resolvingSymlinksInPath().path.hasPrefix(root.path + "/"),
                      !manager.fileExists(atPath: destination.path) else { continue }
                let folder = destination.deletingLastPathComponent()
                try manager.createDirectory(at: folder, withIntermediateDirectories: true,
                    attributes: [.posixPermissions: 0o700])
                guard folder.resolvingSymlinksInPath().path.hasPrefix(root.path + "/") else { continue }
                try manager.setAttributes([.posixPermissions: 0o600], ofItemAtPath: old.path)
                try manager.moveItem(at: old, to: destination)
                moved += 1
            }
        }
        return moved
    }

    public static func safeTitle(_ value: String) -> String {
        let forbidden = CharacterSet(charactersIn: "<>:\"/\\|?*").union(.controlCharacters)
        var result = value.components(separatedBy: forbidden).joined(separator: " ")
            .split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
            .trimmingCharacters(in: CharacterSet(charactersIn: " ."))
        while result.utf8.count > 140 { result.removeLast() }
        return result.isEmpty ? "论文" : result
    }

    public static func target(directory: URL, title: String, createdAt: String,
                              id: String, variant: PDFVariant) -> URL {
        // Empty title keeps compatibility for existing client contract probes.
        guard !title.isEmpty else { return directory.appendingPathComponent("\(id)-\(variant.rawValue).pdf") }
        let date = LibraryDate.parse(createdAt)
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM"
        let month = date.map { formatter.string(from: $0) } ?? "未注明时间"
        formatter.dateFormat = "yyyyMMdd-HHmmss"
        let stamp = date.map { formatter.string(from: $0) } ?? "未注明时间"
        let stem = safeTitle(title)
        let label = variant == .dual ? "双语" : "中文"
        return directory.appendingPathComponent(month, isDirectory: true)
            .appendingPathComponent("\(stem)--\(id)", isDirectory: true)
            .appendingPathComponent("\(stem)--\(stamp)--\(id)--\(label).pdf")
    }
}
