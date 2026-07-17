# SignalDVR Android TV App

Native Android TV app for your SignalDVR server. Built for Nvidia Shield with full
D-pad navigation, DVR pause/rewind, EPG guide grid, and channel info overlay.

## Requirements

- Android Studio Hedgehog (2023.1.1) or newer
- Android SDK 34
- Java 17
- Your SignalDVR server running and accessible on your local network

## Build & Install

1. Open Android Studio → File → Open → select this `signaldvr-android` folder
2. Wait for Gradle sync to complete
3. Edit `app/src/main/res/xml/preferences.xml` — change the default server URL to yours
4. Connect your Nvidia Shield via ADB or use Android Studio's device manager
5. Run → Run 'app'

## Sideload to Shield without Android Studio

```bash
# Build release APK
./gradlew assembleDebug

# Install via ADB (enable ADB on Shield: Settings → Device → Developer Options → ADB)
adb connect 192.168.2.XXX:5555
adb install app/build/outputs/apk/debug/app-debug.apk
```

## Google Play submission checklist

- [ ] Replace `app_banner.xml` with a real 320×180px PNG banner (required for TV)
- [ ] Add `mipmap/ic_launcher` PNG icons (48/72/96/144/192px)
- [ ] Set `versionCode` and `versionName` in `app/build.gradle`
- [ ] Switch to `assembleRelease` with a signed keystore
- [ ] Add screenshots of the app on a TV
- [ ] Write Play Store listing (describe as "personal media server client")

## Remote control mapping

| Shield Button     | Action                        |
|-------------------|-------------------------------|
| D-pad centre      | Play / Pause                  |
| D-pad left        | Rewind 10 seconds             |
| D-pad right       | Fast forward 10 seconds       |
| Back              | Exit player / go back         |
| Info / Guide      | Show channel info overlay     |
| Play/Pause key    | Play / Pause                  |
| Rewind key        | Rewind 10 seconds             |
| Fast-forward key  | Fast forward 10 seconds       |

## App structure

```
MainActivity
 └── HomeFragment          (main menu — Leanback BrowseFragment)
      ├── LiveTvFragment    (D-pad channel list)
      │    └── PlayerActivity (ExoPlayer + DVR seek + GuideOverlayView)
      ├── EpgFragment       (full scrollable EPG grid)
      │    └── PlayerActivity
      ├── RecordingsFragment (recording list)
      │    └── PlayerActivity (direct file playback)
      └── SettingsFragment  (Leanback settings — server URL)
```

## Server changes required

Make sure your SignalDVR server has the three Kodi API routes added
(see `SERVER_CHANGES.py` in the Kodi plugin package):
- `/api/kodi/live`
- `/api/kodi/nowplaying/<channel>`
- `/api/kodi/timeshift/start/<channel>`
