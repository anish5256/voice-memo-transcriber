import Foundation
import Combine

struct Meeting: Identifiable, Sendable {
    let folder: URL
    let title: String
    let recordedAt: Date
    let duration: Double
    let speakers: Int
    var event: MeetingEventContext? = nil
    var id: URL { folder }
    var subtitle: String {
        recordedAt.formatted(.dateTime.day().month(.abbreviated).year()) + " · " +
        recordedAt.formatted(date: .omitted, time: .shortened) + " · " + Self.clock(duration)
    }

    static func clock(_ seconds: Double) -> String {
        let value = Int(max(0, seconds.isFinite ? seconds : 0))
        return String(format: "%d:%02d", value / 60, value % 60)
    }
}

@MainActor
final class MeetingLibrary: ObservableObject {
    @Published private(set) var meetings: [Meeting] = []
    @Published var error: String?
    private var generation = 0

    func refresh(path: String, including folders: [URL] = []) async {
        generation += 1
        let request = generation
        let root = Paths.url(path)
        let result = await Task.detached { Self.scan(root: root, including: folders) }.value
        guard request == generation else { return }
        meetings = result
    }

    nonisolated private static func scan(root: URL, including folders: [URL]) -> [Meeting] {
        let manager = FileManager.default
        func children(_ url: URL) -> [URL] {
            (try? manager.contentsOfDirectory(at: url, includingPropertiesForKeys: nil,
                                             options: [.skipsHiddenFiles])) ?? []
        }
        func numeric(_ name: String, length: Int) -> Bool {
            name.count == length && name.allSatisfy(\.isNumber)
        }
        var candidates = folders
        for year in children(root) where numeric(year.lastPathComponent, length: 4) {
            for month in children(year) where numeric(month.lastPathComponent, length: 2) {
                candidates += children(month)
            }
        }
        return Set(candidates.map(\.standardizedFileURL)).compactMap { folder -> Meeting? in
            guard let data = try? Data(contentsOf: folder.appendingPathComponent("meta.json")),
                  let meta = try? JSONDecoder().decode(Metadata.self, from: data),
                  let date = parseDate(meta.recorded_at) else { return nil }
            return Meeting(folder: folder, title: meta.title, recordedAt: date,
                           duration: meta.duration_s ?? 0, speakers: meta.speakers ?? 0, event: meta.event)
        }.sorted { $0.recordedAt == $1.recordedAt ? $0.id.path > $1.id.path : $0.recordedAt > $1.recordedAt }
    }

    private struct Metadata: Decodable {
        let title: String
        let recorded_at: String
        let duration_s: Double?
        let speakers: Int?
        let event: MeetingEventContext?
        enum CodingKeys: String, CodingKey { case title, recorded_at, duration_s, speakers, event }
        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            title = try c.decode(String.self, forKey: .title)
            recorded_at = try c.decode(String.self, forKey: .recorded_at)
            duration_s = try c.decodeIfPresent(Double.self, forKey: .duration_s)
            speakers = try c.decodeIfPresent(Int.self, forKey: .speakers)
            event = try? c.decode(MeetingEventContext.self, forKey: .event)
        }
    }

    nonisolated private static func parseDate(_ value: String) -> Date? {
        let iso = ISO8601DateFormatter()
        if let date = iso.date(from: value) { return date }
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = iso.date(from: value) { return date }
        let local = DateFormatter()
        local.locale = Locale(identifier: "en_US_POSIX")
        for format in ["yyyy-MM-dd'T'HH:mm:ss.SSSSSS", "yyyy-MM-dd'T'HH:mm:ss", "yyyy-MM-dd HH:mm:ss"] {
            local.dateFormat = format
            if let date = local.date(from: value) { return date }
        }
        return nil
    }
}
