import AppKit
import EventKit
import Combine
import UserNotifications

@MainActor
final class CalendarService: ObservableObject {
    @Published private(set) var access = EKEventStore.authorizationStatus(for: .event)
    @Published private(set) var calendars: [EKCalendar] = []
    @Published private(set) var upcoming: CalendarMeeting?
    @Published private(set) var events: [CalendarMeeting] = []
    @Published var message: String?
    @Published private(set) var selectedIDs: Set<String>?
    @Published private(set) var reminders: Bool
    private let store = EKEventStore()
    private var observers: [NSObjectProtocol] = []
    private var ticker: Task<Void, Never>?
    private var notificationTask: Task<Void, Never>?

    init() {
        selectedIDs = UserDefaults.standard.stringArray(forKey: "calendarIDs").map(Set.init)
        reminders = UserDefaults.standard.object(forKey: "calendarReminders") as? Bool ?? true
        observers.append(NotificationCenter.default.addObserver(forName: .EKEventStoreChanged, object: nil, queue: .main) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        })
        observers.append(NotificationCenter.default.addObserver(forName: NSApplication.didBecomeActiveNotification, object: nil, queue: .main) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        })
        ticker = Task { [weak self] in
            self?.refresh()
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(60))
                guard !Task.isCancelled else { break }
                self?.refresh()
            }
        }
    }
    deinit {
        ticker?.cancel(); notificationTask?.cancel()
        for observer in observers { NotificationCenter.default.removeObserver(observer) }
    }
    var hasAccess: Bool { access == .fullAccess }
    var blocked: Bool { access == .denied || access == .restricted || access == .writeOnly }
    func connect() async {
        do { _ = try await store.requestFullAccessToEvents() }
        catch { message = error.localizedDescription }
        refresh()
    }
    func isSelected(_ id: String) -> Bool { selectedIDs?.contains(id) ?? true }
    func select(_ id: String, enabled: Bool) {
        var ids = selectedIDs ?? Set(calendars.map(\.calendarIdentifier))
        if enabled { ids.insert(id) } else { ids.remove(id) }
        selectedIDs = ids
        UserDefaults.standard.set(Array(ids).sorted(), forKey: "calendarIDs")
        refresh()
    }
    func setReminders(_ enabled: Bool) {
        reminders = enabled
        UserDefaults.standard.set(enabled, forKey: "calendarReminders")
        refresh()
    }
    /// Google calendars reach macOS Calendar through Internet Accounts.
    static func openInternetAccounts() {
        if let url = URL(string: "x-apple.systempreferences:com.apple.Internet-Accounts-Settings.extension") {
            NSWorkspace.shared.open(url)
        }
    }
    static func openPrivacy() {
        if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Calendars") {
            NSWorkspace.shared.open(url)
        }
    }
    func refresh() {
        access = EKEventStore.authorizationStatus(for: .event)
        let now = Date()
        if hasAccess {
            calendars = store.calendars(for: .event).sorted { $0.title.localizedStandardCompare($1.title) == .orderedAscending }
            let selected = calendars.filter { isSelected($0.calendarIdentifier) }
            events = selected.isEmpty ? [] : store.events(matching: store.predicateForEvents(
                withStart: now.addingTimeInterval(-7200), end: now.addingTimeInterval(43200), calendars: selected)).compactMap(Self.map)
        } else { calendars = []; events = [] }
        upcoming = UpcomingMeetingPicker.pick(events, now: now)
        let eligible = reminders ? UpcomingMeetingPicker.eligible(events, now: now).filter { $0.start > now } : []
        let previous = notificationTask
        previous?.cancel()
        notificationTask = Task {
            // Drain any in-flight add before reading pending requests, so an old schedule
            // cannot reinsert a removed event after the new reconciliation.
            await previous?.value
            guard !Task.isCancelled else { return }
            await schedule(eligible)
        }
    }
    private static func person(_ participant: EKParticipant) -> EventPerson {
        let raw = participant.url.absoluteString
        let email = participant.url.scheme?.lowercased() == "mailto" ?
            (String(raw.dropFirst(7)).components(separatedBy: "?").first ?? "").removingPercentEncoding ?? "" : ""
        return EventPerson(name: participant.name, email: email)
    }
    private static func map(_ event: EKEvent) -> CalendarMeeting? {
        guard let id = event.eventIdentifier, let start = event.startDate, let end = event.endDate else { return nil }
        let attendees = event.attendees ?? []
        let participants = attendees.map { participant in
            let status: CalendarParticipant.Status
            switch participant.participantStatus {
            case .pending: status = .pending
            case .accepted: status = .accepted
            case .declined: status = .declined
            case .tentative: status = .tentative
            case .delegated: status = .delegated
            case .completed: status = .completed
            case .inProcess: status = .inProcess
            default: status = .unknown
            }
            return CalendarParticipant(person: person(participant), participantStatus: status, isCurrentUser: participant.isCurrentUser)
        }
        return CalendarMeeting(id: id, start: start, end: end, isAllDay: event.isAllDay,
            isCancelled: event.status == .canceled, participants: participants,
            context: MeetingEventContext(title: event.title ?? "Meeting", start: MeetingEventContext.timestamp(start),
                end: MeetingEventContext.timestamp(end), attendees: attendees.map(person),
                organizer: event.organizer.map(person), location: event.location ?? "", url: event.url?.absoluteString ?? "",
                calendar: event.calendar.title))
    }
    private func schedule(_ events: [CalendarMeeting]) async {
        let center = UNUserNotificationCenter.current()
        let pending = await center.pendingNotificationRequests()
        guard !Task.isCancelled else { return }
        let desired = Set(events.map(\.notificationIdentifier))
        center.removePendingNotificationRequests(withIdentifiers: pending.map(\.identifier).filter {
            $0.hasPrefix(MeetingNotificationIdentifier.prefix) && !desired.contains($0)
        })
        guard reminders, hasAccess else { return }
        do {
            let settings = await center.notificationSettings()
            guard !Task.isCancelled else { return }
            if settings.authorizationStatus == .notDetermined {
                guard try await center.requestAuthorization(options: [.alert, .sound]) else {
                    message = "Meeting reminders are disabled. Enable notifications for MeetingNotes in System Settings."
                    return
                }
            } else if settings.authorizationStatus == .denied {
                message = "Meeting reminders are disabled. Enable notifications for MeetingNotes in System Settings."
                return
            }
            for event in events {
                guard !Task.isCancelled, event.start > Date() else { return }
                let content = UNMutableNotificationContent()
                content.title = event.context.title
                content.body = "Your meeting is starting. Record it to create notes."
                content.sound = .default
                content.categoryIdentifier = "MEETING_RECORD"
                content.userInfo = ["eventIdentifier": event.id, "eventStart": event.start.timeIntervalSince1970]
                let trigger = UNTimeIntervalNotificationTrigger(timeInterval: max(1, event.start.timeIntervalSinceNow), repeats: false)
                try await center.add(UNNotificationRequest(identifier: event.notificationIdentifier, content: content, trigger: trigger))
            }
        } catch { if !Task.isCancelled { message = "Could not schedule meeting reminders: \(error.localizedDescription)" } }
    }
}
