"""Vendor case-mention scanner: alias-file contract + deterministic core.

Three contracts pinned here:

1. The checked-in alias map (shared/data/vendor_aliases.json) COVERS the
   tooling map: every vendor in every tooling_map.json ``examples`` array has
   exactly one entry per category home (or an explicit ``no_safe_alias``
   note), so a curated-examples edit that forgets the alias file fails CI.
2. Alias SAFETY: single common English words can never become aliases —
   single-token aliases must come from the file's documented allowlist of
   coined brand names, multi-word aliases carry a length floor, and a
   test-side denylist of court-prose collisions ("sterling", "purview",
   "hid", "group policy", …) guards both lists.
3. The scanner core (apps/search/vendor_mentions.py) is pure and
   deterministic: word-boundary, case-insensitive, whitespace-tolerant
   matching; DISTINCT case links (never occurrences); verdict-true split
   mirroring the ledger gate; longest-alias-first shadowing; and the
   attach step ranks vendors without ever touching category ranking fields
   (byte-identity is pinned in tests/test_tooling.py).
"""

from __future__ import annotations

from apps.search.tooling import load_tooling_map
from apps.search.vendor_mentions import (
    attach_vendor_mentions,
    compile_alias_matcher,
    load_vendor_aliases,
    scan_vendor_mentions,
)

# ── 1. Alias file ↔ tooling map contract ─────────────────────────────────────


def test_alias_file_covers_every_examples_vendor_exactly() -> None:
    """One alias entry per (category, vendor) examples pair — no gaps, no
    orphans, no duplicates."""
    expected = {
        (cat["id"], name) for cat in load_tooling_map()["categories"] for name in cat["examples"]
    }
    entries = [(v["category"], v["name"]) for v in load_vendor_aliases()["vendors"]]
    assert len(entries) == len(set(entries)), "duplicate (category, vendor) alias entries"
    assert set(entries) == expected, (
        f"alias file out of sync with tooling_map examples: "
        f"missing={expected - set(entries)} orphaned={set(entries) - expected}"
    )


# Court-prose collisions that must never match as a product mention: common
# English words (incl. legal boilerplate like 'purview', the verb 'encase',
# 'hid' under case-insensitive matching) and generic phrases with a
# non-product reading ('group policy' = group insurance policy, 'carbon
# black' = the industrial material, 'area 1' = a numbered facility zone,
# 'one identity' = 'used more than one identity', 'accurate background' =
# everyday screening prose).
FORBIDDEN_ALIASES = {
    "sterling",
    "archer",
    "relativity",
    "hid",
    "sans",
    "purview",
    "falcon",
    "sentinel",
    "canary",
    "defender",
    "chronicle",
    "obsidian",
    "elastic",
    "cortex",
    "magnet",
    "axiom",
    "encase",
    "abnormal",
    "first advantage",
    "group policy",
    "barracuda",
    "carbon black",
    "nightfall",
    "keeper",
    "duo",
    "vault",
    "area 1",
    "area 1 security",
    "one identity",
    "accurate background",
    "illusive",
    "smokescreen",
    "reveal",
    "disco",
    "diligent",
    "guardian",
    "awake",
    "endgame",
    "aperture",
    "harmony",
    "trend",
    "ping",
    "sumo",
    "oxygen",
    "safeguard",
    "skyhigh",
    "mvision",
    "apex one",
}


