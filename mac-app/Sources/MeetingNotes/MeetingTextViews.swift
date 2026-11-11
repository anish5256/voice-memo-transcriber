import SwiftUI

private func inlineText(_ text: String) -> Text {
    let options = AttributedString.MarkdownParsingOptions(interpretedSyntax: .inlineOnlyPreservingWhitespace)
    return Text((try? AttributedString(markdown: text, options: options)) ?? AttributedString(text))
}

struct SummaryTextView: View {
    let source: String
    private var blocks: [String] {
        // Join wrapped paragraph lines, retaining the tiny renderer's structural rows.
        var result: [String] = []
        var paragraph: [String] = []
        func flush() {
            if !paragraph.isEmpty { result.append(paragraph.joined(separator: " ")); paragraph = [] }
        }
        for line in source.components(separatedBy: .newlines) {
            if line.hasPrefix("# ") { flush(); continue }
            if line.trimmingCharacters(in: .whitespaces).isEmpty { flush() }
            else if line.hasPrefix("## ") || line.hasPrefix("- ") { flush(); result.append(line) }
            else { paragraph.append(line) }
        }
        flush()
        return result
    }
    var body: some View {
        if source.isEmpty { Text("No summary available.").foregroundStyle(.secondary) }
        LazyVStack(alignment: .leading, spacing: 12) {
            ForEach(Array(blocks.enumerated()), id: \.offset) { _, line in
                if line.hasPrefix("## ") {
                    inlineText(String(line.dropFirst(3))).font(.title3).fontWeight(.semibold).padding(.top, 8)
                } else if line.hasPrefix("- [ ] ") {
                    HStack(alignment: .top, spacing: 8) {
                        Image(systemName: "square").foregroundStyle(.secondary).padding(.top, 2)
                            .accessibilityLabel("Unchecked action item")
                        inlineText(String(line.dropFirst(6)))
                    }
                } else if line.hasPrefix("- ") {
                    HStack(alignment: .top, spacing: 8) {
                        Text("•").foregroundStyle(.secondary)
                        inlineText(String(line.dropFirst(2)))
                    }
                } else { inlineText(line) }
            }
        }.lineSpacing(4)
    }
}

struct TranscriptTextView: View {
    let source: String
    var body: some View {
        if source.isEmpty { Text("No transcript available.").foregroundStyle(.secondary) }
        LazyVStack(alignment: .leading, spacing: 16) {
            ForEach(Array(source.components(separatedBy: .newlines).enumerated()), id: \.offset) { _, line in
                if !line.isEmpty {
                    if let row = TranscriptLine(line) {
                        HStack(alignment: .top, spacing: 14) {
                            Text(row.timestamp).font(.caption).monospacedDigit()
                                .foregroundStyle(.secondary).frame(width: 52, alignment: .trailing)
                            VStack(alignment: .leading, spacing: 4) {
                                Text(row.speaker).font(.caption).foregroundStyle(.secondary)
                                Text(row.text).frame(maxWidth: .infinity, alignment: .leading)
                            }
                        }
                    } else { Text(line) }
                }
            }
        }.lineSpacing(4)
    }
}

private struct TranscriptLine {
    let timestamp: String
    let speaker: String
    let text: String
    init?(_ line: String) {
        guard line.hasPrefix("["), let end = line.firstIndex(of: "]") else { return nil }
        let rest = line[line.index(after: end)...].trimmingCharacters(in: .whitespaces)
        guard rest.hasPrefix("Speaker "), let colon = rest.firstIndex(of: ":") else { return nil }
        timestamp = String(line[line.index(after: line.startIndex)..<end])
        speaker = String(rest[..<colon])
        text = rest[rest.index(after: colon)...].trimmingCharacters(in: .whitespaces)
    }
}
