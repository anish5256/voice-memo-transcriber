import Foundation

struct EventPerson: Codable, Equatable, Sendable {
    var name: String
    var email: String
    init(name: String?, email: String) {
        let name = name?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        self.name = name.isEmpty ? String(email.split(separator: "@").first ?? "") : name
        self.email = email
    }
}

// Only these fields cross the app/Python boundary. Dates are ISO 8601 with the local offset.
struct MeetingEventContext: Codable, Equatable, Sendable {
    var title: String
    var start: String
    var end: String
    var attendees: [EventPerson]
    var organizer: EventPerson?
    var location: String
    var url: String
    var calendar: String

    func encoded() throws -> Data {
        // Synthesized Codable omits nil optionals; the contract requires an explicit null organizer.
        let encoder = JSONEncoder()
        return try encoder.encode(self)
    }
    enum CodingKeys: String, CodingKey { case title, start, end, attendees, organizer, location, url, calendar }
    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(title, forKey: .title); try c.encode(start, forKey: .start); try c.encode(end, forKey: .end)
        try c.encode(attendees, forKey: .attendees); try c.encode(organizer, forKey: .organizer)
        try c.encode(location, forKey: .location); try c.encode(url, forKey: .url); try c.encode(calendar, forKey: .calendar)
    }
    static func timestamp(_ date: Date) -> String {
        let formatter = ISO8601DateFormatter(); formatter.timeZone = .current
        return formatter.string(from: date)
    }
}

struct CalendarParticipant: Equatable, Sendable {
    enum Status: Equatable, Sendable { case unknown, pending, accepted, declined, tentative, delegated, completed, inProcess }
    var person: EventPerson
    var participantStatus: Status
    var isCurrentUser: Bool
}

struct CalendarMeeting: Identifiable, Equatable, Sendable {
    var id: String
    var start: Date
    var end: Date
    var isAllDay = false
    var isCancelled = false
    var participants: [CalendarParticipant]
    var context: MeetingEventContext
    var notificationIdentifier: String { MeetingNotificationIdentifier.make(eventIdentifier: id, start: start) }
    var timeRange: String {
        start.formatted(date: .omitted, time: .shortened) + " – " + end.formatted(date: .omitted, time: .shortened)
    }
}

enum UpcomingMeetingPicker {
    static func eligible(_ events: [CalendarMeeting], now: Date) -> [CalendarMeeting] {
        events.filter {
            !$0.isAllDay && !$0.isCancelled && $0.start >= now.addingTimeInterval(-2 * 3600) &&
            $0.start <= now.addingTimeInterval(12 * 3600) && $0.end > now &&
            !$0.participants.contains { $0.isCurrentUser && $0.participantStatus == .declined } &&
            $0.participants.contains { !$0.isCurrentUser }
        }.sorted { $0.start == $1.start ? $0.id < $1.id : $0.start < $1.start }
    }
    static func pick(_ events: [CalendarMeeting], now: Date) -> CalendarMeeting? {
        let events = eligible(events, now: now)
        return events.first(where: { $0.start <= now }) ?? events.first
    }
}

enum MeetingNotificationIdentifier {
    static let prefix = "MeetingNotes.calendar."
    static func make(eventIdentifier: String, start: Date) -> String {
        // Deterministic across process launches; unlike Swift's randomized Hasher.
        prefix + Data(eventIdentifier.utf8).base64EncodedString() + "." + String(start.timeIntervalSince1970)
    }
}
