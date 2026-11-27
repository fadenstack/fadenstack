#!/bin/sh
# The Mac release beyond the binary: the signing keychain, the flat pkg and
# its notarization. build-binary.sh makes and signs the binary itself
# (FADEN_MAC_SIGNING); this takes it from dist/.
#
#   sh macos-package.sh secrets            # the developer-id inputs: 0 all set and well-formed,
#                                          #   1 none set, 3 some set or malformed (never ad hoc)
#   sh macos-package.sh keychain           # CI: an ephemeral keychain holding the Developer ID identities
#   sh macos-package.sh verify BINARY      # signed by FADEN_MAC_CODESIGN_IDENTITY's Developer ID and team
#   sh macos-package.sh pkg [--unsigned]   # dist/faden-agent-macos-arm64.pkg
#   sh macos-package.sh notarize           # notarytool, staple, validate, Gatekeeper's assessment
#   sh macos-package.sh cleanup            # CI, always: remove the keychain and the notary key
#
# Inputs (the release workflow's secrets; nothing here has a default):
#   FADEN_MAC_CODESIGN_IDENTITY   "Developer ID Application: <name> (<team>)"
#   FADEN_MAC_INSTALLER_IDENTITY  "Developer ID Installer: <name> (<team>)"
#   APPLE_CERTIFICATES_P12_BASE64, APPLE_CERTIFICATES_P12_PASSWORD
#                                 one .p12 holding both identities (keychain)
#   APPLE_NOTARY_KEY_ID, APPLE_NOTARY_ISSUER_ID, APPLE_NOTARY_KEY_P8_BASE64
#                                 an App Store Connect API key (notarize)
#
# The pkg carries the verified binary to /Library/Fadenstack/staging/ and
# nothing else: no scripts. Joining and activation stay with the generated
# install script, which runs the staged binary's `join` (the agent places
# itself in /Library/Fadenstack/agent/<version>/) and then removes the staged
# copy. Staging is root's: /Library/Fadenstack and everything the pkg writes
# are root:wheel and not writable by the service account.
#
# --unsigned is for checking the package shape on a Mac without the
# Installer identity; such a pkg is never released.
set -eu

SRC=$(cd "$(dirname "$0")" && pwd)
ARTIFACT="faden-agent-macos-arm64"
BIN="$SRC/dist/$ARTIFACT"
PKG="$SRC/dist/$ARTIFACT.pkg"
PKG_ID="com.fadenstack.agent.pkg"
STAGING="Library/Fadenstack/staging"
WORK="${RUNNER_TEMP:-${TMPDIR:-/tmp}}"
KEYCHAIN="${FADEN_MAC_KEYCHAIN:-$WORK/faden-signing.keychain-db}"
#: The App Store Connect key, while notarize runs; cleanup removes it whatever happened.
NOTARY_KEY="$WORK/faden-notary.p8"

die() { echo "ERROR: $*" >&2; exit 1; }

version() {
  sed -n 's/^__version__ = "\(.*\)"/\1/p' "$SRC/faden_agent/__init__.py"
}

# The inputs each step needs, by name; prints the missing ones.
missing() {
  for name in "$@"; do
    eval "value=\${$name:-}"
    [ -n "$value" ] || echo "$name"
  done
}

need() {
  gaps=$(missing "$@")
  [ -z "$gaps" ] || die "developer-id signing needs $(echo $gaps | sed 's/ /, /g'); nothing was signed."
}

ALL_INPUTS="FADEN_MAC_CODESIGN_IDENTITY FADEN_MAC_INSTALLER_IDENTITY APPLE_CERTIFICATES_P12_BASE64 APPLE_CERTIFICATES_P12_PASSWORD APPLE_NOTARY_KEY_ID APPLE_NOTARY_ISSUER_ID APPLE_NOTARY_KEY_P8_BASE64"

# The team in "Developer ID Application: Name (TEAMID)".
team_of() {
  printf '%s' "$1" | sed -n 's/.*(\([A-Z0-9]\{10\}\))$/\1/p'
}

cmd_secrets() {
  gaps=$(missing $ALL_INPUTS)
  if [ "$(echo $gaps)" = "$(echo $ALL_INPUTS)" ]; then
    echo "developer-id: no Apple signing inputs are set"
    exit 1
  fi
  if [ -n "$gaps" ]; then
    # Some but not all: a typo or a forgotten secret, never a choice of ad hoc.
    echo "developer-id: incomplete, missing $(echo $gaps | sed 's/ /, /g')"
    exit 3
  fi
  bad=""
  case "$FADEN_MAC_CODESIGN_IDENTITY" in "Developer ID Application: "*) ;; *) bad="$bad FADEN_MAC_CODESIGN_IDENTITY" ;; esac
  case "$FADEN_MAC_INSTALLER_IDENTITY" in "Developer ID Installer: "*) ;; *) bad="$bad FADEN_MAC_INSTALLER_IDENTITY" ;; esac
  app_team=$(team_of "$FADEN_MAC_CODESIGN_IDENTITY")
  [ -n "$app_team" ] || bad="$bad FADEN_MAC_CODESIGN_IDENTITY(team)"
  [ "$app_team" = "$(team_of "$FADEN_MAC_INSTALLER_IDENTITY")" ] || bad="$bad FADEN_MAC_INSTALLER_IDENTITY(team)"
  if [ -n "$bad" ]; then
    echo "developer-id: malformed:$bad (expected \"Developer ID Application|Installer: <name> (<TEAMID>)\", one team)"
    exit 3
  fi
  echo "developer-id: all inputs present, team $app_team"
}

