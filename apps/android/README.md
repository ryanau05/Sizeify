# Sizeify Android

Native Jetpack Compose app for Sizeify. Kotlin 2.x, AGP 8.x, minSdk 26, targetSdk 34.

## Prerequisites

| Tool          | Version    | How to install                              |
| ------------- | ---------- | ------------------------------------------- |
| JDK (Gradle)  | 21         | `brew install openjdk@21` (keg-only)        |
| Gradle        | 8.11.1     | via the wrapper in [`./gradlew`](gradlew) — no host install required |
| Android SDK   | platform 34, build-tools 34.0.0, platform-tools | `brew install --cask android-commandlinetools` then `sdkmanager --install "platforms;android-34" "build-tools;34.0.0" "platform-tools"` |

The repo's macOS default JDK is Java 25, which Gradle 8.x can't launch on. We pin the daemon JDK to 21 via [`gradle/gradle-daemon-jvm.properties`](gradle/gradle-daemon-jvm.properties); Gradle auto-discovers the brew install through [`gradle.properties`](gradle.properties). You should not need to set `JAVA_HOME`.

## Build

```bash
./gradlew assembleDebug
```

Produces `app/build/outputs/apk/debug/app-debug.apk` (~9 MB).

## Tests

```bash
./gradlew test                  # JVM unit tests
./gradlew connectedAndroidTest  # instrumented tests (requires a running emulator/device)
```

## Lint / format

```bash
./gradlew ktlintFormat ktlintCheck detekt
```

(ktlint/detekt plugins not wired yet — will land in a later phase.)

## Configuration

- Bundle ID / applicationId: `com.sizeify.app`
- `minSdk = 26`, `targetSdk = 34`, `compileSdk = 34`
- Hilt scaffolded via [`@HiltAndroidApp`](app/src/main/kotlin/com/sizeify/app/SizeifyApplication.kt); KSP processes the annotations.
- Single Compose activity at [`MainActivity.kt`](app/src/main/kotlin/com/sizeify/app/MainActivity.kt) showing the text "Sizeify".

## Files NOT committed

[`local.properties`](local.properties) declares the SDK path (machine-specific) and is gitignored. If you check out the repo fresh on a new machine, recreate it:

```
sdk.dir=/Users/<you>/Library/Android/sdk
```
