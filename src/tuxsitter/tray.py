#!/usr/bin/python3
"""Tuxsitter - panel indicator for the child account (remaining time, warnings, extra time)."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402
from i18n import _, N_  # noqa: E402

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib  # noqa: E402

try:
    gi.require_version("XApp", "1.0")
    from gi.repository import XApp  # Cinnamon / Linux Mint
except (ValueError, ImportError):
    XApp = None
AppIndicator = None
if XApp is None:
    for ns in ("AyatanaAppIndicator3", "AppIndicator3"):
        try:
            gi.require_version(ns, "0.1")
            AppIndicator = getattr(__import__("gi.repository", fromlist=[ns]), ns)
            break
        except (ValueError, ImportError):
            pass
try:
    gi.require_version("Notify", "0.7")
    from gi.repository import Notify
    Notify.init("Tuxsitter")
except (ValueError, ImportError):
    Notify = None

WARN_AT = (15 * 60, 5 * 60, 60)
REASONS = {
    "limit": N_("Your screen time for today is used up."),
    "window": N_("This is not screen time right now."),
    "locked": N_("The computer is locked for today."),
}


def reason_text(reason, default=""):
    return _(REASONS[reason]) if reason in REASONS else default

CSS = b"""
.ks-count { font-size: 30pt; font-weight: bold; }
.ks-title { font-size: 15pt; font-weight: bold; }
"""


def notify(title, body, urgent=False):
    if Notify is None:
        return
    try:
        n = Notify.Notification.new(title, body, "tuxsitter")
        if urgent:
            n.set_urgency(Notify.Urgency.CRITICAL)
        n.show()
    except GLib.Error:
        pass


def lbl(text, css=None, wrap=True):
    l = Gtk.Label(label=text, xalign=0.5, justify=Gtk.Justification.CENTER)
    l.set_line_wrap(wrap)
    l.set_max_width_chars(46)
    if css:
        l.get_style_context().add_class(css)
    return l


class ExtraTimeBox(Gtk.Box):
    """Enter the parent PIN -> extra time."""

    def __init__(self, on_done):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.on_done = on_done
        h = Gtk.Box(spacing=6, homogeneous=True)
        self.radios = {}
        grp = None
        for m in (15, 30, 60):
            rb = Gtk.RadioButton.new_with_label_from_widget(grp, "+%s" % C.fmt_duration(m * 60))
            rb.set_mode(False)
            grp = grp or rb
            self.radios[m] = rb
            h.pack_start(rb, True, True, 0)
        self.radios[30].set_active(True)
        self.pin = Gtk.Entry(placeholder_text=_("Parent PIN"), visibility=False,
                             input_purpose=Gtk.InputPurpose.PIN, max_length=8, xalign=0.5)
        self.pin.connect("activate", self.submit)
        btn = Gtk.Button(label=_("Unlock"))
        btn.get_style_context().add_class("suggested-action")
        btn.connect("clicked", self.submit)
        self.msg = lbl("")
        self.pack_start(h, False, False, 0)
        self.pack_start(self.pin, False, False, 0)
        self.pack_start(btn, False, False, 0)
        self.pack_start(self.msg, False, False, 0)

    def submit(self, *_args):
        minutes = next(m for m, rb in self.radios.items() if rb.get_active())
        try:
            r = C.request({"cmd": "extra", "pin": self.pin.get_text(), "minutes": minutes})
        except (OSError, ValueError):
            r = None
        self.pin.set_text("")
        if r and r.get("ok"):
            self.msg.set_text("")
            notify(_("Extra time"), _("+{time} unlocked.").format(time=C.fmt_duration(minutes * 60)))
            self.on_done()
        else:
            self.msg.set_text(C.error_text(r))


class ExtraDialog(Gtk.Window):
    def __init__(self):
        super().__init__(title=_("More time"))
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_resizable(False)
        self.set_keep_above(True)
        self.set_icon_name("tuxsitter")
        b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=20)
        b.pack_start(lbl(_("More time"), "ks-title"), False, False, 0)
        b.pack_start(lbl(_("Ask your parents - they can unlock extra time here with their PIN.")),
                     False, False, 0)
        b.pack_start(ExtraTimeBox(self.destroy), False, False, 0)
        self.add(b)
        self.show_all()


class EndWindow(Gtk.Window):
    """Shown when time is up - with a countdown until logout."""

    def __init__(self, tray):
        super().__init__(title=_("Time is up"))
        self.tray = tray
        self.set_position(Gtk.WindowPosition.CENTER_ALWAYS)
        self.set_keep_above(True)
        self.set_deletable(False)
        self.set_resizable(False)
        self.set_icon_name("tuxsitter")
        self.stick()
        b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=24)
        self.reason = lbl("", "ks-title")
        self.count = lbl("", "ks-count")
        self.info = lbl("")
        b.pack_start(self.reason, False, False, 0)
        b.pack_start(self.count, False, False, 0)
        b.pack_start(self.info, False, False, 0)
        exp = Gtk.Expander(label=_("Parents: unlock more time"))
        exp.add(ExtraTimeBox(self.tray.refresh))
        b.pack_start(exp, False, False, 6)
        self.add(b)
        self.deadline = None

    def update(self, st):
        self.reason.set_text(reason_text(st.get("reason"), _("Your time is up.")))
        self.deadline = time.monotonic() + (st.get("ending_in") or 0)
        nxt = C.next_text(st)
        txt = _("Save your work now - you will be logged out shortly.")
        if nxt:
            txt += "\n" + _("Next screen time: {when}.").format(when=nxt)
        self.info.set_text(txt)
        self.tick()

    def tick(self):
        if self.deadline is None:
            return
        left = max(0, int(self.deadline - time.monotonic()))
        self.count.set_text("0:%02d" % left if left < 60 else "%d:%02d" % divmod(left, 60))


class Tray:
    def __init__(self):
        self.status = {}
        self.warned = set()
        self.warn_day = None
        self.end_win = None
        self.icon = None
        self.menu = Gtk.Menu()
        self.m_time = Gtk.MenuItem(label="…")
        self.m_time.set_sensitive(False)
        self.m_window = Gtk.MenuItem(label="")
        self.m_window.set_sensitive(False)
        m_extra = Gtk.MenuItem(label=_("Ask for more time…"))
        m_extra.connect("activate", lambda *_args: ExtraDialog())
        for it in (self.m_time, self.m_window, Gtk.SeparatorMenuItem(), m_extra):
            self.menu.append(it)
        self.menu.show_all()
        self.refresh()
        GLib.timeout_add_seconds(5, self.refresh)
        GLib.timeout_add(1000, self.tick)

    # ---- panel icon ------------------------------------------------------
    def ensure_icon(self):
        if self.icon is not None:
            return
        if XApp is not None:
            self.icon = XApp.StatusIcon()
            self.icon.set_icon_name("tuxsitter-symbolic")
            self.icon.set_primary_menu(self.menu)
            self.icon.set_secondary_menu(self.menu)
            self.kind = "xapp"
        elif AppIndicator is not None:
            self.icon = AppIndicator.Indicator.new("tuxsitter", "tuxsitter-symbolic",
                                                   AppIndicator.IndicatorCategory.APPLICATION_STATUS)
            self.icon.set_status(AppIndicator.IndicatorStatus.ACTIVE)
            self.icon.set_menu(self.menu)
            self.kind = "appind"
        else:
            self.icon = Gtk.StatusIcon.new_from_icon_name("tuxsitter")
            self.icon.connect("popup-menu", lambda i, b, t: self.menu.popup(None, None, None, None, b, t))
            self.icon.connect("activate", lambda *_args: self.menu.popup(None, None, None, None, 0,
                                                                     Gtk.get_current_event_time()))
            self.kind = "gtk"

    def set_label(self, text, tooltip):
        self.ensure_icon()
        if self.kind == "xapp":
            self.icon.set_label(text)
            self.icon.set_tooltip_text(tooltip)
            self.icon.set_visible(True)
        elif self.kind == "appind":
            self.icon.set_label(text, "0:00")
        else:
            self.icon.set_tooltip_text(tooltip)

    def hide(self):
        if self.icon is None:
            return
        if self.kind == "xapp":
            self.icon.set_visible(False)
        elif self.kind == "appind":
            self.icon.set_status(AppIndicator.IndicatorStatus.PASSIVE)
        else:
            self.icon.set_visible(False)

    # ---- Status -------------------------------------------------------------
    def refresh(self):
        try:
            st = C.request({"cmd": "status"}, timeout=3)
        except (OSError, ValueError):
            st = None
        if not st or not st.get("managed"):
            self.status = {}
            self.hide()
            self.close_end()
            return True
        if self.icon is not None and self.kind == "appind":
            self.icon.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self.status = st
        rem = st.get("remaining")
        if rem is None:
            short, longtxt = "∞", _("Unlimited today")
        elif rem <= 0:
            short, longtxt = "0:00", reason_text(st.get("reason"), _("No time left"))
        else:
            short, longtxt = C.fmt_duration(rem, short=True), _("{time} left today").format(time=C.fmt_duration(rem))
        self.m_time.set_label(longtxt)
        win = st.get("window")
        self.m_window.set_label(_("Allowed from {start} to {end}").format(start=win[0], end=win[1]) if win else _("No time-of-day limit"))
        self.set_label(short, longtxt)
        self.warnings(rem)
        if st.get("ending_in") is not None:
            if self.end_win is None:
                self.end_win = EndWindow(self)
                self.end_win.show_all()
                notify(_("Time is up"), reason_text(st.get("reason")), urgent=True)
            self.end_win.update(st)
            self.end_win.present()
        else:
            self.close_end()
        return True

    def close_end(self):
        if self.end_win is not None:
            self.end_win.destroy()
            self.end_win = None

    def warnings(self, rem):
        day = time.strftime("%Y-%m-%d")
        if day != self.warn_day:
            self.warn_day, self.warned = day, set()
        if rem is None:
            return
        for w in WARN_AT:
            if rem <= w and w not in self.warned:
                # do not replay all older warnings at start-up
                self.warned.update(x for x in WARN_AT if rem <= x)
                if rem > 0:
                    notify(_("{time} left").format(time=C.fmt_duration(rem)),
                           _("Your screen time is almost over. Save your things in time."),
                           urgent=(w <= 60))
                break
        # extra time granted -> re-arm the warnings
        self.warned = {w for w in self.warned if rem <= w}

    def tick(self):
        if self.end_win is not None:
            self.end_win.tick()
        return True


def main():
    if os.geteuid() == 0:
        return
    from gi.repository import Gdk
    prov = Gtk.CssProvider()
    prov.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), prov,
                                             Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    # only one instance per session
    import socket
    global _single
    _single = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        _single.bind("\0tuxsitter-tray-%d" % os.getuid())
    except OSError:
        return
    Tray()
    Gtk.main()


if __name__ == "__main__":
    main()
