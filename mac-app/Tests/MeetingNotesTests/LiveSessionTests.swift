import XCTest
@testable import MeetingNotes

final class LiveSessionTests: XCTestCase {
    func testStateTransitions() {
        var state = RecorderStateMachine()
        XCTAssertFalse(state.apply(.pause))
        XCTAssertFalse(state.apply(.stop))
        XCTAssertTrue(state.apply(.start))
        XCTAssertFalse(state.apply(.start))
        XCTAssertFalse(state.apply(.resume))
        XCTAssertTrue(state.apply(.fail))
        XCTAssertEqual(state.state, .idle)
        XCTAssertTrue(state.apply(.start))
        XCTAssertTrue(state.apply(.ready))
        XCTAssertFalse(state.apply(.ready))
        XCTAssertTrue(state.apply(.pause))
        XCTAssertFalse(state.apply(.pause))
        XCTAssertTrue(state.apply(.resume))
        XCTAssertTrue(state.apply(.pause))
        XCTAssertTrue(state.apply(.stop)) // Stop while paused.
        XCTAssertFalse(state.apply(.stop))
        XCTAssertFalse(state.apply(.resume))
        XCTAssertTrue(state.apply(.finish))
        XCTAssertEqual(state.state, .idle)
        XCTAssertTrue(state.apply(.start))
        XCTAssertTrue(state.apply(.ready))
        XCTAssertTrue(state.apply(.stop))
        XCTAssertTrue(state.apply(.finish))
    }
    private func temporary() throws -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: url) }
        return url
    }
    private func segment(_ session: URL, index: Int) throws {
        let folder = session.appendingPathComponent("segments")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        try Data([1]).write(to: folder.appendingPathComponent(SegmentNaming.name(index) + ".m4a"))
    }
    func testSegmentNamingAndOrder() throws {
        XCTAssertEqual(SegmentNaming.name(1), "seg-00001")
        XCTAssertLessThan(SegmentNaming.name(9), SegmentNaming.name(10))
        let session = try temporary()
        try segment(session, index: 10)
        try segment(session, index: 9)
        try Data().write(to: session.appendingPathComponent("segments/seg-00008.done"))
        XCTAssertEqual(SegmentNaming.segments(in: session).map(\.lastPathComponent), ["seg-00009.m4a", "seg-00010.m4a"])
    }
    func testRecovery() throws {
        let meetings = try temporary()
        let live = meetings.appendingPathComponent("_live")
        let first = live.appendingPathComponent("first")
        let queued = live.appendingPathComponent("queued")
        let empty = live.appendingPathComponent("empty")
        try segment(first, index: 1) // Recovery includes audio without .done after a crash.
        try segment(queued, index: 1)
        try FileManager.default.createDirectory(at: empty.appendingPathComponent("segments"), withIntermediateDirectories: true)
        try Data().write(to: empty.appendingPathComponent("segments/seg-00001.done"))
        XCTAssertEqual(LiveSessionScanner.scan(meetings: meetings, queued: [queued]).map(\.path),
                       [first.resolvingSymlinksInPath().path])
        // A queued session given via a symlinked path must still be recognised as queued.
        XCTAssertEqual(LiveSessionScanner.scan(meetings: meetings, queued: [first, queued.resolvingSymlinksInPath()]), [])
        XCTAssertEqual(LiveSessionScanner.scan(meetings: empty, queued: []), [])
    }
    func testRenameSanitizingAndUniqueness() throws {
        XCTAssertEqual(RecordingRenamer.sanitized(" / . "), "Recording")
        XCTAssertEqual(RecordingRenamer.sanitized("A/B: C\nD"), "A B C D")
        XCTAssertEqual(RecordingRenamer.sanitized(String(repeating: "a", count: 80)).count, 60)
        let meetings = try temporary()
        let recordings = meetings.appendingPathComponent("_recordings")
        try FileManager.default.createDirectory(at: recordings, withIntermediateDirectories: true)
        let source = recordings.appendingPathComponent("original.m4a")
        let existing = recordings.appendingPathComponent("Title.m4a")
        try Data([1]).write(to: source)
        try Data([2]).write(to: existing)
        try RecordingRenamer.rename(source, title: "Title", meetings: meetings)
        XCTAssertEqual(try Data(contentsOf: existing), Data([2]))
        XCTAssertEqual(try Data(contentsOf: recordings.appendingPathComponent("Title (2).m4a")), Data([1]))
        let outside = meetings.appendingPathComponent("outside.m4a")
        try Data([3]).write(to: outside)
        try RecordingRenamer.rename(outside, title: "Title", meetings: meetings)
        XCTAssertTrue(FileManager.default.fileExists(atPath: outside.path))
    }
}
