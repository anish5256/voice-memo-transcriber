import SwiftUI

struct CalendarSettingsView: View {
    @ObservedObject var calendar: CalendarService
    var body: some View {
        Form {
            Section("Calendar access") {
                if calendar.hasAccess {
                    Label("Calendar connected", systemImage: "checkmark.circle.fill").foregroundStyle(.secondary)
                } else if calendar.blocked {
                    Text("Calendar access is denied or restricted. Allow MeetingNotes in System Settings → Privacy & Security → Calendars to show upcoming meetings.")
                    Button("Open Calendar Privacy Settings", action: CalendarService.openPrivacy)
                } else {
                    Text("Show upcoming meetings from the accounts in macOS Calendar.")
                    Button("Connect Calendar") { Task { await calendar.connect() } }
                }
                if let message = calendar.message { Text(message).font(.caption).foregroundStyle(.secondary) }
            }
            if calendar.hasAccess {
                Section("Calendars") {
                    ForEach(calendar.calendars, id: \.calendarIdentifier) { item in
                        Toggle(item.title, isOn: Binding(get: { calendar.isSelected(item.calendarIdentifier) },
                            set: { calendar.select(item.calendarIdentifier, enabled: $0) })).toggleStyle(.checkbox)
                    }
                    if calendar.calendars.isEmpty { Text("No calendars available.").foregroundStyle(.secondary) }
                }
            }
            Section {
                Toggle("Remind me to record meetings", isOn: Binding(get: { calendar.reminders }, set: calendar.setReminders))
            } footer: {
                Text("A notification at the meeting’s start offers Record. Recording starts only when you choose it.")
            }
        }.onAppear { calendar.refresh() }
    }
}

/// Shown in the sidebar whenever there is no upcoming meeting, so connecting is always one click away.
struct CalendarStatusCard: View {
    @ObservedObject var calendar: CalendarService
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Label("Up next", systemImage: "calendar").font(.caption).foregroundStyle(.secondary)
            if calendar.hasAccess {
                if calendar.calendars.isEmpty {
                    Text("No calendars on this Mac yet.").font(.callout)
                    Text("Add your Google account (turn on Calendars) to see its meetings here.")
                        .font(.caption).foregroundStyle(.secondary)
                    Button("Add Google Account…", action: CalendarService.openInternetAccounts).controlSize(.small)
                } else {
                    Text("No meetings in the next 12 hours.").font(.callout).foregroundStyle(.secondary)
                    Text("Only events with other attendees are shown.").font(.caption).foregroundStyle(.tertiary)
                }
            } else if calendar.blocked {
                Text("Calendar access is off.").font(.callout)
                Button("Open Privacy Settings", action: CalendarService.openPrivacy).controlSize(.small)
            } else {
                Text("See your upcoming meetings and record them in one click.").font(.callout)
                Button("Connect Calendar") { Task { await calendar.connect() } }
                    .buttonStyle(.borderedProminent).controlSize(.small)
            }
            if let message = calendar.message { Text(message).font(.caption).foregroundStyle(.secondary) }
        }.padding(.vertical, 8)
    }
}

struct UpcomingMeetingCard: View {
    let meeting: CalendarMeeting
    let busy: Bool
    let record: () -> Void
    private var names: String {
        let people = meeting.participants.filter { !$0.isCurrentUser }.map(\.person.name)
        return people.prefix(3).joined(separator: ", ") + (people.count > 3 ? " +\(people.count - 3)" : "")
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            Label("Up next", systemImage: "calendar").font(.caption).foregroundStyle(.secondary)
            Text(meeting.context.title).font(.headline).lineLimit(2)
            Text(meeting.start <= Date() ? "Now · ends " + meeting.end.formatted(date: .omitted, time: .shortened) : meeting.timeRange)
                .font(.caption).foregroundStyle(.secondary)
            Text(names).font(.caption).foregroundStyle(.secondary).lineLimit(2)
            Button(action: record) { Label("Record", systemImage: "record.circle") }.disabled(busy)
        }.padding(.vertical, 8)
    }
}