# A release binary carries the Developer ID it claims, of its team, under the
# hardened runtime: --verify and the runtime flag alone pass for ad hoc too.
cmd_verify() {
  bin=${1:-}
  [ -f "$bin" ] || die "verify needs the binary to check."
  need FADEN_MAC_CODESIGN_IDENTITY
  team=$(team_of "$FADEN_MAC_CODESIGN_IDENTITY")
  [ -n "$team" ] || die "FADEN_MAC_CODESIGN_IDENTITY names no team: $FADEN_MAC_CODESIGN_IDENTITY"
  codesign --verify --strict "$bin" || die "$bin carries no valid signature."
  info=$(codesign --display --verbose=2 "$bin" 2>&1) || die "codesign cannot read $bin's signature."
  printf '%s\n' "$info" | grep -qx "Authority=$FADEN_MAC_CODESIGN_IDENTITY" \
    || die "$bin is not signed by \"$FADEN_MAC_CODESIGN_IDENTITY\"."
  printf '%s\n' "$info" | grep -qx "TeamIdentifier=$team" || die "$bin's TeamIdentifier is not $team."
  printf '%s\n' "$info" | grep -q 'flags=.*runtime' || die "$bin is not signed with the hardened runtime."
  echo "verified: $bin, $FADEN_MAC_CODESIGN_IDENTITY"
}

cmd_keychain() {
  need APPLE_CERTIFICATES_P12_BASE64 APPLE_CERTIFICATES_P12_PASSWORD FADEN_MAC_CODESIGN_IDENTITY FADEN_MAC_INSTALLER_IDENTITY
  [ "${CI:-}" = true ] || die "keychain is for the release runner (CI=true): it changes the keychain search list."
  pass=$(openssl rand -hex 24)
  p12=$(mktemp -t faden-p12)
  trap 'rm -f "$p12"' EXIT
  printf '%s' "$APPLE_CERTIFICATES_P12_BASE64" | base64 -D > "$p12"
  security create-keychain -p "$pass" "$KEYCHAIN"
  # Long enough to outlast notarize's 30-minute wait after the build.
  security set-keychain-settings -lut 7200 "$KEYCHAIN"
  security unlock-keychain -p "$pass" "$KEYCHAIN"
  security import "$p12" -k "$KEYCHAIN" -P "$APPLE_CERTIFICATES_P12_PASSWORD" \
    -T /usr/bin/codesign -T /usr/bin/productbuild -T /usr/bin/productsign
  security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "$pass" "$KEYCHAIN" >/dev/null
  # shellcheck disable=SC2046  # the list is one path per line, none with spaces on a runner
  security list-keychains -d user -s "$KEYCHAIN" $(security list-keychains -d user | tr -d '"')
  security find-identity -v -p codesigning "$KEYCHAIN" | grep -qF "\"$FADEN_MAC_CODESIGN_IDENTITY\"" \
    || die "the .p12 holds no \"$FADEN_MAC_CODESIGN_IDENTITY\"."
  security find-identity -v "$KEYCHAIN" | grep -qF "\"$FADEN_MAC_INSTALLER_IDENTITY\"" \
    || die "the .p12 holds no \"$FADEN_MAC_INSTALLER_IDENTITY\"."
  echo "keychain: $KEYCHAIN"
}

cmd_cleanup() {
  security delete-keychain "$KEYCHAIN" 2>/dev/null || true
  rm -f "$NOTARY_KEY"
}

