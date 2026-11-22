"""Per-account DNS filter.

All DNS queries of a child account are redirected by nftables (matched by UID)
to a local port. A small forwarder listens there and
  * blocks well-known DNS-over-HTTPS servers and the Firefox canary domain,
  * rewrites Google/Bing/DuckDuckGo to SafeSearch and YouTube to Restricted Mode,
  * forwards everything else to the chosen family filter (plain DNS or NextDNS via DoH).
Python standard library only.
"""
import http.client
import ipaddress
import logging
import queue
import re
import secrets
import select
import socket
import ssl
import struct
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger("tuxsitter.dns")

T_A, T_CNAME, T_SOA, T_AAAA, T_OPT = 1, 5, 6, 28, 41
RC_NOERROR, RC_FORMERR, RC_SERVFAIL, RC_NXDOMAIN = 0, 1, 2, 3

# ---------------------------------------------------------------------------
# Block lists
# ---------------------------------------------------------------------------
CANARY = {"use-application-dns.net"}

DOH_HOSTS = {
    "dns.google", "dns.google.com", "8888.google", "dns64.dns.google",
    "cloudflare-dns.com", "one.one.one.one", "1dot1dot1dot1.cloudflare-dns.com",
    "dns.quad9.net", "dns9.quad9.net", "dns10.quad9.net", "dns11.quad9.net",
    "doh.opendns.com", "doh.familyshield.opendns.com", "familyshield.opendns.com", "dns.opendns.com", "dns.umbrella.com",
    "dns.adguard.com", "dns.adguard-dns.com", "adguard-dns.com",
    "dns.nextdns.io", "nextdns.io",
    "doh.cleanbrowsing.org",
    "mullvad.net",
    "doh.dns.sb", "dns.sb",
    "controld.com",
    "doh.libredns.gr", "dns.switch.ch", "doh.ffmuc.net", "dot.ffmuc.net",
    "dns3.digitalcourage.de", "doh.applied-privacy.net",
    "dns0.eu", "dns.alidns.com", "doh.pub", "dns.twnic.tw",
    "mask.icloud.com", "mask-h2.icloud.com",
    "doh.xfinity.com", "dns.brahma.world", "dnsforge.de",
}

# Also blocked when "block bypass" is enabled
BYPASS_SITES = {"torproject.org"}

# Well-known public resolvers (by IP) - rejected for child accounts by the firewall,
# so a browser cannot bypass the filter with a hard-coded DoH server IP.
DOH_IPV4 = [
    "1.1.1.1", "1.0.0.1", "1.1.1.2", "1.0.0.2", "1.1.1.3", "1.0.0.3",
    "8.8.8.8", "8.8.4.4",
    "9.9.9.9", "149.112.112.112", "9.9.9.10", "149.112.112.10", "9.9.9.11", "149.112.112.11",
    "94.140.14.14", "94.140.15.15", "94.140.14.15", "94.140.15.16", "94.140.14.140", "94.140.14.141",
    "208.67.222.222", "208.67.220.220", "208.67.222.123", "208.67.220.123",
    "185.228.168.0/24", "185.228.169.0/24",
    "45.90.28.0/24", "45.90.30.0/24",
    "194.242.2.0/24", "185.222.222.222", "45.11.45.11",
    "76.76.2.0/24", "76.76.10.0/24",
    "193.110.81.0/24", "185.253.5.0/24",
]
DOH_IPV6 = [
    "2606:4700:4700::/48",
    "2001:4860:4860::8888", "2001:4860:4860::8844",
    "2620:fe::/48", "2620:119:35::/48", "2620:119:53::/48", "2620:0:ccc::2", "2620:0:ccd::2",
    "2a10:50c0::/32",
    "2a07:a8c0::/32", "2a07:a8c1::/32",
    "2a07:e340::/32",
    "2a0d:2a00::/32",
    "2606:1a40::/32",
]

