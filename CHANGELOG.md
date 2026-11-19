# Changelog

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
