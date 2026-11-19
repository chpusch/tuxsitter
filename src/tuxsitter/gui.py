#!/usr/bin/python3
"""Tuxsitter - parent app (runs as root via pkexec)."""
import grp
import os
import pwd
import re
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import i18n  # noqa: E402
import common as C  # noqa: E402
import updates  # noqa: E402
from i18n import _  # noqa: E402

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Gdk, Pango  # noqa: E402

TIMES = [C.min_to_hm(m) for m in range(0, 24 * 60 + 1, 15)]
WEEKDAYS = [i18n.N_("Mon"), i18n.N_("Tue"), i18n.N_("Wed"), i18n.N_("Thu"), i18n.N_("Fri"), i18n.N_("Sat"), i18n.N_("Sun")]

CSS = b"""
.ks-big { font-size: 26pt; font-weight: bold; }
.ks-name { font-size: 16pt; font-weight: bold; }
.ks-section { font-weight: bold; margin-top: 12px; }
.ks-small { font-size: 9pt; }
.ks-ok { color: #2e9d4f; }
.ks-warn { color: #c4651a; }
.ks-err { color: #c0362c; }
list.ks-boxed { border: 1px solid alpha(currentColor, 0.15); border-radius: 8px; }
list.ks-boxed row { padding: 10px 14px; }
list.ks-boxed row:not(:last-child) { border-bottom: 1px solid alpha(currentColor, 0.08); }
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def label(text, css=None, xalign=0.0, wrap=False, markup=False):
    lb = Gtk.Label()
    if markup:
        lb.set_markup(text)
    else:
        lb.set_text(text)
    lb.set_xalign(xalign)
    if wrap:
        lb.set_line_wrap(True)
        lb.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        lb.set_max_width_chars(60)
    for c in (css or "").split():
        lb.get_style_context().add_class(c)
    return lb


def boxed_list():
    lb = Gtk.ListBox()
    lb.set_selection_mode(Gtk.SelectionMode.NONE)
    lb.get_style_context().add_class("ks-boxed")
    return lb


def setting_row(title, widget=None, subtitle=None):
    row = Gtk.ListBoxRow()
    row.set_activatable(False)
    h = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    v.pack_start(label(title), False, False, 0)
    sub = None
    if subtitle is not None:
        sub = label(subtitle, "dim-label ks-small", wrap=True)
        v.pack_start(sub, False, False, 0)
    h.pack_start(v, True, True, 0)
    if widget is not None:
        widget.set_valign(Gtk.Align.CENTER)
        h.pack_end(widget, False, False, 0)
    row.add(h)
    row.sub = sub
    return row


def real_name(user):
    try:
        g = pwd.getpwnam(user).pw_gecos.split(",")[0].strip()
        return g or user
    except KeyError:
        return user


def invoking_user():
    uid = os.environ.get("PKEXEC_UID") or os.environ.get("SUDO_UID")
    try:
        return pwd.getpwuid(int(uid)).pw_name if uid else None
    except (KeyError, ValueError):
        return None


def candidate_users():
    me = invoking_user()
    out = []
    for p in pwd.getpwall():
        if 1000 <= p.pw_uid < 60000 and not p.pw_shell.endswith(("nologin", "false")) and p.pw_name != me:
            out.append(p.pw_name)
    return sorted(out)


def admin_groups_of(user):
    res = []
    for g in ("sudo", "admin", "wheel"):
        try:
            if user in grp.getgrnam(g).gr_mem:
                res.append(g)
        except KeyError:
            pass
    return res


def username_from(name):
    s = name.strip().lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss"), ("é", "e"), ("è", "e"), ("ê", "e"),
                 ("à", "a"), ("á", "a"), ("ç", "c"), ("ñ", "n"), ("ó", "o"), ("í", "i"), ("ú", "u")):
        s = s.replace(a, b)
    s = re.sub(r"[^a-z0-9]", "", s)
    if s and s[0].isdigit():
        s = "k" + s
    return s[:24]


def ask(parent, title, text, ok=None, destructive=False):
    d = Gtk.MessageDialog(transient_for=parent, modal=True, message_type=Gtk.MessageType.QUESTION,
                          buttons=Gtk.ButtonsType.NONE, text=title)
    d.format_secondary_text(text)
    d.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
    b = d.add_button(ok or _("OK"), Gtk.ResponseType.OK)
    if destructive:
        b.get_style_context().add_class("destructive-action")
    else:
        b.get_style_context().add_class("suggested-action")
    r = d.run()
    d.destroy()
    return r == Gtk.ResponseType.OK


def info(parent, title, text, error=False):
    d = Gtk.MessageDialog(transient_for=parent, modal=True,
                          message_type=Gtk.MessageType.ERROR if error else Gtk.MessageType.INFO,
                          buttons=Gtk.ButtonsType.OK, text=title)
    d.format_secondary_text(text)
    d.run()
    d.destroy()


def open_url_as_user(parent, url):
    """Open a link in the parent's own browser - never start a browser as root."""
    user = invoking_user()
    try:
        uid = pwd.getpwnam(user).pw_uid if user else None
    except KeyError:
        uid = None
    if uid is not None:
        env = ["DISPLAY=" + os.environ.get("DISPLAY", ":0"),
               "XDG_RUNTIME_DIR=/run/user/%d" % uid,
               "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/%d/bus" % uid]
        if os.environ.get("XAUTHORITY"):
            env.append("XAUTHORITY=" + os.environ["XAUTHORITY"])
        try:
            subprocess.Popen(["runuser", "-u", user, "--", "env"] + env + ["xdg-open", url],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except OSError:
            pass
    Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(url, -1)
    info(parent, _("Link copied"), _("Open this address in your browser:") + "\n" + url)


def daemon_call(msg, timeout=5):
    try:
        return C.request(msg, timeout=timeout)
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Dialogs
# ---------------------------------------------------------------------------
class PinDialog(Gtk.Dialog):
    def __init__(self, parent, first=False):
        super().__init__(title=_("Parent PIN"), transient_for=parent, modal=True)
        self.set_default_size(380, -1)
        self.add_button(_("Later") if first else _("Cancel"), Gtk.ResponseType.CANCEL)
        self.ok = self.add_button(_("Save"), Gtk.ResponseType.OK)
        self.ok.get_style_context().add_class("suggested-action")
        self.ok.set_sensitive(False)
        box = self.get_content_area()
        box.set_spacing(10)
        box.set_border_width(18)
        intro = _("With the parent PIN you can grant extra time right on your child's computer - "
                  "without logging in. Your child should not know it.")
        if first:
            box.pack_start(label("<b>%s</b>" % GLib.markup_escape_text(_("Welcome to Tuxsitter!")), markup=True), False, False, 0)
        box.pack_start(label(intro, wrap=True), False, False, 0)
        self.e1 = Gtk.Entry(placeholder_text=_("New PIN (4-8 digits)"), visibility=False,
                            input_purpose=Gtk.InputPurpose.PIN, max_length=8)
        self.e2 = Gtk.Entry(placeholder_text=_("Repeat PIN"), visibility=False,
                            input_purpose=Gtk.InputPurpose.PIN, max_length=8, activates_default=True)
        self.hint = label("", "ks-small dim-label")
        for w in (self.e1, self.e2, self.hint):
            box.pack_start(w, False, False, 0)
        for e in (self.e1, self.e2):
            e.connect("changed", self.check)
        self.set_default_response(Gtk.ResponseType.OK)
        self.show_all()

    def check(self, *_args):
        a, b = self.e1.get_text(), self.e2.get_text()
        ok = a.isdigit() and 4 <= len(a) <= 8 and a == b
        if a and not (a.isdigit() and len(a) >= 4):
            self.hint.set_text(_("Please use 4 to 8 digits."))
        elif b and a != b:
            self.hint.set_text(_("The PINs do not match."))
        else:
            self.hint.set_text("")
        self.ok.set_sensitive(ok)

    def pin(self):
        return self.e1.get_text()


class AddChildDialog(Gtk.Dialog):
    def __init__(self, parent, existing):
        super().__init__(title=_("Add child"), transient_for=parent, modal=True)
        self.set_default_size(440, -1)
        self.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        self.ok = self.add_button(_("Add"), Gtk.ResponseType.OK)
        self.ok.get_style_context().add_class("suggested-action")
        box = self.get_content_area()
        box.set_spacing(12)
        box.set_border_width(18)

        users = [u for u in candidate_users() if u not in existing]
        self.radio_existing = Gtk.RadioButton.new_with_label(None, _("Use an existing account"))
        self.radio_new = Gtk.RadioButton.new_with_label_from_widget(self.radio_existing, _("Create a new account for my child"))

        self.combo = Gtk.ComboBoxText()
        for u in users:
            extra = "  (%s)" % _("administrator!") if admin_groups_of(u) else ""
            self.combo.append(u, "%s – %s%s" % (real_name(u), u, extra))
        if users:
            self.combo.set_active(0)

        grid = Gtk.Grid(column_spacing=10, row_spacing=8, margin_start=24)
        self.e_name = Gtk.Entry(placeholder_text=_("e.g. Emma"))
        self.e_user = Gtk.Entry(placeholder_text="emma")
        self.e_pw1 = Gtk.Entry(visibility=False, placeholder_text=_("Password"))
        self.e_pw2 = Gtk.Entry(visibility=False, placeholder_text=_("Repeat password"))
        for i, (t, w) in enumerate(((_("Name"), self.e_name), (_("Username"), self.e_user),
                                    (_("Password"), self.e_pw1), ("", self.e_pw2))):
            grid.attach(label(t), 0, i, 1, 1)
            w.set_hexpand(True)
            grid.attach(w, 1, i, 1, 1)
        self.hint = label("", "ks-small ks-warn", wrap=True)

        box.pack_start(self.radio_existing, False, False, 0)
        al = Gtk.Box(margin_start=24)
        al.pack_start(self.combo, True, True, 0)
        box.pack_start(al, False, False, 0)
        box.pack_start(self.radio_new, False, False, 0)
        box.pack_start(grid, False, False, 0)
        box.pack_start(self.hint, False, False, 0)

        self._user_edited = False
        self.e_name.connect("changed", self._name_changed)
        self.e_user.connect("changed", self._user_changed)
        for w in (self.radio_existing, self.radio_new):
            w.connect("toggled", self.update)
        for w in (self.e_pw1, self.e_pw2, self.combo):
            w.connect("changed", self.update)
        if not users:
            self.radio_new.set_active(True)
            self.radio_existing.set_sensitive(False)
            self.combo.set_sensitive(False)
        self.update()
        self.show_all()

    def _name_changed(self, *_args):
        if not self._user_edited:
            self._setting = True
            self.e_user.set_text(username_from(self.e_name.get_text()))
            self._setting = False
        self.update()

    def _user_changed(self, *_args):
        if not getattr(self, "_setting", False):
            self._user_edited = True
        self.update()

    def update(self, *_args):
        new = self.radio_new.get_active()
        self.combo.set_sensitive(not new)
        for w in (self.e_name, self.e_user, self.e_pw1, self.e_pw2):
            w.set_sensitive(new)
        hint, ok = "", True
        if new:
            u = self.e_user.get_text()
            if not re.match(r"^[a-z][a-z0-9_-]{1,30}$", u):
                ok, hint = False, (_("Username: lowercase letters and digits only.") if u else "")
            elif _user_exists(u):
                ok, hint = False, _("This username already exists.")
            elif len(self.e_pw1.get_text()) < 4:
                ok = False
            elif self.e_pw1.get_text() != self.e_pw2.get_text():
                ok, hint = False, _("The passwords do not match.")
        else:
            u = self.combo.get_active_id()
            ok = bool(u)
            if u and admin_groups_of(u):
                hint = _("This account has administrator rights and could switch the protection off. "
                         "You can remove those rights right afterwards.")
        self.hint.set_text(hint)
        self.ok.set_sensitive(ok)

    def result(self):
        if self.radio_new.get_active():
            return ("new", self.e_user.get_text(), self.e_name.get_text().strip(), self.e_pw1.get_text())
        return ("existing", self.combo.get_active_id(), None, None)


def _user_exists(u):
    try:
        pwd.getpwnam(u)
        return True
    except KeyError:
        return False


def create_account(user, name, password):
    if os.path.exists("/usr/sbin/adduser"):
        cmd = ["adduser", "--disabled-password", "--gecos", name or user, user]
    else:
        cmd = ["useradd", "-m", "-s", "/bin/bash", "-c", name or user, user]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or _("The account could not be created."))
    r = subprocess.run(["chpasswd"], input="%s:%s\n" % (user, password), capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or _("The password could not be set."))


