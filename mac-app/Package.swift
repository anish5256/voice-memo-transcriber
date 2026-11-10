// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "MeetingNotes",
    platforms: [.macOS(.v14)],
    targets: [.executableTarget(name: "MeetingNotes")],
    swiftLanguageModes: [.v5]
)
