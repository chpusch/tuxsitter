#!/usr/bin/python3
"""Live check of the built-in filter providers (encrypted DNS-over-HTTPS).

For every provider it checks that
  1. it answers at all (example.com resolves), and
  2. which ways around the filter it blocks: other DNS services, VPN sites,
     web proxies and Tor (compared with an unfiltered resolver).

What content a provider blocks (adult sites etc.) is the provider's job and is not
tested here - see the provider's own documentation.

Run it on any machine with internet access - no root needed:
    python3 /usr/lib/tuxsitter/check_providers.py            (installed)
    python3 src/tuxsitter/check_providers.py                 (git checkout)
    python3 .../check_providers.py <NextDNS-ID>              (include NextDNS)
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402
import dnsfilter as D  # noqa: E402

NORMAL = "example.com"
UNFILTERED = "https://cloudflare-dns.com/dns-query"

# Well-known examples of each way around a DNS filter
BYPASS = {
    "DNS services": ["dns.google", "dns.quad9.net", "doh.mullvad.net", "dns.adguard-dns.com",
                     "doh.opendns.com", "dns.controld.com"],
    "VPN sites": ["nordvpn.com", "protonvpn.com", "expressvpn.com", "surfshark.com", "windscribe.com"],
    "Web proxies": ["kproxy.com", "hide.me", "croxyproxy.com", "proxysite.com"],
    "Tor": ["torproject.org", "www.torproject.org"],
}


def resolve(up, name):
    rc, recs = D.parse_records(up.query(D.make_query(name, D.T_A, 0x4242)))
    return rc, set(".".join(map(str, rd)) for _, t, _, rd in recs if t == D.T_A)


def is_blocked(up, name, ref_ips):
    """Blocked = no address, 0.0.0.0, or an address the unfiltered resolver does not know
    (i.e. the provider's block page)."""
    rc, ips = resolve(up, name)
    return rc == D.RC_NXDOMAIN or not ips or ips <= {"0.0.0.0"} or not (ips & ref_ips)


def main():
    nextdns_id = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        ref_up = D.DohUpstream(UNFILTERED)
        refs = {name: resolve(ref_up, name)[1] for name in
                [n for names in BYPASS.values() for n in names]}
    except Exception as e:  # noqa: BLE001
        print("Cannot reach the internet (%s). Check your connection and try again." % e)
        sys.exit(2)

    ok_all = True
    header = "%-30s %-8s %-10s" % ("Provider", "Status", "resolves")
    header += "".join(" %-13s" % k for k in BYPASS)
    print(header)
    print("-" * len(header))
    for key in C.PROVIDER_ORDER:
        if key == "custom" or (key == "nextdns" and not nextdns_id):
            continue
        name = C.PROVIDERS[key]["name"]
        try:
            up = D.make_upstream({"provider": key, "nextdns_id": nextdns_id or ""}, "Tuxsitter-Check")
            t0 = time.monotonic()
            rc, ips = resolve(up, NORMAL)
            ms = int((time.monotonic() - t0) * 1000)
            works = rc == D.RC_NOERROR and bool(ips)
            row = "%-30s %-8s %-10s" % (name, "OK" if works else "PROBLEM",
                                        "yes %dms" % ms if works else "NO")
            for names in BYPASS.values():
                tested = [n for n in names if refs.get(n)]          # only domains that exist
                blocked = sum(1 for n in tested if is_blocked(up, n, refs[n]))
                row += " %-13s" % ("%d/%d blocked" % (blocked, len(tested)))
            ok_all &= works
            print(row)
        except Exception as e:  # noqa: BLE001
            ok_all = False
            print("%-30s ERROR    %s" % (name, e))

    print("\nThe last columns show how many well-known examples of each way around a DNS filter")
    print("the provider blocks by itself (Tuxsitter additionally blocks known DNS services for")
    print("children, see 'Block workarounds').")
    if not nextdns_id:
        print("NextDNS skipped - pass your profile ID to include it: check_providers.py <ID>")
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
