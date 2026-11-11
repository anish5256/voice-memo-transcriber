// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "MeetingNotes",
    platforms: [.macOS(.v14)],
    targets: [.executableTarget(name: "MeetingNotes"),
              .testTarget(name: "MeetingNotesTests", dependencies: ["MeetingNotes"])],
    swiftLanguageModes: [.v5]
)
