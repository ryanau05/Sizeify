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

The deployment target is iOS 17; bundle ID is `com.sizeify.app`; signing is set to Automatic with no team configured (simulator builds use ad-hoc signing).

## Tests

No test target yet — to be added in a later phase.

```bash
xcodebuild test -workspace Sizeify.xcworkspace -scheme Sizeify \
  -destination 'platform=iOS Simulator,name=iPhone 16'
```

## Lint and format

```bash
swift format -i -r Sizeify/
swiftlint
```

## Entitlements

[Sizeify/Sizeify.entitlements](Sizeify/Sizeify.entitlements) declares App Groups and Keychain Sharing with empty arrays. Phase 6 (share extension) fills these in with concrete group identifiers.
