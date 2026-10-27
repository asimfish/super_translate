#!/bin/bash
set -euo pipefail
umask 077
PACKAGE_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$HOME/Applications/Super Translate Floating.app"
AGENT_DIR="$HOME/Library/LaunchAgents"
LABEL="com.supertranslate.floating"
DOMAIN="gui/$(id -u)"
LOG_DIR="$HOME/Library/Logs/SuperTranslateFloating"
TUNNEL_LABEL="com.liyufeng.supertranslate.tunnel"
TUNNEL_PLIST="$AGENT_DIR/$TUNNEL_LABEL.plist"

"$PACKAGE_DIR/build.sh"
mkdir -p "$HOME/Applications" "$AGENT_DIR" "$LOG_DIR"
STAGING="$(mktemp -d "$HOME/Applications/.supertranslate-install.XXXXXX")"
/usr/bin/ditto --noextattr "$PACKAGE_DIR/build/Super Translate Floating.app" "$STAGING/Super Translate Floating.app"
/usr/bin/codesign --verify --deep --strict "$STAGING/Super Translate Floating.app"
if [[ -e "$APP_DIR" ]]; then
    if [[ "$(/usr/bin/plutil -extract CFBundleIdentifier raw "$APP_DIR/Contents/Info.plist")" != "$LABEL" ]]; then
        printf '安装路径已被其他 App 占用：%s\n' "$APP_DIR" >&2
        exit 1
    fi
fi
if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
    launchctl bootout "$DOMAIN/$LABEL"
fi
if [[ -e "$APP_DIR" ]]; then
    BACKUP_DIR="$HOME/Library/Caches/SuperTranslateFloating/InstallBackups/$(date +%Y%m%d-%H%M%S)-$$"
    mkdir -p "$BACKUP_DIR"
    mv "$APP_DIR" "$BACKUP_DIR/Super Translate Floating.app"
fi
mv "$STAGING/Super Translate Floating.app" "$APP_DIR"
rmdir "$STAGING"

AGENT_TMP="$(mktemp "$AGENT_DIR/.supertranslate-floating.XXXXXX")"
cat > "$AGENT_TMP" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>RunAtLoad</key><true/>
<key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
<key>LimitLoadToSessionType</key><string>Aqua</string>
<key>ProcessType</key><string>Interactive</string>
</dict></plist>
PLIST
/usr/bin/plutil -insert Label -string "$LABEL" "$AGENT_TMP"
/usr/bin/plutil -insert ProgramArguments -json '[]' "$AGENT_TMP"
/usr/bin/plutil -insert ProgramArguments.0 -string "$APP_DIR/Contents/MacOS/SuperTranslateFloating" "$AGENT_TMP"
/usr/bin/plutil -insert StandardOutPath -string "$LOG_DIR/stdout.log" "$AGENT_TMP"
/usr/bin/plutil -insert StandardErrorPath -string "$LOG_DIR/stderr.log" "$AGENT_TMP"
/usr/bin/plutil -lint "$AGENT_TMP"
mv "$AGENT_TMP" "$AGENT_DIR/$LABEL.plist"

# Reuse the existing system tunnel. Never launch a second translation backend.
if [[ -f "$TUNNEL_PLIST" ]]; then
    launchctl enable "$DOMAIN/$TUNNEL_LABEL"
    if ! launchctl print "$DOMAIN/$TUNNEL_LABEL" >/dev/null 2>&1; then
        launchctl bootstrap "$DOMAIN" "$TUNNEL_PLIST"
    else
        launchctl kickstart "$DOMAIN/$TUNNEL_LABEL"
    fi
fi
launchctl enable "$DOMAIN/$LABEL"
launchctl bootstrap "$DOMAIN" "$AGENT_DIR/$LABEL.plist"
printf '已安装：%s\n悬窗已启动，并设置为登录后自动启动。\n' "$APP_DIR"