# ---------------------------------------------------------------------------
# SafeSearch targets
# ---------------------------------------------------------------------------
GOOGLE_RE = re.compile(r"^(www\.)?google\.(com|de|at|ch|co\.uk|fr|it|es|nl|be|pl|com\.tr|ru|com\.br)$")
YOUTUBE_HOSTS = {
    "www.youtube.com", "m.youtube.com", "youtube.com", "youtubei.googleapis.com",
    "youtube.googleapis.com", "www.youtube-nocookie.com",
}


def safesearch_target(name, dns_cfg):
    if dns_cfg.get("safesearch", True):
        if GOOGLE_RE.match(name):
            return "forcesafesearch.google.com"
        if name in ("www.bing.com", "bing.com"):
            return "strict.bing.com"
        if name in ("duckduckgo.com", "www.duckduckgo.com", "start.duckduckgo.com"):
            return "safe.duckduckgo.com"
    yt = dns_cfg.get("youtube", "moderate")
    if yt in ("moderate", "strict") and name in YOUTUBE_HOSTS:
        return "restrictmoderate.youtube.com" if yt == "moderate" else "restrict.youtube.com"
    return None


def _in_set(name, hostset):
    parts = name.split(".")
    for i in range(len(parts) - 1):
        if ".".join(parts[i:]) in hostset:
            return True
    return False


def is_blocked(name, dns_cfg):
    if name in CANARY:
        return True
    if dns_cfg.get("block_bypass", True):
        if _in_set(name, DOH_HOSTS) or _in_set(name, BYPASS_SITES):
            return True
    return False


# ---------------------------------------------------------------------------
# DNS packet helpers
# ---------------------------------------------------------------------------
class DNSError(Exception):
    pass


def read_name(buf, off):
    labels, jumped, end, hops = [], False, off, 0
    while True:
        if off >= len(buf):
            raise DNSError("name out of range")
        ln = buf[off]
        if ln & 0xC0 == 0xC0:
            if off + 1 >= len(buf):
                raise DNSError("bad pointer")
            ptr = ((ln & 0x3F) << 8) | buf[off + 1]
            if not jumped:
                end = off + 2
            jumped, off = True, ptr
            hops += 1
            if hops > 30:
                raise DNSError("pointer loop")
            continue
        if ln == 0:
            if not jumped:
                end = off + 1
            break
        off += 1
        labels.append(buf[off:off + ln].decode("ascii", "replace"))
        off += ln
    return ".".join(labels).lower(), end


def encode_name(name):
    out = b""
    for label in name.rstrip(".").split("."):
        if label:
            out += bytes([len(label)]) + label.encode("ascii")
    return out + b"\x00"


def parse_question(buf):
    if len(buf) < 12:
        raise DNSError("short")
    qid, flags, qd = struct.unpack("!HHH", buf[:6])
    if qd != 1:
        raise DNSError("qdcount != 1")
    name, off = read_name(buf, 12)
    qtype, qclass = struct.unpack("!HH", buf[off:off + 4])
    return qid, flags, name, qtype, qclass, off + 4


def parse_records(buf):
    """Return (rcode, [(name, type, ttl, rdata)]) of the answer + authority sections."""
    _, flags, qd, an, ns, _ = struct.unpack("!HHHHHH", buf[:12])
    off = 12
    for _ in range(qd):
        _, off = read_name(buf, off)
        off += 4
    recs = []
    for _ in range(an + ns):
        name, off = read_name(buf, off)
        rtype, _, ttl, rdlen = struct.unpack("!HHIH", buf[off:off + 10])
        off += 10
        recs.append((name, rtype, ttl, buf[off:off + rdlen]))
        off += rdlen
    return flags & 0x0F, recs


def udp_limit(query):
    """Largest UDP answer the client accepts: 512 bytes, or the EDNS buffer size it announced."""
    try:
        _, _, _, _, _, off = parse_question(query)
        ar = struct.unpack("!H", query[10:12])[0]
        for _ in range(ar):
            _, off = read_name(query, off)
            rtype, rclass, _, rdlen = struct.unpack("!HHIH", query[off:off + 10])
            if rtype == T_OPT:
                return max(512, min(rclass, 4096))
            off += 10 + rdlen
    except (DNSError, struct.error, IndexError):
        pass
    return 512