# ---------------------------------------------------------------------------
# History chart
# ---------------------------------------------------------------------------
class HistoryChart(Gtk.DrawingArea):
    def __init__(self):
        super().__init__()
        self.data = []      # [(date, seconds)]
        self.limit = None   # Sekunden
        self.set_size_request(-1, 220)
        self.connect("draw", self.on_draw)

    def set_data(self, data):
        self.data = data[-14:]
        self.queue_draw()

    def on_draw(self, w, cr):
        alloc = self.get_allocation()
        W, H = alloc.width, alloc.height
        ctx = self.get_style_context()
        fg = ctx.get_color(Gtk.StateFlags.NORMAL)
        accent = Gdk.RGBA()
        accent.parse("#3d8fd6")
        n = len(self.data) or 1
        top, bottom = 22, 38
        ch = H - top - bottom
        maxv = max([v for _day, v in self.data] + [3600])
        gap = 8
        bw = max(6, (W - gap * (n + 1)) / n)
        cr.select_font_face("Sans")
        cr.set_font_size(10)
        tday = time.strftime("%Y-%m-%d")
        for i, (d, v) in enumerate(self.data):
            x = gap + i * (bw + gap)
            h = ch * v / maxv
            alpha = 1.0 if d == tday else 0.6
            cr.set_source_rgba(accent.red, accent.green, accent.blue, alpha)
            r = min(4, bw / 2)
            y = top + ch - h
            if h > 0.5:
                cr.new_sub_path()
                cr.arc(x + bw - r, y + r, r, -1.5708, 0)
                cr.line_to(x + bw, top + ch)
                cr.line_to(x, top + ch)
                cr.arc(x + r, y + r, r, 3.1416, 4.7124)
                cr.close_path()
                cr.fill()
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.85)
            if v >= 60:
                txt = C.fmt_duration(v, short=True)
                ext = cr.text_extents(txt)
                cr.move_to(x + bw / 2 - ext.width / 2, y - 5)
                cr.show_text(txt)
            try:
                wd = _(WEEKDAYS[time.strptime(d, "%Y-%m-%d").tm_wday])
            except ValueError:
                wd = "?"
            for j, txt in enumerate((wd, d[8:10] + "." + d[5:7] + ".")):
                cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.85 if j == 0 else 0.5)
                ext = cr.text_extents(txt)
                cr.move_to(x + bw / 2 - ext.width / 2, top + ch + 15 + j * 13)
                cr.show_text(txt)
        cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.2)
        cr.set_line_width(1)
        cr.move_to(0, top + ch + 0.5)
        cr.line_to(W, top + ch + 0.5)
        cr.stroke()
        return False


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------
class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=C.APP_NAME)
        self.set_default_size(920, 640)
        self.set_icon_name("tuxsitter")
        self.cfg = C.load_config()
        self.status = {}
        self.cur = None
        self._save_id = None
        self._loading = False
        self.side_rows = {}

        hb = Gtk.HeaderBar(show_close_button=True, title=C.APP_NAME)
        self.set_titlebar(hb)
        self.hb = hb
        menu_btn = Gtk.MenuButton()
        menu_btn.set_image(Gtk.Image.new_from_icon_name("open-menu-symbolic", Gtk.IconSize.BUTTON))
        menu = Gtk.Menu()
        for text, cb in ((_("Change parent PIN…"), self.on_change_pin),
                         (_("About Tuxsitter"), self.on_about)):
            it = Gtk.MenuItem(label=text)
            it.connect("activate", cb)
            menu.append(it)
        menu.insert(Gtk.SeparatorMenuItem(), 1)
        upd = Gtk.CheckMenuItem(label=_("Check for updates"))
        upd.set_active(bool(self.cfg.get("update_check", True)))
        upd.connect("toggled", self.on_toggle_updates)
        menu.insert(upd, 1)
        menu.show_all()
        menu_btn.set_popup(menu)
        hb.pack_end(menu_btn)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(outer)
        self.daemon_bar = Gtk.InfoBar(message_type=Gtk.MessageType.ERROR)
        self.daemon_bar.get_content_area().add(label(_("The Tuxsitter service is not running - protection is currently off.")))
        self.daemon_bar.add_button(_("Start service"), 1)
        self.daemon_bar.connect("response", self.on_start_daemon)
        self.daemon_bar.set_no_show_all(True)
        outer.pack_start(self.daemon_bar, False, False, 0)
        self.update_bar = Gtk.InfoBar(message_type=Gtk.MessageType.INFO, show_close_button=True)
        self.update_label = label("")
        self.update_bar.get_content_area().add(self.update_label)
        self.update_bar.add_button(_("Download"), 1)
        self.update_bar.connect("response", self.on_update_response)
        self.update_bar.set_no_show_all(True)
        self.update_url = None
        outer.pack_start(self.update_bar, False, False, 0)

        main = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        outer.pack_start(main, True, True, 0)

        # Sidebar
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        side.set_size_request(240, -1)
        self.side_list = Gtk.ListBox()
        self.side_list.connect("row-selected", self.on_select)
        sw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        sw.add(self.side_list)
        side.pack_start(label("  " + _("Children"), "ks-section dim-label"), False, False, 6)
        side.pack_start(sw, True, True, 0)
        add = Gtk.Button(label=_("Add child"))
        add.set_image(Gtk.Image.new_from_icon_name("list-add-symbolic", Gtk.IconSize.BUTTON))
        add.set_always_show_image(True)
        add.connect("clicked", self.on_add_child)
        add.set_margin_start(10)
        add.set_margin_end(10)
        add.set_margin_bottom(10)
        add.set_margin_top(10)
        side.pack_end(add, False, False, 0)
        main.pack_start(side, False, False, 0)
        main.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 0)

        self.content = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        main.pack_start(self.content, True, True, 0)
        self.content.add_named(self.build_empty(), "empty")

        self.fill_sidebar()
        self.show_all()
        self.refresh_status()
        GLib.timeout_add_seconds(5, self.refresh_status)
        if not self.cfg.get("pin"):
            GLib.idle_add(self.first_pin)
        self.start_update_check()

    # ---- Empty state -------------------------------------------------------
    def build_empty(self):
        b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, valign=Gtk.Align.CENTER,
                    halign=Gtk.Align.CENTER)
        img = Gtk.Image.new_from_icon_name("tuxsitter", Gtk.IconSize.DIALOG)
        img.set_pixel_size(96)
        b.pack_start(img, False, False, 0)
        b.pack_start(label(_("No child set up yet"), "ks-name", xalign=0.5), False, False, 0)
        b.pack_start(label(_("Choose your child's user account or create a new one.\n"
                             "Then set screen time, allowed hours and the internet filter."),
                           "dim-label", xalign=0.5), False, False, 0)
        btn = Gtk.Button(label=_("Add child"), halign=Gtk.Align.CENTER)
        btn.get_style_context().add_class("suggested-action")
        btn.connect("clicked", self.on_add_child)
        b.pack_start(btn, False, False, 6)
        return b

    # ---- Seitenleiste ------------------------------------------------------
    def fill_sidebar(self, select=None):
        for r in self.side_list.get_children():
            self.side_list.remove(r)
        self.side_rows = {}
        for user in sorted(self.cfg["children"], key=lambda u: real_name(u).lower()):
            row = Gtk.ListBoxRow()
            row.user = user
            h = Gtk.Box(spacing=10, margin=8)
            img = Gtk.Image.new_from_icon_name("avatar-default-symbolic", Gtk.IconSize.DND)
            v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            v.pack_start(label("<b>%s</b>" % GLib.markup_escape_text(real_name(user)), markup=True), False, False, 0)
            st = label("…", "dim-label ks-small")
            v.pack_start(st, False, False, 0)
            h.pack_start(img, False, False, 0)
            h.pack_start(v, True, True, 0)
            row.add(h)
            row.status_label = st
            self.side_list.add(row)
            self.side_rows[user] = row
        self.side_list.show_all()
        target = select or self.cur
        if target in self.side_rows:
            self.side_list.select_row(self.side_rows[target])
        elif self.side_rows:
            self.side_list.select_row(self.side_list.get_row_at_index(0))
        else:
            self.cur = None
            self.content.set_visible_child_name("empty")

    def on_select(self, lb, row):
        if row is None:
            return
        self.cur = row.user
        old = self.content.get_child_by_name("child")
        page = self.build_child_page(row.user)
        if old:
            self.content.remove(old)
        self.content.add_named(page, "child")
        page.show_all()
        self.content.set_visible_child_name("child")
        self.update_today()

    # ---- Child page --------------------------------------------------------
    def build_child_page(self, user):
        c = self.cfg["children"][user]
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        head = Gtk.Box(spacing=14, margin=18, margin_bottom=6)
        img = Gtk.Image.new_from_icon_name("avatar-default-symbolic", Gtk.IconSize.DIALOG)
        img.set_pixel_size(48)
        hv = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER)
        hv.pack_start(label(real_name(user), "ks-name"), False, False, 0)
        self.head_status = label("", "dim-label")
        hv.pack_start(self.head_status, False, False, 0)
        head.pack_start(img, False, False, 0)
        head.pack_start(hv, True, True, 0)
        sw_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER)
        active = Gtk.Switch(active=c.get("enabled", True), halign=Gtk.Align.END)
        active.connect("notify::active", lambda s, _pspec: self.set_val(c, "enabled", s.get_active()))
        sw_box.pack_start(active, False, False, 0)
        sw_box.pack_start(label(_("Protection on"), "ks-small dim-label", xalign=1), False, False, 0)
        head.pack_end(sw_box, False, False, 0)
        page.pack_start(head, False, False, 0)

        self.admin_bar = Gtk.InfoBar(message_type=Gtk.MessageType.WARNING)
        self.admin_bar.get_content_area().add(label(
            _("This account has administrator rights and could switch the protection off."), wrap=True))
        self.admin_bar.add_button(_("Remove rights"), 1)
        self.admin_bar.connect("response", lambda *_args: self.on_remove_admin(user))
        self.admin_bar.set_no_show_all(True)
        page.pack_start(self.admin_bar, False, False, 0)

        stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        switcher = Gtk.StackSwitcher(stack=stack, halign=Gtk.Align.CENTER, margin_bottom=6)
        page.pack_start(switcher, False, False, 0)
        page.pack_start(Gtk.Separator(), False, False, 0)
        for name, title, w in (("today", _("Today"), self.page_today(user)),
                               ("times", _("Schedule"), self.page_times(user, c)),
                               ("net", _("Internet"), self.page_net(user, c)),
                               ("hist", _("History"), self.page_history(user))):
            sc = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
            clamp = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin=24, spacing=8)
            clamp.set_size_request(520, -1)
            clamp.set_halign(Gtk.Align.CENTER)
            clamp.pack_start(w, False, False, 0)
            sc.add(clamp)
            stack.add_titled(sc, name, title)
        page.pack_start(stack, True, True, 0)
        return page

    # ---- Today ------------------------------------------------------------
    def page_today(self, user):
        b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.t_big = label("–", "ks-big", xalign=0.5)
        self.t_sub = label("", "dim-label", xalign=0.5)
        self.t_bar = Gtk.LevelBar(min_value=0, max_value=1)
        self.t_bar.set_size_request(-1, 10)
        self.t_note = label("", xalign=0.5)
        b.pack_start(self.t_big, False, False, 6)
        b.pack_start(self.t_note, False, False, 0)
        b.pack_start(self.t_sub, False, False, 0)
        b.pack_start(self.t_bar, False, False, 8)

        b.pack_start(label(_("More time for today"), "ks-section"), False, False, 0)
        hb = Gtk.Box(spacing=8, homogeneous=True)
        for m in (15, 30, 60):
            btn = Gtk.Button(label="+ %s" % C.fmt_duration(m * 60))
            btn.connect("clicked", lambda _b, m=m: self.cmd({"cmd": "grant", "user": user, "minutes": m}))
            hb.pack_start(btn, True, True, 0)
        b.pack_start(hb, False, False, 0)
        b.pack_start(label(_("Also works outside the allowed hours. Your child can also unlock extra "
                             "time on their computer with your parent PIN."), "dim-label ks-small", wrap=True),
                     False, False, 0)

        b.pack_start(label(_("Lock"), "ks-section"), False, False, 0)
        lst = boxed_list()
        self.lock_switch = Gtk.Switch()
        self._lock_handler = self.lock_switch.connect(
            "notify::active", lambda s, _pspec: self.cmd({"cmd": "lock", "user": user, "on": s.get_active()}))
        lst.add(setting_row(_("Lock for today"), self.lock_switch,
                            _("Logs your child out after one minute. Tomorrow everything is back to normal.")))
        reset = Gtk.Button(label=_("Reset"))
        reset.connect("clicked", lambda *a: ask(self, _("Reset today?"),
                                                _("Today's used time and extra time are set back to zero."),
                                                _("Reset")) and self.cmd({"cmd": "reset_today", "user": user}))
        lst.add(setting_row(_("Reset today's time"), reset))
        b.pack_start(lst, False, False, 0)

        rm = Gtk.Button(label=_("Remove child from Tuxsitter"), halign=Gtk.Align.START, margin_top=24)
        rm.get_style_context().add_class("destructive-action")
        rm.connect("clicked", lambda *_args: self.on_remove_child(user))
        b.pack_start(rm, False, False, 0)
        return b

    def update_today(self):
        user = self.cur
        st = self.status.get(user) if user else None
        if not user or not hasattr(self, "t_big"):
            return
        if not st or not st.get("managed"):
            self.t_big.set_text("–")
            self.t_note.set_text("")
            self.t_sub.set_text(_("Protection is paused.") if not self.cfg["children"][user].get("enabled", True)
                                else _("No data from the service."))
            self.head_status.set_text("")
            self.admin_bar.hide()
            return
        rem, used, limit = st.get("remaining"), st.get("used", 0), st.get("limit")
        if st.get("reason") == "locked":
            big = _("Locked today")
        elif rem is None:
            big = _("Unlimited")
        elif rem <= 0:
            big = _("Time used up") if st.get("reason") == "limit" else _("Not allowed right now")
        else:
            big = _("{time} left").format(time=C.fmt_duration(rem))
        self.t_big.set_text(big)
        if st.get("limited_by") == "window" and st.get("until"):
            note = _("Allowed time ends at {time}.").format(time=st["until"])
        elif st.get("limited_by") == "limit":
            note = _("Daily limit - only counts while your child is logged in.")
        else:
            note = ""
        self.t_note.set_text(note)
        if limit is not None:
            sub = _("Used today: {used} of {limit}").format(used=C.fmt_duration(used), limit=C.fmt_duration(limit))
        else:
            sub = _("Used today: {used}").format(used=C.fmt_duration(used))
        if st.get("window"):
            sub += "   ·   " + _("allowed {start}-{end}").format(start=st["window"][0], end=st["window"][1])
        if st.get("holiday_mode"):
            sub += "   ·   " + _("holiday mode")
        self.t_sub.set_text(sub)
        self.t_bar.set_value(min(1.0, used / limit) if limit else 0)
        online = _("Logged in right now") if st.get("online") else _("Not logged in")
        if st.get("ending_in") is not None:
            online = _("Will be logged out in {s} s").format(s=st["ending_in"])
        self.head_status.set_text(online)
        self.lock_switch.handler_block(self._lock_handler)
        self.lock_switch.set_active(bool(st.get("locked_today")))
        self.lock_switch.handler_unblock(self._lock_handler)
        if st.get("is_admin"):
            self.admin_bar.show()
            self.admin_bar.show_all()
        else:
            self.admin_bar.hide()
        if hasattr(self, "chart"):
            self.chart.set_data(st.get("history", []))
            week = [v for _day, v in st.get("history", [])[-7:]]
            if week:
                self.hist_sum.set_text(_("Average of the last 7 days: {time} per day").format(
                    time=C.fmt_duration(sum(week) / len(week))))

    # ---- Schedule -----------------------------------------------------------
    def page_times(self, user, c):
        b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        lst = boxed_list()
        hol = Gtk.Switch(active=bool(c.get("holiday_mode")))
        hol.connect("notify::active", lambda s, _pspec: self.set_val(c, "holiday_mode", s.get_active()))
        lst.add(setting_row(_("Holiday mode"), hol, _("During school holidays the weekend rules apply every day.")))
        b.pack_start(lst, False, False, 0)
        for key, title in (("school", _("School days (Monday to Friday)")), ("weekend", _("Weekend (Saturday and Sunday)"))):
            b.pack_start(label(title, "ks-section"), False, False, 0)
            b.pack_start(self.day_editor(c[key]), False, False, 0)
        return b

    def day_editor(self, d):
        lst = boxed_list()
        # daily limit
        sw = Gtk.Switch(active=d["limit_enabled"])
        r1 = setting_row(_("Limit screen time per day"), sw)
        lst.add(r1)
        val = label(C.fmt_duration(d["limit_min"] * 60), xalign=1)
        val.set_width_chars(12)
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 480, 15)
        scale.set_draw_value(False)
        scale.set_value(d["limit_min"])
        for m in (60, 120, 180, 240, 360):
            scale.add_mark(m, Gtk.PositionType.BOTTOM, _("{h} h").format(h=m // 60))
        r2 = Gtk.ListBoxRow(activatable=False)
        h = Gtk.Box(spacing=12)
        h.pack_start(scale, True, True, 0)
        h.pack_start(val, False, False, 0)
        r2.add(h)
        lst.add(r2)

        def on_scale(s):
            v = int(round(s.get_value() / 15.0) * 15)
            val.set_text(C.fmt_duration(v * 60) if v else _("no time"))
            self.set_val(d, "limit_min", v)
        scale.connect("value-changed", on_scale)

        def on_limit(s, _pspec):
            scale.set_sensitive(s.get_active())
            self.set_val(d, "limit_enabled", s.get_active())
        sw.connect("notify::active", on_limit)
        scale.set_sensitive(d["limit_enabled"])

        # allowed hours
        sw2 = Gtk.Switch(active=d["window_enabled"])
        lst.add(setting_row(_("Only at certain times"), sw2))
        r3 = Gtk.ListBoxRow(activatable=False)
        h3 = Gtk.Box(spacing=8)
        c_from, c_to = Gtk.ComboBoxText(), Gtk.ComboBoxText()
        for t in TIMES:
            c_from.append(t, t)
            c_to.append(t, t)
        c_from.set_active_id(d["start"] if d["start"] in TIMES else "07:00")
        c_to.set_active_id(d["end"] if d["end"] in TIMES else "20:00")
        warn = label("", "ks-small ks-warn")
        h3.pack_start(label(_("from")), False, False, 0)
        h3.pack_start(c_from, False, False, 0)
        h3.pack_start(label(_("to")), False, False, 0)
        h3.pack_start(c_to, False, False, 0)
        h3.pack_start(warn, True, True, 0)
        r3.add(h3)
        lst.add(r3)

        def on_time(*_args):
            s, e = c_from.get_active_id(), c_to.get_active_id()
            warn.set_text("" if C.hm_to_min(e) > C.hm_to_min(s) else _("End must be after start"))
            self.set_val(d, "start", s)
            self.set_val(d, "end", e)
        c_from.connect("changed", on_time)
        c_to.connect("changed", on_time)

        def on_window(s, _pspec):
            for w in (c_from, c_to):
                w.set_sensitive(s.get_active())
            self.set_val(d, "window_enabled", s.get_active())
        sw2.connect("notify::active", on_window)
        for w in (c_from, c_to):
            w.set_sensitive(d["window_enabled"])
        return lst

    # ---- Internet -----------------------------------------------------------
    def page_net(self, user, c):
        d = c["dns"]
        b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        lst = boxed_list()
        en = Gtk.Switch(active=d["enabled"])
        lst.add(setting_row(_("Internet filter on"), en,
                            _("Applies to this account only. Your own accounts stay unfiltered.")))
        b.pack_start(lst, False, False, 0)

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        b.pack_start(inner, False, False, 0)
        inner.pack_start(label(_("Filter provider"), "ks-section"), False, False, 0)
        plist = boxed_list()
        group = None
        self.nextdns_entry = Gtk.Entry(placeholder_text=_("Profile ID, e.g. a1b2c3"), width_chars=16)
        self.nextdns_entry.set_text(d.get("nextdns_id", ""))
        self.custom_entry = Gtk.Entry(placeholder_text=_("e.g. 192.168.1.2"), width_chars=16)
        self.custom_entry.set_text(d.get("custom", ""))
        extras = {"nextdns": self.nextdns_entry, "custom": self.custom_entry}
        for key in C.PROVIDER_ORDER:
            p = C.PROVIDERS[key]
            rb = Gtk.RadioButton.new_from_widget(group)
            group = group or rb
            row = Gtk.ListBoxRow(activatable=False)
            h = Gtk.Box(spacing=10)
            h.pack_start(rb, False, False, 0)
            v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            v.pack_start(label("<b>%s</b>" % GLib.markup_escape_text(_(p["name"])), markup=True), False, False, 0)
            v.pack_start(label(_(p["desc"]), "dim-label ks-small", wrap=True), False, False, 0)
            h.pack_start(v, True, True, 0)
            if key in extras:
                extras[key].set_valign(Gtk.Align.CENTER)
                h.pack_end(extras[key], False, False, 0)
                extras[key].set_sensitive(d["provider"] == key)
            row.add(h)
            plist.add(row)
            rb.set_active(d["provider"] == key)
            rb.connect("toggled", self.on_provider, d, key, extras)
        inner.pack_start(plist, False, False, 0)

        self.dns_hint = label("", "ks-small", wrap=True)
        test = Gtk.Button(label=_("Test connection"), halign=Gtk.Align.START)
        test.connect("clicked", lambda *_args: self.on_dns_test(d))
        th = Gtk.Box(spacing=12)
        th.pack_start(test, False, False, 0)
        th.pack_start(self.dns_hint, True, True, 0)
        inner.pack_start(th, False, False, 0)

        def on_entry(e, field):
            self.set_val(d, field, e.get_text().strip())
            self.validate_dns(d)
        self.nextdns_entry.connect("changed", on_entry, "nextdns_id")
        self.custom_entry.connect("changed", on_entry, "custom")

        inner.pack_start(label(_("Extra protection"), "ks-section"), False, False, 0)
        opt = boxed_list()
        ss = Gtk.Switch(active=d["safesearch"])
        ss.connect("notify::active", lambda s, _pspec: self.set_val(d, "safesearch", s.get_active()))
        opt.add(setting_row(_("Enforce SafeSearch"), ss, _("Google, Bing and DuckDuckGo only show family-friendly results.")))
        yt = Gtk.ComboBoxText()
        for k, t in (("off", _("No restriction")), ("moderate", _("Restricted")), ("strict", _("Strict"))):
            yt.append(k, t)
        yt.set_active_id(d.get("youtube", "moderate"))
        yt.connect("changed", lambda w: self.set_val(d, "youtube", w.get_active_id()))
        opt.add(setting_row("YouTube", yt, _("Restricted Mode hides videos that are not suitable for children.")))
        bp = Gtk.Switch(active=d["block_bypass"])
        bp.connect("notify::active", lambda s, _pspec: self.set_val(d, "block_bypass", s.get_active()))
        opt.add(setting_row(_("Block workarounds"), bp,
                            _("Blocks encrypted DNS (DoH/DoT) of other providers and the Tor download, "
                              "so the filter cannot be bypassed.")))
        inner.pack_start(opt, False, False, 0)

        def on_en(s, _pspec):
            inner.set_sensitive(s.get_active())
            self.set_val(d, "enabled", s.get_active())
        en.connect("notify::active", on_en)
        inner.set_sensitive(d["enabled"])
        self.validate_dns(d)
        return b

    def on_provider(self, rb, d, key, extras):
        if not rb.get_active():
            return
        for k, e in extras.items():
            e.set_sensitive(k == key)
        self.set_val(d, "provider", key)
        if key in extras:
            extras[key].grab_focus()
        self.validate_dns(d)

    def validate_dns(self, d):
        msg, css = "", "ks-warn"
        if d["provider"] == "nextdns" and not re.match(r"^[A-Za-z0-9]{4,20}$", d.get("nextdns_id", "")):
            msg = _("Please enter your NextDNS profile ID (my.nextdns.io → Setup). Without a valid ID your child has no internet.")
        elif d["provider"] == "custom":
            import ipaddress
            ips = [x.strip() for x in d.get("custom", "").replace(";", ",").split(",") if x.strip()]
            try:
                if not ips:
                    raise ValueError
                for ip in ips:
                    ipaddress.ip_address(ip)
            except ValueError:
                msg = _("Please enter a valid IP address. Without it your child has no internet.")
        self._set_hint(msg, css)

    def _set_hint(self, msg, css):
        ctx = self.dns_hint.get_style_context()
        for c in ("ks-warn", "ks-ok", "ks-err"):
            ctx.remove_class(c)
        ctx.add_class(css)
        self.dns_hint.set_text(msg)

    def on_dns_test(self, d):
        self._set_hint(_("Testing…"), "dim-label")
        cfg = dict(d)

        def run():
            r = daemon_call({"cmd": "dnstest", "dns": cfg}, timeout=12)
            if r and r.get("ok"):
                GLib.idle_add(self._set_hint, "✓ " + _("Filter answers ({ms} ms)").format(ms=r["ms"]), "ks-ok")
            else:
                GLib.idle_add(self._set_hint, "✗ " + C.error_text(r), "ks-err")
        threading.Thread(target=run, daemon=True).start()

    # ---- History ------------------------------------------------------------
    def page_history(self, user):
        b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        b.pack_start(label(_("Screen time of the last 14 days"), "ks-section"), False, False, 0)
        self.chart = HistoryChart()
        b.pack_start(self.chart, False, False, 0)
        self.hist_sum = label("", "dim-label")
        b.pack_start(self.hist_sum, False, False, 0)
        return b

    # ---- Saving & status -----------------------------------------------
    def set_val(self, d, key, value):
        if d.get(key) == value:
            return
        d[key] = value
        self.schedule_save()

    def schedule_save(self):
        if self._save_id:
            GLib.source_remove(self._save_id)
        self.hb.set_subtitle(_("Saving…"))
        self._save_id = GLib.timeout_add(600, self.do_save)

    def do_save(self):
        self._save_id = None
        try:
            C.save_config(self.cfg)
        except OSError as e:
            info(self, _("Saving failed"), str(e), error=True)
            return False
        daemon_call({"cmd": "reload"}, timeout=10)
        self.hb.set_subtitle(_("Saved") + " ✓")
        GLib.timeout_add_seconds(3, lambda: self.hb.set_subtitle(None) or False)
        self.refresh_status()
        return False

    def cmd(self, msg):
        r = daemon_call(msg)
        if r is None or not r.get("ok", True):
            info(self, _("Error"), C.error_text(r), error=True)
        self.refresh_status()

    def refresh_status(self):
        r = daemon_call({"cmd": "status_all"})
        if r is None:
            self.daemon_bar.show()
            self.daemon_bar.show_all()
            self.status = {}
        else:
            self.daemon_bar.hide()
            self.status = r
        for user, row in self.side_rows.items():
            st = self.status.get(user, {})
            if not self.cfg["children"][user].get("enabled", True):
                txt = _("Paused")
            elif not st.get("managed"):
                txt = ""
            elif st.get("reason") == "locked":
                txt = _("Locked today")
            elif st.get("remaining") is None:
                txt = _("Unlimited")
            elif st["remaining"] <= 0:
                txt = _("No time left")
            else:
                txt = _("{time} left").format(time=C.fmt_duration(st["remaining"]))
            if st.get("online"):
                txt = "● " + txt
            row.status_label.set_text(txt)
        self.update_today()
        return True

    def on_start_daemon(self, *_args):
        subprocess.run(["systemctl", "enable", "--now", "tuxsitter.service"])
        GLib.timeout_add(1500, lambda: self.refresh_status() and False)

    # ---- Actions -------------------------------------------------------
    def first_pin(self):
        d = PinDialog(self, first=True)
        if d.run() == Gtk.ResponseType.OK:
            self.cfg["pin"] = C.hash_pin(d.pin())
            self.do_save()
        d.destroy()
        if not self.cfg["children"]:
            self.on_add_child()
        return False

    def on_change_pin(self, *_args):
        d = PinDialog(self)
        if d.run() == Gtk.ResponseType.OK:
            self.cfg["pin"] = C.hash_pin(d.pin())
            self.do_save()
        d.destroy()

    def on_add_child(self, *_args):
        d = AddChildDialog(self, self.cfg["children"])
        resp = d.run()
        res = d.result()
        d.destroy()
        if resp != Gtk.ResponseType.OK:
            return
        kind, user, name, pw = res
        if kind == "new":
            try:
                create_account(user, name, pw)
            except RuntimeError as e:
                info(self, _("The account could not be created."), str(e), error=True)
                return
        self.cfg["children"][user] = C.new_child_config()
        self.do_save()
        self.fill_sidebar(select=user)
        if admin_groups_of(user):
            self.on_remove_admin(user)

    def on_remove_admin(self, user):
        if user == invoking_user():
            info(self, _("Not possible"), _("This is your own account."), error=True)
            return
        if not ask(self, _("Remove administrator rights?"),
                   _("{name} will no longer be able to install programs or switch off the protection. "
                     "Takes effect at the next login.").format(name=real_name(user)), _("Remove rights")):
            return
        for g in admin_groups_of(user):
            subprocess.run(["gpasswd", "-d", user, g], capture_output=True)
        self.refresh_status()

    def on_remove_child(self, user):
        if not ask(self, _("Remove {name}?").format(name=real_name(user)),
                   _("Time limits and the internet filter will no longer apply. The user account itself is kept."),
                   _("Remove"), destructive=True):
            return
        self.cfg["children"].pop(user, None)
        self.cur = None
        self.do_save()
        self.fill_sidebar()

    # ---- Update hint -------------------------------------------------------
    def start_update_check(self, force=False):
        if not self.cfg.get("update_check", True):
            return

        def run():
            res = updates.check(force=force)
            if res:
                GLib.idle_add(self.show_update, *res)
        threading.Thread(target=run, daemon=True).start()

    def show_update(self, version, url):
        self.update_url = url
        self.update_label.set_text(_("Tuxsitter {version} is available (installed: {installed}).").format(
            version=version, installed=C.VERSION))
        self.update_bar.show()
        self.update_bar.show_all()
        return False

    def on_update_response(self, bar, resp):
        if resp == 1 and self.update_url:
            open_url_as_user(self, self.update_url)
        bar.hide()

    def on_toggle_updates(self, item):
        self.cfg["update_check"] = item.get_active()
        self.schedule_save()
        if item.get_active():
            self.start_update_check(force=True)
        else:
            self.update_bar.hide()

    def on_about(self, *_args):
        a = Gtk.AboutDialog(transient_for=self, modal=True, program_name=C.APP_NAME,
                            version=C.VERSION, logo_icon_name="tuxsitter",
                            comments=_("Screen time, allowed hours and an internet filter for children's accounts on Linux."),
                            website=C.HOMEPAGE, license_type=Gtk.License.GPL_3_0)
        a.run()
        a.destroy()


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=C.APP_ID)

    def do_activate(self):
        win = self.get_active_window() or MainWindow(self)
        win.present()


def arg(argv, flag):
    if flag in argv:
        i = argv.index(flag)
        if i + 1 < len(argv):
            return argv[i + 1]
    return None


def apply_theme(argv):
    s = Gtk.Settings.get_default()
    for flag, prop in (("--theme", "gtk-theme-name"), ("--icons", "gtk-icon-theme-name")):
        if flag in argv:
            i = argv.index(flag)
            if i + 1 < len(argv) and argv[i + 1]:
                s.set_property(prop, argv[i + 1])
    if "--dark" in argv:
        s.set_property("gtk-application-prefer-dark-theme", True)


LOG_FILE = "/var/log/tuxsitter-gui.log"
_in_error_dialog = False


def excepthook(exc_type, exc, tb):
    """Log unexpected errors and show them, instead of failing silently."""
    import traceback
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    sys.stderr.write(text)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write("%s Tuxsitter %s\n%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), C.VERSION, text))
    except OSError:
        pass
    global _in_error_dialog
    if _in_error_dialog:
        return
    _in_error_dialog = True
    try:
        d = Gtk.MessageDialog(modal=True, message_type=Gtk.MessageType.ERROR,
                              buttons=Gtk.ButtonsType.CLOSE, text=_("Unexpected error"))
        d.format_secondary_text(_("Please report this at {url} and attach {log}.").format(
            url=C.HOMEPAGE + "/issues", log=LOG_FILE) + "\n\n" + str(exc))
        d.run()
        d.destroy()
    finally:
        _in_error_dialog = False


def main():
    if os.geteuid() != 0:
        os.execvp("pkexec", ["pkexec", os.path.abspath(__file__)] + sys.argv[1:])
    lang = arg(sys.argv, "--lang")
    if lang:
        i18n.setup(lang)
    apply_theme(sys.argv)
    sys.excepthook = excepthook
    prov = Gtk.CssProvider()
    prov.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), prov,
                                             Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    App().run([sys.argv[0]])


if __name__ == "__main__":
    main()
