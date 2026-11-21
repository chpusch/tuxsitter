#!/usr/bin/python3
"""Live check of the built-in filter providers (encrypted DNS-over-HTTPS).

For every provider it checks that
  1. a normal domain resolves (example.com), and
  2. an adult domain is blocked (compared with an unfiltered resolver).

Run it on any machine with internet access - no root needed:
    python3 /usr/lib/tuxsitter/check_providers.py       (installed)
    python3 src/tuxsitter/check_providers.py            (git checkout)
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402
import dnsfilter as D  # noqa: E402

NORMAL = "example.com"
ADULT = "pornhub.com"
UNFILTERED = "https://cloudflare-dns.com/dns-query"


def resolve(up, name):
    rc, recs = D.parse_records(up.query(D.make_query(name, D.T_A, 0x4242)))
    return rc, sorted(".".join(map(str, rd)) for _, t, _, rd in recs if t == D.T_A)


def main():
    nextdns_id = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        ref = set(resolve(D.DohUpstream(UNFILTERED), ADULT)[1])
    except Exception as e:  # noqa: BLE001
        print("Cannot reach the internet (%s). Check your connection and try again." % e)
        sys.exit(2)
    print("Unfiltered reference for %s: %s\n" % (ADULT, ", ".join(sorted(ref)) or "-"))
    ok_all = True
    for key in C.PROVIDER_ORDER:
        if key == "custom" or (key == "nextdns" and not nextdns_id):
            continue
        cfg = {"provider": key, "nextdns_id": nextdns_id or ""}
        name = C.PROVIDERS[key]["name"]
        try:
            up = D.make_upstream(cfg, "Tuxsitter-Check")
            t0 = time.monotonic()
            rc, ips = resolve(up, NORMAL)
            ms = int((time.monotonic() - t0) * 1000)
            works = rc == D.RC_NOERROR and bool(ips)
            rc2, ips2 = resolve(up, ADULT)
            blocked = rc2 == D.RC_NXDOMAIN or not ips2 or set(ips2) <= {"0.0.0.0"} or not (set(ips2) & ref)
            status = "OK" if works and blocked else "PROBLEM"
            ok_all &= status == "OK"
            print("%-28s %-8s resolves: %-3s (%4d ms)   blocks adult: %-3s   [%s]" % (
                name, status, "yes" if works else "NO", ms, "yes" if blocked else "NO", up.host + up.path))
        except Exception as e:  # noqa: BLE001
            ok_all = False
            print("%-28s ERROR    %s" % (name, e))
    if not nextdns_id:
        print("\n(NextDNS skipped - pass your profile ID to include it: check_providers.py <ID>)")
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
