#!/bin/bash
set -euo pipefail
PACKAGE_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ID="$(printf '%s' "$PACKAGE_DIR" | shasum -a 256 | cut -c 1-12)"
# Keep the signed bundle outside synced Desktop folders: File Provider can
# reattach FinderInfo there even after xattr removal, invalidating signing.
APP_DIR="$HOME/Library/Caches/SuperTranslateFloating/$PROJECT_ID/Super Translate Floating.app"
APP_LINK="$PACKAGE_DIR/build/Super Translate Floating.app"
swift build --package-path "$PACKAGE_DIR" -c release
BIN_DIR="$(swift build --package-path "$PACKAGE_DIR" -c release --show-bin-path)"
mkdir -p "$APP_DIR/Contents/MacOS"
cp "$BIN_DIR/SuperTranslateFloating" "$APP_DIR/Contents/MacOS/SuperTranslateFloating"
cat > "$APP_DIR/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>com.supertranslate.floating</string>
<key>CFBundleName</key><string>Super Translate Floating</string>
<key>CFBundleDisplayName</key><string>Super Translate 悬窗</string>
<key>CFBundleExecutable</key><string>SuperTranslateFloating</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>0.1.0</string>
<key>CFBundleVersion</key><string>1</string>
<key>LSMinimumSystemVersion</key><string>13.0</string>
<key>LSUIElement</key><true/>
<key>NSHighResolutionCapable</key><true/>
<key>NSAppTransportSecurity</key><dict>
<key>NSAllowsLocalNetworking</key><true/>
</dict>
</dict></plist>
PLIST
/usr/bin/plutil -lint "$APP_DIR/Contents/Info.plist"
# Finder/File Provider may attach these to a generated app in a synced Desktop.
# Remove only signing-incompatible metadata from this build artifact.
/usr/bin/xattr -dr com.apple.FinderInfo "$APP_DIR" 2>/dev/null || true
/usr/bin/xattr -dr com.apple.ResourceFork "$APP_DIR" 2>/dev/null || true
/usr/bin/codesign --force --sign - "$APP_DIR"
/usr/bin/codesign --verify --deep --strict "$APP_DIR"
mkdir -p "$PACKAGE_DIR/build"
if [[ -e "$APP_LINK" && ! -L "$APP_LINK" ]]; then
    printf '构建路径已被占用，请先移走：%s\n' "$APP_LINK" >&2
    exit 1
fi
ln -sfn "$APP_DIR" "$APP_LINK"
printf 'Built: %s\n' "$APP_DIR"
