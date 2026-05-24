# Sizeify iOS

Native SwiftUI app for Sizeify. iOS 17+, Swift 5.

## Open in Xcode

```bash
open Sizeify.xcworkspace
```

## Build from CLI

```bash
xcodebuild -workspace Sizeify.xcworkspace -scheme Sizeify \
  -destination 'platform=iOS Simulator,name=iPhone 16' build
```

The `iPhone 16` simulator name is intentional — CI uses `iPhone 15` to match the PRD spec, but the local Xcode 26.2 install doesn't ship iPhone 15 in its default device set. See [ADR-0001](../../docs/adr/0001-phase-0-foundation.md) §6 ("Notable decisions").

The deployment target is iOS 17; bundle ID is `com.sizeify.app`; signing is set to Automatic with no team configured (simulator builds use ad-hoc signing).

## Tests

No test target yet — to be added in a later phase.

```bash
xcodebuild test -workspace Sizeify.xcworkspace -scheme Sizeify \
  -destination 'platform=iOS Simulator,name=iPhone 16'
```

## Lint and format

Pinned tool versions (run from `apps/ios/`):

| Tool         | Version  | How to install                |
| ------------ | -------- | ----------------------------- |
| swift-format | 6.2.3    | Bundled with Swift 6.2 / Xcode 26 (`swift format` — no install needed) |
| swiftlint    | 0.63.2   | `brew install swiftlint`      |

Config files:

- [.swift-format](.swift-format) — Apple swift-format rules. (Note: Apple's tool is `swift format` with a space; its config file is `.swift-format` with a hyphen — distinct from the third-party `swiftformat` tool, which reads `.swiftformat`.)
- [.swiftlint.yml](.swiftlint.yml) — SwiftLint config scoped to `Sizeify/`.

Run:

```bash
swift format -i -r Sizeify/   # format in place
swift format lint -r Sizeify/ # lint without modifying
swiftlint                     # SwiftLint
```

All three should exit 0 on a clean tree.

## Entitlements

[Sizeify/Sizeify.entitlements](Sizeify/Sizeify.entitlements) declares App Groups and Keychain Sharing with empty arrays. Phase 6 (share extension) fills these in with concrete group identifiers.
