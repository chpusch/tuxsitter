# Changelog

## 0.4.0 - 2026-11-22

Tighter and more secure internet filter.

- 🔒 **DNS filter runs without any rights**: it is now a separate service (`tuxsitter-dns.service`) under a
  temporary user created by systemd (`DynamicUser`) in a locked-down sandbox. Even a bug in the DNS code
  could not harm the system. Only the service with the right systemd cgroup receives the filter settings.
- 🚪 **Sibling loophole closed**: each child's filter port can only be used by that child.
- 🌐 **Browsers' own DNS locked** (new option, on by default): Firefox, Chrome, Chromium, Brave, Edge and
  Vivaldi can no longer bypass the filter with their own DNS-over-HTTPS setting. An existing Firefox
  `policies.json` is kept and restored on uninstall.
- 📏 Large DNS answers are now truncated correctly for UDP (the system retries over TCP).
- ⚡ More reused connections to the filter provider.
- ⚠️ The parent app warns when the DNS filter service is not running.
- CleanBrowsing is now described as "recommended for older children" (it also blocks bypass methods).
- "Block workarounds" now says honestly what it cannot catch (unknown DNS services, VPNs).
- Robustness tests (fuzzing) run automatically with every change.

## 0.3.0 - 2026-11-22

The parent app no longer runs as root.

- **Works on Wayland and X11**: the parent app now runs as your normal user. Graphical programs
  running as root are often blocked on Wayland - this problem is gone.
- **More secure**: only the small background service has admin rights. The parent app asks it to make
  changes; the service checks with **polkit** (the desktop's own password dialog) that you may manage
  Tuxsitter, and accepts only a fixed set of validated commands.
- The password is asked once when the app opens (polkit remembers it for a few minutes; the app's
  session ends when you close it).
- The PIN hash never leaves the service; settings are validated before they are saved.
- Error log and update cache moved to `~/.cache/tuxsitter/`.

## 0.2.2 - 2026-11-21

Security and privacy hardening of the DNS filter.

- **Encrypted DNS for all built-in providers** (DNS-over-HTTPS with certificate checking):
  Cloudflare for Families, CleanBrowsing Family, AdGuard Family and OpenDNS FamilyShield now use
  their official DoH endpoints, like NextDNS before. A custom server (e.g. Pi-hole) stays classic DNS.
- Classic DNS (custom server): every query now uses a random ID and source port, and answers are
  only accepted if ID and question match - protects against spoofed answers.
- DoH answers are checked against the question as well.
- Limits against flooding: max. 256 DNS queries and 32 TCP connections in parallel,
  max. 32 connections to the control socket.
- New `check_providers.py`: live test that every provider resolves normal sites and blocks adult content.

## 0.2.1 - 2026-11-20

- NextDNS field: the placeholder no longer shows an example ID in real NextDNS format, so nobody
  accidentally uses someone else's profile.

## 0.2.0 - 2026-11-19

- Update hint: the parent app checks GitHub at most once a day and shows a bar when a newer
  version is available. Anonymous, can be switched off in the menu ("Check for updates").

## 0.1.2 - 2026-11-19

- Clearer remaining time: the parent app and the panel now say when the remaining time is
  limited by the end of the allowed hours ("Allowed time ends at 20:00") rather than by the
  daily limit. Before, the countdown looked as if screen time was counted while logged out.

## 0.1.1 - 2026-11-19

- Fix: the parent app could stop working while entering the first PIN (and in a few other
  dialogs) because a callback parameter named `_` hid the translation function.
- Unexpected errors in the parent app are now shown and logged to `/var/log/tuxsitter-gui.log`.
- New test that catches this kind of bug.

## 0.1.0 - 2026-11-19

First public version.

- Daily screen time for school days and weekends, holiday mode
- Allowed hours, warnings and logout with countdown
- Extra time with a parent PIN (rate limited)
- Per-account DNS filter: Cloudflare for Families, CleanBrowsing, AdGuard Family,
  OpenDNS FamilyShield, NextDNS profile (DoH) or custom server
- SafeSearch, YouTube Restricted Mode, blocking of DoH/DoT workarounds
- 14-day history, admin-rights warning, create child accounts from the app
- English, German, French and Spanish
