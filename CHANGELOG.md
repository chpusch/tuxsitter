# Changelog

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
