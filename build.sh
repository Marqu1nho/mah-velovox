#!/usr/bin/env bash
# Build Velovox.app from the Swift sources in VeloVox/ (single binary, no Xcode
# project). Produces Velovox.app at the repo root, ad-hoc signed with a stable
# bundle id so TCC keys its Mic/Accessibility grants on identity, not the hash.
set -euo pipefail
cd "$(dirname "$0")"

APP="VeloVox.app"
BIN="VeloVox"
BUNDLE_ID="com.marco.velovox"

echo "assembling ${APP}..."
rm -rf "${APP}"
mkdir -p "${APP}/Contents/MacOS"

echo "compiling..."
# Compile straight into the bundle — the output binary name "Velovox" would
# otherwise collide with the VeloVox/ source directory.
xcrun -sdk macosx swiftc -O VeloVox/*.swift -o "${APP}/Contents/MacOS/${BIN}"

cat > "${APP}/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>VeloVox</string>
  <key>CFBundleDisplayName</key><string>VeloVox</string>
  <key>CFBundleIdentifier</key><string>${BUNDLE_ID}</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>CFBundleShortVersionString</key><string>0.1</string>
  <key>CFBundleExecutable</key><string>${BIN}</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSMinimumSystemVersion</key><string>26.0</string>
  <key>LSUIElement</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>VeloVox transcribes your speech on-device for dictation.</string>
  <key>NSSpeechRecognitionUsageDescription</key><string>VeloVox transcribes your speech on-device for dictation.</string>
  <key>CFBundleIconFile</key><string>VeloVox</string>
</dict>
</plist>
PLIST

# Wire icon assets: compile the .iconset into a .icns and copy the menu-bar
# template PDF into Resources so the app can load both from its bundle.
echo "bundling icons..."
mkdir -p "${APP}/Contents/Resources"
iconutil -c icns icons/VeloVox.iconset -o "${APP}/Contents/Resources/VeloVox.icns"
cp icons/MenuBarIcon.pdf "${APP}/Contents/Resources/MenuBarIcon.pdf"

# Sign with the stable self-signed "VeloVox Dev" identity (in the login keychain)
# so TCC keys Mic/Accessibility grants on a fixed identity, not the per-build hash —
# grants then survive rebuilds AND whoever launches the app. Falls back to ad-hoc if
# the cert is missing (e.g. a fresh machine before the .p12 is re-imported).
echo "signing..."
SIGN_ID="VeloVox Dev"
if security find-identity -p codesigning | grep -q "$SIGN_ID"; then
    codesign --force --sign "$SIGN_ID" --identifier "${BUNDLE_ID}" "${APP}"
else
    echo "  (no '$SIGN_ID' identity found — ad-hoc signing; expect TCC re-prompts)"
    codesign --force --sign - --identifier "${BUNDLE_ID}" "${APP}"
fi

echo "built $(pwd)/${APP}"
