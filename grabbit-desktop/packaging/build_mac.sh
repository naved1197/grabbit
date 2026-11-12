#!/usr/bin/env bash
# Builds dist/Grabbit-<version>-mac-<apple-silicon|intel>.dmg on this Mac.
# The app is built for this Mac's processor; build on an Intel Mac (or CI) for Intel.
set -euo pipefail
cd "$(dirname "$0")/.."

uv sync --locked
uv run python packaging/fetch_tools.py
uv run pyinstaller --noconfirm --clean --distpath dist --workpath build/pyinstaller packaging/grabbit.spec

VERSION=$(uv run python -c 'import grabbit_desktop as g; print(g.VERSION)')
ARCH=$([[ "$(uname -m)" == "arm64" ]] && echo apple-silicon || echo intel)
APP=dist/Grabbit.app

# Apple Silicon only runs signed code. Without a paid Apple Developer ID this is an
# "ad-hoc" signature, so people opening it the first time must approve it in
# System Settings > Privacy & Security (see README.md).
if ! codesign --verify --deep --strict "$APP" 2>/dev/null; then
  codesign --force --deep --sign - "$APP"
fi
codesign --verify --deep --strict "$APP"

STAGE=build/dmg
rm -rf "$STAGE" && mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
DMG="dist/Grabbit-$VERSION-mac-$ARCH.dmg"
rm -f "$DMG"
hdiutil create -volname "Grabbit" -srcfolder "$STAGE" -fs HFS+ -format UDZO -ov "$DMG" >/dev/null
echo "Built $DMG ($(du -h "$DMG" | cut -f1))"
