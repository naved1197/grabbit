#!/usr/bin/env bash
# Creates Grabbit's Android release signing key and saves it as the two GitHub secrets the release
# workflow signs APKs with. Run it once, on your own computer:
#
#     grabbit-android/packaging/make_release_key.sh [path/to/grabbit-release.jks]
#
# keytool and gh ask for the password themselves; it isn't written anywhere else.
#
# Keep the .jks file and its password safe (a password manager, plus a backup of the file). Every
# update has to be signed with this same key: if it's lost, people have to uninstall Grabbit to
# install a newer version.
set -euo pipefail

KEYSTORE="${1:-$HOME/.grabbit/grabbit-release.jks}"
REPO="${GRABBIT_REPO:-naved1197/grabbit}"
ALIAS="grabbit"

command -v keytool >/dev/null || { echo "This needs keytool, which comes with a JDK (e.g. brew install --cask temurin@17)."; exit 1; }
command -v gh >/dev/null || { echo "This needs the GitHub CLI (gh), signed in to an account that can edit $REPO."; exit 1; }

if [[ -e "$KEYSTORE" ]]; then
    echo "Using the existing key at $KEYSTORE."
else
    mkdir -p "$(dirname "$KEYSTORE")"
    chmod 700 "$(dirname "$KEYSTORE")"
    echo "Creating Grabbit's release key at $KEYSTORE."
    echo "Choose a strong password and save it in your password manager first. keytool asks for it twice."
    keytool -genkeypair -storetype PKCS12 -keystore "$KEYSTORE" -alias "$ALIAS" \
        -keyalg RSA -keysize 4096 -validity 10000 -dname "CN=Grabbit, O=Cubehole"
    chmod 600 "$KEYSTORE"
fi

echo
echo "Saving the key to GitHub as ANDROID_KEYSTORE_BASE64 on $REPO…"
base64 < "$KEYSTORE" | tr -d '\n' | gh secret set ANDROID_KEYSTORE_BASE64 --repo "$REPO"

echo
echo "Now the same password once more, for ANDROID_KEYSTORE_PASSWORD (what you type isn't shown):"
gh secret set ANDROID_KEYSTORE_PASSWORD --repo "$REPO"

echo
echo "Done. Releases built from a v* tag are now signed with this key."
echo "Back up $KEYSTORE and its password somewhere other than this computer."
