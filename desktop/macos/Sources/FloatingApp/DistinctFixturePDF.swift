import AppKit
import Foundation
import PDFKit

// Only contract probes call this; each failure scenario needs distinct content.
enum DistinctFixturePDF {
    static func write(source: URL, to destination: URL) throws {
        guard let document = PDFDocument(url: source), let page = document.page(at: 0)
        else { throw ClientErrorForFixture.invalidPDF }
        let marker = PDFAnnotation(bounds: CGRect(x: 24, y: 24, width: 420, height: 24),
            forType: .freeText, withProperties: nil)
        marker.contents = "Contract scenario: " + destination.lastPathComponent
        marker.font = NSFont.systemFont(ofSize: 9)
        marker.fontColor = .black
        marker.color = .clear
        page.addAnnotation(marker)
        guard document.write(to: destination) else { throw ClientErrorForFixture.invalidPDF }
    }
    private enum ClientErrorForFixture: Error { case invalidPDF }
}
