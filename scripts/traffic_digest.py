"""Weekly / monthly traffic digest from Cloud Run request logs.

Builds the analysis that .github/workflows/traffic-digest.yml emails to the
operator. Same measurement model as the daily traffic-report lane: every page
load fires GET /health (the boot probe), so human-UA health hits ≈ page views;
first-party, ad-block-proof, cookieless. This script adds what a once-a-day
snapshot cannot give:

- a PERIOD view (the last 7 complete UTC days, or the last complete calendar
  month) compared with the period before it, with deltas;
- a COUNTS-ONLY per-day history (``traffic-history.json``) that the lane folds
  the fresh log pull into on every run, so month-over-month comparisons
  survive Cloud Logging's 30-day retention. The history holds no IP, no
  user-agent, no referer path — only daily counters — so it can sit in the
  bucket's export/ prefix like every other derived file;
- one digest rendered three ways from the same dict: Markdown (bucket copy),
  HTML (the email body) and JSON (machine-readable).

Privacy contract (this repo is public, Actions logs are world-readable):
``summary_line()`` is the ONLY thing the workflow prints, and it carries
counts alone. The full digest names IPs, cities and network orgs — it goes to
the operator's inbox and the private bucket, never the job log.
tests/test_traffic_digest.py pins both halves.

Stdlib only — the bare Actions runner has no pydantic (same contract as
evidence_ledger.py and email_domain_scan.py).
"""

from __future__ import annotations

import argparse
import bisect
import csv
import datetime as dt
import html
import ipaddress
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlparse

HISTORY_SCHEMA = 1
# Keys a history day record may carry. The test asserts nothing else leaks in.
HISTORY_DAY_KEYS = frozenset(
    {
        "views",
        "bot",
        "requests",
        "visitor_days",
        "countries",
        "net_class",
        "surfaces",
        "referrers",
        "orgs",
        "probes",
        "status_4xx",
        "status_5xx",
        "probe_clients",
    }
)
COUNTER_KEYS = ("countries", "net_class", "surfaces", "referrers", "orgs")

BOT_RE = re.compile(
    r"bot|crawl|spider|scrape|curl|wget|python-requests|httpx|go-http|"
    r"feedparser|feedfetcher|rss|monitor|probe|uptime|headless",
    re.I,
)
HOSTING_RE = re.compile(
    r"amazon|aws|google|goog|azure|microsoft|digitalocean|linode|akamai|"
    r"cloudflare|ovh|hetzner|vultr|oracle|leaseweb|contabo|scaleway|"
    r"hosting|datacenter|data center|server|vps|colo|cloud|fastly|"
    r"m247|choopa|hostwinds|godaddy|namecheap|censys|shodan|palo alto|"
    r"digital ocean|tencent|alibaba|huawei cloud",
    re.I,
)
ISP_RE = re.compile(
    r"comcast|verizon|at&t|at t|spectrum|charter|cox\b|centurylink|lumen|"
    r"t-mobile|sprint|frontier|windstream|mediacom|optimum|altice|"
    r"vodafone|telefonica|deutsche telekom|orange|telecom italia|"
    r"\bbt\b|sky\b|virgin media|telus|rogers|bell canada|shaw|videotron|"
    r"broadband|cable|\bdsl\b|fiber|fibre|wireless|cellular|mobile|"
    r"communications|telecom|telekom|internet services|isp\b",
    re.I,
)
PROBE_PATHS = ("/.env", "/wp-login.php", "/admin", "/.git", "/xmlrpc.php", "/phpmyadmin")

# API path → the product surface a reader was on. Order matters: first prefix
# match wins. /health is the boot probe and is counted as the page view itself.
SURFACES: tuple[tuple[str, str], ...] = (
    ("/evidence/technique", "TECHNIQUE DOSSIER"),
    ("/evidence/ledger", "EVIDENCE"),
    ("/tooling", "TOOLING"),
    ("/extract/ttps", "MODUS OPERANDI report"),
    ("/articles", "STREAM"),
    ("/search", "SEARCH"),
    ("/feed.xml", "RSS feed"),
    ("/export/", "EXPORT download"),
    ("/trending", "TRENDING"),
    ("/itm", "ITM catalog"),
    ("/sources", "SOURCES"),
    ("/usecases", "USE CASES"),
    ("/lanes/health", "SOURCE HEALTH"),
    ("/social", "SOCIAL"),
    ("/publications", "PUBLICATIONS"),
    ("/reload", "ops: reload"),
)

CLASS_LABEL = {
    "business": "business / org-registered range",
    "isp": "consumer ISP (home, mobile, small office)",
    "hosting": "hosting / datacenter (bots, VPNs, scanners)",
    "unknown": "unclassified (no network match)",
}


TABLE_OPEN = (
    '<table cellspacing="0" cellpadding="0" '
    'style="border-collapse:collapse;width:100%;max-width:720px">'
)
METHOD_NOTE = (
    "Every page load fires GET /health, so human-UA health hits are page views: first-party, "
    "ad-block-proof, cookieless. Bot/script user-agents are counted separately. Geolocation and "
    "network type come from offline DB-IP lite tables, so no visitor IP leaves the pipeline. "
    "Deltas under a base of 10 show the absolute change, not a percentage. Per-day history is "
    "counts only and lives in the private bucket; this digest is the only place IPs and network "
    "names are written."
)


