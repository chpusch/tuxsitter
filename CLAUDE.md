# Notes for Claude and contributors

Tuxsitter is a parental-control app for Linux Mint: daily time limits, allowed hours, logout with
countdown, extra time via parent PIN, and a per-child DNS filter. See README.md for features and
CHANGELOG.md for history. This file records **why** things are the way they are.

## Principles

- **Simple and intuitive** for parents who are not Linux experts. Avoid over-engineering.
- **Feature freeze** since 0.4.x: fix bugs and wait for feedback from real testers before adding
  anything new.
- **Secure by default.** Experienced users may deliberately lower protection; the default must be good.
- Python 3 standard library + PyGObject/GTK3 only. No extra dependencies.
- **Test on Ubuntu too**, not only Mint: Mint pre-installs packages (e.g. `python3-gi-cairo`) that
  Ubuntu lacks. Every Python/GI module used must be listed in `packaging/deb/control`.

## Architecture in short

- `tuxsitter.service` (root): counts time via logind, logs out, manages nftables and browser policies,
  answers a UNIX socket.
- `tuxsitter-dns.service`: DNS proxy without any rights (`DynamicUser=yes`, sandboxed). Gets its
  config from the daemon; the daemon recognises it by its cgroup.
- Parent app runs as a normal user; admin actions are authorised via polkit (`pkcheck`). Works on
  X11 and Wayland.
- Child tray icon: warnings, countdown, extra time with PIN.

## Decisions (keep unless there is a good reason)

- **Default filter is OpenDNS FamilyShield** (0.4.1). Measured with `check_providers.py`, it also
  blocks other DNS services, VPN sites, web proxies and Tor; Cloudflare and AdGuard block almost
  none of these. This closes the gap of self-installed browsers with an unknown DoH server.
  Config version 2 migrated Cloudflare users once.
- **No adult-site test in the code.** `check_providers.py` only checks bypass blocking. Which content
  is blocked is up to the provider.
- **Only DNS-over-HTTPS (HTTP/1.1) upstream**, no DNS-over-TLS, no HTTP/2. DNS4EU was tried and
  dropped (its DoH endpoint is HTTP/2 only) to keep the DNS proxy simple.
- **No broad browser hardening** (private mode etc.). Only the browsers' own DNS setting is locked
  (Firefox and Chromium-family policies), switchable in the app.
- **NextDNS device name stays `Tuxsitter-<username>`** - documented in the README privacy section.
  Helpful for parents in the NextDNS logs.
- **Update check** against GitHub releases at most once a day, can be switched off. No other data
  leaves the computer except DNS queries to the chosen provider.
- **No supply-chain hardening of CI** (e.g. pinning actions) for now - deliberately kept simple.
- **Physical access** (live USB, GRUB recovery, BIOS clock) cannot be solved by the app; only
  BIOS/UEFI and GRUB passwords help.

## Workflow

1. Change code, run `python3 -m unittest discover -s tests -t .`.
2. New or changed UI strings: `python3 tools/update-po.py`, translate in `po/de.po`, `fr.po`, `es.po`
   (English is the source language). All four languages must stay complete.
3. Bump `VERSION` in `src/tuxsitter/common.py`, add a CHANGELOG entry and a `<release>` entry in
   `data/io.github.chpusch.tuxsitter.metainfo.xml` (a test checks it).
4. Build with `./tools/build-deb.sh` → `build/tuxsitter_<version>_all.deb`.
5. The maintainer tests the .deb on his Linux Mint laptop. **Push only after his OK.**
6. The maintainer creates the GitHub release himself (tag `v<version>`); CI attaches the .deb.
   Provide release notes in English with a short German section.

## Don't

- Don't add private data to the repo (NextDNS profile IDs, PINs, usernames, IPs, logs).
- Don't use `_` as a parameter name (it shadows gettext - see `tests/test_code.py`).