cmd_pkg() {
  unsigned=0
  [ "${1:-}" = "--unsigned" ] && unsigned=1
  [ "$unsigned" = 1 ] || need FADEN_MAC_INSTALLER_IDENTITY
  [ -f "$BIN" ] || die "$BIN is missing: run build-binary.sh macos-arm64 first."
  codesign --verify --strict "$BIN" || die "$BIN carries no valid signature."
  ver=$(version)
  [ -n "$ver" ] || die "no __version__ in faden_agent/__init__.py"

  work=$(mktemp -d -t faden-pkg)
  trap 'rm -rf "$work"' EXIT
  mkdir -p "$work/root/$STAGING"
  # The staged name carries the version, so a pkg never replaces the binary
  # an older install script is still running.
  cp -X "$BIN" "$work/root/$STAGING/faden-agent-$ver"
  # Extended attributes ride along as AppleDouble ._ entries, which Installer
  # applies as attributes again, not as files. Drop what can be dropped;
  # com.apple.provenance cannot be, and is harmless.
  xattr -cr "$work/root" 2>/dev/null || true
  chmod 0755 "$work/root/Library" "$work/root/Library/Fadenstack" "$work/root/$STAGING"
  chmod 0755 "$work/root/$STAGING/faden-agent-$ver"
  # --ownership recommended: everything lands root:wheel, whoever built it.
  pkgbuild --root "$work/root" --identifier "$PKG_ID" --version "$ver" \
    --install-location / --ownership recommended "$work/agent.pkg" >/dev/null
  rm -f "$PKG"
  if [ "$unsigned" = 1 ]; then
    productbuild --package "$work/agent.pkg" "$PKG" >/dev/null
  else
    productbuild --package "$work/agent.pkg" --sign "$FADEN_MAC_INSTALLER_IDENTITY" --timestamp "$PKG" >/dev/null
    pkgutil --check-signature "$PKG" | grep -q 'Developer ID Installer' \
      || die "$PKG is not signed with a Developer ID Installer identity."
  fi
  check_payload "$PKG" "$ver"
  echo "pkg: $PKG ($( [ "$unsigned" = 1 ] && echo unsigned || echo signed ))"
}

# What the pkg installs, read back from the pkg itself: exactly the staged
# binary, owned by root, and no scripts.
check_payload() {
  pkg=$1 ver=$2
  out=$(mktemp -d -t faden-pkgx)
  pkgutil --expand "$pkg" "$out/x"
  inner=$(find "$out/x" -name '*.pkg' -type d | head -1)
  [ -n "$inner" ] || die "$pkg holds no component package."
  [ ! -e "$inner/Scripts" ] || die "$pkg carries install scripts; it must not."
  files=$(lsbom -p fUG "$inner/Bom" | awk '$2 != "root" || $3 != "wheel"')
  [ -z "$files" ] || die "$pkg installs something not owned by root: $files"
  lsbom -s "$inner/Bom" | grep -qx "./$STAGING/faden-agent-$ver" \
    || die "$pkg does not stage ./$STAGING/faden-agent-$ver."
  # ._ entries are AppleDouble attribute carriers, not files (see above).
  extra=$(lsbom -s "$inner/Bom" | grep -v '/\._[^/]*$' | grep -vx -e '.' -e './Library' -e './Library/Fadenstack' \
    -e "./$STAGING" -e "./$STAGING/faden-agent-$ver" || true)
  [ -z "$extra" ] || die "$pkg installs more than the staged binary: $extra"
  rm -rf "$out"
}

cmd_notarize() {
  need APPLE_NOTARY_KEY_ID APPLE_NOTARY_ISSUER_ID APPLE_NOTARY_KEY_P8_BASE64
  [ -f "$PKG" ] || die "$PKG is missing: run pkg first."
  pkgutil --check-signature "$PKG" | grep -q 'Developer ID Installer' \
    || die "$PKG is not signed with a Developer ID Installer identity; notarizing it would fail."
  key=$NOTARY_KEY
  trap 'rm -f "$key"' EXIT
  (umask 077 && printf '%s' "$APPLE_NOTARY_KEY_P8_BASE64" | base64 -D > "$key")
  xcrun notarytool submit "$PKG" --key "$key" --key-id "$APPLE_NOTARY_KEY_ID" \
    --issuer "$APPLE_NOTARY_ISSUER_ID" --wait --timeout 30m --output-format json > "$SRC/dist/notary.json"
  status=$(sed -n 's/.*"status" *: *"\([^"]*\)".*/\1/p' "$SRC/dist/notary.json")
  if [ "$status" != Accepted ]; then
    id=$(sed -n 's/.*"id" *: *"\([^"]*\)".*/\1/p' "$SRC/dist/notary.json")
    [ -z "$id" ] || xcrun notarytool log "$id" --key "$key" --key-id "$APPLE_NOTARY_KEY_ID" \
      --issuer "$APPLE_NOTARY_ISSUER_ID" >&2 || true
    die "notarization of $PKG: ${status:-no status}"
  fi
  xcrun stapler staple "$PKG"
  xcrun stapler validate "$PKG"
  spctl --assess --type install --verbose=2 "$PKG"
  # The digest the pin names is the final bytes', after stapling.
  shasum -a 256 "$PKG" | awk '{print $1}' > "$PKG.sha256"
  echo "notarized: $PKG"
}

case "${1:-}" in
  secrets) cmd_secrets ;;
  keychain) cmd_keychain ;;
  cleanup|keychain-delete) cmd_cleanup ;;
  verify) shift; cmd_verify "$@" ;;
  pkg) shift; cmd_pkg "$@" ;;
  notarize) cmd_notarize ;;
  -h|--help|"") sed -n "2,$(($(grep -n '^set -eu' "$0" | head -1 | cut -d: -f1) - 1))p" "$0" ;;
  *) die "unknown command $1 (secrets, keychain, verify, pkg, notarize, cleanup)" ;;
esac