# ---------------------------------------------------------------------------
# Offline GeoIP / ASN (DB-IP lite CSVs, both v4 and v6 rows)
# ---------------------------------------------------------------------------


class RangeDB:
    """Sorted (start, end, *fields) ranges, split by IP version."""

    def __init__(self, path: str | None, extract, blank: tuple):
        self.blank = blank
        self.v4: list[tuple] = []
        self.v6: list[tuple] = []
        if path:
            try:
                with open(path, newline="", encoding="utf-8", errors="replace") as fh:
                    for row in csv.reader(fh):
                        if len(row) < 4:
                            continue
                        try:
                            a = ipaddress.ip_address(row[0])
                            b = ipaddress.ip_address(row[1])
                        except ValueError:
                            continue
                        rec = (int(a), int(b)) + tuple(extract(row))
                        (self.v4 if a.version == 4 else self.v6).append(rec)
            except FileNotFoundError:
                pass
        self.v4.sort()
        self.v6.sort()
        self.s4 = [r[0] for r in self.v4]
        self.s6 = [r[0] for r in self.v6]

    def __len__(self) -> int:
        return len(self.v4) + len(self.v6)

    def lookup(self, ip: str) -> tuple:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return self.blank
        ranges, starts = (self.v4, self.s4) if addr.version == 4 else (self.v6, self.s6)
        if not ranges:
            return self.blank
        n = int(addr)
        i = bisect.bisect_right(starts, n) - 1
        if 0 <= i < len(ranges) and ranges[i][0] <= n <= ranges[i][1]:
            return ranges[i][2:]
        return self.blank


def load_geo(path: str | None) -> RangeDB:
    # DB-IP city-lite: ip_start, ip_end, continent, country, state, city, lat, lng
    return RangeDB(
        path,
        lambda r: (r[3] if len(r) > 3 else r[2], r[5] if len(r) > 5 else ""),
        ("", ""),
    )


def load_asn(path: str | None) -> RangeDB:
    # DB-IP ASN-lite: ip_start, ip_end, as_number, as_organization
    return RangeDB(path, lambda r: (r[3],), ("",))


def net_class(org: str) -> str:
    if not org:
        return "unknown"
    if HOSTING_RE.search(org):
        return "hosting"
    if ISP_RE.search(org):
        return "isp"
    return "business"


_BROWSER_RE = (
    ("Edge", re.compile(r"Edg(?:e|A|iOS)?/", re.I)),
    ("Opera", re.compile(r"OPR/|Opera", re.I)),
    ("Samsung Internet", re.compile(r"SamsungBrowser", re.I)),
    ("Chrome", re.compile(r"Chrome/|CriOS/", re.I)),
    ("Firefox", re.compile(r"Firefox/|FxiOS/", re.I)),
    ("Safari", re.compile(r"Safari/", re.I)),
)
_PLATFORM_RE = (
    ("iPhone", re.compile(r"iPhone", re.I)),
    ("iPad", re.compile(r"iPad", re.I)),
    ("Android", re.compile(r"Android", re.I)),
    ("Windows", re.compile(r"Windows", re.I)),
    ("macOS", re.compile(r"Macintosh|Mac OS X", re.I)),
    ("Linux", re.compile(r"Linux|X11", re.I)),
)


def ua_family(ua: str) -> str:
    """'Chrome · Windows' from a human user-agent; the raw prefix when unknown."""
    browser = next((name for name, rx in _BROWSER_RE if rx.search(ua)), "")
    platform = next((name for name, rx in _PLATFORM_RE if rx.search(ua)), "")
    if browser or platform:
        return " · ".join(x for x in (browser, platform) if x)
    return ua.split("(")[0].strip()[:40] or "?"


def surface_for(path: str) -> str:
    for prefix, label in SURFACES:
        if path.startswith(prefix):
            return label
    return "other"


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------


def _month_start(d: dt.date) -> dt.date:
    return d.replace(day=1)


def _prev_month_start(d: dt.date) -> dt.date:
    first = _month_start(d)
    return _month_start(first - dt.timedelta(days=1))


def windows(period: str, as_of: dt.date) -> tuple[tuple[dt.date, dt.date], tuple[dt.date, dt.date]]:
    """(current, prior) as inclusive [start, end] date pairs.

    weekly: the 7 complete UTC days ending yesterday, vs the 7 before.
    monthly: the last complete calendar month, vs the month before it.
    """
    if period == "weekly":
        end = as_of - dt.timedelta(days=1)
        start = end - dt.timedelta(days=6)
        p_end = start - dt.timedelta(days=1)
        p_start = p_end - dt.timedelta(days=6)
        return (start, end), (p_start, p_end)
    if period == "monthly":
        cur_start = _prev_month_start(as_of)
        cur_end = _month_start(as_of) - dt.timedelta(days=1)
        p_start = _prev_month_start(cur_start)
        p_end = cur_start - dt.timedelta(days=1)
        return (cur_start, cur_end), (p_start, p_end)
    raise ValueError(f"unknown period {period!r}")


