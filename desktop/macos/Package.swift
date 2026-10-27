// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "SuperTranslateFloating",
    platforms: [.macOS(.v13)],
    products: [.executable(name: "SuperTranslateFloating", targets: ["FloatingApp"])],
    targets: [
        .target(name: "FloatingCore"),
        .executableTarget(name: "FloatingApp", dependencies: ["FloatingCore"]),
        .testTarget(name: "FloatingCoreTests", dependencies: ["FloatingCore"]),
    ]
)