def test_alias_safety_rules() -> None:
    """The file's own documented rules, mechanically enforced: single-token
    aliases only from the allowlist; multi-word aliases >= 6 chars; nothing
    from the forbidden denylist; empty alias lists carry a no_safe_alias
    note (and only those do)."""
    data = load_vendor_aliases()
    allowlist = data["single_token_allowlist"]
    assert len(allowlist) == len(set(allowlist)), "duplicate allowlist tokens"
    allowed = {t.lower() for t in allowlist}
    for token in allowlist:
        assert " " not in token and len(token) >= 4, f"allowlist token too weak: {token!r}"
        assert token.lower() not in FORBIDDEN_ALIASES, (
            f"common English word in the single-token allowlist: {token!r}"
        )

    used_single_tokens: set[str] = set()
    for vendor in data["vendors"]:
        who = f"{vendor['category']}/{vendor['name']}"
        aliases = vendor["aliases"]
        assert len({a.lower() for a in aliases}) == len(aliases), f"{who}: duplicate aliases"
        if not aliases:
            note = str(vendor.get("no_safe_alias") or "").strip()
            assert note, f"{who}: empty alias list requires a no_safe_alias note"
            continue
        assert not vendor.get("no_safe_alias"), (
            f"{who}: no_safe_alias note on an entry that HAS aliases"
        )
        for alias in aliases:
            assert isinstance(alias, str) and alias.strip() == alias and alias, (
                f"{who}: malformed alias {alias!r}"
            )
            assert alias.lower() not in FORBIDDEN_ALIASES, (
                f"{who}: forbidden common-word alias {alias!r}"
            )
            if " " in alias:
                assert len(alias) >= 6, f"{who}: multi-word alias too short: {alias!r}"
            else:
                assert alias.lower() in allowed, (
                    f"{who}: single-token alias {alias!r} not in the documented allowlist"
                )
                used_single_tokens.add(alias.lower())
    # The allowlist stays honest: no dead entries accumulating.
    assert allowed == used_single_tokens, (
        f"allowlist tokens not used by any alias: {allowed - used_single_tokens}"
    )


# ── 1b. Real alias file vs. court-style prose ────────────────────────────────
#
# The REAL matcher (the checked-in aliases, the real compiled alternation) run
# over court-filing-style sentences built around the known danger words —
# 'barracuda' the fish, 'carbon black' the industrial material, 'nightfall',
# 'keeper', 'vault', 'duo', numbered facility areas, 'more than one identity',
# 'accurate background check', plus the original sterling-class collisions.
# A future alias edit that reintroduces any of these collisions fails HERE,
# on prose, not just on the denylist.

# Non-product court/business prose: the grown alias file must stay silent on
# every sentence — zero vendors credited across the whole file.
_COURT_PROSE_NEGATIVE = [
    "By nightfall the defendant had copied the customer files to a personal drive.",
    "The plant produced carbon black for tire manufacturing, a process the"
    " indictment describes as a trade secret.",
    "He was the keeper of the branch vault combination and hid the ledger at home.",
    "The duo left the building before the security guards completed their rounds.",
    "Surveillance footage from area 1 of the warehouse showed the loading dock.",
    "A mounted barracuda above his desk was seized along with the laptops.",
    "Counsel called the licensing story a smokescreen and the promised royalties illusive.",
    "Witnesses described his sterling reputation and diligent work within the"
    " purview of the compliance office.",
    "The complaint does not reveal whether more than one identity was used to access the vault.",
    "An accurate background check would have surfaced the prior conviction.",
    "At the disco he told a co-conspirator that the first advantage of the deal was speed.",
    "The guardian ad litem reviewed the trust accounts after the archer tournament.",
    "Investigators moved to encase the drives in evidence bags sans any delay.",
    "He stayed awake monitoring the oxygen sensors as the trend in shipments continued.",
    "Group policy at the insurer required a canary trap for leaked documents.",
    "The technician would ping the server nightly to keep the harmony of the backup schedule.",
]

# Product mentions in the same court-prose register: each sentence must credit
# EXACTLY the (category, vendor) entries listed — compound aliases defuse the
# danger word without losing the real product mention.
_COURT_PROSE_POSITIVE = [
    (
        "The employer's VMware Carbon Black sensor logged the mass file copy.",
        [("edr", "Carbon Black")],
    ),
    ("Nightfall AI flagged credentials pasted into the support ticket.", [("dlp", "Nightfall")]),
    ("Cisco Duo records showed push approvals from an unfamiliar device.", [("iam", "Cisco Duo")]),
    ("The shared admin credential lived in a Keeper Security vault.", [("pam", "Keeper Security")]),
    ("A Barracuda Networks gateway quarantined the outbound message.", [("email", "Barracuda")]),
    (
        "The company retained Cloudflare Area 1 for inbound mail filtering.",
        [("email", "Cloudflare Area 1")],
    ),
    ("Graylog retained eighteen months of VPN logs.", [("siem", "Graylog")]),
    ("Sterling Infosystems produced the pre-employment report.", [("screening-hr", "Sterling")]),
    (
        "Session recordings from Syteca, formerly marketed as Ekran System, captured the export.",
        [("irm", "Syteca")],
    ),
    (
        "Proofpoint DLP blocked the upload, and Proofpoint quarantined the message.",
        [("dlp", "Proofpoint DLP"), ("email", "Proofpoint")],
    ),
    (
        "One Identity Safeguard session logs recorded the privileged transfer.",
        [("pam", "One Identity Safeguard")],
    ),
    (
        "Logs pulled from Exabeam corroborated the badge records.",
        [("siem", "Exabeam"), ("ueba", "Exabeam")],
    ),
]


