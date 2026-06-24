#!/usr/bin/env bash
# Build Velovox.app from the Swift sources in Velovox/ (single binary, no Xcode
# project). Produces Velovox.app at the repo root, ad-hoc signed with a stable
# bundle id so TCC keys its Mic/Accessibility grants on identity, not the hash.
set -euo pipefail
cd "$(dirname "$0")"

APP="Velovox.app"
BIN="Velovox"
BUNDLE_ID="com.marco.velovox"

echo "assembling ${APP}..."
rm -rf "${APP}"
mkdir -p "${APP}/Contents/MacOS"

echo "compiling..."
# Compile straight into the bundle — the output binary name "Velovox" would
# otherwise collide with the Velovox/ source directory.
xcrun -sdk macosx swiftc -O Velovox/*.swift -o "${APP}/Contents/MacOS/${BIN}"

cat > "${APP}/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Velovox</string>
  <key>CFBundleDisplayName</key><string>Velovox</string>
  <key>CFBundleIdentifier</key><string>${BUNDLE_ID}</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>CFBundleShortVersionString</key><string>0.1</string>
  <key>CFBundleExecutable</key><string>${BIN}</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSMinimumSystemVersion</key><string>26.0</string>
  <key>LSUIElement</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>Velovox transcribes your speech on-device for dictation.</string>
  <key>NSSpeechRecognitionUsageDescription</key><string>Velovox transcribes your speech on-device for dictation.</string>
</dict>
</plist>
PLIST

# Ad-hoc sign with a stable bundle id so TCC has something to key on.
echo "signing..."
codesign --force --sign - --identifier "${BUNDLE_ID}" "${APP}"

echo "built $(pwd)/${APP}"