def days_in(window: tuple[dt.date, dt.date]) -> list[str]:
    start, end = window
    out = []
    d = start
    while d <= end:
        out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


# ---------------------------------------------------------------------------
# Log parsing → per-day aggregates (+ the window-level detail that needs IPs)
# ---------------------------------------------------------------------------


def _new_day() -> dict:
    return {
        "views": 0,
        "bot": 0,
        "requests": 0,
        "visitor_days": 0,
        "countries": Counter(),
        "net_class": Counter(),
        "surfaces": Counter(),
        "referrers": Counter(),
        "orgs": Counter(),
        "probes": 0,
        "status_4xx": 0,
        "status_5xx": 0,
        "probe_clients": 0,
    }


def parse_entries(entries: list[dict], geo: RangeDB, asn: RangeDB) -> tuple[dict, dict]:
    """Fold Cloud Logging entries into (days, detail).

    ``days``: date → counts-only record (history-safe).
    ``detail``: date → the IP-level material the digest's security and
    top-source sections need; never persisted.
    """
    days: dict[str, dict] = defaultdict(_new_day)
    detail: dict[str, dict] = defaultdict(
        lambda: {
            "human_ips": set(),
            "ip_hits": Counter(),
            "ip_4xx": Counter(),
            "ip_probe": Counter(),
            "ip_meta": {},
            "probe_paths": Counter(),
            "status": Counter(),
            "endpoints": Counter(),
            "ua_families": Counter(),
        }
    )
    for e in entries:
        ts = e.get("timestamp") or ""
        day = ts[:10]
        if len(day) != 10:
            continue
        req = e.get("httpRequest") or {}
        ip = req.get("remoteIp") or ""
        url = req.get("requestUrl") or ""
        path = urlparse(url).path
        ua = req.get("userAgent") or ""
        ref = req.get("referer") or ""
        status = str(req.get("status") or "")
        d = days[day]
        x = detail[day]
        d["requests"] += 1
        x["status"][status or "?"] += 1
        x["endpoints"][path] += 1
        if status.startswith("4"):
            d["status_4xx"] += 1
            if ip:
                x["ip_4xx"][ip] += 1
        elif status.startswith("5"):
            d["status_5xx"] += 1
        cc, city = geo.lookup(ip)
        (org,) = asn.lookup(ip)
        klass = net_class(org)
        if ip:
            x["ip_hits"][ip] += 1
            x["ip_meta"][ip] = (cc, city, org, klass)
        is_probe = path in PROBE_PATHS or ".." in path or path.endswith((".php", ".asp"))
        if is_probe:
            d["probes"] += 1
            x["probe_paths"][path] += 1
            if ip:
                x["ip_probe"][ip] += 1
        if ref and "thederpweb" not in ref and "insider-intel.net" not in ref:
            d["referrers"][ref.split("?")[0][:80]] += 1
        if path == "/health":
            is_bot = bool(BOT_RE.search(ua)) or not ua
            if is_bot:
                d["bot"] += 1
            else:
                d["views"] += 1
                if ip:
                    x["human_ips"].add(ip)
                if cc:
                    d["countries"][cc] += 1
                d["net_class"][klass] += 1
                if klass == "business" and org:
                    d["orgs"][org[:48]] += 1
                x["ua_families"][ua_family(ua)] += 1
        else:
            s = surface_for(path)
            if s != "other" or not is_probe:
                d["surfaces"][s] += 1
    for day, x in detail.items():
        days[day]["visitor_days"] = len(x["human_ips"])
        days[day]["probe_clients"] = len(x["ip_probe"])
    return dict(days), dict(detail)


# ---------------------------------------------------------------------------
# History (counts only)
# ---------------------------------------------------------------------------


def _serialise_day(rec: dict) -> dict:
    out = {}
    for k in HISTORY_DAY_KEYS:
        v = rec.get(k, Counter() if k in COUNTER_KEYS else 0)
        out[k] = dict(v) if k in COUNTER_KEYS else int(v)
    return out


def _deserialise_day(rec: dict) -> dict:
    out = _new_day()
    for k in HISTORY_DAY_KEYS:
        if k not in rec:
            continue
        out[k] = Counter(rec[k]) if k in COUNTER_KEYS else int(rec[k])
    return out


def load_history(path: str | None) -> dict[str, dict]:
    if not path or not Path(path).exists():
        return {}
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    days = raw.get("days") if isinstance(raw, dict) else None
    if not isinstance(days, dict):
        return {}
    return {d: _deserialise_day(r) for d, r in days.items() if isinstance(r, dict)}


def merge_history(history: dict[str, dict], fresh: dict[str, dict], log_days: set[str]) -> dict:
    """Fresh log days overwrite history; everything else carries forward.

    ``log_days`` is every day the log pull covered (including days with zero
    requests), so a quiet day inside the pull also resets to zero instead of
    keeping a stale count.
    """
    merged = dict(history)
    for day in log_days:
        merged[day] = fresh.get(day, _new_day())
    return merged


def dump_history(merged: dict[str, dict], generated_at: str) -> dict:
    return {
        "schema": HISTORY_SCHEMA,
        "generated_at": generated_at,
        "note": "counts only — no IPs, user-agents or referer paths; fresh log days overwrite",
        "days": {d: _serialise_day(merged[d]) for d in sorted(merged)},
    }