def test_real_alias_file_stays_silent_on_court_prose() -> None:
    vendors = load_vendor_aliases()["vendors"]
    rows = [
        {"link": f"https://c/neg-{n}", "clean_text": text, "verdict_true": True}
        for n, text in enumerate(_COURT_PROSE_NEGATIVE)
    ]
    scan = scan_vendor_mentions(rows, vendors)
    hits = {key: slot["total"] for key, slot in scan["mentions"].items() if slot["total"]}
    assert not hits, f"court-prose false positives: {hits}"


def test_real_alias_file_credits_product_mentions_in_court_prose() -> None:
    vendors = load_vendor_aliases()["vendors"]
    for text, expected in _COURT_PROSE_POSITIVE:
        scan = scan_vendor_mentions(
            [{"link": "https://c/pos", "clean_text": text, "verdict_true": True}], vendors
        )
        hits = sorted(key for key, slot in scan["mentions"].items() if slot["total"])
        assert hits == sorted(expected), f"{text!r}: matched {hits}, expected {expected}"


# ── 2. Scanner core (synthetic, deterministic) ───────────────────────────────

_VENDORS = [
    {"name": "AcmeSpy", "category": "irm", "aliases": ["AcmeSpy", "Acme Insider Monitor"]},
    {"name": "ZetaWatch", "category": "irm", "aliases": ["ZetaWatch"]},
    # Same product homed in two categories (the Netskope shape).
    {"name": "DualHome", "category": "dlp", "aliases": ["SharedProduct"]},
    {"name": "DualHome", "category": "casb", "aliases": ["SharedProduct"]},
    # The no_safe_alias shape: can never miscount.
    {"name": "NoAlias", "category": "siem", "aliases": []},
]


def _row(link: str, text: str, verdict: bool | None = True) -> dict:
    return {"link": link, "clean_text": text, "verdict_true": verdict}


def _counts(scan: dict, category: str, name: str) -> tuple[int, int]:
    slot = scan["mentions"][(category, name)]
    return (len(slot["verdict_true"]), len(slot["total"]))


def test_scanner_word_boundary_and_case_insensitivity() -> None:
    scan = scan_vendor_mentions(
        [
            _row("https://c/1", "The ACMESPY agent logged the copy."),  # case-insensitive
            _row("https://c/2", "AcmeSpyware is unrelated."),  # butted suffix — no match
            _row("https://c/3", "installed (AcmeSpy)."),  # punctuation boundary — match
            _row("https://c/4", "the XAcmeSpy fork"),  # butted prefix — no match
        ],
        _VENDORS,
    )
    assert _counts(scan, "irm", "AcmeSpy") == (2, 2)
    assert scan["scanned_articles"] == 4


def test_scanner_multiword_alias_tolerates_whitespace_runs() -> None:
    scan = scan_vendor_mentions(
        [_row("https://c/1", "deployed Acme\n   Insider \t Monitor at the site")],
        _VENDORS,
    )
    assert _counts(scan, "irm", "AcmeSpy") == (1, 1)


def test_scanner_counts_distinct_cases_never_occurrences() -> None:
    scan = scan_vendor_mentions(
        [
            _row("https://c/1", "AcmeSpy, AcmeSpy, and AcmeSpy again; Acme Insider Monitor too."),
            _row("https://c/2", "AcmeSpy once."),
        ],
        _VENDORS,
    )
    assert _counts(scan, "irm", "AcmeSpy") == (2, 2)


def test_scanner_verdict_split_mirrors_the_ledger_gate() -> None:
    """Only verdict-true rows land in the verdict split; False and missing
    (None) both fail the gate but still count as total mentions."""
    scan = scan_vendor_mentions(
        [
            _row("https://c/1", "AcmeSpy flagged it.", verdict=True),
            _row("https://c/2", "AcmeSpy mentioned in a non-insider matter.", verdict=False),
            _row("https://c/3", "AcmeSpy named, never adjudicated.", verdict=None),
        ],
        _VENDORS,
    )
    assert _counts(scan, "irm", "AcmeSpy") == (1, 3)


