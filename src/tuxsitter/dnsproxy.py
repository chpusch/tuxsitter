#!/usr/bin/python3
"""Tuxsitter DNS filter process.

Runs WITHOUT any privileges: systemd starts it under a throw-away user (DynamicUser)
in a locked-down sandbox (see tuxsitter-dns.service). It only listens on 127.0.0.1/::1
and forwards the children's DNS queries to the chosen family filter.

Which ports to open and which filter to use for which child is fetched from the
main Tuxsitter service every few seconds. If the main service is not reachable at
start-up, no ports are opened - the children then have no internet (fail-closed).
"""
import json
import logging
import os
import signal
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402
import dnsfilter  # noqa: E402

log = logging.getLogger("tuxsitter.dns")
POLL = 3


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if os.geteuid() == 0 and "--allow-root" not in sys.argv:
        print("tuxsitter-dns must not run as root (it is started by systemd with DynamicUser=yes).",
              file=sys.stderr)
        sys.exit(1)
    running = [True]

    def stop(*_args):
        running[0] = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    server = dnsfilter.DNSServer()
    current = None
    log.info("Tuxsitter DNS filter %s started (uid %d)", C.VERSION, os.geteuid())
    while running[0]:
        try:
            r = C.request({"cmd": "dns_config"}, timeout=5)
        except (OSError, ValueError):
            r = None
        if r and r.get("ok"):
            sig = json.dumps(r["children"], sort_keys=True)
            if sig != current:
                server.update({int(port): (user, dns) for port, (user, dns) in r["children"].items()})
                current = sig
                log.info("Filter settings loaded (%d children)", len(r["children"]))
        elif r is not None:
            log.error("Main service refused the request: %s", r.get("error"))
        for _ in range(POLL * 2):
            if not running[0]:
                break
            time.sleep(0.5)
    log.info("stopped")


if __name__ == "__main__":
    main()