# ---------------------------------------------------------------------------
# Period aggregation
# ---------------------------------------------------------------------------


def aggregate(days: dict[str, dict], window: tuple[dt.date, dt.date]) -> dict:
    agg = _new_day()
    covered = 0
    for day in days_in(window):
        rec = days.get(day)
        if rec is None:
            continue
        covered += 1
        for k in ("views", "bot", "requests", "visitor_days", "probes", "status_4xx", "status_5xx"):
            agg[k] += rec[k]
        for k in COUNTER_KEYS:
            agg[k].update(rec[k])
        agg["probe_clients"] += rec["probe_clients"]
    agg["days_covered"] = covered
    agg["days_total"] = len(days_in(window))
    return agg


def delta(cur: int, prev: int, *, small_n: int = 10) -> str:
    """Verb-free, honest delta. Below ``small_n`` on the base, percentages lie."""
    if prev == 0:
        return "new" if cur else "—"
    diff = cur - prev
    if prev < small_n:
        return f"{diff:+d}"
    pct = round(100.0 * diff / prev)
    return f"{pct:+d}%"


def _top(counter: Counter, n: int) -> list[tuple[str, int]]:
    return counter.most_common(n)


def _ip_label(ip: str, meta: dict) -> str:
    cc, city, org, klass = meta.get(ip, ("", "", "", ""))
    loc = f" [{city}, {cc}]" if cc and city else (f" [{cc}]" if cc else "")
    tag = f" — {klass}: {org[:40]}" if org else ""
    return f"{ip}{loc}{tag}"