def test_scanner_shared_alias_credits_both_homes() -> None:
    scan = scan_vendor_mentions([_row("https://c/1", "SharedProduct in use.")], _VENDORS)
    assert _counts(scan, "dlp", "DualHome") == (1, 1)
    assert _counts(scan, "casb", "DualHome") == (1, 1)


def test_scanner_no_safe_alias_vendor_never_counts() -> None:
    scan = scan_vendor_mentions(
        [_row("https://c/1", "NoAlias appears verbatim but has no aliases.")], _VENDORS
    )
    assert _counts(scan, "siem", "NoAlias") == (0, 0)


def test_scanner_longest_alias_shadows_prefix_at_same_position() -> None:
    """'Base Product Pro' credits the specific product, not the 'Base
    Product' prefix vendor — but an unqualified mention elsewhere in the
    same text still credits the prefix vendor."""
    vendors = [
        {"name": "BasePlain", "category": "a", "aliases": ["Base Product"]},
        {"name": "BasePro", "category": "b", "aliases": ["Base Product Pro"]},
    ]
    only_pro = scan_vendor_mentions([_row("https://c/1", "ran Base Product Pro all year")], vendors)
    assert _counts(only_pro, "b", "BasePro") == (1, 1)
    assert _counts(only_pro, "a", "BasePlain") == (0, 0)
    both = scan_vendor_mentions(
        [_row("https://c/2", "Base Product Pro replaced the old Base Product install")], vendors
    )
    assert _counts(both, "b", "BasePro") == (1, 1)
    assert _counts(both, "a", "BasePlain") == (1, 1)


def test_compile_alias_matcher_empty_and_ordering() -> None:
    pattern, homes = compile_alias_matcher([{"name": "NoAlias", "category": "x", "aliases": []}])
    assert pattern is None and homes == {}
    pattern, homes = compile_alias_matcher(_VENDORS)
    assert homes["sharedproduct"] == [("dlp", "DualHome"), ("casb", "DualHome")]


# ── 3. Payload: attach ranks vendors, decoration only ────────────────────────


def test_attach_orders_vendors_verdict_then_total_then_name() -> None:
    row = {
        "id": "irm",
        "label": "Insider-risk platform / UAM",
        "examples": ["Delta", "Alpha", "Echo", "Bravo", "Charlie"],
    }
    scan = {
        "mentions": {
            ("irm", "Alpha"): {"verdict_true": {"a"}, "total": {"a", "b", "c"}},
            ("irm", "Bravo"): {"verdict_true": {"a", "b"}, "total": {"a", "b"}},
            ("irm", "Charlie"): {"verdict_true": {"a"}, "total": {"a"}},
            # Delta / Echo unmentioned -> trail alphabetically at (0, 0).
            ("irm", "Delta"): {"verdict_true": set(), "total": set()},
            ("irm", "Echo"): {"verdict_true": set(), "total": set()},
        }
    }
    attach_vendor_mentions([row], scan)
    assert [v["name"] for v in row["vendors"]] == ["Bravo", "Alpha", "Charlie", "Delta", "Echo"]
    assert row["vendors"][0]["mentions_cases"] == {"verdict_true": 2, "total": 2}
    assert row["vendors"][1]["mentions_cases"] == {"verdict_true": 1, "total": 3}
    assert row["vendors"][-1]["mentions_cases"] == {"verdict_true": 0, "total": 0}
    # Decoration only: nothing else on the row is touched.
    assert row["examples"] == ["Delta", "Alpha", "Echo", "Bravo", "Charlie"]
    assert set(row) == {"id", "label", "examples", "vendors"}


def test_attach_defaults_missing_scan_entries_to_zero() -> None:
    row = {"id": "irm", "examples": ["Ghost"]}
    attach_vendor_mentions([row], {"mentions": {}})
    assert row["vendors"] == [
        {
            "name": "Ghost",
            "mentions_cases": {"verdict_true": 0, "total": 0},
            "cases": [],
            "more_cases": 0,
        }
    ]
