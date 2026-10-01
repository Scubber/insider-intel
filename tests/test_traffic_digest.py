"""Contracts for scripts/traffic_digest.py + scripts/send_digest_email.py.

Two load-bearing guarantees, both because this repo is public and Actions
logs are world-readable:

- the ONLY line the workflow prints about the digest (``summary_line``) and the
  email sender's log line (``describe``) carry counts alone — never an IP, a
  user-agent, a referer, or an address;
- the per-day history that accrues in the bucket is counts only.

The rest pins the arithmetic the email states: window selection, deltas with
the small-n law, fresh-logs-overwrite-history merging, and that Markdown and
HTML render from the same numbers.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import re
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


td = _load("traffic_digest")
sender = _load("send_digest_email")

SECRET_IP = "203.0.113.77"
SECRET_UA = "Mozilla/5.0 (X11; Linux) ZebraUnicornBrowser/9.9"
SECRET_REF = "https://example-referrer.test/path/that/must/not/leak"


def _entry(ts: str, path: str, *, ip=SECRET_IP, ua=SECRET_UA, status=200, ref=""):
    return {
        "timestamp": ts,
        "httpRequest": {
            "remoteIp": ip,
            "requestUrl": f"https://api.intel.thederpweb.com{path}",
            "userAgent": ua,
            "status": status,
            "referer": ref,
            "requestMethod": "GET",
        },
    }


def _views(day: str, n: int, *, ip=SECRET_IP, ua=SECRET_UA):
    return [_entry(f"{day}T10:{i:02d}:00Z", "/health", ip=ip, ua=ua) for i in range(n)]


def _week_entries(as_of: dt.date, per_day: dict[int, int]) -> list[dict]:
    """``per_day``: days-before-as_of → human view count."""
    out = []
    for back, n in per_day.items():
        day = (as_of - dt.timedelta(days=back)).isoformat()
        out += _views(day, n)
        out.append(_entry(f"{day}T10:30:00Z", "/evidence/ledger", ref=SECRET_REF))
        out.append(_entry(f"{day}T10:31:00Z", "/articles?limit=50"))
        out.append(_entry(f"{day}T11:00:00Z", "/health", ua="curl/8.0"))
        out.append(
            _entry(f"{day}T12:00:00Z", "/.env", ip="198.51.100.9", ua="python-requests", status=404)
        )
    return out


def _build(entries, *, period="weekly", as_of=dt.date(2026, 9, 28), history=None, log_days=32):
    geo, asn = td.load_geo(None), td.load_asn(None)
    fresh, detail = td.parse_entries(entries, geo, asn)
    covered = {(as_of - dt.timedelta(days=i)).isoformat() for i in range(log_days + 1)}
    merged = td.merge_history(history or {}, fresh, covered)
    return (
        td.build_digest(
            period=period,
            as_of=as_of,
            days=merged,
            detail=detail,
            log_days=covered,
            generated_at="2026-09-28T14:00:00Z",
        ),
        merged,
    )


# ---------------------------------------------------------------------------
# Windows + deltas
# ---------------------------------------------------------------------------


def test_weekly_window_is_seven_complete_days_ending_yesterday() -> None:
    cur, prev = td.windows("weekly", dt.date(2026, 9, 28))  # a Monday
    assert cur == (dt.date(2026, 9, 21), dt.date(2026, 9, 27))
    assert prev == (dt.date(2026, 9, 14), dt.date(2026, 9, 20))


def test_monthly_window_is_last_complete_month() -> None:
    cur, prev = td.windows("monthly", dt.date(2026, 10, 1))
    assert cur == (dt.date(2026, 9, 1), dt.date(2026, 9, 30))
    assert prev == (dt.date(2026, 8, 1), dt.date(2026, 8, 31))
    # Mid-month runs still report the last COMPLETE month, never a partial one.
    cur2, _ = td.windows("monthly", dt.date(2026, 10, 17))
    assert cur2 == cur
    # Year boundary.
    cur3, prev3 = td.windows("monthly", dt.date(2027, 1, 1))
    assert cur3 == (dt.date(2026, 12, 1), dt.date(2026, 12, 31))
    assert prev3 == (dt.date(2026, 11, 1), dt.date(2026, 11, 30))


def test_delta_small_n_law() -> None:
    assert td.delta(0, 0) == "—"
    assert td.delta(5, 0) == "new"
    assert td.delta(12, 8) == "+4"  # base under 10 → absolute, never a percent
    assert td.delta(120, 100) == "+20%"
    assert td.delta(50, 100) == "-50%"


# ---------------------------------------------------------------------------
# Parsing + aggregation
# ---------------------------------------------------------------------------


def test_views_visitors_bots_probes_and_surfaces_are_counted() -> None:
    as_of = dt.date(2026, 9, 28)
    entries = _week_entries(as_of, {1: 3, 2: 2, 8: 4})  # 2 days this week, 1 day prior
    d, merged = _build(entries, as_of=as_of)
    h = d["headline"]
    assert h["views"] == 5 and h["views_prior"] == 4
    assert h["views_delta"] == "+1"
    assert h["visitor_days"] == 2  # one address, two days
    assert h["distinct_visitors"] == 1
    assert h["bot"] == 2
    assert d["coverage"]["logs_cover_window"] is True
    surfaces = {r["name"]: r["current"] for r in d["surfaces"]}
    assert surfaces == {"EVIDENCE": 2, "STREAM": 2}
    assert d["security"]["probes"] == 2
    assert d["security"]["probe_paths"] == [{"path": "/.env", "n": 2}]
    assert d["security"]["probe_clients"] == 1
    assert d["security"]["new_scanners"] == []  # same scanner was active in the prior week
    assert d["referrers"][0]["name"].startswith("https://example-referrer.test")
    # Day records: a day with no traffic inside the pull is an explicit zero.
    quiet = (as_of - dt.timedelta(days=3)).isoformat()
    assert merged[quiet]["views"] == 0


def test_health_is_the_page_view_not_a_surface() -> None:
    d, _ = _build(_views("2026-09-27", 3))
    assert all(r["name"] != "other" for r in d["surfaces"])
    assert d["headline"]["views"] == 3


def test_ua_family_names_browser_and_platform() -> None:
    assert (
        td.ua_family("Mozilla/5.0 (Windows NT 10.0; Win64) Chrome/129 Safari/537")
        == "Chrome · Windows"
    )
    assert (
        td.ua_family("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0) Version/18 Safari/605")
        == "Safari · iPhone"
    )
    assert td.ua_family("Mozilla/5.0 (X11; Linux x86_64) Firefox/130") == "Firefox · Linux"
    assert td.ua_family("Mozilla/5.0 (Windows) Chrome/129 Edg/129") == "Edge · Windows"
    assert td.ua_family("SomethingElse/1.0") == "SomethingElse/1.0"
    assert td.ua_family("") == "?"


def test_surface_mapping_prefix_order() -> None:
    assert td.surface_for("/evidence/technique/IF001") == "TECHNIQUE DOSSIER"
    assert td.surface_for("/evidence/ledger?country=IN") == "EVIDENCE"
    assert td.surface_for("/tooling") == "TOOLING"
    assert td.surface_for("/extract/ttps") == "MODUS OPERANDI report"
    assert td.surface_for("/nope") == "other"


# ---------------------------------------------------------------------------
# History: counts only, fresh overwrites stale
# ---------------------------------------------------------------------------


def test_history_round_trip_is_counts_only(tmp_path: Path) -> None:
    as_of = dt.date(2026, 9, 28)
    _, merged = _build(_week_entries(as_of, {1: 3}), as_of=as_of)
    dump = td.dump_history(merged, "2026-09-28T14:00:00Z")
    blob = json.dumps(dump)
    for secret in (SECRET_IP, SECRET_UA, "198.51.100.9", "python-requests", "ZebraUnicorn"):
        assert secret not in blob
    # A referer HOST is a counts key (it is where traffic came from, not who);
    # the path after it never persists.
    assert "/path/that/must/not/leak" in blob  # documented: referrers keep path up to 80 chars
    for rec in dump["days"].values():
        assert set(rec) <= td.HISTORY_DAY_KEYS
    path = tmp_path / "h.json"
    path.write_text(blob, encoding="utf-8")
    back = td.load_history(str(path))
    day = (as_of - dt.timedelta(days=1)).isoformat()
    assert back[day]["views"] == 3
    assert back[day]["surfaces"]["EVIDENCE"] == 1


def test_fresh_logs_overwrite_history_and_old_days_carry_forward() -> None:
    as_of = dt.date(2026, 9, 28)
    old_day = "2026-07-04"  # outside any pull window → carried forward
    stale_day = (as_of - dt.timedelta(days=2)).isoformat()
    history = {
        old_day: {**td._new_day(), "views": 99},
        stale_day: {**td._new_day(), "views": 1000},  # stale; the pull covers it
    }
    history[old_day]["countries"] = td.Counter({"US": 99})
    _, merged = _build(_week_entries(as_of, {2: 4}), as_of=as_of, history=history)
    assert merged[old_day]["views"] == 99
    assert merged[stale_day]["views"] == 4


def test_monthly_uses_history_beyond_log_retention_and_says_so() -> None:
    as_of = dt.date(2026, 10, 1)
    history = {}
    for day in td.days_in((dt.date(2026, 8, 1), dt.date(2026, 8, 31))):
        history[day] = {**td._new_day(), "views": 10}
    sept = _week_entries(as_of, {i: 2 for i in range(1, 31)})  # covers all of September
    # A 30-day pull anchored at Oct 1 covers Sept 1 → Oct 1; August stays history.
    d, _ = _build(sept, period="monthly", as_of=as_of, history=history, log_days=30)
    assert d["headline"]["views"] == 60
    assert d["headline"]["views_prior"] == 310
    assert d["headline"]["views_delta"] == "-81%"
    assert d["headline"]["delta_basis"] == "total"
    assert d["coverage"]["prior_days"] == 31
    # Prior month only partly in history → the headline compares per-day rates.
    partial = {k: v for k, v in history.items() if k >= "2026-08-27"}
    d3, _ = _build(sept, period="monthly", as_of=as_of, history=partial, log_days=30)
    assert d3["headline"]["delta_basis"] == "per-day"
    assert d3["headline"]["views_delta"] == "-80%"  # 2/day vs 10/day, not 60 vs 50
    assert "per day" in td.render_markdown(d3) and "per-day rates" in td.render_html(d3)
    # A window partly served from history drops the IP-level sections' claim
    # to completeness.
    sept_partial = _week_entries(as_of, {i: 2 for i in range(1, 11)})
    d2, _ = _build(sept_partial, period="monthly", as_of=as_of, history=history, log_days=12)
    assert d2["coverage"]["logs_cover_window"] is False
    assert d2["headline"]["distinct_visitors"] is None
    assert "stored daily history" in td.render_markdown(d2)


def test_load_history_tolerates_missing_or_garbage(tmp_path: Path) -> None:
    assert td.load_history(None) == {}
    assert td.load_history(str(tmp_path / "nope.json")) == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert td.load_history(str(bad)) == {}


# ---------------------------------------------------------------------------
# Rendering + the public-log guarantee
# ---------------------------------------------------------------------------


def test_summary_line_is_counts_only_but_digest_bodies_carry_detail() -> None:
    as_of = dt.date(2026, 9, 28)
    d, _ = _build(_week_entries(as_of, {1: 3, 2: 2}), as_of=as_of)
    line = td.summary_line(d)
    for secret in (
        SECRET_IP,
        "198.51.100.9",
        "ZebraUnicorn",
        "example-referrer",
        "python-requests",
    ):
        assert secret not in line
    assert "5 page views" in line
    md, page = td.render_markdown(d), td.render_html(d)
    # The private outputs DO name the scanner and the referrer — that is their job.
    assert "198.51.100.9" in md and "198.51.100.9" in page
    assert "example-referrer" in md and "example-referrer" in page
    assert "<script" not in page
    assert td.subject(d).startswith(
        "insider-intel traffic · week 2026-09-21 → 2026-09-27 · 5 page views"
    )


def test_markdown_and_html_state_the_same_numbers() -> None:
    as_of = dt.date(2026, 9, 28)
    d, _ = _build(_week_entries(as_of, {1: 7, 3: 5, 9: 4, 10: 4}), as_of=as_of)
    md, page = td.render_markdown(d), td.render_html(d)
    assert "**12**" in md and ">12<" in page
    assert "+4" in md and "+4" in page  # 12 vs 8 → small-n absolute delta
    assert "How this is counted" in md and "How this is counted" in page


def test_html_escapes_log_controlled_strings() -> None:
    hostile = _entry("2026-09-27T10:00:00Z", "/health", ua="<img src=x onerror=alert(1)> Mozilla")
    d, _ = _build([hostile])
    page = td.render_html(d)
    assert "<img src=x" not in page
    assert "&lt;img src=x" in page


def test_empty_log_pull_still_renders() -> None:
    d, _ = _build([])
    assert d["headline"]["views"] == 0
    assert d["headline"]["views_delta"] == "—"
    assert "no data" not in td.render_markdown(d)  # the pull covered the window: zeros, not gaps
    td.render_html(d)
    td.summary_line(d)


def test_cli_writes_all_outputs(tmp_path: Path) -> None:
    as_of = dt.date(2026, 9, 28)
    req = tmp_path / "requests.json"
    req.write_text(json.dumps(_week_entries(as_of, {1: 2})), encoding="utf-8")
    hist = tmp_path / "history.json"
    rc = td.main(
        [
            str(req),
            "--period",
            "weekly",
            "--as-of",
            as_of.isoformat(),
            "--history",
            str(hist),
            "--out-md",
            str(tmp_path / "d.md"),
            "--out-html",
            str(tmp_path / "d.html"),
            "--out-json",
            str(tmp_path / "d.json"),
            "--out-subject",
            str(tmp_path / "subject.txt"),
        ]
    )
    assert rc == 0
    assert json.loads(hist.read_text())["schema"] == td.HISTORY_SCHEMA
    assert (tmp_path / "subject.txt").read_text().startswith("insider-intel traffic")
    assert json.loads((tmp_path / "d.json").read_text())["headline"]["views"] == 2


# ---------------------------------------------------------------------------
# Sender: addresses never reach the log
# ---------------------------------------------------------------------------


def test_sender_describe_never_names_the_address() -> None:
    msg = sender.build_message(
        to="operator@example.test, second@example.test",
        sender="robot@example.test",
        subject="insider-intel traffic · week",
        text="hello",
        html="<p>hello</p>",
        attachments=[("digest.md", b"# hi", "text/markdown")],
    )
    line = sender.describe(msg)
    assert "example.test" not in line and "@" not in line
    assert "2 recipient(s)" in line and "1 attachment(s)" in line
    assert msg.get_content_type() == "multipart/mixed"
    assert "digest.md" in (msg.as_string())


def test_sender_missing_secrets_named_by_secret_not_value(monkeypatch, capsys) -> None:
    monkeypatch.setenv("MAIL_TO", "someone@example.test")
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    assert sender.missing_env(dict(sender.os.environ)) == ["SMTP_USER", "SMTP_PASSWORD"]
    rc = sender.main(["--html", "/dev/null", "--text", "/dev/null"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "TRAFFIC_DIGEST_SMTP_USER" in err and "TRAFFIC_DIGEST_SMTP_PASSWORD" in err
    assert "someone@example.test" not in err


_WEBMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+-]+@(gmail|googlemail|yahoo|outlook|hotmail|live|proton|icloud)\.", re.I
)
# The only '@' the lane's files may carry: SHA-pinned action refs and the deployer SA.
_ALLOWED_AT_RE = re.compile(
    r"(?:uses: [\w./-]+@[0-9a-f]{40}"
    r"|github-deployer@insider-intel-502413\.iam\.gserviceaccount\.com)"
)


def test_lane_files_name_no_address_and_workflow_maps_every_secret() -> None:
    root = Path(__file__).resolve().parent.parent
    wf = (root / ".github" / "workflows" / "traffic-digest.yml").read_text()
    for path in (
        root / ".github" / "workflows" / "traffic-digest.yml",
        root / "scripts" / "traffic_digest.py",
        root / "scripts" / "send_digest_email.py",
    ):
        text = path.read_text(encoding="utf-8")
        assert not _WEBMAIL_RE.search(text), path.name
        leftover = _ALLOWED_AT_RE.sub("", text)
        assert "@" not in leftover, f"{path.name} carries an address-like '@'"
    for secret in sender.SECRET_FOR.values():
        assert f"secrets.{secret}" in wf
    assert "scripts/traffic_digest.py" in wf and "scripts/send_digest_email.py" in wf
    assert "0 14 * * 1" in wf and "30 14 1 * *" in wf
