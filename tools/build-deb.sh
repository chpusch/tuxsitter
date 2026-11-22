#!/bin/sh
# Build build/tuxsitter_<version>_all.deb  (needs: python3, dpkg-deb)
set -e
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
VERSION=$(python3 -c "import sys; sys.path.insert(0, 'src/tuxsitter'); import common; print(common.VERSION)")
PKG="$ROOT/build/pkg"
rm -rf "$PKG"
mkdir -p "$PKG/DEBIAN" "$PKG/usr/lib/tuxsitter" "$PKG/usr/bin" \
         "$PKG/usr/lib/systemd/system" "$PKG/usr/share/applications" \
         "$PKG/etc/xdg/autostart" "$PKG/usr/share/polkit-1/actions" \
         "$PKG/usr/share/icons/hicolor/scalable/apps" "$PKG/usr/share/doc/tuxsitter"

install -m 644 src/tuxsitter/admin.py src/tuxsitter/browserpolicy.py src/tuxsitter/common.py src/tuxsitter/i18n.py src/tuxsitter/dnsfilter.py src/tuxsitter/updates.py "$PKG/usr/lib/tuxsitter/"
install -m 755 src/tuxsitter/check_providers.py "$PKG/usr/lib/tuxsitter/"
install -m 755 src/tuxsitter/daemon.py src/tuxsitter/dnsproxy.py src/tuxsitter/gui.py src/tuxsitter/tray.py "$PKG/usr/lib/tuxsitter/"
install -m 755 data/tuxsitter.sh "$PKG/usr/bin/tuxsitter"
install -m 644 data/tuxsitter.service data/tuxsitter-dns.service "$PKG/usr/lib/systemd/system/"
install -m 644 data/tuxsitter.desktop "$PKG/usr/share/applications/"
install -m 644 data/tuxsitter-indicator.desktop "$PKG/etc/xdg/autostart/"
install -m 644 data/io.github.chpusch.tuxsitter.policy "$PKG/usr/share/polkit-1/actions/"
install -m 644 data/icons/tuxsitter.svg data/icons/tuxsitter-symbolic.svg "$PKG/usr/share/icons/hicolor/scalable/apps/"
install -m 644 README.md CHANGELOG.md "$PKG/usr/share/doc/tuxsitter/"
{ echo "Tuxsitter - https://github.com/chpusch/tuxsitter"; echo "License: GPL-3.0-or-later"; echo; cat LICENSE; } \
  > "$PKG/usr/share/doc/tuxsitter/copyright"
python3 tools/build-mo.py "$PKG/usr/share/locale" >/dev/null

sed "s/@VERSION@/$VERSION/" packaging/deb/control > "$PKG/DEBIAN/control"
install -m 755 packaging/deb/postinst packaging/deb/prerm packaging/deb/postrm "$PKG/DEBIAN/"

OUT="$ROOT/build/tuxsitter_${VERSION}_all.deb"
dpkg-deb --root-owner-group -Zxz --build "$PKG" "$OUT" >/dev/null
echo "$OUT"
