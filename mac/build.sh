#!/usr/bin/env bash
# Build SpeakWrite.app from main.swift (single-file native app, no Xcode project).
set -euo pipefail
cd "$(dirname "$0")"

APP="SpeakWrite.app"
BIN="SpeakWrite"
BUNDLE_ID="com.marco.speakwrite"

echo "compiling..."
xcrun -sdk macosx swiftc -O main.swift -o "${BIN}"

echo "assembling ${APP}..."
rm -rf "${APP}"
mkdir -p "${APP}/Contents/MacOS"
mv "${BIN}" "${APP}/Contents/MacOS/${BIN}"

cat > "${APP}/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>SpeakWrite</string>
  <key>CFBundleDisplayName</key><string>SpeakWrite</string>
  <key>CFBundleIdentifier</key><string>${BUNDLE_ID}</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>CFBundleShortVersionString</key><string>0.1</string>
  <key>CFBundleExecutable</key><string>${BIN}</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSMinimumSystemVersion</key><string>26.0</string>
  <key>LSUIElement</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>SpeakWrite transcribes your speech.</string>
</dict>
</plist>
PLIST

# Ad-hoc sign with a stable bundle id so TCC has something to key on.
echo "signing..."
codesign --force --sign - --identifier "${BUNDLE_ID}" "${APP}"

echo "built $(pwd)/${APP}"