def fit_udp(query, resp):
    """Answers that are too big for UDP are replaced by an empty answer with the TC
    ("truncated") flag - the client then asks again over TCP and gets everything."""
    if len(resp) <= udp_limit(query):
        return resp
    try:
        _, _, _, _, _, qend = parse_question(resp)
    except (DNSError, struct.error, IndexError):
        return build_response(query, RC_SERVFAIL)
    flags = struct.unpack("!H", resp[2:4])[0] | 0x0200       # TC bit
    return resp[:2] + struct.pack("!HHHHH", flags, 1, 0, 0, 0) + resp[12:qend]


def build_response(query, rcode=RC_NOERROR, answers=()):
    """Response echoing the question. answers: [(type, ttl, rdata)] owned by the question name."""
    qid, flags, _, _, _, qend = parse_question(query)
    rd = flags & 0x0100
    rflags = 0x8000 | 0x0080 | rd | (rcode & 0x0F)  # QR, RA, copy RD
    hdr = struct.pack("!HHHHHH", qid, rflags, 1, len(answers), 0, 0)
    body = query[12:qend]
    for rtype, ttl, rdata in answers:
        body += b"\xc0\x0c" + struct.pack("!HHIH", rtype, 1, ttl, len(rdata)) + rdata
    return hdr + body


