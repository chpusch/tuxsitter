#!/bin/sh
# Starts the Tuxsitter parent app as root (asks for the admin password)
# and passes on the desktop theme and language, which pkexec would drop.
get() { gsettings get "$1" "$2" 2>/dev/null | tr -d "'"; }
THEME=$(get org.cinnamon.desktop.interface gtk-theme); [ -n "$THEME" ] || THEME=$(get org.gnome.desktop.interface gtk-theme)
ICONS=$(get org.cinnamon.desktop.interface icon-theme); [ -n "$ICONS" ] || ICONS=$(get org.gnome.desktop.interface icon-theme)
LNG=${LANGUAGE:-${LC_ALL:-${LC_MESSAGES:-$LANG}}}
if [ "$(id -u)" = 0 ]; then
  exec /usr/lib/tuxsitter/gui.py --theme "$THEME" --icons "$ICONS" --lang "$LNG"
fi
exec pkexec /usr/lib/tuxsitter/gui.py --theme "$THEME" --icons "$ICONS" --lang "$LNG"