def build_digest(
    *,
    period: str,
    as_of: dt.date,
    days: dict[str, dict],
    detail: dict[str, dict],
    log_days: set[str],
    generated_at: str,
) -> dict:
    cur_w, prev_w = windows(period, as_of)
    cur = aggregate(days, cur_w)
    prev = aggregate(days, prev_w)
    cur_days = days_in(cur_w)
    logs_cover_window = all(d in log_days for d in cur_days)

    # Window-level detail (needs IPs; only for days the fresh pull covers).
    human_ips: set[str] = set()
    ip_hits: Counter = Counter()
    ip_4xx: Counter = Counter()
    ip_probe: Counter = Counter()
    ip_meta: dict = {}
    probe_paths: Counter = Counter()
    status: Counter = Counter()
    endpoints: Counter = Counter()
    ua_families: Counter = Counter()
    for day in cur_days:
        x = detail.get(day)
        if not x:
            continue
        human_ips |= x["human_ips"]
        ip_hits.update(x["ip_hits"])
        ip_4xx.update(x["ip_4xx"])
        ip_probe.update(x["ip_probe"])
        ip_meta.update(x["ip_meta"])
        probe_paths.update(x["probe_paths"])
        status.update(x["status"])
        endpoints.update(x["endpoints"])
        ua_families.update(x["ua_families"])
    prev_probe_ips: set[str] = set()
    for day in days_in(prev_w):
        x = detail.get(day)
        if x:
            prev_probe_ips |= set(x["ip_probe"])

    daily = [
        {
            "date": d,
            "views": days[d]["views"] if d in days else None,
            "bot": days[d]["bot"] if d in days else None,
            "visitor_days": days[d]["visitor_days"] if d in days else None,
        }
        for d in cur_days
    ]

    def paired(key: str, n: int) -> list[dict]:
        rows = []
        for name, cnt in _top(cur[key], n):
            rows.append({"name": name, "current": cnt, "prior": prev[key].get(name, 0)})
        return rows

    bot_share = (
        round(100.0 * cur["bot"] / (cur["bot"] + cur["views"])) if cur["bot"] + cur["views"] else 0
    )
    # Unequal coverage (a prior month with 5 days of history vs a full current
    # month) makes a total-vs-total delta a lie; compare per-day rates then.
    same_coverage = (
        cur["days_covered"] == cur["days_total"] and prev["days_covered"] == prev["days_total"]
    )
    delta_basis = "total" if same_coverage else "per-day"

    def head_delta(key: str) -> str:
        if same_coverage:
            return delta(cur[key], prev[key])
        if not cur["days_covered"] or not prev["days_covered"]:
            return delta(cur[key], prev[key])
        return delta(round(cur[key] / cur["days_covered"]), round(prev[key] / prev["days_covered"]))

    human_total = sum(cur["net_class"].values())
    network = []
    for k in ("business", "isp", "hosting", "unknown"):
        n = cur["net_class"].get(k, 0)
        if n or prev["net_class"].get(k, 0):
            network.append(
                {
                    "class": k,
                    "label": CLASS_LABEL[k],
                    "current": n,
                    "prior": prev["net_class"].get(k, 0),
                    "share": round(100.0 * n / human_total) if human_total else 0,
                }
            )

    flagged = [
        {"ip": ip, "label": _ip_label(ip, ip_meta), "n_4xx": n, "probes": ip_probe.get(ip, 0)}
        for ip, n in ip_4xx.most_common(15)
        if n >= 3
    ]
    new_scanners = sorted(
        (ip for ip in ip_probe if ip not in prev_probe_ips), key=lambda i: -ip_probe[i]
    )[:10]

    return {
        "period": period,
        "generated_at": generated_at,
        "window": {"start": cur_w[0].isoformat(), "end": cur_w[1].isoformat()},
        "prior_window": {"start": prev_w[0].isoformat(), "end": prev_w[1].isoformat()},
        "coverage": {
            "current_days": cur["days_covered"],
            "current_days_total": cur["days_total"],
            "prior_days": prev["days_covered"],
            "prior_days_total": prev["days_total"],
            "logs_cover_window": logs_cover_window,
        },
        "headline": {
            "delta_basis": delta_basis,
            "views": cur["views"],
            "views_prior": prev["views"],
            "views_delta": head_delta("views"),
            "visitor_days": cur["visitor_days"],
            "visitor_days_prior": prev["visitor_days"],
            "visitor_days_delta": head_delta("visitor_days"),
            "distinct_visitors": len(human_ips) if logs_cover_window else None,
            "requests": cur["requests"],
            "requests_prior": prev["requests"],
            "requests_delta": head_delta("requests"),
            "bot": cur["bot"],
            "bot_share": bot_share,
            "views_per_day": round(cur["views"] / cur["days_covered"], 1)
            if cur["days_covered"]
            else 0,
            "views_per_day_prior": (
                round(prev["views"] / prev["days_covered"], 1) if prev["days_covered"] else 0
            ),
            "busiest_day": max(
                ((d["date"], d["views"]) for d in daily if d["views"] is not None),
                key=lambda t: t[1],
                default=None,
            ),
        },
        "daily": daily,
        "countries": paired("countries", 15),
        "network": network,
        "orgs": paired("orgs", 15),
        "surfaces": paired("surfaces", 15),
        "referrers": paired("referrers", 15),
        "security": {
            "probes": cur["probes"],
            "probes_prior": prev["probes"],
            "probe_clients": len(ip_probe) if logs_cover_window else cur["probe_clients"],
            "probe_paths": [{"path": p, "n": n} for p, n in probe_paths.most_common(12)],
            "flagged": flagged,
            "new_scanners": [
                {"ip": ip, "label": _ip_label(ip, ip_meta), "probes": ip_probe[ip]}
                for ip in new_scanners
            ],
            "status_4xx": cur["status_4xx"],
            "status_5xx": cur["status_5xx"],
            "status_5xx_prior": prev["status_5xx"],
            "status_mix": [{"status": s, "n": n} for s, n in status.most_common()],
        },
        "top_sources": [
            {"ip": ip, "label": _ip_label(ip, ip_meta), "n": n} for ip, n in ip_hits.most_common(15)
        ],
        "top_endpoints": [{"path": p, "n": n} for p, n in endpoints.most_common(15)],
        "ua_families": [{"family": f, "n": n} for f, n in ua_families.most_common(10)],
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _period_title(d: dict) -> str:
    w = d["window"]
    if d["period"] == "monthly":
        return dt.date.fromisoformat(w["start"]).strftime("%B %Y")
    return f"week {w['start']} → {w['end']}"


def subject(d: dict) -> str:
    h = d["headline"]
    return (
        f"insider-intel traffic · {_period_title(d)} · "
        f"{h['views']} page views ({h['views_delta']} vs prior)"
    )


def _coverage_note(d: dict) -> str:
    c = d["coverage"]
    parts = []
    if c["current_days"] < c["current_days_total"]:
        parts.append(
            f"only {c['current_days']} of {c['current_days_total']} days in this period have data"
        )
    if c["prior_days"] == 0:
        parts.append(
            "no prior-period data yet, so deltas read 'new' — they fill in as the history accrues"
        )
    elif c["prior_days"] < c["prior_days_total"]:
        parts.append(
            f"the prior period has {c['prior_days']} of {c['prior_days_total']} days, "
            "so the headline deltas compare per-day rates"
        )
    if not c["logs_cover_window"]:
        parts.append(
            "part of this period comes from the stored daily history (counts only), so the "
            "visitor, source-IP and scanner sections cover just the days inside the log window"
        )
    return "; ".join(parts)


def render_markdown(d: dict) -> str:
    h = d["headline"]
    s = d["security"]
    L = [f"# insider-intel traffic digest — {_period_title(d)}", ""]
    L.append(
        f"Period {d['window']['start']} → {d['window']['end']} · prior "
        f"{d['prior_window']['start']} → {d['prior_window']['end']} · generated {d['generated_at']}"
    )
    note = _coverage_note(d)
    if note:
        L.append(f"Coverage: {note}.")
    L += ["", "## Bottom line", ""]
    basis = " per day" if h["delta_basis"] == "per-day" else ""
    L.append(
        f"- Page views: **{h['views']}** ({h['views_delta']}{basis} vs {h['views_prior']} prior) · "
        f"{h['views_per_day']}/day (prior {h['views_per_day_prior']}/day)"
    )
    L.append(
        f"- Visitor-days (distinct addresses per day, summed): **{h['visitor_days']}** "
        f"({h['visitor_days_delta']}{basis} vs {h['visitor_days_prior']})"
    )
    if h["distinct_visitors"] is not None:
        L.append(f"- Distinct visitor addresses over the period: **{h['distinct_visitors']}**")
    L.append(
        f"- API requests: {h['requests']} ({h['requests_delta']}) · "
        f"bot/script share of boot probes: {h['bot_share']}%"
    )
    if h["busiest_day"]:
        L.append(f"- Busiest day: {h['busiest_day'][0]} ({h['busiest_day'][1]} views)")
    L.append(
        f"- Scanner probes: {s['probes']} ({delta(s['probes'], s['probes_prior'])}) from "
        f"{s['probe_clients']} client(s) · 5xx responses: {s['status_5xx']} "
        f"({delta(s['status_5xx'], s['status_5xx_prior'])})"
    )
    L += ["", "## Page views per day", ""]
    for row in d["daily"]:
        if row["views"] is None:
            L.append(f"- {row['date']}: no data")
        else:
            L.append(
                f"- {row['date']}: {row['views']} views · {row['visitor_days']} visitors · "
                f"{row['bot']} bot/script"
            )

    def pair_section(title: str, rows: list[dict], empty: str) -> None:
        L.extend(["", f"## {title}", ""])
        if not rows:
            L.append(f"- {empty}")
            return
        for r in rows:
            L.append(
                f"- {r['current']:5d}  {r['name']}  "
                f"(prior {r['prior']}, {delta(r['current'], r['prior'])})"
            )

    pair_section("Where visitors came from (country)", d["countries"], "no geolocated views")
    L.extend(["", "## Human page loads by network type", ""])
    L.append(
        "(network type is suggestive of visitor kind, never employer: "
        "a corporate visitor on mobile reads as ISP)"
    )
    for r in d["network"]:
        L.append(f"- {r['current']:5d}  {r['share']:3d}%  {r['label']}  (prior {r['prior']})")
    pair_section("Business / org networks among human visitors", d["orgs"], "none matched")
    pair_section("What they read (API calls by product surface)", d["surfaces"], "no surface calls")
    pair_section(
        "External referrers", d["referrers"], "none recorded (direct visits / referrer stripped)"
    )
    L.extend(["", "## Security", ""])
    if s["probe_paths"]:
        L.append("Probe paths hit:")
        for r in s["probe_paths"]:
            L.append(f"- {r['n']:4d}  {r['path']}")
    else:
        L.append("- no probe paths hit")
    L.append("")
    L.append("4xx-heavy clients (3+ 4xx responses):")
    if s["flagged"]:
        for r in s["flagged"]:
            L.append(f"- {r['n_4xx']:4d} 4xx · {r['probes']} probes  {r['label']}")
    else:
        L.append("- none")
    L.append("")
    L.append("Scanner clients not seen in the prior period:")
    if s["new_scanners"]:
        for r in s["new_scanners"]:
            L.append(f"- {r['probes']:4d} probes  {r['label']}")
    else:
        L.append("- none")
    L.append("")
    L.append(
        "Response status mix: " + ", ".join(f"{r['status']}×{r['n']}" for r in s["status_mix"])
    )
    L.extend(["", "## Top source addresses", ""])
    for r in d["top_sources"]:
        L.append(f"- {r['n']:5d}  {r['label']}")
    L.extend(["", "## Top endpoints", ""])
    for r in d["top_endpoints"]:
        L.append(f"- {r['n']:6d}  {r['path']}")
    L.extend(["", "## Browser families (human boot probes)", ""])
    for r in d["ua_families"]:
        L.append(f"- {r['n']:5d}  {r['family']}")
    L.extend(["", "## How this is counted", ""])
    L.append(METHOD_NOTE)
    return "\n".join(L) + "\n"


def _h(x) -> str:
    return html.escape(str(x))


def render_html(d: dict) -> str:
    h = d["headline"]
    s = d["security"]
    font = "font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;"
    mono = "font-family:SFMono-Regular,Menlo,Consolas,monospace;"
    th = (
        'style="text-align:left;padding:4px 8px;border-bottom:1px solid #ccc;'
        f'font-size:12px;color:#555;{font}"'
    )
    td = f'style="padding:4px 8px;border-bottom:1px solid #eee;font-size:13px;{font}"'
    thn = th.replace("text-align:left", "text-align:right")
    tdn = (
        'style="padding:4px 8px;border-bottom:1px solid #eee;font-size:13px;'
        f'text-align:right;{mono}"'
    )
    h2 = f'style="font-size:15px;margin:22px 0 6px;{font}"'
    p = f'style="font-size:13px;color:#444;margin:0 0 8px;{font}"'

    def table(headers: list[str], rows: list[list], numeric: set[int]) -> str:
        out = [TABLE_OPEN]
        out.append(
            "<tr>"
            + "".join(
                f"<th {thn if i in numeric else th}>{_h(x)}</th>" for i, x in enumerate(headers)
            )
            + "</tr>"
        )
        for r in rows:
            out.append(
                "<tr>"
                + "".join(
                    f"<td {tdn if i in numeric else td}>{_h(x)}</td>" for i, x in enumerate(r)
                )
                + "</tr>"
            )
        out.append("</table>")
        return "\n".join(out)

    def pair_table(rows: list[dict], label: str) -> str:
        if not rows:
            return f"<p {p}>none recorded</p>"
        return table(
            [label, "this period", "prior", "change"],
            [[r["name"], r["current"], r["prior"], delta(r["current"], r["prior"])] for r in rows],
            {1, 2, 3},
        )

    tile = (
        f'<td style="padding:10px 14px;border:1px solid #ddd;border-radius:6px;{font}">'
        '<div style="font-size:11px;color:#666;text-transform:uppercase;'
        'letter-spacing:.04em">{k}</div>'
        f'<div style="font-size:22px;{mono}">{{v}}</div>'
        '<div style="font-size:12px;color:#666">{sub}</div></td>'
    )
    basis = " per day" if h["delta_basis"] == "per-day" else ""
    tiles = [
        tile.format(
            k="page views",
            v=h["views"],
            sub=f"{h['views_delta']}{basis} vs {h['views_prior']} prior",
        ),
        tile.format(
            k="visitor-days",
            v=h["visitor_days"],
            sub=f"{h['visitor_days_delta']}{basis} vs {h['visitor_days_prior']}",
        ),
        tile.format(k="views / day", v=h["views_per_day"], sub=f"prior {h['views_per_day_prior']}"),
        tile.format(k="scanner probes", v=s["probes"], sub=f"{s['probe_clients']} client(s)"),
    ]
    note = _coverage_note(d)
    max_views = max((r["views"] or 0) for r in d["daily"]) or 1
    daily_rows = []
    for r in d["daily"]:
        if r["views"] is None:
            daily_rows.append(
                f"<tr><td {td}>{r['date']}</td><td {tdn}>—</td>"
                f'<td {td} colspan="2">no data</td></tr>'
            )
            continue
        w = int(round(160 * r["views"] / max_views))
        bar = (
            f'<span style="display:inline-block;height:10px;width:{w}px;background:#2b6cb0"></span>'
        )
        daily_rows.append(
            f"<tr><td {td}>{r['date']}</td><td {tdn}>{r['views']}</td>"
            f"<td {td}>{bar}</td><td {tdn}>{r['visitor_days']} visitors · {r['bot']} bot</td></tr>"
        )

    parts = [
        '<!doctype html><html><body style="margin:0;padding:16px;background:#fff;color:#111">',
        f'<div style="max-width:720px;margin:0 auto;{font}">',
        f'<h1 style="font-size:18px;margin:0 0 4px;{font}">'
        f"insider-intel traffic digest — {_h(_period_title(d))}</h1>",
        f"<p {p}>Period {d['window']['start']} → {d['window']['end']} · prior "
        f"{d['prior_window']['start']} → {d['prior_window']['end']} · "
        f"generated {_h(d['generated_at'])}</p>",
    ]
    if note:
        parts.append(f"<p {p}><b>Coverage:</b> {_h(note)}.</p>")
    parts.append(
        '<table cellspacing="6" cellpadding="0" '
        'style="border-collapse:separate;width:100%;max-width:720px"><tr>'
    )
    parts.extend(tiles)
    parts.append("</tr></table>")
    extra = []
    if h["distinct_visitors"] is not None:
        extra.append(f"{h['distinct_visitors']} distinct visitor addresses over the period")
    extra.append(f"{h['requests']} API requests ({h['requests_delta']})")
    extra.append(f"bot/script share of boot probes {h['bot_share']}%")
    if h["busiest_day"]:
        extra.append(f"busiest day {h['busiest_day'][0]} with {h['busiest_day'][1]} views")
    extra.append(
        f"5xx responses {s['status_5xx']} ({delta(s['status_5xx'], s['status_5xx_prior'])})"
    )
    parts.append(f"<p {p}>" + _h(" · ".join(extra)) + "</p>")

    parts.append(f"<h2 {h2}>Page views per day</h2>")
    parts.append(TABLE_OPEN)
    parts.extend(daily_rows)
    parts.append("</table>")

    parts.append(f"<h2 {h2}>Where visitors came from</h2>")
    parts.append(pair_table(d["countries"], "country"))
    parts.append(f"<h2 {h2}>Human page loads by network type</h2>")
    parts.append(
        f"<p {p}>Network type is suggestive of visitor kind, never employer: "
        "a corporate visitor on mobile reads as ISP.</p>"
    )
    parts.append(
        table(
            ["network type", "this period", "share", "prior"],
            [[r["label"], r["current"], f"{r['share']}%", r["prior"]] for r in d["network"]],
            {1, 2, 3},
        )
        if d["network"]
        else f"<p {p}>no human page loads</p>"
    )
    parts.append(f"<h2 {h2}>Business / org networks among human visitors</h2>")
    parts.append(pair_table(d["orgs"], "network org"))
    parts.append(f"<h2 {h2}>What they read</h2>")
    parts.append(f"<p {p}>API calls grouped by the product surface that makes them.</p>")
    parts.append(pair_table(d["surfaces"], "surface"))
    parts.append(f"<h2 {h2}>External referrers</h2>")
    parts.append(pair_table(d["referrers"], "referrer"))

    parts.append(f"<h2 {h2}>Security</h2>")
    parts.append(
        f"<p {p}>{s['probes']} scanner probes "
        f"({delta(s['probes'], s['probes_prior'])} vs prior) from "
        f"{s['probe_clients']} client(s) · {s['status_4xx']} 4xx · {s['status_5xx']} 5xx.</p>"
    )
    if s["probe_paths"]:
        parts.append(
            table(["probe path", "hits"], [[r["path"], r["n"]] for r in s["probe_paths"]], {1})
        )
    parts.append(f"<p {p}><b>4xx-heavy clients</b> (3+ 4xx responses)</p>")
    parts.append(
        table(
            ["client", "4xx", "probes"],
            [[r["label"], r["n_4xx"], r["probes"]] for r in s["flagged"]],
            {1, 2},
        )
        if s["flagged"]
        else f"<p {p}>none</p>"
    )
    parts.append(f"<p {p}><b>Scanner clients not seen in the prior period</b></p>")
    parts.append(
        table(["client", "probes"], [[r["label"], r["probes"]] for r in s["new_scanners"]], {1})
        if s["new_scanners"]
        else f"<p {p}>none</p>"
    )
    parts.append(
        f"<p {p}>Status mix: "
        + _h(", ".join(f"{r['status']}×{r['n']}" for r in s["status_mix"]))
        + "</p>"
    )

    parts.append(f"<h2 {h2}>Top source addresses</h2>")
    parts.append(
        table(["address", "requests"], [[r["label"], r["n"]] for r in d["top_sources"]], {1})
    )
    parts.append(f"<h2 {h2}>Top endpoints</h2>")
    parts.append(
        table(["endpoint", "requests"], [[r["path"], r["n"]] for r in d["top_endpoints"]], {1})
    )
    parts.append(f"<h2 {h2}>Browser families</h2>")
    parts.append(
        table(["family", "boot probes"], [[r["family"], r["n"]] for r in d["ua_families"]], {1})
    )

    parts.append(f"<h2 {h2}>How this is counted</h2>")
    parts.append(f"<p {p}>{_h(METHOD_NOTE)}</p>")
    parts.append("</div></body></html>")
    return "\n".join(parts)


def summary_line(d: dict) -> str:
    """The ONE line the public job log may carry: counts only."""
    h = d["headline"]
    s = d["security"]
    c = d["coverage"]
    return (
        f"digest {d['period']} {d['window']['start']}→{d['window']['end']}: "
        f"{h['views']} page views ({h['views_delta']}), {h['visitor_days']} visitor-days, "
        f"{h['requests']} requests, {h['bot']} bot/script, {len(d['countries'])} countries, "
        f"{s['probes']} probes from {s['probe_clients']} client(s), {s['status_5xx']} 5xx · "
        f"coverage {c['current_days']}/{c['current_days_total']} days, "
        f"prior {c['prior_days']}/{c['prior_days_total']}"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("requests_json", help="gcloud logging read --format json output")
    ap.add_argument("--period", choices=("weekly", "monthly"), default="weekly")
    ap.add_argument("--as-of", help="UTC date the run is anchored to (default: today)")
    ap.add_argument(
        "--log-days", type=int, default=32, help="days the log pull covered (--freshness)"
    )
    ap.add_argument("--geoip", help="DB-IP city-lite CSV")
    ap.add_argument("--asn", help="DB-IP ASN-lite CSV")
    ap.add_argument("--history", help="traffic-history.json to read and rewrite")
    ap.add_argument("--out-md", required=True)
    ap.add_argument("--out-html", required=True)
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--out-subject", help="write the email subject line here")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    a = _parse_args(argv)
    as_of = dt.date.fromisoformat(a.as_of) if a.as_of else dt.datetime.now(dt.UTC).date()
    generated_at = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    entries = json.loads(Path(a.requests_json).read_text(encoding="utf-8"))
    geo, asn = load_geo(a.geoip), load_asn(a.asn)
    fresh, detail = parse_entries(entries, geo, asn)
    # The pull covers [as_of - log_days, as_of); today is partial and never
    # inside a window, but it still overwrites history so a re-run tomorrow
    # sees yesterday's full day.
    log_days = {(as_of - dt.timedelta(days=i)).isoformat() for i in range(0, a.log_days + 1)}
    history = load_history(a.history)
    merged = merge_history(history, fresh, log_days)
    digest = build_digest(
        period=a.period,
        as_of=as_of,
        days=merged,
        detail=detail,
        log_days=log_days,
        generated_at=generated_at,
    )
    Path(a.out_md).write_text(render_markdown(digest), encoding="utf-8")
    Path(a.out_html).write_text(render_html(digest), encoding="utf-8")
    Path(a.out_json).write_text(json.dumps(digest, indent=1, sort_keys=True), encoding="utf-8")
    if a.history:
        Path(a.history).write_text(
            json.dumps(dump_history(merged, generated_at), indent=1), encoding="utf-8"
        )
    if a.out_subject:
        Path(a.out_subject).write_text(subject(digest) + "\n", encoding="utf-8")
    print(f"geoip ranges: {len(geo)} · asn ranges: {len(asn)} · log entries: {len(entries)}")
    print(summary_line(digest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
