#!/bin/sh
# Starts the Tuxsitter parent app as the normal user.
# It runs without admin rights (works on X11 and Wayland); changes are made by the
# Tuxsitter service after polkit asked for the administrator password.
exec /usr/lib/tuxsitter/gui.py "$@"
