import XCTest
@testable import MeetingNotes

final class CalendarMeetingTests: XCTestCase {
    private let now = Date(timeIntervalSince1970: 1_800_000_000)
    private func meeting(_ id: String = "event", offset: Double = 60, duration: Double = 3600) -> CalendarMeeting {
        let start = now.addingTimeInterval(offset)
        return CalendarMeeting(id: id, start: start, end: start.addingTimeInterval(duration), participants: [
            CalendarParticipant(person: EventPerson(name: "Guest", email: "guest@example.com"), participantStatus: .accepted, isCurrentUser: false)
        ], context: MeetingEventContext(title: "Planning", start: MeetingEventContext.timestamp(start),
            end: MeetingEventContext.timestamp(start.addingTimeInterval(duration)), attendees: [EventPerson(name: nil, email: "guest@example.com")],
            organizer: nil, location: "Room", url: "", calendar: "Work"))
    }
    func testInProgressBeatsUpcomingAndUpcomingIsSoonest() {
        let current = meeting("current", offset: -600)
        let soon = meeting("soon", offset: 10)
        let later = meeting("later", offset: 100)
        XCTAssertEqual(UpcomingMeetingPicker.pick([later, soon, current], now: now)?.id, "current")
        XCTAssertEqual(UpcomingMeetingPicker.pick([later, soon], now: now)?.id, "soon")
    }
    func testIgnoredEvents() {
        var allDay = meeting(); allDay.isAllDay = true
        var cancelled = meeting(); cancelled.isCancelled = true
        var declined = meeting()
        declined.participants.append(CalendarParticipant(person: EventPerson(name: "Me", email: "me@example.com"), participantStatus: .declined, isCurrentUser: true))
        var alone = meeting(); alone.participants = []
        var onlyMe = declined; onlyMe.participants = Array(declined.participants.suffix(1))
        onlyMe.participants[0].participantStatus = .accepted
        XCTAssertTrue(UpcomingMeetingPicker.eligible([allDay, cancelled, declined, alone, onlyMe], now: now).isEmpty)
        var guestDeclined = meeting(); guestDeclined.participants[0].participantStatus = .declined
        XCTAssertNotNil(UpcomingMeetingPicker.pick([guestDeclined], now: now))
    }
    func testWindowBoundariesAndEndedEvent() {
        XCTAssertNotNil(UpcomingMeetingPicker.pick([meeting(offset: 43200)], now: now))
        XCTAssertNil(UpcomingMeetingPicker.pick([meeting(offset: 43201)], now: now))
        XCTAssertNotNil(UpcomingMeetingPicker.pick([meeting(offset: -7200, duration: 8000)], now: now))
        XCTAssertNil(UpcomingMeetingPicker.pick([meeting(offset: -7201, duration: 8000)], now: now))
        XCTAssertNil(UpcomingMeetingPicker.pick([meeting(offset: -3600, duration: 3600)], now: now))
    }
    func testNotificationIdentifierIsStableAndRescheduleHasNewIdentity() {
        let event = meeting("Recurring / event")
        XCTAssertEqual(event.notificationIdentifier, meeting("Recurring / event").notificationIdentifier)
        XCTAssertNotEqual(event.notificationIdentifier, meeting("Recurring / event", offset: 120).notificationIdentifier)
        XCTAssertNotEqual(event.notificationIdentifier, meeting("Other").notificationIdentifier)
    }
    func testEventJSONContract() throws {
        let event = meeting().context
        let data = try event.encoded()
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual(Set(json.keys), Set(["title", "start", "end", "attendees", "organizer", "location", "url", "calendar"]))
        XCTAssertTrue(json["organizer"] is NSNull)
        let people = try XCTUnwrap(json["attendees"] as? [[String: String]])
        XCTAssertEqual(people, [["name": "guest", "email": "guest@example.com"]])
        XCTAssertNotNil(ISO8601DateFormatter().date(from: try XCTUnwrap(json["start"] as? String)))
        XCTAssertEqual(try JSONDecoder().decode(MeetingEventContext.self, from: data), event)
        var organized = event
        organized.organizer = EventPerson(name: "Host", email: "host@example.com")
        let organizedJSON = try XCTUnwrap(JSONSerialization.jsonObject(with: organized.encoded()) as? [String: Any])
        XCTAssertEqual(organizedJSON["organizer"] as? [String: String], ["name": "Host", "email": "host@example.com"])
    }
}
