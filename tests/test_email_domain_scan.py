"""Contracts for scripts/email_domain_scan.py — the email-destination scan.

The load-bearing test here is the REDACTION GUARANTEE: no output path of the
scan (report or JSON) may ever contain a local part. The product's charter is
roles-never-individuals, and this scan is the first code in the repo that
touches literal email addresses — the guarantee is what keeps it on the right
side of that line.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "email_domain_scan.py"
_spec = importlib.util.spec_from_file_location("email_domain_scan", _SCRIPT)
scan_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scan_mod)


def _row(link, text, *, title="Some Co. v. Doe", forensics=None, history=None):
    row = {"link": link, "title": title, "clean_text": text}
    if forensics is not None:
        row["forensics"] = forensics
    if history is not None:
        row["enrichment_history"] = history
    return row


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def test_extraction_cleanup_and_junk_rejection() -> None:
    assert scan_mod._clean("John.Smith@Gmail.COM.") == ("John.Smith", "gmail.com")
    assert scan_mod._clean("(jdoe@yahoo.com),") == ("jdoe", "yahoo.com")
    assert scan_mod._clean("mailto:a.b@proton.me") == ("a.b", "proton.me")
    assert scan_mod._clean("bad@no-dot") is None
    assert scan_mod._clean("x@a..b.com") is None
    assert scan_mod._clean("@gmail.com") is None


def test_per_case_dedupe_and_hit_count() -> None:
    text = (
        "He forwarded files to his personal account jdoe@gmail.com. "
        "Again jdoe@gmail.com received them."
    )
    stats, pairs = scan_mod.scan([_row("l1", text)])
    assert stats["hits"] == 2
    assert len(pairs) == 1  # one case-address pair
    assert stats["cases_with_address"] == 1


def test_enrichment_history_is_never_scanned() -> None:
    history = [
        {"forensics": {"methods": [{"action": "sent to ghost@gmail.com", "observables": []}]}}
    ]
    stats, pairs = scan_mod.scan([_row("l1", "No addresses in the body.", history=history)])
    assert len(pairs) == 0, "a history-only address leaked into the scan"


# ---------------------------------------------------------------------------
# Context classification
# ---------------------------------------------------------------------------


def test_counsel_signature_block_classifies_as_counsel() -> None:
    text = (
        "Respectfully submitted, /s/ Jane Roe, Esq. LLP, jroe@biglaw.com, Attorneys for Plaintiff"
    )
    _, pairs = scan_mod.scan([_row("l1", text)])
    assert pairs[0]["class"] == "counsel_service"


def test_personal_forward_classifies_as_exfil() -> None:
    text = (
        "Before resigning he forwarded the customer list to his personal "
        "email jdoe@gmail.com that night."
    )
    _, pairs = scan_mod.scan([_row("l1", text)])
    assert pairs[0]["class"] == "exfil_context"


def test_bare_mention_stays_mention() -> None:
    text = "The account someone@example.com appears in the exhibit index."
    _, pairs = scan_mod.scan([_row("l1", text)])
    assert pairs[0]["class"] == "mention"


def test_forensics_fields_are_exfil_by_construction() -> None:
    fx = {"methods": [{"action": "copied data to backup@gmail.com", "observables": []}]}
    _, pairs = scan_mod.scan([_row("l1", "clean body, no address", forensics=fx)])
    assert pairs[0]["class"] == "exfil_context"
    assert pairs[0]["source"].startswith("forensics.")


def test_court_infrastructure_domains_are_counsel_regardless_of_context() -> None:
    text = "He forwarded everything to his personal account notice@ecf.uscourts.gov supposedly."
    _, pairs = scan_mod.scan([_row("l1", text)])
    assert pairs[0]["class"] == "counsel_service"


def test_exfil_outranks_mention_for_the_same_pair() -> None:
    text = (
        "The address jdoe@gmail.com appears in the index. Later he forwarded "
        "trade secrets to his personal email jdoe@gmail.com after resigning."
    )
    _, pairs = scan_mod.scan([_row("l1", text)])
    assert len(pairs) == 1
    assert pairs[0]["class"] == "exfil_context"


# ---------------------------------------------------------------------------
# Categories, shapes, flags
# ---------------------------------------------------------------------------


def test_domain_categories() -> None:
    assert scan_mod.domain_category("gmail.com") == "consumer_webmail"
    assert scan_mod.domain_category("proton.me") == "privacy_encrypted"
    assert scan_mod.domain_category("mailinator.com") == "disposable"
    assert scan_mod.domain_category("uscourts.gov") == "gov_legal_infra"
    assert scan_mod.domain_category("mit.edu") == "edu"
    assert scan_mod.domain_category("rivalcorp.com") == "corporate_other"


def test_local_part_shapes() -> None:
    assert scan_mod.local_shape("john.smith") == "name_like"
    assert scan_mod.local_shape("jdoe1984") == "handle_digits"
    assert scan_mod.local_shape("admin") == "role_account"
    assert scan_mod.local_shape("xk92bq7hd413z") == "random_machine"


def test_automation_flags_detected() -> None:
    window = "he set an auto-forwarding rule that BCC'd 4,000 emails to the account"
    flags = scan_mod.automation_flags(window)
    assert "auto_forward" in flags
    assert "bcc" in flags
    assert "bulk_volume" in flags


# ---------------------------------------------------------------------------
# THE redaction guarantee
# ---------------------------------------------------------------------------

_SEEDED_LOCALS = ("secretlocal.part", "veryunique4711", "counselperson")


def _seeded_rows():
    fx = {
        "methods": [
            {
                "action": "exfiltrated to veryunique4711@rivalcorp.com nightly",
                "evidence_quote": "sent everything to veryunique4711@rivalcorp.com",
                "observables": [
                    {
                        "description": "mail logs for secretlocal.part@gmail.com",
                        "artifact": "email logs",
                    }
                ],
            }
        ],
        "hunt_terms": ["secretlocal.part@gmail.com"],
    }
    return [
        _row(
            "l1",
            "He forwarded files to his personal email secretlocal.part@gmail.com before resigning. "
            "Respectfully submitted /s/ counselperson@lawfirmllp.com, Attorneys for Plaintiff.",
            title="Titles can quote secretlocal.part@gmail.com too",
            forensics=fx,
        )
    ]


def test_no_local_part_survives_into_the_report() -> None:
    stats, pairs = scan_mod.scan(_seeded_rows())
    report = scan_mod.render(stats, pairs, max_snippets=50)
    for local in _SEEDED_LOCALS:
        assert local not in report, f"local part {local!r} leaked into the report"
    assert "<redacted>@gmail.com" in report
    assert "gmail.com" in report and "rivalcorp.com" in report


def test_no_local_part_survives_into_the_json() -> None:
    stats, pairs = scan_mod.scan(_seeded_rows())
    blob = json.dumps(scan_mod.to_json(stats, pairs))
    for local in _SEEDED_LOCALS:
        assert local not in blob, f"local part {local!r} leaked into the JSON"


def test_end_to_end_cli_output_is_redacted(tmp_path, capsys) -> None:
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text("\n".join(json.dumps(r) for r in _seeded_rows()) + "\n", encoding="utf-8")
    out_json = tmp_path / "scan.json"
    rc = scan_mod.main([str(corpus), "--json", str(out_json), "--max-snippets", "10"])
    assert rc == 0
    captured = capsys.readouterr().out
    for local in _SEEDED_LOCALS:
        assert local not in captured
        assert local not in out_json.read_text(encoding="utf-8")
    assert "Domain tally" in captured


# ---------------------------------------------------------------------------
# Runner constraint
# ---------------------------------------------------------------------------


def test_script_is_stdlib_only() -> None:
    """The bare Actions runner has no pydantic and no shared package.

    Same tripwire style as the evidence-core standalone test: the file must
    load with no project imports at all.
    """
    import re as _re

    src = _SCRIPT.read_text(encoding="utf-8")
    imports = _re.findall(r"^(?:from|import)\s+(\S+)", src, _re.M)
    stdlib_only = {
        "__future__",
        "argparse",
        "importlib.util",
        "json",
        "pathlib",
        "re",
        "sys",
        "collections",
    }
    assert set(imports) <= stdlib_only, f"non-stdlib import crept in: {set(imports) - stdlib_only}"
    # And it actually loaded above via spec_from_file_location with only stdlib
    # available on the path — reaching this line is the proof.
    assert hasattr(scan_mod, "redact")


def _scan_file(tmp_path, lines):
    path = tmp_path / "corpus.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in lines), encoding="utf-8")
    return scan_mod.scan(scan_mod._iter_rows(str(path)))


def test_email_scan_collapses_last_line_wins(tmp_path) -> None:
    """The store is append-only mid-cycle, so one link can occupy two lines.

    The API reads last-line-wins; the scan must agree. Run 3 (2026-08-29)
    deduped FIRST-wins and so scanned the stale generation of every updated
    row — the direction this test pins.
    """
    link = "https://ex.com/one"
    stats, pairs = _scan_file(
        tmp_path,
        [_row(link, "no address here"), _row(link, "sent files to mule.acct@gmail.com")],
    )
    assert stats["cases"] == 1
    assert len(pairs) == 1 and pairs[0]["domain"] == "gmail.com"

    stats, pairs = _scan_file(
        tmp_path,
        [_row(link, "sent files to mule.acct@gmail.com"), _row(link, "no address here")],
    )
    assert stats["cases"] == 1
    assert pairs == []


def test_report_carries_the_reading_rules() -> None:
    """Every surface teaches itself: the report states its own method limits."""
    stats, pairs = scan_mod.scan(_seeded_rows())
    report = scan_mod.render(stats, pairs)
    assert "never printed" in report
    assert "keyword" in report


def _unused(*_a):  # keep sys import honest if pytest strips capsys path
    return sys.version


def test_proximity_decides_when_conduct_and_counsel_share_a_window() -> None:
    """A filing's conduct sentence and its service block often sit side by
    side. The vocabulary CLOSER to the address wins — presence alone must not
    flip a proton upload into counsel plumbing, or a counsel address into
    exfil because 'personal email' appears a sentence earlier."""
    text = (
        "Defendant uploaded the design files to k9x2qq81mzt4@protonmail.com and later "
        "wiped the laptop. Service via ECF to counsel of record at the firm."
    )
    _, pairs = scan_mod.scan([_row("l1", text)])
    proton = [p for p in pairs if p["domain"] == "protonmail.com"][0]
    assert proton["class"] == "exfil_context"

    text2 = (
        "He forwarded the lists to his personal email account elsewhere. Respectfully "
        "submitted, /s/ A. Chen, Esq., LLP, achen@chenlawgroup.com, Attorneys for Plaintiff."
    )
    _, pairs2 = scan_mod.scan([_row("l2", text2)])
    counsel = [p for p in pairs2 if p["domain"] == "chenlawgroup.com"][0]
    assert counsel["class"] == "counsel_service"


def test_dotted_name_with_digits_is_handle_digits() -> None:
    assert scan_mod.local_shape("mark.delgado77") == "handle_digits"


# ---------------------------------------------------------------------------
# Precision fixes (2026-08 hand review: 38% counsel leakage into exfil)
# ---------------------------------------------------------------------------


def test_service_list_window_is_counsel_despite_sent_vocabulary() -> None:
    """E-filing SENT lists carry conduct verbs but are plumbing, not conduct."""
    text = (
        "Notice has been sent to the following: alpha@wickfirm.com, "
        "beta@yetterfirm.com, gamma@phangfirm.com per the docket."
    )
    _, pairs = scan_mod.scan([_row("l1", text)])
    assert len(pairs) == 3
    assert all(p["class"] == "counsel_service" for p in pairs)


def test_forensics_field_with_many_addresses_stays_exfil() -> None:
    fx = {
        "methods": [
            {
                "action": ("sent data to a@gmail.com, b@gmail.com and c@gmail.com nightly"),
                "observables": [],
            }
        ]
    }
    _, pairs = scan_mod.scan([_row("l1", "clean body", forensics=fx)])
    assert len(pairs) == 3
    assert all(p["class"] == "exfil_context" for p in pairs)


def test_creditor_matrix_vocabulary_is_counsel() -> None:
    text = (
        "Copies sent to parties on the creditor matrix including trustee@somefirm.com as required."
    )
    _, pairs = scan_mod.scan([_row("l1", text)])
    assert pairs[0]["class"] == "counsel_service"


def test_legacy_hunt_queries_are_never_scanned() -> None:
    fx = {
        "hunt_queries": [
            {"logic": "SELECT * WHERE dest LIKE '%ghost@kalshi.com%'", "rationale": "x"}
        ]
    }
    _, pairs = scan_mod.scan([_row("l1", "No addresses in the body.", forensics=fx)])
    assert len(pairs) == 0, "a legacy hunt_queries address leaked into the scan"


def test_duplicate_snippets_collapse_in_evidence_but_not_tallies() -> None:
    text = (
        "Before resigning he forwarded the customer list to his personal "
        "email jdoe@gmail.com that night."
    )
    stats, pairs = scan_mod.scan([_row("l1", text), _row("l2", text)])
    assert len(pairs) == 2  # tallies stay per-pair
    report = scan_mod.render(stats, pairs, max_snippets=50)
    assert report.count("> Before resigning") == 1  # evidence collapses
    assert "×2 (2 cases)" in report
    blob = scan_mod.to_json(stats, pairs)
    assert len(blob["exfil_pairs"]) == 1
    assert blob["exfil_pairs"][0]["duplicate_pairs"] == 2
    assert blob["by_class"]["exfil_context"] == 2