def make_query(name, qtype, qid=0):
    hdr = struct.pack("!HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    return hdr + encode_name(name) + struct.pack("!HH", qtype, 1)


def min_ttl(buf, default=60):
    try:
        rcode, recs = parse_records(buf)
    except Exception:
        return 0
    if rcode not in (RC_NOERROR, RC_NXDOMAIN):
        return 0
    ttls = [r[2] for r in recs if r[1] != T_OPT]
    return min(ttls) if ttls else default


# ---------------------------------------------------------------------------
# Upstreams
# ---------------------------------------------------------------------------
def same_question(query, resp):
    """True if the response answers exactly the question we asked (anti-spoofing)."""
    try:
        q = parse_question(query)
        r = parse_question(resp)
    except (DNSError, struct.error, IndexError):
        return False
    return q[2:5] == r[2:5] and resp[2] & 0x80   # name, type, class + QR bit


class PlainUpstream:
    """Classic DNS on port 53 (only used for a custom server, e.g. a Pi-hole at home).

    Every query gets a fresh random ID and a fresh random source port; answers
    are accepted only if ID and question match."""

    def __init__(self, servers):
        self.servers = [s.strip() for s in servers if s.strip()]
        if not self.servers:
            raise ValueError("bad_custom_dns")
        for s in self.servers:
            try:
                ipaddress.ip_address(s)
            except ValueError:
                raise ValueError("bad_custom_dns")
        self.port = 53

    def _udp(self, srv, data):
        fam = socket.AF_INET6 if ":" in srv else socket.AF_INET
        with socket.socket(fam, socket.SOCK_DGRAM) as s:
            s.settimeout(2.0)
            s.connect((srv, self.port))   # kernel drops datagrams from other addresses
            s.send(data)
            deadline = time.monotonic() + 2.0
            while True:
                resp = s.recv(4096)
                if resp[:2] == data[:2] and same_question(data, resp):
                    return resp
                if time.monotonic() > deadline:
                    raise socket.timeout()
                s.settimeout(max(0.05, deadline - time.monotonic()))

    def _tcp(self, srv, data):
        with socket.create_connection((srv, self.port), timeout=3.0) as s:
            s.sendall(struct.pack("!H", len(data)) + data)
            ln = struct.unpack("!H", _recv_exact(s, 2))[0]
            resp = _recv_exact(s, ln)
            if resp[:2] != data[:2] or not same_question(data, resp):
                raise OSError("DNS answer does not match the question")
            return resp

    def query(self, data, tcp=False):
        orig_id = data[:2]
        data = struct.pack("!H", secrets.randbits(16)) + data[2:]
        last = None
        for srv in self.servers:
            try:
                if tcp:
                    resp = self._tcp(srv, data)
                else:
                    resp = self._udp(srv, data)
                    if resp[2] & 0x02:  # truncated: retry over TCP
                        resp = self._tcp(srv, data)
                return orig_id + resp[2:]
            except OSError as e:
                last = e
        raise last or OSError("DNS server unreachable")


class DohUpstream:
    """DNS-over-HTTPS (RFC 8484) with certificate checking and a small keep-alive pool."""

    def __init__(self, url, size=8):
        u = urllib.parse.urlsplit(url)
        if u.scheme != "https" or not u.hostname:
            raise ValueError("bad DoH URL")
        self.host, self.port = u.hostname, u.port or 443
        self.path = u.path or "/dns-query"
        self.ctx = ssl.create_default_context()      # verifies the server certificate
        self.pool = queue.LifoQueue()
        self.size = size

    def _conn(self):
        try:
            return self.pool.get_nowait()
        except queue.Empty:
            return http.client.HTTPSConnection(self.host, self.port, timeout=5.0, context=self.ctx)

    def _post(self, conn, data):
        conn.request("POST", self.path, body=data, headers={
            "Content-Type": "application/dns-message",
            "Accept": "application/dns-message",
            "User-Agent": "Tuxsitter",
        })
        r = conn.getresponse()
        body = r.read(65536)
        if r.status != 200:
            raise OSError("DoH HTTP %d" % r.status)
        return body

    def query(self, data, tcp=False):
        qid = data[:2]
        data = b"\x00\x00" + data[2:]   # RFC 8484: ID 0 for better caching
        for attempt in range(2):
            conn = self._conn()
            try:
                body = self._post(conn, data)
                if not same_question(data, body):
                    raise OSError("DoH answer does not match the question")
                if self.pool.qsize() < self.size:
                    self.pool.put(conn)
                else:
                    conn.close()
                return qid + body[2:]
            except (OSError, http.client.HTTPException) as e:
                conn.close()       # stale keep-alive connection -> retry once with a new one
                if attempt:
                    raise OSError(str(e))
        raise OSError("DoH failed")


NEXTDNS_ID_RE = re.compile(r"^[A-Za-z0-9]{4,20}$")


def make_upstream(dns_cfg, device="Tuxsitter"):
    from common import PROVIDERS
    prov = dns_cfg.get("provider", "cloudflare")
    if prov == "custom":
        return PlainUpstream(dns_cfg.get("custom", "").replace(";", ",").split(","))
    url = PROVIDERS.get(prov, PROVIDERS["cloudflare"])["doh"]
    if prov == "nextdns":
        pid = dns_cfg.get("nextdns_id", "").strip()
        if not NEXTDNS_ID_RE.match(pid):
            raise ValueError("bad_nextdns_id")
        url = url.format(id=pid, device=re.sub(r"[^A-Za-z0-9-]", "-", device)[:30])
    return DohUpstream(url)


# ---------------------------------------------------------------------------
# Filter (one child)
# ---------------------------------------------------------------------------
class ChildFilter:
    def __init__(self, user, dns_cfg):
        self.user = user
        self.cfg = dns_cfg
        self.upstream = make_upstream(dns_cfg, "Tuxsitter-" + user)
        self.cache = {}
        self.lock = threading.Lock()
        self.stats = {"queries": 0, "blocked": 0, "errors": 0}

    def _cache_get(self, key):
        with self.lock:
            v = self.cache.get(key)
            if v and v[0] > time.monotonic():
                return v[1]
            if v:
                del self.cache[key]
        return None

    def _cache_put(self, key, resp):
        ttl = min(max(min_ttl(resp), 0), 300)
        if ttl < 5:
            return
        with self.lock:
            if len(self.cache) > 5000:
                self.cache.clear()
            self.cache[key] = (time.monotonic() + ttl, resp)

    def _resolve(self, name, qtype, tcp=False):
        key = (name, qtype)
        hit = self._cache_get(key)
        if hit is not None:
            return hit
        resp = self.upstream.query(make_query(name, qtype, 0), tcp=tcp)
        self._cache_put(key, resp)
        return resp

    def handle(self, query, tcp=False):
        try:
            qid, flags, name, qtype, qclass, _ = parse_question(query)
        except Exception:
            return None
        self.stats["queries"] += 1
        try:
            if is_blocked(name, self.cfg):
                self.stats["blocked"] += 1
                return build_response(query, RC_NXDOMAIN)
            target = safesearch_target(name, self.cfg)
            if target:
                if qtype not in (T_A, T_AAAA):
                    return build_response(query, RC_NOERROR)
                rc, recs = parse_records(self._resolve(target, qtype))
                answers = [(t, min(ttl, 300), rd) for (_, t, ttl, rd) in recs if t == qtype]
                return build_response(query, RC_NOERROR, answers)
            if qclass != 1:
                return build_response(query, RC_NOERROR)
            # normal query: forward unchanged (keeps EDNS)
            key = ("raw", name, qtype, query[2] & 0x01, query[10:12])
            hit = self._cache_get(key)
            if hit is None:
                hit = self.upstream.query(b"\x00\x00" + query[2:], tcp=tcp)
                self._cache_put(key, hit)
            return query[:2] + hit[2:]
        except Exception as e:
            self.stats["errors"] += 1
            log.debug("DNS error %s %s: %s", self.user, name, e)
            return build_response(query, RC_SERVFAIL)


def _recv_exact(s, n):
    buf = b""
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise OSError("connection closed")
        buf += chunk
    return buf


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------
MAX_INFLIGHT = 256   # UDP queries being processed at the same time
MAX_TCP = 32         # open DNS-over-TCP connections


class DNSServer:
    """Listens on 127.0.0.1/::1, one port per child (UDP + TCP)."""

    def __init__(self):
        self.filters = {}      # port -> ChildFilter
        self.sockets = {}      # port -> [sockets]
        self.pool = ThreadPoolExecutor(max_workers=48, thread_name_prefix="dns")
        # limits, so a flood of queries cannot exhaust memory or threads
        self.inflight = threading.BoundedSemaphore(MAX_INFLIGHT)
        self.tcp_slots = threading.BoundedSemaphore(MAX_TCP)
        self.lock = threading.Lock()
        self.running = True
        self.wake_r, self.wake_w = socket.socketpair()
        threading.Thread(target=self._loop, name="dns-loop", daemon=True).start()

    def update(self, wanted):
        """wanted: {port: (user, dns_cfg)}"""
        with self.lock:
            for port in list(self.sockets):
                if port not in wanted:
                    for s in self.sockets.pop(port):
                        s.close()
                    self.filters.pop(port, None)
            for port, (user, cfg) in wanted.items():
                old = self.filters.get(port)
                if old is None or old.cfg != cfg or old.user != user:
                    try:
                        self.filters[port] = ChildFilter(user, cfg)
                    except Exception as e:
                        log.error("Invalid DNS settings for %s: %s", user, e)
                        self.filters[port] = _FailFilter(user, cfg)
                if port not in self.sockets:
                    self.sockets[port] = self._open(port)
        self.wake_w.send(b"x")

    def _open(self, port):
        socks = []
        for fam, addr in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
            for typ in (socket.SOCK_DGRAM, socket.SOCK_STREAM):
                try:
                    s = socket.socket(fam, typ)
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    if fam == socket.AF_INET6:
                        s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                    s.bind((addr, port))
                    if typ == socket.SOCK_STREAM:
                        s.listen(64)
                    s.setblocking(False)
                    socks.append(s)
                except OSError as e:
                    if fam == socket.AF_INET:
                        log.error("Cannot open DNS port %s:%d: %s", addr, port, e)
        return socks

    def _loop(self):
        while self.running:
            with self.lock:
                smap = {s: port for port, ss in self.sockets.items() for s in ss}
            try:
                ready, _, _ = select.select(list(smap) + [self.wake_r], [], [], 5)
            except (OSError, ValueError):
                time.sleep(0.2)
                continue
            for s in ready:
                if s is self.wake_r:
                    self.wake_r.recv(64)
                    continue
                port = smap[s]
                try:
                    if s.type == socket.SOCK_DGRAM:
                        data, addr = s.recvfrom(4096)
                        if self.inflight.acquire(blocking=False):
                            self.pool.submit(self._udp_job, s, port, data, addr)
                        # else: overloaded -> drop, the client simply retries
                    else:
                        conn, _ = s.accept()
                        if self.tcp_slots.acquire(blocking=False):
                            self.pool.submit(self._tcp_job, conn, port)
                        else:
                            conn.close()
                except OSError:
                    pass

    def _filter(self, port):
        with self.lock:
            return self.filters.get(port)

    def _udp_job(self, s, port, data, addr):
        try:
            f = self._filter(port)
            resp = f.handle(data) if f else None
            if resp:
                resp = fit_udp(data, resp)
                try:
                    s.sendto(resp, addr)
                except OSError:
                    pass
        finally:
            self.inflight.release()

    def _tcp_job(self, conn, port):
        conn.settimeout(10)
        try:
            while True:
                ln = struct.unpack("!H", _recv_exact(conn, 2))[0]
                data = _recv_exact(conn, ln)
                f = self._filter(port)
                if not f:
                    return
                resp = f.handle(data, tcp=True)
                if not resp:
                    return
                conn.sendall(struct.pack("!H", len(resp)) + resp)
        except (OSError, struct.error):
            pass
        finally:
            conn.close()
            self.tcp_slots.release()


class _FailFilter:
    """Invalid configuration: no internet rather than unfiltered internet."""

    def __init__(self, user, cfg):
        self.user, self.cfg = user, cfg
        self.stats = {"queries": 0, "blocked": 0, "errors": 0}

    def handle(self, query, tcp=False):
        try:
            return build_response(query, RC_SERVFAIL)
        except Exception:
            return None


def test_upstream(dns_cfg, name="example.com"):
    """For the parent app: check that the selected filter answers."""
    t0 = time.monotonic()
    up = make_upstream(dns_cfg, "Tuxsitter-Test")
    resp = up.query(make_query(name, T_A, 0x1234))
    rc, recs = parse_records(resp)
    ms = int((time.monotonic() - t0) * 1000)
    if rc != RC_NOERROR or not any(r[1] == T_A for r in recs):
        raise LookupError("no_answer")
    return ms


# ---------------------------------------------------------------------------
# nftables
# ---------------------------------------------------------------------------
NFT_TABLE = "tuxsitter"


def build_ruleset(entries):
    """entries: [(uid, port, block_bypass)]"""
    lines = [
        "table inet %s" % NFT_TABLE,
        "delete table inet %s" % NFT_TABLE,
        "table inet %s {" % NFT_TABLE,
        "  set doh4 { type ipv4_addr; flags interval; elements = { %s } }" % ", ".join(DOH_IPV4),
        "  set doh6 { type ipv6_addr; flags interval; elements = { %s } }" % ", ".join(DOH_IPV6),
        "  chain nat_out {",
        "    type nat hook output priority -100; policy accept;",
    ]
    for uid, port, _ in entries:
        lines.append("    meta skuid %d udp dport 53 redirect to :%d" % (uid, port))
        lines.append("    meta skuid %d tcp dport 53 redirect to :%d" % (uid, port))
    lines += [
        "  }",
        "  chain filter_out {",
        "    type filter hook output priority 0; policy accept;",
    ]
    for uid, port, _ in entries:
        # a child's filter port may only be used by that child (not by a sibling with laxer rules)
        lines.append("    udp dport %d meta skuid != %d reject" % (port, uid))
        lines.append("    tcp dport %d meta skuid != %d reject" % (port, uid))
    for uid, _, block in entries:
        if block:
            lines.append("    meta skuid %d tcp dport 853 reject" % uid)
            lines.append("    meta skuid %d udp dport { 853, 8853 } reject" % uid)
            lines.append("    meta skuid %d ip daddr @doh4 reject" % uid)
            lines.append("    meta skuid %d ip6 daddr @doh6 reject" % uid)
    lines += ["  }", "}"]
    return "\n".join(lines) + "\n"


EMPTY_RULESET = "table inet %s\ndelete table inet %s\n" % (NFT_TABLE, NFT_TABLE)
